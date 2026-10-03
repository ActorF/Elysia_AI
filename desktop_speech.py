"""Coordinate managed sentence synthesis and private desktop audio delivery.

This composition boundary deliberately runs beside the canonical Chat stream.
It may segment, synthesize, cancel, or discard speech, but it never owns or
rewrites the Assistant text that ``Brain`` persists.  Model assets and runtime
paths remain in Python; Electron receives only validated event metadata and the
matching PCM WAV bytes through its inherited private pipe.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Condition, Event, Lock, RLock, Thread
from typing import Any, Final, Literal, Protocol, TypeAlias, runtime_checkable

from config.settings import AppSettings, VOICE_EMOTIONS, VoiceEmotion
from desktop_protocol import AudioChannelWriter, MAX_MESSAGE_LENGTH, ProtocolEventName
from voice.managed_gpt_sovits import (
    ManagedGptSovitsConfig,
    ManagedGptSovitsRuntime,
)
from voice.profiles import JsonVoiceProfileCatalog
from voice.speech_queue import (
    SpeechQueueClip,
    SpeechQueueConfig,
    SpeechQueueEvent,
    SpeechQueueFailure,
    SpeechSynthesisBindingLease,
    SpeechSynthesisQueue,
    SpeechTurn,
    SpeechTurnStateError,
    SpeechTurnTerminal,
    SPEECH_SEGMENT_MAX_CHUNK_CODE_POINTS,
    _create_managed_synthesis_binding_lease,
)


logger = logging.getLogger(__name__)

DesktopSpeechState: TypeAlias = Literal[
    "idle",
    "starting",
    "ready",
    "unavailable",
    "closed",
]
DesktopSpeechEventSink: TypeAlias = Callable[
    [ProtocolEventName, str, dict[str, Any]],
    None,
]

_SPEECH_SPOOL_MAX_CODE_POINTS: Final = MAX_MESSAGE_LENGTH
_SPEECH_SPOOL_BLOCK_CODE_POINTS: Final = SPEECH_SEGMENT_MAX_CHUNK_CODE_POINTS
_DEFAULT_LANGUAGE: Final = "auto"
_LEASE_ID: Final = "desktop-managed-lease"
_CACHE_IDENTITY: Final = "desktop-managed-voice"
_VOICE_PROFILE_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MIN_SPEECH_RATE_PERCENT: Final = 50
_MAX_SPEECH_RATE_PERCENT: Final = 200


@dataclass(frozen=True, slots=True, repr=False)
class DesktopSpeechConfig:
    """Freeze private runtime locations and bounded managed-worker policy."""

    runtime_root: Path
    worker_script: Path
    catalog_path: Path
    asset_root: Path
    voice_profile_id: str
    voice_emotion: VoiceEmotion
    speech_rate_percent: int
    allow_local_evaluation: bool
    deterministic_seed: int
    synthesis_timeout_seconds: float

    @classmethod
    def from_app_settings(cls, settings: AppSettings) -> "DesktopSpeechConfig":
        """Derive every path from the trusted application root, never Renderer."""

        if not isinstance(settings, AppSettings):
            raise TypeError("settings must be an AppSettings instance.")
        base_dir = settings.base_dir.resolve()
        data_layout = settings.data_layout
        return cls(
            runtime_root=(
                base_dir / "models" / "cache" / "GPT-SoVITS-v2-240821"
            ),
            worker_script=base_dir / "scripts" / "gpt_sovits_worker.py",
            catalog_path=(
                data_layout.settings / "voice-profiles.json"
            ),
            asset_root=base_dir / "models" / "weights" / "gpt-sovits",
            voice_profile_id=settings.voice_profile_id,
            voice_emotion=settings.voice_emotion,
            speech_rate_percent=settings.speech_rate_percent,
            allow_local_evaluation=settings.gpt_sovits_allow_local_evaluation,
            deterministic_seed=settings.gpt_sovits_deterministic_seed,
            synthesis_timeout_seconds=(
                settings.gpt_sovits_request_timeout_seconds
            ),
        )

    def __post_init__(self) -> None:
        """Reject caller-defined relative paths and mutable scalar subclasses."""

        paths = (
            self.runtime_root,
            self.worker_script,
            self.catalog_path,
            self.asset_root,
        )
        if any(not isinstance(path, Path) or not path.is_absolute() for path in paths):
            raise ValueError("Desktop speech paths must be absolute Path values.")
        if (
            not isinstance(self.voice_profile_id, str)
            or _VOICE_PROFILE_ID_PATTERN.fullmatch(self.voice_profile_id) is None
        ):
            raise ValueError(
                "voice_profile_id must be a bounded lowercase logical identifier."
            )
        if self.voice_emotion not in VOICE_EMOTIONS:
            raise ValueError("voice_emotion must be neutral, happy, or sad.")
        if (
            type(self.speech_rate_percent) is not int
            or not _MIN_SPEECH_RATE_PERCENT
            <= self.speech_rate_percent
            <= _MAX_SPEECH_RATE_PERCENT
        ):
            raise ValueError("speech_rate_percent is outside the safe range.")
        if type(self.allow_local_evaluation) is not bool:
            raise TypeError("allow_local_evaluation must be a Boolean.")
        if (
            type(self.deterministic_seed) is not int
            or not 0 <= self.deterministic_seed <= 2_147_483_647
        ):
            raise ValueError("deterministic_seed is outside the managed range.")
        if (
            type(self.synthesis_timeout_seconds) is not float
            or not 0.1 <= self.synthesis_timeout_seconds <= 300.0
        ):
            raise ValueError("synthesis_timeout_seconds is outside the safe range.")

    def __repr__(self) -> str:
        """Expose policy without leaking private model or runtime locations."""

        return (
            f"{type(self).__name__}("
            f"allow_local_evaluation={self.allow_local_evaluation!r}, "
            f"synthesis_timeout_seconds={self.synthesis_timeout_seconds!r})"
        )


@dataclass(frozen=True, slots=True)
class DesktopSpeechStatus:
    """Describe readiness without paths, prompts, model names, or diagnostics."""

    state: DesktopSpeechState
    available: bool


@runtime_checkable
class DesktopSpeechTurn(Protocol):
    """Receive a copy of streamed reply chunks for optional local playback."""

    def feed(self, chunk: str) -> None:
        """Offer one exact Brain chunk without changing its canonical owner."""
        ...

    def finish(self) -> None:
        """Flush the final speech tail after Brain commits the complete reply."""
        ...

    def cancel(self) -> bool:
        """Mark this Chat request stale and cancel work not across delivery."""
        ...


@dataclass(slots=True)
class _TurnRecord:
    """Hold one pending or queue-backed turn behind the coordinator lock."""

    request_id: str
    chat_id: str
    queue_turn_id: str
    pending_blocks: deque[str] = field(default_factory=deque, repr=False)
    pending_code_points: int = 0
    accepted_code_points: int = 0
    delegate: SpeechTurn | None = field(default=None, repr=False)
    input_finished: bool = False
    delegate_input_closed: bool = False
    input_truncated: bool = False
    cancelled: bool = False
    terminal_emitted: bool = False


class _DesktopSpeechTurnHandle:
    """Route one generation's speech copy through its exact coordinator record."""

    __slots__ = ("_coordinator", "_record")

    def __init__(
        self,
        coordinator: "DesktopSpeechCoordinator",
        record: _TurnRecord,
    ) -> None:
        self._coordinator = coordinator
        self._record = record

    def feed(self, chunk: str) -> None:
        """Offer one chunk; speech admission failure remains text-independent."""

        self._coordinator._feed_turn(self._record, chunk)

    def finish(self) -> None:
        """Close sentence input exactly once for the matching record."""

        self._coordinator._finish_turn(self._record)

    def cancel(self) -> bool:
        """Cancel only the matching record and report whether it was active."""

        return self._coordinator._cancel_turn(self._record)


