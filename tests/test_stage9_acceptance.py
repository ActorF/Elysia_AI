"""Exercise Stage 9 plans through real Backend RPCs and lifecycle authority gates."""

from collections.abc import Callable
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any

import pytest

import desktop_work_plans as plans_module
from chats import ProjectId
from desktop_backend import DesktopBackend
from desktop_protocol import ProtocolMethod, ProtocolValidationError
from desktop_work import DesktopWorkRuntime
from projects import create_project
from tests.test_desktop_backend import (
    FakeBrain, _ControlledTranscriber, _initialized_backend, _output_messages,
    _request, _transcription_runner_factory,
)
from work_tools.contracts import ToolCall, ToolResult
from work_tools.executor import WorkToolExecutor
from work_tools.workspace import WorkspaceBoundary


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 3.0
    tick = Event()
    while not predicate():
        assert monotonic() < deadline, "Stage 9 worker did not settle."
        tick.wait(0.002)


def _rpc(
    backend: DesktopBackend,
    output: StringIO,
    request_id: str,
    method: ProtocolMethod,
    params: dict[str, Any],
) -> dict[str, Any]:
    backend._handle_line(json.dumps(_request(request_id, method, params)))
    return next(message for message in reversed(_output_messages(output)) if message.get("id") == request_id)


def _backend(tmp_path: Path) -> tuple[DesktopBackend, FakeBrain, StringIO, Path]:
    factory, _runners = _transcription_runner_factory(_ControlledTranscriber(RuntimeError("Unused STT")))
    backend, brain, output = _initialized_backend(tmp_path, factory)
    backend._runtime_settings = replace(backend._runtime_settings, data_root=tmp_path / "application-data")
    owner = {"chatId": str(brain.chat.chat_id)}
    assert _rpc(backend, output, "mode-work", "chat.mode", {**owner, "mode": "work"})["ok"]
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "notes.txt").write_bytes(b"original\n")
    assert _rpc(backend, output, "bind-work", "work.workspace.bind", {**owner, "workspacePath": str(selected)})["ok"]
    return backend, brain, output, selected


def _create_plan(backend: DesktopBackend, brain: FakeBrain, output: StringIO, calls: list[ToolCall]) -> dict[str, Any]:
    result = _rpc(backend, output, "plan-create", "work.plan.create", {
        "chatId": str(brain.chat.chat_id), "title": "Read, approve, update and verify",
        "calls": [call.to_data() for call in calls],
    })
    assert result["ok"] is True
    return result["result"]["plan"]


