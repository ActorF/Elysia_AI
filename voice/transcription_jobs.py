"""Run synchronous local transcription behind a bounded background scheduler.

The engine-independent :class:`~voice.transcription.Transcriber` contract is
deliberately synchronous because native speech engines expose blocking calls.
This module supplies the lifecycle boundary needed by the desktop bridge:
submission returns immediately, worker and queue capacity are bounded, and
callers can observe success, failure, cancellation, or a wall-clock deadline.

Python cannot safely kill a thread while native inference is running.  A
running job therefore becomes logically ``cancelled`` or ``timed_out`` at the
request boundary while ``capacity_released`` remains false until the call
actually returns.  Its late result is discarded, and the occupied slot cannot
be reused; this prevents cancellation from creating unbounded hidden work.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from threading import Condition, Event, Lock, Thread, Timer, current_thread
from time import monotonic
from typing import Callable, Final, Literal, TypeAlias

from .transcription import (
    Transcriber,
    TranscriptionError,
    TranscriptionFailedError,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionUnavailableError,
    TranscriptionValidationError,
)


TRANSCRIPTION_JOB_MAX_ID_LENGTH: Final = 128
TRANSCRIPTION_JOB_MAX_WORKERS: Final = 8
TRANSCRIPTION_JOB_MAX_QUEUE_SIZE: Final = 64
TRANSCRIPTION_JOB_MAX_RETAINED: Final = 4_096
TRANSCRIPTION_JOB_MAX_TIMEOUT_SECONDS: Final = 600.0

TranscriptionJobState: TypeAlias = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]
TranscriptionJobFailureCode: TypeAlias = Literal[
    "invalid_request",
    "unavailable",
    "transcription_failed",
    "internal_error",
]

_TERMINAL_STATES: Final = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out"}
)
_INVALID_REQUEST_MESSAGE: Final = "The transcription request was rejected."
_UNAVAILABLE_MESSAGE: Final = "Local transcription is unavailable."
_FAILED_MESSAGE: Final = "Local transcription failed."
_INTERNAL_MESSAGE: Final = "The local transcription worker failed."


def _is_strict_integer(value: object) -> bool:
    """Reject booleans even though ``bool`` inherits from ``int``."""

    return isinstance(value, int) and not isinstance(value, bool)


def _validate_timeout(value: object) -> float:
    """Return one finite bounded deadline suitable for a daemon timer."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        # Converting an unbounded Python integer inside math.isfinite can raise
        # OverflowError before this validator can return its stable error.
        or (isinstance(value, float) and not math.isfinite(value))
        or value <= 0.0
        or value > TRANSCRIPTION_JOB_MAX_TIMEOUT_SECONDS
    ):
        raise TranscriptionJobValidationError(
            "timeout_seconds must be finite and greater than zero, up to 600."
        )
    return float(value)


class TranscriptionJobError(Exception):
    """Base class for stable background-transcription scheduler failures."""


class TranscriptionJobValidationError(TranscriptionJobError):
    """Report malformed job identifiers, requests, or deadline values."""


class TranscriptionJobCapacityError(TranscriptionJobError):
    """Report that every worker and bounded waiting slot is reserved."""


class TranscriptionJobConflictError(TranscriptionJobError):
    """Report reuse of a job identifier still retained by the scheduler."""


class TranscriptionJobNotFoundError(TranscriptionJobError):
    """Report lookup of an unknown or deliberately evicted job identifier."""


class TranscriptionJobClosedError(TranscriptionJobError):
    """Report submission after scheduler shutdown has begun."""


class TranscriptionJobWaitTimeoutError(TranscriptionJobError):
    """Report that an observer stopped waiting before the job became terminal."""


