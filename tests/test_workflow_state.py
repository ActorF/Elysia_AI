"""Verify strict versioned Work state, legacy projection, and nonexecuting recovery."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from agent.work_plan import WorkPlanRunner
from agent.workflow_state import (
    MAX_WORKFLOW_STATE_BYTES, WorkflowState, WorkflowStateError,
    make_checkpoint, replace_workflow_plan, workflow_from_plan, workspace_key,
)
from work_tools.contracts import ToolCall, ToolResult


_NOW = "2026-10-14T20:00:00+00:00"


def _plan(calls: list[ToolCall] | None = None) -> dict[str, Any]:
    runner = WorkPlanRunner(
        lambda call, cancel: ToolResult("cancelled", error_code="cancelled"),
        lambda call: {},
        lambda token, granted: ToolResult("denied", error_code="permission_denied"),
    )
    return {**runner.create("Read, edit, verify", calls or [ToolCall("metadata", "notes.txt")]), "busy": False}


def _state(plan: dict[str, Any] | None = None, **changes: Any) -> WorkflowState:
    return workflow_from_plan(
        plan or _plan(), chat_id="chat_fixture", project_id="project_fixture",
        selected_workspace_key="a" * 64, created_at=_NOW, **changes,
    )


def _success(call: dict[str, Any]) -> dict[str, Any]:
    path = call["arguments"]["path"]
    if call["toolId"] == "metadata":
        return ToolResult("succeeded", {"path": path, "name": path, "kind": "file", "sizeBytes": 1}).to_data()
    return ToolResult("succeeded", {"path": path, "undoId": "undo_" + "c" * 32}).to_data()


def test_current_runner_plan_round_trips_losslessly_and_never_executes() -> None:
    """Wrap the actual Stage 9 producer's DTO without changing its public contract."""

    plan = _plan([ToolCall("metadata", "notes.txt"), ToolCall("edit", "notes.txt", content="new")])
    state = _state(plan)
    assert state.to_stage9_plan() == plan
    assert state.to_stage9_plan(busy=True) == {**plan, "busy": True}
    restored = WorkflowState.from_json(state.to_json())
    assert restored.to_data() == state.to_data()
    assert "busy" not in restored.to_data()["plan"]
    assert restored.to_data()["request"]["chatId"] == "chat_fixture"
    assert restored.to_data()["request"]["projectId"] == "project_fixture"


def test_snapshots_and_constructor_inputs_cannot_mutate_stored_state() -> None:
    """Detach every nested call/result so dictionary aliases cannot replace an approved action."""

    original = _state()
    data = original.to_data()
    state = WorkflowState(data)
    data["plan"]["steps"][0]["call"]["arguments"]["path"] = "other.txt"
    snapshot = state.to_data()
    snapshot["request"]["chatId"] = "chat_other"
    assert state.to_data() == original.to_data()


@pytest.mark.parametrize("field,prefix,suffix_length", [("chatId", "chat", 123), ("projectId", "project", 120)])
def test_canonical_owner_identifier_length_matches_128_character_boundary(field: str, prefix: str, suffix_length: int) -> None:
    """Project's longer prefix reduces its suffix budget so every canonical owner stays within 128 characters."""

    data = _state().to_data()
    data["request"][field] = prefix + "_" + "a" * suffix_length
    assert len(WorkflowState(data).to_data()["request"][field]) == 128
    data["request"][field] += "a"
    with pytest.raises(WorkflowStateError):
        WorkflowState(data)


@pytest.mark.parametrize("location,key", [
    ("top", "callback"), ("request", "workspacePath"), ("plan", "busy"),
    ("step", "write_started"), ("cursor", "command"),
])
def test_unknown_fields_are_rejected_at_every_state_layer(location: str, key: str) -> None:
    """Persist only the closed data schema; native capabilities and callbacks have no field."""

    data = _state().to_data()
    target = data if location == "top" else data["plan"]["steps"][0] if location == "step" else data[location]
    target[key] = "unsafe extra"
    with pytest.raises(WorkflowStateError):
        WorkflowState(data)


@pytest.mark.parametrize("version", [True, "1", 0, 2, None])
def test_versions_require_exact_supported_integer(version: object) -> None:
    """Reject incompatible or Boolean versions instead of silently migrating persisted requests."""

    data = _state().to_data()
    data["version"] = version
    with pytest.raises(WorkflowStateError) as error:
        WorkflowState(data)
    assert error.value.code == "unsupported_version"


