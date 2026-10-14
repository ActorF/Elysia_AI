"""Verify monotonic Work budgets, explicit read retries, and honest callback draining."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from typing import Any

import pytest

from agent.workflow_jobs import WorkflowJobBudget, WorkflowJobError
from work_tools.contracts import ERROR_MESSAGES, ToolCall, ToolResult


_STEP = "step_" + "1" * 32
_NEXT = "step_" + "2" * 32
_READ = ToolCall("read_text", "notes.txt")
_WRITE = ToolCall("create", "created.txt", content="private content")


class _Clock:
    """Supply a deterministic local clock without wall-clock sleeps in deadline tests."""

    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _success() -> ToolResult:
    return ToolResult("succeeded", {"path": "notes.txt", "content": "private body", "sizeBytes": 12})


@pytest.mark.parametrize("options", [
    {"total_timeout_seconds": 0}, {"total_timeout_seconds": 901}, {"total_timeout_seconds": True},
    {"total_timeout_seconds": float("nan")}, {"total_timeout_seconds": float("inf")},
    {"max_read_retries": -1}, {"max_read_retries": 3}, {"max_read_retries": True}, {"clock": None},
])
def test_unbounded_or_untyped_configuration_is_refused(options: dict[str, Any]) -> None:
    """Keep the resource policy within fixed finite time and explicit retry limits."""

    with pytest.raises(WorkflowJobError):
        WorkflowJobBudget(**options)


def test_job_admission_error_codes_share_the_closed_tool_error_catalog() -> None:
    """Every resource-policy error can become an existing fixed ToolResult without introducing another protocol code."""

    for code in ("conflict", "cancelled", "timeout", "invalid_request"):
        error = WorkflowJobError(code)  # type: ignore[arg-type]
        assert error.code in ERROR_MESSAGES
        assert ToolResult("failed", error_code=error.code).error_code == error.code


def test_first_admission_starts_budget_and_total_time_includes_pauses_and_approval_waits() -> None:
    """Pending display consumes no budget; later idle time cannot reset a started job's deadline."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    clock.value += 1000
    assert budget.snapshot()["totalRemainingSeconds"] == 300
    attempt = budget.begin_step(_STEP, _READ)
    budget.finish(attempt, _success())
    clock.value += 299
    assert budget.snapshot()["totalRemainingSeconds"] == 1
    assert budget.snapshot()["pauseCountsTowardBudget"] is True
    clock.value += 1
    with pytest.raises(WorkflowJobError) as error:
        budget.begin_step(_NEXT, _READ)
    assert error.value.code == "timeout" and not budget.is_busy()


def test_late_read_timeout_discards_its_body_only_after_the_real_callback_returns() -> None:
    """Expiry requests stop and blocks new admission while retaining the physical callback lease."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    attempt = budget.begin_step(_STEP, _READ)
    clock.value += 30
    assert attempt.should_stop() and budget.is_busy()
    assert budget.snapshot()["stopReason"] == "timeout"
    with pytest.raises(WorkflowJobError):
        budget.begin_step(_NEXT, _READ)
    result = budget.finish(attempt, _success())
    assert result.status == "failed" and result.error_code == "timeout" and result.data is None
    assert not budget.is_busy() and budget.can_retry(_STEP)


@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
def test_cancel_and_shutdown_never_release_a_blocked_native_callback(stop: str) -> None:
    """Use an actual blocked callback to prove logical stopping cannot admit a replacement operation."""

    budget = WorkflowJobBudget()
    entered, release = Event(), Event()
    attempt = budget.begin_step(_STEP, _READ)

    def _callback() -> ToolResult:
        entered.set()
        assert release.wait(5)
        return budget.finish(attempt, _success())

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_callback)
        assert entered.wait(2)
        if stop == "cancel":
            budget.cancel()
        else:
            assert budget.shutdown() is False
        try:
            assert attempt.should_stop() and budget.is_busy() and not future.done()
            with pytest.raises(WorkflowJobError):
                budget.begin_step(_NEXT, _READ)
        finally:
            release.set()
        result = future.result(timeout=2)
    assert result.status == "cancelled" and result.data is None and not budget.is_busy()
    assert not budget.can_retry(_STEP)
    assert budget.shutdown() is True


def test_only_an_explicit_exact_safe_read_retry_can_run_and_the_cap_is_two() -> None:
    """Never schedule retry internally, and reject changed calls or a fourth physical read attempt."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    first = budget.begin_step(_STEP, _READ)
    budget.finish(first, ToolResult("failed", error_code="io_error"))
    assert budget.can_retry(_STEP) and not budget.is_busy()
    with pytest.raises(WorkflowJobError):
        budget.begin_step(_STEP, _READ)
    with pytest.raises(WorkflowJobError):
        budget.begin_step(_STEP, ToolCall("read_text", "different.txt"), retry=True)
    for number in (2, 3):
        retry = budget.begin_step(_STEP, _READ, retry=True)
        assert retry.attempt_number == number
        assert not budget.can_retry(_STEP)
        budget.finish(retry, ToolResult("failed", error_code="timeout"))
    assert not budget.can_retry(_STEP)
    with pytest.raises(WorkflowJobError):
        budget.begin_step(_STEP, _READ, retry=True)


