"""Verify canonical scope, real file approval, retirement, and Work plan routing."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any

import pytest

import desktop_work_plans as plans_module
from chats import ChatSession, ProjectId, create_chat_session
from desktop_work_plans import DesktopWorkPlans
from work_tools.contracts import ToolCall, ToolProtocolError, ToolResult
from work_tools.executor import WorkToolExecutor
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 3.0
    tick = Event()
    while not predicate():
        assert monotonic() < deadline, "Work plan integration did not settle."
        tick.wait(0.002)


def _setup(tmp_path: Path) -> tuple[DesktopWorkPlans, list[ChatSession], dict[str, Path], WriteCoordinator]:
    chat = create_chat_session(title="Work", mode="work", model_name="test-model")
    selected = tmp_path / "selected"
    selected.mkdir()
    current = [chat]
    scopes = {str(chat.chat_id): selected}
    writer = WriteCoordinator(tmp_path / "private" / "work")
    manager = DesktopWorkPlans(
        lambda: current[0], lambda chat_id: WorkspaceBoundary(scopes[chat_id]), lambda: writer,
    )
    return manager, current, scopes, writer


def _plan(manager: DesktopWorkPlans, chat_id: str) -> dict[str, Any]:
    plan = manager.get(chat_id)
    assert plan is not None
    return plan


def test_real_read_edit_verify_plan_requires_confirmation_and_preserves_safe_audit(tmp_path: Path) -> None:
    """Perform the Stage 9 read-to-preview-to-confirm-to-write-to-verify path."""

    manager, current, scopes, writer = _setup(tmp_path)
    chat = current[0]
    chat_id = str(chat.chat_id)
    note = scopes[chat_id] / "notes.txt"
    note.write_bytes(b"original\n")
    created = manager.create(chat, "Update notes", [
        ToolCall("read_text", "notes.txt"), ToolCall("edit", "notes.txt", content="updated\n"),
        ToolCall("read_text", "notes.txt"),
    ])
    assert set(created) == {"planId", "title", "state", "steps", "busy"}
    assert created["busy"] is False and manager.has_live_plan(chat_id)
    manager.control(chat_id, created["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = _plan(manager, chat_id)
    assert waiting["state"] == "waiting" and waiting["busy"] is False
    assert note.read_bytes() == b"original\n"
    approval = waiting["steps"][1]["approval"]
    assert manager.owns_approval(chat_id, approval["approvalId"])
    assert "-original" in approval["preview"] and "+updated" in approval["preview"]
    manager.confirm(chat_id, created["planId"], approval["approvalId"], True)
    _wait(lambda: not manager.is_busy())
    final = _plan(manager, chat_id)
    assert final["state"] == "succeeded" and final["busy"] is False
    assert note.read_bytes() == b"updated\n"
    assert final["steps"][2]["result"]["data"]["content"] == "updated\n"
    history = writer.audit(chat_id)
    writes = [entry for entry in history if entry["toolId"] == "edit"]
    assert len(writes) == 1 and writes[0]["result"] == "succeeded"
    assert len([entry for entry in history if entry["toolId"] == "read_text"]) == 2
    assert "updated" not in str(history) and "original" not in str(history)


@pytest.mark.parametrize("change", ["mode", "project", "root", "chat"])
def test_each_worker_reacquires_scope_before_the_next_native_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """A later step cannot inherit a changed Chat, Project, mode, or selected root."""

    manager, current, scopes, _writer = _setup(tmp_path)
    original = current[0]
    chat_id = str(original.chat_id)
    note = scopes[chat_id] / "notes.txt"
    note.write_text("original", encoding="utf-8")
    actual_run = WorkToolExecutor.run

    def _switch_after_read(self: WorkToolExecutor, call: ToolCall, **kwargs: Any) -> ToolResult:
        result = actual_run(self, call, **kwargs)
        if change == "mode":
            current[0] = replace(current[0], mode="chat")
        elif change == "project":
            current[0] = replace(current[0], project_id=ProjectId("project_changed"))
        elif change == "root":
            replacement = tmp_path / "replacement"
            replacement.mkdir()
            (replacement / "notes.txt").write_text("different", encoding="utf-8")
            scopes[chat_id] = replacement
        else:
            current[0] = create_chat_session(title="Other", mode="work", model_name="test-model")
        return result

    monkeypatch.setattr(plans_module.WorkToolExecutor, "run", _switch_after_read)
    created = manager.create(original, "Stale capability", [
        ToolCall("read_text", "notes.txt"), ToolCall("edit", "notes.txt", content="unapproved"),
    ])
    manager.control(chat_id, created["planId"], "start")
    _wait(lambda: not manager.is_busy())
    current[0] = original
    scopes[chat_id] = note.parent
    failed = _plan(manager, chat_id)
    assert failed["state"] == "failed"
    assert failed["steps"][0]["result"]["status"] == "denied"
    assert failed["steps"][1]["approval"] is None
    assert note.read_text("utf-8") == "original"


def test_confirmation_refuses_a_rebound_workspace_without_consuming_a_write(tmp_path: Path) -> None:
    """An approval belongs to its captured root rather than any later valid selection."""

    manager, current, scopes, _writer = _setup(tmp_path)
    chat_id = str(current[0].chat_id)
    original = scopes[chat_id]
    (original / "notes.txt").write_text("old", encoding="utf-8")
    created = manager.create(current[0], "Edit", [ToolCall("edit", "notes.txt", content="new")])
    manager.control(chat_id, created["planId"], "start")
    _wait(lambda: not manager.is_busy())
    token = _plan(manager, chat_id)["steps"][0]["approval"]["approvalId"]
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "notes.txt").write_text("other", encoding="utf-8")
    scopes[chat_id] = replacement
    with pytest.raises(ToolProtocolError, match="changed"):
        manager.confirm(chat_id, created["planId"], token, True)
    assert (original / "notes.txt").read_text("utf-8") == "old"
    assert (replacement / "notes.txt").read_text("utf-8") == "other"


def test_cancelled_plan_approval_cannot_execute_through_the_generic_write_route(tmp_path: Path) -> None:
    """Revoke the writer capability as well as rejecting the plan UI's stale token."""

    manager, current, scopes, writer = _setup(tmp_path)
    chat_id = str(current[0].chat_id)
    selected = scopes[chat_id]
    (selected / "notes.txt").write_text("original", encoding="utf-8")
    created = manager.create(current[0], "Cancel approval", [ToolCall("edit", "notes.txt", content="new")])
    manager.control(chat_id, created["planId"], "start")
    _wait(lambda: not manager.is_busy())
    token = _plan(manager, chat_id)["steps"][0]["approval"]["approvalId"]
    manager.control(chat_id, created["planId"], "cancel")
    assert manager.owns_approval(chat_id, token)
    with pytest.raises(ValueError):
        manager.confirm(chat_id, created["planId"], token, True)
    direct = writer.confirm(chat_id, None, WorkspaceBoundary(selected), token, True)
    assert direct.status == "denied" and direct.error_code == "permission_denied"
    assert (selected / "notes.txt").read_text("utf-8") == "original"


