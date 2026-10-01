"""Test the strict loopback Ollama grounded-answer adapter offline."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from threading import Event
from typing import Any, cast

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import documents.ollama_grounding as ollama_grounding
from documents import (
    GROUNDED_ANSWER_SCHEMA_VERSION,
    GROUNDED_PROMPT_TEMPLATE_VERSION,
    DocumentOperationCancelledError,
    GroundedAnswerFailedError,
    GroundedAnswerGeneratorPolicy,
    GroundedAnswerRequest,
    GroundedAnswerUnavailableError,
    GroundedPromptMessage,
    OllamaGroundedAnswerAdapter,
    OllamaGroundedAnswerConfig,
)
from documents.grounding import (
    _SYSTEM_PROMPT,
    _passage_id,
    _request_fingerprint,
)


_MODEL = "fixture-grounded:1"
_DIGEST = hashlib.sha256(b"fixture-grounded-model").hexdigest()


class _FakeSocket:
    """Record read deadlines applied by the hardened response reader."""

    def __init__(self) -> None:
        """Create an empty timeout history."""

        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        """Record one positive remaining wall-clock budget."""

        self.timeouts.append(timeout)


class _FakeConnection:
    """Expose the urllib3 connection shape used for socket deadlines."""

    def __init__(self, socket: _FakeSocket) -> None:
        """Retain the fake response socket."""

        self.sock = socket


class _FakeRaw:
    """Return one bounded body through the exact ``read1`` transport seam."""

    def __init__(self, body: bytes) -> None:
        """Create one unread body and an available fake socket."""

        self._body = body
        self.closed = False
        self.socket = _FakeSocket()
        self._connection = _FakeConnection(self.socket)

    def read1(self, size: int, *, decode_content: bool) -> bytes:
        """Return the body once and then mark the stream exhausted."""

        assert size == 64 * 1024
        assert decode_content is False
        if self.closed:
            return b""
        self.closed = True
        return self._body


class _FakeResponse:
    """Expose one strict identity-encoded JSON HTTP response."""

    def __init__(
        self,
        value: object,
        *,
        status_code: int = 200,
    ) -> None:
        """Encode a scripted value with unambiguous response metadata."""

        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.status_code = status_code
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        self.raw = _FakeRaw(body)
        self.closed = False

    def close(self) -> None:
        """Record deterministic release by the adapter."""

        self.closed = True


class _FakeSession:
    """Consume ordered responses while retaining complete request metadata."""

    def __init__(self, factory: _SessionFactory) -> None:
        """Bind one session to its shared script and call recorder."""

        self.factory = factory
        self.trust_env = True
        self.mounts: list[tuple[str, object]] = []

    def __enter__(self) -> _FakeSession:
        """Return the fake context-managed session."""

        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        """Leave the session without suppressing errors."""

        return None

    def mount(self, prefix: str, adapter: object) -> None:
        """Record the deadline-aware zero-retry adapter installation."""

        self.mounts.append((prefix, adapter))

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        """Record one direct request and return or raise its scripted result."""

        self.factory.calls.append(
            {"session": self, "method": method, "url": url, **kwargs}
        )
        if not self.factory.responses:
            raise AssertionError("Unexpected fake HTTP request.")
        value = self.factory.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        callback = self.factory.on_request
        if callback is not None:
            callback(method)
        return value


class _SessionFactory:
    """Create isolated sessions around one ordered response queue."""

    def __init__(
        self,
        responses: list[_FakeResponse | BaseException],
        *,
        on_request: Callable[[str], None] | None = None,
    ) -> None:
        """Retain scripted outcomes and initialize request history."""

        self.responses = responses
        self.on_request = on_request
        self.calls: list[dict[str, object]] = []
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        """Return one recording session for one adapter operation."""

        session = _FakeSession(self)
        self.sessions.append(session)
        return session


def _tags_response(digest: str = _DIGEST) -> _FakeResponse:
    """Return one exact installed model tag and manifest digest."""

    return _FakeResponse(
        {"models": [{"name": _MODEL, "model": _MODEL, "digest": digest}]}
    )


def _canonical_json(value: object) -> str:
    """Encode the canonical prompt spelling required by the domain contract."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _request(adapter: OllamaGroundedAnswerAdapter) -> GroundedAnswerRequest:
    """Build one valid self-bound grounded request for adapter-only tests."""

    identity = adapter.identity
    policy = GroundedAnswerGeneratorPolicy()
    citation_id = "citation_" + hashlib.sha256(b"citation").hexdigest()
    preferences = hashlib.sha256(b"preferences").hexdigest()
    envelope: dict[str, object] = {
        "schema_version": GROUNDED_ANSWER_SCHEMA_VERSION,
        "request_fingerprint": "0" * 64,
        "untrusted_data": {
            "question": "What is the local fact?",
            "answer_preferences": {
                "answer_style": "default",
                "style_guidance": None,
                "preferences_fingerprint": preferences,
            },
            "passages": [
                {
                    "passage_id": _passage_id(
                        "prose",
                        "The local fact is bounded.",
                    ),
                    "kind": "prose",
                    "text": "The local fact is bounded.",
                    "citation_ids": [citation_id],
                }
            ],
        },
    }
    provisional_messages = (
        GroundedPromptMessage("system", _SYSTEM_PROMPT),
        GroundedPromptMessage("user", _canonical_json(envelope)),
    )
    fingerprint = _request_fingerprint(
        identity,
        provisional_messages,
        (citation_id,),
        policy,
        preferences,
    )
    envelope["request_fingerprint"] = fingerprint
    return GroundedAnswerRequest(
        schema_version=GROUNDED_ANSWER_SCHEMA_VERSION,
        prompt_template_version=GROUNDED_PROMPT_TEMPLATE_VERSION,
        identity=identity,
        messages=(
            GroundedPromptMessage("system", _SYSTEM_PROMPT),
            GroundedPromptMessage("user", _canonical_json(envelope)),
        ),
        allowed_citation_ids=(citation_id,),
        policy=policy,
        preferences_fingerprint=preferences,
    )


