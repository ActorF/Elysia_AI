"""Test bounded background transcription without native speech dependencies."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread
from time import monotonic, sleep

import pytest

import voice as voice_package
from voice.capture import VOICE_CAPTURE_MIN_SPEECH_SAMPLES, VoiceCapture
from voice.transcription import (
    TranscriptionError,
    TranscriptionFailedError,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionUnavailableError,
    TranscriptionValidationError,
)
from voice.transcription_jobs import (
    TranscriptionJobCapacityError,
    TranscriptionJobClosedError,
    TranscriptionJobConflictError,
    TranscriptionJobNotFoundError,
    TranscriptionJobRunner,
    TranscriptionJobRunnerConfig,
    TranscriptionJobSnapshot,
    TranscriptionJobValidationError,
    TranscriptionJobWaitTimeoutError,
)


_RESULT = TranscriptionResult(
    text="hello",
    language="en",
    language_probability=0.9,
)


def _request(session_suffix: str = "job") -> TranscriptionRequest:
    """Build one valid non-silent capture for scheduler tests."""

    sample_count = VOICE_CAPTURE_MIN_SPEECH_SAMPLES
    return TranscriptionRequest(
        capture=VoiceCapture(
            session_id=f"voice_{session_suffix}",
            pcm_s16le=b"\x01\x00" * sample_count,
            sample_rate_hz=16_000,
            sample_count=sample_count,
            speech_start_sample=0,
            speech_end_sample=sample_count,
        )
    )


def _config(
    *,
    workers: int = 1,
    queued: int = 2,
    retained: int = 128,
    timeout: float = 5.0,
) -> TranscriptionJobRunnerConfig:
    """Build a short but non-racy scheduler configuration."""

    return TranscriptionJobRunnerConfig(
        max_concurrent_jobs=workers,
        max_queued_jobs=queued,
        max_retained_jobs=retained,
        default_timeout_seconds=timeout,
    )


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 2.0,
) -> None:
    """Poll a lock-safe predicate until a deterministic test deadline."""

    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("Timed out waiting for scheduler state.")


class _BlockingTranscriber:
    """Hold adapter calls until tests release their bounded workers."""

    def __init__(self, result: TranscriptionResult = _RESULT) -> None:
        """Initialize call counters and explicit synchronization gates."""

        self.result = result
        self.started = Event()
        self.release = Event()
        self._lock = Lock()
        self.calls = 0
        self.active_calls = 0
        self.maximum_active_calls = 0

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Block one valid request while recording physical concurrency."""

        assert isinstance(request, TranscriptionRequest)
        with self._lock:
            self.calls += 1
            self.active_calls += 1
            self.maximum_active_calls = max(
                self.maximum_active_calls,
                self.active_calls,
            )
            self.started.set()
        assert self.release.wait(2.0)
        with self._lock:
            self.active_calls -= 1
        return self.result


