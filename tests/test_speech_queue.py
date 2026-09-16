"""Test streaming sentence synthesis without a model, GPU, or audio device."""

from __future__ import annotations

from collections.abc import Callable
import gc
from io import BytesIO
import re
from threading import Event, Lock, Thread, current_thread
from time import monotonic, sleep
import wave
import weakref

import pytest

import voice as voice_package
from voice.speech_queue import (
    SPEECH_SEGMENT_MAX_CHUNK_CODE_POINTS,
    SentenceSegmenterConfig,
    SpeechQueueCapacityError,
    SpeechQueueClip,
    SpeechQueueConfig,
    SpeechQueueFailure,
    SpeechQueueValidationError,
    SpeechSynthesisBindingLease,
    SpeechSynthesisQueue,
    SpeechTurn,
    SpeechTurnStateError,
    SpeechTurnTerminal,
    StreamingSentenceSegmenter,
    _create_managed_synthesis_binding_lease,
)
from voice.synthesis import (
    SYNTHESIS_MAX_AUDIO_BYTES,
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
)


def _wav_bytes(sample: int = 1) -> bytes:
    """Build one tiny valid PCM WAV result for deterministic queue tests."""

    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(sample.to_bytes(2, "little", signed=True) * 32)
    return output.getvalue()


def _result(sample: int = 1) -> SynthesisResult:
    """Return one validated test result with distinguishable sample bytes."""

    return SynthesisResult(_wav_bytes(sample), "wav", 1.0)


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 2.0,
) -> None:
    """Poll a concurrency-safe predicate under one short test deadline."""

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Timed out waiting for speech queue state.")


class _SequenceSynthesizer:
    """Return or raise scripted outcomes while recording exact requests."""

    def __init__(self, outcomes: list[object] | None = None) -> None:
        """Store repeatable outcomes behind a thread-safe request log."""

        self._outcomes = outcomes or [_result()]
        self._lock = Lock()
        self.requests: list[SynthesisRequest] = []

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Record one request and resolve the next scripted outcome."""

        with self._lock:
            index = min(len(self.requests), len(self._outcomes) - 1)
            self.requests.append(request)
            outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, SynthesisResult)
        return outcome


class _BlockingSynthesizer:
    """Hold one physical call so capacity and cancellation can be observed."""

    def __init__(self) -> None:
        """Create explicit start/release gates and an empty request log."""

        self.started = Event()
        self.release = Event()
        self.requests: list[SynthesisRequest] = []

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Wait for the test without pretending the native call was cancelled."""

        self.requests.append(request)
        self.started.set()
        assert self.release.wait(2.0)
        return _result()


class _ManagedSequenceLease:
    """Model a factory-owned runtime lease without starting a child process."""

    def __init__(self, outcomes: list[object] | None = None) -> None:
        """Store scripted outcomes and token-aware operation logs."""

        self._outcomes = outcomes or [_result()]
        self._lock = Lock()
        self.calls: list[tuple[str, str, str]] = []
        self.abort_tokens: list[str] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep the fake aligned with the incomplete production manifest."""

        return False

    @property
    def poisoned(self) -> bool:
        """Keep ordinary scripted calls reusable across sentence failures."""

        return False

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Record one managed call and resolve its scripted outcome."""

        with self._lock:
            index = min(len(self.calls), len(self._outcomes) - 1)
            self.calls.append((text, language, operation_token))
            outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, SynthesisResult)
        return outcome

    def abort(self, operation_token: str) -> bool:
        """Record a best-effort abort without inventing an active operation."""

        with self._lock:
            self.abort_tokens.append(operation_token)
        return False


class _BlockingManagedLease:
    """Expose running managed synthesis and independently callable abort."""

    def __init__(
        self,
        *,
        release_on_abort: bool = False,
        abort_error: BaseException | None = None,
    ) -> None:
        """Create deterministic synthesis, abort, and optional failure gates."""

        self.release_on_abort = release_on_abort
        self.abort_error = abort_error
        self.started = Event()
        self.release = Event()
        self.abort_called = Event()
        self.on_abort: Callable[[], None] | None = None
        self.calls: list[tuple[str, str, str]] = []
        self.abort_tokens: list[str] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep this managed fake non-cacheable like the current runtime."""

        return False

    @property
    def poisoned(self) -> bool:
        """Leave lifecycle poisoning to dedicated integration fakes."""

        return False

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Hold one physical operation until abort or the test releases it."""

        self.calls.append((text, language, operation_token))
        self.started.set()
        assert self.release.wait(2.0)
        return _result()

    def abort(self, operation_token: str) -> bool:
        """Run the configured lock-probe and optionally release synthesis."""

        self.abort_tokens.append(operation_token)
        self.abort_called.set()
        if self.on_abort is not None:
            self.on_abort()
        if self.release_on_abort:
            self.release.set()
        if self.abort_error is not None:
            raise self.abort_error
        return self.release_on_abort


class _StaleAwareManagedLease:
    """Delay token comparison to reproduce an abort-after-next-start race."""

    def __init__(self) -> None:
        """Create separate gates for two serialized synthesis operations."""

        self._lock = Lock()
        self._active_token: str | None = None
        self.first_started = Event()
        self.release_first = Event()
        self.second_started = Event()
        self.release_second = Event()
        self.abort_entered = Event()
        self.allow_abort_compare = Event()
        self.calls: list[tuple[str, str, str]] = []
        self.aborted_tokens: list[str] = []
        self.ignored_tokens: list[str] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep both operations outside the audio cache."""

        return False

    @property
    def poisoned(self) -> bool:
        """Model token ordering independently from generation lifetime."""

        return False

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Publish one active token and clear it only when still current."""

        with self._lock:
            self._active_token = operation_token
            self.calls.append((text, language, operation_token))
        if len(self.calls) == 1:
            self.first_started.set()
            assert self.release_first.wait(2.0)
            sample = 1
        else:
            self.second_started.set()
            assert self.release_second.wait(2.0)
            sample = 2
        with self._lock:
            if self._active_token == operation_token:
                self._active_token = None
        return _result(sample)

    def abort(self, operation_token: str) -> bool:
        """Compare late so a newer token can prove stale abort isolation."""

        self.abort_entered.set()
        assert self.allow_abort_compare.wait(2.0)
        with self._lock:
            if self._active_token != operation_token:
                self.ignored_tokens.append(operation_token)
                return False
            self._active_token = None
            self.aborted_tokens.append(operation_token)
            return True


class _PreStartAbortManagedLease:
    """Remember aborts that arrive before synthesis registers active work."""

    def __init__(self) -> None:
        """Create a deterministic gap before the first simulated I/O call."""

        self._lock = Lock()
        self._abort_tombstones: set[str] = set()
        self.synthesize_entered = Event()
        self.allow_registration = Event()
        self.io_started = False
        self.calls: list[tuple[str, str, str]] = []
        self.abort_tokens: list[str] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep pre-start cancellation independent of audio caching."""

        return False

    @property
    def poisoned(self) -> bool:
        """Confirm a pre-start tombstone does not invalidate the worker."""

        return False

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Fail a tombstoned token before simulating protocol or model work."""

        self.calls.append((text, language, operation_token))
        self.synthesize_entered.set()
        assert self.allow_registration.wait(2.0)
        with self._lock:
            if operation_token in self._abort_tombstones:
                raise SynthesisUnavailableError("pre-start operation cancelled")
            self.io_started = True
        return _result()

    def abort(self, operation_token: str) -> bool:
        """Tombstone a token even when no active operation exists yet."""

        with self._lock:
            self._abort_tombstones.add(operation_token)
            self.abort_tokens.append(operation_token)
        return True


class _SlowAbortManagedLease(_BlockingManagedLease):
    """Block physical abort so non-blocking shutdown can be measured."""

    def __init__(self) -> None:
        """Create independent gates for abort dispatch and synthesis return."""

        super().__init__()
        self.abort_started = Event()
        self.release_abort = Event()
        self.abort_threads: list[str] = []

    def abort(self, operation_token: str) -> bool:
        """Wait on the dispatcher without occupying the shutdown caller."""

        self.abort_tokens.append(operation_token)
        self.abort_threads.append(current_thread().name)
        self.abort_started.set()
        if self.on_abort is not None:
            self.on_abort()
        assert self.release_abort.wait(2.0)
        self.release.set()
        return True