@pytest.mark.parametrize("result", [
    ToolResult("denied", error_code="permission_denied"), ToolResult("failed", error_code="path_denied"),
    ToolResult("failed", error_code="conflict"), ToolResult("cancelled", error_code="cancelled"),
    _success(),
])
def test_permanent_failures_cancelled_reads_and_successes_are_not_retryable(result: ToolResult) -> None:
    """Retry transient failed reads only; permission and filesystem conflicts require a fresh explicit plan."""

    budget = WorkflowJobBudget()
    attempt = budget.begin_step(_STEP, _READ)
    budget.finish(attempt, result)
    assert not budget.can_retry(_STEP)
    with pytest.raises(WorkflowJobError):
        budget.begin_step(_STEP, _READ, retry=True)


def test_preview_then_mutation_are_distinct_callbacks_and_mutations_are_never_retried() -> None:
    """Keep approval preparation separate from its native mutation without treating confirmation as retry."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    preview = budget.begin_step(_STEP, _WRITE)
    assert preview.phase == "preview" and budget.snapshot()["stepRemainingSeconds"] == 30
    budget.finish_preview(preview)
    mutation = budget.begin_step(_STEP, _WRITE, phase="mutation")
    assert mutation.phase == "mutation" and budget.snapshot()["stepRemainingSeconds"] == 20
    budget.finish(mutation, ToolResult("failed", error_code="io_error"), admitted_write=True)
    assert not budget.can_retry(_STEP)
    for retry in (False, True):
        with pytest.raises(WorkflowJobError):
            budget.begin_step(_STEP, _WRITE, phase="mutation", retry=retry)


@pytest.mark.parametrize("stop", ["timeout", "cancel", "shutdown"])
@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_admitted_write_retains_its_real_outcome_after_later_stop_request(stop: str, status: str) -> None:
    """A performed write's audit and result remain truthful when a deadline or user stop arrives later."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    attempt = budget.begin_step(_STEP, _WRITE, phase="mutation")
    if stop == "timeout":
        clock.value += 400
    elif stop == "cancel":
        budget.cancel()
    else:
        assert budget.shutdown() is False
    assert attempt.should_stop() and budget.is_busy()
    actual = (ToolResult("succeeded", {"path": "created.txt", "undoId": "undo_" + "3" * 32})
              if status == "succeeded" else ToolResult("failed", error_code="io_error"))
    settled = budget.finish(attempt, actual, admitted_write=True)
    assert settled.to_data() == actual.to_data() and not budget.is_busy()


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_expired_or_cancelled_preview_is_not_published_but_physical_capacity_is_released(stop: str) -> None:
    """An approval returned after its preparation budget cannot become a current UI approval."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    preview = budget.begin_step(_STEP, _WRITE)
    if stop == "timeout":
        clock.value += 30
    else:
        budget.cancel()
    with pytest.raises(WorkflowJobError) as error:
        budget.finish_preview(preview)
    assert error.value.code == ("timeout" if stop == "timeout" else "cancelled")
    assert not budget.is_busy()


def test_foreign_forged_and_completed_attempts_cannot_release_another_callbacks_lease() -> None:
    """Compare the exact in-memory lease identity so delayed or reconstructed tokens cannot clear busy state."""

    budget, foreign = WorkflowJobBudget(), WorkflowJobBudget()
    attempt = budget.begin_step(_STEP, _READ)
    foreign_attempt = foreign.begin_step(_STEP, _READ)
    forged = replace(attempt)
    for invalid in (foreign_attempt, forged):
        with pytest.raises(WorkflowJobError):
            budget.finish(invalid, _success())
        assert budget.is_busy() and invalid.should_stop() is (invalid is forged)
    budget.finish(attempt, _success())
    newer = budget.begin_step(_NEXT, _READ)
    assert attempt.should_stop()
    with pytest.raises(WorkflowJobError):
        budget.finish(attempt, _success())
    assert budget.is_busy()
    budget.finish(newer, _success())
    foreign.finish(foreign_attempt, _success())


def test_progress_is_detached_bounded_and_contains_no_paths_or_content() -> None:
    """Expose only resource facts and logical step IDs, never selected file bodies or sensitive callback details."""

    budget = WorkflowJobBudget()
    first = budget.begin_step(_STEP, _READ)
    budget.finish(first, ToolResult("failed", error_code="io_error"))
    progress = budget.snapshot()
    progress["retryableStepIds"].clear()
    assert budget.snapshot()["retryableStepIds"] == [_STEP]
    second = budget.begin_step(_NEXT, _WRITE)
    rendered = repr(budget.snapshot())
    assert "created.txt" not in rendered and "notes.txt" not in rendered and "private" not in rendered
    budget.finish_preview(second)


def test_clock_regression_cannot_extend_a_previously_observed_deadline() -> None:
    """Clamp a backward clock reading so malformed test clocks cannot revive expired attempts."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    attempt = budget.begin_step(_STEP, _READ)
    clock.value += 31
    assert attempt.should_stop()
    clock.value = 0
    assert attempt.should_stop()
    assert budget.finish(attempt, _success()).error_code == "timeout"


