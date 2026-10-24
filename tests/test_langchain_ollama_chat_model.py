"""Test the LangChain Ollama adapter and streamed error translation."""

from collections.abc import Iterator

import pytest
from httpx import ConnectError
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    BaseMessage,
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
    """Verify that ensure model available uses checker."""
    check_count = 0

    def fake_ensure_model_available(
        self: OllamaChatModel,
    ) -> None:
        """Count delegation to the direct Ollama availability checker."""
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
    """Verify that generate reply uses ordered chat messages."""
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        """Assert role-preserving LangChain conversion and return text."""
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
            content="  Hello, Ying!  ", response_metadata={"done": True, "done_reason": "stop"},
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
    """Verify that generate reply rejects non text content."""
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        """Return LangChain's non-text content shape for rejection coverage."""
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
    """Verify that generate reply rejects offline Ollama."""
    def fake_invoke(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> AIMessage:
        """Simulate an offline Ollama connection during synchronous invoke."""
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
    """Verify that stream reply yields ordered text chunks."""
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        """Assert ordered prompt conversion and yield three text chunks."""
        assert isinstance(messages, list)
        assert len(messages) == 2

        assert isinstance(messages[0], SystemMessage)
        assert messages[0].content == "You are Elysia."

        assert isinstance(messages[1], HumanMessage)
        assert messages[1].content == "Hello"

        yield AIMessageChunk(content="Hello")
        yield AIMessageChunk(content=" ")
        yield AIMessageChunk(content="Ying!")
        yield AIMessageChunk(content="", response_metadata={"done": True, "done_reason": "stop"})

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
    """Verify that stream reply rejects empty reply."""
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        """Yield only blank chunks so the adapter must reject an empty reply."""
        yield AIMessageChunk(content="")
        yield AIMessageChunk(content="   ")
        yield AIMessageChunk(content="", response_metadata={"done": True, "done_reason": "stop"})

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
    """Verify that stream reply rejects non text content."""
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        """Yield a non-text chunk for streamed response validation."""
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
    """Verify that stream reply translates midstream connection error."""
    def fake_stream(
        self: ChatOllama,
        messages: object,
        **kwargs: object,
    ) -> Iterator[AIMessageChunk]:
        """Yield partial text before simulating a midstream disconnect."""
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


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("terminal, expected_error", [
    ({"done": True, "done_reason": "stop"}, None),
    ({"done": True}, None),
    ({"done": True, "done_reason": "length"}, "output limit"),
    ({"done": True, "done_reason": "load"}, "complete response"),
    ({"done": True, "done_reason": "cancelled"}, "did not finish"),
    ({"done": False}, "complete response"),
    (None, "complete response"),
])
def test_installed_langchain_preserves_native_completion_contract(
    monkeypatch: pytest.MonkeyPatch, streaming: bool,
    terminal: dict[str, object] | None, expected_error: str | None,
) -> None:
    """Exercise real SDK metadata conversion and synthetic-last handling without inference.

    Only the native response iterator is replaced. Actual ChatOllama and
    LangChain aggregation must expose native done metadata for both invoke and
    stream; LangChain's own trailing empty chunk is not evidence of completion.
    """

    closed: list[bool] = []

    def _native_stream(self: ChatOllama, messages: list[BaseMessage], stop: list[str] | None = None,
                       **kwargs: object) -> Iterator[dict[str, object]]:
        try:
            yield {"message": {"role": "assistant", "content": "Partial answer"}, "done": False}
            if terminal is not None:
                yield {"message": {"role": "assistant", "content": ""}, **terminal}
        finally:
            closed.append(True)

    monkeypatch.setattr(ChatOllama, "_create_chat_stream", _native_stream)
    model = LangChainOllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    messages = [{"role": "user", "content": "Question"}]

    def _reply() -> str:
        return "".join(model.stream_reply(messages)) if streaming else model.generate_reply(messages)

    if expected_error is None:
        assert _reply() == "Partial answer"
    else:
        with pytest.raises(ChatModelResponseError, match=expected_error):
            _reply()
    assert closed == [True]


@pytest.mark.parametrize("after_terminal", [
    AIMessageChunk(content="late text"),
    AIMessageChunk(content="", response_metadata={"done": True, "done_reason": "stop"}),
])
def test_stream_rejects_content_or_another_terminal_after_completion(
    monkeypatch: pytest.MonkeyPatch, after_terminal: AIMessageChunk,
) -> None:
    """Prevent malformed provider streams from extending an already completed answer."""

    def _stream(self: ChatOllama, messages: object, **kwargs: object) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content="Answer", response_metadata={"done": True, "done_reason": "stop"})
        yield after_terminal

    monkeypatch.setattr(ChatOllama, "stream", _stream)
    model = LangChainOllamaChatModel("fixture:latest", "http://127.0.0.1:11434")
    with pytest.raises(ChatModelResponseError):
        list(model.stream_reply([{"role": "user", "content": "Question"}]))