class _SpoofedBindingLease(SpeechSynthesisBindingLease):
    """Attempt to override immutable trust through ordinary subclassing."""

    @property
    def binding_verified(self) -> bool:
        """Pretend the subclass owns a verified managed process."""

        return True

    @property
    def cache_eligible(self) -> bool:
        """Pretend incomplete runtime evidence permits audio reuse."""

        return True


class _CacheClaimingManagedLease(_ManagedSequenceLease):
    """Pretend a partial runtime manifest already permits audio caching."""

    @property
    def cache_eligible(self) -> bool:
        """Return the unsafe claim that the private factory must reject."""

        return True


class _PoisoningManagedLease(_ManagedSequenceLease):
    """Expose explicit terminal invalidation for binding callback tests."""

    def __init__(self, *, poison_during_synthesis: bool = False) -> None:
        """Start healthy and optionally fail while executing synthesis."""

        super().__init__()
        self._poisoned = False
        self._poison_during_synthesis = poison_during_synthesis

    @property
    def poisoned(self) -> bool:
        """Return whether this fake generation became permanently unusable."""

        return self._poisoned

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Poison and fail when requested, otherwise use the scripted result."""

        if self._poison_during_synthesis:
            self._poisoned = True
            raise KeyboardInterrupt("private-managed-poison")
        return super().synthesize(text, language, operation_token)

    def abort(self, operation_token: str) -> bool:
        """Poison this generation as a matching active abort would."""

        self.abort_tokens.append(operation_token)
        self._poisoned = True
        return True


class _TerminalObserver:
    """Expose terminal delivery without retaining event payloads in a list."""

    def __init__(self, delivered: Event) -> None:
        """Retain only the caller-owned completion signal."""

        self._delivered = delivered

    def __call__(self, event: object) -> None:
        """Signal after the terminal reaches this callback."""

        if isinstance(event, SpeechTurnTerminal):
            self._delivered.set()


def _lease(
    synthesizer: object,
    *,
    lease_id: str = "lease-one",
    cache_identity: str = "binding-one",
) -> SpeechSynthesisBindingLease:
    """Build one ordinary external-service lease for queue tests."""

    return SpeechSynthesisBindingLease(
        lease_id=lease_id,
        cache_identity=cache_identity,
        synthesizer=synthesizer,  # type: ignore[arg-type]
        profile_id="default",
        emotion="neutral",
        language="auto",
    )


def _managed_lease(
    runtime_lease: object,
    *,
    lease_id: str = "managed-lease-one",
    cache_identity: str = "managed-binding-one",
    language: str = "zh",
    on_invalidated: Callable[[], None] | None = None,
) -> SpeechSynthesisBindingLease:
    """Issue one private-factory lease around a model-free managed fake."""

    return _create_managed_synthesis_binding_lease(
        lease_id=lease_id,
        cache_identity=cache_identity,
        runtime_lease=runtime_lease,  # type: ignore[arg-type]
        profile_id="elysia-v2",
        emotion="neutral",
        language=language,  # type: ignore[arg-type]
        on_invalidated=on_invalidated,
    )


def test_segmenter_preserves_arbitrary_chunk_boundaries() -> None:
    """Hold terminal punctuation briefly and keep its closing quote attached."""

    segmenter = StreamingSentenceSegmenter()

    assert segmenter.feed("你好") == ()
    assert segmenter.feed("呀！") == ()
    assert segmenter.feed("”下一句") == (
        # A closing quote arriving in the next model chunk belongs to the
        # previous utterance instead of becoming a punctuation-only fragment.
        segmenter_sentence(0, "你好呀！”"),
    )
    assert segmenter.finish() == (segmenter_sentence(1, "下一句"),)


def test_sentence_queue_api_is_exported_from_voice_package() -> None:
    """Keep the scheduler and binding lease available through Voice API."""

    assert voice_package.SpeechSynthesisQueue is SpeechSynthesisQueue
    assert voice_package.StreamingSentenceSegmenter is StreamingSentenceSegmenter
    assert voice_package.SpeechSynthesisBindingLease is SpeechSynthesisBindingLease


def segmenter_sentence(sequence: int, text: str):
    """Create a sentence without repeating its imported concrete type."""

    from voice.speech_queue import SpeechSentence

    return SpeechSentence(sequence, text)


def test_segmenter_uses_soft_then_hard_length_boundaries() -> None:
    """Prefer a recent pause but bound long CJK text without whitespace."""

    config = SentenceSegmenterConfig(max_code_points=32, soft_break_floor=16)
    soft = StreamingSentenceSegmenter(config)
    sentences = soft.feed(("a" * 20) + ", " + ("b" * 15))
    assert sentences == (segmenter_sentence(0, ("a" * 20) + ", "),)
    assert soft.finish() == (segmenter_sentence(1, "b" * 15),)

    hard = StreamingSentenceSegmenter(config)
    assert hard.feed("你" * 40) == (segmenter_sentence(0, "你" * 32),)
    assert hard.finish() == (segmenter_sentence(1, "你" * 8),)


def test_distant_punctuation_cannot_bypass_the_hard_ceiling() -> None:
    """Split at the length bound before considering a much later period."""

    segmenter = StreamingSentenceSegmenter(
        SentenceSegmenterConfig(max_code_points=32, soft_break_floor=16)
    )
    sentences = segmenter.feed(("a" * 40) + ". ")

    assert sentences[0] == segmenter_sentence(0, "a" * 32)
    assert sentences[1] == segmenter_sentence(1, ("a" * 8) + ".")
    assert all(len(sentence.text) <= 32 for sentence in sentences)


def test_trailing_closers_cannot_bypass_the_hard_ceiling() -> None:
    """Bound a pathological run of closing punctuation after a period."""

    segmenter = StreamingSentenceSegmenter(
        SentenceSegmenterConfig(max_code_points=32, soft_break_floor=16)
    )
    sentences = segmenter.feed(("a" * 31) + "." + (")" * 50) + "x")

    assert sentences[0] == segmenter_sentence(0, ("a" * 31) + ".")
    assert all(len(sentence.text) <= 32 for sentence in sentences)


def test_segmenter_drops_only_non_speakable_final_tail() -> None:
    """Avoid consuming synthesis capacity for whitespace and punctuation."""

    segmenter = StreamingSentenceSegmenter()
    assert segmenter.feed("？！  ") == ()
    assert segmenter.finish() == ()
    with pytest.raises(SpeechTurnStateError):
        segmenter.feed("late")


def test_segmenter_rejects_oversized_chunk_before_mutating_state() -> None:
    """Prevent one protocol frame from constructing an attacker-sized batch."""

    segmenter = StreamingSentenceSegmenter()
    oversized = "A" * (SPEECH_SEGMENT_MAX_CHUNK_CODE_POINTS + 1)
    with pytest.raises(SpeechQueueValidationError, match="4096"):
        segmenter.feed(oversized)

    assert segmenter.feed("Safe. ") == (segmenter_sentence(0, "Safe."),)
    assert segmenter.finish() == ()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"max_code_points": True}, "max_code_points"),
        ({"max_code_points": 31}, "max_code_points"),
        ({"max_code_points": 1_025}, "max_code_points"),
        (
            {"max_code_points": 32, "soft_break_floor": 32},
            "soft_break_floor",
        ),
    ],
)
def test_segmenter_config_rejects_unsafe_bounds(
    values: dict[str, object],
    message: str,
) -> None:
    """Reject booleans and limits outside the synthesis contract."""

    with pytest.raises(SpeechQueueValidationError, match=message):
        SentenceSegmenterConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"max_delivery_events": True}, "max_delivery_events"),
        ({"max_delivery_events": 0}, "max_delivery_events"),
        (
            {"max_delivery_bytes": SYNTHESIS_MAX_AUDIO_BYTES - 1},
            "max_delivery_bytes",
        ),
        ({"max_delivery_bytes": 128 * 1024 * 1024 + 1}, "max_delivery_bytes"),
    ],
)
def test_queue_config_rejects_unsafe_delivery_bounds(
    values: dict[str, object],
    message: str,
) -> None:
    """Require enough preallocated bytes for one maximum valid result."""

    with pytest.raises(SpeechQueueValidationError, match=message):
        SpeechQueueConfig(**values)  # type: ignore[arg-type]


def test_binding_lease_freezes_selection_and_does_not_invent_trust() -> None:
    """Keep profile metadata immutable and external bindings unverified."""

    synthesizer = _SequenceSynthesizer()
    lease = SpeechSynthesisBindingLease(
        lease_id="lease-fixed",
        cache_identity="binding-fixed",
        synthesizer=synthesizer,
        profile_id="voice-a",
        emotion="happy",
        language="zh",
    )

    assert lease.binding_verified is False
    assert lease.cache_eligible is False
    assert lease.synthesize("你好", "a" * 64) == _result()
    assert lease.abort("b" * 64) is False
    assert synthesizer.requests == [
        SynthesisRequest("你好", "zh", "voice-a", "happy")
    ]
    with pytest.raises(SpeechQueueValidationError, match="external-unverified"):
        SpeechSynthesisBindingLease(
            lease_id="lease-bad",
            cache_identity="binding-bad",
            synthesizer=synthesizer,
            binding_trust="elysia-owned",  # type: ignore[arg-type]
        )


def test_private_factory_issues_owned_non_cacheable_binding() -> None:
    """Bind managed methods and language without exposing a public trust flag."""

    runtime = _ManagedSequenceLease()
    lease = _managed_lease(runtime, language="en")
    token = "c" * 64

    assert type(lease) is SpeechSynthesisBindingLease
    assert lease.binding_verified is True
    assert lease.cache_eligible is False
    assert lease.binding_trust == "elysia-owned"
    assert lease.synthesize("Hello", token) == _result()
    assert lease.abort(token) is False
    assert runtime.calls == [("Hello", "en", token)]
    assert runtime.abort_tokens == [token]
    assert token not in repr(lease)


def test_managed_binding_notifies_once_only_after_generation_poison() -> None:
    """Distinguish a healthy tombstone from permanent managed invalidation."""

    notifications: list[str] = []
    tombstone_runtime = _PreStartAbortManagedLease()
    tombstone_binding = _managed_lease(
        tombstone_runtime,
        on_invalidated=lambda: notifications.append("tombstone"),
    )

    assert tombstone_binding.abort("d" * 64)
    assert notifications == []

    poisoned_runtime = _PoisoningManagedLease()
    poisoned_binding = _managed_lease(
        poisoned_runtime,
        lease_id="managed-poisoned-abort",
        on_invalidated=lambda: notifications.append("poisoned"),
    )
    assert poisoned_binding.abort("e" * 64)
    assert poisoned_binding.abort("f" * 64)
    assert notifications == ["poisoned"]


def test_managed_binding_notifies_when_synthesis_poisons_generation() -> None:
    """Surface spontaneous native invalidation before queue failure mapping."""

    notifications: list[str] = []
    runtime = _PoisoningManagedLease(poison_during_synthesis=True)
    binding = _managed_lease(
        runtime,
        on_invalidated=lambda: notifications.append("poisoned"),
    )

    with pytest.raises(KeyboardInterrupt, match="private-managed-poison"):
        binding.synthesize("Hello", "a" * 64)
    binding.abort("b" * 64)

    assert notifications == ["poisoned"]


def test_private_factory_rejects_a_runtime_cache_claim() -> None:
    """Refuse cache eligibility until the complete manifest work exists."""

    with pytest.raises(TypeError, match="non-cacheable managed contract"):
        _managed_lease(_CacheClaimingManagedLease())


@pytest.mark.parametrize("token", ["a" * 63, "A" * 64, "g" * 64, ""])
def test_binding_lease_rejects_malformed_operation_tokens(token: str) -> None:
    """Keep abort correlation fixed-width and canonical at every lease edge."""

    lease = _lease(_SequenceSynthesizer())
    with pytest.raises(SpeechQueueValidationError, match="operation_token"):
        lease.synthesize("Hello", token)
    with pytest.raises(SpeechQueueValidationError, match="operation_token"):
        lease.abort(token)


def test_queue_rejects_subclass_that_spoofs_managed_trust() -> None:
    """Admit only the exact factory type instead of virtual trust properties."""

    spoofed = _SpoofedBindingLease(
        lease_id="spoofed-lease",
        cache_identity="spoofed-binding",
        synthesizer=_SequenceSynthesizer(),
    )
    queue = SpeechSynthesisQueue()
    try:
        with pytest.raises(TypeError, match="SpeechSynthesisBindingLease"):
            queue.start_turn("turn-spoofed", spoofed, lambda _event: None)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_managed_sentences_receive_unique_tokens_and_never_cache() -> None:
    """Keep managed audio uncached while correlating every physical call."""

    runtime = _ManagedSequenceLease([_result(1), _result(2), _result(3)])
    first_events: list[object] = []
    second_events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        first = queue.start_turn(
            "turn-managed-token-a",
            _managed_lease(runtime),
            first_events.append,
        )
        first.feed("Repeat. Again. ")
        first.finish()
        _wait_until(lambda: len(first_events) == 3)

        second = queue.start_turn(
            "turn-managed-token-b",
            _managed_lease(runtime, lease_id="managed-lease-two"),
            second_events.append,
        )
        second.feed("Repeat. ")
        second.finish()
        _wait_until(lambda: len(second_events) == 2)

        tokens = [call[2] for call in runtime.calls]
        assert [call[:2] for call in runtime.calls] == [
            ("Repeat.", "zh"),
            (" Again.", "zh"),
            ("Repeat.", "zh"),
        ]
        assert len(set(tokens)) == 3
        assert all(re.fullmatch(r"[0-9a-f]{64}", token) for token in tokens)
        clips = [
            event
            for event in (*first_events, *second_events)
            if isinstance(event, SpeechQueueClip)
        ]
        assert len(clips) == 3
        assert all(clip.cache_hit is False for clip in clips)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_queue_synthesizes_sentences_in_fifo_order() -> None:
    """Emit sentence audio and one terminal in the original reply order."""

    synthesizer = _SequenceSynthesizer([_result(1), _result(2)])
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        turn = queue.start_turn("turn-order", _lease(synthesizer), events.append)
        assert tuple(sentence.text for sentence in turn.feed("One. Two. ")) == (
            "One.",
            " Two.",
        )
        assert turn.finish() == ()
        _wait_until(lambda: any(isinstance(item, SpeechTurnTerminal) for item in events))

        assert [request.text for request in synthesizer.requests] == ["One.", " Two."]
        clips = [item for item in events if isinstance(item, SpeechQueueClip)]
        assert [clip.sequence for clip in clips] == [0, 1]
        assert [clip.result for clip in clips] == [_result(1), _result(2)]
        assert events[-1] == SpeechTurnTerminal("turn-order", "completed", 2, 2, 0)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_slow_delivery_cannot_create_an_unbounded_audio_backlog() -> None:
    """Reserve worst-case bytes before synthesis and stop at delivery credits."""

    first_clip_entered = Event()
    release_first_clip = Event()
    events: list[object] = []

    def callback(event: object) -> None:
        """Block the first clip so producer and worker bounds are observable."""

        if isinstance(event, SpeechQueueClip) and not first_clip_entered.is_set():
            first_clip_entered.set()
            assert release_first_clip.wait(2.0)
        events.append(event)

    synthesizer = _SequenceSynthesizer()
    queue = SpeechSynthesisQueue(
        SpeechQueueConfig(
            max_pending_sentences=8,
            max_delivery_events=2,
            max_delivery_bytes=2 * SYNTHESIS_MAX_AUDIO_BYTES,
        )
    )
    turn = queue.start_turn("turn-slow-delivery", _lease(synthesizer), callback)
    try:
        turn.feed("One. Two. Three. Four. Five. Six. ")
        assert first_clip_entered.wait(1.0)
        _wait_until(
            lambda: queue.get_status().pending_delivery_events == 2
            and queue.get_status().queued_sentences == 4
        )

        for index in range(4):
            turn.feed(f"More {index}. ")
        with pytest.raises(SpeechQueueCapacityError):
            turn.feed("Overflow. ")

        status = queue.get_status()
        assert len(synthesizer.requests) == 2
        assert status.pending_delivery_events == 2
        assert status.pending_delivery_clips == 2
        assert status.pending_delivery_bytes <= status.max_delivery_bytes
        assert status.delivery_callbacks_in_progress == 1
        assert status.occupied_slots == status.max_pending_sentences

        turn.finish()
        release_first_clip.set()
        _wait_until(
            lambda: any(isinstance(item, SpeechTurnTerminal) for item in events)
        )
        settled = queue.get_status()
        assert len(synthesizer.requests) == 10
        assert settled.pending_delivery_events == 0
        assert settled.pending_delivery_clips == 0
        assert settled.pending_delivery_bytes == 0
    finally:
        release_first_clip.set()
        queue.shutdown(timeout_seconds=2.0)


def test_queue_never_caches_an_unverified_external_binding() -> None:
    """Avoid stale voice reuse when an external process may hot-switch weights."""

    synthesizer = _SequenceSynthesizer([_result(3), _result(4)])
    queue = SpeechSynthesisQueue()
    first_events: list[object] = []
    second_events: list[object] = []
    third_events: list[object] = []
    try:
        first = queue.start_turn("turn-cache-a", _lease(synthesizer), first_events.append)
        first.feed("Repeat. ")
        first.finish()
        _wait_until(lambda: len(first_events) == 2)

        second = queue.start_turn(
            "turn-cache-b",
            _lease(synthesizer, lease_id="lease-two"),
            second_events.append,
        )
        second.feed("Repeat. ")
        second.finish()
        _wait_until(lambda: len(second_events) == 2)

        third = queue.start_turn(
            "turn-cache-c",
            _lease(
                synthesizer,
                lease_id="lease-three",
                cache_identity="binding-two",
            ),
            third_events.append,
        )
        third.feed("Repeat. ")
        third.finish()
        _wait_until(lambda: len(third_events) == 2)

        assert len(synthesizer.requests) == 3
        assert isinstance(first_events[0], SpeechQueueClip)
        assert first_events[0].cache_hit is False
        assert isinstance(second_events[0], SpeechQueueClip)
        assert second_events[0].cache_hit is False
        assert isinstance(third_events[0], SpeechQueueClip)
        assert third_events[0].cache_hit is False
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_queue_rejects_an_overflow_batch_without_partial_admission() -> None:
    """Keep text generation non-blocking and avoid half-enqueued feed calls."""

    synthesizer = _BlockingSynthesizer()
    events: list[object] = []
    queue = SpeechSynthesisQueue(SpeechQueueConfig(max_pending_sentences=2))
    try:
        first = queue.start_turn("turn-running", _lease(synthesizer), events.append)
        first.feed("One. ")
        assert synthesizer.started.wait(1.0)

        second = queue.start_turn(
            "turn-overflow",
            _lease(synthesizer, lease_id="lease-overflow"),
            events.append,
        )
        with pytest.raises(SpeechQueueCapacityError, match="capacity"):
            second.feed("Two. Three. ")
        assert second.cancel() is True
        assert first.cancel() is True
        synthesizer.release.set()
        assert queue.shutdown(timeout_seconds=2.0)
        assert len(synthesizer.requests) == 1
        assert not any(isinstance(item, SpeechQueueClip) for item in events)
    finally:
        synthesizer.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_capacity_rejection_rolls_back_segmenter_state_for_retry() -> None:
    """Retry the same chunk after capacity frees without loss or duplication."""

    synthesizer = _BlockingSynthesizer()
    queue = SpeechSynthesisQueue(SpeechQueueConfig(max_pending_sentences=1))
    first_events: list[object] = []
    retry_events: list[object] = []
    try:
        first = queue.start_turn("turn-holder", _lease(synthesizer), first_events.append)
        first.feed("Holding. ")
        first.finish()
        assert synthesizer.started.wait(1.0)

        retry = queue.start_turn(
            "turn-retry",
            _lease(synthesizer, lease_id="lease-retry"),
            retry_events.append,
        )
        with pytest.raises(SpeechQueueCapacityError):
            retry.feed("Retry. ")

        synthesizer.release.set()
        _wait_until(
            lambda: any(
                isinstance(item, SpeechTurnTerminal)
                for item in first_events
            )
        )
        assert tuple(sentence.text for sentence in retry.feed("Retry. ")) == (
            "Retry.",
        )
        retry.finish()
        _wait_until(
            lambda: any(
                isinstance(item, SpeechTurnTerminal)
                for item in retry_events
            )
        )
        clips = [item for item in retry_events if isinstance(item, SpeechQueueClip)]
        assert [clip.sequence for clip in clips] == [0]
        assert [request.text for request in synthesizer.requests] == [
            "Holding.",
            "Retry.",
        ]
    finally:
        synthesizer.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_running_cancellation_discards_late_audio() -> None:
    """Publish cancellation promptly but retain capacity until inference returns."""

    synthesizer = _BlockingSynthesizer()
    events: list[object] = []
    queue = SpeechSynthesisQueue(SpeechQueueConfig(max_pending_sentences=1))
    try:
        turn = queue.start_turn("turn-cancel", _lease(synthesizer), events.append)
        turn.feed("One. ")
        assert synthesizer.started.wait(1.0)
        running_status = queue.get_status()
        assert running_status.pending_delivery_events == 1
        assert running_status.pending_delivery_clips == 0
        assert running_status.pending_delivery_bytes == SYNTHESIS_MAX_AUDIO_BYTES
        assert turn.cancel() is True
        _wait_until(lambda: any(isinstance(item, SpeechTurnTerminal) for item in events))
        assert events == [SpeechTurnTerminal("turn-cancel", "cancelled", 1, 0, 0)]

        other = queue.start_turn(
            "turn-blocked",
            _lease(synthesizer, lease_id="lease-blocked"),
            events.append,
        )
        with pytest.raises(SpeechQueueCapacityError):
            other.feed("Blocked. ")
        other.cancel()

        synthesizer.release.set()
        assert queue.shutdown(timeout_seconds=2.0)
        assert not any(isinstance(item, SpeechQueueClip) for item in events)
        settled_status = queue.get_status()
        assert settled_status.pending_delivery_events == 0
        assert settled_status.pending_delivery_bytes == 0
    finally:
        synthesizer.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_managed_cancel_aborts_outside_the_queue_lock() -> None:
    """Let the abort boundary re-enter queue status without deadlocking."""

    runtime = _BlockingManagedLease(release_on_abort=True)
    events: list[object] = []
    observed_status: list[bool] = []
    cancel_results: list[bool] = []
    cancel_finished = Event()
    queue = SpeechSynthesisQueue()
    runtime.on_abort = lambda: observed_status.append(queue.get_status().closed)
    turn = queue.start_turn(
        "turn-managed-abort-lock",
        _managed_lease(runtime),
        events.append,
    )
    try:
        turn.feed("Interrupt me. ")
        assert runtime.started.wait(1.0)
        running_token = runtime.calls[0][2]

        def cancel() -> None:
            """Expose completion without making a deadlock hang the test run."""

            cancel_results.append(turn.cancel())
            cancel_finished.set()

        thread = Thread(target=cancel, daemon=True)
        thread.start()
        assert cancel_finished.wait(1.0)
        thread.join(1.0)
        _wait_until(lambda: queue.get_status().running_sentences == 0)

        assert cancel_results == [True]
        assert observed_status == [False]
        assert runtime.abort_tokens == [running_token]
        assert events == [
            SpeechTurnTerminal("turn-managed-abort-lock", "cancelled", 1, 0, 0)
        ]
    finally:
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_pre_start_abort_tombstone_prevents_managed_io() -> None:
    """Preserve a cancel that wins before runtime active-token registration.

    The queue supplies the same operation token to synthesize and abort.  The
    managed lease, rather than the queue, owns the pre-start tombstone that
    closes the remaining race before any protocol write or model inference.
    """

    runtime = _PreStartAbortManagedLease()
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-managed-pre-start-abort",
        _managed_lease(runtime),
        events.append,
    )
    try:
        turn.feed("Do not start I/O. ")
        assert runtime.synthesize_entered.wait(1.0)
        operation_token = runtime.calls[0][2]

        assert turn.cancel() is True
        assert runtime.abort_tokens == [operation_token]
        runtime.allow_registration.set()
        _wait_until(lambda: queue.get_status().running_sentences == 0)

        assert runtime.io_started is False
        assert len(runtime.calls) == 1
        assert events == [
            SpeechTurnTerminal(
                "turn-managed-pre-start-abort", "cancelled", 1, 0, 0
            )
        ]
    finally:
        runtime.allow_registration.set()
        queue.shutdown(timeout_seconds=2.0)


def test_stale_managed_abort_cannot_kill_the_following_sentence() -> None:
    """Ignore token A after the single worker has advanced to token B."""

    runtime = _StaleAwareManagedLease()
    first_events: list[object] = []
    second_events: list[object] = []
    cancel_results: list[bool] = []
    cancel_finished = Event()
    queue = SpeechSynthesisQueue()
    first = queue.start_turn(
        "turn-stale-abort-a",
        _managed_lease(runtime),
        first_events.append,
    )
    try:
        first.feed("First. ")
        first.finish()
        assert runtime.first_started.wait(1.0)
        first_token = runtime.calls[0][2]

        second = queue.start_turn(
            "turn-stale-abort-b",
            _managed_lease(runtime, lease_id="managed-stale-two"),
            second_events.append,
        )
        second.feed("Second. ")
        second.finish()

        def cancel_first() -> None:
            """Delay the stale comparison while the worker advances."""

            cancel_results.append(first.cancel())
            cancel_finished.set()

        thread = Thread(target=cancel_first, daemon=True)
        thread.start()
        assert runtime.abort_entered.wait(1.0)
        runtime.release_first.set()
        assert runtime.second_started.wait(1.0)
        second_token = runtime.calls[1][2]
        assert second_token != first_token

        runtime.allow_abort_compare.set()
        assert cancel_finished.wait(1.0)
        thread.join(1.0)
        runtime.release_second.set()
        _wait_until(lambda: len(second_events) == 2)

        assert cancel_results == [True]
        assert runtime.aborted_tokens == []
        assert runtime.ignored_tokens == [first_token]
        assert isinstance(second_events[0], SpeechQueueClip)
        assert second_events[0].result == _result(2)
        assert isinstance(second_events[1], SpeechTurnTerminal)
        assert not any(
            isinstance(event, SpeechQueueClip) for event in first_events
        )
    finally:
        runtime.release_first.set()
        runtime.release_second.set()
        runtime.allow_abort_compare.set()
        queue.shutdown(timeout_seconds=2.0)


def test_concurrent_managed_cancel_aborts_the_running_token_once() -> None:
    """Give one cancelling caller ownership and make every loser a no-op."""

    runtime = _BlockingManagedLease()
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-managed-cancel-once",
        _managed_lease(runtime),
        lambda _event: None,
    )
    try:
        turn.feed("Only once. ")
        assert runtime.started.wait(1.0)
        results: list[bool] = []
        threads = [
            Thread(target=lambda: results.append(turn.cancel()), daemon=True)
            for _index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1.0)

        assert sorted(results) == [False, True]
        assert runtime.abort_tokens == [runtime.calls[0][2]]
    finally:
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_managed_abort_failure_is_contained_and_sanitized() -> None:
    """Keep cancellation authoritative when physical termination raises."""

    runtime = _BlockingManagedLease(
        abort_error=KeyboardInterrupt("private-abort-canary")
    )
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-managed-abort-failure",
        _managed_lease(runtime),
        events.append,
    )
    try:
        turn.feed("Cancel safely. ")
        assert runtime.started.wait(1.0)
        assert turn.cancel() is True
        _wait_until(lambda: len(events) == 1)
        runtime.release.set()
        _wait_until(lambda: queue.get_status().running_sentences == 0)

        assert events == [
            SpeechTurnTerminal(
                "turn-managed-abort-failure", "cancelled", 1, 0, 0
            )
        ]
        assert "private-abort-canary" not in repr(events)
        assert "private-abort-canary" not in repr(queue.get_status())
    finally:
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_cancel_waits_for_a_clip_delivery_that_already_won() -> None:
    """Never acknowledge cancellation before an already-decided clip callback."""

    clip_entered = Event()
    release_clip = Event()
    cancel_finished = Event()
    events: list[object] = []

    def callback(event: object) -> None:
        """Pause inside clip delivery to expose the cancellation race."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)
        events.append(event)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-cancel-race",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("Delivered. Suppress me. ")
        assert clip_entered.wait(1.0)
        _wait_until(lambda: queue.get_status().pending_delivery_clips == 2)
        cancel_result: list[bool] = []

        def cancel() -> None:
            """Record when cancellation can cross the delivery gate."""

            cancel_result.append(turn.cancel())
            cancel_finished.set()

        thread = Thread(target=cancel)
        thread.start()
        assert not cancel_finished.wait(0.05)
        release_clip.set()
        thread.join(1.0)
        _wait_until(lambda: len(events) == 2)

        assert cancel_result == [True]
        assert isinstance(events[0], SpeechQueueClip)
        assert events[0].sequence == 0
        assert events[1] == SpeechTurnTerminal(
            "turn-cancel-race", "cancelled", 2, 2, 0
        )
        status = queue.get_status()
        assert status.pending_delivery_events == 0
        assert status.pending_delivery_clips == 0
        assert status.pending_delivery_bytes == 0
    finally:
        release_clip.set()
        queue.shutdown(timeout_seconds=2.0)