def _answer_content(request: GroundedAnswerRequest) -> str:
    """Return one schema-shaped answer bound to the supplied request."""

    return _canonical_json(
        {
            "schema_version": GROUNDED_ANSWER_SCHEMA_VERSION,
            "request_fingerprint": request.request_fingerprint,
            "status": "insufficient_evidence",
            "statements": [],
        }
    )


def test_config_rejects_non_loopback_and_ambiguous_origins() -> None:
    """Remote, credential-bearing, and path-bearing origins fail closed."""

    for origin in (
        "https://127.0.0.1:11434",
        "http://example.com:11434",
        "http://user:secret@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
    ):
        with pytest.raises(ValueError):
            OllamaGroundedAnswerConfig(model_tag=_MODEL, base_url=origin)


def test_adapter_pins_digest_and_sends_closed_deterministic_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation checks the digest on both sides and exposes no model tools."""

    factory = _SessionFactory([_tags_response()])
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(model_tag=_MODEL)
    )
    request = _request(adapter)
    scripted = [
        _tags_response(),
        _FakeResponse(
            {
                "model": _MODEL,
                "done": True,
                "done_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": _answer_content(request),
                },
            }
        ),
        _tags_response(),
    ]
    factory.responses.extend(scripted)

    assert adapter.generate(request) == _answer_content(request)
    assert [call["method"] for call in factory.calls] == [
        "GET",
        "GET",
        "POST",
        "GET",
    ]
    payload = json.loads(cast(bytes, factory.calls[2]["data"]))
    assert payload == {
        "model": _MODEL,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "stream": False,
        "tools": [],
        "think": False,
        "truncate": False,
        "format": "json",
        "options": {"temperature": 0.0},
    }
    assert all(session.trust_env is False for session in factory.sessions)
    assert all(
        cast(bool, call["allow_redirects"]) is False
        and cast(bool, call["stream"]) is True
        and cast(dict[str, str], call["headers"])["Accept-Encoding"]
        == "identity"
        for call in factory.calls
    )
    assert all(response.closed for response in scripted)


def test_adapter_rejects_model_digest_change_after_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutable tag race discards the complete answer instead of publishing it."""

    factory = _SessionFactory([_tags_response()])
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(model_tag=_MODEL)
    )
    request = _request(adapter)
    factory.responses.extend(
        [
            _tags_response(),
            _FakeResponse(
                {
                    "model": _MODEL,
                    "done": True,
                    "done_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": _answer_content(request),
                    },
                }
            ),
            _tags_response(hashlib.sha256(b"replacement").hexdigest()),
        ]
    )

    with pytest.raises(
        GroundedAnswerUnavailableError,
        match="model identity changed",
    ):
        adapter.generate(request)


