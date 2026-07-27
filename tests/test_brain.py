from pathlib import Path

import pytest

from core import Brain
from memory import Memory


class FakeChatModel:
    """Test model that does not require Ollama."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.received_message: str | None = None
        self.received_system_prompt: str | None = None

    def generate_reply(
        self,
        user_message: str,
        *,
        system_prompt: str,
    ) -> str:
        self.received_message = user_message
        self.received_system_prompt = system_prompt
        return self._reply

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
    assert (
        chat_model.received_message
        == "Hello, Elysia!"
    )

    system_prompt = chat_model.received_system_prompt

    assert system_prompt is not None
    assert "You are Elysia" in system_prompt
    assert "USER_PROFILE_JSON:" in system_prompt
    assert '"user_name": "Ying"' in system_prompt

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

    assert chat_model.received_message is None
    assert chat_model.received_system_prompt is None


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