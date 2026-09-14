"""Segment streamed replies and synthesize their sentences in bounded order.

The Chat model and local speech engine are both synchronous from this module's
point of view, but they run at very different speeds.  A streaming segmenter
extracts natural utterances without changing the canonical Chat reply, while a
single daemon worker serializes expensive synthesis behind a bounded FIFO.

Cancellation is logical because Python cannot safely kill a thread blocked in
native or HTTP inference.  A cancelled running sentence therefore keeps its
capacity slot until the Synthesizer returns, and its late audio is discarded.
This mirrors the transcription boundary and prevents repeated cancellation
from creating hidden, unbounded work.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from threading import Condition, Event, Lock, Thread, current_thread
from time import monotonic
from typing import Callable, Final, Literal, Protocol, TypeAlias

from .synthesis import (
    SYNTHESIS_MAX_AUDIO_BYTES,
    SYNTHESIS_MAX_IDENTIFIER_LENGTH,
    SYNTHESIS_MAX_TEXT_CODE_POINTS,
    SpeechSynthesizer,
    SynthesisError,
    SynthesisFailedError,
    SynthesisLanguage,
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
    SynthesisValidationError,
)


SPEECH_SEGMENT_DEFAULT_MAX_CODE_POINTS: Final = 240
SPEECH_SEGMENT_MIN_MAX_CODE_POINTS: Final = 32
SPEECH_SEGMENT_MAX_MAX_CODE_POINTS: Final = 1_024
SPEECH_SEGMENT_MAX_CHUNK_CODE_POINTS: Final = SYNTHESIS_MAX_TEXT_CODE_POINTS
SPEECH_QUEUE_MAX_ACTIVE_TURNS: Final = 64
SPEECH_QUEUE_MAX_PENDING_SENTENCES: Final = 64
SPEECH_QUEUE_MAX_DELIVERY_EVENTS: Final = 16
SPEECH_QUEUE_MAX_DELIVERY_BYTES: Final = 128 * 1024 * 1024
SPEECH_QUEUE_MAX_CACHE_ENTRIES: Final = 128
SPEECH_QUEUE_MAX_CACHE_BYTES: Final = 128 * 1024 * 1024
SPEECH_QUEUE_MAX_RETAINED_TURNS: Final = 4_096
SPEECH_QUEUE_MAX_SHUTDOWN_SECONDS: Final = 300.0

SpeechBindingTrust: TypeAlias = Literal["external-unverified"]
SpeechQueueFailureCode: TypeAlias = Literal[
    "invalid_request",
    "unavailable",
    "synthesis_failed",
    "internal_error",
]
SpeechTurnTerminalState: TypeAlias = Literal["completed", "cancelled"]

_NATURAL_TERMINATORS: Final = frozenset("。！？!?.\n")
_TRAILING_TERMINATORS: Final = _NATURAL_TERMINATORS - {"\n"}
_TRAILING_CLOSERS: Final = frozenset('"\'”’」』】）》〕］）)]}')
_SOFT_BREAKS: Final = frozenset("，,；;：:\n\t ")
_SAFE_IDENTIFIER_PATTERN: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
)
_INVALID_REQUEST_MESSAGE: Final = "The speech sentence was rejected."
_UNAVAILABLE_MESSAGE: Final = "Local speech synthesis is unavailable."
_FAILED_MESSAGE: Final = "Local speech synthesis failed."
_INTERNAL_MESSAGE: Final = "The local speech worker failed."


def _is_strict_integer(value: object) -> bool:
    """Reject booleans even though ``bool`` inherits from ``int``."""

    return isinstance(value, int) and not isinstance(value, bool)


def _require_safe_identifier(value: object, label: str) -> str:
    """Return one bounded scheduler identifier without control characters."""

    if (
        not isinstance(value, str)
        or _SAFE_IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise SpeechQueueValidationError(
            f"{label} must be a bounded ASCII identifier."
        )
    return value


def _validate_shutdown_timeout(value: object) -> float:
    """Return a finite shared worker-join timeout."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
        or value <= 0.0
        or value > SPEECH_QUEUE_MAX_SHUTDOWN_SECONDS
    ):
        raise SpeechQueueValidationError(
            "timeout_seconds must be finite and greater than zero, up to 300."
        )
    return float(value)


class SpeechQueueError(Exception):
    """Base class for stable sentence-queue failures."""


class SpeechQueueValidationError(SpeechQueueError):
    """Report malformed queue configuration, text, or identifiers."""


class SpeechQueueCapacityError(SpeechQueueError):
    """Report that the running sentence and bounded FIFO are full."""


class SpeechQueueClosedError(SpeechQueueError):
    """Report work submitted after queue shutdown begins."""


class SpeechTurnStateError(SpeechQueueError):
    """Report text submitted after its turn finished or was cancelled."""


@dataclass(frozen=True, slots=True)
class SentenceSegmenterConfig:
    """Configure a safe latency ceiling for one synthesized sentence."""

    max_code_points: int = SPEECH_SEGMENT_DEFAULT_MAX_CODE_POINTS
    soft_break_floor: int = 96

    def __post_init__(self) -> None:
        """Keep both boundaries useful and below the engine text limit."""

        if (
            not _is_strict_integer(self.max_code_points)
            or not SPEECH_SEGMENT_MIN_MAX_CODE_POINTS
            <= self.max_code_points
            <= min(
                SPEECH_SEGMENT_MAX_MAX_CODE_POINTS,
                SYNTHESIS_MAX_TEXT_CODE_POINTS,
            )
        ):
            raise SpeechQueueValidationError(
                "max_code_points must be an integer from 32 through 1024."
            )
        if (
            not _is_strict_integer(self.soft_break_floor)
            or not 1 <= self.soft_break_floor < self.max_code_points
        ):
            raise SpeechQueueValidationError(
                "soft_break_floor must be below max_code_points."
            )


