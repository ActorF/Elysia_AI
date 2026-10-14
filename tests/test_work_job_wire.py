"""Verify Work job DTO bounds, plan correlation, safe retry hints, and draining states."""

from copy import deepcopy
from typing import Any

import pytest

from agent.workflow_jobs import WorkflowJobBudget
from work_tools.contracts import ToolCall, ToolProtocolError, ToolResult
from work_tools.job_wire import validate_work_job


_PLAN = "plan_" + "1" * 32
_STEP = "step_" + "2" * 32


def _plan(call: ToolCall | None = None, status: str = "pending", result: ToolResult | None = None) -> dict[str, Any]:
    return {
        "planId": _PLAN, "title": "Synthetic Work job", "state": "failed" if status == "failed" else "pending", "busy": False,
        "steps": [{"stepId": _STEP, "call": (call or ToolCall("metadata", "")).to_data(), "status": status,
                   "result": None if result is None else result.to_data(), "approval": None}],
    }


def _job() -> dict[str, Any]:
    return {"planId": _PLAN, **WorkflowJobBudget(clock=lambda: 100.0).snapshot()}


def test_valid_idle_and_draining_cancelled_leases_are_detached_and_correlated() -> None:
    """An active physical read may belong to a logically cancelled step until its native callback returns."""

    plan = _plan()
    idle = _job()
    assert validate_work_job(idle, plan) == idle
    returned = validate_work_job(idle, plan)
    returned["retryableStepIds"].append(_STEP)
    assert idle["retryableStepIds"] == []
    plan["state"] = "cancelled"
    plan["steps"][0].update(status="cancelled", result=ToolResult("cancelled", error_code="cancelled").to_data())
    active = {**idle, "physicalBusy": True, "cancelRequested": True, "activeStepId": _STEP,
              "activePhase": "read", "activeAttempt": 1, "stepRemainingSeconds": 25.0, "stopReason": "cancelled"}
    assert validate_work_job(active, plan) == active


@pytest.mark.parametrize("change", [
    {"extra": "native capability"}, {"planId": "plan_" + "3" * 32}, {"totalTimeoutSeconds": 0},
    {"totalTimeoutSeconds": 901}, {"totalTimeoutSeconds": True}, {"totalTimeoutSeconds": float("nan")},
    {"totalRemainingSeconds": 301}, {"totalRemainingSeconds": -1}, {"stepTimeoutSeconds": 31},
    {"mutationAdmissionSeconds": 21}, {"maxReadRetries": 3}, {"maxReadRetries": True},
    {"maxReadRetries": 1.5}, {"pauseCountsTowardBudget": False}, {"physicalBusy": 1},
    {"cancelRequested": 1}, {"closed": 0}, {"stopReason": "conflict"}, {"retryableStepIds": [_STEP]},
    {"activeStepId": _STEP}, {"activePhase": "read"}, {"activeAttempt": 1}, {"stepRemainingSeconds": 10},
    {"totalRemainingSeconds": 0}, {"stopReason": "timeout"}, {"cancelRequested": True}, {"closed": True},
])
def test_unknown_untyped_unbounded_or_contradictory_idle_fields_are_refused(change: dict[str, Any]) -> None:
    """A producer cannot inflate budgets, invent retry authority, or hide a stopped lease in progress metadata."""

    with pytest.raises(ToolProtocolError):
        validate_work_job({**_job(), **change}, _plan())


@pytest.mark.parametrize("change", [
    {"activeStepId": "step_" + "4" * 32}, {"activePhase": "mutation"}, {"activePhase": "arbitrary"},
    {"activeAttempt": 0}, {"activeAttempt": 4}, {"activeAttempt": True}, {"stepRemainingSeconds": 31},
    {"stepRemainingSeconds": float("inf")}, {"stepRemainingSeconds": True},
    {"totalRemainingSeconds": 4, "stepRemainingSeconds": 5}, {"stepRemainingSeconds": 0},
    {"retryableStepIds": [_STEP]},
])
def test_active_read_facts_require_the_current_step_phase_attempt_and_clamped_deadline(change: dict[str, Any]) -> None:
    """Bound active reads by exact membership and both step/total time without trusting logical running flags."""

    active = {**_job(), "physicalBusy": True, "activeStepId": _STEP, "activePhase": "read",
              "activeAttempt": 1, "stepRemainingSeconds": 20}
    with pytest.raises(ToolProtocolError):
        validate_work_job({**active, **change}, _plan())