def test_external_cancel_and_callback_reentry_cannot_deadlock_handle() -> None:
    """Avoid a producer-lock cycle when a winning callback also calls cancel."""

    clip_entered = Event()
    allow_reentry = Event()
    callback_finished = Event()
    external_finished = Event()
    callback_cancel_results: list[bool] = []
    external_cancel_results: list[bool] = []
    turn_holder: list[SpeechTurn] = []

    def callback(event: object) -> None:
        """Re-enter cancel after an external cancellation begins waiting."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert allow_reentry.wait(2.0)
            callback_cancel_results.append(turn_holder[0].cancel())
            callback_finished.set()

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-cancel-reentry-race",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    turn_holder.append(turn)
    try:
        turn.feed("Delivered. ")
        assert clip_entered.wait(1.0)

        def cancel_externally() -> None:
            """Win cancellation and wait for the already-running callback."""

            external_cancel_results.append(turn.cancel())
            external_finished.set()

        thread = Thread(target=cancel_externally)
        thread.start()
        sleep(0.05)
        assert not external_finished.is_set()
        allow_reentry.set()

        assert callback_finished.wait(1.0)
        assert external_finished.wait(1.0)
        thread.join(1.0)
        assert callback_cancel_results == [False]
        assert external_cancel_results == [True]
    finally:
        allow_reentry.set()
        queue.shutdown(timeout_seconds=2.0)


def test_cancel_waits_only_for_the_notification_that_crossed_boundary() -> None:
    """Do not extend a clip barrier into its cancelled terminal callback."""

    clip_entered = Event()
    release_clip = Event()
    terminal_entered = Event()
    cancel_returned = Event()
    events: list[object] = []

    def callback(event: object) -> None:
        """Make the terminal depend on cancel returning to expose cycles."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)
        elif isinstance(event, SpeechTurnTerminal):
            terminal_entered.set()
            assert cancel_returned.wait(1.0)
        events.append(event)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-cancel-exact-barrier",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    cancel_results: list[bool] = []
    try:
        turn.feed("Delivered. ")
        assert clip_entered.wait(1.0)

        def cancel() -> None:
            """Signal immediately after the winning clip barrier clears."""

            cancel_results.append(turn.cancel())
            cancel_returned.set()

        thread = Thread(target=cancel)
        thread.start()
        _wait_until(lambda: queue.get_status().pending_notifications == 2)
        release_clip.set()

        assert terminal_entered.wait(1.0)
        assert cancel_returned.wait(1.0)
        thread.join(1.0)
        _wait_until(lambda: len(events) == 2)
        assert cancel_results == [True]
        assert isinstance(events[0], SpeechQueueClip)
        assert isinstance(events[1], SpeechTurnTerminal)
    finally:
        release_clip.set()
        cancel_returned.set()
        queue.shutdown(timeout_seconds=2.0)