@dataclass(frozen=True, slots=True)
class SpeechSentence:
    """Identify one ordered, engine-valid utterance from a streamed reply."""

    sequence: int
    text: str

    def __post_init__(self) -> None:
        """Reject invalid sequence numbers and text that cannot be spoken."""

        if not _is_strict_integer(self.sequence) or self.sequence < 0:
            raise SpeechQueueValidationError(
                "Speech sentence sequence must be a non-negative integer."
            )
        try:
            SynthesisRequest(text=self.text)
        except SynthesisValidationError as error:
            raise SpeechQueueValidationError(
                "Speech sentence text must be valid synthesis text."
            ) from error


class StreamingSentenceSegmenter:
    """Extract natural sentences from arbitrary model-stream chunk boundaries.

    Terminal punctuation is held until the following chunk or ``finish`` so a
    closing quote can remain attached.  Replies with no punctuation are split
    at a recent comma/space when possible, otherwise at the hard code-point
    ceiling.  Only the private speech buffer is segmented; callers retain and
    persist the original stream independently and therefore lose no text.
    """

    def __init__(
        self,
        config: SentenceSegmenterConfig | None = None,
    ) -> None:
        """Initialize an empty single-use stream segmenter."""

        if config is not None and not isinstance(config, SentenceSegmenterConfig):
            raise TypeError("config must be a SentenceSegmenterConfig.")
        self._config = config or SentenceSegmenterConfig()
        self._buffer = ""
        self._next_sequence = 0
        self._finished = False

    def feed(self, chunk: str) -> tuple[SpeechSentence, ...]:
        """Accept one exact model chunk and return newly complete sentences."""

        if self._finished:
            raise SpeechTurnStateError("The sentence stream is already finished.")
        if not isinstance(chunk, str):
            raise SpeechQueueValidationError("Speech stream chunks must be strings.")
        if not chunk:
            return ()
        if len(chunk) > SPEECH_SEGMENT_MAX_CHUNK_CODE_POINTS:
            # Reject before concatenation or sentence construction. The caller
            # still owns/persists the canonical model text and may disable
            # speech for this turn without allocating an attacker-sized tuple.
            raise SpeechQueueValidationError(
                "Speech stream chunks must not exceed 4096 code points."
            )
        self._buffer += chunk
        return self._drain(final=False)

    def finish(self) -> tuple[SpeechSentence, ...]:
        """Flush the final speakable tail exactly once."""

        if self._finished:
            raise SpeechTurnStateError("The sentence stream is already finished.")
        self._finished = True
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> tuple[SpeechSentence, ...]:
        """Repeatedly remove natural or length-bounded utterances."""

        sentences: list[SpeechSentence] = []
        while self._buffer:
            split_at = self._next_split(final=final)
            if split_at is None:
                break
            candidate = self._buffer[:split_at]
            self._buffer = self._buffer[split_at:]
            try:
                sentence = SpeechSentence(self._next_sequence, candidate)
            except SpeechQueueValidationError:
                # A punctuation/whitespace-only tail has no acoustic content.
                # Dropping it from speech does not alter the separately retained
                # canonical Chat text.
                continue
            sentences.append(sentence)
            self._next_sequence += 1
        return tuple(sentences)

    def _next_split(self, *, final: bool) -> int | None:
        """Choose the earliest natural boundary or one bounded fallback."""

        # Never inspect beyond the hard ceiling for a preferred terminator. A
        # distant period must not turn a 240-code-point latency/resource bound
        # into an arbitrarily large sentence.
        scan_limit = min(len(self._buffer), self._config.max_code_points)
        for index, character in enumerate(self._buffer[:scan_limit]):
            if character not in _NATURAL_TERMINATORS:
                continue
            if (
                character == "."
                and index > 0
                and index + 1 < len(self._buffer)
                and self._buffer[index - 1].isdecimal()
                and self._buffer[index + 1].isdecimal()
            ):
                # A decimal point is not a speech boundary. Waiting until both
                # neighbours exist avoids splitting a number across model
                # chunks while preserving an ordinary final period.
                continue
            end = index + 1
            while (
                end < scan_limit
                and (
                    self._buffer[end] in _TRAILING_CLOSERS
                    or self._buffer[end] in _TRAILING_TERMINATORS
                )
            ):
                end += 1
            if end < len(self._buffer) or final:
                return end

        if len(self._buffer) >= self._config.max_code_points:
            ceiling = self._config.max_code_points
            for index in range(ceiling - 1, self._config.soft_break_floor - 1, -1):
                if self._buffer[index] in _SOFT_BREAKS:
                    return index + 1
            return ceiling
        if final:
            return len(self._buffer)
        return None

    def _checkpoint(self) -> tuple[str, int, bool]:
        """Capture private parser state for atomic queue admission rollback."""

        return self._buffer, self._next_sequence, self._finished

    def _restore(self, checkpoint: tuple[str, int, bool]) -> None:
        """Restore state when the queue rejects a whole produced batch."""

        self._buffer, self._next_sequence, self._finished = checkpoint