@pytest.mark.parametrize("phase", ["preview", "mutation"])
def test_write_progress_is_single_attempt_and_never_a_read_retry_hint(phase: str) -> None:
    """A write phase uses the proper preparation ceiling but cannot impersonate a retryable safe read."""

    plan = _plan(ToolCall("create", "notes.txt", content="synthetic"))
    active = {**_job(), "physicalBusy": True, "activeStepId": _STEP, "activePhase": phase,
              "activeAttempt": 1, "stepRemainingSeconds": 20}
    assert validate_work_job(active, plan) == active
    for change in ({"activeAttempt": 2}, {"activePhase": "read"}, {"retryableStepIds": [_STEP]}):
        with pytest.raises(ToolProtocolError):
            validate_work_job({**active, **change}, plan)


@pytest.mark.parametrize("change", [
    {"retryableStepIds": [_STEP, _STEP]}, {"retryableStepIds": ["step_" + "4" * 32]},
    {"retryableStepIds": [False]}, {"retryableStepIds": {"stepId": _STEP}},
    {"closed": True, "stopReason": "cancelled"}, {"cancelRequested": True, "stopReason": "cancelled"},
    {"totalRemainingSeconds": 0, "stopReason": "timeout"},
])
def test_retry_hints_are_unique_exact_failed_reads_and_require_an_idle_open_budget(change: dict[str, Any]) -> None:
    """Hints remain descriptive and cannot escape current plan membership, stop facts, or native retry checks."""

    plan = _plan(status="failed", result=ToolResult("failed", error_code="io_error"))
    idle = {**_job(), "retryableStepIds": [_STEP]}
    assert validate_work_job(idle, plan) == idle
    with pytest.raises(ToolProtocolError):
        validate_work_job({**idle, **change}, plan)


@pytest.mark.parametrize("call,error", [(ToolCall("metadata", ""), "conflict"),
                                        (ToolCall("delete", "notes.txt"), "io_error")])
def test_conflicts_and_failed_mutations_never_advertise_retry(call: ToolCall, error: str) -> None:
    """A transient-looking write error cannot obtain the safe-read retry presentation."""

    plan = _plan(call, "failed", ToolResult("failed", error_code=error))
    with pytest.raises(ToolProtocolError):
        validate_work_job({**_job(), "retryableStepIds": [_STEP]}, plan)


def test_every_field_is_required_and_an_invalid_or_absent_plan_is_refused() -> None:
    """Reject partial observations and reuse full plan validation without a parallel permissive contract."""

    idle = _job()
    for field in idle:
        missing = deepcopy(idle)
        del missing[field]
        with pytest.raises(ToolProtocolError):
            validate_work_job(missing, _plan())
    for plan in (None, {"planId": _PLAN}, {**_plan(), "steps": []}):
        with pytest.raises(ToolProtocolError):
            validate_work_job(idle, plan)


def test_valid_expiry_cancellation_and_total_clamped_active_budgets_match_native_snapshots() -> None:
    """Accept truthful stop facts emitted by the live budget at exact deadline and cancel boundaries."""

    tick = 100.0
    budget = WorkflowJobBudget(total_timeout_seconds=5, clock=lambda: tick)
    lease = budget.begin_step(_STEP, ToolCall("metadata", ""))
    active = {"planId": _PLAN, **budget.snapshot()}
    assert active["stepRemainingSeconds"] == active["totalRemainingSeconds"] == 5
    assert validate_work_job(active, _plan()) == active
    tick += 5
    expired = {"planId": _PLAN, **budget.snapshot()}
    assert validate_work_job(expired, _plan()) == expired
    budget.cancel()
    cancelled = {"planId": _PLAN, **budget.snapshot()}
    assert validate_work_job(cancelled, _plan()) == cancelled
    budget.finish(lease, ToolResult("failed", error_code="io_error"))
    settled = {"planId": _PLAN, **budget.snapshot()}
    assert validate_work_job(settled, _plan()) == settled
