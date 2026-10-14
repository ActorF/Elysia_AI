"""Reject plan retargeting and malformed state before crossing the desktop wire."""

from copy import deepcopy
from typing import Any

import pytest

from work_tools.contracts import ToolResult, validate_plan_title
from work_tools.wire import validate_work_params, validate_work_result


def _plan() -> dict[str, Any]:
    return {"kind": "work.plan", "plan": {
        "planId": "plan_" + "1" * 32, "title": "Reviewed plan", "state": "succeeded", "busy": False,
        "steps": [{"stepId": "step_" + "2" * 32,
                   "call": {"toolId": "read_text", "arguments": {"path": "notes.txt"}},
                   "status": "succeeded", "approval": None,
                   "result": ToolResult("succeeded", {"path": "notes.txt", "content": "text", "sizeBytes": 4}).to_data()}],
    }}


@pytest.mark.parametrize("title", ["\ufeff", " \u0085\ufeff", "\ud800", "x" * 201, "a\x00b"])
def test_plan_titles_match_desktop_unicode_and_encoding_limits(title: str) -> None:
    """Reject Unicode blank-only or unencodable titles before worker admission."""

    with pytest.raises(ValueError):
        validate_plan_title(title)
    with pytest.raises(ValueError):
        validate_work_params("work.plan.create", {"chatId": "chat_test", "title": title,
                            "calls": [{"toolId": "metadata", "arguments": {"path": ""}}]})


def test_plan_titles_preserve_astral_code_points_and_protocol_nonblank_controls() -> None:
    """Use code points rather than UTF-16 units and preserve the reviewed text."""

    assert validate_plan_title("🙂" * 200) == "🙂" * 200
    assert validate_plan_title("\x1c") == "\x1c"


@pytest.mark.parametrize("mutation", ["retarget", "duplicate", "bad_status", "unknown", "multiple_active"])
def test_plan_results_reject_cross_step_paths_and_inconsistent_states(mutation: str) -> None:
    """Prevent a valid tool result from being presented as another step's outcome."""

    state = _plan()
    assert validate_work_result(state) == state
    plan = state["plan"]
    step = plan["steps"][0]
    if mutation == "retarget":
        step["result"]["data"]["path"] = "other.txt"
    elif mutation == "duplicate":
        plan["steps"].append(deepcopy(step))
    elif mutation == "bad_status":
        step["status"] = "failed"
    elif mutation == "unknown":
        plan["nativePath"] = "C:/private"
    else:
        step["status"], step["result"] = "running", None
        second = deepcopy(step)
        second["stepId"] = "step_" + "3" * 32
        plan["steps"].append(second)
    with pytest.raises(ValueError):
        validate_work_result(state)


def test_cancelled_plan_can_retain_an_admitted_write_while_physical_io_drains() -> None:
    """Keep cancellation truthful without inventing termination of a native write."""

    state = _plan()
    state["plan"].update(state="cancelled", busy=True)
    step = state["plan"]["steps"][0]
    step.update(call={"toolId": "edit", "arguments": {"path": "notes.txt", "content": "text"}},
                status="running", result=None)
    assert validate_work_result(state) == state
