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
    MAX_WORK_CONTEXT_BYTES, MAX_WORK_STEPS, WorkThinkingUnavailableError, prepare_work_messages,
    stream_work_reply, validate_work_call, validate_work_options, validate_work_result, work_call_allowed,
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
            plan = self.plans.pop(0) if self.plans else {"tool": "answer", "arguments": {}}
            yield plan if isinstance(plan, str) else json.dumps(plan)
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
    """Retry replaces only the persisted answer pair even when full permission is submitted."""
    backend, brain, chat, output, model = _backend(tmp_path)
    try:
        _start(backend, chat, permission="none")
        assert backend._wait_for_generation(timeout=3)
        old = brain.get_chat(chat.chat_id).messages
        model.plans = [{"tool": "open_app", "arguments": {"application": "notepad"}}]
        _send(backend, "retry", "chat.retry", {"chatId": str(chat.chat_id), "userMessageId": str(old[0].message_id),
              "assistantMessageId": str(old[1].message_id), "speakReply": False,
              "workOptions": {"thinking": "deep", "permission": "full"}})
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
    assert len(calls) == MAX_WORK_STEPS


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
        yield AIMessageChunk(content="", response_metadata={"done": True, "done_reason": "stop"})
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


def _command_result(command="ver", *, code=0, termination="exit", stdout="Microsoft Windows", stderr="", cwd="C:\\Users"):
    succeeded = code == 0 and termination == "exit"
    return {"status": "succeeded" if succeeded else "cancelled" if termination == "cancelled" else "failed",
            "text": "CMD exited." if succeeded else "CMD did not succeed.",
            "error": None if succeeded else "cancelled" if termination == "cancelled" else "failed",
            "command": {"command": command, "cwd": cwd, "stdout": stdout, "stderr": stderr,
                        "exitCode": code, "termination": termination, "truncated": False}}


@pytest.mark.parametrize("permission,expected", [("none", False), ("read", False), ("auto", False), ("ask", True), ("full", True)])
def test_cmd_authority_requires_explicit_new_permission(permission, expected):
    """Old automatic desktop authority never silently acquires general shell access."""
    assert work_call_allowed(permission, "run_command") is expected
    assert validate_work_options({"thinking": "off", "permission": permission})["permission"] == permission
    if permission == "full":
        assert all(work_call_allowed(permission, tool) for tool in ("read_text", "get_volume", "open_app", "set_volume"))


@pytest.mark.parametrize("args", [
    {}, {"command": " "}, {"command": "echo\0secret"}, {"command": "\ud800"}, {"command": "爱" * 2731},
    {"command": "echo first\necho second"}, {"command": "echo first\recho second"},
    {"command": "echo first\r\necho second"},
    {"command": "ver", "cwd": "C:relative"}, {"command": "ver", "cwd": "\\relative"},
    {"command": "ver", "cwd": "\\\\server"}, {"command": "ver", "cwd": "C:\\" + "x" * 2046},
    {"command": "ver", "cwd": "\\\\server\\share\\folder"}, {"command": "ver", "cwd": "//server/share"},
    {"command": "ver", "timeoutMs": True}, {"command": "ver", "timeoutMs": 999},
    {"command": "ver", "timeoutMs": 120001}, {"command": "ver", "permission": "full"},
])
def test_cmd_arguments_reject_ambiguous_paths_and_unbounded_input(args):
    """Command data is closed, byte-bounded Unicode with explicit Windows directory semantics."""
    with pytest.raises(ValueError):
        validate_work_call("run_command", args)


@pytest.mark.parametrize("cwd", ["C:\\Users", "D:/Work Folder"])
def test_cmd_accepts_general_shell_syntax_and_absolute_directories(cwd):
    """General commands support shell syntax and optional drive-absolute directories without an allowlist."""
    args = {"command": "echo hello & ver", "cwd": cwd, "timeoutMs": 120000}
    assert validate_work_call("run_command", args) == ("run_command", args)
    assert validate_work_call("run_command", {"command": "ver"}) == ("run_command", {"command": "ver"})
    unc_command = {"command": 'dir "\\\\server\\share"', "cwd": cwd}
    assert validate_work_call("run_command", unc_command) == ("run_command", unc_command)