@dataclass(frozen=True, slots=True)
class TranscriptionJobRunnerConfig:
    """Bound worker, waiting, retention, and default deadline resources.

    ``max_queued_jobs`` counts work waiting behind occupied workers.  Admission
    reserves at most ``max_concurrent_jobs + max_queued_jobs`` total slots.
    ``max_retained_jobs`` bounds completed snapshots; active native calls are
    never evicted even after logical cancellation or timeout.
    """

    max_concurrent_jobs: int = 1
    max_queued_jobs: int = 2
    max_retained_jobs: int = 128
    default_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        """Reject values that could disable or defeat scheduler bounds."""

        if (
            not _is_strict_integer(self.max_concurrent_jobs)
            or not 1
            <= self.max_concurrent_jobs
            <= TRANSCRIPTION_JOB_MAX_WORKERS
        ):
            raise TranscriptionJobValidationError(
                "max_concurrent_jobs must be an integer from 1 through 8."
            )
        if (
            not _is_strict_integer(self.max_queued_jobs)
            or not 0
            <= self.max_queued_jobs
            <= TRANSCRIPTION_JOB_MAX_QUEUE_SIZE
        ):
            raise TranscriptionJobValidationError(
                "max_queued_jobs must be an integer from 0 through 64."
            )
        if (
            not _is_strict_integer(self.max_retained_jobs)
            or not 1
            <= self.max_retained_jobs
            <= TRANSCRIPTION_JOB_MAX_RETAINED
        ):
            raise TranscriptionJobValidationError(
                "max_retained_jobs must be an integer from 1 through 4096."
            )
        _validate_timeout(self.default_timeout_seconds)


@dataclass(frozen=True, slots=True)
class TranscriptionJobFailure:
    """Describe one sanitized worker failure without carrying an exception."""

    code: TranscriptionJobFailureCode
    message: str


@dataclass(frozen=True, slots=True)
class TranscriptionJobSnapshot:
    """Expose an immutable point-in-time view of one transcription job.

    A terminal state reports the user's logical outcome.  For a running cancel
    or timeout, ``capacity_released`` remains false until uninterruptible native
    work returns.  ``queue_position`` is one-based and only present while the
    job is waiting.
    """

    job_id: str
    state: TranscriptionJobState
    queue_position: int | None
    capacity_released: bool
    result: TranscriptionResult | None
    failure: TranscriptionJobFailure | None

    @property
    def is_terminal(self) -> bool:
        """Return whether the caller-visible outcome can no longer change."""

        return self.state in _TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class TranscriptionJobRunnerStatus:
    """Summarize scheduler load without exposing capture or transcript data."""

    closed: bool
    running_jobs: int
    queued_jobs: int
    occupied_slots: int
    retained_jobs: int
    max_concurrent_jobs: int
    max_queued_jobs: int


TranscriptionJobCallback: TypeAlias = Callable[[TranscriptionJobSnapshot], None]


@dataclass(slots=True)
class _JobRecord:
    """Hold mutable lifecycle data protected by the scheduler condition."""

    job_id: str
    request: TranscriptionRequest | None
    state: TranscriptionJobState = "queued"
    capacity_released: bool = False
    result: TranscriptionResult | None = None
    failure: TranscriptionJobFailure | None = None
    completed: Event = field(default_factory=Event)
    timer: Timer | None = None
    retained: bool = False
    discard_on_release: bool = False


