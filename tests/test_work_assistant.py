"""Exercise Work turn authority, real receipts, cancellation and model thinking."""

import json
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_ollama import ChatOllama
from ollama import ResponseError

import desktop_backend as backend_module
from core.langchain_ollama_chat_model import LangChainOllamaChatModel
from core.exceptions import GenerationCancelledError
from core.work_assistant import (
    WorkThinkingUnavailableError, prepare_work_messages, validate_work_call, validate_work_options, validate_work_result,
)
from desktop_protocol import ProtocolValidationError, build_request
from tests.test_brain import _active_brain
from tests.test_desktop_backend import _initialized_backend, _output_messages, _request, _success_result


class _Model:
    """Serve closed plans before the final answer and record per-call thinking."""

    def __init__(self, plans=()):
        """Keep deterministic decisions without performing model or native I/O."""
        self.plans = list(plans)
        self.prompts = []
        self.thinking = []

    def stream_reply(self, messages):
        """Provide ordinary text for compatibility with unrelated Brain features."""
        yield "Answer."

    def stream_work_reply(self, messages, *, thinking):
        """Separate planner JSON from the public answer and observe request flags."""
        self.prompts.append(messages)
        self.thinking.append(thinking)
        if messages[0]["content"].startswith("Choose the next action"):
            yield json.dumps(self.plans.pop(0) if self.plans else {"tool": "answer", "arguments": {}})
        else:
            yield "实际结果已核对。"


def _backend(tmp_path: Path, plans=(), *, mode="work"):
    backend, _, output = _initialized_backend(tmp_path, lambda _: None)
    model = _Model(plans)
    brain, _, chat = _active_brain(tmp_path / "real", model)
    chat = brain.set_chat_mode(chat.chat_id, mode)
    backend._brain, backend._active_chat = brain, chat
    return backend, brain, chat, output, model


def _send(backend, request_id, method, params):
    assert backend._handle_line(json.dumps(_request(request_id, method, params)))


def _start(backend, chat, *, permission="ask", thinking="off"):
    _send(backend, "generation", "chat.stream", {
        "chatId": str(chat.chat_id), "message": "请完成这项任务", "speakReply": False,
        "workOptions": {"thinking": thinking, "permission": permission},
    })


def _event(output, name="chat.work.request"):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        item = next((entry for entry in _output_messages(output) if entry.get("event") == name), None)
        if item is not None:
            return item
        Event().wait(0.01)
    pytest.fail("Expected generation event was not emitted")


def _resolve(event, *, status="succeeded", text="Default output volume: 35%.", error=None):
    return {"requestId": event["requestId"], "chatId": event["data"]["chatId"], "callId": event["data"]["callId"],
            "result": {"status": status, "text": text, "error": error}}


def test_native_roundtrip_requires_exact_owner_and_commits_only_answer(tmp_path):
    """One tool receipt is consumed once; progress and planning never become reply tokens."""
    backend, brain, chat, output, model = _backend(tmp_path, [{"tool": "get_volume", "arguments": {}}])
    try:
        _start(backend, chat, permission="read", thinking="deep")
        event = _event(output)
        wrong = {**_resolve(event), "callId": "work_call_" + "0" * 32}
        _send(backend, "wrong", "chat.work.resolve", wrong)
        assert _success_result(_output_messages(output), "wrong")["accepted"] is False
        _send(backend, "resolved", "chat.work.resolve", _resolve(event))
        assert backend._wait_for_generation(timeout=3)
        _send(backend, "replay", "chat.work.resolve", _resolve(event))
        frames = _output_messages(output)
        assert _success_result(frames, "replay")["accepted"] is False
        assert _success_result(frames, "resolved")["accepted"] is True
        assert model.thinking and all(model.thinking)
        assert "35%" in model.prompts[-1][-1]["content"]
        assert "35%" in str([entry for entry in frames if entry.get("event") == "chat.work.progress"])
        streamed = "".join(entry["chunk"] for entry in frames if entry.get("type") == "stream")
        assert streamed == "实际结果已核对。"
        assert [item.content for item in brain.get_chat(chat.chat_id).messages] == ["请完成这项任务", streamed]
        assert _success_result(frames, "generation")["reply"] == streamed
    finally:
        backend._require_attachment_store().close()


def test_cancelling_native_wait_rejects_late_receipt_without_commit(tmp_path):
    """Stopping retains the generation until its worker exits and suppresses late results."""
    backend, brain, chat, output, model = _backend(tmp_path, [{"tool": "get_volume", "arguments": {}}])
    try:
        _start(backend, chat)
        event = _event(output)
        _send(backend, "cancel", "request.cancel", {"requestId": "generation"})
        assert backend._wait_for_generation(timeout=3)
        _send(backend, "late", "chat.work.resolve", _resolve(event))
        assert _success_result(_output_messages(output), "late")["accepted"] is False
        assert brain.get_chat(chat.chat_id).messages == ()
        assert len(model.prompts) == 1
    finally:
        backend._require_attachment_store().close()


