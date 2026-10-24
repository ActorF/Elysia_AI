"""Verify native research receipts and scoped image export through the real NDJSON backend."""

import base64
import json
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

import desktop_backend as backend_module
from attachments import AttachmentScope
from desktop_protocol import ProtocolValidationError, build_event, build_request
from tests.test_brain import _active_brain
from tests.test_desktop_backend import _initialized_backend, _output_messages, _request, _success_result
from tests.test_internet_assistant import _Model, _evidence


def _backend(tmp_path: Path, *, plan: str | None = None):
    backend, fake, output = _initialized_backend(tmp_path, lambda _: None)
    model = None
    if plan is not None:
        model = _Model(plan)
        brain, _, chat = _active_brain(tmp_path / "real", model)
        backend._brain = brain
        backend._active_chat = chat
    else:
        brain, chat = fake, fake.chat
    return backend, brain, chat, output, model


def _send(backend, request_id, method, params):
    assert backend._handle_line(json.dumps(_request(request_id, method, params)))


def _search_event(output, request_id=None):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        found = next((message for message in _output_messages(output)
                      if message.get("event") == "chat.internet.request" and (request_id is None or message.get("requestId") == request_id)), None)
        if found is not None:
            return found
        Event().wait(0.01)
    pytest.fail("Research event was not emitted")


def _start(backend, chat):
    _send(backend, "generation", "chat.stream", {
        "chatId": str(chat.chat_id), "message": "Please research", "speakReply": False, "automaticInternet": True,
    })


def _resolve_params(event, *, error=None):
    return {"requestId": event["requestId"], "chatId": event["data"]["chatId"],
            "searchId": event["data"]["searchId"], "sources": _evidence()["sources"] if error is None else [], "error": error}


def test_roundtrip_search_receipt_is_owned_one_use_and_never_streams_planner(tmp_path: Path) -> None:
    """Only the current request's evidence can reach its answer and history."""
    backend, brain, chat, output, model = _backend(tmp_path, plan='{"action":"search","query":"public topic","language":"en"}')
    try:
        _start(backend, chat)
        event = _search_event(output)
        assert backend._generation_task is not None
        assert not any(message.get("type") == "stream" for message in _output_messages(output))
        wrong = {**_resolve_params(event), "requestId": "old-generation"}
        _send(backend, "wrong", "chat.internet.resolve", wrong)
        assert _success_result(_output_messages(output), "wrong")["accepted"] is False
        _send(backend, "resolved", "chat.internet.resolve", _resolve_params(event))
        assert backend._wait_for_generation(timeout=3)
        _send(backend, "replay", "chat.internet.resolve", _resolve_params(event))
        messages = _output_messages(output)
        assert _success_result(messages, "resolved")["accepted"] is True
        assert _success_result(messages, "replay")["accepted"] is False
        assert "public topic" not in "".join(item["chunk"] for item in messages if item.get("type") == "stream")
        assert len(brain.get_chat(chat.chat_id).messages) == 2
        assert "UNTRUSTED_PUBLIC_RESEARCH_JSON" in model.prompts[-1][-1]["content"]
        visible = "".join(item["chunk"] for item in messages if item.get("type") == "stream")
        assert "Retrieved sources" in visible
        assert _success_result(messages, "generation")["reply"] == visible
        assert brain.get_chat(chat.chat_id).messages[-1].content == visible
    finally:
        backend._require_attachment_store().close()


def test_cancel_waiting_search_ignores_late_result_and_does_not_commit(tmp_path: Path) -> None:
    """Cancellation wakes the same generation worker without committing a partial turn."""
    backend, brain, chat, output, model = _backend(tmp_path, plan='{"action":"search","query":"topic","language":"en"}')
    try:
        _start(backend, chat)
        event = _search_event(output)
        _send(backend, "cancel", "request.cancel", {"requestId": "generation"})
        assert backend._wait_for_generation(timeout=3)
        _send(backend, "late", "chat.internet.resolve", _resolve_params(event))
        assert _success_result(_output_messages(output), "late")["accepted"] is False
        assert brain.get_chat(chat.chat_id).messages == ()
        assert len(model.prompts) == 1
    finally:
        backend._require_attachment_store().close()