@dataclass(frozen=True, slots=True, init=False)
class SpeechSynthesisBindingLease:
    """Freeze request-level voice fields for an unverified external speech turn.

    ``binding_trust`` describes model-identity evidence, not reachability.  An
    ordinary upstream GPT-SoVITS endpoint must use ``external-unverified`` even
    when synthesis succeeds.  A later managed-runtime lease will implement its
    own attested type; this general constructor deliberately cannot manufacture
    an ``elysia-owned`` claim from caller-supplied strings. The external engine
    may still mutate behind its endpoint, so this lease is never cache-eligible.
    """

    lease_id: str
    cache_identity: str
    profile_id: str
    emotion: str
    language: SynthesisLanguage
    binding_trust: SpeechBindingTrust
    _synthesizer: SpeechSynthesizer = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        lease_id: str,
        cache_identity: str,
        synthesizer: SpeechSynthesizer,
        profile_id: str = "default",
        emotion: str = "neutral",
        language: SynthesisLanguage = "auto",
        binding_trust: SpeechBindingTrust = "external-unverified",
    ) -> None:
        """Validate and retain one immutable adapter/selection snapshot."""

        _require_safe_identifier(lease_id, "lease_id")
        _require_safe_identifier(cache_identity, "cache_identity")
        try:
            selection = SynthesisRequest(
                text="validation",
                language=language,
                profile_id=profile_id,
                emotion=emotion,
            )
        except SynthesisValidationError as error:
            raise SpeechQueueValidationError(
                "The synthesis lease selection is invalid."
            ) from error
        if not callable(getattr(synthesizer, "synthesize", None)):
            raise TypeError("synthesizer must implement synthesize(request).")
        if binding_trust != "external-unverified":
            raise SpeechQueueValidationError(
                "A general synthesis lease must remain external-unverified."
            )

        object.__setattr__(self, "lease_id", lease_id)
        object.__setattr__(self, "cache_identity", cache_identity)
        object.__setattr__(self, "profile_id", selection.profile_id)
        object.__setattr__(self, "emotion", selection.emotion)
        object.__setattr__(self, "language", selection.language)
        object.__setattr__(self, "binding_trust", binding_trust)
        object.__setattr__(self, "_synthesizer", synthesizer)

    @property
    def binding_verified(self) -> bool:
        """Return false because a general external lease cannot attest weights."""

        return False

    def synthesize(self, text: str) -> SynthesisResult:
        """Synthesize text using only this lease's frozen logical selection."""

        return self._synthesizer.synthesize(
            SynthesisRequest(
                text=text,
                language=self.language,
                profile_id=self.profile_id,
                emotion=self.emotion,
            )
        )


@dataclass(frozen=True, slots=True)
class SpeechQueueConfig:
    """Bound work, delivery reservations, and successful in-memory audio."""

    max_active_turns: int = 16
    max_pending_sentences: int = 8
    max_delivery_events: int = 2
    max_delivery_bytes: int = 2 * SYNTHESIS_MAX_AUDIO_BYTES
    max_cache_entries: int = 16
    max_cache_bytes: int = 64 * 1024 * 1024
    max_retained_turns: int = 128

    def __post_init__(self) -> None:
        """Reject resource limits that could disable or unbound the queue."""

        if (
            not _is_strict_integer(self.max_active_turns)
            or not 1 <= self.max_active_turns <= SPEECH_QUEUE_MAX_ACTIVE_TURNS
        ):
            raise SpeechQueueValidationError(
                "max_active_turns must be an integer from 1 through 64."
            )
        if (
            not _is_strict_integer(self.max_pending_sentences)
            or not 1
            <= self.max_pending_sentences
            <= SPEECH_QUEUE_MAX_PENDING_SENTENCES
        ):
            raise SpeechQueueValidationError(
                "max_pending_sentences must be an integer from 1 through 64."
            )
        if (
            not _is_strict_integer(self.max_delivery_events)
            or not 1
            <= self.max_delivery_events
            <= SPEECH_QUEUE_MAX_DELIVERY_EVENTS
        ):
            raise SpeechQueueValidationError(
                "max_delivery_events must be an integer from 1 through 16."
            )
        if (
            not _is_strict_integer(self.max_delivery_bytes)
            or not SYNTHESIS_MAX_AUDIO_BYTES
            <= self.max_delivery_bytes
            <= SPEECH_QUEUE_MAX_DELIVERY_BYTES
        ):
            raise SpeechQueueValidationError(
                "max_delivery_bytes must be an integer from 32 through 128 MiB."
            )
        if (
            not _is_strict_integer(self.max_cache_entries)
            or not 0 <= self.max_cache_entries <= SPEECH_QUEUE_MAX_CACHE_ENTRIES
        ):
            raise SpeechQueueValidationError(
                "max_cache_entries must be an integer from 0 through 128."
            )
        if (
            not _is_strict_integer(self.max_cache_bytes)
            or not 0 <= self.max_cache_bytes <= SPEECH_QUEUE_MAX_CACHE_BYTES
        ):
            raise SpeechQueueValidationError(
                "max_cache_bytes must be an integer from 0 through 128 MiB."
            )
        if (self.max_cache_entries == 0) != (self.max_cache_bytes == 0):
            raise SpeechQueueValidationError(
                "Cache entry and byte limits must both be zero or both be positive."
            )
        if (
            not _is_strict_integer(self.max_retained_turns)
            or not 1
            <= self.max_retained_turns
            <= SPEECH_QUEUE_MAX_RETAINED_TURNS
        ):
            raise SpeechQueueValidationError(
                "max_retained_turns must be an integer from 1 through 4096."
            )


@dataclass(frozen=True, slots=True)
class SpeechQueueClip:
    """Publish one ordered validated audio result without its source text."""

    turn_id: str
    sequence: int
    result: SynthesisResult
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class SpeechQueueFailure:
    """Publish one sanitized sentence failure and allow later work to continue."""

    turn_id: str
    sequence: int
    code: SpeechQueueFailureCode
    message: str


@dataclass(frozen=True, slots=True)
class SpeechTurnTerminal:
    """Publish one logical turn terminal after completion or cancellation."""

    turn_id: str
    state: SpeechTurnTerminalState
    submitted_sentences: int
    completed_sentences: int
    failed_sentences: int