def test_native_timeout_is_evidence_of_failure_not_success(tmp_path, monkeypatch):
    """A missing Main receipt yields an explicit uncertain outcome and stops actions."""
    monkeypatch.setattr(backend_module, "WORK_TOOL_TIMEOUT_SECONDS", 0.03)
    backend, _, chat, output, model = _backend(tmp_path, [{"tool": "get_volume", "arguments": {}}])
    try:
        _start(backend, chat)
        _event(output)
        assert backend._wait_for_generation(timeout=3)
        assert '"error": "timeout"' in model.prompts[-1][-1]["content"]
        assert len(model.prompts) == 2
    finally:
        backend._require_attachment_store().close()


@pytest.mark.parametrize("permission,plans,expected", [
    ("none", [{"tool": "set_volume", "arguments": {"level": 40}}], "results\": []"),
    ("read", [{"tool": "set_volume", "arguments": {"level": 40}}], "permission_denied"),
])
def test_permission_ceiling_cannot_be_overridden_by_planner(tmp_path, permission, plans, expected):
    """No-tools skips planning, and read-only blocks even a valid model write proposal."""
    backend, _, chat, output, model = _backend(tmp_path, plans)
    try:
        _start(backend, chat, permission=permission)
        assert backend._wait_for_generation(timeout=3)
        assert not any(entry.get("event") == "chat.work.request" for entry in _output_messages(output))
        assert expected in model.prompts[-1][-1]["content"]
        assert not any(model.thinking)
    finally:
        backend._require_attachment_store().close()


def test_canonical_chat_mode_rejects_work_options_before_model_or_tools(tmp_path):
    """A caller cannot turn an ordinary Chat into Work by forging its request options."""
    backend, brain, chat, output, model = _backend(tmp_path, mode="chat")
    try:
        _start(backend, chat)
        result = next(item for item in _output_messages(output) if item.get("id") == "generation")
        assert result["ok"] is False and result["error"]["code"] == "work.denied"
        assert model.prompts == [] and brain.get_chat(chat.chat_id).messages == ()
    finally:
        backend._require_attachment_store().close()


def test_file_search_uses_bound_workspace_and_reports_relative_paths(tmp_path):
    """Generation file reads reuse native Workspace scope and leave a redacted audit record."""
    backend, brain, chat, output, model = _backend(tmp_path, [{"tool": "search_files", "arguments": {"path": "", "query": "report"}}])
    workspace = tmp_path / "selected-files"
    workspace.mkdir()
    (workspace / "report.txt").write_text("private file body", encoding="utf-8")
    try:
        _send(backend, "bind", "work.workspace.bind", {"chatId": str(chat.chat_id), "workspacePath": str(workspace)})
        assert _success_result(_output_messages(output), "bind")["workspacePath"]
        _start(backend, chat, permission="read")
        assert backend._wait_for_generation(timeout=3)
        assert "report.txt" in model.prompts[-1][-1]["content"]
        phases = str([entry for entry in _output_messages(output) if entry.get("event") == "chat.work.progress"])
        assert "report.txt" in phases and "private file body" not in phases
        assert len(brain.get_chat(chat.chat_id).messages) == 2
        _send(backend, "audit", "work.audit.list", {"chatId": str(chat.chat_id)})
        assert _success_result(_output_messages(output), "audit")["entries"]
    finally:
        backend._require_attachment_store().close()


def test_retry_never_replays_tools_and_keeps_request_local_deep_thinking(tmp_path):
    """Retry replaces only the persisted answer pair even when auto permission is submitted."""
    backend, brain, chat, output, model = _backend(tmp_path)
    try:
        _start(backend, chat, permission="none")
        assert backend._wait_for_generation(timeout=3)
        old = brain.get_chat(chat.chat_id).messages
        model.plans = [{"tool": "open_app", "arguments": {"application": "notepad"}}]
        _send(backend, "retry", "chat.retry", {"chatId": str(chat.chat_id), "userMessageId": str(old[0].message_id),
              "assistantMessageId": str(old[1].message_id), "speakReply": False,
              "workOptions": {"thinking": "deep", "permission": "auto"}})
        assert backend._wait_for_generation(timeout=3)
        assert _success_result(_output_messages(output), "retry")
        assert model.thinking[-1] is True and model.plans
        assert "retryWithoutTools" in model.prompts[-1][-1]["content"]
        assert len(brain.get_chat(chat.chat_id).messages) == 2
        assert not any(item.get("event") == "chat.work.request" for item in _output_messages(output))
    finally:
        backend._require_attachment_store().close()