@pytest.mark.parametrize("field,value", [
    ("chatId", "../../other"), ("projectId", "project_"), ("mode", "chat"),
    ("workspaceKey", "../root"), ("callsDigest", "b" * 64),
    ("title", "\ufeff"), ("title", "\ud800"), ("title", "new title"),
    ("createdAt", "2026-10-14T20:00:00"), ("createdAt", "2026-10-14T20:00:00+01:00"),
])
def test_request_identity_digest_title_and_timestamp_are_correlated(field: str, value: object) -> None:
    """Require canonical owner IDs, matching immutable calls/title, valid UTF-8, and UTC."""

    data = _state().to_data()
    data["request"][field] = value
    with pytest.raises(WorkflowStateError):
        WorkflowState(data)


@pytest.mark.parametrize("arguments", [
    {"path": "../secret"}, {"path": "C:/secret"}, {"path": "safe.txt", "command": "delete"},
    {"path": "safe.txt", "content": "unexpected"},
])
def test_state_reuses_stage9_tool_boundary(arguments: dict[str, Any]) -> None:
    """Reject escaped paths and argument pollution even in a private persisted state."""

    data = _state().to_data()
    data["plan"]["steps"][0]["call"]["arguments"] = arguments
    with pytest.raises(WorkflowStateError):
        WorkflowState(data)


def test_workspace_digest_preserves_large_native_identity_without_disclosing_path(tmp_path: Path) -> None:
    """Bind the selected directory exactly while keeping large Windows file IDs out of JSON numbers."""

    path = str(tmp_path)
    key = workspace_key(path, (7, 2**100))
    assert len(key) == 64 and key == workspace_key(path, (7, 2**100))
    assert key != workspace_key(path, (7, 2**100 + 1))
    assert key != workspace_key(str(tmp_path / "other"), (7, 2**100))
    state = workflow_from_plan(_plan(), chat_id="chat_fixture", project_id=None,
                               selected_workspace_key=key, created_at=_NOW)
    assert path not in state.to_json().decode()


@pytest.mark.parametrize("root,identity", [("relative", (1, 2)), ("C:/bad\x00root", (1, 2)),
    ("C:/root", (True, 2)), ("C:/root", (-1, 2)), ("C:/root", (1, 2**128))])
def test_workspace_digest_rejects_noncanonical_identity_inputs(root: str, identity: Any) -> None:
    """Do not hash relative/native-invalid selections or Boolean/negative directory identities."""

    with pytest.raises(WorkflowStateError):
        workspace_key(root, identity)


def test_safe_checkpoint_scrubs_waiting_approval_and_preserves_completed_results() -> None:
    """Recovery retains completed reads but requires a fresh preview and fresh authority for the write."""

    plan = _plan([ToolCall("metadata", "notes.txt"), ToolCall("edit", "notes.txt", content="new")])
    first, second = plan["steps"]
    first.update(status="succeeded", result=_success(first["call"]))
    second.update(status="waiting", approval={
        "approvalId": "approval_" + "d" * 32, "call": deepcopy(second["call"]),
        "risk": "medium", "preview": "old secret preview", "expiresAt": _NOW,
    })
    plan["state"] = "waiting"
    state = _state(plan)
    recovered = make_checkpoint(state, saved_at=_NOW)
    data = recovered.to_data()
    assert data["plan"]["state"] == "paused"
    assert data["plan"]["steps"][0] == first
    assert data["plan"]["steps"][1]["status"] == "pending"
    assert data["plan"]["steps"][1]["approval"] is None
    assert data["checkpoint"]["disposition"] == "revalidate"
    assert data["checkpoint"]["requiresRevalidation"] is True
    assert "old secret preview" not in recovered.to_json().decode()
    assert "approval_" not in recovered.to_json().decode()
    assert WorkflowState.from_json(recovered.to_json()).to_data() == data
    with pytest.raises(WorkflowStateError):
        replace_workflow_plan(recovered, recovered.to_stage9_plan())


