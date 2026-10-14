"""Validate the small Work RPC surface before filesystem capabilities are used."""

from __future__ import annotations

from typing import Any
import json
import re
from datetime import datetime

from .contracts import TOOL_CATALOG, descriptor_to_data, ToolResult, ToolProtocolError, validate_plan_title, validate_tool_call, validate_tool_result
from .audit_wire import validate_audit_page


_METHOD_FIELDS = {
    "work.workspace.get": {"chatId"},
    "work.workspace.bind": {"chatId", "workspacePath"},
    "work.tools.list": {"chatId"},
    "work.tool.run": {"chatId", "call"},
    "work.tool.preview": {"chatId", "call"},
    "work.tool.confirm": {"chatId", "approvalId", "granted"},
    "work.plan.get": {"chatId"},
    "work.plan.create": {"chatId", "title", "calls"},
    "work.plan.control": {"chatId", "planId", "action"},
    "work.plan.confirm": {"chatId", "planId", "approvalId", "granted"},
    "work.plan.revise": {"chatId", "planId", "approvalId", "call"},
    "work.audit.list": {"chatId"},
    "work.undo": {"chatId", "undoId", "confirmed"},
}
WORK_METHODS = tuple(_METHOD_FIELDS)
_PLAN_STATES = {"pending", "running", "paused", "waiting", "succeeded", "failed", "cancelled"}
_STEP_STATES = _PLAN_STATES - {"paused"}


def validate_work_params(method: str, value: object) -> dict[str, Any]:
    """Require exact method fields, bounded IDs, and native-selected bind paths.

    Electron Main alone supplies workspacePath on bind. This validation is a
    second boundary for the authenticated process protocol, not a permission
    grant to accept raw renderer paths.
    """

    fields = _METHOD_FIELDS.get(method)
    supplied = set(value) if type(value) is dict else set()
    if method == "work.plan.create" and isinstance(value, dict) and "useProjectKnowledge" in supplied:
        fields = {*_METHOD_FIELDS[method], "useProjectKnowledge"}
        if type(value["useProjectKnowledge"]) is not bool:
            raise ValueError("Work knowledge choice must be explicit.")
    if fields is None or not isinstance(value, dict) or supplied != fields:
        raise ValueError("Work request fields are invalid.")
    chat_id = value["chatId"]
    if not isinstance(chat_id, str) or re.fullmatch(r"chat_[A-Za-z0-9_-]{1,123}", chat_id) is None:
        raise ValueError("Work requires a valid Chat ID.")
    if method == "work.workspace.bind":
        root = value["workspacePath"]
        if root is not None and (not isinstance(root, str) or not root.strip() or len(root) > 32767 or any(ord(char) < 32 or ord(char) == 127 for char in root)):
            raise ValueError("Workspace selection is invalid.")
    elif method in {"work.tool.run", "work.tool.preview"}:
        validate_tool_call(value["call"])
    elif method in {"work.tool.confirm", "work.plan.confirm", "work.plan.revise"}:
        if method == "work.plan.revise":
            validate_tool_call(value["call"])
            _bounded_json(value, 768 * 1024)
        if type(value["approvalId"]) is not str or re.fullmatch(r"approval_[0-9a-f]{32}", value["approvalId"]) is None or (method != "work.plan.revise" and type(value["granted"]) is not bool):
            raise ValueError("Work confirmation is invalid.")
    elif method == "work.plan.create":
        validate_plan_title(value["title"])
        if type(value["calls"]) is not list or not 1 <= len(value["calls"]) <= 16:
            raise ValueError("Work plan steps are invalid.")
        for call in value["calls"]:
            validate_tool_call(call)
        _bounded_json(value, 768 * 1024)
    elif method == "work.plan.control":
        if type(value["action"]) is not str or value["action"] not in {"start", "pause", "resume", "cancel", "next"}:
            raise ValueError("Work plan action is invalid.")
    elif method == "work.undo":
        _validate_id(value["undoId"], "undo")
        if value["confirmed"] is not True:
            raise ValueError("Work Undo requires explicit confirmation.")
    if method in {"work.plan.control", "work.plan.confirm", "work.plan.revise"}:
        _validate_id(value["planId"], "plan")
    return dict(value)