def test_finish_cannot_publish_terminal_before_final_clip_callback() -> None:
    """Linearize normal completion after every ordered sentence event."""

    clip_entered = Event()
    release_clip = Event()
    finish_completed = Event()
    events: list[object] = []

    def callback(event: object) -> None:
        """Pause the final clip while another thread closes input."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)
        events.append(event)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-finish-race",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("Delivered. ")
        assert clip_entered.wait(1.0)

        def finish() -> None:
            """Close stream input while the final clip callback is blocked."""

            turn.finish()
            finish_completed.set()

        thread = Thread(target=finish)
        thread.start()
        assert finish_completed.wait(0.2)
        assert events == []
        release_clip.set()
        thread.join(1.0)
        _wait_until(lambda: len(events) == 2)

        assert isinstance(events[0], SpeechQueueClip)
        assert events[1] == SpeechTurnTerminal(
            "turn-finish-race", "completed", 1, 1, 0
        )
    finally:
        release_clip.set()
        queue.shutdown(timeout_seconds=2.0)


def test_cancel_nowait_does_not_join_a_crossed_delivery_callback() -> None:
    """Return after logical cancellation while an older callback still owns data."""

    clip_entered = Event()
    release_clip = Event()
    cancel_returned = Event()
    events: list[object] = []
    results: list[bool] = []

    def callback(event: object) -> None:
        """Hold the exact clip that crossed the callback boundary first."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)
        events.append(event)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-cancel-nowait",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    turn.feed("Already crossing. Later work is stale. ")
    assert clip_entered.wait(1.0)

    def cancel() -> None:
        """Record that the non-blocking cancellation returned immediately."""

        results.append(turn.cancel_nowait())
        cancel_returned.set()

    thread = Thread(target=cancel)
    thread.start()
    try:
        assert cancel_returned.wait(0.2)
        assert results == [True]
        assert events == []
        release_clip.set()
        thread.join(1.0)
        _wait_until(
            lambda: any(isinstance(event, SpeechTurnTerminal) for event in events)
        )
        assert isinstance(events[0], SpeechQueueClip)
        assert events[-1] == SpeechTurnTerminal(
            "turn-cancel-nowait", "cancelled", 2, 2, 0
        )
    finally:
        release_clip.set()
        thread.join(1.0)
        queue.shutdown(timeout_seconds=2.0)


