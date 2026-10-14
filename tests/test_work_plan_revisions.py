"""Verify explicit suffix revisions, owner races, and paused approval timeout routing."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import pytest

import work_tools.writes as writes_module
from chats import create_chat_session
from desktop_work import DesktopWorkRuntime
from work_tools.contracts import ToolCall
from work_tools.wire import validate_work_params


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 5
    tick = Event()
    while not predicate():
        assert monotonic() < deadline
        tick.wait(.002)


def _setup(tmp_path: Path) -> tuple[DesktopWorkRuntime, str, Path, dict[str, Any]]:
    chat = create_chat_session(title="Work", mode="work", model_name="test")
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "note.txt").write_text("original", encoding="utf-8")
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: chat)
    chat_id = str(chat.chat_id)
    runtime.dispatch("work.workspace.bind", {"chatId": chat_id, "workspacePath": str(selected)})
    plan = runtime.dispatch("work.plan.create", {"chatId": chat_id, "title": "Read edit verify", "calls": [
        ToolCall("metadata", "note.txt").to_data(), ToolCall("edit", "note.txt", content="initial").to_data(),
        ToolCall("metadata", "note.txt").to_data(),
    ]})["plan"]
    runtime.dispatch("work.plan.control", {"chatId": chat_id, "planId": plan["planId"], "action": "start"})
    _wait(lambda: not runtime.is_busy())
    waiting = runtime.dispatch("work.plan.get", {"chatId": chat_id})["plan"]
    assert waiting["state"] == "waiting"
    return runtime, chat_id, selected, waiting


def _request(chat_id: str, waiting: dict[str, Any]) -> dict[str, Any]:
    return {"chatId": chat_id, "planId": waiting["planId"],
            "approvalId": waiting["steps"][1]["approval"]["approvalId"],
            "call": ToolCall("edit", "note.txt", content="revised").to_data()}


def test_real_rpc_revision_consumes_old_token_and_never_replays_completed_steps(tmp_path: Path) -> None:
    """The real runtime returns a new pending suffix; only fresh Start and approval can change the file."""

    runtime, chat_id, selected, waiting = _setup(tmp_path)
    request = _request(chat_id, waiting)
    revised = runtime.dispatch("work.plan.revise", validate_work_params("work.plan.revise", request))["plan"]
    assert revised["planId"] != waiting["planId"] and revised["state"] == "pending" and not revised["busy"]
    assert [step["call"]["toolId"] for step in revised["steps"]] == ["edit", "metadata"]
    assert revised["steps"][0]["call"] == request["call"]
    assert (selected / "note.txt").read_text("utf-8") == "original"
    with pytest.raises(ValueError):
        runtime.dispatch("work.plan.confirm", {"chatId": chat_id, "planId": waiting["planId"],
                                               "approvalId": request["approvalId"], "granted": True})
    runtime.dispatch("work.plan.control", {"chatId": chat_id, "planId": revised["planId"], "action": "start"})
    _wait(lambda: not runtime.is_busy())
    fresh = runtime.dispatch("work.plan.get", {"chatId": chat_id})["plan"]
    token = fresh["steps"][0]["approval"]["approvalId"]
    assert token != request["approvalId"]
    runtime.dispatch("work.plan.confirm", {"chatId": chat_id, "planId": revised["planId"], "approvalId": token, "granted": True})
    _wait(lambda: not runtime.is_busy())
    assert runtime.dispatch("work.plan.get", {"chatId": chat_id})["plan"]["state"] == "succeeded"
    assert (selected / "note.txt").read_text("utf-8") == "revised"
    history = runtime.dispatch("work.audit.list", {"chatId": chat_id})["entries"]
    assert [entry["decision"] for entry in history if entry["toolId"] == "edit"] == ["approve", "modify"]
    assert len([entry for entry in history if entry["toolId"] == "metadata"]) == 2


@pytest.mark.parametrize("change", ["tool", "token", "plan", "extra", "path"])
def test_revision_rejects_changed_tool_stale_identity_pollution_and_unsafe_path(tmp_path: Path, change: str) -> None:
    """Malformed revisions neither mutate nor consume the still-visible valid approval."""

    runtime, chat_id, selected, waiting = _setup(tmp_path)
    request = _request(chat_id, waiting)
    if change == "tool":
        request["call"] = ToolCall("delete", "note.txt").to_data()
    elif change == "token":
        request["approvalId"] = "approval_" + "a" * 32
    elif change == "plan":
        request["planId"] = "plan_" + "a" * 32
    elif change == "extra":
        request["approved"] = True
    else:
        request["call"]["arguments"]["path"] = "../outside"
    with pytest.raises(ValueError):
        runtime.dispatch("work.plan.revise", validate_work_params("work.plan.revise", request))
    current = runtime.dispatch("work.plan.get", {"chatId": chat_id})["plan"]
    assert current["state"] == "waiting" and current["planId"] == waiting["planId"]
    assert (selected / "note.txt").read_text("utf-8") == "original"


def test_invalidation_between_revision_decision_and_creation_cannot_reinstall_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Navigation wins the creation CAS permanently; a consumed old modify never publishes a fresh owner afterward."""

    runtime, chat_id, selected, waiting = _setup(tmp_path)
    manager = runtime._plans
    original = manager._create

    def _invalidate_then_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        manager.invalidate(chat_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_create", _invalidate_then_create)
    with pytest.raises(ValueError):
        runtime.dispatch("work.plan.revise", _request(chat_id, waiting))
    assert not manager.has_live_plan(chat_id)
    assert (selected / "note.txt").read_text("utf-8") == "original"
    assert runtime.dispatch("work.audit.list", {"chatId": chat_id})["entries"][0]["decision"] == "modify"


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("clock", ["utc", "monotonic"])
def test_get_automatically_settles_waiting_and_paused_expired_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paused: bool, clock: str) -> None:
    """Polling launches only a nonmutating timeout decision using the writer's own clocks, including rollback protection."""

    runtime, chat_id, selected, waiting = _setup(tmp_path)
    writer = runtime._get_writes()
    token = waiting["steps"][1]["approval"]["approvalId"]
    pending = writer._pending[token]
    if paused:
        runtime.dispatch("work.plan.control", {"chatId": chat_id, "planId": waiting["planId"], "action": "pause"})
    if clock == "utc":
        monkeypatch.setattr(writer, "_clock", lambda: pending.expires_at + timedelta(seconds=1))
    else:
        monkeypatch.setattr(writer, "_clock", lambda: datetime(2000, 1, 1, tzinfo=timezone.utc))
        monkeypatch.setattr(writes_module, "monotonic", lambda: pending.deadline + 1)
    runtime.dispatch("work.plan.get", {"chatId": chat_id})
    _wait(lambda: not runtime.is_busy())
    final = runtime.dispatch("work.plan.get", {"chatId": chat_id})["plan"]
    assert final["state"] == "failed" and final["steps"][1]["result"]["error"]["code"] == "timeout"
    assert (selected / "note.txt").read_text("utf-8") == "original"
    writes = [entry for entry in runtime.dispatch("work.audit.list", {"chatId": chat_id})["entries"] if entry["toolId"] == "edit"]
    assert len(writes) == 1 and writes[0]["decision"] == "timeout"