@pytest.mark.parametrize("changes", [
    {"exitCode": True}, {"exitCode": 4294967296}, {"exitCode": -2147483649}, {"exitCode": None},
    {"exitCode": 1}, {"termination": "timeout"}, {"stdout": "\0"}, {"stderr": "\ud800"},
    {"stdout": "爱" * 3000, "stderr": "爱" * 3000}, {"cwd": "relative"}, {"truncated": 1},
    {"command": "echo first\necho second"}, {"command": "echo first\recho second"},
    {"cwd": "\\\\server\\share"},
])
def test_cmd_receipts_reject_untruthful_or_oversized_results(changes):
    """Success requires actual exit zero and stream limits count both UTF-8 outputs together."""
    result = _command_result()
    result["command"].update(changes)
    with pytest.raises(ValueError):
        validate_work_result(result)


@pytest.mark.parametrize("code", [-2147483648, 0, 4294967295])
def test_cmd_exit_codes_cover_windows_native_range(code):
    """Native signed and unsigned process codes retain their original integer values."""
    assert validate_work_result(_command_result(code=code))["command"]["exitCode"] == code
    assert validate_work_result(_command_result(code=code, termination="timeout"))["command"]["exitCode"] == code


def test_cmd_receipts_preserve_multiline_process_output():
    """Only executable text is single-line; genuine stdout and stderr retain CR/LF separators."""
    result = _command_result(stdout="first\r\nsecond\n", stderr="warning\r\n")
    assert validate_work_result(result) == result


def test_cmd_failed_execution_can_be_corrected_using_real_stderr():
    """An unavailable executable exposes its actual failure before a distinct corrective command."""
    commands = ["wmic os get Caption", "ver"]
    model = _Model([{"tool": "run_command", "arguments": {"command": command}} for command in commands])
    calls = []
    def _run(tool, arguments):
        calls.append(arguments["command"])
        return _command_result(arguments["command"], code=9009 if len(calls) == 1 else 0,
                               stderr="wmic is not recognized" if len(calls) == 1 else "")
    messages = prepare_work_messages(model, [{"role": "user", "content": "请用 CMD 查询 Windows 版本"}],
                                     {"thinking": "off", "permission": "full"}, _run, None, None)
    assert calls == commands
    assert "wmic is not recognized" in model.prompts[1][-1]["content"]
    assert '"exitCode": 9009' in messages[-1]["content"]
    assert "Microsoft Windows" in messages[-1]["content"]


@pytest.mark.parametrize("first_plan", ["this is invalid JSON", {"tool": "answer", "arguments": {}}])
def test_explicit_cmd_request_repairs_invalid_or_premature_answer_once(first_plan):
    """A bad or premature plan gets one schema correction before any command can execute."""
    model = _Model([first_plan, {"tool": "run_command", "arguments": {"command": "ver"}}])
    calls = []
    def _run(tool, arguments):
        calls.append(arguments["command"])
        return _command_result(arguments["command"])
    prepare_work_messages(model, [{"role": "user", "content": "Run a CMD command to check Windows"}],
                          {"thinking": "off", "permission": "full"}, _run, None, None)
    assert calls == ["ver"] and len(model.prompts) == 3
    assert "Correction:" in model.prompts[1][0]["content"]


def test_repeated_invalid_cmd_planning_reports_failure_without_executing_user_text():
    """An explicit command request cannot fall back to executing arbitrary conversational text."""
    model = _Model([{"tool": "answer", "arguments": {}}, "bad json"])
    calls = []
    messages = prepare_work_messages(model, [{"role": "user", "content": "运行 CMD 帮我诊断系统"}],
                                     {"thinking": "off", "permission": "full"}, lambda *args: calls.append(args), None, None)
    assert calls == [] and len(model.prompts) == 2
    assert "invalid_request" in messages[-1]["content"]


def test_cmd_same_text_is_not_replayed_with_different_timeout_or_directory():
    """Changing metadata cannot repeat side effects after an uncertain execution failure."""
    model = _Model([{"tool": "run_command", "arguments": {"command": "ver", "timeoutMs": timeout}}
                    for timeout in (1000, 120000)])
    calls = []
    def _run(tool, arguments):
        calls.append(arguments)
        return _command_result(code=None, termination="timeout")
    prepare_work_messages(model, [{"role": "user", "content": "Check version"}],
                          {"thinking": "off", "permission": "full"}, _run, None, None)
    assert len(calls) == 1


@pytest.mark.parametrize("result", [
    {"status": "denied", "text": "No authorization", "error": "permission_denied"},
    _command_result(code=None, termination="cancelled"),
    {"status": "failed", "text": "Missing execution receipt", "error": "unavailable"},
])
def test_cmd_denied_cancelled_or_unconfirmed_result_stops_tools(result):
    """Only a confirmed ordinary command failure may lead to a corrective action."""
    model = _Model([{"tool": "run_command", "arguments": {"command": command}} for command in ("ver", "echo next")])
    calls = []
    def _run(tool, arguments):
        calls.append(arguments)
        return result
    prepare_work_messages(model, [{"role": "user", "content": "Check version"}],
                          {"thinking": "off", "permission": "ask"}, _run, None, None)
    assert len(calls) == 1 and len(model.prompts) == 1


