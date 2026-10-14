"""Exercise durable Work recovery with synthetic files and explicit fresh approval."""

from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import pytest

from agent.work_plan import WorkPlanRunner
from agent.workflow_session import WorkflowSession
from agent.workflow_state import workspace_key
from agent.workflow_store import WorkflowStore
from chats import create_chat_session
from desktop_work_plans import DesktopWorkPlans
from work_tools.contracts import ToolCall, ToolResult
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 5
    while not predicate():
        assert monotonic() < deadline, "Recovery worker did not settle."
        Event().wait(0.002)


def _manager(tmp_path: Path) -> tuple[DesktopWorkPlans, Any, Path, WorkflowStore]:
    chat = create_chat_session(title="Recovery", mode="work", model_name="test-model")
    selected = tmp_path / "selected"
    selected.mkdir()
    writer = WriteCoordinator(tmp_path / "private")
    store = WorkflowStore(tmp_path / "private" / "workflows")
    manager = DesktopWorkPlans(lambda: chat, lambda _chat: WorkspaceBoundary(selected), lambda: writer,
                               workflow_store=store)
    return manager, chat, selected, store


def test_restart_recovers_waiting_plan_paused_and_requires_a_new_exact_approval(tmp_path: Path) -> None:
    """Reopening disk state cannot consume the approval minted by the previous process."""

    manager, chat, selected, store = _manager(tmp_path)
    chat_id = str(chat.chat_id)
    (selected / "note.txt").write_text("old", encoding="utf-8")
    plan = manager.create(chat, "Edit and verify", [
        ToolCall("metadata", "note.txt"), ToolCall("edit", "note.txt", content="new"),
        ToolCall("read_text", "note.txt"),
    ])
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None and waiting["state"] == "waiting"
    old_token = waiting["steps"][1]["approval"]["approvalId"]
    replacement_writer = WriteCoordinator(tmp_path / "private")
    restarted = DesktopWorkPlans(lambda: chat, lambda _chat: WorkspaceBoundary(selected), lambda: replacement_writer,
                                 workflow_store=store)
    recovered = restarted.get(chat_id)
    assert recovered is not None and recovered["state"] == "paused" and not recovered["busy"]
    assert recovered["steps"][0]["status"] == "succeeded"
    assert recovered["steps"][1]["status"] == "pending" and recovered["steps"][1]["approval"] is None
    assert (selected / "note.txt").read_text("utf-8") == "old"
    with pytest.raises(ValueError):
        restarted.confirm(chat_id, plan["planId"], old_token, True)
    restarted.control(chat_id, plan["planId"], "resume")
    _wait(lambda: not restarted.is_busy())
    fresh = restarted.get(chat_id)
    assert fresh is not None
    approval = fresh["steps"][1]["approval"]
    assert approval["approvalId"] != old_token
    restarted.confirm(chat_id, plan["planId"], approval["approvalId"], True)
    _wait(lambda: not restarted.is_busy())
    assert restarted.get(chat_id)["state"] == "succeeded"
    assert (selected / "note.txt").read_text("utf-8") == "new"


def test_checkpoint_failure_before_admitted_write_prevents_native_effect() -> None:
    """A write cannot begin unless its uncertain-write checkpoint became durable."""

    effects: list[bool] = []

    def _save(_plan: dict[str, Any], admitted: str | None) -> None:
        if admitted is not None:
            raise OSError("synthetic persistence failure")

    call = ToolCall("edit", "note.txt", content="new")
    runner = WorkPlanRunner(
        lambda _call, _cancel: ToolResult("failed", error_code="io_error"),
        lambda _call: {"approvalId": "approval_" + "a" * 32, "call": call.to_data(), "risk": "medium",
                       "preview": "-old\n+new", "expiresAt": "2100-01-01T00:00:00+00:00"},
        lambda _token, _granted: (effects.append(True) or ToolResult("failed", error_code="io_error")),
        checkpoint=_save,
    )
    plan = runner.create("Update", [call])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    runner.confirm(plan["planId"], "approval_" + "a" * 32, True)
    _wait(lambda: not runner.is_busy())
    assert effects == [] and runner.get()["state"] == "failed"


