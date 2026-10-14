"""Bound a live Work job without pretending cooperative callbacks have stopped.

The monotonic wall-time budget starts at first admission and includes pauses and
human approval waits. A lease owns physical capacity until its exact callback
returns: cancellation, shutdown, and expiry only request a stop. Retries are
explicit, bounded, and limited to failed safe reads; this resource policy never
grants approval or replays a mutation. Native writes retain their real result
after admission because cancelling an already performed side effect is fiction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import re
from threading import RLock
from time import monotonic
from typing import Any, Literal

from work_tools.contracts import ToolCall, ToolProtocolError, ToolResult, validate_tool_call, validate_tool_result


JobPhase = Literal["read", "preview", "mutation"]
_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})
_RETRY_ERRORS = frozenset({"timeout", "io_error"})
_MAX_STEPS = 16
_MAX_TOTAL_SECONDS = 900.0
_STEP_SECONDS = 30.0
_MUTATION_ADMISSION_SECONDS = 20.0


class WorkflowJobError(ValueError):
    """Expose a fixed admission/retry failure without including callback data or paths."""

    def __init__(self, code: Literal["conflict", "cancelled", "timeout", "invalid_request"] = "conflict") -> None:
        """Select one closed error code suitable for the existing Work tool error schema."""

        self.code = code
        super().__init__({
            "conflict": "The Work job no longer accepts this attempt.",
            "cancelled": "The Work job was cancelled.",
            "timeout": "The Work job budget expired.",
            "invalid_request": "The Work job parameters are invalid.",
        }[code])


@dataclass(frozen=True, slots=True)
class WorkflowAttempt:
    """Identify one exact physical callback lease; observation never releases its capacity."""

    step_id: str
    phase: JobPhase
    attempt_number: int
    _budget: WorkflowJobBudget
    _started_at: float
    _deadline: float

    def should_stop(self) -> bool:
        """Request cooperative stop on cancellation, expiry, shutdown, or a stale lease."""

        return self._budget._stop_reason(self) is not None


@dataclass(slots=True)
class _History:
    """Retain only exact call identity and bounded attempt facts for sixteen steps."""

    call: ToolCall
    read_attempts: int = 0
    preview_started: bool = False
    mutation_started: bool = False
    last_result: ToolResult | None = None


class WorkflowJobBudget:
    """Own one sequential job's live monotonic limits and explicit safe-read retry policy."""

    def __init__(
        self, *, total_timeout_seconds: float = 300.0, max_read_retries: int = 2,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Configure at most fifteen minutes and two retries; the clock must be a fast local monotonic reader."""

        if (type(total_timeout_seconds) not in {int, float} or not math.isfinite(total_timeout_seconds)
            or not 0 < total_timeout_seconds <= _MAX_TOTAL_SECONDS
            or type(max_read_retries) is not int or not 0 <= max_read_retries <= 2 or not callable(clock)):
            raise WorkflowJobError("invalid_request")
        self._total_seconds = float(total_timeout_seconds)
        self._max_retries = max_read_retries
        self._clock = clock
        self._lock = RLock()
        self._last_now = 0.0
        self._started_at: float | None = None
        self._active: WorkflowAttempt | None = None
        self._history: dict[str, _History] = {}
        self._cancelled = False
        self._closed = False
        with self._lock:
            self._now_locked()

    def begin_step(
        self, step_id: str, call: ToolCall, *, retry: bool = False, phase: JobPhase | None = None,
    ) -> WorkflowAttempt:
        """Lease one exact call, admitting an explicit retry only after a drained transient read failure.

        Reads and previews have thirty seconds. Mutation's twenty seconds is an
        admission deadline only; the native writer independently checks its
        deadline immediately before mutation and must settle truthful results.
        A mutation phase is a resource fact and provides no approval authority.
        """

        if (type(step_id) is not str or re.fullmatch(r"step_[0-9a-f]{32}", step_id) is None
            or type(call) is not ToolCall or type(retry) is not bool):
            raise WorkflowJobError("invalid_request")
        checked = validate_tool_call(call.to_data())
        read = checked.tool_id in _READ_TOOLS
        selected: JobPhase = ("read" if read else "preview") if phase is None else phase
        if type(selected) is not str or selected not in ({"read"} if read else {"preview", "mutation"}):
            raise WorkflowJobError("invalid_request")
        with self._lock:
            now = self._now_locked()
            reason = self._job_stop_locked(now)
            if reason is not None:
                raise WorkflowJobError(reason)
            if self._active is not None:
                raise WorkflowJobError()
            previous = self._history.get(step_id)
            if previous is not None and previous.call != checked:
                raise WorkflowJobError()
            if retry:
                if (not read or previous is None or previous.last_result is None
                    or previous.last_result.status != "failed" or previous.last_result.error_code not in _RETRY_ERRORS
                    or previous.read_attempts >= self._max_retries + 1):
                    raise WorkflowJobError()
            elif read and previous is not None:
                raise WorkflowJobError()
            if previous is None:
                if len(self._history) >= _MAX_STEPS:
                    raise WorkflowJobError()
                previous = _History(checked)
                self._history[step_id] = previous
            if selected == "preview":
                if retry or previous.preview_started or previous.mutation_started:
                    raise WorkflowJobError()
                previous.preview_started = True
            elif selected == "mutation":
                if retry or previous.mutation_started:
                    raise WorkflowJobError()
                previous.mutation_started = True
            else:
                previous.read_attempts += 1
                previous.last_result = None
            if self._started_at is None:
                self._started_at = now
            step_seconds = _MUTATION_ADMISSION_SECONDS if selected == "mutation" else _STEP_SECONDS
            lease = WorkflowAttempt(step_id, selected, max(1, previous.read_attempts), self, now,
                                    min(now + step_seconds, self._started_at + self._total_seconds))
            self._active = lease
            return lease

    def fresh(self) -> WorkflowJobBudget:
        """Create limits for an explicitly new task, preserving injected configuration but no prior lease/history.

        Explicit read retries must reuse this object instead. Only a separate
        user-created plan may start a new total budget and attempt allowance.
        """

        with self._lock:
            if self._active is not None:
                raise WorkflowJobError()
            return WorkflowJobBudget(total_timeout_seconds=self._total_seconds,
                                     max_read_retries=self._max_retries, clock=self._clock)

    def finish(self, attempt: WorkflowAttempt, result: ToolResult, *, admitted_write: bool = False) -> ToolResult:
        """Release only this returned callback, preserving admitted writes and bounding late read publication.

        The caller invokes this once its callback has physically returned, even
        when that callback raised. A malformed result becomes a fixed io_error.
        Expired/cancelled reads cannot publish stale bodies. Already admitted
        writes preserve success or failure regardless of a later stop request.
        """

        with self._lock:
            self._require_active_locked(attempt)
            if type(admitted_write) is not bool or (admitted_write and attempt.phase != "mutation"):
                raise WorkflowJobError("invalid_request")
            history = self._history[attempt.step_id]
            try:
                encoded = validate_tool_result(history.call.tool_id, result)
                error = encoded["error"]
                settled = ToolResult(encoded["status"], encoded["data"], None if error is None else error["code"])
            except (ToolProtocolError, TypeError, ValueError):
                settled = ToolResult("failed", error_code="io_error")
            reason = self._stop_reason_locked(attempt, self._now_locked())
            # A verified successful mutation is itself evidence that the side
            # effect happened. Preserve it even if a caller omitted the marker;
            # failed admitted writes still require the explicit marker because
            # their result cannot reveal whether bytes were already changed.
            observed_write = attempt.phase == "mutation" and settled.status in {"succeeded", "partial"}
            if reason is not None and not (admitted_write or observed_write):
                settled = ToolResult("cancelled" if reason == "cancelled" else "failed", error_code=reason)
            history.last_result = settled
            self._active = None
            return settled

    def finish_preview(self, attempt: WorkflowAttempt) -> None:
        """Release a returned preview callback, refusing to publish an expired or cancelled approval."""

        with self._lock:
            self._require_active_locked(attempt)
            if attempt.phase != "preview":
                raise WorkflowJobError("invalid_request")
            reason = self._stop_reason_locked(attempt, self._now_locked())
            self._active = None
            if reason is not None:
                raise WorkflowJobError(reason)

    def cancel(self) -> None:
        """Request cooperative cancellation without releasing a callback's physical lease."""

        with self._lock:
            self._cancelled = True

    def shutdown(self) -> bool:
        """Close admission immediately and report whether the actual callback has already drained."""

        with self._lock:
            self._closed = True
            return self._active is None

    def is_busy(self) -> bool:
        """Report the physical callback lease even after logical cancel, expiry, or shutdown."""

        with self._lock:
            return self._active is not None

    def can_retry(self, step_id: str) -> bool:
        """Report whether a user may explicitly retry the exact drained read within the remaining budget."""

        if type(step_id) is not str or re.fullmatch(r"step_[0-9a-f]{32}", step_id) is None:
            return False
        with self._lock:
            now = self._now_locked()
            history = self._history.get(step_id)
            return (self._active is None and self._job_stop_locked(now) is None and history is not None
                    and history.call.tool_id in _READ_TOOLS and history.last_result is not None
                    and history.last_result.status == "failed" and history.last_result.error_code in _RETRY_ERRORS
                    and history.read_attempts < self._max_retries + 1)

    def snapshot(self) -> dict[str, Any]:
        """Expose detached progress/limits without call contents, paths, or live execution capabilities."""

        with self._lock:
            now = self._now_locked()
            active = self._active
            remaining = self._total_seconds if self._started_at is None else max(0.0, self._started_at + self._total_seconds - now)
            return {
                "totalTimeoutSeconds": self._total_seconds, "totalRemainingSeconds": remaining,
                "stepTimeoutSeconds": _STEP_SECONDS, "mutationAdmissionSeconds": _MUTATION_ADMISSION_SECONDS,
                "maxReadRetries": self._max_retries, "pauseCountsTowardBudget": True,
                "physicalBusy": active is not None, "cancelRequested": self._cancelled, "closed": self._closed,
                "activeStepId": None if active is None else active.step_id,
                "activePhase": None if active is None else active.phase,
                "activeAttempt": None if active is None else active.attempt_number,
                "stepRemainingSeconds": None if active is None else max(0.0, active._deadline - now),
                "stopReason": self._job_stop_locked(now) if active is None else self._stop_reason_locked(active, now),
                "retryableStepIds": [step_id for step_id in self._history if self.can_retry(step_id)],
            }

    def _now_locked(self) -> float:
        value = self._clock()
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise WorkflowJobError("invalid_request")
        # A faulty or test clock must never move a previously observed deadline
        # into the future. The production monotonic clock already guarantees it.
        self._last_now = max(self._last_now, float(value))
        return self._last_now

    def _job_stop_locked(self, now: float) -> Literal["cancelled", "timeout"] | None:
        if self._cancelled or self._closed:
            return "cancelled"
        if self._started_at is not None and now >= self._started_at + self._total_seconds:
            return "timeout"
        return None

    def _require_active_locked(self, attempt: WorkflowAttempt) -> None:
        if type(attempt) is not WorkflowAttempt or self._active is not attempt:
            raise WorkflowJobError()

    def _stop_reason_locked(self, attempt: WorkflowAttempt, now: float) -> Literal["cancelled", "timeout"] | None:
        reason = self._job_stop_locked(now)
        return reason if reason is not None else "timeout" if now >= attempt._deadline else None

    def _stop_reason(self, attempt: WorkflowAttempt) -> Literal["conflict", "cancelled", "timeout"] | None:
        with self._lock:
            if self._active is not attempt:
                return "conflict"
            return self._stop_reason_locked(attempt, self._now_locked())