@pytest.mark.parametrize("permission", ["read", "ask"])
def test_cmd_authorization_refusal_is_not_reported_as_windows_execution_failure(permission):
    """Keep app/user refusal distinct from OS execution and stop before a workaround."""

    model = _Model([
        {"tool": "run_command", "arguments": {"command": "date /t"}},
        {"tool": "run_command", "arguments": {"command": "runas /user:Administrator cmd"}},
    ])
    calls = []
    refusal = "The user selected No. The CMD command was never started. Windows permissions were not tested."

    def _run(tool, arguments):
        calls.append((tool, arguments))
        return {"status": "denied", "text": refusal, "error": "permission_denied"}

    messages = prepare_work_messages(model, [{"role": "user", "content": "用 CMD 查询日期"}],
                                     {"thinking": "off", "permission": permission}, _run, None, None)
    assert len(model.prompts) == 1
    assert len(calls) == (1 if permission == "ask" else 0)
    evidence = json.loads(messages[-1]["content"].split("\n", 1)[1])
    assert len(evidence["results"]) == 1
    result = evidence["results"][0]["result"]
    assert result["status"] == "denied" and "command" not in result
    assert result["text"] == refusal if permission == "ask" else "不是 Windows 系统权限错误" in result["text"]
    guidance = messages[-2]["content"]
    assert "not a Windows file-permission, administrator or UAC failure" in guidance
    assert "do not recommend administrator mode" in guidance
    assert "if the user selected No" in guidance


@pytest.mark.parametrize("thinking", ["off", "deep"])
@pytest.mark.parametrize("message,expected", [
    ("用 CMD 查询日期", "这条 CMD 命令未获授权，已跳过，没有执行。"),
    ("Use CMD to check today's date", "The CMD command was not approved, so it was skipped and not run."),
])
def test_sole_denied_cmd_commits_localized_acknowledgement_without_final_model(
    tmp_path, thinking, message, expected,
):
    """A user's No persists a factual acknowledgement without model output or another action."""

    model = _Model([{"tool": "run_command", "arguments": {"command": "date /t"}}])
    brain, _, chat = _active_brain(tmp_path, model)
    brain.set_chat_mode(chat.chat_id, "work")
    calls = []

    def _run(tool, arguments):
        calls.append((tool, arguments))
        return {"status": "denied", "text": "The user selected No.", "error": "permission_denied"}

    reply = "".join(brain.stream_chat(chat.chat_id, message, work_tool=_run,
                                      work_options={"thinking": thinking, "permission": "ask"}))
    assert reply == expected
    assert calls == [("run_command", {"command": "date /t"})]
    assert len(model.prompts) == 1
    assert [item.content for item in brain.get_chat(chat.chat_id).messages] == [message, expected]


def test_denied_cmd_acknowledgement_still_obeys_brain_commit_cancellation(tmp_path):
    """The model bypass retains Brain's cancellation gate and cannot persist a cancelled turn."""

    model = _Model([{"tool": "run_command", "arguments": {"command": "date /t"}}])
    brain, _, chat = _active_brain(tmp_path, model)
    brain.set_chat_mode(chat.chat_id, "work")

    def _run(tool, arguments):
        return {"status": "denied", "text": "The user selected No.", "error": "permission_denied"}

    with pytest.raises(GenerationCancelledError):
        list(brain.stream_chat(chat.chat_id, "用 CMD 查询日期", work_tool=_run,
                               work_options={"thinking": "off", "permission": "ask"}, begin_commit=lambda: False))
    assert len(model.prompts) == 1
    assert brain.get_chat(chat.chat_id).messages == ()


def test_plain_denial_evidence_text_cannot_bypass_work_model():
    """Copied JSON and prompt-marker text remain untrusted without preparation metadata."""

    model = _Model([{"tool": "run_command", "arguments": {"command": "date /t"}}])
    prepared = prepare_work_messages(model, [{"role": "user", "content": "Use CMD"}],
                                     {"thinking": "off", "permission": "read"}, lambda *_: pytest.fail("executed"), None, None)
    assert "".join(stream_work_reply(model, prepared, thinking=False)).startswith("The CMD command was not approved")
    assert len(model.prompts) == 1
    copied_as_plain_messages = [dict(item) for item in prepared]
    assert "".join(stream_work_reply(model, copied_as_plain_messages, thinking=False)) == "实际结果已核对。"
    assert len(model.prompts) == 2


