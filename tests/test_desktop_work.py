"""Exercise authenticated Work scope, typed reads, and one-action write permission."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from chats import ChatSession, create_chat_session
from desktop_work import DesktopWorkRuntime
from work_tools.workspace import WorkspaceBoundary
from desktop_protocol import build_request, build_success_response, ProtocolValidationError
from tests.test_desktop_backend import (_initialized_backend, _transcription_runner_factory,
                                       _request, _output_messages, _ControlledTranscriber)


def _chat() -> ChatSession:
    return create_chat_session(title="Work", mode="work", model_name="test-model")


def test_scope_persists_per_chat_and_clears_without_affecting_project(tmp_path: Path) -> None:
    """Standalone Work binding survives coordinator recreation and revokes explicitly."""

    selected = tmp_path / "selected"
    selected.mkdir()
    current = _chat()
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: current)
    args = {"chatId": str(current.chat_id)}
    assert runtime.dispatch("work.workspace.get", args)["workspacePath"] is None
    saved = runtime.dispatch("work.workspace.bind", {**args, "workspacePath": str(selected)})
    assert saved == {"kind": "work.workspace", **args, "workspacePath": str(selected)}
    assert DesktopWorkRuntime(tmp_path / "private", lambda: current).dispatch("work.workspace.get", args) == saved
    current = _chat()
    assert runtime.dispatch("work.workspace.get", {"chatId": str(current.chat_id)})["workspacePath"] is None
    current = replace(current, mode="chat")
    with pytest.raises(PermissionError):
        runtime.dispatch("work.workspace.bind", {"chatId": str(current.chat_id), "workspacePath": str(selected)})
    current = replace(current, mode="work")
    assert runtime.dispatch("work.workspace.bind", {"chatId": str(current.chat_id), "workspacePath": None})["workspacePath"] is None


def test_scope_rejects_stale_owner_and_replaced_root(tmp_path: Path) -> None:
    """An old request or recreated directory cannot inherit the selected capability."""

    current = _chat()
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: current)
    selected = tmp_path / "selected"
    selected.mkdir()
    runtime.dispatch("work.workspace.bind", {"chatId": str(current.chat_id), "workspacePath": str(selected)})
    with pytest.raises(ValueError):
        runtime.dispatch("work.workspace.get", {"chatId": "chat_wrong"})
    selected.rename(tmp_path / "original")
    selected.mkdir()
    with pytest.raises(ValueError):
        runtime.boundary(str(current.chat_id))


def test_workspace_rpc_reaches_runtime_and_chat_mode_refuses_it(tmp_path: Path) -> None:
    """Authenticated bridge requests use the runtime rather than an isolated demo."""

    factory, _ = _transcription_runner_factory(_ControlledTranscriber(RuntimeError("Unused transcription")))
    backend, brain, output = _initialized_backend(tmp_path, factory)
    backend._runtime_settings = replace(backend._runtime_settings, data_root=tmp_path / "application-data")
    chat_id = str(brain.chat.chat_id)
    request = _request("work-chat-denied", "work.workspace.get", {"chatId": chat_id})
    assert backend._handle_line(json.dumps(request))
    assert _output_messages(output)[-1]["error"]["code"] == "work.denied"
    backend._handle_line(json.dumps(_request("mode-work", "chat.mode", {"chatId": chat_id, "mode": "work"})))
    selected = tmp_path / "selected"
    selected.mkdir()
    backend._handle_line(json.dumps(_request("bind-work", "work.workspace.bind", {"chatId": chat_id, "workspacePath": str(selected)})))
    assert _output_messages(output)[-1]["result"]["workspacePath"] == str(selected)
    backend._handle_line(json.dumps(_request("clear-work", "work.workspace.bind", {"chatId": chat_id, "workspacePath": None})))
    assert _output_messages(output)[-1]["result"]["workspacePath"] is None


@pytest.mark.parametrize("params", [{}, {"chatId": "../outside"}, {"chatId": "chat_test", "path": "/tmp"}])
def test_work_wire_refuses_extra_or_invalid_fields(params: dict[str, object]) -> None:
    """Schema validation rejects arbitrary filesystem capability extensions."""

    with pytest.raises(ProtocolValidationError):
        build_request("test", "work.workspace.get", params)


def test_scope_result_has_discriminant_and_no_extra_native_fields() -> None:
    """Workspace state cannot be mistaken for a Chat reply or expose native handles."""

    result = {"kind": "work.workspace", "chatId": "chat_test", "workspacePath": None}
    assert build_success_response("work", result)["result"] == result
    with pytest.raises(ProtocolValidationError):
        build_success_response("work", {**result, "handle": 123})


def test_catalog_declares_strict_typed_tools_and_rejects_tampering(tmp_path: Path) -> None:
    """Active Work exposes trusted schemas and cannot accept forged command extensions."""

    current = _chat()
    runtime = DesktopWorkRuntime(tmp_path, lambda: current)
    result = runtime.dispatch("work.tools.list", {"chatId": str(current.chat_id)})
    assert [tool["toolId"] for tool in result["tools"]] == ["list_directory", "read_text", "search_files", "metadata", "create", "edit", "move", "delete"]
    assert build_success_response("catalog", result)["result"] == result
    result["tools"][0]["risk"] = "critical"
    with pytest.raises(ProtocolValidationError):
        build_success_response("catalog", result)


def test_tools_cannot_bind_their_own_permission_or_application_storage(tmp_path: Path) -> None:
    """Editing a binding journal cannot expand access to an unselected directory."""

    current = _chat()
    private = tmp_path / "application-data"
    private.mkdir()
    runtime = DesktopWorkRuntime(private / "work", lambda: current, protected_roots=(private,))
    for selected in (private, tmp_path):
        with pytest.raises(PermissionError):
            runtime.dispatch("work.workspace.bind", {"chatId": str(current.chat_id), "workspacePath": str(selected)})
    assert not (private / "work" / "bindings").exists()


def test_real_bound_read_returns_verified_bytes_and_refuses_unapproved_writes(tmp_path: Path) -> None:
    """The desktop path reads the selected file and cannot execute a write call."""

    current = _chat()
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "notes.txt").write_text("original", encoding="utf-8")
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: current)
    owner = {"chatId": str(current.chat_id)}
    runtime.dispatch("work.workspace.bind", {**owner, "workspacePath": str(selected)})
    result = runtime.dispatch("work.tool.run", {**owner, "call": {"toolId": "read_text", "arguments": {"path": "notes.txt"}}})
    assert result["result"]["data"]["content"] == "original"
    assert build_success_response("read", result)["result"] == result
    denied = runtime.dispatch("work.tool.run", {**owner, "call": {"toolId": "edit", "arguments": {"path": "notes.txt", "content": "unapproved"}}})
    assert denied["result"]["error"]["code"] == "permission_required"
    assert (selected / "notes.txt").read_text("utf-8") == "original"


def test_write_preview_shows_exact_change_and_does_not_change_selected_file(tmp_path: Path) -> None:
    """Preview is integrated while file mutation still requires a separate permission decision."""

    current = _chat()
    selected = tmp_path / "selected"
    selected.mkdir()
    original = selected / "notes.txt"
    original.write_text("original\n", encoding="utf-8")
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: current)
    owner = {"chatId": str(current.chat_id)}
    runtime.dispatch("work.workspace.bind", {**owner, "workspacePath": str(selected)})
    call = {"toolId": "edit", "arguments": {"path": "notes.txt", "content": "replacement\n"}}
    state = runtime.dispatch("work.tool.preview", {**owner, "call": call})
    assert build_success_response("preview", state)["result"] == state
    assert state["approval"]["call"] == call
    assert "-original" in state["approval"]["preview"]
    assert "+replacement" in state["approval"]["preview"]
    assert original.read_text("utf-8") == "original\n"
    runtime.dispatch("work.workspace.bind", {**owner, "workspacePath": None})
    denied = runtime._get_writes().confirm(str(current.chat_id), None, WorkspaceBoundary(selected),
                                          state["approval"]["approvalId"], True)
    assert denied.status == "denied"


def test_confirm_requires_exact_current_action_and_refuses_replay(tmp_path: Path) -> None:
    """A declined or superseded action cannot later mutate the selected file."""

    current = _chat()
    selected = tmp_path / "selected"
    selected.mkdir()
    target = selected / "notes.txt"
    target.write_text("original", encoding="utf-8")
    runtime = DesktopWorkRuntime(tmp_path / "private", lambda: current)
    owner = {"chatId": str(current.chat_id)}
    runtime.dispatch("work.workspace.bind", {**owner, "workspacePath": str(selected)})
    call = {"toolId": "edit", "arguments": {"path": "notes.txt", "content": "declined"}}
    first = runtime.dispatch("work.tool.preview", {**owner, "call": call})["approval"]
    denied = runtime.dispatch("work.tool.confirm", {**owner, "approvalId": first["approvalId"], "granted": False})
    assert denied["result"]["status"] == "denied"
    assert target.read_text("utf-8") == "original"
    call["arguments"]["content"] = "approved modified action"
    second = runtime.dispatch("work.tool.preview", {**owner, "call": call})["approval"]
    request = {**owner, "approvalId": second["approvalId"], "granted": True}
    written = runtime.dispatch("work.tool.confirm", request)
    assert written["result"]["status"] == "succeeded"
    assert target.read_text("utf-8") == "approved modified action"
    assert runtime.dispatch("work.tool.confirm", request)["result"]["status"] == "denied"
    assert build_success_response("write", written)["result"] == written


def test_approval_cannot_be_retargeted_while_confirming() -> None:
    """The decision schema carries no path, content, or permanent-grant extension."""

    params = {"chatId": "chat_test", "approvalId": "approval_" + "0" * 32, "granted": True, "path": "other.txt"}
    with pytest.raises(ProtocolValidationError):
        build_request("retarget", "work.tool.confirm", params)
