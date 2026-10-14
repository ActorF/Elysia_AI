"""Keep Work audit history redacted and relative recovery claims verifiable."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from work_tools.audit_wire import validate_audit_page
from work_tools.contracts import ToolCall, ToolResult
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator


def _history() -> dict[str, Any]:
    return {"kind": "work.audit", "entries": [{
        "timestamp": "2026-10-14T16:00:00+00:00", "chatId": "chat_test", "projectId": None,
        "toolId": "edit", "parameters": {"path": "notes.txt", "contentBytes": 4, "contentSha256": "a" * 64},
        "approver": "user", "result": "succeeded", "error": None,
        "undoId": "undo_" + "1" * 32, "recoverable": False, "recovery": None,
    }], "truncated": False}


@pytest.mark.parametrize("leak", ["content", "native_path", "search_query", "raw_error"])
def test_audit_rejects_original_contents_queries_paths_and_exception_text(leak: str) -> None:
    """Accept only closed summaries when private journal data crosses into the Renderer."""

    state = _history()
    assert validate_audit_page(state) == state
    entry = state["entries"][0]
    if leak == "content":
        entry["parameters"]["content"] = "private source text"
    elif leak == "native_path":
        entry["parameters"]["path"] = "C:/Users/private/notes.txt"
    elif leak == "search_query":
        entry.update(toolId="search_files", undoId=None)
        entry["parameters"] = {"path": "", "query": "private search text"}
    else:
        entry["error"] = "File C:/Users/private/notes.txt failed"
    with pytest.raises(ValueError):
        validate_audit_page(state)


def test_recovery_disclosure_requires_exact_relative_copy_and_matching_digest_pair() -> None:
    """A journal folder alone must never imply that a restoration copy was verified."""

    state = _history()
    token = "undo_" + "1" * 32
    directory = "workspace/work/transactions/" + token
    entry = state["entries"][0]
    entry.update(result="failed", error="io_error", undoId=None, recoverable=True,
                 recovery={"recoveryId": token, "directory": directory, "backupPath": None, "beforeSha256": None})
    assert validate_audit_page(state) == state
    entry["recovery"].update(backupPath=directory + "/before.bin", beforeSha256="b" * 64)
    assert validate_audit_page(state) == state
    for mutation in ({"backupPath": "../other/before.bin"}, {"backupPath": None}, {"beforeSha256": None}):
        changed = deepcopy(state)
        changed["entries"][0]["recovery"].update(mutation)
        with pytest.raises(ValueError):
            validate_audit_page(changed)


def test_actual_write_history_matches_redacted_audit_contract(tmp_path: Path) -> None:
    """Validate service-generated history before and after a conditional Undo."""

    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "notes.txt").write_text("old private text", encoding="utf-8")
    boundary = WorkspaceBoundary(selected)
    writer = WriteCoordinator(tmp_path / "private")
    approval = writer.preview("chat_test", None, boundary, ToolCall("edit", "notes.txt", content="new private text"))
    result = writer.confirm("chat_test", None, boundary, approval["approvalId"], True)
    assert result.status == "succeeded"
    history = {"kind": "work.audit", **writer.audit_page("chat_test")}
    assert validate_audit_page(history) == history
    assert "private text" not in str(history)
    assert writer.undo("chat_test", None, boundary, history["entries"][0]["undoId"]).status == "succeeded"
    undone = {"kind": "work.audit", **writer.audit_page("chat_test")}
    assert validate_audit_page(undone) == undone
    assert undone["entries"][0]["result"] == "undone" and undone["entries"][0]["undoId"] is None


def test_actual_read_history_does_not_publish_body_or_literal_query(tmp_path: Path) -> None:
    """Require search and read audit summaries to survive restart without sensitive values."""

    writer = WriteCoordinator(tmp_path / "private")
    read = ToolCall("read_text", "notes.txt")
    writer.record_read("chat_test", None, read, ToolResult("succeeded", {"path": "notes.txt", "content": "private body", "sizeBytes": 12}))
    query = "private needle"
    writer.record_read("chat_test", None, ToolCall("search_files", "", query=query),
                       ToolResult("succeeded", {"matches": [], "truncated": False}))
    history = {"kind": "work.audit", **WriteCoordinator(tmp_path / "private").audit_page("chat_test")}
    assert validate_audit_page(history) == history
    assert "private body" not in str(history) and query not in str(history)
    assert {entry["toolId"] for entry in history["entries"]} == {"read_text", "search_files"}


def test_audit_page_cannot_mix_independent_chat_owners() -> None:
    """Reject cross-Chat record injection even before request-specific correlation."""

    history = _history()
    other = deepcopy(history["entries"][0])
    other.update(chatId="chat_other", undoId="undo_" + "2" * 32)
    history["entries"].append(other)
    with pytest.raises(ValueError):
        validate_audit_page(history)