@pytest.mark.parametrize("tool_id", ["metadata", "edit"])
def test_nonadmitted_inflight_read_or_preview_can_be_revalidated(tool_id: str) -> None:
    """A trusted not-admitted marker distinguishes preview I/O from mutation admission."""

    call = ToolCall("metadata", "notes.txt") if tool_id == "metadata" else ToolCall("edit", "notes.txt", content="new")
    plan = _plan([call])
    plan["state"] = "running"
    plan["steps"][0]["status"] = "running"
    state = _state(plan, admitted_step_id=None)
    data = make_checkpoint(state, saved_at=_NOW).to_data()
    assert data["admittedStepId"] is None
    assert data["checkpoint"]["disposition"] == "revalidate"
    assert data["plan"]["steps"][0]["status"] == "pending"


def test_unknown_running_write_requires_journal_review_and_never_becomes_retryable() -> None:
    """An ambiguous admitted mutation survives recovery as uncertain, never as a repeatable pending write."""

    plan = _plan([ToolCall("edit", "notes.txt", content="new")])
    plan["state"] = "running"
    plan["steps"][0]["status"] = "running"
    state = _state(plan)
    recovered = make_checkpoint(state, saved_at=_NOW)
    data = recovered.to_data()
    assert data["admittedStepId"] == plan["steps"][0]["stepId"]
    assert data["checkpoint"]["disposition"] == "uncertain_write"
    assert data["plan"]["state"] == "paused"
    assert data["plan"]["steps"][0]["status"] == "running"
    assert data["error"]["code"] == "recovery_required"
    assert recovered.to_stage9_plan()["busy"] is False
    for action_plan in (recovered.to_stage9_plan(), {**recovered.to_stage9_plan(), "state": "running"}):
        with pytest.raises(WorkflowStateError, match="interrupted write"):
            replace_workflow_plan(recovered, action_plan)


def test_checkpoint_parent_chain_is_explicit_and_no_restore_has_worker_lease() -> None:
    """Versioned checkpoint provenance chains data only and cannot claim a process worker."""

    first = make_checkpoint(_state(), saved_at=_NOW)
    second = make_checkpoint(first, saved_at=_NOW)
    assert second.to_data()["checkpoint"]["parentId"] == first.to_data()["checkpoint"]["checkpointId"]
    assert second.to_data()["revision"] == 2
    assert not second.to_stage9_plan()["busy"]


def test_cancelled_admitted_write_preserves_late_actual_success() -> None:
    """Logical cancellation does not erase the durable result of an already admitted native write."""

    plan = _plan([ToolCall("edit", "notes.txt", content="new")])
    plan["state"] = "running"
    plan["steps"][0]["status"] = "running"
    state = _state(plan)
    cancelled = replace_workflow_plan(state, {**plan, "state": "cancelled", "busy": True})
    settled = deepcopy(cancelled.to_stage9_plan())
    settled["steps"][0].update(status="succeeded", result=_success(settled["steps"][0]["call"]))
    final = replace_workflow_plan(cancelled, settled)
    assert final.to_data()["plan"]["state"] == "cancelled"
    assert final.to_data()["result"] == {"status": "cancelled", "completedSteps": 1}
    assert final.to_data()["admittedStepId"] is None
    assert final.to_stage9_plan()["steps"][0]["result"]["status"] == "succeeded"


def test_terminal_states_and_settled_results_cannot_be_restarted_or_rewritten() -> None:
    """Absorbing terminal plans stop replay and keep previously published file outcomes immutable."""

    plan = _plan()
    pending = _state(plan)
    running = replace_workflow_plan(pending, {**plan, "state": "running"})
    succeeded = deepcopy(running.to_stage9_plan())
    succeeded["state"] = "succeeded"
    succeeded["steps"][0].update(status="succeeded", result=_success(succeeded["steps"][0]["call"]))
    complete = replace_workflow_plan(running, succeeded)
    with pytest.raises(WorkflowStateError):
        replace_workflow_plan(complete, {**succeeded, "state": "running"})
    changed = deepcopy(succeeded)
    changed["steps"][0]["result"]["data"]["sizeBytes"] = 999
    with pytest.raises(WorkflowStateError):
        replace_workflow_plan(complete, changed)
    assert make_checkpoint(complete, saved_at=_NOW).to_data()["result"]["status"] == "succeeded"


def test_call_and_plan_identity_are_immutable_across_progress_updates() -> None:
    """A new call or reordered/replaced step requires a new explicit plan and approval route."""

    state = _state()
    for change in ("planId", "stepId", "call"):
        plan = state.to_stage9_plan()
        if change == "call":
            plan["steps"][0]["call"]["arguments"]["path"] = "other.txt"
        elif change == "stepId":
            plan["steps"][0]["stepId"] = "step_" + "e" * 32
        else:
            plan["planId"] = "plan_" + "e" * 32
        with pytest.raises(WorkflowStateError):
            replace_workflow_plan(state, plan)