@pytest.mark.parametrize("cancel", [False, True])
def test_retry_research_uses_a_fresh_owned_receipt_and_preserves_history_on_stop(tmp_path: Path, cancel: bool) -> None:
    """Retry traverses the real private research protocol, including its cancellation fence."""
    backend, brain, chat, output, model = _backend(tmp_path, plan="Original answer")
    try:
        list(brain.stream_chat(chat.chat_id, "Original question"))
        before = brain.get_chat(chat.chat_id).messages
        model.responses = ['{"action":"search","query":"replacement fact","language":"en"}', "Replacement answer"]
        _send(backend, "retry-research", "chat.retry", {
            "chatId": str(chat.chat_id), "userMessageId": str(before[-2].message_id),
            "assistantMessageId": str(before[-1].message_id), "speakReply": False,
            "message": "Please verify a replacement fact", "automaticInternet": True,
        })
        event = _search_event(output, "retry-research")
        assert event["data"]["query"] == "replacement fact"
        assert brain.get_chat(chat.chat_id).messages == before
        if cancel:
            _send(backend, "cancel-retry", "request.cancel", {"requestId": "retry-research"})
            assert backend._wait_for_generation(timeout=3)
        _send(backend, "retry-evidence", "chat.internet.resolve", _resolve_params(event))
        assert backend._wait_for_generation(timeout=3)
        assert _success_result(_output_messages(output), "retry-evidence")["accepted"] is not cancel
        after = brain.get_chat(chat.chat_id).messages
        if cancel:
            assert after == before
        else:
            assert len(after) == 2
            assert after[-2].message_id == before[-2].message_id
            assert after[-2].content == "Please verify a replacement fact"
            assert "Replacement answer" in after[-1].content and "Retrieved sources" in after[-1].content
    finally:
        backend._require_attachment_store().close()


def test_timeout_answers_with_explicit_missing_verification(tmp_path: Path, monkeypatch) -> None:
    """An absent Main reply cannot block local Chat forever or create false evidence."""
    monkeypatch.setattr(backend_module, "INTERNET_SEARCH_TIMEOUT_SECONDS", 0.03)
    backend, _, chat, output, model = _backend(tmp_path, plan='{"action":"search","query":"topic","language":"en"}')
    try:
        _start(backend, chat)
        event = _search_event(output)
        assert backend._wait_for_generation(timeout=3)
        assert '"error": "timeout"' in model.prompts[-1][-1]["content"]
        _send(backend, "late", "chat.internet.resolve", _resolve_params(event))
        assert _success_result(_output_messages(output), "late")["accepted"] is False
    finally:
        backend._require_attachment_store().close()


def test_disabled_research_continues_with_honest_local_answer(tmp_path: Path) -> None:
    """Turning Internet off resolves the research phase without fabricating sources."""
    backend, _, chat, output, model = _backend(tmp_path, plan='{"action":"search","query":"topic","language":"zh"}')
    try:
        _start(backend, chat)
        event = _search_event(output)
        _send(backend, "disabled", "chat.internet.resolve", _resolve_params(event, error="disabled"))
        assert backend._wait_for_generation(timeout=3)
        assert '"error": "disabled"' in model.prompts[-1][-1]["content"]
    finally:
        backend._require_attachment_store().close()


@pytest.mark.parametrize("suffix,media,payload", [
    ("png", "image/png", b"\x89PNG\r\n\x1a\nfixture"),
    ("jpg", "image/jpeg", b"\xff\xd8\xfffixture"),
])
def test_native_image_reads_verified_owned_snapshot_only(tmp_path: Path, suffix: str, media: str, payload: bytes) -> None:
    """Native Main gets image bytes and safe metadata, never the private source path."""
    backend, _, chat, output, _ = _backend(tmp_path)
    store = backend._require_attachment_store()
    try:
        source = tmp_path / ("picture." + suffix)
        source.write_bytes(payload)
        scope = AttachmentScope(kind="chat", id=str(chat.chat_id))
        staged = store.stage_files(scope, [source])
        params = {"scope": {"kind": "chat", "id": scope.id}, "attachmentId": staged.attachments[0].attachment_id}
        _send(backend, "image", "attachment.native_source", params)
        result = _success_result(_output_messages(output), "image")
        assert result == {"kind": "attachment.native_source", "fileName": source.name,
                          "mediaType": media, "dataBase64": base64.b64encode(payload).decode("ascii")}
        _send(backend, "wrong-chat", "attachment.native_source", {**params, "scope": {"kind": "chat", "id": "chat_other"}})
        error = next(item for item in _output_messages(output) if item.get("id") == "wrong-chat")
        assert error["error"]["code"] == "chat.not_active"
        assert str(source) not in json.dumps(_output_messages(output))
    finally:
        store.close()