def test_clip_callback_cannot_deadlock_waiting_for_its_own_terminal() -> None:
    """Report not-ready instead of blocking the sole delivery daemon."""

    wait_results: list[bool] = []
    events: list[object] = []
    queue = SpeechSynthesisQueue()

    def callback(event: object) -> None:
        """Attempt an otherwise-indefinite terminal wait from the notifier."""

        events.append(event)
        if isinstance(event, SpeechQueueClip):
            wait_results.append(queue.wait_turn("turn-reentrant-wait"))

    turn = queue.start_turn(
        "turn-reentrant-wait",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("Delivered. ")
        turn.finish()
        _wait_until(lambda: len(events) == 2)
        assert wait_results == [False]
        assert isinstance(events[-1], SpeechTurnTerminal)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_clip_callback_can_cancel_remaining_delivery_reentrantly() -> None:
    """Avoid self-deadlock and suppress every later clip for the same turn."""

    events: list[object] = []
    cancel_results: list[bool] = []
    turn_holder: list[SpeechTurn] = []

    def callback(event: object) -> None:
        """Cancel from the first clip while running on the notifier thread."""

        events.append(event)
        if isinstance(event, SpeechQueueClip):
            cancel_results.append(turn_holder[0].cancel())

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-reentrant-clip",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    turn_holder.append(turn)
    try:
        turn.feed("First. Suppress this. ")
        _wait_until(
            lambda: any(isinstance(item, SpeechTurnTerminal) for item in events)
        )
        clips = [item for item in events if isinstance(item, SpeechQueueClip)]
        assert [clip.sequence for clip in clips] == [0]
        assert cancel_results == [True]
        assert events[-1] == SpeechTurnTerminal(
            "turn-reentrant-clip", "cancelled", 2, 2, 0
        )
        status = queue.get_status()
        assert status.pending_delivery_events == 0
        assert status.pending_delivery_clips == 0
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_terminal_callback_can_reenter_its_turn_without_deadlock() -> None:
    """Run callbacks outside producer locks even for an empty finished turn."""

    callback_finished = Event()
    cancel_results: list[bool] = []
    queue = SpeechSynthesisQueue()
    turn_holder: list[SpeechTurn] = []

    def callback(event: object) -> None:
        """Attempt a harmless late cancel from the notifier thread."""

        assert isinstance(event, SpeechTurnTerminal)
        turn = turn_holder[0]
        cancel_results.append(turn.cancel())
        callback_finished.set()

    turn = queue.start_turn(
        "turn-reentrant-terminal",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    turn_holder.append(turn)
    try:
        finisher = Thread(target=turn.finish)
        finisher.start()
        finisher.join(1.0)
        assert not finisher.is_alive()
        assert callback_finished.wait(1.0)
        assert cancel_results == [False]
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_blocked_empty_terminal_keeps_active_turn_capacity_bounded() -> None:
    """Count a terminal callback as active until its consumer returns."""

    terminal_entered = Event()
    release_terminal = Event()

    def callback(event: object) -> None:
        """Hold an empty terminal at the external delivery boundary."""

        assert isinstance(event, SpeechTurnTerminal)
        terminal_entered.set()
        assert release_terminal.wait(2.0)

    queue = SpeechSynthesisQueue(SpeechQueueConfig(max_active_turns=1))
    try:
        first = queue.start_turn(
            "turn-blocked-terminal",
            _lease(_SequenceSynthesizer()),
            callback,
        )
        first.finish()
        assert terminal_entered.wait(1.0)
        with pytest.raises(SpeechQueueCapacityError, match="turn capacity"):
            queue.start_turn(
                "turn-terminal-overflow",
                _lease(_SequenceSynthesizer(), lease_id="lease-terminal-overflow"),
                lambda _event: None,
            )
        status = queue.get_status()
        assert status.active_turns == 1
        assert status.pending_notifications == 1
        assert status.delivery_callbacks_in_progress == 1

        release_terminal.set()
        _wait_until(lambda: queue.get_status().active_turns == 0)
        replacement = queue.start_turn(
            "turn-terminal-replacement",
            _lease(_SequenceSynthesizer(), lease_id="lease-terminal-replacement"),
            lambda _event: None,
        )
        replacement.finish()
    finally:
        release_terminal.set()
        queue.shutdown(timeout_seconds=2.0)


def test_shutdown_without_pending_cancel_still_closes_open_turns() -> None:
    """Cancel input-open turns while allowing already-finished input to drain."""

    synthesizer = _BlockingSynthesizer()
    draining_events: list[object] = []
    open_events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        draining = queue.start_turn(
            "turn-draining",
            _lease(synthesizer),
            draining_events.append,
        )
        draining.feed("Drain. ")
        draining.finish()
        assert synthesizer.started.wait(1.0)
        queue.start_turn(
            "turn-open",
            _lease(synthesizer, lease_id="lease-open"),
            open_events.append,
        )

        assert queue.shutdown(wait=False, cancel_pending=False) is False
        _wait_until(lambda: len(open_events) == 1)
        assert open_events == [
            SpeechTurnTerminal("turn-open", "cancelled", 0, 0, 0)
        ]

        synthesizer.release.set()
        assert queue.shutdown(cancel_pending=False, timeout_seconds=2.0)
        assert isinstance(draining_events[0], SpeechQueueClip)
        assert draining_events[1] == SpeechTurnTerminal(
            "turn-draining", "completed", 1, 1, 0
        )
    finally:
        synthesizer.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_nonblocking_shutdown_aborts_a_running_managed_operation() -> None:
    """Initiate physical cancellation without waiting for managed inference."""

    runtime = _BlockingManagedLease(release_on_abort=True)
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-managed-shutdown",
        _managed_lease(runtime),
        lambda _event: None,
    )
    try:
        turn.feed("Stop now. ")
        assert runtime.started.wait(1.0)
        started = monotonic()
        result = queue.shutdown(wait=False)
        elapsed = monotonic() - started

        assert isinstance(result, bool)
        assert elapsed < 0.2
        assert runtime.abort_called.wait(1.0)
        assert runtime.abort_tokens == [runtime.calls[0][2]]
        assert queue.shutdown(timeout_seconds=2.0)
    finally:
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_nonblocking_shutdown_uses_the_fixed_abort_dispatcher() -> None:
    """Dispatch a slow, queue-reentrant abort without blocking shutdown."""

    runtime = _SlowAbortManagedLease()
    observed_closed: list[bool] = []
    queue = SpeechSynthesisQueue()
    runtime.on_abort = lambda: observed_closed.append(queue.get_status().closed)
    turn = queue.start_turn(
        "turn-managed-slow-shutdown-abort",
        _managed_lease(runtime),
        lambda _event: None,
    )
    try:
        turn.feed("Abort asynchronously. ")
        assert runtime.started.wait(1.0)
        started = monotonic()
        assert queue.shutdown(wait=False) is False
        elapsed = monotonic() - started

        assert elapsed < 0.2
        assert runtime.abort_started.wait(1.0)
        assert runtime.abort_threads == ["elysia-speech-abort"]
        assert observed_closed == [True]
        assert queue.shutdown(timeout_seconds=0.05) is False

        runtime.release_abort.set()
        assert queue.shutdown(timeout_seconds=2.0)
        assert runtime.abort_tokens == [runtime.calls[0][2]]
    finally:
        runtime.release_abort.set()
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_graceful_shutdown_does_not_abort_closed_managed_input() -> None:
    """Drain explicitly finished input when cancel_pending is disabled."""

    runtime = _BlockingManagedLease()
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-managed-graceful-shutdown",
        _managed_lease(runtime),
        events.append,
    )
    try:
        turn.feed("Drain normally. ")
        turn.finish()
        assert runtime.started.wait(1.0)
        assert queue.shutdown(wait=False, cancel_pending=False) is False
        assert runtime.abort_tokens == []

        runtime.release.set()
        assert queue.shutdown(cancel_pending=False, timeout_seconds=2.0)
        assert isinstance(events[0], SpeechQueueClip)
        assert isinstance(events[1], SpeechTurnTerminal)
        assert events[1].state == "completed"
    finally:
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_graceful_shutdown_aborts_managed_input_that_remains_open() -> None:
    """Cancel an unfinished stream even when accepted closed work may drain."""

    runtime = _BlockingManagedLease(release_on_abort=True)
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-managed-open-shutdown",
        _managed_lease(runtime),
        lambda _event: None,
    )
    try:
        turn.feed("Still open. ")
        assert runtime.started.wait(1.0)
        queue.shutdown(wait=False, cancel_pending=False)

        assert runtime.abort_called.wait(1.0)
        assert runtime.abort_tokens == [runtime.calls[0][2]]
        assert queue.shutdown(cancel_pending=False, timeout_seconds=2.0)
    finally:
        runtime.release.set()
        queue.shutdown(timeout_seconds=2.0)