def test_running_step_cannot_be_reset_to_pending_through_progress_updates() -> None:
    """Only checkpoint preparation may reset interrupted safe I/O; ordinary updates cannot replay calls."""

    plan = _plan()
    plan["state"] = "running"
    plan["steps"][0]["status"] = "running"
    state = _state(plan)
    backward = deepcopy(plan)
    backward["steps"][0]["status"] = "pending"
    with pytest.raises(WorkflowStateError):
        replace_workflow_plan(state, backward)


def test_error_text_is_fixed_and_failed_checkpoint_preserves_actual_failure() -> None:
    """Native exception strings cannot enter failure state; checkpointing must not erase a failure."""

    plan = _plan()
    plan["state"] = "failed"
    plan["steps"][0].update(status="failed", result=ToolResult("failed", error_code="io_error").to_data())
    state = _state(plan)
    assert make_checkpoint(state, saved_at=_NOW).to_data()["error"] == state.to_data()["error"]
    data = state.to_data()
    data["error"]["message"] = "C:/private/secret.txt: raw diagnostics"
    with pytest.raises(WorkflowStateError) as error:
        WorkflowState(data)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("mutation", ["bool_revision", "bool_count", "duplicate_step", "out_of_order", "multiple_active"])
def test_revision_counts_unique_steps_and_sequential_state_are_strict(mutation: str) -> None:
    """Reject Boolean integers and impossible sequential progress before graph routing."""

    state = _state(_plan([ToolCall("metadata", "first.txt"), ToolCall("metadata", "second.txt")]))
    data = state.to_data()
    plan = data["plan"]
    if mutation == "bool_revision":
        data["revision"] = True
    elif mutation == "duplicate_step":
        plan["steps"][1]["stepId"] = plan["steps"][0]["stepId"]
    elif mutation == "bool_count":
        plan["state"] = "cancelled"
        for step in plan["steps"]:
            step.update(status="cancelled", result=ToolResult("cancelled", error_code="cancelled").to_data())
        data["result"] = {"status": "cancelled", "completedSteps": False}
    else:
        plan["state"] = "running"
        plan["steps"][1]["status"] = "running"
        if mutation == "multiple_active":
            plan["steps"][0]["status"] = "running"
    with pytest.raises(WorkflowStateError):
        WorkflowState(data)


@pytest.mark.parametrize("payload", [b'{"version":1,"version":1}', b'{"version":NaN}', b'\xff', b'[' * 1000])
def test_json_loader_rejects_duplicate_nonfinite_invalid_utf8_and_hostile_depth(payload: bytes) -> None:
    """Authenticate strict JSON rather than allowing parser extensions or ambiguous persisted keys."""

    with pytest.raises(WorkflowStateError):
        WorkflowState.from_json(payload)


def test_frame_size_limit_is_enforced_before_json_parsing_and_after_encoding() -> None:
    """Bound both persisted byte frames and combined multi-step content allocations."""

    with pytest.raises(WorkflowStateError):
        WorkflowState.from_json(b" " * (MAX_WORKFLOW_STATE_BYTES + 1))
    data = _state().to_data()
    data["unexpected"] = "x" * MAX_WORKFLOW_STATE_BYTES
    with pytest.raises(WorkflowStateError):
        WorkflowState(data)


def test_unsafe_checkpoint_fields_and_permission_tokens_are_rejected() -> None:
    """A forged recovery record cannot claim validated rights or downgrade an uncertain write."""

    state = make_checkpoint(_state(), saved_at=_NOW)
    for change in ("requiresRevalidation", "disposition", "parentId", "extra"):
        data = state.to_data()
        checkpoint = data["checkpoint"]
        if change == "extra":
            checkpoint["approved"] = True
        elif change == "parentId":
            checkpoint["parentId"] = checkpoint["checkpointId"]
        else:
            checkpoint[change] = False if change == "requiresRevalidation" else "uncertain_write"
        with pytest.raises(WorkflowStateError):
            WorkflowState(data)
    assert set(json.loads(state.to_json())) == {
        "version", "workflowId", "revision", "request", "plan", "cursor", "admittedStepId", "result", "error", "checkpoint",
    }