def test_backend_read_plan_confirmation_write_verification_and_chat_denial(tmp_path: Path) -> None:
    """Complete the Stage 9 gate through authenticated production dispatch and persisted file bytes."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    plan = _create_plan(backend, brain, output, [
        ToolCall("read_text", "notes.txt"), ToolCall("edit", "notes.txt", content="verified update\n"),
        ToolCall("read_text", "notes.txt"),
    ])
    _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
    assert backend._work_runtime is not None
    _wait(lambda: not backend._work_runtime.is_busy())  # type: ignore[union-attr]
    waiting = _rpc(backend, output, "plan-waiting", "work.plan.get", owner)["result"]["plan"]
    assert waiting["state"] == "waiting"
    assert (selected / "notes.txt").read_bytes() == b"original\n"
    approval = waiting["steps"][1]["approval"]
    bypass = _rpc(backend, output, "generic-bypass", "work.tool.confirm", {
        **owner, "approvalId": approval["approvalId"], "granted": True,
    })
    assert bypass["result"]["result"]["status"] == "denied"
    _rpc(backend, output, "plan-confirm", "work.plan.confirm", {
        **owner, "planId": plan["planId"], "approvalId": approval["approvalId"], "granted": True,
    })
    _wait(lambda: not backend._work_runtime.is_busy())  # type: ignore[union-attr]
    final = _rpc(backend, output, "plan-done", "work.plan.get", owner)["result"]["plan"]
    assert final["state"] == "succeeded" and final["busy"] is False
    assert final["steps"][2]["result"]["data"]["content"] == "verified update\n"
    assert (selected / "notes.txt").read_bytes() == b"verified update\n"
    _rpc(backend, output, "switch-chat", "chat.mode", {**owner, "mode": "chat"})
    denied = _rpc(backend, output, "chat-write-denied", "work.tool.preview", {
        **owner, "call": ToolCall("edit", "notes.txt", content="silent edit").to_data(),
    })
    assert denied["ok"] is False and denied["error"]["code"] == "work.denied"
    assert (selected / "notes.txt").read_bytes() == b"verified update\n"


def test_busy_plan_blocks_lifecycle_before_repository_changes_and_cancel_remains_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hold one read at a native boundary and verify mutation admission cannot bypass its drain."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    entered = Event()
    release = Event()

    def _blocking_read(_self: WorkToolExecutor, _call: ToolCall, **_kwargs: Any) -> ToolResult:
        entered.set()
        assert release.wait(3.0)
        return ToolResult("succeeded", {"path": "notes.txt", "content": "late read", "sizeBytes": 9})

    monkeypatch.setattr(plans_module.WorkToolExecutor, "run", _blocking_read)
    plan = _create_plan(backend, brain, output, [ToolCall("read_text", "notes.txt")])
    _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
    assert entered.wait(2.0)
    calls = list(brain.session_calls)
    requests: list[tuple[ProtocolMethod, dict[str, Any]]] = [
        ("chat.create", {"title": "Unexpected", "mode": "chat"}),
        ("chat.open", owner), ("chat.rename", {**owner, "title": "Unexpected"}),
        ("chat.mode", {**owner, "mode": "chat"}), ("chat.archive", {**owner, "archived": True}),
        ("chat.delete", owner), ("chat.stream", {**owner, "message": "reply", "speakReply": False}),
        ("project.create", {"name": "Unexpected", "customInstructions": None}),
        ("project.archive", {"projectId": "project_missing", "archived": True}),
        ("project.chat.move", {**owner, "projectId": None}),
        ("work.workspace.bind", {**owner, "workspacePath": None}),
    ]
    for index, (method, params) in enumerate(requests):
        response = _rpc(backend, output, f"blocked-{index}", method, params)
        assert response["ok"] is False and response["error"]["code"] == "work.busy"
    assert brain.session_calls == calls
    with pytest.raises(ProtocolValidationError) as blocked_generation:
        backend._reserve_generation("direct-generation", {**owner, "message": "reply", "speakReply": False}, method="chat.stream")
    assert blocked_generation.value.code == "work.busy"
    assert backend._work_runtime is not None
    with pytest.raises(PermissionError):
        backend._work_runtime.dispatch("work.workspace.bind", {**owner, "workspacePath": None})
    cancelled = _rpc(backend, output, "plan-cancel", "work.plan.control", {
        **owner, "planId": plan["planId"], "action": "cancel",
    })["result"]["plan"]
    assert cancelled["state"] == "cancelled" and cancelled["busy"] is True
    still_busy = _rpc(backend, output, "still-blocked", "chat.delete", owner)
    assert still_busy["error"]["code"] == "work.busy"
    release.set()
    _wait(lambda: not backend._work_runtime.is_busy())  # type: ignore[union-attr]
    final = _rpc(backend, output, "plan-drained", "work.plan.get", owner)["result"]["plan"]
    assert final["busy"] is False and "late read" not in str(final)
    assert (selected / "notes.txt").read_bytes() == b"original\n"


def test_archived_or_missing_project_refuses_work_and_restore_needs_new_preview(tmp_path: Path) -> None:
    """A Chat's linked Project remains an authority boundary for independent Work bindings."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    project = create_project(name="Work Project")
    brain.add_project(project)
    canonical = brain.get_chat(brain.chat.chat_id)
    brain.add_chat(replace(canonical, project_id=project.project_id))
    call = ToolCall("edit", "notes.txt", content="new preview required\n").to_data()
    first = _rpc(backend, output, "first-preview", "work.tool.preview", {**owner, "call": call})["result"]["approval"]
    archived = _rpc(backend, output, "archive-project", "project.archive", {"projectId": str(project.project_id), "archived": True})
    assert archived["ok"] is True
    blocked = _rpc(backend, output, "archived-work", "work.tool.confirm", {
        **owner, "approvalId": first["approvalId"], "granted": True,
    })
    assert blocked["ok"] is False and blocked["error"]["code"] == "work.denied"
    _rpc(backend, output, "restore-project", "project.archive", {"projectId": str(project.project_id), "archived": False})
    stale = _rpc(backend, output, "old-after-restore", "work.tool.confirm", {
        **owner, "approvalId": first["approvalId"], "granted": True,
    })
    assert stale["result"]["result"]["status"] == "denied"
    assert (selected / "notes.txt").read_bytes() == b"original\n"
    second = _rpc(backend, output, "fresh-preview", "work.tool.preview", {**owner, "call": call})["result"]["approval"]
    assert second["approvalId"] != first["approvalId"]
    brain.add_chat(replace(brain.get_chat(brain.chat.chat_id), project_id=ProjectId("project_missing")))
    missing = _rpc(backend, output, "missing-project", "work.tool.run", {**owner, "call": ToolCall("read_text", "notes.txt").to_data()})
    assert missing["ok"] is False and missing["error"]["code"] == "work.denied"