def validate_work_result(value: object) -> dict[str, Any]:
    """Validate scope, trusted tools, explicit plans, or redacted bounded history."""

    if isinstance(value, dict) and value.get("kind") == "work.audit":
        return validate_audit_page(value)
    if isinstance(value, dict) and value.get("kind") == "work.plan":
        if not {"kind", "plan"} <= set(value) <= {"kind", "plan", "workflow", "recovery"}:
            raise ValueError("Work plan result fields are invalid.")
        _validate_plan(value["plan"])
        if "workflow" in value:
            _validate_workflow_observation(value["workflow"], value["plan"])
        if "recovery" in value:
            notice = value["recovery"]
            if type(notice) is not dict or set(notice) != {"status", "planId"}:
                raise ValueError("Work recovery notice is invalid.")
            _validate_id(notice["planId"], "plan")
            if type(notice["status"]) is not str or notice["status"] not in {"restored", "uncertain_write", "storage_failed"}:
                raise ValueError("Work recovery state is invalid.")
            if notice["status"] == "uncertain_write":
                if value["plan"] is not None:
                    raise ValueError("An uncertain write cannot own a runnable plan.")
            elif value["plan"] is None or notice["planId"] != value["plan"]["planId"]:
                raise ValueError("Work recovery changed its plan.")
        return dict(value)
    if isinstance(value, dict) and value.get("kind") == "work.approval":
        if set(value) != {"kind", "approval"}:
            raise ValueError("Work approval result fields are invalid.")
        _validate_approval(value["approval"])
        return dict(value)
    if isinstance(value, dict) and value.get("kind") == "work.result":
        if set(value) != {"kind", "result"}:
            raise ValueError("Work result fields are invalid.")
        _validate_outcome(value["result"])
        return dict(value)
    if isinstance(value, dict) and value.get("kind") == "work.tools":
        if set(value) != {"kind", "tools"} or type(value["tools"]) is not list or len(value["tools"]) > 8:
            raise ValueError("Work catalog fields are invalid.")
        trusted = {descriptor.tool_id: descriptor_to_data(descriptor) for descriptor in TOOL_CATALOG}
        seen: set[str] = set()
        for entry in value["tools"]:
            if type(entry) is not dict or type(entry.get("toolId")) is not str:
                raise ValueError("Work catalog entry is invalid.")
            tool_id = entry["toolId"]
            if tool_id in seen or trusted.get(tool_id) != entry:
                raise ValueError("Work catalog must contain unique trusted entries.")
            seen.add(tool_id)
        return dict(value)
    if not isinstance(value, dict) or set(value) != {"kind", "chatId", "workspacePath"} or value["kind"] != "work.workspace":
        raise ValueError("Work response fields are invalid.")
    validate_work_params("work.workspace.bind", {"chatId": value["chatId"], "workspacePath": value["workspacePath"]})
    return dict(value)


def _validate_id(value: object, prefix: str) -> None:
    if type(value) is not str or re.fullmatch(prefix + r"_[0-9a-f]{32}", value) is None:
        raise ValueError("Work identifier is invalid.")


def _validate_workflow_observation(value: object, plan: dict[str, Any] | None) -> None:
    """Constrain model advice to a nonauthorizing, owner-correlated text record."""

    if type(value) is not dict or set(value) != {"planId", "node", "memoryCount", "ragCount",
                                                "memoryUnavailable", "ragUnavailable", "advice"}:
        raise ValueError("Work observation fields are invalid.")
    if plan is None or value["planId"] != plan["planId"]:
        raise ValueError("Work observation changed its plan.")
    if type(value["node"]) is not str or value["node"] not in {"idle", "admission", "memory", "rag", "brain", "approval", "tool", "verification", "end"}:
        raise ValueError("Work observation node is invalid.")
    for key in ("memoryCount", "ragCount"):
        if type(value[key]) is not int or not 0 <= value[key] <= 8:
            raise ValueError("Work observation count is invalid.")
    for key in ("memoryUnavailable", "ragUnavailable"):
        if type(value[key]) is not bool:
            raise ValueError("Work observation availability is invalid.")
    advice = value["advice"]
    if advice is not None and (type(advice) is not dict or set(advice) != {"status", "text"}
        or type(advice["status"]) is not str or advice["status"] not in {"ready", "unavailable"}
        or type(advice["text"]) is not str or "\x00" in advice["text"] or len(advice["text"].encode("utf-8")) > 8192):
        raise ValueError("Work advice is invalid.")


def _bounded_json(value: object, maximum: int) -> None:
    """Apply the same UTF-8 snapshot budget as the worker before transport."""

    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > maximum:
        raise ValueError("Work snapshot exceeds its size limit.")