@pytest.mark.parametrize("name,payload", [("notes.txt", b"not image"), ("fake.png", b"not png")])
def test_native_image_rejects_unsupported_type_and_header(tmp_path: Path, name: str, payload: bytes) -> None:
    """A display extension cannot authorize unsupported or spoofed image content."""
    backend, _, chat, output, _ = _backend(tmp_path)
    store = backend._require_attachment_store()
    try:
        source = tmp_path / name
        source.write_bytes(payload)
        scope = AttachmentScope(kind="chat", id=str(chat.chat_id))
        staged = store.stage_files(scope, [source])
        _send(backend, "invalid-image", "attachment.native_source", {
            "scope": {"kind": "chat", "id": scope.id}, "attachmentId": staged.attachments[0].attachment_id,
        })
        result = next(item for item in _output_messages(output) if item.get("id") == "invalid-image")
        assert result["ok"] is False
        assert "dataBase64" not in json.dumps(result)
    finally:
        store.close()


def test_internal_commands_reject_unknown_fields_and_project_scope() -> None:
    """Closed Main-only contracts cannot accept renderer paths or public-network overrides."""
    with pytest.raises(ProtocolValidationError):
        build_request("image", "attachment.native_source", {"scope": {"kind": "project", "id": "project_test"}, "attachmentId": "attachment_test"})
    with pytest.raises(ProtocolValidationError):
        build_request("chat", "chat.stream", {"chatId": "chat_test", "message": "hi", "speakReply": False, "automaticInternet": True, "url": "https://example.org"})


def test_project_sources_routing_ignores_automatic_internet_flag(tmp_path: Path, monkeypatch) -> None:
    """Project citations keep their dedicated answerer even if Main supplies both flags."""
    backend, _, chat, _, _ = _backend(tmp_path)
    calls = []
    def _grounded(*args, **kwargs):
        calls.append((args, kwargs))
        return iter(["Grounded answer"])
    brain = SimpleNamespace(stream_grounded_chat=_grounded,
                            stream_chat=lambda *_args, **_kwargs: pytest.fail("ordinary Chat called"))
    task = SimpleNamespace(chat_id=chat.chat_id, should_cancel=lambda: False, begin_commit=lambda: True, work_options=None)
    monkeypatch.setattr(backend, "_reserve_generation", lambda *_args, **_kwargs: (brain, task))
    monkeypatch.setattr(backend, "_require_knowledge_runtime", lambda: SimpleNamespace(answers=SimpleNamespace(answer="local-answerer")))
    monkeypatch.setattr(backend, "_launch_reserved_generation", lambda owner, task, run: list(run(owner, task)))
    try:
        backend._start_chat_stream("grounded", {"chatId": str(chat.chat_id), "message": "Question", "speakReply": False,
                                                "useProjectKnowledge": True, "automaticInternet": True})
        assert len(calls) == 1
        assert calls[0][0][-1] == "local-answerer"
        assert "internet_research" not in calls[0][1]
    finally:
        backend._require_attachment_store().close()


def test_work_mode_native_image_export_remains_available(tmp_path: Path) -> None:
    """Local Work image review uses Chat-scoped bytes without enabling Internet."""
    backend, brain, chat, output, _ = _backend(tmp_path)
    work_chat = replace(chat, mode="work")
    brain._chats[chat.chat_id] = work_chat
    backend._active_chat = work_chat
    store = backend._require_attachment_store()
    try:
        source = tmp_path / "work.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        scope = AttachmentScope(kind="chat", id=str(chat.chat_id))
        staged = store.stage_files(scope, [source])
        _send(backend, "work-image", "attachment.native_source", {"scope": {"kind": "chat", "id": scope.id},
                                                                "attachmentId": staged.attachments[0].attachment_id})
        assert _success_result(_output_messages(output), "work-image")["mediaType"] == "image/png"
    finally:
        store.close()


def test_weather_request_preserves_period_and_emits_scoped_research_phases(tmp_path: Path) -> None:
    """Main receives a typed forecast request and UI phases under the same generation identity."""

    backend, brain, chat, output, _ = _backend(tmp_path, plan='{"action":"weather","query":"上海","language":"zh"}')
    try:
        _send(backend, "generation", "chat.stream", {
            "chatId": str(chat.chat_id), "message": "上海明天天气如何？", "speakReply": False, "automaticInternet": True,
        })
        event = _search_event(output)
        assert event["data"]["kind"] == "weather" and event["data"]["when"] == "tomorrow"
        params = _resolve_params(event)
        params["sources"] = _evidence("weather")["sources"]
        _send(backend, "resolved", "chat.internet.resolve", params)
        assert backend._wait_for_generation(timeout=3)
        phases = [item for item in _output_messages(output) if item.get("event") == "chat.research.status"]
        assert [item["data"]["stage"] for item in phases] == ["planning", "weather", "answering"]
        assert all(item["requestId"] == "generation" and item["data"]["chatId"] == str(chat.chat_id) for item in phases)
        assert len(brain.get_chat(chat.chat_id).messages) == 2
    finally:
        backend._require_attachment_store().close()


