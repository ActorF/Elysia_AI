"""Validate live Work budget observations against one exact public plan.

The DTO describes progress and possible user actions, never execution authority.
Its physical lease can remain active after logical cancellation; binding it to
the step identity rather than a running status preserves truthful draining.
Budget limits and retry hints are checked here before crossing process/renderer
boundaries, while the native budget independently authorizes every admission.
"""

from __future__ import annotations

import math
from typing import Any, cast

from .contracts import ToolProtocolError


_FIELDS = frozenset({
    "planId", "totalTimeoutSeconds", "totalRemainingSeconds", "stepTimeoutSeconds",
    "mutationAdmissionSeconds", "maxReadRetries", "pauseCountsTowardBudget", "physicalBusy",
    "cancelRequested", "closed", "activeStepId", "activePhase", "activeAttempt",
    "stepRemainingSeconds", "stopReason", "retryableStepIds",
})
_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})


def _number(value: object, minimum: float, maximum: float) -> bool:
    return type(value) in {int, float} and math.isfinite(cast(float, value)) and minimum <= cast(float, value) <= maximum


def validate_work_job(value: object, plan: dict[str, Any] | None) -> dict[str, Any]:
    """Require a detached exact plan-bound observation with finite limits and safe read-only retry hints."""

    # The existing plan gate is shared rather than duplicated. Its import is
    # local because wire will itself call this validator only after it has
    # validated a complete plan; eager imports would create a module cycle.
    from .wire import _validate_plan

    try:
        _validate_plan(plan)
        if type(value) is not dict or set(value) != _FIELDS or plan is None or value["planId"] != plan["planId"]:
            raise ToolProtocolError()
        total = value["totalTimeoutSeconds"]
        if (not _number(total, 0, 900) or total == 0
            or not _number(value["totalRemainingSeconds"], 0, total)
            or type(value["stepTimeoutSeconds"]) not in {int, float} or value["stepTimeoutSeconds"] != 30
            or type(value["mutationAdmissionSeconds"]) not in {int, float} or value["mutationAdmissionSeconds"] != 20
            or type(value["maxReadRetries"]) is not int or not 0 <= value["maxReadRetries"] <= 2
            or value["pauseCountsTowardBudget"] is not True
            or any(type(value[key]) is not bool for key in ("physicalBusy", "cancelRequested", "closed"))
            or value["stopReason"] is not None and (type(value["stopReason"]) is not str or value["stopReason"] not in {"cancelled", "timeout"})):
            raise ToolProtocolError()
        if value["physicalBusy"]:
            active = next((step for step in plan["steps"] if step["stepId"] == value["activeStepId"]), None)
            phase = value["activePhase"]
            if (active is None or type(phase) is not str or phase not in {"read", "preview", "mutation"}
                or (active["call"]["toolId"] in _READ_TOOLS) != (phase == "read")
                or type(value["activeAttempt"]) is not int or not 1 <= value["activeAttempt"] <= value["maxReadRetries"] + 1
                or phase != "read" and value["activeAttempt"] != 1
                or not _number(value["stepRemainingSeconds"], 0, 20 if phase == "mutation" else 30)
                or value["stepRemainingSeconds"] > value["totalRemainingSeconds"]):
                raise ToolProtocolError()
        elif any(value[key] is not None for key in ("activeStepId", "activePhase", "activeAttempt", "stepRemainingSeconds")):
            raise ToolProtocolError()
        stopped = ("cancelled" if value["cancelRequested"] or value["closed"] else
                   "timeout" if value["totalRemainingSeconds"] == 0
                   or value["physicalBusy"] and value["stepRemainingSeconds"] == 0 else None)
        if value["stopReason"] != stopped:
            raise ToolProtocolError()
        retryable = value["retryableStepIds"]
        if type(retryable) is not list or len(retryable) > 16 or any(type(item) is not str for item in retryable):
            raise ToolProtocolError()
        if len(set(retryable)) != len(retryable):
            raise ToolProtocolError()
        for step_id in retryable:
            step = next((step for step in plan["steps"] if step["stepId"] == step_id), None)
            if (step is None or step["status"] != "failed" or step["call"]["toolId"] not in _READ_TOOLS
                or step["result"] is None or step["result"]["status"] != "failed"
                or step["result"]["error"] is None or step["result"]["error"]["code"] not in {"timeout", "io_error"}):
                raise ToolProtocolError()
        if retryable and (value["physicalBusy"] or value["closed"] or value["cancelRequested"] or stopped is not None):
            raise ToolProtocolError()
        return {**value, "retryableStepIds": list(retryable)}
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError):
        raise ToolProtocolError() from None