class DesktopSpeechCoordinator:
    """Own managed TTS bootstrap, bounded FIFO work, and fd3 frame delivery.

    Bootstrap runs on a daemon so missing or slow optional voice assets cannot
    delay text Chat initialization. A second fixed daemon drains one bounded
    raw-text spool into the smaller sentence FIFO with backpressure, so a fast
    model never blocks Chat streaming and cannot turn ordinary queue pressure
    into destructive cancellation of the managed speech worker.
    """

    def __init__(
        self,
        config: DesktopSpeechConfig,
        audio_writer: AudioChannelWriter,
        event_sink: DesktopSpeechEventSink,
    ) -> None:
        """Take exclusive ownership of one private audio writer without I/O."""

        if not isinstance(config, DesktopSpeechConfig):
            raise TypeError("config must be a DesktopSpeechConfig.")
        if not isinstance(audio_writer, AudioChannelWriter):
            raise TypeError("audio_writer must be an AudioChannelWriter.")
        if not callable(event_sink):
            raise TypeError("event_sink must be callable.")
        self._config = config
        self._audio_writer: AudioChannelWriter | None = audio_writer
        self._event_sink = event_sink
        self._lock = RLock()
        self._work_condition = Condition(self._lock)
        self._turn_transition_lock = Lock()
        self._state: DesktopSpeechState = "idle"
        self._settled = Event()
        self._runtime: ManagedGptSovitsRuntime | None = None
        self._queue: SpeechSynthesisQueue | None = None
        self._binding: SpeechSynthesisBindingLease | None = None
        self._active_turn: _TurnRecord | None = None
        self._bootstrap_thread: Thread | None = None
        self._feeder_thread: Thread | None = None

    def get_status(self) -> DesktopSpeechStatus:
        """Return a safe in-memory readiness snapshot without probing disk."""

        with self._lock:
            state = self._state
        return DesktopSpeechStatus(state=state, available=state == "ready")

    def start(self) -> None:
        """Begin one feeder and one lazy managed-runtime acquisition."""

        feeder: Thread | None = None
        bootstrap: Thread | None = None
        with self._lock:
            if self._state != "idle":
                return
            self._state = "starting"
            try:
                feeder = Thread(
                    target=self._feeder_loop,
                    name="elysia-desktop-speech-feeder",
                    daemon=True,
                )
                bootstrap = Thread(
                    target=self._bootstrap,
                    name="elysia-desktop-speech-bootstrap",
                    daemon=True,
                )
            except BaseException:
                # Ownership has not left this coordinator; the common failure
                # path below closes the writer and publishes settled state.
                pass
            else:
                self._feeder_thread = feeder
                self._bootstrap_thread = bootstrap
        if feeder is None or bootstrap is None:
            # Thread creation is optional-voice infrastructure failure, not a
            # reason to fail Backend initialization or retain the fd3 writer.
            self._mark_unavailable("thread_creation_failed")
            return
        try:
            # Start the waiter first. It cannot act until bootstrap publishes a
            # ready queue, but this order guarantees no ready turn can miss its
            # only admission daemon.
            feeder.start()
            bootstrap.start()
        except BaseException:
            self._mark_unavailable("thread_start_failed")

    def start_turn(self, request_id: str, chat_id: str) -> DesktopSpeechTurn:
        """Replace stale playback and open one text-independent speech turn."""

        if type(request_id) is not str or type(chat_id) is not str:
            raise TypeError("request_id and chat_id must be strings.")
        with self._turn_transition_lock:
            self.start()
            # Protocol identifiers intentionally accept a wider Unicode
            # surface than the queue's internal identifier grammar. A fresh
            # opaque bridge ID preserves that contract without request text.
            record = _TurnRecord(
                request_id=request_id,
                chat_id=chat_id,
                queue_turn_id=f"desktop-{secrets.token_hex(16)}",
            )
            with self._work_condition:
                previous, self._active_turn = self._active_turn, None
                self._work_condition.notify_all()
            if previous is not None:
                self._cancel_turn(previous)

            delegate_to_cancel: SpeechTurn | None = None
            with self._work_condition:
                if self._state in ("unavailable", "closed"):
                    record.cancelled = True
                else:
                    self._active_turn = record
                    if self._state == "ready":
                        delegate_to_cancel = self._attach_delegate_locked(record)
                self._work_condition.notify_all()
            if delegate_to_cancel is not None:
                self._discard_delegate(delegate_to_cancel)
            elif record.cancelled:
                self._emit_undelivered_cancel(record)
            return _DesktopSpeechTurnHandle(self, record)

    def cancel_turn(self, request_id: str, chat_id: str) -> bool:
        """Cancel only the exact active speech turn named by both identifiers.

        ``False`` is a successful idempotent outcome when the turn is already
        cancelled or terminal, or when either identifier is stale. Serializing
        the ownership check with ``start_turn`` prevents a late interruption
        from cancelling a replacement turn that reused neither full key.
        """

        if type(request_id) is not str or type(chat_id) is not str:
            raise TypeError("request_id and chat_id must be strings.")
        with self._turn_transition_lock:
            with self._lock:
                record = self._active_turn
                if (
                    record is None
                    or record.request_id != request_id
                    or record.chat_id != chat_id
                ):
                    return False
                claimed, delegate = self._claim_turn_cancellation_locked(record)
            if not claimed:
                return False
            self._finish_turn_cancellation(record, delegate)
            return True

    def wait_until_settled(self, timeout_seconds: float | None = None) -> bool:
        """Wait for ready/unavailable/closed state for tests and diagnostics."""

        if timeout_seconds is not None and (
            type(timeout_seconds) is not float or timeout_seconds < 0.0
        ):
            raise ValueError("timeout_seconds must be a non-negative float.")
        return self._settled.wait(timeout_seconds)

    def shutdown(self) -> None:
        """Stop admission and initiate bounded queue, worker, and pipe cleanup."""

        with self._work_condition:
            if self._state == "closed":
                return
            self._state = "closed"
            self._settled.set()
            active, self._active_turn = self._active_turn, None
            if active is not None:
                # Queue shutdown below performs non-blocking cancellation.
                # The ordinary turn method could wait behind a pipe write after
                # Electron has already begun its own teardown.
                active.cancelled = True
                active.pending_blocks.clear()
                active.pending_code_points = 0
            queue, self._queue = self._queue, None
            runtime, self._runtime = self._runtime, None
            writer, self._audio_writer = self._audio_writer, None
            self._binding = None
            self._work_condition.notify_all()
        self._release_optional_resources(writer, queue, runtime)

    @staticmethod
    def _release_optional_resources(
        writer: AudioChannelWriter | None,
        queue: SpeechSynthesisQueue | None,
        runtime: ManagedGptSovitsRuntime | None,
    ) -> None:
        """Attempt every independent cleanup even when an earlier owner fails."""

        if writer is not None:
            try:
                writer.close()
            except BaseException:
                logger.error("Desktop speech audio channel did not close cleanly.")
        if queue is not None:
            try:
                queue.shutdown(wait=False, cancel_pending=True)
            except BaseException:
                logger.error("Desktop speech queue did not close cleanly.")
        if runtime is not None:
            try:
                runtime.shutdown()
            except BaseException:
                logger.error("Desktop speech runtime did not close cleanly.")

    def _bootstrap(self) -> None:
        """Acquire a fixed catalog selection without blocking Backend startup."""

        runtime: ManagedGptSovitsRuntime | None = None
        queue: SpeechSynthesisQueue | None = None
        try:
            runtime = ManagedGptSovitsRuntime(
                ManagedGptSovitsConfig(
                    runtime_root=self._config.runtime_root,
                    worker_script=self._config.worker_script,
                    synthesis_timeout_seconds=(
                        self._config.synthesis_timeout_seconds
                    ),
                    device="cuda",
                    seed=self._config.deterministic_seed,
                )
            )
            with self._work_condition:
                publish_runtime = self._state == "starting"
                if publish_runtime:
                    self._runtime = runtime
                self._work_condition.notify_all()
            if not publish_runtime:
                # This runtime has not crossed the coordinator ownership
                # boundary. A concurrent terminal failure or shutdown therefore
                # cannot have detached it and the bootstrap thread must clean it.
                self._release_optional_resources(None, None, runtime)
                return
            catalog = JsonVoiceProfileCatalog.load(
                self._config.catalog_path,
                self._config.asset_root,
                allow_local_evaluation=self._config.allow_local_evaluation,
            )
            selection = catalog.resolve_selection(
                self._config.voice_profile_id,
                self._config.voice_emotion,
            )
            # The persisted percentage is an absolute user-facing playback
            # rate. Replacing the catalog default avoids compounding two rate
            # multipliers, which could exceed the worker's validated 0.5-2.0
            # boundary even though both inputs were independently valid.
            selection = replace(
                selection,
                speed_factor=self._config.speech_rate_percent / 100.0,
            )
            runtime_lease = runtime.acquire_lease(selection)
            binding = _create_managed_synthesis_binding_lease(
                lease_id=_LEASE_ID,
                cache_identity=_CACHE_IDENTITY,
                runtime_lease=runtime_lease,
                profile_id=selection.profile_id,
                emotion=selection.emotion,
                language=_DEFAULT_LANGUAGE,
                on_invalidated=self._mark_unavailable,
            )
            queue = SpeechSynthesisQueue(
                SpeechQueueConfig(
                    # Electron tracks four correlated turns. Matching that
                    # bound leaves room for one callback-blocked stale turn,
                    # one replacement terminal, and the current admission.
                    max_active_turns=4,
                    max_pending_sentences=8,
                    max_delivery_events=2,
                    max_delivery_bytes=64 * 1024 * 1024,
                    max_cache_entries=0,
                    max_cache_bytes=0,
                    max_retained_turns=16,
                )
            )
            with self._work_condition:
                publish_queue = self._state == "starting"
                if publish_queue:
                    self._binding = binding
                    self._queue = queue
                    self._state = "ready"
                    active = self._active_turn
                    delegate_to_cancel: SpeechTurn | None = None
                    if active is not None and not active.cancelled:
                        delegate_to_cancel = self._attach_delegate_locked(active)
                    self._settled.set()
                self._work_condition.notify_all()
            if not publish_queue:
                # Runtime ownership crossed the first checkpoint. Any state
                # transition away from ``starting`` detached and shut it down;
                # only this not-yet-published queue remains ours to release.
                self._release_optional_resources(None, queue, None)
                return
            if delegate_to_cancel is not None:
                self._discard_delegate(delegate_to_cancel)
            elif active is not None and active.cancelled:
                self._emit_undelivered_cancel(active)
        except BaseException:
            self._release_optional_resources(None, queue, runtime)
            self._mark_unavailable("bootstrap_failed")

    def _attach_delegate_locked(
        self,
        record: _TurnRecord,
    ) -> SpeechTurn | None:
        """Attach queue ownership and wake the independent admission daemon.

        The caller owns ``self._lock``.  Cancellation can wait for a delivery
        callback that also needs this lock, so cleanup is deliberately returned
        to the caller instead of running within the critical section.
        """

        queue = self._queue
        binding = self._binding
        if queue is None or binding is None or record.cancelled:
            return None
        try:
            delegate = queue.start_turn(
                record.queue_turn_id,
                binding,
                lambda event: self._on_queue_event(record, event),
            )
            record.delegate = delegate
            self._work_condition.notify_all()
            return None
        except BaseException:
            record.cancelled = True
            record.pending_blocks.clear()
            record.pending_code_points = 0
            if self._active_turn is record:
                self._active_turn = None
            self._work_condition.notify_all()
            return record.delegate

    @staticmethod
    def _append_spool_locked(record: _TurnRecord, chunk: str) -> None:
        """Append text as a bounded number of engine-safe input blocks.

        The spool limit bounds total text while fixed-size blocks also bound
        Python object overhead when an LLM yields one-character chunks. A
        block never exceeds the segmenter's public per-feed contract.
        """

        offset = 0
        if record.pending_blocks:
            tail = record.pending_blocks[-1]
            available = _SPEECH_SPOOL_BLOCK_CODE_POINTS - len(tail)
            if available > 0:
                copied = min(available, len(chunk))
                record.pending_blocks[-1] = tail + chunk[:copied]
                offset = copied
        while offset < len(chunk):
            end = min(
                offset + _SPEECH_SPOOL_BLOCK_CODE_POINTS,
                len(chunk),
            )
            record.pending_blocks.append(chunk[offset:end])
            offset = end
        record.pending_code_points += len(chunk)
        record.accepted_code_points += len(chunk)

    def _feeder_loop(self) -> None:
        """Drain spooled text through queue backpressure on one fixed daemon.

        The Chat worker only appends strings and signals this condition. The
        feeder is the sole blocking producer, and the queue wakes it whenever
        synthesis releases sentence capacity or cancellation closes the turn.
        """

        while True:
            with self._work_condition:
                record: _TurnRecord | None = None
                delegate: SpeechTurn | None = None
                block: str | None = None
                finish = False
                while record is None:
                    if self._state in ("unavailable", "closed"):
                        return
                    candidate = self._active_turn
                    if (
                        self._state == "ready"
                        and candidate is not None
                        and not candidate.cancelled
                        and candidate.delegate is not None
                    ):
                        if candidate.pending_blocks:
                            record = candidate
                            delegate = candidate.delegate
                            block = candidate.pending_blocks.popleft()
                            candidate.pending_code_points -= len(block)
                        elif (
                            candidate.input_finished
                            and not candidate.delegate_input_closed
                        ):
                            record = candidate
                            delegate = candidate.delegate
                            candidate.delegate_input_closed = True
                            finish = True
                    if record is None:
                        self._work_condition.wait()

            if delegate is None or record is None:
                self._mark_unavailable("admission_state_invalid")
                return
            try:
                if finish:
                    delegate.finish_waiting()
                elif block is not None:
                    delegate.feed_waiting(block)
                else:
                    raise RuntimeError("Speech feeder selected no operation.")
            except SpeechTurnStateError:
                # Replacement, explicit cancellation, shutdown, and a managed
                # runtime failure all close the queue turn and wake this exact
                # waiter. They are already owned by the corresponding state
                # transition and must not produce a second terminal action.
                with self._lock:
                    stale = (
                        record.cancelled
                        or record is not self._active_turn
                        or self._state in ("unavailable", "closed")
                    )
                if stale:
                    continue
                self._mark_unavailable("admission_turn_closed")
                return
            except BaseException:
                with self._lock:
                    terminal_state = self._state in ("unavailable", "closed")
                if terminal_state:
                    return
                self._mark_unavailable("admission_failed")
                return

    @staticmethod
    def _discard_delegate(delegate: SpeechTurn) -> None:
        """Discard stale speech without poisoning a healthy managed worker.

        Only one bounded native sentence can remain in flight. Letting it
        finish silently avoids turning an ordinary Chat replacement or Stop
        action into permanent speech loss for every later turn.
        """

        try:
            delegate.discard_nowait()
        except BaseException:
            # A failed optional queue is subsequently retired by its normal
            # shutdown path; text Chat must not inherit the failure.
            pass

    def _feed_turn(self, record: _TurnRecord, chunk: str) -> None:
        """Spool one exact Chat chunk without waiting for speech capacity."""

        if type(chunk) is not str:
            raise TypeError("chunk must be a string.")
        if not chunk:
            return
        truncated = False
        with self._work_condition:
            if (
                record is not self._active_turn
                or record.cancelled
                or record.input_finished
            ):
                return
            next_size = record.accepted_code_points + len(chunk)
            if next_size > _SPEECH_SPOOL_MAX_CODE_POINTS:
                # Backend's canonical reply bound is checked before every
                # speech copy, so this is defense against an integration bug.
                # Finish the already accepted prefix instead of aborting the
                # active native call and poisoning all later speech.
                record.input_truncated = True
                record.input_finished = True
                truncated = True
            else:
                self._append_spool_locked(record, chunk)
            self._work_condition.notify_all()
        if truncated:
            logger.error(
                "Desktop speech input exceeded its bounded spool; "
                "the accepted prefix will finish."
            )

    def _finish_turn(self, record: _TurnRecord) -> None:
        """Close spool input without waiting for synthesis or playback."""

        with self._work_condition:
            if (
                record is not self._active_turn
                or record.cancelled
                or record.input_finished
            ):
                return
            record.input_finished = True
            self._work_condition.notify_all()

    def _cancel_turn(self, record: _TurnRecord) -> bool:
        """Mark one record stale and cancel without joining callbacks.

        A clip callback that already crossed the queue's delivery boundary can
        finish later.  The Electron delivery owner independently rejects stale
        or terminal turns, so replacing a Chat never waits for that callback.
        """

        with self._work_condition:
            claimed, delegate = self._claim_turn_cancellation_locked(record)
        if not claimed:
            return False
        self._finish_turn_cancellation(record, delegate)
        return True

    def _claim_turn_cancellation_locked(
        self,
        record: _TurnRecord,
    ) -> tuple[bool, SpeechTurn | None]:
        """Linearize cancellation against terminal delivery under ``_lock``."""

        if record.cancelled or record.terminal_emitted:
            return False, None
        record.cancelled = True
        record.pending_blocks.clear()
        record.pending_code_points = 0
        if self._active_turn is record:
            self._active_turn = None
        self._work_condition.notify_all()
        return True, record.delegate

    def _finish_turn_cancellation(
        self,
        record: _TurnRecord,
        delegate: SpeechTurn | None,
    ) -> None:
        """Complete a claimed cancellation without holding coordinator locks."""

        if delegate is not None:
            self._discard_delegate(delegate)
        else:
            self._emit_undelivered_cancel(record)

    def _emit_undelivered_cancel(self, record: _TurnRecord) -> None:
        """Retire one turn that never acquired a queue-owned terminal event."""

        with self._lock:
            if (
                not record.cancelled
                or record.delegate is not None
                or record.terminal_emitted
            ):
                return
            record.terminal_emitted = True
        try:
            self._event_sink(
                "voice.speech.terminal",
                record.request_id,
                {
                    "chatId": record.chat_id,
                    "state": "cancelled",
                    "submittedSentences": 0,
                    "completedSentences": 0,
                    "failedSentences": 0,
                },
            )
        except BaseException:
            self._mark_unavailable("undelivered_terminal_failed")

    def _on_queue_event(
        self,
        record: _TurnRecord,
        event: SpeechQueueEvent,
    ) -> None:
        """Translate one queue event and pair clip metadata before fd3 bytes."""

        try:
            if isinstance(event, SpeechQueueClip):
                with self._lock:
                    writer = self._audio_writer
                    if self._state != "ready" or writer is None:
                        return
                frame = writer.prepare_wav(event.result.audio, event.sequence)
                data = {"chatId": record.chat_id, **frame.metadata()}
                try:
                    self._event_sink(
                        "voice.speech.clip",
                        record.request_id,
                        data,
                    )
                except BaseException:
                    writer.discard_frame(frame)
                    raise
                writer.write_frame(frame)
                return
            if isinstance(event, SpeechQueueFailure):
                self._event_sink(
                    "voice.speech.failure",
                    record.request_id,
                    {
                        "chatId": record.chat_id,
                        "sequence": event.sequence,
                        "code": event.code,
                    },
                )
                return
            if not isinstance(event, SpeechTurnTerminal):
                raise TypeError("Speech queue emitted an unknown event.")
            with self._work_condition:
                if record.terminal_emitted:
                    raise RuntimeError(
                        "Speech turn emitted duplicate terminal state."
                    )
                # Claim terminality before calling the external sink so an
                # exact cancel racing that callback has one deterministic
                # winner and can never report that it stopped settled work.
                record.terminal_emitted = True
                if self._active_turn is record:
                    self._active_turn = None
                self._work_condition.notify_all()
            self._event_sink(
                "voice.speech.terminal",
                record.request_id,
                {
                    "chatId": record.chat_id,
                    "state": event.state,
                    "submittedSentences": event.submitted_sentences,
                    "completedSentences": event.completed_sentences,
                    "failedSentences": event.failed_sentences,
                },
            )
        except BaseException:
            self._mark_unavailable("delivery_failed")

    def _mark_unavailable(
        self,
        reason: str = "managed_runtime_invalidated",
    ) -> None:
        """Disable failed optional speech with one non-sensitive reason code."""

        undelivered: _TurnRecord | None = None
        with self._work_condition:
            if self._state in ("unavailable", "closed"):
                return
            self._state = "unavailable"
            self._settled.set()
            active, self._active_turn = self._active_turn, None
            if active is not None:
                # This path can run from the queue notifier itself. Local state
                # plus non-blocking queue shutdown cannot wait on that callback.
                active.cancelled = True
                active.pending_blocks.clear()
                active.pending_code_points = 0
                if active.delegate is None:
                    # Bootstrap can fail on either side of start_turn's state
                    # check. Retire pre-queue work in both schedules so the
                    # observable lifecycle never depends on thread timing.
                    undelivered = active
            queue, self._queue = self._queue, None
            runtime, self._runtime = self._runtime, None
            writer, self._audio_writer = self._audio_writer, None
            self._binding = None
            self._work_condition.notify_all()
        self._release_optional_resources(writer, queue, runtime)
        if undelivered is not None:
            self._emit_undelivered_cancel(undelivered)
        logger.error(
            "Optional managed desktop speech became unavailable: reason=%s.",
            reason,
        )


__all__ = [
    "DesktopSpeechConfig",
    "DesktopSpeechCoordinator",
    "DesktopSpeechEventSink",
    "DesktopSpeechState",
    "DesktopSpeechStatus",
    "DesktopSpeechTurn",
]
