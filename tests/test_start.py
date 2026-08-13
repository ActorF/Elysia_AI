from dataclasses import replace
from pathlib import Path

import pytest

import start
from core import (
    ConfigurationError,
    ModelConversationSummarizer,
    ModelMemoryExtractor,
)
from memory import ShortTermMemory


class FakeStartupChatModel:
    def __init__(
        self,
        model_name: str,
        ollama_host: str,
    ) -> None:
        self.model_name = model_name
        self.ollama_host = ollama_host

    def ensure_model_available(self) -> None:
        pass


@pytest.mark.parametrize("token_budget", [0, -1])
def test_validate_settings_rejects_invalid_token_budget(
    monkeypatch: pytest.MonkeyPatch,
    token_budget: int,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(
            start.SETTINGS,
            short_term_memory_token_budget=token_budget,
        ),
    )

    with pytest.raises(
        ConfigurationError,
        match=(
            r"SHORT_TERM_MEMORY_TOKEN_BUDGET "
            r"must be greater than zero\."
        ),
    ):
        start.validate_settings()


def test_create_brain_uses_configured_token_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(
            start.SETTINGS,
            base_dir=tmp_path,
            short_term_memory_token_budget=2,
        ),
    )
    monkeypatch.setattr(
        start,
        "LangChainOllamaChatModel",
        FakeStartupChatModel,
    )

    brain = start.create_brain()
    short_term_memory = brain._short_term_memory

    assert isinstance(
        short_term_memory,
        ShortTermMemory,
    )

    conversation_summarizer = (
        brain._conversation_summarizer
    )

    assert isinstance(
        conversation_summarizer,
        ModelConversationSummarizer,
    )
    assert (
        conversation_summarizer._chat_model
        is brain._chat_model
    )

    short_term_memory.remember_turn("aaaa", "bbbb")
    short_term_memory.remember_turn("cccc", "dddd")

    assert short_term_memory.get_turns() == [
        {
            "user_message": "cccc",
            "assistant_message": "dddd",
        }
    ]
    assert short_term_memory.get_token_count() == 2

    assert isinstance(
        brain._memory_extractor,
        ModelMemoryExtractor,
    )
