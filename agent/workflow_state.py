"""Define the versioned, capability-free state shared by Work graphs and recovery.

Stage 9 plan snapshots remain the public projection. This private envelope binds
the ordered calls to their canonical owner and an opaque selected-root digest;
loading it never reconstructs a filesystem capability or authorizes a write.
Immutable encoded storage prevents callers from changing a state after validation.
Checkpoint preparation distinguishes interrupted admitted mutations from safely
repeatable reads/previews: an uncertain write requires journal review/new planning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Final, Literal, TypedDict
from uuid import uuid4

from work_tools.contracts import ERROR_MESSAGES, ToolCall, validate_plan_title
from work_tools.wire import validate_work_result


WORKFLOW_STATE_VERSION: Final = 1
MAX_WORKFLOW_STATE_BYTES: Final = 768 * 1024
WorkflowNode = Literal["brain", "memory", "rag", "tool", "approval", "verification", "end"]
_NODES = frozenset({"brain", "memory", "rag", "tool", "approval", "verification", "end"})
_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})
_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_TRANSITIONS = {
    "pending": {"pending", "running", "paused", "cancelled"},
    "running": {"running", "waiting", "paused", "succeeded", "failed", "cancelled"},
    "waiting": {"waiting", "running", "paused", "failed", "cancelled"},
    "paused": {"paused", "running", "waiting", "failed", "cancelled"},
    "succeeded": {"succeeded"}, "failed": {"failed"}, "cancelled": {"cancelled"},
}
_STEP_TRANSITIONS = {
    "pending": {"pending", "running", "waiting", "succeeded", "failed", "cancelled"},
    "running": {"running", "waiting", "succeeded", "failed", "cancelled"},
    "waiting": {"waiting", "running", "failed", "cancelled"},
    "succeeded": {"succeeded"}, "failed": {"failed"}, "cancelled": {"cancelled"},
}
_ERRORS = {**ERROR_MESSAGES, "invalid_state": "The workflow state is invalid.",
           "unsupported_version": "This workflow version is not supported.",
           "state_conflict": "The workflow state changed.",
           "recovery_required": "Review the interrupted write before creating a new plan."}


class WorkflowRequest(TypedDict):
    """Identify one explicit request without embedding a native capability or prompt."""

    requestId: str
    chatId: str
    projectId: str | None
    mode: Literal["work"]
    workspaceKey: str
    callsDigest: str
    title: str
    createdAt: str


class WorkflowStep(TypedDict):
    """Retain the exact Stage 9 call, result, and waiting-approval step contract."""

    stepId: str
    call: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    approval: dict[str, Any] | None


class WorkflowApproval(TypedDict):
    """Disclose one exact Stage 9 write preview; serialized tokens confer no authority."""

    approvalId: str
    call: dict[str, Any]
    risk: str
    preview: str
    expiresAt: str


class WorkflowPlan(TypedDict):
    """Keep logical plan progress separate from process-local physical worker leases."""

    planId: str
    title: str
    state: str
    steps: list[WorkflowStep]


class WorkflowResult(TypedDict):
    """Summarize a terminal plan without duplicating its potentially private read bodies."""

    status: str
    completedSteps: int


class WorkflowError(TypedDict):
    """Describe failure using fixed text and bounded node/step identifiers only."""

    code: str
    message: str
    node: WorkflowNode
    stepId: str | None


class WorkflowCheckpoint(TypedDict):
    """Record recovery provenance; every restored owner must revalidate its authority."""

    checkpointId: str
    parentId: str | None
    savedAt: str
    disposition: Literal["revalidate", "uncertain_write"]
    requiresRevalidation: Literal[True]


class WorkflowStateData(TypedDict):
    """Provide the strict serializable schema consumed by the later StateGraph bridge."""

    version: int
    workflowId: str
    revision: int
    request: WorkflowRequest
    plan: WorkflowPlan
    cursor: dict[str, Any]
    admittedStepId: str | None
    result: WorkflowResult | None
    error: WorkflowError | None
    checkpoint: WorkflowCheckpoint | None


class WorkflowStateError(ValueError):
    """Expose a closed error code without retaining unsafe input or exception details."""

    def __init__(self, code: str = "invalid_state") -> None:
        """Select fixed error text; callers cannot supply arbitrary diagnostics."""

        self.code = code if type(code) is str and code in _ERRORS else "invalid_state"
        super().__init__(_ERRORS[self.code])


def _keys(value: object, expected: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise WorkflowStateError()
    return value


def _id(value: object, prefix: str, *, owner: bool = False) -> None:
    suffix_limit = 128 - len(prefix) - 1
    pattern = prefix + (rf"_[A-Za-z0-9_-]{{1,{suffix_limit}}}" if owner else r"_[0-9a-f]{32}")
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise WorkflowStateError()


def _utc(value: object) -> None:
    if type(value) is not str or len(value) > 64:
        raise WorkflowStateError()
    try:
        offset = datetime.fromisoformat(value).utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise WorkflowStateError()
    except ValueError:
        raise WorkflowStateError() from None


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def workspace_key(normalized_root: str, root_identity: tuple[int, int]) -> str:
    """Hash a trusted boundary's exact canonical path and directory identity.

    The caller supplies WorkspaceBoundary facts, never a renderer path. Decimal
    strings preserve native file identifiers larger than JavaScript safe integers;
    the digest itself cannot reopen or discover the selected directory.
    """

    if (type(normalized_root) is not str or not 1 <= len(normalized_root) <= 32767
        or not Path(normalized_root).is_absolute()
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized_root)
        or type(root_identity) is not tuple or len(root_identity) != 2
        or any(type(part) is not int or not 0 <= part < 2**128 for part in root_identity)):
        raise WorkflowStateError()
    try:
        return _digest({"root": normalized_root, "identity": [str(part) for part in root_identity]})
    except UnicodeError:
        raise WorkflowStateError() from None


def _guard_json(value: object, depth: int, remaining: list[int]) -> None:
    """Bound hostile traversal before JSON encoding; do not accept Python object hooks."""

    remaining[0] -= 1
    if depth > 20 or remaining[0] < 0:
        raise WorkflowStateError()
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is str:
        if "\x00" in value:
            raise WorkflowStateError()
        value.encode("utf-8")
    elif type(value) is list:
        for item in value:
            _guard_json(item, depth + 1, remaining)
    elif type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise WorkflowStateError()
            _guard_json(key, depth + 1, remaining)
            _guard_json(item, depth + 1, remaining)
    else:
        raise WorkflowStateError()


def _encode(value: object) -> bytes:
    try:
        _guard_json(value, 0, [100_000])
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_WORKFLOW_STATE_BYTES:
            raise WorkflowStateError()
        return encoded
    except (UnicodeError, TypeError, OverflowError, RecursionError):
        raise WorkflowStateError() from None


def _result(plan: dict[str, Any]) -> dict[str, Any] | None:
    if plan["state"] not in _TERMINAL:
        return None
    return {"status": plan["state"],
            "completedSteps": sum(step["status"] == "succeeded" for step in plan["steps"])}


def _error(plan: dict[str, Any], cursor: dict[str, Any]) -> dict[str, Any] | None:
    if plan["state"] != "failed":
        return None
    step = next((step for step in plan["steps"] if step["status"] == "failed"), None)
    code = "io_error" if step is None else step["result"]["error"]["code"]
    return {"code": code, "message": _ERRORS[code], "node": cursor["node"],
            "stepId": None if step is None else step["stepId"]}


def _validate(value: dict[str, Any]) -> None:
    _keys(value, {"version", "workflowId", "revision", "request", "plan", "cursor",
                  "admittedStepId", "result", "error", "checkpoint"})
    if type(value["version"]) is not int or value["version"] != WORKFLOW_STATE_VERSION:
        raise WorkflowStateError("unsupported_version")
    _id(value["workflowId"], "workflow")
    if type(value["revision"]) is not int or not 0 <= value["revision"] <= 2**53 - 1:
        raise WorkflowStateError()
    request = _keys(value["request"], {"requestId", "chatId", "projectId", "mode",
                                       "workspaceKey", "callsDigest", "title", "createdAt"})
    _id(request["requestId"], "request")
    _id(request["chatId"], "chat", owner=True)
    if request["projectId"] is not None:
        _id(request["projectId"], "project", owner=True)
    if request["mode"] != "work":
        raise WorkflowStateError()
    for key in ("workspaceKey", "callsDigest"):
        if type(request[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", request[key]) is None:
            raise WorkflowStateError()
    validate_plan_title(request["title"])
    _utc(request["createdAt"])
    plan = _keys(value["plan"], {"planId", "title", "state", "steps"})
    validate_work_result({"kind": "work.plan", "plan": {**plan, "busy": False}})
    if plan["title"] != request["title"] or _digest([step["call"] for step in plan["steps"]]) != request["callsDigest"]:
        raise WorkflowStateError()
    cursor = _keys(value["cursor"], {"node", "stepId"})
    if type(cursor["node"]) is not str or cursor["node"] not in _NODES:
        raise WorkflowStateError()
    steps = {step["stepId"]: step for step in plan["steps"]}
    for field_value in (cursor["stepId"], value["admittedStepId"]):
        if field_value is not None and (type(field_value) is not str or field_value not in steps):
            raise WorkflowStateError()
    if value["admittedStepId"] is not None:
        admitted = steps[value["admittedStepId"]]
        if admitted["status"] != "running" or admitted["call"]["toolId"] in _READ_TOOLS:
            raise WorkflowStateError()
    # Completed steps form a prefix. Cancellation may settle later untouched
    # steps while an admitted write drains, so it is intentionally exceptional.
    saw_unfinished = False
    for step in plan["steps"]:
        if step["status"] in {"running", "waiting"} and saw_unfinished:
            raise WorkflowStateError()
        if step["status"] == "succeeded":
            if saw_unfinished:
                raise WorkflowStateError()
        else:
            saw_unfinished = True
    if plan["state"] == "pending" and any(step["status"] != "pending" for step in plan["steps"]):
        raise WorkflowStateError()
    if plan["state"] == "succeeded" and any(step["status"] != "succeeded" for step in plan["steps"]):
        raise WorkflowStateError()
    if plan["state"] == "waiting" and not any(step["status"] == "waiting" for step in plan["steps"]):
        raise WorkflowStateError()
    if plan["state"] in {"succeeded", "failed"} and value["admittedStepId"] is not None:
        raise WorkflowStateError()
    if plan["state"] == "failed" and any(step["status"] in {"running", "waiting"} for step in plan["steps"]):
        raise WorkflowStateError()
    if plan["state"] == "cancelled" and any(step["status"] == "waiting" or
        (step["status"] == "running" and step["stepId"] != value["admittedStepId"]) for step in plan["steps"]):
        raise WorkflowStateError()
    if value["result"] != _result(plan):
        raise WorkflowStateError()
    if value["result"] is not None:
        result = _keys(value["result"], {"status", "completedSteps"})
        if type(result["completedSteps"]) is not int:
            raise WorkflowStateError()
    if value["error"] is not None:
        error = _keys(value["error"], {"code", "message", "node", "stepId"})
        if type(error["code"]) is not str or error["code"] not in _ERRORS or error["message"] != _ERRORS[error["code"]]:
            raise WorkflowStateError()
        if error["node"] not in _NODES or (error["stepId"] is not None and error["stepId"] not in steps):
            raise WorkflowStateError()
    if plan["state"] == "failed" and value["error"] is None:
        raise WorkflowStateError()
    if plan["state"] not in {"failed", "paused"} and value["error"] is not None:
        raise WorkflowStateError()
    checkpoint = value["checkpoint"]
    if checkpoint is not None:
        _keys(checkpoint, {"checkpointId", "parentId", "savedAt", "disposition", "requiresRevalidation"})
        _id(checkpoint["checkpointId"], "checkpoint")
        if checkpoint["parentId"] is not None:
            _id(checkpoint["parentId"], "checkpoint")
            if checkpoint["parentId"] == checkpoint["checkpointId"]:
                raise WorkflowStateError()
        _utc(checkpoint["savedAt"])
        if checkpoint["disposition"] not in {"revalidate", "uncertain_write"} or checkpoint["requiresRevalidation"] is not True:
            raise WorkflowStateError()
        if any(step["approval"] is not None for step in plan["steps"]):
            raise WorkflowStateError()
        if checkpoint["disposition"] == "uncertain_write":
            if value["admittedStepId"] is None or plan["state"] != "paused":
                raise WorkflowStateError()
            if value["error"] is None or value["error"]["code"] != "recovery_required" or value["error"]["stepId"] != value["admittedStepId"]:
                raise WorkflowStateError()
        elif (value["admittedStepId"] is not None or plan["state"] not in {"paused", *_TERMINAL}
              or any(step["status"] in {"running", "waiting"} for step in plan["steps"])):
            raise WorkflowStateError()


@dataclass(frozen=True, slots=True, init=False)
class WorkflowState:
    """Own a fully validated immutable JSON state, independent of workers and permissions."""

    _encoded: bytes = field(repr=False)

    def __init__(self, value: object) -> None:
        """Detach and validate exact state fields before accepting a domain snapshot."""

        encoded = _encode(value)
        try:
            detached = json.loads(encoded)
            _validate(detached)
        except WorkflowStateError:
            raise
        except (ValueError, TypeError, KeyError, OverflowError):
            raise WorkflowStateError() from None
        object.__setattr__(self, "_encoded", encoded)

    def to_data(self) -> dict[str, Any]:
        """Return a fresh JSON object; mutating it cannot change this state."""

        data: dict[str, Any] = json.loads(self._encoded)
        return data

    def to_json(self) -> bytes:
        """Return bounded UTF-8 bytes suitable for a private checkpoint store."""

        return self._encoded

    def to_stage9_plan(self, *, busy: bool = False) -> dict[str, Any]:
        """Project the existing wire DTO using freshly sampled physical busy state."""

        if type(busy) is not bool:
            raise WorkflowStateError()
        plan = {**self.to_data()["plan"], "busy": busy}
        validate_work_result({"kind": "work.plan", "plan": plan})
        return plan

    @classmethod
    def from_json(cls, encoded: bytes) -> WorkflowState:
        """Reject duplicate keys, unsupported values, and oversized persisted frames."""

        if type(encoded) is not bytes or len(encoded) > MAX_WORKFLOW_STATE_BYTES:
            raise WorkflowStateError()

        def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise WorkflowStateError()
                result[key] = value
            return result

        try:
            return cls(json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique))
        except WorkflowStateError:
            raise
        except (UnicodeError, ValueError, RecursionError):
            raise WorkflowStateError() from None


def _admitted(plan: dict[str, Any], marker: str | None) -> str | None:
    """Treat an imported running write conservatively unless its admission is known."""

    if marker != "unknown":
        return marker
    return next((step["stepId"] for step in plan["steps"] if step["status"] == "running"
                 and step["call"]["toolId"] not in _READ_TOOLS), None)


def workflow_from_plan(
    plan: dict[str, Any], *, chat_id: str, project_id: str | None,
    selected_workspace_key: str, created_at: str, admitted_step_id: str | None = "unknown",
) -> WorkflowState:
    """Wrap a validated Stage 9 snapshot with immutable request/owner correlation.

    Physical ``busy`` is deliberately discarded: a new process cannot inherit
    another process's worker lease. The trusted runner bridge supplies its actual
    admitted-write marker (None explicitly means not admitted). The default treats
    an ambiguous running write as admitted, preferring manual review to a replay.
    """

    validate_work_result({"kind": "work.plan", "plan": plan})
    logical = {key: value for key, value in plan.items() if key != "busy"}
    cursor = {"node": "tool", "stepId": next((step["stepId"] for step in plan["steps"]
                                               if step["status"] in {"running", "waiting"}), None)}
    return WorkflowState({
        "version": WORKFLOW_STATE_VERSION, "workflowId": "workflow_" + uuid4().hex,
        "revision": 0, "request": {
            "requestId": "request_" + uuid4().hex, "chatId": chat_id, "projectId": project_id,
            "mode": "work", "workspaceKey": selected_workspace_key,
            "callsDigest": _digest([step["call"] for step in plan["steps"]]),
            "title": plan["title"], "createdAt": created_at,
        }, "plan": logical, "cursor": cursor, "admittedStepId": _admitted(plan, admitted_step_id),
        "result": _result(logical), "error": _error(logical, cursor), "checkpoint": None,
    })


def replace_workflow_plan(
    state: WorkflowState, plan: dict[str, Any], *, node: WorkflowNode = "tool",
    admitted_step_id: str | None = "unknown",
) -> WorkflowState:
    """Advance observed plan progress without changing IDs, calls, or settled results.

    This pure transition grants no permission and executes nothing. Terminal
    plans are absorbing; a cancelled admitted write may still publish its actual
    result once, matching the durable Stage 9 mutation service.
    """

    value = state.to_data()
    checkpoint = value["checkpoint"]
    if checkpoint is not None:
        raise WorkflowStateError("recovery_required")
    old = value["plan"]
    validate_work_result({"kind": "work.plan", "plan": plan})
    new = {key: item for key, item in plan.items() if key != "busy"}
    if (new["planId"] != old["planId"] or new["title"] != old["title"]
        or new["state"] not in _TRANSITIONS[old["state"]]
        or len(new["steps"]) != len(old["steps"])):
        raise WorkflowStateError("state_conflict")
    for before, after in zip(old["steps"], new["steps"], strict=True):
        if before["stepId"] != after["stepId"] or before["call"] != after["call"]:
            raise WorkflowStateError("state_conflict")
        # Fast native reads may skip observable intermediate states, but a
        # running/waiting step cannot move backwards to a replayable pending call.
        # Only explicit checkpoint preparation can reset a safe interrupted step.
        if after["status"] not in _STEP_TRANSITIONS[before["status"]]:
            raise WorkflowStateError("state_conflict")
        if before["status"] in _TERMINAL and before != after:
            raise WorkflowStateError("state_conflict")
        if old["state"] in _TERMINAL and before != after and before["stepId"] != value["admittedStepId"]:
            raise WorkflowStateError("state_conflict")
    cursor = {"node": node, "stepId": next((step["stepId"] for step in new["steps"]
                                            if step["status"] in {"running", "waiting"}), None)}
    value.update(revision=value["revision"] + 1, plan=new, cursor=cursor,
                 admittedStepId=_admitted(new, admitted_step_id), result=_result(new), error=_error(new, cursor))
    return WorkflowState(value)


def make_checkpoint(state: WorkflowState, *, saved_at: str) -> WorkflowState:
    """Prepare a nonexecuting recovery snapshot, scrubbing every live approval.

    Safe reads/previews become pending under a paused plan and require fresh
    authority/file checks. An admitted write stays running and explicitly
    uncertain; recovery must consult the mutation journal and create a new plan,
    never replay that operation from this snapshot.
    """

    value = state.to_data()
    admitted = value["admittedStepId"]
    old_checkpoint = value["checkpoint"]
    for step in value["plan"]["steps"]:
        step["approval"] = None
        if step["stepId"] != admitted and step["status"] in {"running", "waiting"}:
            step["status"] = "pending"
    if value["plan"]["state"] not in _TERMINAL or admitted is not None:
        value["plan"]["state"] = "paused"
    value["checkpoint"] = {
        "checkpointId": "checkpoint_" + uuid4().hex,
        "parentId": None if old_checkpoint is None else old_checkpoint["checkpointId"],
        "savedAt": saved_at, "disposition": "revalidate" if admitted is None else "uncertain_write",
        "requiresRevalidation": True,
    }
    value["revision"] += 1
    value["result"] = _result(value["plan"])
    value["error"] = (value["error"] if admitted is None else {
        "code": "recovery_required", "message": _ERRORS["recovery_required"],
        "node": "tool", "stepId": admitted,
    })
    return WorkflowState(value)
