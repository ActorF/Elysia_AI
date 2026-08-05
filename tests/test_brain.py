from collections.abc import Iterator
from pathlib import Path

import pytest

from core import Brain
from core.chat_model import ChatMessage
from memory import Memory, ShortTermMemory

class FakeChatModel:
    def __init__(
        self,
        reply: str,
        stream_chunks: list[str] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self._reply = reply
        self._stream_chunks = (
            stream_chunks
            if stream_chunks is not None
            else [reply]
        )
        self._stream_error = stream_error
        self.received_messages: list[ChatMessage] | None = None

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        self.received_messages = messages
        return self._reply

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        self.received_messages = messages
        yield from self._stream_chunks

        if self._stream_error is not None:
            raise self._stream_error


def test_chat_returns_reply_and_saves_messages(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    chat_model = FakeChatModel("Hello, Ying!")
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
    )

    reply = brain.chat("  Hello, Elysia!  ")

    assert reply == "Hello, Ying!"

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert len(received_messages) == 2

    assert received_messages[0]["role"] == "system"
    assert "你是 Elysia" in received_messages[0]["content"]
    assert (
        "USER_PROFILE_JSON:"
        in received_messages[0]["content"]
    )
    assert (
        '"user_name": "Ying"'
        in received_messages[0]["content"]
    )

    assert received_messages[1] == {
        "role": "user",
        "content": "Hello, Elysia!",
    }
    messages = memory.get_recent_messages(2)

    assert len(messages) == 2
    assert messages[0]["speaker"] == "Ying"
    assert (
        messages[0]["message"]
        == "Hello, Elysia!"
    )
    assert messages[1]["speaker"] == "Elysia"
    assert (
        messages[1]["message"]
        == "Hello, Ying!"
    )


def test_chat_rejects_empty_user_message(
    tmp_path: Path,
) -> None:
    chat_model = FakeChatModel("Unused reply")
    brain = Brain(
        "fake-model",
        Memory(tmp_path),
        chat_model,
    )

    with pytest.raises(
        ValueError,
        match=r"User message cannot be empty\.",
    ):
        brain.chat("   ")

    assert chat_model.received_messages is None


def test_chat_rejects_empty_model_reply(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
        FakeChatModel("   "),
    )

    with pytest.raises(
        ValueError,
        match=r"Model reply cannot be empty\.",
    ):
        brain.chat("Hello")

    assert memory.get_recent_messages() == []


def test_build_recent_context_maps_message_roles(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
    )

    memory.save_message("Ying", "First message")
    memory.save_message("Elysia", "Second message")
    memory.save_message("Unknown", "Ignored message")

    profile = memory.load_profile()

    context = brain._build_recent_context(
        profile,
        limit=3,
    )

    assert context == [
        {
            "role": "user",
            "content": "First message",
        },
        {
            "role": "assistant",
            "content": "Second message",
        },
    ]

def test_build_recent_context_respects_limit(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
    )

    memory.save_message("Ying", "First")
    memory.save_message("Elysia", "Second")
    memory.save_message("Ying", "Third")
    memory.save_message("Elysia", "Fourth")

    profile = memory.load_profile()

    context = brain._build_recent_context(
        profile,
        limit=2,
    )

    assert context == [
        {
            "role": "user",
            "content": "Third",
        },
        {
            "role": "assistant",
            "content": "Fourth",
        },
    ]

def test_build_chat_messages_orders_context(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
    )

    memory.save_message("Ying", "Previous question")
    memory.save_message("Elysia", "Previous answer")

    profile = memory.load_profile()

    messages = brain._build_chat_messages(
        profile,
        "Current question",
        limit=2,
    )

    assert messages[0]["role"] == "system"

    assert messages[1:] == [
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


def test_chat_includes_previous_turn_in_context(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    chat_model = FakeChatModel("First reply")

    brain = Brain(
        "fake-model",
        memory,
        chat_model,
    )

    brain.chat("First question")
    brain.chat("Second question")

    received_messages = chat_model.received_messages

    assert received_messages is not None

    assert received_messages[1:] == [
        {
            "role": "user",
            "content": "First question",
        },
        {
            "role": "assistant",
            "content": "First reply",
        },
        {
            "role": "user",
            "content": "Second question",
        },
    ]


def test_stream_chat_yields_chunks_and_saves_complete_turn(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Hello", " ", "Ying!"],
    )
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
    )

    chunks = list(
        brain.stream_chat("  Hello, Elysia!  ")
    )

    assert chunks == ["Hello", " ", "Ying!"]

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert received_messages[-1] == {
        "role": "user",
        "content": "Hello, Elysia!",
    }

    messages = memory.get_recent_messages(2)

    assert len(messages) == 2
    assert messages[0]["speaker"] == "Ying"
    assert messages[0]["message"] == "Hello, Elysia!"
    assert messages[1]["speaker"] == "Elysia"
    assert messages[1]["message"] == "Hello Ying!"


def test_stream_chat_does_not_save_partial_turn_on_stream_error(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Partial reply"],
        stream_error=RuntimeError(
            "Streaming interrupted."
        ),
    )
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
    )

    stream = brain.stream_chat("Hello")

    assert next(stream) == "Partial reply"

    with pytest.raises(
        RuntimeError,
        match=r"Streaming interrupted\.",
    ):
        next(stream)

    assert memory.get_recent_messages(2) == []

def test_chat_uses_and_updates_short_term_memory(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    short_term_memory.remember_turn(
        "Previous question",
        "Previous answer",
    )

    chat_model = FakeChatModel("Current answer")
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
        short_term_memory=short_term_memory,
    )

    reply = brain.chat("Current question")

    assert reply == "Current answer"

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert received_messages[1:] == [
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

    assert short_term_memory.get_turns() == [
        {
            "user_message": "Previous question",
            "assistant_message": "Previous answer",
        },
        {
            "user_message": "Current question",
            "assistant_message": "Current answer",
        },
    ]


def test_short_term_memory_trimming_changes_context(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    short_term_memory = ShortTermMemory(
        token_budget=4,
    )

    short_term_memory.remember_turn("aaaa", "bbbb")
    short_term_memory.remember_turn("cccc", "dddd")
    short_term_memory.remember_turn("eeee", "ffff")

    brain = Brain(
        "fake-model",
        memory,
        short_term_memory=short_term_memory,
    )

    messages = brain._build_chat_messages(
        memory.load_profile(),
        "gggg",
    )

    assert messages[1:] == [
        {
            "role": "user",
            "content": "cccc",
        },
        {
            "role": "assistant",
            "content": "dddd",
        },
        {
            "role": "user",
            "content": "eeee",
        },
        {
            "role": "assistant",
            "content": "ffff",
        },
        {
            "role": "user",
            "content": "gggg",
        },
    ]


def test_new_short_term_session_ignores_saved_history(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.save_message("Ying", "Old question")
    memory.save_message("Elysia", "Old answer")

    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    chat_model = FakeChatModel("Current answer")

    brain = Brain(
        "fake-model",
        memory,
        chat_model,
        short_term_memory=short_term_memory,
    )

    brain.chat("Current question")

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert received_messages[1:] == [
        {
            "role": "user",
            "content": "Current question",
        }
    ]


def test_chat_does_not_save_failed_short_term_turn(
    tmp_path: Path,
) -> None:
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    brain = Brain(
        "fake-model",
        Memory(tmp_path),
        FakeChatModel("   "),
        short_term_memory=short_term_memory,
    )

    with pytest.raises(
        ValueError,
        match=r"Model reply cannot be empty\.",
    ):
        brain.chat("Hello")

    assert short_term_memory.get_turns() == []


def test_stream_chat_saves_complete_short_term_turn(
    tmp_path: Path,
) -> None:
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Hello", " ", "Ying!"],
    )
    brain = Brain(
        "fake-model",
        Memory(tmp_path),
        chat_model,
        short_term_memory=short_term_memory,
    )

    chunks = list(brain.stream_chat("Hello, Elysia!"))

    assert chunks == ["Hello", " ", "Ying!"]
    assert short_term_memory.get_turns() == [
        {
            "user_message": "Hello, Elysia!",
            "assistant_message": "Hello Ying!",
        }
    ]


def test_stream_chat_does_not_save_partial_short_term_turn(
    tmp_path: Path,
) -> None:
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Partial reply"],
        stream_error=RuntimeError(
            "Streaming interrupted."
        ),
    )
    brain = Brain(
        "fake-model",
        Memory(tmp_path),
        chat_model,
        short_term_memory=short_term_memory,
    )

    stream = brain.stream_chat("Hello")

    assert next(stream) == "Partial reply"

    with pytest.raises(
        RuntimeError,
        match=r"Streaming interrupted\.",
    ):
        next(stream)

    assert short_term_memory.get_turns() == []
