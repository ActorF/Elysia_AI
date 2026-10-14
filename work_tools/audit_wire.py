"""Validate redacted Work history and verified relative recovery disclosures.

Audit is a presentation contract, not a new filesystem capability. No record
may expose original content, search text, native paths, or a restore command.
Undo identifiers refer to already recorded writes and require a separate
explicit decision plus the mutation service's current owner/hash checks.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, cast

from .contracts import ERROR_MESSAGES, MAX_TOOL_CONTENT_BYTES, ToolCall, ToolId


_TOOLS = {"list_directory", "read_text", "search_files", "metadata", "create", "edit", "move", "delete"}
_READ_TOOLS = {"list_directory", "read_text", "search_files", "metadata"}
_RESULTS = {"succeeded", "partial", "denied", "cancelled", "failed", "undone"}
_FIELDS = {"timestamp", "chatId", "projectId", "toolId", "parameters", "approver", "result", "error", "undoId", "recoverable", "recovery"}


def validate_audit_page(value: object) -> dict[str, Any]:
    """Require a bounded newest-first history without content or cross-owner paths."""

    if type(value) is not dict or set(value) != {"kind", "entries", "truncated"} or value["kind"] != "work.audit":
        raise ValueError("Work audit fields are invalid.")
    entries = value["entries"]
    if type(entries) is not list or len(entries) > 100 or type(value["truncated"]) is not bool:
        raise ValueError("Work audit page is invalid.")
    previous: datetime | None = None
    chat_id: str | None = None
    undo_ids: set[str] = set()
    for entry in entries:
        timestamp = _validate_entry(entry)
        if chat_id is not None and entry["chatId"] != chat_id:
            raise ValueError("Work history cannot mix Chat owners.")
        chat_id = entry["chatId"]
        if previous is not None and timestamp > previous:
            raise ValueError("Work history must be newest first.")
        previous = timestamp
        token = entry["undoId"]
        if token is not None:
            if token in undo_ids:
                raise ValueError("Work history Undo identifiers must be unique.")
            undo_ids.add(token)
    encoded = json.dumps({"entries": entries, "truncated": value["truncated"]}, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 512 * 1024:
        raise ValueError("Work history exceeds its byte limit.")
    return dict(value)


def _id(value: object, prefix: str, *, opaque: bool = False) -> None:
    suffix = r"[0-9a-f]{32}" if opaque else r"[A-Za-z0-9_-]+"
    if type(value) is not str or len(value) > 128 or re.fullmatch(prefix + "_" + suffix, value) is None:
        raise ValueError("Work audit identifier is invalid.")


def _hash(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Work audit digest is invalid.")


def _validate_entry(value: object) -> datetime:
    """Rebuild path validation from closed tools instead of trusting stored summaries."""

    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("Work audit entry fields are invalid.")
    _id(value["chatId"], "chat")
    if value["projectId"] is not None:
        _id(value["projectId"], "project")
    raw_time = value["timestamp"]
    if type(raw_time) is not str or len(raw_time) > 128 or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", raw_time) is None:
        raise ValueError("Work audit timestamp is invalid.")
    timestamp = datetime.fromisoformat(raw_time)
    tool_id = value["toolId"]
    if type(tool_id) is not str or tool_id not in _TOOLS:
        raise ValueError("Work audit tool is invalid.")
    params = value["parameters"]
    expected = {"path"}
    if tool_id in {"create", "edit"}:
        expected |= {"contentBytes", "contentSha256"}
    elif tool_id == "move":
        expected.add("destination")
    elif tool_id == "search_files":
        expected |= {"queryCharacters", "querySha256"}
    if type(params) is not dict or set(params) != expected:
        raise ValueError("Work audit parameters are invalid.")
    # Content and literal search text are deliberately absent from history.
    # Empty placeholder text only asks ToolCall to validate its path contract.
    ToolCall(cast(ToolId, tool_id), params["path"], content="" if tool_id in {"create", "edit"} else None,
             destination=params.get("destination"), query="x" if tool_id == "search_files" else None)
    if "contentBytes" in params:
        if type(params["contentBytes"]) is not int or not 0 <= params["contentBytes"] <= MAX_TOOL_CONTENT_BYTES:
            raise ValueError("Work audit content size is invalid.")
        _hash(params["contentSha256"])
    if "queryCharacters" in params:
        if type(params["queryCharacters"]) is not int or not 1 <= params["queryCharacters"] <= 256:
            raise ValueError("Work audit query size is invalid.")
        _hash(params["querySha256"])
    if value["approver"] != "user" or type(value["result"]) is not str or value["result"] not in _RESULTS:
        raise ValueError("Work audit decision is invalid.")
    if value["error"] is not None and (type(value["error"]) is not str or value["error"] not in ERROR_MESSAGES):
        raise ValueError("Work audit error is invalid.")
    if type(value["recoverable"]) is not bool:
        raise ValueError("Work audit recovery state is invalid.")
    # An interrupted Undo retains the original forward result (or the completed
    # inverse result when its final save failed). Its separate recovery/error
    # fields describe that unsettled state, so only settled records use these
    # stricter result/error combinations.
    if not value["recoverable"]:
        if value["result"] in {"succeeded", "undone"} and value["error"] is not None:
            raise ValueError("Settled Work success cannot contain an error.")
        if value["result"] in {"denied", "cancelled", "failed"} and value["error"] is None:
            raise ValueError("Settled Work failures require a fixed error.")
    undo_id = value["undoId"]
    if undo_id is not None:
        _id(undo_id, "undo", opaque=True)
        if tool_id in _READ_TOOLS or value["result"] != "succeeded" or value["recoverable"]:
            raise ValueError("Work audit Undo is unavailable for this outcome.")
    recovery = value["recovery"]
    if value["recoverable"]:
        if tool_id in _READ_TOOLS or type(recovery) is not dict or set(recovery) != {"recoveryId", "directory", "backupPath", "beforeSha256"}:
            raise ValueError("Work audit recovery fields are invalid.")
        _id(recovery["recoveryId"], "undo", opaque=True)
        directory = "workspace/work/transactions/" + recovery["recoveryId"]
        if recovery["directory"] != directory:
            raise ValueError("Work recovery directory is invalid.")
        if recovery["backupPath"] is None:
            if recovery["beforeSha256"] is not None:
                raise ValueError("A recovery digest requires its verified copy.")
        else:
            if tool_id == "create" or recovery["backupPath"] != directory + "/before.bin":
                raise ValueError("Work recovery copy is invalid.")
            _hash(recovery["beforeSha256"])
    elif recovery is not None:
        raise ValueError("Settled Work history cannot expose interrupted recovery.")
    if tool_id in _READ_TOOLS and value["result"] == "undone":
        raise ValueError("Read tools cannot have an Undo result.")
    return timestamp