class TranscriptionJobRunner:
    """Execute blocking transcriptions with bounded daemon-worker resources.

    The runner starts a fixed number of daemon workers.  ``submit`` never waits
    for inference, and a full scheduler rejects work instead of growing an
    unbounded executor queue.  ``on_terminal`` is invoked exactly once per job,
    outside scheduler locks; it must return promptly because it runs on the
    worker, timer, cancellation, or shutdown caller that wins the terminal race.
    Callback failures are contained so they cannot kill a worker.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        *,
        config: TranscriptionJobRunnerConfig | None = None,
        on_terminal: TranscriptionJobCallback | None = None,
    ) -> None:
        """Start fixed daemon workers around one thread-safe Transcriber."""

        if not callable(getattr(transcriber, "transcribe", None)):
            raise TypeError("transcriber must implement transcribe(request).")
        if config is not None and not isinstance(
            config, TranscriptionJobRunnerConfig
        ):
            raise TypeError("config must be a TranscriptionJobRunnerConfig.")
        if on_terminal is not None and not callable(on_terminal):
            raise TypeError("on_terminal must be callable or None.")

        self._transcriber = transcriber
        self._config = config or TranscriptionJobRunnerConfig()
        self._on_terminal = on_terminal
        # Admission callbacks run outside the lifecycle condition so protocol
        # output cannot hold the scheduler state lock. A separate lock keeps
        # cancel/shutdown from observing the pre-runnable reservation halfway
        # through its started-frame gate.
        self._admission_lock = Lock()
        self._condition = Condition(Lock())
        self._queue: deque[str] = deque()
        self._jobs: dict[str, _JobRecord] = {}
        self._retained_order: deque[str] = deque()
        self._occupied_slots = 0
        self._closed = False
        self._workers = tuple(
            Thread(
                target=self._worker_loop,
                name=f"elysia-transcription-{index + 1}",
                daemon=True,
            )
            for index in range(self._config.max_concurrent_jobs)
        )
        for worker in self._workers:
            worker.start()

    def submit(
        self,
        job_id: str,
        request: TranscriptionRequest,
        *,
        timeout_seconds: float | None = None,
        on_admitted: TranscriptionJobCallback | None = None,
    ) -> TranscriptionJobSnapshot:
        """Admit work without blocking, or reject it when capacity is full.

        ``on_admitted`` runs synchronously after capacity is reserved but before
        the worker or deadline Timer can run. The desktop bridge uses this gate
        to emit its started/progress frames before a fast job emits a terminal
        frame. It must return promptly and must not call back into this runner.

        The deadline starts when that gate succeeds and includes queue time. A
        stale queued capture is less useful than a prompt timeout, and including
        queue time prevents overload from silently extending latency.
        """

        self._validate_job_id(job_id)
        if not isinstance(request, TranscriptionRequest):
            raise TranscriptionJobValidationError(
                "request must be a validated TranscriptionRequest."
            )
        if on_admitted is not None and not callable(on_admitted):
            raise TypeError("on_admitted must be callable or None.")
        deadline = _validate_timeout(
            self._config.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        with self._admission_lock:
            with self._condition:
                if self._closed:
                    raise TranscriptionJobClosedError(
                        "The transcription job runner is closed."
                    )
                if job_id in self._jobs:
                    raise TranscriptionJobConflictError(
                        "The transcription job identifier is already in use."
                    )
                capacity = (
                    self._config.max_concurrent_jobs
                    + self._config.max_queued_jobs
                )
                if self._occupied_slots >= capacity:
                    raise TranscriptionJobCapacityError(
                        "The transcription worker and queue capacity are full."
                    )

                record = _JobRecord(job_id=job_id, request=request)
                self._jobs[job_id] = record
                self._occupied_slots += 1
                # The record is deliberately not runnable yet. Report its
                # eventual one-based position to the admission callback.
                snapshot = TranscriptionJobSnapshot(
                    job_id=job_id,
                    state="queued",
                    queue_position=len(self._queue) + 1,
                    capacity_released=False,
                    result=None,
                    failure=None,
                )

            try:
                if on_admitted is not None:
                    on_admitted(snapshot)
            except BaseException:
                # A failed started-frame gate means no job was publicly begun.
                # Roll back the reservation and retain neither PCM nor history.
                with self._condition:
                    record.request = None
                    self._occupied_slots -= 1
                    del self._jobs[job_id]
                    self._condition.notify_all()
                raise

            timer = Timer(deadline, self._timeout_job, args=(job_id,))
            # Timers are bounded by admitted jobs, but making them daemon threads
            # is still required so native work cannot strand process shutdown.
            timer.daemon = True
            with self._condition:
                record.timer = timer
                self._queue.append(job_id)
                try:
                    # Starting while the condition is held lets a zero-latency
                    # Timer block on the same lock until the queue entry exists.
                    timer.start()
                except BaseException:
                    self._queue.remove(job_id)
                    record.request = None
                    record.timer = None
                    self._occupied_slots -= 1
                    del self._jobs[job_id]
                    self._condition.notify_all()
                    raise
                runnable_snapshot = self._snapshot_locked(record)
                self._condition.notify()
            return runnable_snapshot

    def get_snapshot(self, job_id: str) -> TranscriptionJobSnapshot:
        """Return the latest immutable lifecycle snapshot for one retained job."""

        self._validate_job_id(job_id)
        with self._condition:
            return self._snapshot_locked(self._require_job_locked(job_id))

    def list_snapshots(self) -> tuple[TranscriptionJobSnapshot, ...]:
        """Return current active and retained jobs in admission order."""

        with self._condition:
            return tuple(
                self._snapshot_locked(record)
                for record in self._jobs.values()
            )

    def forget(self, job_id: str) -> None:
        """Discard terminal transcript metadata at the integration boundary.

        Callers should invoke this after emitting or otherwise consuming a
        terminal result. A logically terminal job whose native inference is
        still draining keeps only its capacity record until the worker returns;
        this prevents identifier reuse from hiding uninterruptible work.
        """

        self._validate_job_id(job_id)
        with self._condition:
            record = self._require_job_locked(job_id)
            if record.state not in _TERMINAL_STATES:
                raise TranscriptionJobConflictError(
                    "Only a terminal transcription job can be forgotten."
                )
            record.result = None
            record.failure = None
            if record.capacity_released:
                if record.retained:
                    self._retained_order.remove(job_id)
                del self._jobs[job_id]
            else:
                record.discard_on_release = True

    def get_status(self) -> TranscriptionJobRunnerStatus:
        """Return bounded scheduler counts for health and progress reporting."""

        with self._condition:
            return TranscriptionJobRunnerStatus(
                closed=self._closed,
                running_jobs=sum(
                    record.state == "running"
                    for record in self._jobs.values()
                ),
                queued_jobs=sum(
                    record.state == "queued"
                    for record in self._jobs.values()
                ),
                occupied_slots=self._occupied_slots,
                retained_jobs=len(self._retained_order),
                max_concurrent_jobs=self._config.max_concurrent_jobs,
                max_queued_jobs=self._config.max_queued_jobs,
            )

    def wait(
        self,
        job_id: str,
        timeout_seconds: float | None = None,
    ) -> TranscriptionJobSnapshot:
        """Wait only as an explicit observer and return the terminal snapshot.

        This observer timeout never mutates the transcription deadline.  The
        desktop protocol loop should use callbacks or snapshots instead of this
        blocking helper.
        """

        self._validate_job_id(job_id)
        if timeout_seconds is not None:
            timeout_seconds = _validate_timeout(timeout_seconds)
        with self._condition:
            record = self._require_job_locked(job_id)

        if not record.completed.wait(timeout_seconds):
            raise TranscriptionJobWaitTimeoutError(
                "The observer stopped waiting before transcription completed."
            )
        # Keep the record reference so bounded-history eviction cannot make a
        # waiter lose the terminal result after its Event has already fired.
        with self._condition:
            return self._snapshot_locked(record)

    def cancel(self, job_id: str) -> TranscriptionJobSnapshot:
        """Logically cancel queued or running work and discard any late result.

        Queued work releases capacity immediately.  Running native inference
        cannot be killed safely, so its snapshot is terminal but continues to
        reserve a slot until the Transcriber returns.
        """

        self._validate_job_id(job_id)
        terminal_snapshot: TranscriptionJobSnapshot | None = None
        timer: Timer | None = None
        with self._admission_lock:
            with self._condition:
                record = self._require_job_locked(job_id)
                if record.state not in _TERMINAL_STATES:
                    was_queued = record.state == "queued"
                    if was_queued:
                        self._queue.remove(job_id)
                        record.request = None
                    record.state = "cancelled"
                    record.completed.set()
                    timer = record.timer
                    record.timer = None
                    if was_queued:
                        self._release_capacity_locked(record)
                    terminal_snapshot = self._snapshot_locked(record)
                    self._condition.notify_all()
                snapshot = self._snapshot_locked(record)

        if timer is not None:
            timer.cancel()
        if terminal_snapshot is not None:
            self._notify_terminal(terminal_snapshot)
        return snapshot

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_pending: bool = True,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Close admission and optionally cancel every unfinished job.

        ``wait=False`` is always non-blocking with respect to native inference;
        daemon workers exit after current calls return.  ``wait=True`` joins all
        workers up to one shared optional timeout. Without that timeout it can
        wait indefinitely for an uncooperative native library. The return value
        states whether every worker has physically stopped, keeping protocol
        shutdown distinct from logical cancellation.
        """

        if not isinstance(wait, bool) or not isinstance(cancel_pending, bool):
            raise TypeError("wait and cancel_pending must be booleans.")
        if not wait and timeout_seconds is not None:
            raise TranscriptionJobValidationError(
                "timeout_seconds requires wait=True during shutdown."
            )
        if timeout_seconds is not None:
            timeout_seconds = _validate_timeout(timeout_seconds)

        notifications: list[TranscriptionJobSnapshot] = []
        timers: list[Timer] = []
        with self._admission_lock:
            with self._condition:
                self._closed = True
                if cancel_pending:
                    self._queue.clear()
                    for record in tuple(self._jobs.values()):
                        if record.state not in ("queued", "running"):
                            continue
                        was_queued = record.state == "queued"
                        record.state = "cancelled"
                        record.completed.set()
                        if record.timer is not None:
                            timers.append(record.timer)
                            record.timer = None
                        if was_queued:
                            record.request = None
                            self._release_capacity_locked(record)
                        notifications.append(self._snapshot_locked(record))
                self._condition.notify_all()

        for timer in timers:
            timer.cancel()
        for snapshot in notifications:
            self._notify_terminal(snapshot)
        if wait:
            caller = current_thread()
            deadline = (
                None
                if timeout_seconds is None
                else monotonic() + timeout_seconds
            )
            for worker in self._workers:
                # A terminal callback may initiate shutdown on its worker. It
                # cannot join itself, but the daemon exits on the next loop.
                if worker is not caller:
                    remaining = (
                        None
                        if deadline is None
                        else max(0.0, deadline - monotonic())
                    )
                    worker.join(remaining)
        return all(not worker.is_alive() for worker in self._workers)

    def _worker_loop(self) -> None:
        """Drain admitted jobs while preserving the fixed worker bound."""

        while True:
            with self._condition:
                while not self._queue:
                    if self._closed:
                        return
                    self._condition.wait()
                job_id = self._queue.popleft()
                record = self._jobs[job_id]
                # Queue removal for cancellation and timeout occurs under the
                # same condition, so only queued records can reach a worker.
                record.state = "running"
                request = record.request
                record.request = None

            if request is None:
                # This invariant is guarded defensively because silently
                # passing no audio into adapter code would obscure corruption.
                self._finish_job(
                    record,
                    result=None,
                    failure=TranscriptionJobFailure(
                        code="internal_error",
                        message=_INTERNAL_MESSAGE,
                    ),
                )
                continue

            self._execute_job(record, request)
            # The job record never retains PCM after dispatch. Explicitly
            # dropping the worker local also prevents the idle loop frame from
            # extending the capture lifetime until another job arrives.
            del request

    def _execute_job(
        self,
        record: _JobRecord,
        request: TranscriptionRequest,
    ) -> None:
        """Run one adapter call and contain every failure inside its job."""

        result: TranscriptionResult | None = None
        failure: TranscriptionJobFailure | None = TranscriptionJobFailure(
            code="internal_error",
            message=_INTERNAL_MESSAGE,
        )
        try:
            candidate = self._transcriber.transcribe(request)
            if not isinstance(candidate, TranscriptionResult):
                raise TypeError("Transcriber returned an invalid result.")
            result = candidate
            failure = None
        except TranscriptionError as error:
            failure = self._failure_from_error(error)
        except BaseException:
            # A Transcriber is an isolation boundary. Even SystemExit from
            # faulty adapter code must not permanently remove a pool worker.
            failure = TranscriptionJobFailure(
                code="internal_error",
                message=_INTERNAL_MESSAGE,
            )

        self._finish_job(record, result=result, failure=failure)

    def _finish_job(
        self,
        record: _JobRecord,
        *,
        result: TranscriptionResult | None,
        failure: TranscriptionJobFailure | None,
    ) -> None:
        """Linearize natural completion against cancellation and deadline."""

        terminal_snapshot: TranscriptionJobSnapshot | None = None
        timer: Timer | None = None
        with self._condition:
            if record.state == "running":
                if failure is None:
                    record.state = "succeeded"
                    record.result = result
                else:
                    record.state = "failed"
                    record.failure = failure
                record.completed.set()
                timer = record.timer
                record.timer = None
                self._release_capacity_locked(record)
                terminal_snapshot = self._snapshot_locked(record)
            else:
                # Cancellation or timeout already published the logical result;
                # native completion only releases its reserved capacity.
                self._release_capacity_locked(record)
            self._condition.notify_all()

        if timer is not None:
            timer.cancel()
        if terminal_snapshot is not None:
            self._notify_terminal(terminal_snapshot)

    def _timeout_job(self, job_id: str) -> None:
        """Publish one admission-to-completion deadline without killing a thread."""

        terminal_snapshot: TranscriptionJobSnapshot | None = None
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None or record.state in _TERMINAL_STATES:
                return
            was_queued = record.state == "queued"
            if was_queued:
                self._queue.remove(job_id)
                record.request = None
            record.state = "timed_out"
            record.timer = None
            record.completed.set()
            if was_queued:
                self._release_capacity_locked(record)
            terminal_snapshot = self._snapshot_locked(record)
            self._condition.notify_all()

        self._notify_terminal(terminal_snapshot)

    def _release_capacity_locked(self, record: _JobRecord) -> None:
        """Release one admission exactly once and make it retention-eligible."""

        if record.capacity_released:
            return
        record.capacity_released = True
        self._occupied_slots -= 1
        if record.discard_on_release:
            self._jobs.pop(record.job_id, None)
            return
        if record.state in _TERMINAL_STATES and not record.retained:
            record.retained = True
            self._retained_order.append(record.job_id)
            self._prune_retained_locked()

    def _prune_retained_locked(self) -> None:
        """Evict oldest completed records while never hiding active native work."""

        while len(self._retained_order) > self._config.max_retained_jobs:
            expired_job_id = self._retained_order.popleft()
            expired = self._jobs.get(expired_job_id)
            if expired is not None and expired.capacity_released:
                del self._jobs[expired_job_id]

    def _snapshot_locked(self, record: _JobRecord) -> TranscriptionJobSnapshot:
        """Copy protected state without carrying PCM into observable metadata."""

        queue_position: int | None = None
        if record.state == "queued":
            for index, queued_job_id in enumerate(self._queue, start=1):
                if queued_job_id == record.job_id:
                    queue_position = index
                    break
        return TranscriptionJobSnapshot(
            job_id=record.job_id,
            state=record.state,
            queue_position=queue_position,
            capacity_released=record.capacity_released,
            result=record.result,
            failure=record.failure,
        )

    def _require_job_locked(self, job_id: str) -> _JobRecord:
        """Resolve a retained job under the condition or raise a stable error."""

        try:
            return self._jobs[job_id]
        except KeyError:
            raise TranscriptionJobNotFoundError(
                "The transcription job was not found."
            ) from None

    def _notify_terminal(self, snapshot: TranscriptionJobSnapshot) -> None:
        """Contain callback failures so scheduler capacity always recovers."""

        callback = self._on_terminal
        if callback is None:
            return
        try:
            callback(snapshot)
        except BaseException:
            # The callback is integration glue, not job work. A faulty emitter
            # must not kill the worker or Timer that owns lifecycle cleanup.
            return

    @staticmethod
    def _failure_from_error(error: TranscriptionError) -> TranscriptionJobFailure:
        """Map typed adapter failures to fixed messages safe for protocol output."""

        if isinstance(error, TranscriptionValidationError):
            return TranscriptionJobFailure(
                code="invalid_request",
                message=_INVALID_REQUEST_MESSAGE,
            )
        if isinstance(error, TranscriptionUnavailableError):
            return TranscriptionJobFailure(
                code="unavailable",
                message=_UNAVAILABLE_MESSAGE,
            )
        if isinstance(error, TranscriptionFailedError):
            return TranscriptionJobFailure(
                code="transcription_failed",
                message=_FAILED_MESSAGE,
            )
        return TranscriptionJobFailure(
            code="internal_error",
            message=_INTERNAL_MESSAGE,
        )

    @staticmethod
    def _validate_job_id(job_id: object) -> None:
        """Require a bounded non-empty identifier compatible with the protocol."""

        if (
            not isinstance(job_id, str)
            or not job_id
            or len(job_id) > TRANSCRIPTION_JOB_MAX_ID_LENGTH
        ):
            raise TranscriptionJobValidationError(
                "job_id must be a non-empty string no longer than 128 characters."
            )