class _SequenceTranscriber:
    """Return or raise scripted adapter outcomes in call order."""

    def __init__(self, outcomes: list[object]) -> None:
        """Store at least one outcome behind a concurrency-safe counter."""

        if not outcomes:
            raise ValueError("At least one scripted outcome is required.")
        self._outcomes = outcomes
        self._lock = Lock()
        self.calls = 0

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Resolve one outcome without exposing the request payload."""

        assert isinstance(request, TranscriptionRequest)
        with self._lock:
            index = min(self.calls, len(self._outcomes) - 1)
            self.calls += 1
            outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, TranscriptionResult)
        return outcome


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_concurrent_jobs", True),
        ("max_concurrent_jobs", 0),
        ("max_concurrent_jobs", 9),
        ("max_queued_jobs", True),
        ("max_queued_jobs", -1),
        ("max_queued_jobs", 65),
        ("max_retained_jobs", True),
        ("max_retained_jobs", 0),
        ("max_retained_jobs", 4_097),
        ("default_timeout_seconds", True),
        ("default_timeout_seconds", 0.0),
        ("default_timeout_seconds", float("inf")),
        ("default_timeout_seconds", 10**1_000),
        ("default_timeout_seconds", 600.1),
    ],
)
def test_runner_config_rejects_unbounded_values(
    field: str,
    value: object,
) -> None:
    """Reject resource settings outside their documented hard bounds."""

    values: dict[str, object] = {
        "max_concurrent_jobs": 1,
        "max_queued_jobs": 2,
        "max_retained_jobs": 128,
        "default_timeout_seconds": 120.0,
    }
    values[field] = value
    with pytest.raises(TranscriptionJobValidationError):
        TranscriptionJobRunnerConfig(**values)  # type: ignore[arg-type]


def test_job_api_is_exported_from_voice_package() -> None:
    """Keep the background scheduler available through the public Voice API."""

    assert voice_package.TranscriptionJobRunner is TranscriptionJobRunner
    assert (
        voice_package.TranscriptionJobRunnerConfig
        is TranscriptionJobRunnerConfig
    )
    assert voice_package.TRANSCRIPTION_JOB_MAX_WORKERS == 8
    assert voice_package.TRANSCRIPTION_JOB_MAX_QUEUE_SIZE == 64


def test_submit_returns_before_blocking_transcription_finishes() -> None:
    """Keep synchronous model inference off the submitting protocol thread."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    try:
        submitted = runner.submit("job-1", _request())
        assert submitted.state == "queued"
        assert transcriber.started.wait(1.0)
        assert runner.get_snapshot("job-1").state == "running"
        transcriber.release.set()
        completed = runner.wait("job-1", 1.0)
        assert completed.state == "succeeded"
        assert completed.result == _RESULT
        assert completed.capacity_released is True
        assert runner._jobs["job-1"].request is None
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_capacity_rejects_work_beyond_worker_and_queue_bounds() -> None:
    """Reject overload instead of placing captures in an unbounded queue."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=1))
    try:
        runner.submit("job-running", _request("running"))
        assert transcriber.started.wait(1.0)
        queued = runner.submit("job-queued", _request("queued"))
        assert queued.state == "queued"
        assert queued.queue_position == 1
        with pytest.raises(TranscriptionJobCapacityError, match="capacity"):
            runner.submit("job-rejected", _request("rejected"))

        status = runner.get_status()
        assert status.running_jobs == 1
        assert status.queued_jobs == 1
        assert status.occupied_slots == 2
        assert status.max_concurrent_jobs == 1
        assert status.max_queued_jobs == 1
    finally:
        runner.cancel("job-queued")
        transcriber.release.set()
        runner.shutdown()


def test_multiple_workers_never_exceed_the_configured_concurrency() -> None:
    """Cap physical adapter calls even when the queue contains more work."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(workers=2, queued=2),
    )
    try:
        for index in range(4):
            runner.submit(f"job-{index}", _request(str(index)))
        _wait_until(lambda: transcriber.calls == 2)
        assert transcriber.maximum_active_calls == 2
        with pytest.raises(TranscriptionJobCapacityError):
            runner.submit("job-overflow", _request("overflow"))
        transcriber.release.set()
        for index in range(4):
            assert runner.wait(f"job-{index}", 1.0).state == "succeeded"
        assert transcriber.maximum_active_calls == 2
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_cancelling_a_queued_job_prevents_adapter_execution() -> None:
    """Remove queued work and release its slot before the worker can see PCM."""

    transcriber = _BlockingTranscriber()
    terminal: list[TranscriptionJobSnapshot] = []
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=1),
        on_terminal=terminal.append,
    )
    try:
        runner.submit("job-running", _request("running"))
        assert transcriber.started.wait(1.0)
        runner.submit("job-queued", _request("queued"))
        cancelled = runner.cancel("job-queued")
        assert cancelled.state == "cancelled"
        assert cancelled.capacity_released is True
        assert runner._jobs["job-queued"].request is None
        assert runner.wait("job-queued", 1.0) == cancelled
        assert [item.job_id for item in terminal] == ["job-queued"]
        transcriber.release.set()
        assert runner.wait("job-running", 1.0).state == "succeeded"
        assert transcriber.calls == 1
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_cancelling_running_work_keeps_its_slot_until_native_return() -> None:
    """Expose logical cancellation without reusing an occupied native worker."""

    transcriber = _BlockingTranscriber()
    terminal: list[TranscriptionJobSnapshot] = []
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=0),
        on_terminal=terminal.append,
    )
    try:
        runner.submit("job-1", _request())
        assert transcriber.started.wait(1.0)
        cancelled = runner.cancel("job-1")
        assert cancelled.state == "cancelled"
        assert cancelled.capacity_released is False
        assert runner.wait("job-1", 1.0).state == "cancelled"
        assert runner._jobs["job-1"].request is None
        with pytest.raises(TranscriptionJobCapacityError):
            runner.submit("job-2", _request("two"))

        transcriber.release.set()
        _wait_until(
            lambda: runner.get_snapshot("job-1").capacity_released
        )
        assert runner.get_snapshot("job-1").result is None
        assert [item.state for item in terminal] == ["cancelled"]
        runner.submit("job-2", _request("two"))
        assert runner.wait("job-2", 1.0).state == "succeeded"
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_running_deadline_discards_late_result_and_holds_capacity() -> None:
    """Publish timeout promptly while accurately reporting a draining worker."""

    transcriber = _BlockingTranscriber()
    terminal: list[TranscriptionJobSnapshot] = []
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=0),
        on_terminal=terminal.append,
    )
    try:
        runner.submit("job-timeout", _request(), timeout_seconds=0.05)
        assert transcriber.started.wait(1.0)
        timed_out = runner.wait("job-timeout", 1.0)
        assert timed_out.state == "timed_out"
        assert timed_out.capacity_released is False
        assert runner._jobs["job-timeout"].request is None
        with pytest.raises(TranscriptionJobCapacityError):
            runner.submit("job-blocked", _request("blocked"))

        transcriber.release.set()
        _wait_until(
            lambda: runner.get_snapshot("job-timeout").capacity_released
        )
        final = runner.get_snapshot("job-timeout")
        assert final.state == "timed_out"
        assert final.result is None
        assert [item.state for item in terminal] == ["timed_out"]
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_queued_deadline_releases_capacity_without_adapter_call() -> None:
    """Expire stale waiting audio before native inference begins."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=1))
    try:
        runner.submit("job-running", _request("running"))
        assert transcriber.started.wait(1.0)
        runner.submit(
            "job-timeout",
            _request("timeout"),
            timeout_seconds=0.05,
        )
        snapshot = runner.wait("job-timeout", 1.0)
        assert snapshot.state == "timed_out"
        assert snapshot.capacity_released is True
        assert runner._jobs["job-timeout"].request is None
        assert transcriber.calls == 1
    finally:
        transcriber.release.set()
        runner.shutdown()


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            TranscriptionValidationError("private PCM details"),
            "invalid_request",
            "The transcription request was rejected.",
        ),
        (
            TranscriptionUnavailableError("private model path"),
            "unavailable",
            "Local transcription is unavailable.",
        ),
        (
            TranscriptionFailedError("native library details"),
            "transcription_failed",
            "Local transcription failed.",
        ),
        (
            TranscriptionError("unknown typed details"),
            "internal_error",
            "The local transcription worker failed.",
        ),
        (
            RuntimeError("unexpected secret"),
            "internal_error",
            "The local transcription worker failed.",
        ),
    ],
)
def test_worker_failures_are_typed_and_sanitized(
    error: BaseException,
    code: str,
    message: str,
) -> None:
    """Map adapter and unexpected failures without exposing exception text."""

    runner = TranscriptionJobRunner(
        _SequenceTranscriber([error]),
        config=_config(queued=0),
    )
    try:
        runner.submit("job-failure", _request())
        snapshot = runner.wait("job-failure", 1.0)
        assert snapshot.state == "failed"
        assert snapshot.failure is not None
        assert snapshot.failure.code == code
        assert snapshot.failure.message == message
        assert runner._jobs["job-failure"].request is None
        assert "secret" not in snapshot.failure.message
        assert "details" not in snapshot.failure.message
    finally:
        runner.shutdown()


def test_base_exception_does_not_remove_the_scheduler_worker() -> None:
    """Contain faulty adapter control-flow exceptions and process later jobs."""

    transcriber = _SequenceTranscriber([SystemExit("stop"), _RESULT])
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    try:
        runner.submit("job-exit", _request("exit"))
        assert runner.wait("job-exit", 1.0).state == "failed"
        runner.submit("job-after", _request("after"))
        assert runner.wait("job-after", 1.0).state == "succeeded"
    finally:
        runner.shutdown()


def test_observer_wait_timeout_does_not_cancel_the_job() -> None:
    """Keep polling deadlines separate from the configured job deadline."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    try:
        runner.submit("job-wait", _request())
        assert transcriber.started.wait(1.0)
        with pytest.raises(TranscriptionJobWaitTimeoutError):
            runner.wait("job-wait", 0.01)
        assert runner.get_snapshot("job-wait").state == "running"
        transcriber.release.set()
        assert runner.wait("job-wait", 1.0).state == "succeeded"
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_completed_history_is_bounded_and_oldest_first() -> None:
    """Evict old terminal output instead of accumulating transcripts forever."""

    runner = TranscriptionJobRunner(
        _SequenceTranscriber([_RESULT]),
        config=_config(queued=0, retained=1),
    )
    try:
        runner.submit("job-old", _request("old"))
        assert runner.wait("job-old", 1.0).state == "succeeded"
        runner.submit("job-new", _request("new"))
        assert runner.wait("job-new", 1.0).state == "succeeded"
        with pytest.raises(TranscriptionJobNotFoundError):
            runner.get_snapshot("job-old")
        assert [item.job_id for item in runner.list_snapshots()] == ["job-new"]
        assert runner.get_status().retained_jobs == 1
    finally:
        runner.shutdown()