def test_invalidation_retains_cancelled_snapshot_busy_until_native_read_drains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retire promptly without losing physical ownership or publishing late file contents."""

    manager, current, _scopes, _writer = _setup(tmp_path)
    chat_id = str(current[0].chat_id)
    entered = Event()
    release = Event()

    def _blocking_read(_self: WorkToolExecutor, _call: ToolCall, **_kwargs: Any) -> ToolResult:
        entered.set()
        assert release.wait(3.0)
        return ToolResult("succeeded", {"path": "notes.txt", "content": "late private result", "sizeBytes": 19})

    monkeypatch.setattr(plans_module.WorkToolExecutor, "run", _blocking_read)
    created = manager.create(current[0], "Read", [ToolCall("read_text", "notes.txt")])
    manager.control(chat_id, created["planId"], "start")
    assert entered.wait(2.0)
    manager.invalidate(chat_id)
    retired = _plan(manager, chat_id)
    assert retired["state"] == "cancelled" and retired["busy"] is True
    assert manager.is_busy() and not manager.has_live_plan(chat_id)
    with pytest.raises(ToolProtocolError):
        manager.create(current[0], "Too early", [ToolCall("metadata", "")])
    release.set()
    _wait(lambda: not manager.is_busy())
    final = _plan(manager, chat_id)
    assert final == {**retired, "busy": False}
    assert "late private result" not in str(final)


def test_shutdown_closes_admission_and_tracks_retired_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close all owners while retaining the physical drain gate for a blocked callback."""

    manager, current, _scopes, _writer = _setup(tmp_path)
    chat_id = str(current[0].chat_id)
    entered = Event()
    release = Event()

    def _blocking_read(_self: WorkToolExecutor, _call: ToolCall, **_kwargs: Any) -> ToolResult:
        entered.set()
        assert release.wait(3.0)
        return ToolResult("failed", error_code="io_error")

    monkeypatch.setattr(plans_module.WorkToolExecutor, "run", _blocking_read)
    created = manager.create(current[0], "Shutdown", [ToolCall("metadata", "")])
    manager.control(chat_id, created["planId"], "start")
    assert entered.wait(2.0)
    assert manager.shutdown() is False
    with pytest.raises(ToolProtocolError):
        manager.create(current[0], "Closed", [ToolCall("metadata", "")])
    with pytest.raises(ToolProtocolError):
        manager.control(chat_id, created["planId"], "resume")
    release.set()
    _wait(lambda: not manager.is_busy())
    assert manager.shutdown() is True