def _validate_plan(value: object) -> None:
    """Correlate each result and permission with its immutable explicit step.

    Physical busy state is sampled after the runner snapshot. It may change
    between those reads, so it is validated as a Boolean rather than inferred
    from logical state; a cancelled native write can still own physical I/O.
    """

    if value is None:
        return
    if type(value) is not dict or set(value) != {"planId", "title", "state", "steps", "busy"}:
        raise ValueError("Work plan fields are invalid.")
    _validate_id(value["planId"], "plan")
    validate_plan_title(value["title"])
    if type(value["state"]) is not str or value["state"] not in _PLAN_STATES or type(value["busy"]) is not bool:
        raise ValueError("Work plan state is invalid.")
    if type(value["steps"]) is not list or not 1 <= len(value["steps"]) <= 16:
        raise ValueError("Work plan steps are invalid.")
    seen: set[str] = set()
    active_steps = 0
    for step in value["steps"]:
        if type(step) is not dict or set(step) != {"stepId", "call", "status", "result", "approval"}:
            raise ValueError("Work plan step fields are invalid.")
        _validate_id(step["stepId"], "step")
        if step["stepId"] in seen:
            raise ValueError("Work plan step identifiers must be unique.")
        seen.add(step["stepId"])
        call = validate_tool_call(step["call"])
        if type(step["status"]) is not str or step["status"] not in _STEP_STATES:
            raise ValueError("Work plan step state is invalid.")
        if step["status"] in {"running", "waiting"}:
            active_steps += 1
        result = step["result"]
        if result is not None:
            outcome = _validate_outcome(result)
            validate_tool_result(call.tool_id, outcome)
            data = outcome.data
            if data is not None:
                if call.tool_id in {"list_directory", "search_files"}:
                    entries = data["entries"] if call.tool_id == "list_directory" else data["matches"]
                    prefix = call.path + "/" if call.path else ""
                    if any(not entry["path"].startswith(prefix) or entry["path"] == call.path for entry in entries):
                        raise ValueError("Work plan result escaped its requested prefix.")
                elif data["path"] != call.path or (call.tool_id == "move" and data["destination"] != call.destination):
                    raise ValueError("Work plan result changed its requested path.")
        approval = step["approval"]
        if approval is not None:
            _validate_approval(approval)
            if step["status"] != "waiting" or result is not None or approval["call"] != call.to_data():
                raise ValueError("Work plan approval changed its waiting step.")
        elif step["status"] == "waiting":
            raise ValueError("Waiting Work steps require an approval.")
        if step["status"] in {"pending", "running", "waiting"} and result is not None:
            raise ValueError("Active Work steps cannot publish terminal results.")
        if step["status"] in {"succeeded", "failed", "cancelled"} and result is None:
            raise ValueError("Settled Work steps require their result.")
        expected = {"succeeded": {"succeeded", "partial"}, "failed": {"denied", "failed"}, "cancelled": {"cancelled"}}
        if step["status"] in expected and result["status"] not in expected[step["status"]]:
            raise ValueError("Work step state does not match its result.")
    if active_steps > 1:
        raise ValueError("Work plan cannot own multiple active steps.")
    _bounded_json(value, 768 * 1024)


def _validate_outcome(value: object) -> ToolResult:
    """Enforce fixed errors and one known typed data shape in generic response envelopes."""

    if type(value) is not dict or set(value) != {"status", "data", "error"}:
        raise ValueError("Work tool outcome fields are invalid.")
    error = value["error"]
    if error is not None and (type(error) is not dict or set(error) != {"code", "message"}):
        raise ValueError("Work error fields are invalid.")
    typed = ToolResult(value["status"], value["data"], None if error is None else error["code"])
    if typed.to_data() != value:
        raise ValueError("Work error message is invalid.")
    for descriptor in TOOL_CATALOG:
        try:
            validate_tool_result(descriptor.tool_id, typed)
            return typed
        except ToolProtocolError:
            continue
    raise ValueError("Work tool data does not match a known result shape.")


def _validate_approval(value: object) -> None:
    """Check the exact action, fixed risk, safe preview, and UTC lease disclosure."""

    if type(value) is not dict or set(value) != {"approvalId", "call", "risk", "preview", "expiresAt"}:
        raise ValueError("Work approval fields are invalid.")
    if type(value["approvalId"]) is not str or re.fullmatch(r"approval_[0-9a-f]{32}", value["approvalId"]) is None:
        raise ValueError("Work approval identifier is invalid.")
    call = validate_tool_call(value["call"])
    descriptor = next(item for item in TOOL_CATALOG if item.tool_id == call.tool_id)
    if descriptor.risk == "low" or value["risk"] != descriptor.risk:
        raise ValueError("Work approval risk is invalid.")
    if type(value["preview"]) is not str or len(value["preview"].encode("utf-8")) > 32768 or "\x00" in value["preview"]:
        raise ValueError("Work approval preview is invalid.")
    if type(value["expiresAt"]) is not str or len(value["expiresAt"]) > 128:
        raise ValueError("Work approval expiry is invalid.")
    expires = datetime.fromisoformat(value["expiresAt"])
    offset = expires.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("Work approval expiry must use UTC.")
