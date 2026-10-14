"""Validate the small Work RPC surface before filesystem capabilities are used."""

from __future__ import annotations

from typing import Any
import re
from datetime import datetime

from .contracts import TOOL_CATALOG, descriptor_to_data, ToolResult, ToolProtocolError, validate_tool_call, validate_tool_result


WORK_METHODS = ("work.workspace.get", "work.workspace.bind", "work.tools.list", "work.tool.run", "work.tool.preview")


def validate_work_params(method: str, value: object) -> dict[str, Any]:
    """Require exact method fields, bounded IDs, and native-selected bind paths.

    Electron Main alone supplies workspacePath on bind. This validation is a
    second boundary for the authenticated process protocol, not a permission
    grant to accept raw renderer paths.
    """

    fields = {"chatId", "workspacePath"} if method == "work.workspace.bind" else {"chatId", "call"} if method in {"work.tool.run", "work.tool.preview"} else {"chatId"}
    if method not in WORK_METHODS or not isinstance(value, dict) or set(value) != fields:
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
    return dict(value)


def validate_work_result(value: object) -> dict[str, Any]:
    """Validate scope, a trusted catalog, or a bounded typed tool outcome."""

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