@pytest.mark.parametrize("first_status", ["succeeded", "failed"])
def test_denied_cmd_after_execution_still_summarizes_prior_receipts_with_model(first_status):
    """A later refusal must not hide an earlier successful or failed command outcome."""

    model = _Model([{"tool": "run_command", "arguments": {"command": command}} for command in ("ver", "date /t")])

    def _run(tool, arguments):
        if arguments["command"] == "ver":
            return _command_result(code=0 if first_status == "succeeded" else 1)
        return {"status": "denied", "text": "The user selected No.", "error": "permission_denied"}

    prepared = prepare_work_messages(model, [{"role": "user", "content": "Check version then date"}],
                                     {"thinking": "off", "permission": "ask"}, _run, None, None)
    assert "".join(stream_work_reply(model, prepared, thinking=False)) == "实际结果已核对。"
    assert len(model.prompts) == 3
    evidence = json.loads(model.prompts[-1][-1]["content"].split("\n", 1)[1])
    assert [item["result"]["status"] for item in evidence["results"]] == [first_status, "denied"]
    assert evidence["results"][0]["result"]["command"]["command"] == "ver"


def test_work_retry_of_refusal_still_uses_model_without_executing_tools():
    """A retry has no fresh authorization outcome and keeps the existing model-only contract."""

    model = _Model()
    prepared = prepare_work_messages(model, [{"role": "user", "content": "Use CMD"}],
                                     {"thinking": "off", "permission": "ask"}, lambda *_: pytest.fail("executed"),
                                     None, None, retry=True)
    assert "".join(stream_work_reply(model, prepared, thinking=False)) == "实际结果已核对。"
    assert len(model.prompts) == 1


def test_cmd_model_context_is_bounded_while_actual_receipts_stay_complete():
    """Eight full stream receipts yield true marked excerpts within the aggregate context budget."""
    model = _Model([{"tool": "run_command", "arguments": {"command": "echo " + str(index)}} for index in range(10)])
    native = []
    def _run(tool, arguments):
        result = _command_result(arguments["command"], stdout="\r\n" * 4096, stderr="爱" * 2700)
        native.append(result)
        return result
    messages = prepare_work_messages(model, [{"role": "user", "content": "Inspect system"}],
                                     {"thinking": "off", "permission": "full"}, _run, None, None)
    results = json.loads(messages[-1]["content"].split("\n", 1)[1])["results"]
    assert len(native) == len(results) == MAX_WORK_STEPS
    assert len(json.dumps(results, ensure_ascii=False).encode("utf-8")) <= MAX_WORK_CONTEXT_BYTES
    assert all(item["contextTruncated"] for item in results)
    assert all(item["command"]["stdout"] == "\r\n" * 4096 for item in native)
    assert "Excerpt truncated for model context" in results[0]["result"]["command"]["stdout"]


def test_cmd_native_roundtrip_requires_matching_command_receipt(tmp_path):
    """Generation ownership and exact executable text bind CMD success before the model sees output."""
    backend, _, chat, output, model = _backend(tmp_path, [{"tool": "run_command", "arguments": {"command": "ver", "cwd": "C:\\Users"}}])
    try:
        _start(backend, chat, permission="full")
        event = _event(output)
        params = _resolve(event)
        _send(backend, "missing", "chat.work.resolve", params)
        assert _success_result(_output_messages(output), "missing")["accepted"] is False
        params["result"] = _command_result("echo different")
        _send(backend, "wrong-command", "chat.work.resolve", params)
        assert _success_result(_output_messages(output), "wrong-command")["accepted"] is False
        params["result"] = _command_result(cwd="C:\\Other")
        _send(backend, "wrong-cwd", "chat.work.resolve", params)
        assert _success_result(_output_messages(output), "wrong-cwd")["accepted"] is False
        params["result"] = _command_result()
        _send(backend, "right", "chat.work.resolve", params)
        assert _success_result(_output_messages(output), "right")["accepted"] is True
        assert backend._wait_for_generation(timeout=3)
        assert "Microsoft Windows" in model.prompts[-1][-1]["content"]
    finally:
        backend._require_attachment_store().close()


