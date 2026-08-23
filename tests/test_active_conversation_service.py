"""Tests for guarded Chat lifecycle and completed persistence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from chats import ChatId, JsonChatRepository
from core import (
    ActiveConversation,
    ActiveConversationService,
    ChatBusyError,
    ChatChangedDuringGenerationError,
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