def test_sixteen_distinct_steps_are_bounded_and_invalid_phase_never_claims_capacity() -> None:
    """Reject overflow and phase confusion before starting another physical callback."""

    budget = WorkflowJobBudget()
    for index in range(16):
        attempt = budget.begin_step("step_" + f"{index:032x}", _READ)
        budget.finish(attempt, _success())
    with pytest.raises(WorkflowJobError):
        budget.begin_step("step_" + f"{16:032x}", _READ)
    assert not budget.is_busy()
    for call, phase in ((_READ, "mutation"), (_WRITE, "read"), (_WRITE, "arbitrary")):
        with pytest.raises(WorkflowJobError):
            WorkflowJobBudget().begin_step(_STEP, call, phase=phase)  # type: ignore[arg-type]


def test_invalid_callback_result_is_fixed_failure_and_valid_payloads_are_detached() -> None:
    """Release returned callbacks safely without publishing a wrong-tool payload or preserving mutable data aliases."""

    budget = WorkflowJobBudget()
    first = budget.begin_step(_STEP, _READ)
    wrong = ToolResult("succeeded", {"path": "notes.txt", "undoId": "undo_" + "3" * 32})
    assert budget.finish(first, wrong).error_code == "io_error"
    retry = budget.begin_step(_STEP, _READ, retry=True)
    actual = _success()
    settled = budget.finish(retry, actual)
    assert actual.data is not None and settled.data is not None
    actual.data["content"] = "modified elsewhere"
    assert settled.data["content"] == "private body"


def test_successful_mutation_is_truthful_even_if_caller_omits_admitted_marker() -> None:
    """A verified successful mutation cannot be rewritten as an unperformed timeout by an omitted marker."""

    clock = _Clock()
    budget = WorkflowJobBudget(clock=clock)
    attempt = budget.begin_step(_STEP, _WRITE, phase="mutation")
    clock.value += 400
    actual = ToolResult("succeeded", {"path": "created.txt", "undoId": "undo_" + "3" * 32})
    assert budget.finish(attempt, actual).to_data() == actual.to_data()