def test_session_preserves_cancelled_terminal_instead_of_reviving_old_pending_plan(tmp_path: Path) -> None:
    """A later process observes the final cancelled record, not an earlier runnable checkpoint."""

    manager, chat, selected, store = _manager(tmp_path)
    plan = manager.create(chat, "Cancel before work", [ToolCall("metadata", "")])
    manager.control(str(chat.chat_id), plan["planId"], "cancel")
    boundary = WorkspaceBoundary(selected)
    session = WorkflowSession(store, str(chat.chat_id), None, workspace_key(boundary.normalized_root, boundary.root_identity))
    recovered = session.adopt_recovery()
    assert recovered["state"] == "cancelled"
    assert all(step["status"] == "cancelled" for step in recovered["steps"])


def test_interrupted_admitted_write_is_inspection_only_and_never_replayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An admitted checkpoint has no runnable owner after restart even before the callback returns."""

    manager, chat, selected, store = _manager(tmp_path)
    (selected / "note.txt").write_text("old", encoding="utf-8")
    entered, release = Event(), Event()

    def _blocked(_owner: Any, _token: str, _granted: bool) -> ToolResult:
        entered.set()
        assert release.wait(5)
        return ToolResult("failed", error_code="io_error")

    monkeypatch.setattr(manager, "_confirm_write", _blocked)
    plan = manager.create(chat, "Interrupted edit", [ToolCall("edit", "note.txt", content="new")])
    manager.control(str(chat.chat_id), plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    token = manager.get(str(chat.chat_id))["steps"][0]["approval"]["approvalId"]
    manager.confirm(str(chat.chat_id), plan["planId"], token, True)
    assert entered.wait(5)
    try:
        writer = WriteCoordinator(tmp_path / "private")
        restarted = DesktopWorkPlans(lambda: chat, lambda _chat: WorkspaceBoundary(selected), lambda: writer,
                                     workflow_store=store)
        assert restarted.get(str(chat.chat_id)) is None
        assert restarted.recovery_notice(str(chat.chat_id)) == {"status": "uncertain_write", "planId": plan["planId"]}
        assert not restarted.has_live_plan(str(chat.chat_id))
        with pytest.raises(ValueError):
            restarted.control(str(chat.chat_id), plan["planId"], "resume")
        assert (selected / "note.txt").read_text("utf-8") == "old"
    finally:
        release.set()
        _wait(lambda: not manager.is_busy())


def test_recovery_does_not_import_a_different_selected_directory(tmp_path: Path) -> None:
    """A directory with a similar file name cannot inherit the former binding's pending action."""

    manager, chat, _selected, store = _manager(tmp_path)
    manager.create(chat, "Old owner", [ToolCall("create", "note.txt", content="private")])
    other = tmp_path / "other"
    other.mkdir()
    writer = WriteCoordinator(tmp_path / "private")
    restarted = DesktopWorkPlans(lambda: chat, lambda _chat: WorkspaceBoundary(other), lambda: writer,
                                 workflow_store=store)
    assert restarted.get(str(chat.chat_id)) is None
    assert list(other.iterdir()) == []


def test_cancel_remains_responsive_while_a_checkpoint_is_slow() -> None:
    """Storage serialization must not hold the logical state lock or admit a cancelled read."""

    entered, release = Event(), Event()
    reads: list[str] = []

    def _save(plan: dict[str, Any], _admitted: str | None) -> None:
        if plan["state"] == "running":
            entered.set()
            assert release.wait(5)

    runner = WorkPlanRunner(
        lambda call, _cancel: (reads.append(call.path) or ToolResult("failed", error_code="io_error")),
        lambda _call: {}, lambda _token, _granted: ToolResult("failed", error_code="io_error"), checkpoint=_save,
    )
    plan = runner.create("Slow private disk", [ToolCall("metadata", "note.txt")])
    runner.control(plan["planId"], "start")
    assert entered.wait(5)
    try:
        started = monotonic()
        assert runner.control(plan["planId"], "cancel")["state"] == "cancelled"
        assert monotonic() - started < 1
        assert runner.is_busy()
    finally:
        release.set()
        _wait(lambda: not runner.is_busy())
    assert reads == []


