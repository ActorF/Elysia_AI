"""Test application composition and startup validation."""

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
from projects import (
    ProjectChatService,
    ProjectRelationshipError,
    ProjectStorageError,
)
from recovery import DataPortabilityService


class FakeStartupChatModel:
    """Expose startup model configuration without contacting Ollama."""
    def __init__(
        self,
        model_name: str,
        ollama_host: str,
    ) -> None:
        """Initialize deterministic state for this test double."""
        self.model_name = model_name
        self.ollama_host = ollama_host

    def ensure_model_available(
        self,
    ) -> None:
        """Emulate or record model-availability validation."""
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
    """Verify that validate settings rejects invalid token budget."""
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
    """Verify that validate settings rejects invalid retrieval limit."""
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


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_validate_settings_rejects_invalid_import_size_limit(
    monkeypatch: pytest.MonkeyPatch,
    max_bytes: int,
) -> None:
    """Verify that validate settings rejects invalid import size limit."""
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(start.SETTINGS, data_import_max_bytes=max_bytes),
    )

    with pytest.raises(
        ConfigurationError,
        match="DATA_IMPORT_MAX_BYTES must be greater than zero",
    ):
        start.validate_settings()


def test_create_brain_uses_configured_token_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that create brain uses configured token budget."""
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
    project_service = brain._project_service
    assert isinstance(project_service, ProjectChatService)
    assert (
        project_service._chat_repository
        is active_service._chat_repository
    )
    assert (
        project_service._project_repository
        is active_service._project_repository
    )
    assert getattr(project_service._is_chat_busy, "__self__", None) is (
        active_service
    )

    chat = brain.create_chat(title="Startup Chat")
    assert brain.get_chat(chat.chat_id) == chat
    project = brain.create_project(name="Startup Project")
    assert brain.move_chat(chat.chat_id, project.project_id).project_id == (
        project.project_id
    )
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
    """Verify that create brain migrates legacy conversation once."""
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


def test_create_brain_records_permanent_legacy_chat_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a deleted migrated Chat absent across a fresh composition root."""

    legacy_file = (
        tmp_path
        / "workspace"
        / "conversations"
        / "conversation.json"
    )
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(
        json.dumps({"messages": [{
            "timestamp": "2026-08-01 12:00:00",
            "speaker": "User",
            "message": "Delete this migrated Chat",
        }]}),
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
    migrated = first_brain.list_chats()[0]

    first_brain.delete_chat(migrated.chat_id)
    second_brain = start.create_brain()

    assert second_brain.list_chats(include_archived=True) == ()
    state = json.loads(
        (
            tmp_path
            / "workspace"
            / "migrations"
            / "legacy_conversation_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert state["schema_version"] == 2
    assert state["chat_deleted"] is True


def test_create_brain_records_project_cascade_legacy_chat_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a migrated Chat deleted when its owning Project is cascaded."""

    legacy_file = (
        tmp_path
        / "workspace"
        / "conversations"
        / "conversation.json"
    )
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(
        json.dumps({"messages": [{
            "timestamp": "2026-08-01 12:00:00",
            "speaker": "User",
            "message": "Delete this Chat with its Project",
        }]}),
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
    migrated = first_brain.list_chats()[0]
    project = first_brain.create_project(name="Delete everything")
    first_brain.move_chat(migrated.chat_id, project.project_id)
    project_service = first_brain._project_service
    assert isinstance(project_service, ProjectChatService)

    project_service.delete_project(project.project_id, policy="cascade")
    second_brain = start.create_brain()

    assert second_brain.list_chats(include_archived=True) == ()
    state = json.loads(
        (
            tmp_path
            / "workspace"
            / "migrations"
            / "legacy_conversation_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert state["chat_deleted"] is True


def test_project_cascade_failure_restores_legacy_chat_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reverse both Chat deletion and its tombstone when cascade fails."""

    legacy_file = (
        tmp_path
        / "workspace"
        / "conversations"
        / "conversation.json"
    )
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(
        json.dumps({"messages": [{
            "timestamp": "2026-08-01 12:00:00",
            "speaker": "User",
            "message": "Restore me if Project deletion fails",
        }]}),
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
    migrated = first_brain.list_chats()[0]
    project = first_brain.create_project(name="Rollback cascade")
    assigned = first_brain.move_chat(
        migrated.chat_id,
        project.project_id,
    )
    project_service = first_brain._project_service
    assert isinstance(project_service, ProjectChatService)

    def fail_project_delete(_project_id: object) -> None:
        """Fail after the Chat lifecycle transaction has started."""

        raise ProjectStorageError("simulated Project deletion failure")

    monkeypatch.setattr(
        project_service._project_repository,
        "delete_project",
        fail_project_delete,
    )

    with pytest.raises(ProjectRelationshipError):
        project_service.delete_project(
            project.project_id,
            policy="cascade",
        )

    assert first_brain.get_chat(migrated.chat_id) == assigned
    state = json.loads(
        (
            tmp_path
            / "workspace"
            / "migrations"
            / "legacy_conversation_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert state["chat_deleted"] is False
    second_brain = start.create_brain()
    assert second_brain.get_chat(migrated.chat_id) == assigned


def test_create_data_portability_service_uses_configured_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that create data portability service uses configured limit."""
    monkeypatch.setattr(
        start,
        "SETTINGS",
        replace(
            start.SETTINGS,
            base_dir=tmp_path,
            data_import_max_bytes=1234,
        ),
    )

    service = start.create_data_portability_service()

    assert isinstance(service, DataPortabilityService)
    assert service.max_import_bytes == 1234
