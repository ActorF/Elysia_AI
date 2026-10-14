"""Validate the small Work RPC surface before filesystem capabilities are used."""

from __future__ import annotations

from typing import Any
import re

from .contracts import TOOL_CATALOG, descriptor_to_data


WORK_METHODS = ("work.workspace.get", "work.workspace.bind", "work.tools.list")


def validate_work_params(method: str, value: object) -> dict[str, Any]:
    """Require exact method fields, bounded IDs, and native-selected bind paths.

    Electron Main alone supplies workspacePath on bind. This validation is a
    second boundary for the authenticated process protocol, not a permission
    grant to accept raw renderer paths.
    """

    fields = {"chatId", "workspacePath"} if method == "work.workspace.bind" else {"chatId"}
    if method not in WORK_METHODS or not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Work request fields are invalid.")
    chat_id = value["chatId"]
    if not isinstance(chat_id, str) or re.fullmatch(r"chat_[A-Za-z0-9_-]{1,123}", chat_id) is None:
        raise ValueError("Work requires a valid Chat ID.")
    if method == "work.workspace.bind":
        root = value["workspacePath"]
        if root is not None and (not isinstance(root, str) or not root.strip() or len(root) > 32767 or any(ord(char) < 32 or ord(char) == 127 for char in root)):
            raise ValueError("Workspace selection is invalid.")
    return dict(value)


def validate_work_result(value: object) -> dict[str, Any]:
    """Validate scope or the trusted catalog before publishing a Work response."""

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
