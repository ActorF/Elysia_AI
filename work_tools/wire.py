"""Validate the small Work RPC surface before filesystem capabilities are used."""

from __future__ import annotations

from typing import Any
import re


WORK_METHODS = ("work.workspace.get", "work.workspace.bind")


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
    """Validate canonical Workspace scope without treating it as a Chat reply."""

    if not isinstance(value, dict) or set(value) != {"kind", "chatId", "workspacePath"} or value["kind"] != "work.workspace":
        raise ValueError("Work response fields are invalid.")
    validate_work_params("work.workspace.bind", {"chatId": value["chatId"], "workspacePath": value["workspacePath"]})
    return dict(value)