def test_nonblocking_shutdown_does_not_wait_for_running_callback() -> None:
    """Keep wait=False truthful while cancelling undelivered turn events."""

    clip_entered = Event()
    release_clip = Event()
    events: list[object] = []

    def callback(event: object) -> None:
        """Hold the callback that shutdown must not synchronously wait for."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)
        events.append(event)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-nonblocking-shutdown",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("Delivered. ")
        assert clip_entered.wait(1.0)
        started = monotonic()
        assert queue.shutdown(wait=False) is False
        assert monotonic() - started < 0.2
        assert queue.get_status().closed is True

        release_clip.set()
        assert queue.shutdown(timeout_seconds=2.0)
        assert isinstance(events[0], SpeechQueueClip)
        assert events[1] == SpeechTurnTerminal(
            "turn-nonblocking-shutdown", "cancelled", 1, 1, 0
        )
    finally:
        release_clip.set()
        queue.shutdown(timeout_seconds=2.0)


def test_shutdown_cannot_orphan_a_terminal_owned_by_waiting_cancel() -> None:
    """Keep the notifier alive across concurrent cancel and shutdown handoff."""

    clip_entered = Event()
    release_clip = Event()
    cancel_finished = Event()
    cancel_results: list[bool] = []
    events: list[object] = []

    def callback(event: object) -> None:
        """Hold the clip while cancel establishes its terminal notification."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)
        events.append(event)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-cancel-shutdown-handoff",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("Delivered. ")
        assert clip_entered.wait(1.0)

        def cancel() -> None:
            """Wait for the clip after atomically owning cancellation."""

            cancel_results.append(turn.cancel())
            cancel_finished.set()

        thread = Thread(target=cancel)
        thread.start()
        _wait_until(lambda: queue.get_status().pending_notifications == 2)
        assert not cancel_finished.is_set()

        assert queue.shutdown(wait=False) is False
        release_clip.set()
        assert cancel_finished.wait(1.0)
        thread.join(1.0)
        assert queue.shutdown(timeout_seconds=2.0)

        assert cancel_results == [True]
        assert isinstance(events[0], SpeechQueueClip)
        assert events[1] == SpeechTurnTerminal(
            "turn-cancel-shutdown-handoff", "cancelled", 1, 1, 0
        )
        status = queue.get_status()
        assert status.pending_notifications == 0
        assert status.active_turns == 0
    finally:
        release_clip.set()
        queue.shutdown(timeout_seconds=2.0)