def test_forget_removes_consumed_transcript_and_identifier() -> None:
    """Let integration erase private terminal output immediately after use."""

    runner = TranscriptionJobRunner(
        _SequenceTranscriber([_RESULT]),
        config=_config(queued=0),
    )
    try:
        runner.submit("job-private", _request())
        assert runner.wait("job-private", 1.0).result == _RESULT
        runner.forget("job-private")
        with pytest.raises(TranscriptionJobNotFoundError):
            runner.get_snapshot("job-private")
        runner.submit("job-private", _request("reuse"))
        assert runner.wait("job-private", 1.0).state == "succeeded"
    finally:
        runner.shutdown()


def test_forget_keeps_draining_capacity_record_until_native_return() -> None:
    """Clear logical metadata without hiding an uninterruptible cancelled call."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    try:
        runner.submit("job-draining", _request())
        assert transcriber.started.wait(1.0)
        runner.cancel("job-draining")
        runner.forget("job-draining")
        with pytest.raises(TranscriptionJobConflictError):
            runner.submit("job-draining", _request("duplicate"))
        assert runner.get_status().occupied_slots == 1

        transcriber.release.set()
        _wait_until(lambda: runner.get_status().occupied_slots == 0)
        with pytest.raises(TranscriptionJobNotFoundError):
            runner.get_snapshot("job-draining")
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_forget_rejects_non_terminal_work() -> None:
    """Prevent callers from erasing the only record of active native work."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    try:
        runner.submit("job-running", _request())
        assert transcriber.started.wait(1.0)
        with pytest.raises(TranscriptionJobConflictError):
            runner.forget("job-running")
    finally:
        transcriber.release.set()
        runner.shutdown()


