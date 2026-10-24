"""Verify desktop GPU ownership covers visible and hidden Ollama model calls.

Inert transport seams observe request residency, complete stream lifetime, and
embedding batches without contacting Ollama, loading weights, or using GPU.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import ModuleType
from typing import Any, cast

import pytest
from httpx import ConnectError
from langchain_core.messages import AIMessage, AIMessageChunk

from core import ChatModelConnectionError, ChatModelError, ChatModelResponseError
from core import langchain_ollama_chat_model as langchain_model
from core import ollama_chat_model as direct_model
from core.chat_model import ChatMessage
from documents import ollama_embedding as embedding_model
from documents.embedding import EmbeddingInput, EmbeddingRequest, EmbeddingUnavailableError
from gpu_admission import GpuAdmissionError


_MESSAGES: list[ChatMessage] = [{"role": "user", "content": "A local request."}]


def _install_gpu(monkeypatch: pytest.MonkeyPatch, module: ModuleType, events: list[str], *,
                 desktop: bool = True, refused: bool = False) -> None:
    @contextmanager
    def _lease(owner: str) -> Iterator[object]:
        assert owner == "ollama"
        if refused:
            raise GpuAdmissionError("private native admission detail")
        events.append("grant")
        try:
            yield object()
        finally:
            events.append("release")

    monkeypatch.setattr(module, "using_desktop_gpu", lambda: desktop)
    monkeypatch.setattr(module, "gpu_lease", _lease)


class _Sdk:
    """Observe LangChain invocation and owned iterator cleanup without HTTP."""

    def __init__(self, events: list[str], options: dict[str, Any], *, error: bool = False,
                 invalid: bool = False, thinking_only: bool = False) -> None:
        """Configure deterministic request chunks and native-like failure points."""

        self.events = events
        self.options = options
        self.error = error
        self.invalid = invalid
        self.thinking_only = thinking_only
        self.stream_options: list[dict[str, object]] = []

    def invoke(self, messages: object) -> AIMessage:
        """Return one synchronous result while the caller owns its GPU grant."""

        self.events.append("invoke")
        if self.error:
            raise ConnectError("private HTTP detail")
        return AIMessage(content="Local answer.", response_metadata={"done": True, "done_reason": "stop"})

    def stream(self, messages: object, **options: object) -> Iterator[AIMessageChunk]:
        """Retain an observable stream owner through exhaustion or explicit close."""

        self.stream_options.append(options)
        self.events.append("http-start")
        try:
            if self.invalid:
                yield AIMessageChunk(content=["invalid"])
            elif self.thinking_only:
                yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "private reasoning"})
            else:
                yield AIMessageChunk(content="First")
            if self.error:
                raise ConnectError("private HTTP detail")
            yield AIMessageChunk(content=" answer.")
            yield AIMessageChunk(content="", response_metadata={"done": True, "done_reason": "stop"})
        finally:
            self.events.append("http-close")


def _langchain(monkeypatch: pytest.MonkeyPatch, events: list[str], *, desktop: bool = True,
               error: bool = False, invalid: bool = False, thinking_only: bool = False,
               refused: bool = False) -> tuple[langchain_model.LangChainOllamaChatModel, _Sdk]:
    _install_gpu(monkeypatch, langchain_model, events, desktop=desktop, refused=refused)
    created: list[_Sdk] = []

    def _sdk(**options: Any) -> _Sdk:
        sdk = _Sdk(events, options, error=error, invalid=invalid, thinking_only=thinking_only)
        created.append(sdk)
        return sdk

    monkeypatch.setattr(langchain_model, "ChatOllama", _sdk)
    model = langchain_model.LangChainOllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    return model, created[0]


def test_direct_generation_including_hidden_calls_holds_gpu_for_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter boundary covers generation regardless of which application called it."""

    events: list[str] = []
    _install_gpu(monkeypatch, direct_model, events)

    def _request(self: object, method: str, route: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        assert (method, route) == ("POST", "/api/chat")
        assert payload is not None and payload["keep_alive"] == 0
        assert events == ["grant"]
        events.append("http")
        return {"message": {"content": "Answer."}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(direct_model.OllamaChatModel, "_request_json", _request)
    model = direct_model.OllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    assert model.generate_reply(_MESSAGES) == "Answer."
    assert events == ["grant", "http", "release"]


@pytest.mark.parametrize("adapter", ["direct", "langchain"])
@pytest.mark.parametrize(("origin", "expected"), [
    ("http://localhost:11434/", "http://127.0.0.1:11434"),
    ("http://[::1]:11434/", "http://[::1]:11434"),
])
def test_desktop_inference_origin_matches_main_residency_endpoint(
    monkeypatch: pytest.MonkeyPatch, adapter: str, origin: str, expected: str,
) -> None:
    """DNS aliases cannot send inference to a different daemon than Main unloads."""

    events: list[str] = []
    module = direct_model if adapter == "direct" else langchain_model
    _install_gpu(monkeypatch, module, events)
    if adapter == "direct":
        model = direct_model.OllamaChatModel("fixture:latest", origin)
        assert model._ollama_host == expected
    else:
        options: dict[str, Any] = {}

        def _sdk(**values: Any) -> _Sdk:
            options.update(values)
            return _Sdk(events, values)

        monkeypatch.setattr(langchain_model, "ChatOllama", _sdk)
        langchain_model.LangChainOllamaChatModel("fixture:latest", origin)
        assert options["base_url"] == expected


def test_desktop_localhost_request_uses_the_literal_ipv4_loopback_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real HTTP exchange uses the same literal endpoint as residency cleanup."""

    events: list[str] = []
    requests: list[tuple[str, str, object]] = []
    _install_gpu(monkeypatch, direct_model, events)

    class _Handler(BaseHTTPRequestHandler):
        """Answer only the local fixture request without loading a model."""

        def do_POST(self) -> None:
            """Record the actual HTTP authority and return one bounded answer."""

            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers["Host"], body))
            payload = b'{"message":{"content":"Local endpoint."},"done":true,"done_reason":"stop"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            """Keep the fixture's private request data out of test output."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_port
        model = direct_model.OllamaChatModel("fixture:latest", f"http://localhost:{port}")
        assert model.generate_reply(_MESSAGES) == "Local endpoint."
        assert requests == [("/api/chat", f"127.0.0.1:{port}", {
            "model": "fixture:latest", "messages": _MESSAGES, "stream": False,
            "think": False, "keep_alive": 0,
        })]
        assert events == ["grant", "release"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2.0)
        assert not worker.is_alive()


def test_direct_work_inference_uses_same_admission_and_zero_residency(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct Work answer cannot bypass admission by using its iterator facade."""

    events: list[str] = []
    _install_gpu(monkeypatch, direct_model, events)

    def _request(self: object, method: str, route: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        assert payload is not None and payload["think"] is True and payload["keep_alive"] == 0
        events.append("http")
        return {"message": {"content": "Work answer."}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(direct_model.OllamaChatModel, "_request_json", _request)
    model = direct_model.OllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    assert list(model.stream_work_reply(_MESSAGES, thinking=True)) == ["Work answer."]
    assert events == ["grant", "http", "release"]


def test_direct_legacy_payload_and_availability_probe_do_not_acquire_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console/library requests keep their defaults and metadata never loads weights."""

    events: list[str] = []
    _install_gpu(monkeypatch, direct_model, events, desktop=False)

    def _request(self: object, method: str, route: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if route == "/api/tags":
            return {"models": [{"name": "fixture:latest"}]}
        assert payload is not None and "keep_alive" not in payload
        return {"message": {"content": "Answer."}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(direct_model.OllamaChatModel, "_request_json", _request)
    model = direct_model.OllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    model.ensure_model_available()
    model.generate_reply(_MESSAGES)
    assert events == []
    monkeypatch.setattr(direct_model, "using_desktop_gpu", lambda: True)
    model.ensure_model_available()
    assert events == []


def test_direct_http_failure_releases_its_gpu_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transport errors leave no hidden global grant behind the failed request."""

    events: list[str] = []
    _install_gpu(monkeypatch, direct_model, events)

    def _request(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("http-error")
        raise ChatModelConnectionError("offline")

    monkeypatch.setattr(direct_model.OllamaChatModel, "_request_json", _request)
    model = direct_model.OllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    with pytest.raises(ChatModelConnectionError):
        model.generate_reply(_MESSAGES)
    assert events == ["grant", "http-error", "release"]


def test_langchain_generation_owns_gpu_and_constructor_disables_idle_residency(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hidden memory extraction through LangChain has the same physical admission."""

    events: list[str] = []
    model, sdk = _langchain(monkeypatch, events)
    assert sdk.options["keep_alive"] == 0
    assert model.generate_reply(_MESSAGES) == "Local answer."
    assert events == ["grant", "invoke", "release"]


def test_langchain_legacy_constructor_and_calls_keep_existing_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without Main's broker the SDK receives no new residency constructor field."""

    events: list[str] = []
    model, sdk = _langchain(monkeypatch, events, desktop=False)
    assert "keep_alive" not in sdk.options
    model.generate_reply(_MESSAGES)
    assert list(model.stream_reply(_MESSAGES)) == ["First", " answer."]
    assert events == ["invoke", "http-start", "http-close"]


def test_stream_grant_remains_held_until_exhaustion_closes_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yielding one token transfers no physical ownership back to the broker."""

    events: list[str] = []
    model, _sdk = _langchain(monkeypatch, events)
    chunks = model.stream_reply(_MESSAGES)
    assert events == []
    assert next(chunks) == "First"
    assert events == ["grant", "http-start"]
    assert list(chunks) == [" answer."]
    assert events == ["grant", "http-start", "http-close", "release"]


def test_explicit_stream_close_closes_owned_http_before_releasing_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Brain cancellation can close a paused stream without leaving native HTTP live."""

    events: list[str] = []
    model, _sdk = _langchain(monkeypatch, events)
    chunks = model.stream_reply(_MESSAGES)
    assert next(chunks) == "First"
    cast(Any, chunks).close()
    assert events == ["grant", "http-start", "http-close", "release"]


def test_work_thinking_chunks_keep_gpu_owned_without_publishing_private_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hidden thinking remains an empty cancellation point within the same grant."""

    events: list[str] = []
    model, sdk = _langchain(monkeypatch, events, thinking_only=True)
    chunks = model.stream_work_reply(_MESSAGES, thinking=True)
    assert next(chunks) == ""
    assert events == ["grant", "http-start"]
    assert sdk.stream_options == [{"reasoning": True}]
    cast(Any, chunks).close()
    assert events[-2:] == ["http-close", "release"]


def test_midstream_error_closes_http_before_global_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """A disconnected response cannot leave a grant or stream owner behind."""

    events: list[str] = []
    model, _sdk = _langchain(monkeypatch, events, error=True)
    chunks = model.stream_reply(_MESSAGES)
    assert next(chunks) == "First"
    with pytest.raises(ChatModelConnectionError):
        next(chunks)
    assert events == ["grant", "http-start", "http-close", "release"]


def test_invalid_stream_content_closes_http_before_releasing_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protocol rejection closes the underlying producer as promptly as cancellation."""

    events: list[str] = []
    model, _sdk = _langchain(monkeypatch, events, invalid=True)
    with pytest.raises(ChatModelResponseError):
        list(model.stream_reply(_MESSAGES))
    assert events == ["grant", "http-start", "http-close", "release"]


@pytest.mark.parametrize("adapter", ["direct", "langchain"])
def test_refused_gpu_admission_never_starts_inference(monkeypatch: pytest.MonkeyPatch, adapter: str) -> None:
    """Bounded broker refusal becomes a domain error without provider invocation."""

    events: list[str] = []
    if adapter == "direct":
        _install_gpu(monkeypatch, direct_model, events, refused=True)
        model: Any = direct_model.OllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    else:
        model, _sdk = _langchain(monkeypatch, events, refused=True)
    with pytest.raises(ChatModelError, match="Local GPU execution"):
        model.generate_reply(_MESSAGES)
    assert events == []


def _embedding(monkeypatch: pytest.MonkeyPatch, events: list[str], *, desktop: bool = True,
               refused: bool = False) -> embedding_model.OllamaEmbeddingAdapter:
    _install_gpu(monkeypatch, embedding_model, events, desktop=desktop, refused=refused)
    adapter = embedding_model.OllamaEmbeddingAdapter()

    def _request(self: object, method: str, route: str, **kwargs: object) -> dict[str, object]:
        if route == "/api/tags":
            events.append("tags")
            return {"models": [{"name": adapter.identity.model_tag, "digest": adapter.identity.model_digest}]}
        assert route == "/api/embed"
        payload = cast(dict[str, object], kwargs["payload"])
        assert payload.get("keep_alive") == 0 if desktop else "keep_alive" not in payload
        assert payload["input"] == ["Private indexed text."]
        events.append("embed")
        return {"model": adapter.identity.model_tag,
                "embeddings": [[1.0] + [0.0] * (adapter.identity.dimension - 1)]}

    monkeypatch.setattr(embedding_model.OllamaEmbeddingAdapter, "_request_json", _request)
    return adapter


def test_embedding_batch_owns_gpu_while_manifest_probes_remain_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Background indexing and retrieval cannot bypass the heavyweight admission rule."""

    events: list[str] = []
    adapter = _embedding(monkeypatch, events)
    result = adapter.embed(EmbeddingRequest(purpose="document", items=(EmbeddingInput("item", "Private indexed text."),)))
    assert result.vectors[0].item_id == "item"
    assert events == ["tags", "grant", "embed", "release", "tags"]


def test_legacy_embedding_keeps_existing_payload_and_admission_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standalone indexing remains compatible when the desktop broker is absent."""

    events: list[str] = []
    adapter = _embedding(monkeypatch, events, desktop=False)
    adapter.embed(EmbeddingRequest(purpose="document", items=(EmbeddingInput("item", "Private indexed text."),)))
    assert events == ["tags", "embed", "tags"]


def test_refused_embedding_admission_never_posts_private_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unavailable GPU returns the embedding domain failure before POST bytes."""

    events: list[str] = []
    adapter = _embedding(monkeypatch, events, refused=True)
    with pytest.raises(EmbeddingUnavailableError, match="Local GPU execution"):
        adapter.embed(EmbeddingRequest(purpose="document", items=(EmbeddingInput("item", "Private indexed text."),)))
    assert events == ["tags"]