def test_shutdown_timeout_includes_blocked_callback_cancellation() -> None:
    """Apply one deadline instead of waiting indefinitely before daemon joins."""

    clip_entered = Event()
    release_clip = Event()

    def callback(event: object) -> None:
        """Block one clip beyond the deliberately short shutdown timeout."""

        if isinstance(event, SpeechQueueClip):
            clip_entered.set()
            assert release_clip.wait(2.0)

    queue = SpeechSynthesisQueue()
    turn = queue.start_turn(
        "turn-shutdown-timeout",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("Delivered. ")
        assert clip_entered.wait(1.0)
        started = monotonic()
        assert not queue.shutdown(timeout_seconds=0.05)
        elapsed = monotonic() - started
        assert 0.03 <= elapsed < 0.5

        release_clip.set()
        assert queue.shutdown(timeout_seconds=2.0)
    finally:
        release_clip.set()
        queue.shutdown(timeout_seconds=2.0)


def test_terminal_turn_retention_and_empty_active_turns_are_bounded() -> None:
    """Prevent empty or forgotten turn identifiers from growing without limit."""

    queue = SpeechSynthesisQueue(
        SpeechQueueConfig(
            max_active_turns=1,
            max_pending_sentences=1,
            max_retained_turns=2,
        )
    )
    try:
        held = queue.start_turn(
            "turn-held",
            _lease(_SequenceSynthesizer()),
            lambda _event: None,
        )
        with pytest.raises(SpeechQueueCapacityError, match="turn capacity"):
            queue.start_turn(
                "turn-overflow-empty",
                _lease(_SequenceSynthesizer(), lease_id="lease-empty"),
                lambda _event: None,
            )
        held.finish()
        _wait_until(lambda: queue.get_status().active_turns == 0)

        for index in range(3):
            turn = queue.start_turn(
                f"turn-retained-{index}",
                _lease(
                    _SequenceSynthesizer(),
                    lease_id=f"lease-retained-{index}",
                ),
                lambda _event: None,
            )
            turn.finish()
            _wait_until(lambda: queue.get_status().active_turns == 0)

        status = queue.get_status()
        assert status.active_turns == 0
        assert status.retained_turns == 2
        assert status.occupied_slots == 0
        with pytest.raises(SpeechTurnStateError, match="unknown"):
            queue.wait_turn("turn-held", 0.1)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_terminal_tombstone_releases_engine_and_callback_references() -> None:
    """Do not pin old model processes or callback closures in idle history."""

    terminal_delivered = Event()
    synthesizer = _SequenceSynthesizer()
    callback = _TerminalObserver(terminal_delivered)
    synthesizer_reference = weakref.ref(synthesizer)
    callback_reference = weakref.ref(callback)
    lease = _lease(synthesizer)
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn("turn-scrubbed", lease, callback)
    try:
        turn.feed("Delivered. ")
        turn.finish()
        assert terminal_delivered.wait(1.0)
        _wait_until(lambda: queue.get_status().active_turns == 0)
        queue.forget_turn("turn-scrubbed")

        del turn
        del lease
        del callback
        del synthesizer
        gc.collect()
        assert synthesizer_reference() is None
        assert callback_reference() is None
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_managed_terminal_tombstone_releases_runtime_and_callback() -> None:
    """Scrub the owned process wrapper after work and delivery fully drain."""

    terminal_delivered = Event()
    runtime = _ManagedSequenceLease()
    callback = _TerminalObserver(terminal_delivered)
    runtime_reference = weakref.ref(runtime)
    callback_reference = weakref.ref(callback)
    lease = _managed_lease(runtime)
    queue = SpeechSynthesisQueue()
    turn = queue.start_turn("turn-managed-scrubbed", lease, callback)
    try:
        turn.feed("Delivered. ")
        turn.finish()
        assert terminal_delivered.wait(1.0)
        _wait_until(lambda: queue.get_status().active_turns == 0)
        queue.forget_turn("turn-managed-scrubbed")

        del turn
        del lease
        del callback
        del runtime
        gc.collect()
        assert runtime_reference() is None
        assert callback_reference() is None
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_sentence_failure_is_sanitized_and_later_work_continues() -> None:
    """Skip one unavailable sentence without exposing details or stopping FIFO."""

    synthesizer = _SequenceSynthesizer(
        [SynthesisUnavailableError("private endpoint"), _result(7)]
    )
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        turn = queue.start_turn("turn-failure", _lease(synthesizer), events.append)
        turn.feed("First. Second. ")
        turn.finish()
        _wait_until(lambda: len(events) == 3)

        assert events[0] == SpeechQueueFailure(
            "turn-failure",
            0,
            "unavailable",
            "Local speech synthesis is unavailable.",
        )
        assert isinstance(events[1], SpeechQueueClip)
        assert events[1].sequence == 1
        assert events[2] == SpeechTurnTerminal(
            "turn-failure", "completed", 2, 2, 1
        )
        assert "private endpoint" not in repr(events)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_unencodable_sentence_cannot_kill_worker_or_leak_credit() -> None:
    """Contain an adapter encoding failure and keep the sole worker usable."""

    encoding_error = UnicodeEncodeError(
        "utf-8",
        "A\ud800.",
        1,
        2,
        "surrogates not allowed",
    )
    synthesizer = _SequenceSynthesizer([encoding_error, _result(9)])
    first_events: list[object] = []
    second_events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        first = queue.start_turn(
            "turn-surrogate",
            _lease(synthesizer),
            first_events.append,
        )
        first.feed("A\ud800. ")
        first.finish()
        _wait_until(lambda: len(first_events) == 2)
        assert first_events[0] == SpeechQueueFailure(
            "turn-surrogate",
            0,
            "internal_error",
            "The local speech worker failed.",
        )

        second = queue.start_turn(
            "turn-after-surrogate",
            _lease(synthesizer, lease_id="lease-after-surrogate"),
            second_events.append,
        )
        second.feed("Still alive. ")
        second.finish()
        _wait_until(lambda: len(second_events) == 2)
        assert isinstance(second_events[0], SpeechQueueClip)
        status = queue.get_status()
        assert status.pending_delivery_events == 0
        assert status.pending_delivery_bytes == 0
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_managed_failure_clears_token_before_the_next_sentence() -> None:
    """Recover from BaseException with a fresh operation correlation token."""

    runtime = _ManagedSequenceLease(
        [KeyboardInterrupt("private-managed-canary"), _result(9)]
    )
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        turn = queue.start_turn(
            "turn-managed-failure-recovery",
            _managed_lease(runtime),
            events.append,
        )
        turn.feed("First. Second. ")
        turn.finish()
        _wait_until(lambda: len(events) == 3)

        assert isinstance(events[0], SpeechQueueFailure)
        assert events[0].message == "The local speech worker failed."
        assert isinstance(events[1], SpeechQueueClip)
        assert isinstance(events[2], SpeechTurnTerminal)
        assert runtime.calls[0][2] != runtime.calls[1][2]
        assert "private-managed-canary" not in repr(events)
        assert queue.get_status().running_sentences == 0
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_token_entropy_failure_is_sanitized_and_worker_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request entropy only for work and contain a failed token allocation."""

    attempts: list[int] = []

    def _token_hex(byte_count: int) -> str:
        """Fail the first concrete job and issue a valid token to the second."""

        assert byte_count == 32
        attempts.append(byte_count)
        if len(attempts) == 1:
            raise KeyboardInterrupt("private-entropy-canary")
        return "d" * 64

    monkeypatch.setattr("voice.speech_queue.secrets.token_hex", _token_hex)
    synthesizer = _SequenceSynthesizer([_result(7)])
    events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        sleep(0.05)
        assert attempts == []

        turn = queue.start_turn(
            "turn-entropy-recovery",
            _lease(synthesizer),
            events.append,
        )
        turn.feed("Entropy fails. Worker survives. ")
        turn.finish()
        _wait_until(lambda: len(events) == 3)
        sleep(0.05)

        assert attempts == [32, 32]
        assert events[0] == SpeechQueueFailure(
            "turn-entropy-recovery",
            0,
            "internal_error",
            "The local speech worker failed.",
        )
        assert isinstance(events[1], SpeechQueueClip)
        assert events[1].sequence == 1
        assert isinstance(events[2], SpeechTurnTerminal)
        assert [request.text for request in synthesizer.requests] == [
            " Worker survives."
        ]
        assert "private-entropy-canary" not in repr(events)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_binding_metadata_failure_cannot_kill_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain a future lease's broken cache property inside the job."""

    def cache_eligible(lease: SpeechSynthesisBindingLease) -> bool:
        """Fail only one lease so the same worker can prove recovery."""

        if lease.lease_id == "lease-broken-binding":
            raise RuntimeError("private attestation details")
        return False

    monkeypatch.setattr(
        SpeechSynthesisBindingLease,
        "cache_eligible",
        property(cache_eligible),
    )
    synthesizer = _SequenceSynthesizer()
    first_events: list[object] = []
    second_events: list[object] = []
    queue = SpeechSynthesisQueue()
    try:
        first = queue.start_turn(
            "turn-broken-binding",
            _lease(synthesizer, lease_id="lease-broken-binding"),
            first_events.append,
        )
        first.feed("First. ")
        first.finish()
        _wait_until(lambda: len(first_events) == 2)
        assert isinstance(first_events[0], SpeechQueueFailure)
        assert first_events[0].message == "The local speech worker failed."

        second = queue.start_turn(
            "turn-binding-recovered",
            _lease(synthesizer, lease_id="lease-binding-recovered"),
            second_events.append,
        )
        second.feed("Second. ")
        second.finish()
        _wait_until(lambda: len(second_events) == 2)
        assert isinstance(second_events[0], SpeechQueueClip)
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_callback_failure_cannot_remove_the_queue_worker() -> None:
    """Contain a broken delivery sink and continue synthesizing later turns."""

    synthesizer = _SequenceSynthesizer()
    queue = SpeechSynthesisQueue()
    good_events: list[object] = []
    try:
        broken = queue.start_turn(
            "turn-broken-sink",
            _lease(synthesizer),
            lambda _event: (_ for _ in ()).throw(RuntimeError("sink failed")),
        )
        broken.feed("First. ")
        broken.finish()
        assert queue.wait_turn("turn-broken-sink", 1.0)

        good = queue.start_turn(
            "turn-good-sink",
            _lease(synthesizer, lease_id="lease-good"),
            good_events.append,
        )
        good.feed("Second. ")
        good.finish()
        _wait_until(lambda: len(good_events) == 2)
        assert isinstance(good_events[0], SpeechQueueClip)
        assert isinstance(good_events[1], SpeechTurnTerminal)
        status = queue.get_status()
        assert status.pending_delivery_events == 0
        assert status.pending_delivery_bytes == 0
    finally:
        queue.shutdown(timeout_seconds=2.0)


def test_callback_shutdown_does_not_join_delivery_dependent_worker() -> None:
    """Return from internal shutdown when its peer needs current credit."""

    shutdown_results: list[bool] = []
    events: list[object] = []
    callback_returned = Event()
    queue = SpeechSynthesisQueue(
        SpeechQueueConfig(
            max_delivery_events=1,
            max_delivery_bytes=SYNTHESIS_MAX_AUDIO_BYTES,
        )
    )

    def callback(event: object) -> None:
        """Request graceful draining from the first delivered clip."""

        events.append(event)
        if isinstance(event, SpeechQueueClip) and not shutdown_results:
            shutdown_results.append(
                queue.shutdown(wait=True, cancel_pending=False)
            )
            callback_returned.set()

    turn = queue.start_turn(
        "turn-callback-shutdown",
        _lease(_SequenceSynthesizer()),
        callback,
    )
    try:
        turn.feed("First. Second. ")
        turn.finish()
        assert callback_returned.wait(1.0)
        assert shutdown_results == [False]
        assert queue.shutdown(cancel_pending=False, timeout_seconds=2.0)
        assert [
            item.sequence for item in events if isinstance(item, SpeechQueueClip)
        ] == [0, 1]
        assert isinstance(events[-1], SpeechTurnTerminal)
    finally:
        queue.shutdown(timeout_seconds=2.0)