def test_deleted_chat_tombstone_survives_its_old_callback_draining(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deleted owner's late checkpoint cannot resurrect its task after the durable CAS tombstone."""

    manager, chat, selected, store = _manager(tmp_path)
    entered, release = Event(), Event()

    def _blocked(_owner: Any, _call: ToolCall, _cancel: Callable[[], bool]) -> ToolResult:
        entered.set()
        assert release.wait(5)
        return ToolResult("failed", error_code="io_error")

    monkeypatch.setattr(manager, "_read", _blocked)
    chat_id = str(chat.chat_id)
    boundary = WorkspaceBoundary(selected)
    key = workspace_key(boundary.normalized_root, boundary.root_identity)
    plan = manager.create(chat, "Delete while draining", [ToolCall("metadata", "")])
    manager.control(chat_id, plan["planId"], "start")
    assert entered.wait(5)
    try:
        before = store.load_latest(chat_id, None, key)
        assert before is not None and before.state is not None
        manager.invalidate(chat_id)
        manager.forget_checkpoint(chat_id)
        tombstone = store.load_latest(chat_id, None, key)
        assert tombstone is not None and tombstone.state is None and tombstone.revision > before.revision
        assert manager.is_busy()
    finally:
        release.set()
        _wait(lambda: not manager.is_busy())
    restarted = WorkflowStore(tmp_path / "private" / "workflows")
    final = restarted.load_latest(chat_id, None, key)
    assert final is not None and final.state is None and final.revision == tombstone.revision
    assert list(selected.iterdir()) == []


def test_recovery_reservation_blocks_create_until_old_load_cannot_revoke_a_new_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serialize lazy recovery with replacement so an older get cannot invalidate a new plan's preview."""

    from concurrent.futures import ThreadPoolExecutor

    manager, chat, selected, store = _manager(tmp_path)
    chat_id = str(chat.chat_id)
    original = manager.create(chat, "Saved read", [ToolCall("metadata", "")])
    writer = WriteCoordinator(tmp_path / "private")
    restarted = DesktopWorkPlans(lambda: chat, lambda _chat: WorkspaceBoundary(selected), lambda: writer,
                                 workflow_store=store)
    entered, release = Event(), Event()
    original_load = store.load_latest

    def _blocked(owner: str, project: str | None, key: str) -> Any:
        loaded = original_load(owner, project, key)
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return loaded

    monkeypatch.setattr(store, "load_latest", _blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(restarted.get, chat_id)
        assert entered.wait(5)
        try:
            with pytest.raises(ValueError):
                restarted.create(chat, "Concurrent replacement", [ToolCall("create", "new.txt", content="new")])
            assert not reading.done() and list(selected.iterdir()) == []
        finally:
            release.set()
        recovered = reading.result(timeout=5)
    assert recovered is not None and recovered["planId"] == original["planId"] and recovered["state"] == "paused"
    replacement = restarted.create(chat, "Replacement after recovery", [ToolCall("create", "new.txt", content="new")])
    restarted.control(chat_id, replacement["planId"], "start")
    _wait(lambda: not restarted.is_busy())
    waiting = restarted.get(chat_id)
    assert waiting is not None and waiting["state"] == "waiting"
    token = waiting["steps"][0]["approval"]["approvalId"]
    restarted.confirm(chat_id, replacement["planId"], token, True)
    _wait(lambda: not restarted.is_busy())
    final = restarted.get(chat_id)
    assert final is not None and final["state"] == "succeeded"
    assert (selected / "new.txt").read_text("utf-8") == "new"


def test_thread_start_failure_is_saved_as_failed_instead_of_recoverable_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure to acquire a worker is a real pending-to-failed transition in durable state."""

    manager, chat, selected, store = _manager(tmp_path)
    plan = manager.create(chat, "Worker unavailable", [ToolCall("metadata", "")])

    def _fail_start(_thread: Any) -> None:
        raise RuntimeError("synthetic thread allocation failure")

    monkeypatch.setattr("agent.work_plan.Thread.start", _fail_start)
    result = manager.control(str(chat.chat_id), plan["planId"], "start")
    assert result["state"] == "failed"
    boundary = WorkspaceBoundary(selected)
    stored = store.load_latest(str(chat.chat_id), None, workspace_key(boundary.normalized_root, boundary.root_identity))
    assert stored is not None and stored.state is not None
    assert stored.state.to_data()["plan"]["state"] == "failed"
