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
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Validate the tags request and report the configured model present."""
        assert method == "GET"
        assert url == (
            "http://localhost:11434/api/tags"
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
        requests,
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
        requests,
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


def test_generate_reply_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that generate reply returns content."""
    def fake_request(
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Validate the chat request and return a padded assistant reply."""
        assert method == "POST"
        assert url == (
            "http://localhost:11434/api/chat"
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
                }
            }
        )

    monkeypatch.setattr(
        requests,
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


def test_request_rejects_offline_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that request rejects offline Ollama."""
    def fake_request(
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        """Simulate an offline Ollama endpoint for request error mapping."""
        raise RequestsConnectionError("Offline")

    monkeypatch.setattr(
        requests,
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
        requests,
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
