"""Tests for guarded Chat lifecycle and completed persistence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from chats import (
    ChatId,
    ChatNotFoundError,
    JsonChatRepository,
    create_attachment_metadata,
)
from core import (
    ActiveConversation,
    ActiveConversationService,
    ChatBusyError,
    ChatChangedDuringGenerationError,
    ChatRetryTargetError,
    ConversationUnavailableError,
)
from projects import JsonProjectRepository, ProjectSettings

BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _services(
    tmp_path: Path,
) -> tuple[
    JsonChatRepository,
    JsonProjectRepository,
    ActiveConversationService,
]:
    chats = JsonChatRepository(
        tmp_path / "data" / "chats",
        clock=lambda: BASE_TIME,
    )
    projects = JsonProjectRepository(
        tmp_path / "data" / "projects",
        clock=lambda: BASE_TIME,
    )
    active = ActiveConversationService(
        chats,
        projects,
        clock=lambda: BASE_TIME + timedelta(minutes=1),
    )
    return chats, projects, active


def test_open_turn_loads_matching_chat_project_and_mode(
    tmp_path: Path,
) -> None:
    chats, projects, active = _services(tmp_path)
    project = projects.create_project(
        name="Scoped Project",
        settings=ProjectSettings(
            custom_instructions="Answer from this Project only."
        ),
    )
    chat = active.create_chat(
        title="Work Chat",
        mode="work",
        model_name="fake-model",
        project_id=project.project_id,
    )

    with active.open_turn(chat.chat_id) as context:
        assert context.chat_session == chat
        assert context.chat_session.mode == "work"
        assert context.project == project
        assert active.is_chat_busy(chat.chat_id) is True

    assert active.is_chat_busy(chat.chat_id) is False
    assert chats.get_chat(chat.chat_id) == chat


def test_same_chat_is_busy_but_different_chat_can_open(
    tmp_path: Path,
) -> None:
    _chats, _projects, active = _services(tmp_path)
    first = active.create_chat(
        title="First",
        mode="chat",
        model_name="fake-model",
    )
    second = active.create_chat(
        title="Second",
        mode="chat",
        model_name="fake-model",
    )

    with active.open_turn(first.chat_id):
        with pytest.raises(ChatBusyError, match=r"already generating"):
            with active.open_turn(first.chat_id):
                pass

        with active.open_turn(second.chat_id) as second_context:
            assert second_context.chat_session.chat_id == second.chat_id


def test_chat_actions_preserve_messages_summary_and_project(
    tmp_path: Path,
) -> None:
    chats, projects, active = _services(tmp_path)
    project = projects.create_project(name="Action Project")
    chat = active.create_chat(
        title="Original",
        mode="work",
        model_name="fake-model",
        project_id=project.project_id,
    )
    with active.open_turn(chat.chat_id) as context:
        with_messages = active.commit_turn(
            context,
            user_message="Keep this question",
            assistant_message="Keep this answer",
        )
    with active.open_turn(chat.chat_id) as context:
        with_summary = active.commit_summary(
            context,
            facts=("Keep this fact",),
            decisions=(),
            action_items=(),
            unresolved_questions=(),
            source_message_ids=(
                message.message_id for message in with_messages.messages
            ),
        )

    renamed = active.rename_chat(chat.chat_id, "Renamed")
    pinned = active.pin_chat(chat.chat_id)
    archived = active.archive_chat(chat.chat_id)
    persisted = active.get_chat(chat.chat_id)

    assert renamed.title == "Renamed"
    assert pinned.is_pinned is True
    assert archived.is_archived is True
    assert active.list_chats() == ()
    assert active.list_chats(include_archived=True) == (archived,)
    assert persisted.messages == with_summary.messages
    assert persisted.summary == with_summary.summary
    assert persisted.project_id == project.project_id
    assert projects.get_project(project.project_id) == project

    restored = active.archive_chat(chat.chat_id, archived=False)
    unpinned = active.pin_chat(chat.chat_id, pinned=False)

    assert restored.is_archived is False
    assert unpinned.is_pinned is False
    assert active.get_chat(chat.chat_id).messages == with_summary.messages


@pytest.mark.parametrize(
    "action",
    ["rename", "pin", "archive", "delete"],
)
def test_busy_chat_rejects_session_actions_without_changes(
    tmp_path: Path,
    action: str,
) -> None:
    chats, _projects, active = _services(tmp_path)
    chat = active.create_chat(
        title="Busy",
        mode="chat",
        model_name="fake-model",
    )

    with active.open_turn(chat.chat_id):
        with pytest.raises(ChatBusyError, match=r"cannot be changed"):
            if action == "rename":
                active.rename_chat(chat.chat_id, "Rejected")
            elif action == "pin":
                active.pin_chat(chat.chat_id)
            elif action == "archive":
                active.archive_chat(chat.chat_id)
            else:
                active.delete_chat(chat.chat_id)

        assert chats.get_chat(chat.chat_id) == chat


def test_delete_chat_removes_only_target_and_preserves_project_and_sibling(
    tmp_path: Path,
) -> None:
    _chats, projects, active = _services(tmp_path)
    project = projects.create_project(name="Kept Project")
    target = active.create_chat(
        title="Delete",
        mode="work",
        model_name="fake-model",
        project_id=project.project_id,
    )
    sibling = active.create_chat(
        title="Keep",
        mode="work",
        model_name="fake-model",
        project_id=project.project_id,
    )
    with active.open_turn(sibling.chat_id) as context:
        kept_sibling = active.commit_turn(
            context,
            user_message="Sibling question",
            assistant_message="Sibling answer",
        )

    active.delete_chat(target.chat_id)

    with pytest.raises(ChatNotFoundError):
        active.get_chat(target.chat_id)
    assert active.get_chat(sibling.chat_id) == kept_sibling
    assert projects.get_project(project.project_id) == project


def test_different_chat_commits_preserve_shared_index(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    first = active.create_chat(
        title="First",
        mode="chat",
        model_name="fake-model",
    )
    second = active.create_chat(
        title="Second",
        mode="chat",
        model_name="fake-model",
    )
    barrier = Barrier(2)

    def commit(chat_id: ChatId, label: str) -> None:
        with active.open_turn(chat_id) as context:
            barrier.wait()
            active.commit_turn(
                context,
                user_message=f"{label} question",
                assistant_message=f"{label} answer",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(commit, first.chat_id, "First"),
            executor.submit(commit, second.chat_id, "Second"),
        ]
        for future in futures:
            future.result()

    metadata = {entry.chat_id: entry for entry in chats.list_chats()}
    assert metadata[first.chat_id].message_count == 2
    assert metadata[second.chat_id].message_count == 2
    assert len(chats.get_chat(first.chat_id).messages) == 2
    assert len(chats.get_chat(second.chat_id).messages) == 2


def test_commit_turn_saves_complete_pair_only_to_named_chat(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    first = active.create_chat(
        title="First",
        mode="chat",
        model_name="fake-model",
    )
    second = active.create_chat(
        title="Second",
        mode="chat",
        model_name="fake-model",
    )

    with active.open_turn(first.chat_id) as context:
        updated = active.commit_turn(
            context,
            user_message="Question",
            assistant_message="Answer",
        )

    assert [message.role for message in updated.messages] == [
        "user",
        "assistant",
    ]
    assert [message.content for message in updated.messages] == [
        "Question",
        "Answer",
    ]
    assert updated.updated_at == BASE_TIME + timedelta(minutes=1)
    assert chats.get_chat(first.chat_id) == updated
    assert chats.get_chat(second.chat_id).messages == ()


def test_retry_replaces_only_tail_content_and_preserves_message_identity(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    chat = active.create_chat(
        title="Retry",
        mode="chat",
        model_name="fake-model",
    )
    with active.open_turn(chat.chat_id) as context:
        with_turn = active.commit_turn(
            context,
            user_message="Original question",
            assistant_message="Original answer",
        )

    attachment = create_attachment_metadata(
        file_name="context.txt",
        media_type="text/plain",
        size_bytes=7,
    )
    user_record, assistant_record = with_turn.messages
    with_attachment = replace(
        with_turn,
        messages=(
            replace(user_record, attachments=(attachment,)),
            assistant_record,
        ),
    )
    chats.save_chat(with_attachment)
    with active.open_turn(chat.chat_id) as context:
        with_summary = active.commit_summary(
            context,
            facts=("Old fact",),
            decisions=(),
            action_items=(),
            unresolved_questions=(),
            source_message_ids=(
                message.message_id for message in with_attachment.messages
            ),
        )

    original_pair = with_summary.messages
    with active.open_turn(chat.chat_id) as context:
        retried = active.commit_retry(
            context,
            user_message_id=original_pair[0].message_id,
            assistant_message_id=original_pair[1].message_id,
            user_message="Edited question",
            assistant_message="Replacement answer",
        )

    assert [message.content for message in retried.messages] == [
        "Edited question",
        "Replacement answer",
    ]
    assert [message.message_id for message in retried.messages] == [
        message.message_id for message in original_pair
    ]
    assert [message.created_at for message in retried.messages] == [
        message.created_at for message in original_pair
    ]
    assert retried.messages[0].attachments == (attachment,)
    assert retried.summary is None
    assert chats.get_chat(chat.chat_id) == retried


def test_retry_rejects_a_non_tail_pair_without_changing_chat(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    chat = active.create_chat(
        title="Retry target",
        mode="chat",
        model_name="fake-model",
    )
    with active.open_turn(chat.chat_id) as context:
        first_turn = active.commit_turn(
            context,
            user_message="First question",
            assistant_message="First answer",
        )
    with active.open_turn(chat.chat_id) as context:
        complete_chat = active.commit_turn(
            context,
            user_message="Second question",
            assistant_message="Second answer",
        )

    with active.open_turn(chat.chat_id) as context:
        with pytest.raises(ChatRetryTargetError, match=r"tail turn"):
            active.commit_retry(
                context,
                user_message_id=first_turn.messages[0].message_id,
                assistant_message_id=first_turn.messages[1].message_id,
                user_message="Rejected edit",
                assistant_message="Rejected answer",
            )

    assert chats.get_chat(chat.chat_id) == complete_chat


def test_commit_rejects_context_after_guard_closes(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    chat = active.create_chat(
        title="Closed",
        mode="chat",
        model_name="fake-model",
    )

    context: ActiveConversation
    with active.open_turn(chat.chat_id) as context:
        pass

    with pytest.raises(
        ChatChangedDuringGenerationError,
        match=r"no longer valid",
    ):
        active.commit_turn(
            context,
            user_message="Question",
            assistant_message="Answer",
        )

    assert chats.get_chat(chat.chat_id).messages == ()


def test_concurrent_repository_change_is_not_overwritten(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    chat = active.create_chat(
        title="Original",
        mode="chat",
        model_name="fake-model",
    )

    with active.open_turn(chat.chat_id) as context:
        chats.rename_chat(chat.chat_id, "Renamed elsewhere")

        with pytest.raises(
            ChatChangedDuringGenerationError,
            match=r"changed before",
        ):
            active.commit_turn(
                context,
                user_message="Question",
                assistant_message="Stale answer",
            )

    persisted = chats.get_chat(chat.chat_id)
    assert persisted.title == "Renamed elsewhere"
    assert persisted.messages == ()


def test_archived_chat_and_project_are_read_only(
    tmp_path: Path,
) -> None:
    chats, projects, active = _services(tmp_path)
    archived_project = projects.create_project(name="Archived Project")
    projects.archive_project(archived_project.project_id)

    with pytest.raises(
        ConversationUnavailableError,
        match=r"archived Project",
    ):
        active.create_chat(
            title="Rejected",
            mode="chat",
            model_name="fake-model",
            project_id=archived_project.project_id,
        )

    chat = active.create_chat(
        title="Archived Chat",
        mode="chat",
        model_name="fake-model",
    )
    chats.archive_chat(chat.chat_id)

    with pytest.raises(
        ConversationUnavailableError,
        match=r"Archived Chat",
    ):
        with active.open_turn(chat.chat_id):
            pass

    assert active.is_chat_busy(chat.chat_id) is False


def test_get_or_create_default_chat_resumes_existing_chat(
    tmp_path: Path,
) -> None:
    _chats, _projects, active = _services(tmp_path)
    created = active.get_or_create_default_chat(
        title="Console Chat",
        mode="chat",
        model_name="fake-model",
    )
    resumed = active.get_or_create_default_chat(
        title="Unused title",
        mode="work",
        model_name="unused-model",
    )

    assert resumed == created
    assert len(active.list_chats()) == 1


def test_commit_summary_is_bound_to_guarded_chat(
    tmp_path: Path,
) -> None:
    chats, _projects, active = _services(tmp_path)
    chat = active.create_chat(
        title="Summary",
        mode="chat",
        model_name="fake-model",
    )
    with active.open_turn(chat.chat_id) as context:
        with_turn = active.commit_turn(
            context,
            user_message="Question",
            assistant_message="Answer",
        )

    with active.open_turn(chat.chat_id) as context:
        summarized = active.commit_summary(
            context,
            facts=["One fact"],
            decisions=[],
            action_items=[],
            unresolved_questions=[],
            source_message_ids=(
                message.message_id
                for message in with_turn.messages
            ),
        )

    assert summarized.summary is not None
    assert summarized.summary.facts == ("One fact",)
    assert summarized.summary.source_message_ids == tuple(
        message.message_id for message in with_turn.messages
    )
    assert chats.get_chat(chat.chat_id) == summarized


def test_naive_service_clock_cannot_persist_turn(
    tmp_path: Path,
) -> None:
    chats = JsonChatRepository(
        tmp_path / "data" / "chats",
        clock=lambda: BASE_TIME,
    )
    projects = JsonProjectRepository(tmp_path / "data" / "projects")
    active = ActiveConversationService(
        chats,
        projects,
        clock=lambda: datetime(2026, 8, 22, 12, 1),
    )
    chat = active.create_chat(
        title="Clock",
        mode="chat",
        model_name="fake-model",
    )

    with active.open_turn(chat.chat_id) as context:
        with pytest.raises(ValueError, match=r"aware datetime"):
            active.commit_turn(
                context,
                user_message="Question",
                assistant_message="Answer",
            )

    assert chats.get_chat(chat.chat_id).messages == ()