def test_planner_bounds_actions_and_never_repeats_identical_effect():
    """A looping or oversized model proposal cannot cause unbounded native callbacks."""
    prompt = [{"role": "user", "content": "Set the volume"}]
    action = {"tool": "set_volume", "arguments": {"level": 30}}
    calls = []
    def _run(tool, arguments):
        calls.append((tool, arguments))
        return {"status": "succeeded", "text": "Volume 30%", "error": None}
    prepare_work_messages(_Model([action, action]), prompt, {"thinking": "off", "permission": "auto"}, _run, None, None)
    assert len(calls) == 1
    calls.clear()
    plans = [{"tool": "set_volume", "arguments": {"level": value}} for value in range(10)]
    prepare_work_messages(_Model(plans), prompt, {"thinking": "off", "permission": "auto"}, _run, None, None)
    assert len(calls) == 4


@pytest.mark.parametrize("tool,args", [("shell", {"command": "calc"}), ("open_app", {"application": "C:/app.exe"}),
    ("set_volume", {"level": True}), ("set_volume", {"level": 101}), ("list_apps", {"permission": "auto"}),
    ("read_text", {"path": "../private.txt"})])
def test_closed_tool_contract_rejects_escalation_and_paths(tool, args):
    """Unknown operations, out-of-range native values and path escapes fail validation."""
    with pytest.raises(ValueError):
        validate_work_call(tool, args)


def test_work_result_and_options_reject_utf8_overflow_and_contradictions():
    """Cross-runtime ceilings use bytes and success cannot carry a failure code."""
    for result in [{"status": "succeeded", "text": "x", "error": "failed"},
                   {"status": "failed", "text": "x", "error": None},
                   {"status": "succeeded", "text": "爱" * 11000, "error": None}]:
        with pytest.raises(ValueError):
            validate_work_result(result)
    with pytest.raises(ValueError):
        validate_work_options({"thinking": "deep", "permission": "admin"})
    with pytest.raises(ProtocolValidationError):
        build_request("bad", "chat.stream", {"chatId": "chat_test", "message": "Hi", "speakReply": False,
                      "workOptions": {"thinking": "deep", "permission": "auto", "command": "calc"}})


def test_langchain_thinking_is_per_request_and_reasoning_is_not_answer(monkeypatch):
    """SDK invocation carries the actual thinking flag while private reasoning stays out of text."""
    flags = []
    def _stream(self, messages, **kwargs):
        flags.append(kwargs["reasoning"])
        yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "private model reasoning"})
        yield AIMessageChunk(content="Answer")
    monkeypatch.setattr(ChatOllama, "stream", _stream)
    model = LangChainOllamaChatModel("qwen3:8b", "http://localhost:11434")
    assert list(model.stream_work_reply([{"role": "user", "content": "Hi"}], thinking=True)) == ["", "Answer"]
    assert list(model.stream_reply([{"role": "user", "content": "Hi"}])) == ["Answer"]
    assert flags == [True, False] and model._langchain_model.reasoning is False


def test_model_rejecting_thinking_has_explicit_failure(monkeypatch):
    """Unsupported deep thinking must not silently continue with a normal generation."""
    def _stream(self, messages, **kwargs):
        raise ResponseError("model does not support thinking", status_code=400)
        yield
    monkeypatch.setattr(ChatOllama, "stream", _stream)
    model = LangChainOllamaChatModel("local-model", "http://localhost:11434")
    with pytest.raises(WorkThinkingUnavailableError):
        list(model.stream_work_reply([{"role": "user", "content": "Hi"}], thinking=True))


def test_cancel_during_reasoning_only_chunks_never_commits(tmp_path, monkeypatch):
    """Private reasoning chunks allow the owning generation to stop before answer tokens."""
    cancelled = Event()
    def _stream(self, messages, **kwargs):
        cancelled.set()
        yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hidden thoughts"})
        pytest.fail("The model iterator continued after its cancellation checkpoint")
    monkeypatch.setattr(ChatOllama, "stream", _stream)
    model = LangChainOllamaChatModel("local-model", "http://localhost:11434")
    brain, _, chat = _active_brain(tmp_path, model)
    brain.set_chat_mode(chat.chat_id, "work")
    with pytest.raises(GenerationCancelledError):
        list(brain.stream_chat(chat.chat_id, "Think", should_cancel=cancelled.is_set,
                              work_options={"thinking": "deep", "permission": "none"}))
    assert brain.get_chat(chat.chat_id).messages == ()