def test_nonblocking_shutdown_cancels_queue_and_marks_running_work() -> None:
    """Close promptly while retaining honest draining-worker accounting."""

    transcriber = _BlockingTranscriber()
    terminal: list[TranscriptionJobSnapshot] = []
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=1),
        on_terminal=terminal.append,
    )
    runner.submit("job-running", _request("running"))
    assert transcriber.started.wait(1.0)
    runner.submit("job-queued", _request("queued"))

    started = monotonic()
    stopped = runner.shutdown(wait=False, cancel_pending=True)
    assert monotonic() - started < 0.5
    assert stopped is False
    assert runner.get_snapshot("job-running").state == "cancelled"
    assert runner.get_snapshot("job-running").capacity_released is False
    assert runner.get_snapshot("job-queued").capacity_released is True
    with pytest.raises(TranscriptionJobClosedError):
        runner.submit("job-late", _request("late"))

    transcriber.release.set()
    _wait_until(lambda: runner.get_status().occupied_slots == 0)
    assert {item.job_id for item in terminal} == {
        "job-running",
        "job-queued",
    }
    assert transcriber.calls == 1
    assert runner.shutdown() is True


def test_shutdown_can_drain_accepted_queue_before_joining() -> None:
    """Finish admitted work when graceful shutdown explicitly avoids cancel."""

    transcriber = _SequenceTranscriber([_RESULT])
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=1),
    )
    runner.submit("job-one", _request("one"))
    runner.submit("job-two", _request("two"))
    assert runner.shutdown(wait=True, cancel_pending=False) is True
    assert runner.get_snapshot("job-one").state == "succeeded"
    assert runner.get_snapshot("job-two").state == "succeeded"
    assert transcriber.calls == 2


