import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chats import (
    ChatDataCorruptionError,
    ChatId,
    ChatMessageId,
    ChatNotFoundError,
    ChatRepository,
    ChatStorageError,
    ChatSummary,
    JsonChatRepository,
    ProjectId,
    create_attachment_metadata,
    create_chat_message,
)

BASE_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _clock(*times: datetime) -> Callable[[], datetime]:
    time_iterator = iter(times)
    return lambda: next(time_iterator)


def _storage_directory(tmp_path: Path) -> Path:
    return tmp_path / "data" / "chats"


def _session_file(storage_directory: Path, chat_id: ChatId) -> Path:
    return storage_directory / "sessions" / f"{chat_id}.json"


def test_complete_chat_survives_repository_restart(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository: ChatRepository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Elysia project",
        mode="work",
        model_name="qwen3.5:9b",
        project_id=ProjectId("project_123"),
    )
    attachment = create_attachment_metadata(
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=128,
    )
    user_message = create_chat_message(
        role="user",
        content="Read this file.",
        attachments=(attachment,),
        created_at=BASE_TIME + timedelta(seconds=1),
    )
    assistant_message = create_chat_message(
        role="assistant",
        content="I read the file.",
        created_at=BASE_TIME + timedelta(seconds=2),
    )
    summary = ChatSummary(
        facts=("The chat contains one text attachment.",),
        decisions=("Keep stable message IDs.",),
        action_items=(),
        unresolved_questions=(),
        source_message_ids=(
            user_message.message_id,
            assistant_message.message_id,
        ),
        updated_at=BASE_TIME + timedelta(seconds=2),
    )
    updated_chat = replace(
        chat,
        updated_at=BASE_TIME + timedelta(seconds=2),
        messages=(user_message, assistant_message),
        summary=summary,
    )

    repository.save_chat(updated_chat)

    restarted_repository = JsonChatRepository(storage_directory)
    assert restarted_repository.get_chat(chat.chat_id) == updated_chat
    assert (
        restarted_repository.list_chats()[0].message_count
        == 2
    )


def test_index_contains_metadata_but_not_messages(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Lightweight index",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    message = create_chat_message(
        role="user",
        content="Stored only in the detail file.",
        created_at=BASE_TIME + timedelta(seconds=1),
    )
    repository.save_chat(
        replace(
            chat,
            updated_at=message.created_at,
            messages=(message,),
        )
    )

    index_data = json.loads(
        (storage_directory / "index.json").read_text(
            encoding="utf-8"
        )
    )
    index_entry = index_data["chats"][0]

    assert index_entry["message_count"] == 1
    assert "messages" not in index_entry
    assert "summary" not in index_entry


def test_list_chats_does_not_read_corrupt_session_details(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Still listed",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    _session_file(storage_directory, chat.chat_id).write_text(
        '{"broken":',
        encoding="utf-8",
    )

    metadata_entries = repository.list_chats()

    assert [entry.chat_id for entry in metadata_entries] == [
        chat.chat_id
    ]
    with pytest.raises(ChatDataCorruptionError):
        repository.get_chat(chat.chat_id)


