import pytest
from httpx import ConnectError
from langchain_core.messages import (
    AIMessage,
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

def test_generate_reply_uses_system_and_human_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        assert isinstance(messages, list)
        assert len(messages) == 2

        assert isinstance(
            messages[0],
            SystemMessage,
        )
        assert (
            messages[0].content
            == "You are Elysia."
        )

        assert isinstance(
            messages[1],
            HumanMessage,
        )
        assert (
            messages[1].content
            == "Hello, Elysia!"
        )

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
        "  Hello, Elysia!  ",
        system_prompt="  You are Elysia.  ",
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
            "Hello",
            system_prompt="You are Elysia.",
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
            "Hello",
            system_prompt="You are Elysia.",
        )