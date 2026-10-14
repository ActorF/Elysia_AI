"""Exercise authenticated Work binding, canonical mode gating, and strict DTOs."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from chats import ChatSession, create_chat_session
from desktop_work import DesktopWorkRuntime
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
    backend._runtime_settings = replace(backend._runtime_settings, data_root=tmp_path)
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