def test_shutdown_wait_uses_one_bounded_physical_deadline() -> None:
    """Return false instead of hanging when native inference ignores cancel."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    runner.submit("job-draining", _request())
    assert transcriber.started.wait(1.0)

    started = monotonic()
    stopped = runner.shutdown(
        wait=True,
        cancel_pending=True,
        timeout_seconds=0.02,
    )
    assert stopped is False
    assert monotonic() - started < 0.5
    snapshot = runner.get_snapshot("job-draining")
    assert snapshot.state == "cancelled"
    assert snapshot.capacity_released is False

    transcriber.release.set()
    assert runner.shutdown(wait=True, timeout_seconds=1.0) is True


def test_callback_failure_cannot_kill_worker_or_hide_completion() -> None:
    """Continue scheduling after faulty integration callback code raises."""

    callback_calls = 0

    def failing_callback(snapshot: TranscriptionJobSnapshot) -> None:
        """Raise after observing each terminal snapshot."""

        nonlocal callback_calls
        assert snapshot.is_terminal
        callback_calls += 1
        raise RuntimeError("emitter failed")

    runner = TranscriptionJobRunner(
        _SequenceTranscriber([_RESULT]),
        config=_config(queued=0),
        on_terminal=failing_callback,
    )
    try:
        runner.submit("job-one", _request("one"))
        assert runner.wait("job-one", 1.0).state == "succeeded"
        runner.submit("job-two", _request("two"))
        assert runner.wait("job-two", 1.0).state == "succeeded"
        _wait_until(lambda: callback_calls == 2)
    finally:
        runner.shutdown()


def test_admission_gate_precedes_worker_and_terminal_callback() -> None:
    """Let the bridge publish started frames before fast work can finish."""

    transcriber = _BlockingTranscriber()
    gate_entered = Event()
    release_gate = Event()
    submit_finished = Event()
    order: list[str] = []
    submission_errors: list[BaseException] = []

    def on_admitted(snapshot: TranscriptionJobSnapshot) -> None:
        """Hold the admission gate while asserting the worker cannot start."""

        assert snapshot.state == "queued"
        assert snapshot.queue_position == 1
        order.append("admitted")
        gate_entered.set()
        assert release_gate.wait(1.0)

    def on_terminal(snapshot: TranscriptionJobSnapshot) -> None:
        """Record the terminal callback after the admitted frame gate."""

        assert snapshot.state == "succeeded"
        order.append("terminal")

    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=0),
        on_terminal=on_terminal,
    )

    def submit_job() -> None:
        """Submit from another thread so the test can inspect the held gate."""

        try:
            runner.submit(
                "job-gated",
                _request(),
                on_admitted=on_admitted,
            )
        except BaseException as error:
            submission_errors.append(error)
        finally:
            submit_finished.set()

    submitter = Thread(target=submit_job, daemon=True)
    try:
        submitter.start()
        assert gate_entered.wait(1.0)
        assert transcriber.started.is_set() is False
        assert submit_finished.is_set() is False
        release_gate.set()
        assert transcriber.started.wait(1.0)
        transcriber.release.set()
        assert runner.wait("job-gated", 1.0).state == "succeeded"
        assert submit_finished.wait(1.0)
        submitter.join(1.0)
        assert submission_errors == []
        assert order == ["admitted", "terminal"]
    finally:
        release_gate.set()
        transcriber.release.set()
        submitter.join(1.0)
        runner.shutdown()


def test_failed_admission_callback_rolls_back_without_terminal_event() -> None:
    """Release every reservation when started-frame publication fails."""

    terminal: list[TranscriptionJobSnapshot] = []
    transcriber = _SequenceTranscriber([_RESULT])
    runner = TranscriptionJobRunner(
        transcriber,
        config=_config(queued=0),
        on_terminal=terminal.append,
    )

    def reject_admission(snapshot: TranscriptionJobSnapshot) -> None:
        """Simulate failure while publishing the protocol started frame."""

        assert snapshot.job_id == "job-rejected"
        raise RuntimeError("output stream closed")

    try:
        with pytest.raises(RuntimeError, match="output stream closed"):
            runner.submit(
                "job-rejected",
                _request("rejected"),
                on_admitted=reject_admission,
            )
        assert runner.get_status().occupied_slots == 0
        assert runner.list_snapshots() == ()
        assert terminal == []
        assert transcriber.calls == 0

        runner.submit("job-recovered", _request("recovered"))
        assert runner.wait("job-recovered", 1.0).state == "succeeded"
    finally:
        runner.shutdown()


def test_job_lookup_identity_and_deadline_inputs_are_validated() -> None:
    """Reject ambiguous identifiers, duplicates, missing jobs, and deadlines."""

    transcriber = _BlockingTranscriber()
    runner = TranscriptionJobRunner(transcriber, config=_config(queued=0))
    try:
        with pytest.raises(TranscriptionJobValidationError):
            runner.submit("", _request())
        with pytest.raises(TranscriptionJobValidationError):
            runner.submit("x" * 129, _request())
        with pytest.raises(TranscriptionJobValidationError):
            runner.submit("job-bad-request", object())  # type: ignore[arg-type]
        with pytest.raises(TranscriptionJobValidationError):
            runner.submit("job-bad-timeout", _request(), timeout_seconds=0.0)
        with pytest.raises(TranscriptionJobNotFoundError):
            runner.get_snapshot("job-missing")

        runner.submit("job-duplicate", _request("duplicate"))
        with pytest.raises(TranscriptionJobConflictError):
            runner.submit("job-duplicate", _request("other"))
    finally:
        transcriber.release.set()
        runner.shutdown()