@dataclass(frozen=True, slots=True)
class SpeechQueueStatus:
    """Summarize bounded load without text, audio, or profile details.

    A pending delivery event includes native synthesis after its worst-case
    byte reservation, a queued callback, or the callback currently executing.
    Sentence slots separately count only queued and native-running work.
    """

    closed: bool
    active_turns: int
    retained_turns: int
    running_sentences: int
    queued_sentences: int
    occupied_slots: int
    pending_notifications: int
    pending_delivery_events: int
    pending_delivery_clips: int
    pending_delivery_bytes: int
    delivery_callbacks_in_progress: int
    max_active_turns: int
    max_pending_sentences: int
    max_delivery_events: int
    max_delivery_bytes: int


SpeechQueueEvent: TypeAlias = (
    SpeechQueueClip | SpeechQueueFailure | SpeechTurnTerminal
)
SpeechQueueCallback: TypeAlias = Callable[[SpeechQueueEvent], None]


def _discard_speech_queue_event(_event: SpeechQueueEvent) -> None:
    """Replace a terminal record's potentially heavyweight callback."""

    return


class SpeechTurn(Protocol):
    """Accept streamed reply text for one independently cancellable speech turn."""

    @property
    def turn_id(self) -> str:
        """Return the opaque identifier used to correlate queue events."""
        ...

    def feed(self, chunk: str) -> tuple[SpeechSentence, ...]:
        """Segment and enqueue one exact model-stream chunk."""
        ...

    def finish(self) -> tuple[SpeechSentence, ...]:
        """Flush the final text tail and close sentence admission."""
        ...

    def cancel(self) -> bool:
        """Cancel queued work and suppress audio from a running sentence."""
        ...


@dataclass(slots=True)
class _TurnRecord:
    """Hold mutable turn lifecycle protected by the queue condition."""

    turn_id: str
    lease: SpeechSynthesisBindingLease | None
    callback: SpeechQueueCallback
    segmenter: StreamingSentenceSegmenter
    input_closed: bool = False
    cancelled: bool = False
    terminal_enqueued: bool = False
    terminal_published: bool = False
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    running: int = 0
    terminal: Event = field(default_factory=Event)
    retained: bool = False
    delivery_in_progress: _Notification | None = None
    pending_notifications: int = 0
    producer_operations: int = 0


@dataclass(slots=True)
class _SentenceJob:
    """Retain one sentence only until its worker begins synthesis."""

    turn: _TurnRecord
    sentence: SpeechSentence | None
    delivery_reserved_bytes: int | None = None


@dataclass(slots=True)
class _Notification:
    """Queue one callback outside every scheduler and producer lock."""

    record: _TurnRecord
    event: SpeechQueueEvent
    holds_delivery_credit: bool = False
    delivery_reserved_bytes: int = 0
    is_delivery_clip: bool = False


class _SpeechTurnHandle:
    """Serialize producers while cancellation uses queue-owned state."""

    def __init__(self, queue: "SpeechSynthesisQueue", record: _TurnRecord) -> None:
        """Bind one private record to its owning queue."""

        self._queue = queue
        self._record = record
        self._input_lock = Lock()

    @property
    def turn_id(self) -> str:
        """Return the opaque identifier used to correlate queue events."""

        return self._record.turn_id

    def feed(self, chunk: str) -> tuple[SpeechSentence, ...]:
        """Segment and enqueue one exact model-stream chunk atomically."""

        with self._input_lock:
            self._queue._begin_producer_operation(self._record)
            try:
                checkpoint = self._record.segmenter._checkpoint()
                sentences = self._record.segmenter.feed(chunk)
                try:
                    self._queue._submit_batch(self._record, sentences)
                except BaseException:
                    # Queue shutdown/capacity can race the preliminary open
                    # check. Restore text and sequence allocation so callers
                    # may retry or cancel without a silently lost utterance.
                    self._record.segmenter._restore(checkpoint)
                    raise
                return sentences
            finally:
                self._queue._end_producer_operation(self._record)

    def finish(self) -> tuple[SpeechSentence, ...]:
        """Flush the final text tail and close sentence admission."""

        with self._input_lock:
            self._queue._begin_producer_operation(self._record)
            try:
                checkpoint = self._record.segmenter._checkpoint()
                sentences = self._record.segmenter.finish()
                try:
                    self._queue._submit_batch(self._record, sentences)
                except BaseException:
                    self._record.segmenter._restore(checkpoint)
                    raise
                self._queue._close_turn_input(self._record)
                return sentences
            finally:
                self._queue._end_producer_operation(self._record)

    def cancel(self) -> bool:
        """Cancel queued work and suppress audio from a running sentence."""

        # Cancellation must not retain the producer lock while waiting for a
        # callback: that callback may safely re-enter this same handle. A feed
        # racing here is still atomic because submission rechecks cancelled
        # state under the queue condition and restores its parser checkpoint.
        return self._queue._cancel_turn(self._record)


