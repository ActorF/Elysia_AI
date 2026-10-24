"""Test the direct Ollama chat-model adapter and failures."""

import pytest
import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
)

from core import (
    ChatModelConnectionError,
    ChatModelNotFoundError,
    ChatModelResponseError,
    OllamaChatModel,
)


class FakeResponse:
    """Small replacement for requests.Response."""

    def __init__(
        self,
        payload: object,
        status_code: int = 200,
    ) -> None:
        """Initialize deterministic state for this test double."""
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        """Return the configured fake JSON response."""
        return self._payload


def test_ensure_model_available_finds_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that ensure model available finds model."""
    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Validate the tags request and report the configured model present."""
        assert self.trust_env is False
        assert kwargs["allow_redirects"] is False
        assert method == "GET"
        assert url == (
            "http://127.0.0.1:11434/api/tags"
        )
        assert kwargs["timeout"] == 120.0
        assert kwargs["json"] is None

        return FakeResponse(
            {
                "models": [
                    {
                        "name": "qwen3.5:9b",
                        "model": "qwen3.5:9b",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        requests.Session,
        "request",
        fake_request,
    )

    chat_model = OllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434/",
    )

    chat_model.ensure_model_available()


def test_ensure_model_available_rejects_missing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that ensure model available rejects missing model."""
    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Return a tags payload that omits the requested model."""
        return FakeResponse(
            {
                "models": [
                    {"name": "another-model:latest"}
                ]
            }
        )

    monkeypatch.setattr(
        requests.Session,
        "request",
        fake_request,
    )

    chat_model = OllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelNotFoundError,
        match="is not installed",
    ):
        chat_model.ensure_model_available()


@pytest.mark.parametrize(
    ("requested", "installed"),
    [
        ("llama3.2", "llama3.2:latest"),
        ("llama3.2:latest", "llama3.2"),
        ("example/custom", "example/custom:latest"),
        ("localhost:5000/example/custom", "localhost:5000/example/custom:latest"),
    ],
)
@pytest.mark.parametrize("catalog_key", ["name", "model"])
def test_ensure_model_available_accepts_implicit_latest_tag(
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
    installed: str,
    catalog_key: str,
) -> None:
    """Accept the same default-tag aliases Ollama accepts for inference."""

    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Return one installed model under either supported catalog key."""
        return FakeResponse({"models": [{catalog_key: installed}]})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    OllamaChatModel(requested, "http://localhost:11434").ensure_model_available()


@pytest.mark.parametrize("requested", ["llama3.2", "llama3.2:latest"])
def test_ensure_model_available_does_not_substitute_another_tag(
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
) -> None:
    """Keep explicit installed variants distinct from the default latest tag."""

    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Report a different installed variant and malformed catalog entries."""
        return FakeResponse({"models": [None, {}, {"name": "llama3.2:1b"}]})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with pytest.raises(ChatModelNotFoundError, match="is not installed"):
        OllamaChatModel(requested, "http://localhost:11434").ensure_model_available()


def test_generate_reply_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that generate reply returns content."""
    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Validate the chat request and return a padded assistant reply."""
        assert self.trust_env is False
        assert kwargs["allow_redirects"] is False
        assert method == "POST"
        assert url == (
            "http://127.0.0.1:11434/api/chat"
        )
        assert kwargs["json"] == {
            "model": "qwen3.5:9b",
            "messages": [
                {
                    "role": "system",
                    "content": "You are Elysia.",
                },
                {
                    "role": "user",
                    "content": "Previous question",
                },
                {
                    "role": "assistant",
                    "content": "Previous answer",
                },
                {
                    "role": "user",
                    "content": "Current question",
                },
            ],
            "stream": False,
            "think": False,
        }

        return FakeResponse(
            {
                "message": {
                    "role": "assistant",
                    "content": "  Hello, Ying!  ",
                },
                "done": True,
                "done_reason": "stop",
            }
        )

    monkeypatch.setattr(
        requests.Session,
        "request",
        fake_request,
    )

    chat_model = OllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )
    reply = chat_model.generate_reply(
        [
            {
                "role": "system",
                "content": "You are Elysia.",
            },
            {
                "role": "user",
                "content": "Previous question",
            },
            {
                "role": "assistant",
                "content": "Previous answer",
            },
            {
                "role": "user",
                "content": "Current question",
            },
        ]
    )

    assert reply == "Hello, Ying!"


@pytest.mark.parametrize("metadata, expected_error", [
    ({"done": True, "done_reason": "stop"}, None),
    ({"done": True}, None),
    ({"done": True, "done_reason": "length"}, "output limit"),
    ({"done": True, "done_reason": "load"}, "did not finish"),
    ({"done": True, "done_reason": "cancelled"}, "did not finish"),
    ({"done": False}, "without a complete response"),
    ({"done": 1}, "without a complete response"),
    ({}, "without a complete response"),
])
def test_direct_reply_requires_a_successful_terminal_response(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, object], expected_error: str | None,
) -> None:
    """Never commit partial HTTP 200 text while retaining legacy done-only replies."""

    def _request(self: requests.Session, *args: object, **kwargs: object) -> FakeResponse:
        return FakeResponse({"message": {"role": "assistant", "content": "Partial answer"}, **metadata})

    monkeypatch.setattr(requests.Session, "request", _request)
    model = OllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    if expected_error is None:
        assert model.generate_reply([{"role": "user", "content": "Question"}]) == "Partial answer"
    else:
        with pytest.raises(ChatModelResponseError, match=expected_error):
            model.generate_reply([{"role": "user", "content": "Question"}])


def test_completion_diagnostics_exclude_untrusted_content(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Log only bounded counts and a fixed reason instead of provider-controlled fields."""

    def _request(self: requests.Session, *args: object, **kwargs: object) -> FakeResponse:
        return FakeResponse({"message": {"role": "assistant", "content": "private answer"},
                             "done": True, "done_reason": "private provider reason",
                             "prompt_eval_count": "private prompt", "eval_count": 3})

    monkeypatch.setattr(requests.Session, "request", _request)
    caplog.set_level("INFO", logger="core.ollama_chat_model")
    with pytest.raises(ChatModelResponseError, match="did not finish"):
        OllamaChatModel("fixture:latest", "http://127.0.0.1:11434").generate_reply(
            [{"role": "user", "content": "private question"}])
    assert "reason=unknown" in caplog.text and "output_tokens=3" in caplog.text
    assert "private" not in caplog.text


def test_request_rejects_offline_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that request rejects offline Ollama."""
    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Simulate an offline Ollama endpoint for request error mapping."""
        raise RequestsConnectionError("Offline")

    monkeypatch.setattr(
        requests.Session,
        "request",
        fake_request,
    )

    chat_model = OllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelConnectionError,
        match="Could not connect to Ollama",
    ):
        chat_model.ensure_model_available()


def test_generate_reply_rejects_invalid_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that generate reply rejects invalid content."""
    def fake_request(
        self: requests.Session,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Return a response whose assistant content is not textual."""
        return FakeResponse(
            {
                "message": {
                    "role": "assistant",
                    "content": 42,
                }
            }
        )

    monkeypatch.setattr(
        requests.Session,
        "request",
        fake_request,
    )

    chat_model = OllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelResponseError,
        match="has no text content",
    ):
        chat_model.generate_reply(
            [
                {
                    "role": "system",
                    "content": "You are Elysia.",
                },
                {
                    "role": "user",
                    "content": "Previous question",
                },
                {
                    "role": "assistant",
                    "content": "Previous answer",
                },
                {
                    "role": "user",
                    "content": "Current question",
                },
            ]
        )