def test_other_chat_cannot_read_or_confirm_the_old_owner_plan(tmp_path: Path) -> None:
    """Keep plan snapshots and permission tokens associated with their exact Chat."""

    manager, current, scopes, _writer = _setup(tmp_path)
    first = current[0]
    first_id = str(first.chat_id)
    plan = manager.create(first, "First", [ToolCall("create", "first.txt", content="first")])
    manager.control(first_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    token = _plan(manager, first_id)["steps"][0]["approval"]["approvalId"]
    second = create_chat_session(title="Second", mode="work", model_name="test-model")
    current[0] = second
    second_id = str(second.chat_id)
    selected = tmp_path / "second"
    selected.mkdir()
    scopes[second_id] = selected
    assert manager.get(second_id) is None and not manager.owns_approval(second_id, token)
    with pytest.raises(ToolProtocolError):
        manager.get(first_id)
    with pytest.raises(ToolProtocolError):
        manager.confirm(first_id, plan["planId"], token, True)
    new = manager.create(second, "Second", [ToolCall("metadata", "")])
    assert new["planId"] != plan["planId"]
    assert not (scopes[first_id] / "first.txt").exists()


def test_late_preview_permission_is_revoked_when_owner_invalidation_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoke an approval created after cancellation raced with preview's file read."""

    manager, current, scopes, writer = _setup(tmp_path)
    chat_id = str(current[0].chat_id)
    entered = Event()
    release = Event()
    tokens: list[str] = []
    original = writer.preview

    def _late_preview(*args: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(3.0)
        result = original(*args)
        tokens.append(result["approvalId"])
        return result

    monkeypatch.setattr(writer, "preview", _late_preview)
    plan = manager.create(current[0], "Late preview", [ToolCall("create", "new.txt", content="new")])
    manager.control(chat_id, plan["planId"], "start")
    assert entered.wait(2.0)
    manager.invalidate(chat_id)
    release.set()
    _wait(lambda: not manager.is_busy())
    assert len(tokens) == 1
    denied = writer.confirm(chat_id, None, WorkspaceBoundary(scopes[chat_id]), tokens[0], True)
    assert denied.status == "denied" and not (scopes[chat_id] / "new.txt").exists()


def test_cancel_control_revokes_permission_created_after_its_initial_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview finishing after cancel must not recreate a hidden generic-write permission."""

    manager, current, scopes, writer = _setup(tmp_path)
    chat_id = str(current[0].chat_id)
    entered = Event()
    release = Event()
    tokens: list[str] = []
    original = writer.preview

    def _late_preview(*args: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(3.0)
        approval = original(*args)
        tokens.append(approval["approvalId"])
        return approval

    monkeypatch.setattr(writer, "preview", _late_preview)
    plan = manager.create(current[0], "Cancel preview", [ToolCall("create", "new.txt", content="new")])
    manager.control(chat_id, plan["planId"], "start")
    assert entered.wait(2.0)
    manager.control(chat_id, plan["planId"], "cancel")
    release.set()
    _wait(lambda: not manager.is_busy())
    assert len(tokens) == 1
    result = writer.confirm(chat_id, None, WorkspaceBoundary(scopes[chat_id]), tokens[0], True)
    assert result.status == "denied"
    assert not (scopes[chat_id] / "new.txt").exists()


def test_registry_lock_is_not_held_while_canonical_providers_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow busy inspection and invalidation while a provider waits outside the registry lock."""

    manager, current, _scopes, _writer = _setup(tmp_path)
    plan = manager.create(current[0], "Provider", [ToolCall("metadata", "")])
    chat_id = str(current[0].chat_id)
    entered = Event()
    release = Event()
    original = manager._chat_provider

    def _blocking_provider() -> ChatSession:
        entered.set()
        assert release.wait(3.0)
        return original()

    monkeypatch.setattr(manager, "_chat_provider", _blocking_provider)
    result: list[Any] = []

    def _get() -> None:
        try:
            result.append(manager.get(chat_id))
        except ValueError:
            result.append(None)

    thread = Thread(target=_get, daemon=True)
    thread.start()
    assert entered.wait(2.0)
    assert manager.is_busy() is False
    manager.invalidate(chat_id)
    release.set()
    thread.join(2.0)
    assert not thread.is_alive()
    assert _plan(manager, chat_id)["state"] == "cancelled"
    assert plan["state"] == "pending"