def test_committed_chat_delete_recovers_active_pointer_when_optional_binding_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed binding-file unlink cannot turn a completed deletion into a stranded active Chat."""

    backend, brain, output, _selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    assert backend._work_runtime is not None

    def _unlink_failure(_chat_id: str) -> None:
        raise OSError("C:/Users/private/binding.json failed to unlink")

    monkeypatch.setattr(backend._work_runtime, "forget_binding", _unlink_failure)
    deleted = _rpc(backend, output, "delete-chat", "chat.delete", owner)
    assert deleted["ok"] is True
    assert deleted["result"]["activeChat"]["chatId"] != owner["chatId"]
    assert backend._active_chat_snapshot() is not None
    listed = _rpc(backend, output, "list-after-delete", "chat.list", {"includeArchived": True})
    assert listed["ok"] is True
    assert "private" not in caplog.text and "binding.json" not in caplog.text


@pytest.mark.parametrize("ending", ["shutdown", "eof"])
def test_backend_shutdown_waits_for_admitted_real_write_without_holding_state_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    """Let an approved native write and audit settle before process shutdown acknowledges completion."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    plan = _create_plan(backend, brain, output, [ToolCall("edit", "notes.txt", content="settled write\n")])
    _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
    assert backend._work_runtime is not None
    runtime = backend._work_runtime
    _wait(lambda: not runtime.is_busy())
    approval = _rpc(backend, output, "pending-write", "work.plan.get", owner)["result"]["plan"]["steps"][0]["approval"]
    writer = runtime._get_writes()
    original_apply = writer._apply
    entered = Event()
    release = Event()

    def _blocked_apply(boundary: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        entered.set()
        assert release.wait(3.0)
        original_apply(boundary, call, before)

    monkeypatch.setattr(writer, "_apply", _blocked_apply)
    _rpc(backend, output, "plan-confirm", "work.plan.confirm", {
        **owner, "planId": plan["planId"], "approvalId": approval["approvalId"], "granted": True,
    })
    assert entered.wait(2.0)
    ended = Event()

    def _end() -> None:
        if ending == "shutdown":
            backend._handle_line(json.dumps(_request("shutdown-work", "shutdown", {})))
        else:
            backend._input_stream = StringIO()
            backend.run()
        ended.set()

    thread = Thread(target=_end, daemon=True)
    thread.start()
    _wait(lambda: runtime._closed)
    assert ended.is_set() is False and runtime.is_busy()
    assert backend._state_lock.acquire(blocking=False)
    backend._state_lock.release()
    release.set()
    assert ended.wait(3.0)
    thread.join(1.0)
    assert (selected / "notes.txt").read_bytes() == b"settled write\n"
    assert writer.audit(owner["chatId"])[0]["result"] == "succeeded"
    assert runtime.is_busy() is False
    if ending == "shutdown":
        response = next(message for message in _output_messages(output) if message.get("id") == "shutdown-work")
        assert response["result"] == {"stopped": True}


def test_scope_invalidation_without_write_or_plan_does_not_open_native_recovery(tmp_path: Path) -> None:
    """Keep read-only mode switches and unbinds free of unnecessary recovery storage creation."""

    current = FakeBrain().chat
    current = replace(current, mode="work")
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: current)
    runtime.dispatch("work.workspace.get", {"chatId": str(current.chat_id)})
    runtime.invalidate(str(current.chat_id))
    assert runtime._writes is None and not (tmp_path / "private").exists()