@pytest.mark.parametrize("extra", [{"kind": "shell"}, {"when": "tomorrow"}, {"kind": "search", "when": "now"}, {"kind": "weather", "when": "yesterday"}])
def test_internet_event_rejects_unknown_capabilities_and_weather_period_on_search(extra: dict) -> None:
    """Optional weather routing does not open generic tools or unrelated search parameters."""

    with pytest.raises(ProtocolValidationError):
        build_event("chat.internet.request", request_id="generation", data={
            "chatId": "chat_fixture", "searchId": "internet_search_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "query": "Shanghai", "language": "en", **extra,
        })


def test_research_status_rejects_provider_text_in_the_fixed_phase_contract() -> None:
    """The UI phase wire cannot carry a query, raw error or arbitrary model output."""

    with pytest.raises(ProtocolValidationError):
        build_event("chat.research.status", request_id="generation", data={"chatId": "chat_fixture", "stage": "run shell"})
    with pytest.raises(ProtocolValidationError):
        build_event("chat.research.status", request_id="generation", data={"chatId": "chat_fixture", "stage": "planning", "query": "private"})


@pytest.mark.parametrize("automatic", [{}, {"automaticInternet": False}])
@pytest.mark.parametrize("question", ["请查询上海现在的天气。", "请上网查询Python最新版本"])
def test_real_backend_offline_chat_instructs_enabling_internet_without_model_generation(tmp_path: Path, automatic: dict, question: str) -> None:
    """An omitted or false native capability follows the actual offline desktop path safely."""

    backend, brain, chat, output, model = _backend(tmp_path, plan="Should not generate or plan")
    try:
        _send(backend, "offline-generation", "chat.stream", {
            "chatId": str(chat.chat_id), "message": question, "speakReply": False, **automatic,
        })
        assert backend._wait_for_generation(timeout=3)
        messages = _output_messages(output)
        reply = _success_result(messages, "offline-generation")["reply"]
        assert "允许联网" in reply
        assert not any(item.get("event") in ("chat.internet.request", "chat.research.status") for item in messages)
        assert model.prompts == []
        assert brain.get_chat(chat.chat_id).messages[-1].content == reply
    finally:
        backend._require_attachment_store().close()


def test_real_backend_preserves_user_weather_qualifiers_after_geocoder_clarification(tmp_path: Path) -> None:
    """Three actual NDJSON turns retain the full user tuple even when the model repeats a city-only query."""

    backend, brain, chat, output, model = _backend(tmp_path, plan='{"action":"clarify","query":null,"language":"zh"}')
    try:
        _send(backend, "weather-question", "chat.stream", {"chatId": str(chat.chat_id), "message": "现在天气怎么样？", "speakReply": False, "automaticInternet": True})
        assert backend._wait_for_generation(timeout=3)
        model.responses = ['{"action":"weather","query":"上海","language":"zh"}']
        _send(backend, "weather-city", "chat.stream", {"chatId": str(chat.chat_id), "message": "上海", "speakReply": False, "automaticInternet": True})
        city_event = _search_event(output, "weather-city")
        city_result = _resolve_params(city_event)
        city_result["sources"] = _evidence("weather")["sources"]
        city_result["sources"][0]["url"] = "https://geocoding-api.open-meteo.com/v1/search?name=Shanghai"
        city_result["sources"][0]["snippet"] = "多个地点匹配；未取得天气数据。"
        _send(backend, "city-result", "chat.internet.resolve", city_result)
        assert backend._wait_for_generation(timeout=3)
        assert brain.get_chat(chat.chat_id).messages[-1].content.startswith("这个地点名称尚未能唯一确定。")
        model.responses = ['{"action":"weather","query":"上海","language":"zh"}', "上海天气结果"]
        _send(backend, "weather-qualified", "chat.stream", {"chatId": str(chat.chat_id), "message": "上海，上海市，CN", "speakReply": False, "automaticInternet": True})
        qualified = _search_event(output, "weather-qualified")
        assert qualified["data"]["query"] == "上海，上海市，CN"
        result = _resolve_params(qualified)
        result["sources"] = _evidence("weather")["sources"]
        _send(backend, "qualified-result", "chat.internet.resolve", result)
        assert backend._wait_for_generation(timeout=3)
        assert model.closed == 4
        assert "上海天气结果" in _success_result(_output_messages(output), "weather-qualified")["reply"]
        assert len(brain.get_chat(chat.chat_id).messages) == 6
    finally:
        backend._require_attachment_store().close()
