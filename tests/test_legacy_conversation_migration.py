import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import chats.migration as migration_module
from chats import (
    JsonChatRepository,
    LegacyConversationMigrator,
    LegacyMigrationError,
)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def conversation_path(base_dir: Path) -> Path:
    return (
        base_dir
        / "workspace"
        / "conversations"
        / "conversation.json"
    )


def create_migrator(
    base_dir: Path,
) -> tuple[LegacyConversationMigrator, JsonChatRepository]:
    repository = JsonChatRepository(
        base_dir / "workspace" / "chats"
    )
    migrator = LegacyConversationMigrator(
        base_dir=base_dir,
        chat_repository=repository,
        model_name="qwen3.5:9b",
        clock=lambda: datetime(
            2026, 8, 23, 12, 0, tzinfo=timezone.utc
        ),
    )
    return migrator, repository


def write_legacy_conversation(base_dir: Path) -> None:
    write_json(
        conversation_path(base_dir),
        {
            "messages": [
                {
                    "timestamp": "2026-07-01 10:00:00",
                    "speaker": "User",
                    "message": "Remember this.",
                },
                {
                    "timestamp": "2026-07-01 10:00:05",
                    "speaker": "Elysia",
                    "message": "I will.",
                },
            ]
        },
    )


def test_migration_preserves_messages_summary_and_sources(
    tmp_path: Path,
) -> None:
    write_legacy_conversation(tmp_path)
    conversations = tmp_path / "workspace" / "conversations"
    write_json(
        conversations / "conversation_summary.json",
        {
            "schema_version": 1,
            "summary": {
                "content": {
                    "facts": ["The user asked Elysia to remember."],
                    "decisions": [],
                    "action_items": [],
                    "unresolved_questions": [],
                },
                "source_message_count": 2,
                "source_start_timestamp": "2026-07-01 10:00:00",
                "source_end_timestamp": "2026-07-01 10:00:05",
                "updated_at": "2026-07-01 10:00:05",
            },
        },
    )
    memory_dir = tmp_path / "workspace" / "memory"
    write_json(memory_dir / "profile.json", {"legacy": "profile"})
    write_json(
        memory_dir / "long_term_memory.json",
        {"memories": [{"legacy": "memory"}]},
    )
    migrator, repository = create_migrator(tmp_path)

    result = migrator.migrate()

    assert result.status == "migrated"
    assert result.chat_id is not None
    session = repository.get_chat(result.chat_id)
    assert session.title == "Legacy Conversation"
    assert [message.role for message in session.messages] == [
        "user", "assistant"
    ]
    assert [message.content for message in session.messages] == [
        "Remember this.", "I will."
    ]
    assert session.messages[0].created_at == datetime(
        2026, 7, 1, 10, 0, tzinfo=timezone.utc
    )
    assert session.summary is not None
    assert session.summary.facts == (
        "The user asked Elysia to remember.",
    )
    assert session.summary.source_message_ids == tuple(
        message.message_id for message in session.messages
    )

    state_path = (
        tmp_path
        / "workspace"
        / "migrations"
        / "legacy_conversation_v1.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    source_bytes = conversation_path(tmp_path).read_bytes()
    assert state["source_sha256"] == hashlib.sha256(
        source_bytes
    ).hexdigest()
    assert state["chat_id"] == str(result.chat_id)
    assert state["sources"]["summary"]["sha256"] is not None
    assert state["sources"]["profile"]["sha256"] is not None
    assert (
        state["sources"]["long_term_memory"]["sha256"]
        is not None
    )
    backup = Path(state["backup_path"])
    assert backup.read_bytes() == source_bytes


def test_migration_is_idempotent(tmp_path: Path) -> None:
    write_legacy_conversation(tmp_path)
    migrator, repository = create_migrator(tmp_path)

    first = migrator.migrate()
    second = migrator.migrate()

    assert first.status == "migrated"
    assert second.status == "already_migrated"
    assert second.chat_id == first.chat_id
    assert len(repository.list_chats()) == 1


def test_missing_or_empty_legacy_data_needs_no_migration(
    tmp_path: Path,
) -> None:
    migrator, repository = create_migrator(tmp_path)
    assert migrator.migrate().status == "not_needed"

    write_json(conversation_path(tmp_path), {"messages": []})
    assert migrator.migrate().status == "not_needed"
    assert repository.list_chats() == ()


def test_invalid_legacy_data_creates_neither_backup_nor_chat(
    tmp_path: Path,
) -> None:
    conversation_path(tmp_path).parent.mkdir(parents=True)
    conversation_path(tmp_path).write_text("not json", encoding="utf-8")
    migrator, repository = create_migrator(tmp_path)

    with pytest.raises(
        LegacyMigrationError,
        match="not valid UTF-8 JSON",
    ):
        migrator.migrate()

    assert repository.list_chats() == ()
    assert not (tmp_path / "workspace" / "migrations").exists()


def test_changed_source_is_not_imported_twice(tmp_path: Path) -> None:
    write_legacy_conversation(tmp_path)
    migrator, repository = create_migrator(tmp_path)
    migrator.migrate()
    write_json(
        conversation_path(tmp_path),
        {
            "messages": [{
                "timestamp": "2026-07-01 11:00:00",
                "speaker": "User",
                "message": "Changed after migration.",
            }]
        },
    )

    with pytest.raises(
        LegacyMigrationError,
        match="changed after migration",
    ):
        migrator.migrate()

    assert len(repository.list_chats()) == 1


def test_state_write_failure_rolls_back_created_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_legacy_conversation(tmp_path)
    migrator, repository = create_migrator(tmp_path)

    def fail_state_write(path: Path, data: object) -> None:
        raise OSError("simulated state failure")

    monkeypatch.setattr(
        migration_module,
        "atomic_write_json",
        fail_state_write,
    )

    with pytest.raises(
        LegacyMigrationError,
        match="new Chat was rolled back",
    ):
        migrator.migrate()

    assert repository.list_chats() == ()
    backup_directory = (
        tmp_path / "workspace" / "migrations" / "backups"
    )
    assert len(tuple(backup_directory.iterdir())) == 1
    assert not (
        tmp_path
        / "workspace"
        / "migrations"
        / "legacy_conversation_v1.json"
    ).exists()