class SpeechSynthesisQueue:
    """Run sentence synthesis on one bounded FIFO daemon worker.

    One worker preserves playback order and respects GPT-SoVITS's process-wide
    model state.  Admission never blocks: if text generation outruns the fixed
    capacity, speech fails closed while the caller may continue the canonical
    text stream. Each native call first reserves one delivery event and the
    maximum valid audio size; that reservation shrinks to the actual payload
    and remains charged through callback completion. Successful audio is cached
    only for an attested owned lease; an external unverified engine may
    hot-switch weights and is never cached. Cache keys use a lease-scoped keyed
    digest so raw reply text is not retained.
    """

    def __init__(self, config: SpeechQueueConfig | None = None) -> None:
        """Start one worker and allocate bounded empty state."""

        if config is not None and not isinstance(config, SpeechQueueConfig):
            raise TypeError("config must be a SpeechQueueConfig.")
        self._config = config or SpeechQueueConfig()
        self._condition = Condition(Lock())
        self._queue: deque[_SentenceJob] = deque()
        self._notifications: deque[_Notification] = deque()
        self._turns: dict[str, _TurnRecord] = {}
        self._retained_order: deque[str] = deque()
        self._occupied_slots = 0
        self._pending_delivery_events = 0
        self._pending_delivery_clips = 0
        self._pending_delivery_bytes = 0
        self._closed = False
        self._shutdown_prepared = False
        self._cache_secret = secrets.token_bytes(32)
        self._cache: OrderedDict[tuple[str, str], SynthesisResult] = OrderedDict()
        self._cache_bytes = 0
        self._worker = Thread(
            target=self._worker_loop,
            name="elysia-speech-synthesis",
            daemon=True,
        )
        self._notifier = Thread(
            target=self._notifier_loop,
            name="elysia-speech-delivery",
            daemon=True,
        )
        self._worker.start()
        self._notifier.start()

    def start_turn(
        self,
        turn_id: str,
        lease: SpeechSynthesisBindingLease,
        callback: SpeechQueueCallback,
        *,
        segmenter_config: SentenceSegmenterConfig | None = None,
    ) -> SpeechTurn:
        """Create one streamed speech turn without performing synthesis."""

        _require_safe_identifier(turn_id, "turn_id")
        if not isinstance(lease, SpeechSynthesisBindingLease):
            raise TypeError("lease must be a SpeechSynthesisBindingLease.")
        if not callable(callback):
            raise TypeError("callback must be callable.")
        if segmenter_config is not None and not isinstance(
            segmenter_config, SentenceSegmenterConfig
        ):
            raise TypeError("segmenter_config must be a SentenceSegmenterConfig.")
        with self._condition:
            if self._closed:
                raise SpeechQueueClosedError("The speech synthesis queue is closed.")
            if turn_id in self._turns:
                raise SpeechTurnStateError("The speech turn identifier is in use.")
            # A terminal turn remains active until its final callback returns.
            # Otherwise a blocked consumer could create an unbounded stream of
            # empty terminal notifications while bypassing active-turn limits.
            active_turns = sum(
                not record.retained for record in self._turns.values()
            )
            if active_turns >= self._config.max_active_turns:
                raise SpeechQueueCapacityError(
                    "The speech turn capacity is full."
                )
            record = _TurnRecord(
                turn_id=turn_id,
                lease=lease,
                callback=callback,
                segmenter=StreamingSentenceSegmenter(segmenter_config),
            )
            self._turns[turn_id] = record
        return _SpeechTurnHandle(self, record)

    def wait_turn(
        self,
        turn_id: str,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Wait as an explicit observer and report whether a terminal arrived."""

        _require_safe_identifier(turn_id, "turn_id")
        if timeout_seconds is not None:
            timeout_seconds = _validate_shutdown_timeout(timeout_seconds)
        with self._condition:
            record = self._turns.get(turn_id)
            if record is None:
                raise SpeechTurnStateError("The speech turn is unknown.")
        if (
            current_thread() in (self._worker, self._notifier)
            and not record.terminal.is_set()
        ):
            # Neither daemon may block waiting for a terminal whose progress
            # requires that same daemon to finish its current work/callback.
            return False
        return record.terminal.wait(timeout_seconds)

    def forget_turn(self, turn_id: str) -> None:
        """Release a terminal turn identifier after its consumer settles."""

        _require_safe_identifier(turn_id, "turn_id")
        with self._condition:
            record = self._turns.get(turn_id)
            if record is None:
                raise SpeechTurnStateError("The speech turn is unknown.")
            if not record.retained:
                raise SpeechTurnStateError(
                    "A speech turn can be forgotten only after work and delivery "
                    "drain."
                )
            self._retained_order.remove(turn_id)
            del self._turns[turn_id]

    def get_status(self) -> SpeechQueueStatus:
        """Return current bounded load without retaining speech content."""

        with self._condition:
            return SpeechQueueStatus(
                closed=self._closed,
                active_turns=sum(
                    not record.retained for record in self._turns.values()
                ),
                retained_turns=len(self._retained_order),
                running_sentences=sum(
                    record.running for record in self._turns.values()
                ),
                queued_sentences=len(self._queue),
                occupied_slots=self._occupied_slots,
                pending_notifications=sum(
                    record.pending_notifications
                    for record in self._turns.values()
                ),
                pending_delivery_events=self._pending_delivery_events,
                pending_delivery_clips=self._pending_delivery_clips,
                pending_delivery_bytes=self._pending_delivery_bytes,
                delivery_callbacks_in_progress=sum(
                    record.delivery_in_progress is not None
                    for record in self._turns.values()
                ),
                max_active_turns=self._config.max_active_turns,
                max_pending_sentences=self._config.max_pending_sentences,
                max_delivery_events=self._config.max_delivery_events,
                max_delivery_bytes=self._config.max_delivery_bytes,
            )

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_pending: bool = True,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Close admission, optionally cancel turns, and join the worker safely."""

        if not isinstance(wait, bool) or not isinstance(cancel_pending, bool):
            raise TypeError("wait and cancel_pending must be booleans.")
        if not wait and timeout_seconds is not None:
            raise SpeechQueueValidationError(
                "timeout_seconds requires wait=True during shutdown."
            )
        timeout = (
            None
            if timeout_seconds is None
            else _validate_shutdown_timeout(timeout_seconds)
        )
        records: tuple[_TurnRecord, ...] = ()
        with self._condition:
            self._closed = True
            records = tuple(
                record
                for record in self._turns.values()
                if (
                    not record.terminal_published
                    and (cancel_pending or not record.input_closed)
                )
            )
        for record in records:
            # Joining below provides the optional wait and shared timeout.
            # Cancellation itself stays non-blocking so wait=False is truthful
            # and a blocked callback cannot consume the timeout before joins.
            self._cancel_turn(record, wait_for_delivery=False)
        with self._condition:
            self._shutdown_prepared = True
            self._condition.notify_all()
        caller_is_internal = current_thread() in (self._worker, self._notifier)
        # An internal callback cannot join either daemon safely: the peer may
        # require this thread to publish or release its current delivery credit.
        # Initiate shutdown and let an external owner perform any blocking join.
        if caller_is_internal:
            return False
        deadline = None if timeout is None else monotonic() + timeout
        if wait:
            remaining = (
                None
                if deadline is None
                else max(0.0, deadline - monotonic())
            )
            self._worker.join(remaining)
        if wait:
            remaining = (
                None
                if deadline is None
                else max(0.0, deadline - monotonic())
            )
            self._notifier.join(remaining)
        return not self._worker.is_alive() and not self._notifier.is_alive()

    def _begin_producer_operation(self, record: _TurnRecord) -> None:
        """Reserve one producer operation before touching streamed text."""

        with self._condition:
            if self._closed:
                raise SpeechQueueClosedError("The speech synthesis queue is closed.")
            if record.cancelled or record.input_closed:
                raise SpeechTurnStateError("The speech turn no longer accepts text.")
            record.producer_operations += 1

    def _end_producer_operation(self, record: _TurnRecord) -> None:
        """Release producer state and scrub a terminal record when safe."""

        with self._condition:
            if record.producer_operations <= 0:
                raise RuntimeError("Speech producer accounting underflowed.")
            record.producer_operations -= 1
            self._retain_if_drained_locked(record)
            self._condition.notify_all()

    def _submit_batch(
        self,
        record: _TurnRecord,
        sentences: tuple[SpeechSentence, ...],
    ) -> None:
        """Reserve every sentence in one feed call or reject the entire batch."""

        if not sentences:
            return
        with self._condition:
            if self._closed:
                raise SpeechQueueClosedError("The speech synthesis queue is closed.")
            if record.cancelled or record.input_closed:
                raise SpeechTurnStateError("The speech turn no longer accepts text.")
            if (
                self._occupied_slots + len(sentences)
                > self._config.max_pending_sentences
            ):
                raise SpeechQueueCapacityError(
                    "The speech synthesis worker and FIFO capacity are full."
                )
            for sentence in sentences:
                self._queue.append(_SentenceJob(record, sentence))
            record.submitted += len(sentences)
            self._occupied_slots += len(sentences)
            # Worker and notifier share this condition. Wake both so a single
            # streamed sentence cannot accidentally wake only the notifier and
            # then stall until an unrelated feed or finish operation occurs.
            self._condition.notify_all()

    def _close_turn_input(self, record: _TurnRecord) -> None:
        """Publish completion immediately when no sentence work remains."""

        with self._condition:
            record.input_closed = True
            terminal = self._terminal_if_ready_locked(record)
            if terminal is not None:
                self._enqueue_notification_locked(record, terminal)
            self._condition.notify_all()

    def _cancel_turn(
        self,
        record: _TurnRecord,
        *,
        wait_for_delivery: bool = True,
    ) -> bool:
        """Cancel every event that has not crossed the callback boundary."""

        with self._condition:
            if record.terminal_published or record.cancelled:
                return False
            record.cancelled = True
            record.input_closed = True
            retained: deque[_SentenceJob] = deque()
            removed = 0
            while self._queue:
                job = self._queue.popleft()
                if job.turn is record:
                    job.sentence = None
                    removed += 1
                else:
                    retained.append(job)
            self._queue = retained
            # Audio/failure events decided but not yet delivered have not
            # crossed the callback boundary. Remove them so a successful cancel
            # acknowledgement cannot be followed by a stale playback token.
            retained_notifications: deque[_Notification] = deque()
            while self._notifications:
                notification = self._notifications.popleft()
                if notification.record is record:
                    if isinstance(notification.event, SpeechTurnTerminal):
                        record.terminal_enqueued = False
                    self._release_notification_locked(notification)
                else:
                    retained_notifications.append(notification)
            self._notifications = retained_notifications
            record.completed += removed
            self._occupied_slots -= removed
            winning_notification = record.delivery_in_progress

            # Establish terminal ownership before any wait. A concurrent
            # non-blocking shutdown may otherwise let the notifier exit after
            # the current callback but before this waiter can enqueue it.
            terminal = self._publish_terminal_locked(record, "cancelled")
            if terminal is not None:
                self._enqueue_notification_locked(record, terminal)

            # Mark cancellation before waiting. This prevents the notifier from
            # starting another stale clip while an external caller waits for a
            # callback that already crossed the delivery boundary. Re-entrant
            # cancellation from that callback must not wait on itself.
            while (
                wait_for_delivery
                and winning_notification is not None
                and record.delivery_in_progress is winning_notification
                and current_thread() is not self._notifier
            ):
                self._condition.wait()
            self._condition.notify_all()
        return True

    def _worker_loop(self) -> None:
        """Drain the FIFO until closed and every admitted sentence settles."""

        while True:
            with self._condition:
                while (
                    not self._queue
                    or not self._delivery_credit_available_locked()
                ):
                    if self._closed and self._occupied_slots == 0:
                        return
                    self._condition.wait()
                job = self._queue.popleft()
                self._reserve_delivery_credit_locked(job)
                sentence = job.sentence
                job.sentence = None
                job.turn.running += 1

            if sentence is None:
                self._finish_job(
                    job,
                    sequence=0,
                    result=None,
                    failure=SpeechQueueFailure(
                        job.turn.turn_id,
                        0,
                        "internal_error",
                        _INTERNAL_MESSAGE,
                    ),
                    cache_hit=False,
                )
                del job
                continue
            self._execute_job(job, sentence)
            # Do not retain streamed reply text in an idle worker frame.
            del sentence
            del job

    def _execute_job(
        self,
        job: _SentenceJob,
        sentence: SpeechSentence,
    ) -> None:
        """Use a lease-scoped cache and contain every Synthesizer failure."""

        record = job.turn
        cache_key: tuple[str, str] | None = None
        result: SynthesisResult | None = None
        failure: SpeechQueueFailure | None = None
        cache_hit = False
        try:
            lease = record.lease
            if lease is None:
                raise RuntimeError("Speech binding lease was already released.")
            # External services are never stable enough to cache, so avoid
            # deriving even a transient text digest for an unverified lease.
            if lease.binding_verified:
                cache_key = self._cache_key(lease, sentence.text)
                with self._condition:
                    cached = self._cache.get(cache_key)
                    if cached is not None:
                        self._cache.move_to_end(cache_key)
                        result = cached
                        cache_hit = True

            if result is None:
                candidate = lease.synthesize(sentence.text)
                if not isinstance(candidate, SynthesisResult):
                    raise TypeError("Speech Synthesizer returned an invalid result.")
                result = candidate
        except BaseException as error:
            failure = self._failure_from_error(
                record.turn_id,
                sentence.sequence,
                error,
            )

        self._finish_job(
            job,
            sequence=sentence.sequence,
            result=result,
            failure=failure,
            cache_hit=cache_hit,
            cache_key=cache_key,
        )

    def _finish_job(
        self,
        job: _SentenceJob,
        *,
        sequence: int,
        result: SynthesisResult | None,
        failure: SpeechQueueFailure | None,
        cache_hit: bool,
        cache_key: tuple[str, str] | None = None,
    ) -> None:
        """Resolve one reserved job and suppress results after cancellation."""

        with self._condition:
            record = job.turn
            record.running -= 1
            record.completed += 1
            self._occupied_slots -= 1
            if not record.cancelled:
                if result is not None:
                    self._resize_delivery_credit_locked(job, len(result.audio))
                    if (
                        not cache_hit
                        and cache_key is not None
                    ):
                        self._remember_cache_locked(cache_key, result)
                    event: SpeechQueueClip | SpeechQueueFailure | None = (
                        SpeechQueueClip(
                            record.turn_id,
                            sequence,
                            result,
                            cache_hit,
                        )
                    )
                elif failure is not None:
                    self._resize_delivery_credit_locked(job, 0)
                    record.failed += 1
                    event = failure
                else:
                    self._resize_delivery_credit_locked(job, 0)
                    record.failed += 1
                    event = SpeechQueueFailure(
                        record.turn_id,
                        sequence,
                        "internal_error",
                        _INTERNAL_MESSAGE,
                    )
                if event is not None:
                    self._enqueue_notification_locked(
                        record,
                        event,
                        delivery_job=job,
                    )
                terminal = self._terminal_if_ready_locked(record)
                if terminal is not None:
                    self._enqueue_notification_locked(record, terminal)
            else:
                self._release_job_delivery_credit_locked(job)
                self._retain_if_drained_locked(record)
            self._condition.notify_all()

    def _delivery_credit_available_locked(self) -> bool:
        """Return whether one worst-case synthesis result can be admitted."""

        return (
            self._pending_delivery_events
            < self._config.max_delivery_events
            and self._pending_delivery_bytes + SYNTHESIS_MAX_AUDIO_BYTES
            <= self._config.max_delivery_bytes
        )

    def _reserve_delivery_credit_locked(self, job: _SentenceJob) -> None:
        """Reserve worst-case output before native synthesis allocates audio."""

        if job.delivery_reserved_bytes is not None:
            raise RuntimeError("Speech delivery credit was already reserved.")
        job.delivery_reserved_bytes = SYNTHESIS_MAX_AUDIO_BYTES
        self._pending_delivery_events += 1
        self._pending_delivery_bytes += SYNTHESIS_MAX_AUDIO_BYTES

    def _resize_delivery_credit_locked(
        self,
        job: _SentenceJob,
        actual_bytes: int,
    ) -> None:
        """Replace a worst-case reservation with validated result bytes."""

        reserved_bytes = job.delivery_reserved_bytes
        if reserved_bytes is None:
            raise RuntimeError("Speech delivery credit was not reserved.")
        if not 0 <= actual_bytes <= SYNTHESIS_MAX_AUDIO_BYTES:
            raise RuntimeError("Speech delivery result exceeded its reservation.")
        self._pending_delivery_bytes += actual_bytes - reserved_bytes
        job.delivery_reserved_bytes = actual_bytes

    def _release_job_delivery_credit_locked(self, job: _SentenceJob) -> None:
        """Release a running job's reservation exactly once."""

        reserved_bytes = job.delivery_reserved_bytes
        if reserved_bytes is None:
            return
        self._pending_delivery_events -= 1
        self._pending_delivery_bytes -= reserved_bytes
        job.delivery_reserved_bytes = None

    def _release_notification_locked(self, notification: _Notification) -> None:
        """Release notification bookkeeping after cancellation or callback."""

        record = notification.record
        if record.pending_notifications <= 0:
            raise RuntimeError("Speech notification accounting underflowed.")
        record.pending_notifications -= 1
        if not notification.holds_delivery_credit:
            return
        self._pending_delivery_events -= 1
        self._pending_delivery_bytes -= notification.delivery_reserved_bytes
        if notification.is_delivery_clip:
            self._pending_delivery_clips -= 1
        notification.holds_delivery_credit = False
        notification.delivery_reserved_bytes = 0
        notification.is_delivery_clip = False

    def _notifier_loop(self) -> None:
        """Deliver ordered callbacks without holding any internal queue lock."""

        while True:
            with self._condition:
                while not self._notifications:
                    if (
                        self._closed
                        and self._shutdown_prepared
                        and self._occupied_slots == 0
                    ):
                        return
                    self._condition.wait()
                notification = self._notifications.popleft()
                notification.record.delivery_in_progress = notification
                if isinstance(notification.event, SpeechTurnTerminal):
                    # The terminal becomes irrevocable when its callback starts.
                    # A re-entrant cancel from that callback must report false.
                    notification.record.terminal_published = True
                    notification.record.terminal.set()
            try:
                self._notify(
                    notification.record.callback,
                    notification.event,
                )
            finally:
                with self._condition:
                    if notification.record.delivery_in_progress is notification:
                        notification.record.delivery_in_progress = None
                    self._release_notification_locked(notification)
                    self._retain_if_drained_locked(notification.record)
                    self._condition.notify_all()
            # The daemon's idle frame must not retain the last audio payload or
            # its old binding after accounting says delivery has completed.
            del notification

    def _enqueue_notification_locked(
        self,
        record: _TurnRecord,
        event: SpeechQueueEvent,
        *,
        delivery_job: _SentenceJob | None = None,
    ) -> None:
        """Append one event and transfer any job delivery reservation to it."""

        notification = _Notification(record, event)
        if delivery_job is not None:
            reserved_bytes = delivery_job.delivery_reserved_bytes
            if reserved_bytes is None:
                raise RuntimeError("Speech delivery credit was not reserved.")
            notification.holds_delivery_credit = True
            notification.delivery_reserved_bytes = reserved_bytes
            notification.is_delivery_clip = isinstance(event, SpeechQueueClip)
            if notification.is_delivery_clip:
                self._pending_delivery_clips += 1
            delivery_job.delivery_reserved_bytes = None
        record.pending_notifications += 1
        self._notifications.append(notification)

    def _terminal_if_ready_locked(
        self,
        record: _TurnRecord,
    ) -> SpeechTurnTerminal | None:
        """Complete a closed turn only after all sentence events are ordered."""

        if (
            not record.input_closed
            or record.completed != record.submitted
            or record.running != 0
        ):
            return None
        return self._publish_terminal_locked(record, "completed")

    def _publish_terminal_locked(
        self,
        record: _TurnRecord,
        state: SpeechTurnTerminalState,
    ) -> SpeechTurnTerminal | None:
        """Create one terminal snapshot while holding the lifecycle lock."""

        if record.terminal_enqueued or record.terminal_published:
            return None
        record.terminal_enqueued = True
        terminal = SpeechTurnTerminal(
            turn_id=record.turn_id,
            state=state,
            submitted_sentences=record.submitted,
            completed_sentences=record.completed,
            failed_sentences=record.failed,
        )
        return terminal

    def _retain_if_drained_locked(self, record: _TurnRecord) -> None:
        """Bound terminal history once no physical Synthesizer call remains."""

        if (
            not record.terminal_published
            or record.running > 0
            or record.pending_notifications > 0
            or record.producer_operations > 0
            or record.retained
        ):
            return
        record.retained = True
        # A tombstone needs only its terminal signal and counters. Release the
        # potentially heavyweight engine/callback and overwrite any cancelled
        # unsynthesized text before retaining the identifier in bounded history.
        record.lease = None
        record.callback = _discard_speech_queue_event
        record.segmenter = StreamingSentenceSegmenter()
        self._retained_order.append(record.turn_id)
        while len(self._retained_order) > self._config.max_retained_turns:
            expired_id = self._retained_order.popleft()
            expired = self._turns.get(expired_id)
            if expired is not None and expired.retained and expired.running == 0:
                del self._turns[expired_id]

    def _cache_key(
        self,
        lease: SpeechSynthesisBindingLease,
        text: str,
    ) -> tuple[str, str]:
        """Hash text with a process-local key instead of retaining it in cache."""

        selection = "\0".join(
            (lease.profile_id, lease.emotion, lease.language, text)
        ).encode("utf-8", "surrogatepass")
        digest = hmac.new(
            self._cache_secret,
            selection,
            hashlib.sha256,
        ).hexdigest()
        return lease.cache_identity, digest

    def _remember_cache_locked(
        self,
        key: tuple[str, str],
        result: SynthesisResult,
    ) -> None:
        """Insert one result and evict oldest audio under both cache bounds."""

        if (
            self._config.max_cache_entries == 0
            or len(result.audio) > self._config.max_cache_bytes
        ):
            return
        existing = self._cache.pop(key, None)
        if existing is not None:
            self._cache_bytes -= len(existing.audio)
        self._cache[key] = result
        self._cache_bytes += len(result.audio)
        while (
            len(self._cache) > self._config.max_cache_entries
            or self._cache_bytes > self._config.max_cache_bytes
        ):
            _, evicted = self._cache.popitem(last=False)
            self._cache_bytes -= len(evicted.audio)

    @staticmethod
    def _failure_from_error(
        turn_id: str,
        sequence: int,
        error: BaseException,
    ) -> SpeechQueueFailure:
        """Map adapter failures without preserving private exception text."""

        if isinstance(error, SynthesisValidationError):
            return SpeechQueueFailure(
                turn_id, sequence, "invalid_request", _INVALID_REQUEST_MESSAGE
            )
        if isinstance(error, SynthesisUnavailableError):
            return SpeechQueueFailure(
                turn_id, sequence, "unavailable", _UNAVAILABLE_MESSAGE
            )
        if isinstance(error, SynthesisFailedError):
            return SpeechQueueFailure(
                turn_id, sequence, "synthesis_failed", _FAILED_MESSAGE
            )
        if isinstance(error, SynthesisError):
            return SpeechQueueFailure(
                turn_id, sequence, "internal_error", _INTERNAL_MESSAGE
            )
        return SpeechQueueFailure(
            turn_id, sequence, "internal_error", _INTERNAL_MESSAGE
        )

    @staticmethod
    def _notify(callback: SpeechQueueCallback, event: SpeechQueueEvent) -> None:
        """Contain consumer failures so one bad sink cannot kill the worker."""

        try:
            callback(event)
        except BaseException:
            # A future protocol/file sink is an isolation boundary. It can
            # report its own failure, but cannot reduce physical worker count.
            return
