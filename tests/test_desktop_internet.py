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
from desktop_protocol import ProtocolValidationError, build_request
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


def _search_event(output):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        found = next((message for message in _output_messages(output)
                      if message.get("event") == "chat.internet.request"), None)
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
    task = SimpleNamespace(chat_id=chat.chat_id, should_cancel=lambda: False, begin_commit=lambda: True)
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
