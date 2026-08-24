from dataclasses import replace
import json
from pathlib import Path

import pytest

import start
from core import (
    ActiveConversationService,
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

    active_service = brain._active_conversation_service
    assert isinstance(active_service, ActiveConversationService)

    chat = brain.create_chat(title="Startup Chat")
    assert brain.get_chat(chat.chat_id) == chat
    assert (
        tmp_path
        / "workspace"
        / "chats"
        / "sessions"
        / f"{chat.chat_id}.json"
    ).exists()


def test_create_brain_migrates_legacy_conversation_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_file = (
        tmp_path
        / "workspace"
        / "conversations"
        / "conversation.json"
    )
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(
        json.dumps({
            "messages": [
                {
                    "timestamp": "2026-08-01 12:00:00",
                    "speaker": "User",
                    "message": "Legacy question",
                },
                {
                    "timestamp": "2026-08-01 12:00:01",
                    "speaker": "Elysia",
                    "message": "Legacy answer",
                },
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(start.SETTINGS, base_dir=tmp_path),
    )
    monkeypatch.setattr(
        start,
        "LangChainOllamaChatModel",
        FakeStartupChatModel,
    )

    first_brain = start.create_brain()
    second_brain = start.create_brain()

    first_chats = first_brain.list_chats()
    second_chats = second_brain.list_chats()
    assert len(first_chats) == 1
    assert second_chats == first_chats
    migrated = first_brain.get_chat(first_chats[0].chat_id)
    assert [message.content for message in migrated.messages] == [
        "Legacy question",
        "Legacy answer",
    ]
