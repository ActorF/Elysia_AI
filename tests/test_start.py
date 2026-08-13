from dataclasses import replace
from pathlib import Path

import pytest

import start
from core import (
    ConfigurationError,
    ModelConversationSummarizer,
    ModelMemoryExtractor,
)
from memory import (
    MemoryRetriever,
    ShortTermMemory,
)


class FakeStartupChatModel:
    def __init__(
        self,
        model_name: str,
        ollama_host: str,
    ) -> None:
        self.model_name = model_name
        self.ollama_host = ollama_host

    def ensure_model_available(
        self,
    ) -> None:
        pass


@pytest.mark.parametrize(
    "token_budget",
    [
        0,
        -1,
    ],
)
def test_validate_settings_rejects_invalid_token_budget(
    monkeypatch: pytest.MonkeyPatch,
    token_budget: int,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(
            start.SETTINGS,
            short_term_memory_token_budget=(
                token_budget
            ),
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


@pytest.mark.parametrize(
    "retrieval_limit",
    [
        0,
        -1,
    ],
)
def test_validate_settings_rejects_invalid_retrieval_limit(
    monkeypatch: pytest.MonkeyPatch,
    retrieval_limit: int,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(
            start.SETTINGS,
            memory_retrieval_limit=(
                retrieval_limit
            ),
        ),
    )

    with pytest.raises(
        ConfigurationError,
        match=(
            r"MEMORY_RETRIEVAL_LIMIT "
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

    short_term_memory = (
        brain._short_term_memory
    )

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

    short_term_memory.remember_turn(
        "aaaa",
        "bbbb",
    )
    short_term_memory.remember_turn(
        "cccc",
        "dddd",
    )

    assert short_term_memory.get_turns() == [
        {
            "user_message": "cccc",
            "assistant_message": "dddd",
        }
    ]
    assert (
        short_term_memory.get_token_count()
        == 2
    )

    assert isinstance(
        brain._memory_extractor,
        ModelMemoryExtractor,
    )

    memory_retriever = (
        brain._memory_retriever
    )

    assert isinstance(
        memory_retriever,
        MemoryRetriever,
    )
    assert (
        memory_retriever.result_limit
        == start.SETTINGS.memory_retrieval_limit
    )