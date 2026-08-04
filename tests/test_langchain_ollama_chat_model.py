from collections.abc import Iterator

import pytest
from httpx import ConnectError
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
)
from langchain_ollama import ChatOllama

from core import (
    ChatModelConnectionError,
    ChatModelResponseError,
    LangChainOllamaChatModel,
    OllamaChatModel,
)


def test_ensure_model_available_uses_checker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_count = 0

    def fake_ensure_model_available(
        self: OllamaChatModel,
    ) -> None:
        nonlocal check_count
        check_count += 1

    monkeypatch.setattr(
        OllamaChatModel,
        "ensure_model_available",
        fake_ensure_model_available,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    chat_model.ensure_model_available()

    assert check_count == 1

def test_generate_reply_uses_ordered_chat_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        assert isinstance(messages, list)
        assert len(messages) == 4

        assert isinstance(messages[0], SystemMessage)
        assert messages[0].content == "You are Elysia."

        assert isinstance(messages[1], HumanMessage)
        assert messages[1].content == "Previous question"

        assert isinstance(messages[2], AIMessage)
        assert messages[2].content == "Previous answer"

        assert isinstance(messages[3], HumanMessage)
        assert messages[3].content == "Current question"

        return AIMessage(
            content="  Hello, Ying!  "
        )

    monkeypatch.setattr(
        ChatOllama,
        "invoke",
        fake_invoke,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    reply = chat_model.generate_reply(
        [
            {
                "role": "system",
                "content": "  You are Elysia.  ",
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

def test_generate_reply_rejects_non_text_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        return AIMessage(content=["Hello"])

    monkeypatch.setattr(
        ChatOllama,
        "invoke",
        fake_invoke,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelResponseError,
        match="non-text content",
    ):
        chat_model.generate_reply(
            [
                {
                    "role": "system",
                    "content": "You are Elysia.",
                },
                {
                    "role": "user",
                    "content": "Hello",
                },
            ]
        )


def test_generate_reply_rejects_offline_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        raise ConnectError("Offline")

    monkeypatch.setattr(
        ChatOllama,
        "invoke",
        fake_invoke,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelConnectionError,
        match="Could not connect to Ollama",
    ):
        chat_model.generate_reply(
            [
                {
                    "role": "system",
                    "content": "You are Elysia.",
                },
                {
                    "role": "user",
                    "content": "Hello",
                },
            ]
        )


def test_stream_reply_yields_ordered_text_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        assert isinstance(messages, list)
        assert len(messages) == 2

        assert isinstance(messages[0], SystemMessage)
        assert messages[0].content == "You are Elysia."

        assert isinstance(messages[1], HumanMessage)
        assert messages[1].content == "Hello"

        yield AIMessageChunk(content="Hello")
        yield AIMessageChunk(content=" ")
        yield AIMessageChunk(content="Ying!")

    monkeypatch.setattr(
        ChatOllama,
        "stream",
        fake_stream,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    chunks = list(
        chat_model.stream_reply(
            [
                {
                    "role": "system",
                    "content": "  You are Elysia.  ",
                },
                {
                    "role": "user",
                    "content": "Hello",
                },
            ]
        )
    )

    assert chunks == ["Hello", " ", "Ying!"]
    assert "".join(chunks) == "Hello Ying!"

def test_stream_reply_rejects_empty_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content="")
        yield AIMessageChunk(content="   ")

    monkeypatch.setattr(
        ChatOllama,
        "stream",
        fake_stream,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelResponseError,
        match=r"LangChain returned an empty reply\.",
    ):
        list(
            chat_model.stream_reply(
                [
                    {
                        "role": "user",
                        "content": "Hello",
                    }
                ]
            )
        )

def test_stream_reply_rejects_non_text_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content=["Hello"])

    monkeypatch.setattr(
        ChatOllama,
        "stream",
        fake_stream,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    with pytest.raises(
        ChatModelResponseError,
        match=r"LangChain returned non-text content\.",
    ):
        list(
            chat_model.stream_reply(
                [
                    {
                        "role": "user",
                        "content": "Hello",
                    }
                ]
            )
        )

def test_stream_reply_translates_midstream_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content="Partial reply")
        raise ConnectError("Connection refused")

    monkeypatch.setattr(
        ChatOllama,
        "stream",
        fake_stream,
    )

    chat_model = LangChainOllamaChatModel(
        "qwen3.5:9b",
        "http://localhost:11434",
    )

    chunks = chat_model.stream_reply(
        [
            {
                "role": "user",
                "content": "Hello",
            }
        ]
    )

    assert next(chunks) == "Partial reply"

    with pytest.raises(
        ChatModelConnectionError,
        match=r"Could not connect to Ollama",
    ):
        next(chunks)