def test_pin_order_survives_repository_restart(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(
            BASE_TIME,
            BASE_TIME + timedelta(minutes=1),
        ),
    )
    older_chat = repository.create_chat(
        title="Older",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    newer_chat = repository.create_chat(
        title="Newer",
        mode="chat",
        model_name="qwen3.5:9b",
    )

    assert [
        entry.chat_id for entry in repository.list_chats()
    ] == [newer_chat.chat_id, older_chat.chat_id]

    repository.pin_chat(older_chat.chat_id)
    restarted_repository = JsonChatRepository(storage_directory)

    assert [
        entry.chat_id
        for entry in restarted_repository.list_chats()
    ] == [older_chat.chat_id, newer_chat.chat_id]
    assert restarted_repository.get_chat(
        older_chat.chat_id
    ).is_pinned is True


def test_archive_hides_and_restore_returns_chat(
    tmp_path: Path,
) -> None:
    repository = JsonChatRepository(
        _storage_directory(tmp_path),
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Archive me",
        mode="chat",
        model_name="qwen3.5:9b",
    )

    archived_metadata = repository.archive_chat(chat.chat_id)

    assert archived_metadata.is_archived is True
    assert repository.list_chats() == ()
    assert repository.list_chats(
        include_archived=True
    )[0].chat_id == chat.chat_id
    assert repository.get_chat(chat.chat_id).is_archived is True

    restored_metadata = repository.archive_chat(
        chat.chat_id,
        archived=False,
    )

    assert restored_metadata.is_archived is False
    assert repository.list_chats()[0].chat_id == chat.chat_id


def test_rename_preserves_id_messages_and_project(
    tmp_path: Path,
) -> None:
    repository = JsonChatRepository(
        _storage_directory(tmp_path),
        clock=_clock(
            BASE_TIME,
            BASE_TIME + timedelta(minutes=1),
        ),
    )
    chat = repository.create_chat(
        title="Old title",
        mode="work",
        model_name="qwen3.5:9b",
        project_id=ProjectId("project_123"),
    )
    message = create_chat_message(
        role="user",
        content="Keep me.",
        created_at=BASE_TIME + timedelta(seconds=1),
    )
    repository.save_chat(
        replace(
            chat,
            updated_at=message.created_at,
            messages=(message,),
        )
    )

    renamed_chat = repository.rename_chat(
        chat.chat_id,
        "New title",
    )

    assert renamed_chat.chat_id == chat.chat_id
    assert renamed_chat.title == "New title"
    assert renamed_chat.messages == (message,)
    assert renamed_chat.project_id == ProjectId("project_123")
    assert repository.list_chats()[0].title == "New title"


def test_delete_removes_index_and_detail_file(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Delete me",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    session_file = _session_file(storage_directory, chat.chat_id)

    repository.delete_chat(chat.chat_id)

    assert repository.list_chats(include_archived=True) == ()
    assert not session_file.exists()
    with pytest.raises(ChatNotFoundError):
        repository.get_chat(chat.chat_id)


def test_restore_chat_recreates_deleted_complete_session(
    tmp_path: Path,
) -> None:
    repository = JsonChatRepository(
        _storage_directory(tmp_path),
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Restore me",
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=ProjectId("project_123"),
    )
    message = create_chat_message(
        role="user",
        content="Preserve this content.",
        created_at=BASE_TIME,
    )
    complete_chat = replace(chat, messages=(message,))
    repository.save_chat(complete_chat)
    repository.delete_chat(chat.chat_id)

    repository.restore_chat(complete_chat)

    assert repository.get_chat(chat.chat_id) == complete_chat


@pytest.mark.parametrize(
    "operation",
    [
        "get",
        "rename",
        "pin",
        "archive",
        "delete",
    ],
)
def test_missing_chat_operations_raise_not_found(
    tmp_path: Path,
    operation: str,
) -> None:
    repository = JsonChatRepository(_storage_directory(tmp_path))
    missing_id = ChatId("chat_missing")

    with pytest.raises(ChatNotFoundError):
        if operation == "get":
            repository.get_chat(missing_id)
        elif operation == "rename":
            repository.rename_chat(missing_id, "New title")
        elif operation == "pin":
            repository.pin_chat(missing_id)
        elif operation == "archive":
            repository.archive_chat(missing_id)
        else:
            repository.delete_chat(missing_id)


def test_atomic_replace_failure_preserves_existing_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME, BASE_TIME),
    )
    chat = repository.create_chat(
        title="Original title",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    session_file = _session_file(storage_directory, chat.chat_id)
    original_text = session_file.read_text(encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "chats.storage.os.replace",
        fail_replace,
    )

    with pytest.raises(ChatStorageError):
        repository.rename_chat(chat.chat_id, "Lost title")

    assert session_file.read_text(encoding="utf-8") == original_text
    assert list(storage_directory.rglob("*.tmp")) == []
    assert repository.get_chat(chat.chat_id).title == "Original title"


def test_corrupt_index_can_be_backed_up_and_recovered(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Recover me",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    repository.pin_chat(chat.chat_id)
    index_file = storage_directory / "index.json"
    index_file.write_text('{"broken":', encoding="utf-8")

    with pytest.raises(ChatDataCorruptionError):
        repository.list_chats()

    recovered_entries = repository.recover_index()

    assert recovered_entries[0].chat_id == chat.chat_id
    assert recovered_entries[0].is_pinned is True
    assert repository.list_chats() == recovered_entries
    backup_files = list(
        storage_directory.glob("index.corrupt-*.json")
    )
    assert len(backup_files) == 1
    assert backup_files[0].read_text(encoding="utf-8") == '{"broken":'


def test_missing_index_is_rebuilt_from_session_files(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Rebuild me",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    (storage_directory / "index.json").unlink()

    restarted_repository = JsonChatRepository(storage_directory)

    assert restarted_repository.list_chats()[0].chat_id == chat.chat_id
    assert (storage_directory / "index.json").exists()


def test_index_detail_disagreement_is_reported_as_corruption(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Correct title",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    index_file = storage_directory / "index.json"
    index_data = json.loads(index_file.read_text(encoding="utf-8"))
    index_data["chats"][0]["title"] = "Wrong title"
    index_file.write_text(
        json.dumps(index_data),
        encoding="utf-8",
    )

    with pytest.raises(
        ChatDataCorruptionError,
        match=r"index metadata does not match",
    ):
        repository.get_chat(chat.chat_id)


def test_invalid_chat_id_cannot_escape_storage_directory(
    tmp_path: Path,
) -> None:
    repository = JsonChatRepository(_storage_directory(tmp_path))

    with pytest.raises(ChatNotFoundError, match=r"Invalid chat ID"):
        repository.get_chat(ChatId("../outside"))


def test_list_handles_two_hundred_chats_from_index(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=lambda: BASE_TIME,
    )

    for number in range(200):
        repository.create_chat(
            title=f"Chat {number}",
            mode="chat",
            model_name="qwen3.5:9b",
        )

    detail_file = next(
        (storage_directory / "sessions").glob("chat_*.json")
    )
    detail_file.write_text('{"broken":', encoding="utf-8")

    metadata_entries = repository.list_chats()

    assert len(metadata_entries) == 200
    assert all(entry.message_count == 0 for entry in metadata_entries)


def test_detail_with_unknown_summary_message_is_corrupt(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonChatRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    chat = repository.create_chat(
        title="Corrupt summary",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    session_file = _session_file(storage_directory, chat.chat_id)
    session_data = json.loads(session_file.read_text(encoding="utf-8"))
    session_data["summary"] = {
        "facts": [],
        "decisions": [],
        "action_items": [],
        "unresolved_questions": ["Unknown source?"],
        "source_message_ids": [str(ChatMessageId("message_missing"))],
        "updated_at": BASE_TIME.isoformat(),
    }
    session_file.write_text(
        json.dumps(session_data),
        encoding="utf-8",
    )

    with pytest.raises(ChatDataCorruptionError):
        repository.get_chat(chat.chat_id)
