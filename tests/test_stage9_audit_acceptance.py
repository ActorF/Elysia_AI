"""Verify production Work audit, confirmed Undo, and interrupted recovery through RPC."""

from collections.abc import Callable
from io import StringIO
import json
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, cast

import pytest

import desktop_work_plans as plans_module
from desktop_backend import DesktopBackend
from desktop_protocol import ProtocolMethod, ProtocolValidationError, build_request
from tests.test_desktop_backend import FakeBrain
from tests.test_stage9_acceptance import _backend, _create_plan, _rpc as _base_rpc
from work_tools.contracts import ToolCall, ToolResult
from work_tools.executor import WorkToolExecutor
from work_tools.workspace import WorkspaceBoundary, WorkspaceBoundaryError


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 3.0
    tick = Event()
    while not predicate():
        assert monotonic() < deadline, "Stage 9 audit worker did not settle."
        tick.wait(0.002)


def _rpc(
    backend: DesktopBackend,
    output: StringIO,
    request_id: str,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Use real wire validation as soon as the pending audit RPCs are registered."""

    return _base_rpc(backend, output, request_id, cast(ProtocolMethod, method), params)


def _edit(backend: DesktopBackend, brain: FakeBrain, output: StringIO) -> tuple[str, str]:
    owner = {"chatId": str(brain.chat.chat_id)}
    approval = _rpc(backend, output, "edit-preview", "work.tool.preview", {
        **owner, "call": ToolCall("edit", "notes.txt", content="updated audit\n").to_data(),
    })["result"]["approval"]
    written = _rpc(backend, output, "edit-confirm", "work.tool.confirm", {
        **owner, "approvalId": approval["approvalId"], "granted": True,
    })
    assert written["ok"] and written["result"]["result"]["status"] == "succeeded"
    return written["result"]["result"]["data"]["undoId"], approval["approvalId"]


def test_production_plan_audit_then_confirmed_undo_restores_original_bytes(tmp_path: Path) -> None:
    """Complete the read-plan-approve-edit-verify-audit-Undo path with real persisted files."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    _rpc(backend, output, "initial-read", "work.tool.run", {**owner, "call": ToolCall("read_text", "notes.txt").to_data()})
    plan = _create_plan(backend, brain, output, [
        ToolCall("read_text", "notes.txt"), ToolCall("edit", "notes.txt", content="private audited update\n"),
        ToolCall("read_text", "notes.txt"),
    ])
    _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
    assert backend._work_runtime is not None
    runtime = backend._work_runtime
    _wait(lambda: not runtime.is_busy())
    waiting = _rpc(backend, output, "waiting", "work.plan.get", owner)["result"]["plan"]
    assert waiting["state"] == "waiting"
    approval = waiting["steps"][1]["approval"]
    _rpc(backend, output, "plan-approval", "work.plan.confirm", {
        **owner, "planId": plan["planId"], "approvalId": approval["approvalId"], "granted": True,
    })
    _wait(lambda: not runtime.is_busy())
    complete = _rpc(backend, output, "complete", "work.plan.get", owner)["result"]["plan"]
    assert complete["state"] == "succeeded"
    assert complete["steps"][2]["result"]["data"]["content"] == "private audited update\n"
    audit = _rpc(backend, output, "audit", "work.audit.list", owner)["result"]
    assert set(audit) == {"kind", "entries", "truncated"}
    assert audit["kind"] == "work.audit" and audit["truncated"] is False
    assert [entry["toolId"] for entry in audit["entries"]].count("read_text") == 3
    write = next(entry for entry in audit["entries"] if entry["toolId"] == "edit")
    assert set(write["parameters"]) == {"path", "contentBytes", "contentSha256"}
    assert write["approver"] == "user" and write["undoId"] is not None
    assert "private audited update" not in json.dumps(audit)
    assert "original" not in json.dumps(audit)
    assert str(selected) not in json.dumps(audit)
    undone = _rpc(backend, output, "undo", "work.undo", {**owner, "undoId": write["undoId"], "confirmed": True})
    assert undone["result"]["result"]["status"] == "succeeded"
    assert (selected / "notes.txt").read_bytes() == b"original\n"
    final = _rpc(backend, output, "audit-after-undo", "work.audit.list", owner)["result"]
    record = next(entry for entry in final["entries"] if entry["toolId"] == "edit")
    assert record["result"] == "undone" and record["undoId"] is None and record["recovery"] is None


def test_external_edit_prevents_undo_and_old_permission_replay(tmp_path: Path) -> None:
    """Neither Undo nor consumed approval may overwrite a later independent edit."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    undo_id, approval_id = _edit(backend, brain, output)
    (selected / "notes.txt").write_bytes(b"external user edit\n")
    undone = _rpc(backend, output, "undo-external", "work.undo", {**owner, "undoId": undo_id, "confirmed": True})
    assert undone["result"]["result"]["status"] == "denied"
    replay = _rpc(backend, output, "approval-replay", "work.tool.confirm", {**owner, "approvalId": approval_id, "granted": True})
    assert replay["result"]["result"]["status"] == "denied"
    assert (selected / "notes.txt").read_bytes() == b"external user edit\n"


def test_undo_revokes_a_visible_preview_of_the_superseded_file_state(tmp_path: Path) -> None:
    """A generic approval prepared before Undo cannot later reapply its old file assumptions."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    undo_id, _approval_id = _edit(backend, brain, output)
    stale = _rpc(backend, output, "superseded-preview", "work.tool.preview", {
        **owner, "call": ToolCall("edit", "notes.txt", content="stale preview").to_data(),
    })["result"]["approval"]
    undone = _rpc(backend, output, "undo-preview-state", "work.undo", {**owner, "undoId": undo_id, "confirmed": True})
    assert undone["result"]["result"]["status"] == "succeeded"
    refused = _rpc(backend, output, "superseded-confirm", "work.tool.confirm", {
        **owner, "approvalId": stale["approvalId"], "granted": True,
    })
    assert refused["result"]["result"]["status"] == "denied"
    assert (selected / "notes.txt").read_bytes() == b"original\n"


@pytest.mark.parametrize("change", ["owner", "project", "root"])
def test_undo_cannot_cross_owner_project_or_workspace_identity(tmp_path: Path, change: str) -> None:
    """A persisted inverse action remains bound to the original explicit capability."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    undo_id, _approval_id = _edit(backend, brain, output)
    if change == "owner":
        created = _rpc(backend, output, "other-chat", "chat.create", {"title": "Other", "mode": "work"})
        owner = {"chatId": created["result"]["activeChat"]["chatId"]}
        _rpc(backend, output, "other-scope", "work.workspace.bind", {**owner, "workspacePath": str(selected)})
    elif change == "project":
        project = _rpc(backend, output, "new-project", "project.create", {"name": "Changed context", "customInstructions": None})
        _rpc(backend, output, "move-project", "project.chat.move", {**owner, "projectId": project["result"]["activeProject"]["projectId"]})
    else:
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        (replacement / "notes.txt").write_bytes(b"updated audit\n")
        _rpc(backend, output, "new-root", "work.workspace.bind", {**owner, "workspacePath": str(replacement)})
    result = _rpc(backend, output, "wrong-scope-undo", "work.undo", {**owner, "undoId": undo_id, "confirmed": True})
    assert result["result"]["result"]["status"] == "denied"
    assert (selected / "notes.txt").read_bytes() == b"updated audit\n"
    if change == "root":
        assert (replacement / "notes.txt").read_bytes() == b"updated audit\n"


def test_history_filters_owner_and_redacts_literal_search_and_result_contents(tmp_path: Path) -> None:
    """Return each Chat's explicit history without another Chat's writes or plaintext query."""

    backend, brain, output, selected = _backend(tmp_path)
    first = {"chatId": str(brain.chat.chat_id)}
    undo_id, _approval_id = _edit(backend, brain, output)
    query = "literal private lookup"
    _rpc(backend, output, "search", "work.tool.run", {**first, "call": ToolCall("search_files", "", query=query).to_data()})
    history = _rpc(backend, output, "first-history", "work.audit.list", first)["result"]
    search = next(entry for entry in history["entries"] if entry["toolId"] == "search_files")
    assert set(search["parameters"]) == {"path", "queryCharacters", "querySha256"}
    assert search["parameters"]["queryCharacters"] == len(query)
    assert query not in json.dumps(history) and "updated audit" not in json.dumps(history)
    other = _rpc(backend, output, "other-chat", "chat.create", {"title": "Other owner", "mode": "work"})
    second = {"chatId": other["result"]["activeChat"]["chatId"]}
    _rpc(backend, output, "other-binding", "work.workspace.bind", {**second, "workspacePath": str(selected)})
    empty = _rpc(backend, output, "empty-history", "work.audit.list", second)["result"]
    assert empty["entries"] == []
    _rpc(backend, output, "other-read", "work.tool.run", {**second, "call": ToolCall("metadata", "notes.txt").to_data()})
    isolated = _rpc(backend, output, "other-history", "work.audit.list", second)["result"]
    assert len(isolated["entries"]) == 1 and isolated["entries"][0]["chatId"] == second["chatId"]
    assert undo_id not in json.dumps(isolated)


def test_interrupted_write_discloses_verified_manual_recovery_without_auto_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose a bounded relative intent folder and valid before-image after a partial mutation."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    assert backend._work_runtime is not None
    writer = backend._work_runtime._get_writes()

    def _interrupted(boundary: WorkspaceBoundary, call: ToolCall, _before: bytes | None) -> None:
        (boundary.root / call.path).write_bytes(b"partial admitted edit\n")
        raise WorkspaceBoundaryError("private native interrupted operation")

    monkeypatch.setattr(writer, "_apply", _interrupted)
    approval = _rpc(backend, output, "interrupted-preview", "work.tool.preview", {
        **owner, "call": ToolCall("edit", "notes.txt", content="complete").to_data(),
    })["result"]["approval"]
    result = _rpc(backend, output, "interrupted-confirm", "work.tool.confirm", {**owner, "approvalId": approval["approvalId"], "granted": True})
    assert result["result"]["result"]["status"] == "failed"
    audit = _rpc(backend, output, "recovery-history", "work.audit.list", owner)["result"]
    entry = audit["entries"][0]
    assert entry["recoverable"] is True and entry["undoId"] is None
    recovery = entry["recovery"]
    assert recovery["directory"] == "workspace/work/transactions/" + recovery["recoveryId"]
    assert recovery["backupPath"] == recovery["directory"] + "/before.bin"
    copy = backend._runtime_settings.data_layout.root / recovery["backupPath"]
    assert copy.read_bytes() == b"original\n"
    assert (selected / "notes.txt").read_bytes() == b"partial admitted edit\n"
    assert "private native" not in json.dumps(audit) and str(selected) not in json.dumps(audit)


def test_undo_waits_for_live_plan_and_physical_cancelled_read_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not run an inverse action while another explicit plan still owns logical or native capacity."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    undo_id, _approval_id = _edit(backend, brain, output)
    entered = Event()
    release = Event()

    def _blocking_read(_self: WorkToolExecutor, _call: ToolCall, **_kwargs: Any) -> ToolResult:
        entered.set()
        assert release.wait(3.0)
        return ToolResult("succeeded", {"path": "notes.txt", "name": "notes.txt", "kind": "file", "sizeBytes": 14})

    monkeypatch.setattr(plans_module.WorkToolExecutor, "run", _blocking_read)
    plan = _create_plan(backend, brain, output, [ToolCall("metadata", "notes.txt")])
    pending = _rpc(backend, output, "undo-live", "work.undo", {**owner, "undoId": undo_id, "confirmed": True})
    assert pending["ok"] is False
    _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
    assert entered.wait(2.0)
    _rpc(backend, output, "plan-cancel", "work.plan.control", {**owner, "planId": plan["planId"], "action": "cancel"})
    draining = _rpc(backend, output, "undo-draining", "work.undo", {**owner, "undoId": undo_id, "confirmed": True})
    assert draining["ok"] is False and draining["error"]["code"] == "work.busy"
    release.set()
    assert backend._work_runtime is not None
    _wait(lambda: not backend._work_runtime.is_busy())  # type: ignore[union-attr]
    undone = _rpc(backend, output, "undo-after-drain", "work.undo", {**owner, "undoId": undo_id, "confirmed": True})
    assert undone["result"]["result"]["status"] == "succeeded"
    assert (selected / "notes.txt").read_bytes() == b"original\n"


@pytest.mark.parametrize("params", [
    {"chatId": "chat_owner", "undoId": "undo_" + "a" * 32, "confirmed": False},
    {"chatId": "chat_owner", "undoId": "undo_" + "a" * 32},
    {"chatId": "chat_owner", "undoId": "undo_" + "a" * 32, "confirmed": True, "path": "different.txt"},
])
def test_undo_protocol_requires_exact_explicit_true_and_no_retargeting(params: dict[str, Any]) -> None:
    """Keep Undo confirmation a distinct exact action without implicit or permanent consent."""

    with pytest.raises(ProtocolValidationError):
        build_request("invalid-undo", cast(ProtocolMethod, "work.undo"), params)


@pytest.mark.parametrize("route", ["standalone", "plan"])
def test_read_audit_storage_failure_suppresses_unaudited_file_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    """Fail safely when private read history cannot be saved after the filesystem read completed."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    assert backend._work_runtime is not None
    writer = backend._work_runtime._get_writes()

    def _unavailable(*_args: Any) -> None:
        raise OSError("private history journal cannot be written")

    monkeypatch.setattr(writer, "record_read", _unavailable)
    if route == "standalone":
        result = _rpc(backend, output, "read-unlogged", "work.tool.run", {**owner, "call": ToolCall("read_text", "notes.txt").to_data()})["result"]["result"]
    else:
        plan = _create_plan(backend, brain, output, [ToolCall("read_text", "notes.txt")])
        _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
        _wait(lambda: not backend._work_runtime.is_busy())  # type: ignore[union-attr]
        failed = _rpc(backend, output, "failed-plan", "work.plan.get", owner)["result"]["plan"]
        assert failed["state"] == "failed"
        result = failed["steps"][0]["result"]
    assert result["status"] == "failed" and result["data"] is None
    assert result["error"]["code"] == "io_error"
    assert "private history" not in json.dumps(result)
    assert (selected / "notes.txt").read_bytes() == b"original\n"


def test_history_refuses_blocked_native_write_so_cancel_remains_responsive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the foreground reader out of the audit mutex while an admitted write is draining."""

    backend, brain, output, selected = _backend(tmp_path)
    owner = {"chatId": str(brain.chat.chat_id)}
    plan = _create_plan(backend, brain, output, [ToolCall("edit", "notes.txt", content="approved write\n")])
    _rpc(backend, output, "plan-start", "work.plan.control", {**owner, "planId": plan["planId"], "action": "start"})
    assert backend._work_runtime is not None
    runtime = backend._work_runtime
    _wait(lambda: not runtime.is_busy())
    pending = _rpc(backend, output, "waiting-write", "work.plan.get", owner)["result"]["plan"]
    approval = pending["steps"][0]["approval"]
    # Waiting for a user's decision owns no native transaction lock.
    idle_history = _rpc(backend, output, "idle-history", "work.audit.list", owner)
    assert idle_history["ok"] is True
    writer = runtime._get_writes()
    apply = writer._apply
    entered = Event()
    release = Event()

    def _blocked_apply(boundary: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        entered.set()
        assert release.wait(3.0)
        apply(boundary, call, before)

    monkeypatch.setattr(writer, "_apply", _blocked_apply)
    _rpc(backend, output, "approve-write", "work.plan.confirm", {
        **owner, "planId": plan["planId"], "approvalId": approval["approvalId"], "granted": True,
    })
    assert entered.wait(2.0)
    try:
        denied = _rpc(backend, output, "busy-history", "work.audit.list", owner)
        assert denied["ok"] is False and denied["error"]["code"] == "work.busy"
        with pytest.raises(PermissionError):
            runtime.dispatch("work.audit.list", owner)
        cancelled = _rpc(backend, output, "cancel-write", "work.plan.control", {
            **owner, "planId": plan["planId"], "action": "cancel",
        })
        assert cancelled["ok"] is True and cancelled["result"]["plan"]["busy"] is True
        assert not release.is_set()
    finally:
        release.set()
    _wait(lambda: not runtime.is_busy())
    history = _rpc(backend, output, "drained-history", "work.audit.list", owner)
    assert history["ok"] is True and history["result"]["entries"][0]["result"] == "succeeded"
    assert (selected / "notes.txt").read_bytes() == b"approved write\n"