@pytest.mark.parametrize("message,expected_calls", [
    ("实际运行 CMD 查询系统，不要只给教程", 1),
    ("Run CMD to check Windows; do not just explain how.", 1),
    ("Run CMD without only giving a tutorial.", 1),
    ("不要执行命令，只讲解如何运行 CMD", 0),
    ("Do not run any commands; explain how to run CMD.", 0),
    ("Explain CMD without executing commands.", 0),
])
def test_cmd_repair_distinguishes_execution_negation_from_no_tutorial_requests(message, expected_calls):
    """Only negation of execution suppresses repair of a premature conversational answer."""
    model = _Model([{"tool": "answer", "arguments": {}}, {"tool": "run_command", "arguments": {"command": "ver"}}])
    calls = []
    def _run(tool, arguments):
        calls.append(arguments)
        return _command_result()
    prepare_work_messages(model, [{"role": "user", "content": message}],
                          {"thinking": "off", "permission": "full"}, _run, None, None)
    assert len(calls) == expected_calls


def test_cmd_native_bridge_accepts_all_eight_actions_and_marks_budget(tmp_path):
    """The wire owner and planner share the same eight-action ceiling and explicit exhaustion evidence."""
    plans = [{"tool": "run_command", "arguments": {"command": "echo " + str(index)}} for index in range(9)]
    backend, _, chat, output, model = _backend(tmp_path, plans)
    try:
        _start(backend, chat, permission="full")
        seen = set()
        for index in range(MAX_WORK_STEPS):
            deadline = monotonic() + 3
            event = None
            while monotonic() < deadline:
                event = next((item for item in _output_messages(output) if item.get("event") == "chat.work.request"
                              and item["data"]["callId"] not in seen), None)
                if event is not None:
                    break
                Event().wait(0.01)
            assert event is not None
            seen.add(event["data"]["callId"])
            params = _resolve(event)
            params["result"] = _command_result(event["data"]["arguments"]["command"], code=9009, stderr="Command unavailable")
            _send(backend, "step-" + str(index), "chat.work.resolve", params)
            assert _success_result(_output_messages(output), "step-" + str(index))["accepted"] is True
        assert backend._wait_for_generation(timeout=3)
        assert len(seen) == MAX_WORK_STEPS and len(model.plans) == 1
        assert '"stepBudgetExhausted": true' in model.prompts[-1][-1]["content"]
        assert "eight-action limit" in model.prompts[-1][-2]["content"]
    finally:
        backend._require_attachment_store().close()


def test_system_information_answer_requires_exact_receipt_facts_without_identity_inferences():
    """Kernel and account strings remain exact evidence and never imply OS branding or privileges."""
    outputs = {"ver": "Microsoft Windows [Version 10.0.26200.9711]\r\n", "hostname": "LabNode\r\n", "whoami": "lab\\alex\r\n"}
    model = _Model([{"tool": "run_command", "arguments": {"command": command}} for command in outputs])
    messages = prepare_work_messages(model, [{"role": "user", "content": "运行 CMD 查询版本、计算机名和当前用户"}],
                                     {"thinking": "off", "permission": "full"},
                                     lambda _tool, args: _command_result(args["command"], stdout=outputs[args["command"]]), None, None)
    results = json.loads(messages[-1]["content"].split("\n", 1)[1])["results"]
    assert {item["arguments"]["command"]: item["result"]["command"]["stdout"] for item in results} == outputs
    guidance = messages[-2]["content"]
    assert "Copy version/build numbers, hostnames and account identifiers exactly" in guidance
    assert "raw kernel version from ver does not establish a Windows marketing name, release or edition" in guidance
    assert "does not establish group membership, administrator rights or elevation" in guidance
    assert "Omit unsupported extra details, or clearly say they were not checked" in guidance
    assert "collect additional relevant read-only facts" in model.prompts[0][0]["content"]


def test_cmd_planner_scopes_actions_to_latest_request_instead_of_replaying_history():
    """Prior user requests remain reference context and cannot silently append commands to a new turn."""
    model = _Model()
    messages = [{"role": "user", "content": "运行 hostname 和 whoami"},
                {"role": "assistant", "content": "已查到计算机名和当前用户。"},
                {"role": "user", "content": "分两次执行 where missing_program，然后 ver；只做这两步。"}]
    prepare_work_messages(model, messages, {"thinking": "off", "permission": "full"},
                          lambda _tool, _args: _command_result(), None, None)
    prompt = model.prompts[0]
    context = json.loads(prompt[-1]["content"])
    assert context["latestMessage"] == messages[-1]["content"]
    assert context["recentMessages"][0] == messages[0]
    assert "latestMessage defines this turn's task and action scope" in prompt[0]["content"]
    assert "recentMessages only to resolve references" in prompt[0]["content"]
    assert "do not repeat earlier requests or append unrelated commands" in prompt[0]["content"]
    assert "explicit command count, order and steps" in prompt[0]["content"]