def test_adapter_discards_response_when_cancel_wins_during_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check the Event after synchronous Ollama inference and skip publication."""

    cancelled = Event()
    factory = _SessionFactory(
        [_tags_response()],
        on_request=lambda method: cancelled.set() if method == "POST" else None,
    )
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(model_tag=_MODEL)
    )
    request = _request(adapter)
    factory.responses.extend(
        [
            _tags_response(),
            _FakeResponse(
                {
                    "model": _MODEL,
                    "done": True,
                    "done_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": _answer_content(request),
                    },
                }
            ),
        ]
    )

    with pytest.raises(DocumentOperationCancelledError, match="cancelled"):
        adapter.generate(request, cancel_requested=cancelled.is_set)

    assert [call["method"] for call in factory.calls] == ["GET", "GET", "POST"]


def test_adapter_rejects_truncated_or_reasoning_bearing_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-stop completions and hidden reasoning fail before JSON publication."""

    factory = _SessionFactory([_tags_response()])
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(model_tag=_MODEL)
    )
    request = _request(adapter)
    factory.responses.extend(
        [
            _tags_response(),
            _FakeResponse(
                {
                    "model": _MODEL,
                    "done": True,
                    "done_reason": "length",
                    "message": {
                        "role": "assistant",
                        "content": _answer_content(request),
                        "thinking": "private chain",
                    },
                }
            ),
            _tags_response(),
        ]
    )

    with pytest.raises(
        GroundedAnswerFailedError,
        match="invalid response",
    ):
        adapter.generate(request)


def test_adapter_sanitizes_transport_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native paths and transport detail never cross the public error boundary."""

    secret = r"C:\Users\Private\model-manifest"
    factory = _SessionFactory([RequestsConnectionError(secret)])
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(model_tag=_MODEL)
    )

    with pytest.raises(GroundedAnswerUnavailableError) as captured:
        _ = adapter.identity
    assert secret not in str(captured.value)
    assert str(captured.value) == (
        "Local grounded-answer service is not running or reachable."
    )
    assert captured.value.__cause__ is None


def test_adapter_rejects_http_json_beyond_configured_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared JSON response over budget fails before parsing or publication."""

    factory = _SessionFactory([_tags_response()])
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(
            model_tag=_MODEL,
            max_response_body_bytes=64 * 1_024,
        )
    )
    request = _request(adapter)
    factory.responses.extend(
        [
            _tags_response(),
            _FakeResponse(
                {
                    "model": _MODEL,
                    "done": True,
                    "done_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "x" * (70 * 1_024),
                    },
                }
            ),
        ]
    )

    with pytest.raises(
        GroundedAnswerFailedError,
        match="invalid response",
    ):
        adapter.generate(request)


def test_adapter_rejects_duplicate_installed_tag_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conflicting same-tag manifests cannot select an arbitrary model."""

    factory = _SessionFactory(
        [
            _FakeResponse(
                {
                    "models": [
                        {"name": _MODEL, "digest": _DIGEST},
                        {
                            "name": _MODEL,
                            "digest": hashlib.sha256(b"other").hexdigest(),
                        },
                    ]
                }
            )
        ]
    )
    monkeypatch.setattr(ollama_grounding.requests, "Session", factory)
    adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(model_tag=_MODEL)
    )

    with pytest.raises(
        GroundedAnswerUnavailableError,
        match="model is unavailable",
    ):
        _ = adapter.identity
