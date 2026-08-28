"""End-to-end tests for Brain's explicit multi-Chat entry points."""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chats import (
    ChatId,
    ChatMessageRole,
    ChatNotFoundError,
    JsonChatRepository,
    create_chat_message,
)
from core import (
    ActiveConversationService,
    Brain,
    ChatBusyError,
    ChatChangedDuringGenerationError,
    ChatModelMismatchError,
)
from core.chat_model import ChatMessage
from memory import (
    ConversationMessage,
    ConversationSummaryContent,
    Memory,
    MemoryRetriever,
    ShortTermMemory,
)
from projects import JsonProjectRepository, ProjectSettings


class RoutedChatModel:
    """Return deterministic replies while recording every prompt."""

    def __init__(
        self,
        *,
        stream_chunks: list[str] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.stream_chunks = stream_chunks
        self.stream_error = stream_error
        self.received_messages: list[list[ChatMessage]] = []

    def generate_reply(self, messages: list[ChatMessage]) -> str:
        self.received_messages.append(list(messages))
        return f"Reply to {messages[-1]['content']}"

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        self.received_messages.append(list(messages))
        chunks = (
            self.stream_chunks
            if self.stream_chunks is not None
            else [f"Reply to {messages[-1]['content']}"]
        )
        yield from chunks
        if self.stream_error is not None:
            raise self.stream_error


class FakeChatSummarizer:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                list[ConversationMessage],
                ConversationSummaryContent | None,
            ]
        ] = []

    def summarize(
        self,
        messages: list[ConversationMessage],
        previous_content: ConversationSummaryContent | None = None,
    ) -> ConversationSummaryContent:
        self.calls.append((list(messages), previous_content))
        return {
            "facts": [f"Covered {len(messages)} new messages"],
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
        }


def _brain(
    tmp_path: Path,
    chat_model: RoutedChatModel,
    *,
    summarizer: FakeChatSummarizer | None = None,
    token_budget: int = 1_000,
) -> tuple[
    Brain,
    Memory,
    JsonChatRepository,
    JsonProjectRepository,
]:
    memory = Memory(tmp_path)
    chats = JsonChatRepository(tmp_path / "data" / "chats")
    projects = JsonProjectRepository(tmp_path / "data" / "projects")
    active = ActiveConversationService(chats, projects)
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
        short_term_memory=ShortTermMemory(token_budget),
        conversation_summarizer=summarizer,
        memory_retriever=MemoryRetriever(10),
        active_conversation_service=active,
    )
    return brain, memory, chats, projects


def _active_context_json(system_prompt: str) -> object:
    serialized = system_prompt.split(
        "ACTIVE_CONVERSATION_JSON:\n",
        1,
    )[1].split("\nUSER_PROFILE_JSON:\n", 1)[0]
    return json.loads(serialized)


def _retrieved_memory_json(system_prompt: str) -> list[dict[str, object]]:
    serialized = system_prompt.split(
        "RETRIEVED_MEMORY_JSON:\n",
        1,
    )[1].split("\nACTIVE_CONVERSATION_JSON:\n", 1)[0]
    decoded: object = json.loads(serialized)
    assert isinstance(decoded, list)
    return decoded


def test_switching_chats_keeps_context_and_persistence_independent(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain, legacy_memory, _chats, _projects = _brain(tmp_path, model)
    first = brain.create_chat(title="First")
    second = brain.create_chat(title="Second")

    brain.chat(first.chat_id, "First A")
    brain.chat(second.chat_id, "First B")
    brain.chat(first.chat_id, "Second A")

    third_prompt = model.received_messages[2]
    assert third_prompt[1:] == [
        {"role": "user", "content": "First A"},
        {"role": "assistant", "content": "Reply to First A"},
        {"role": "user", "content": "Second A"},
    ]
    assert all(
        "First B" not in message["content"]
        for message in third_prompt
    )
    assert [
        message.content
        for message in brain.get_chat(first.chat_id).messages
    ] == ["First A", "Reply to First A", "Second A", "Reply to Second A"]
    assert [
        message.content
        for message in brain.get_chat(second.chat_id).messages
    ] == ["First B", "Reply to First B"]
    assert legacy_memory.get_all_messages() == []


def test_each_chat_rebuilds_its_own_token_bounded_recent_window(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain, _memory, chats, _projects = _brain(
        tmp_path,
        model,
        token_budget=4,
    )
    chat = brain.create_chat(title="Bounded")
    created_at = datetime.now(timezone.utc)
    turn_data: tuple[tuple[ChatMessageRole, str], ...] = (
        ("user", "aaaa"),
        ("assistant", "bbbb"),
        ("user", "cccc"),
        ("assistant", "dddd"),
        ("user", "eeee"),
        ("assistant", "ffff"),
    )
    stored = replace(
        chat,
        updated_at=created_at,
        messages=tuple(
            create_chat_message(
                role=role,
                content=content,
                created_at=created_at,
            )
            for role, content in turn_data
        ),
    )
    chats.save_chat(stored)

    brain.chat(chat.chat_id, "gggg")

    assert model.received_messages[0][1:] == [
        {"role": "user", "content": "cccc"},
        {"role": "assistant", "content": "dddd"},
        {"role": "user", "content": "eeee"},
        {"role": "assistant", "content": "ffff"},
        {"role": "user", "content": "gggg"},
    ]


def test_project_mode_and_instructions_enter_only_active_prompt(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain, _memory, _chats, projects = _brain(tmp_path, model)
    project = projects.create_project(
        name="Elysia Project",
        settings=ProjectSettings(
            custom_instructions="Use this repository's terminology."
        ),
    )
    project_chat = brain.create_chat(
        title="Project Work",
        mode="work",
        project_id=project.project_id,
    )
    plain_chat = brain.create_chat(title="Plain Chat")

    brain.chat(project_chat.chat_id, "Inspect architecture")
    brain.chat(plain_chat.chat_id, "General question")

    project_context = _active_context_json(
        model.received_messages[0][0]["content"]
    )
    plain_context = _active_context_json(
        model.received_messages[1][0]["content"]
    )
    assert project_context == {
        "chat_id": str(project_chat.chat_id),
        "mode": "work",
        "model_name": "fake-model",
        "project": {
            "project_id": str(project.project_id),
            "name": "Elysia Project",
            "custom_instructions": "Use this repository's terminology.",
        },
    }
    assert plain_context == {
        "chat_id": str(plain_chat.chat_id),
        "mode": "chat",
        "model_name": "fake-model",
        "project": None,
    }


def test_brain_exposes_guarded_chat_session_actions(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain, _memory, _chats, projects = _brain(tmp_path, model)
    project = projects.create_project(name="Brain Project")
    chat = brain.create_chat(
        title="Original",
        mode="work",
        project_id=project.project_id,
    )
    brain.chat(chat.chat_id, "Keep this turn")
    stored_messages = brain.get_chat(chat.chat_id).messages

    renamed = brain.rename_chat(chat.chat_id, "Renamed")
    pinned = brain.pin_chat(chat.chat_id)
    archived = brain.archive_chat(chat.chat_id)

    assert renamed.title == "Renamed"
    assert pinned.is_pinned is True
    assert archived.is_archived is True
    assert brain.get_chat(chat.chat_id).messages == stored_messages
    assert brain.get_chat(chat.chat_id).project_id == project.project_id
    assert projects.get_project(project.project_id) == project

    brain.delete_chat(chat.chat_id)

    with pytest.raises(ChatNotFoundError):
        brain.get_chat(chat.chat_id)
    assert projects.get_project(project.project_id) == project


def test_active_chat_loads_only_its_readable_memory_scopes(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain, memory, _chats, projects = _brain(tmp_path, model)
    alpha = projects.create_project(name="Alpha")
    beta = projects.create_project(name="Beta")
    alpha_chat = brain.create_chat(
        title="Alpha Chat",
        project_id=alpha.project_id,
    )
    memory.save_long_term_memory(
        "project_database",
        "Alpha SQLite",
        "user_explicit",
        "Alpha uses SQLite.",
        scope="project",
        scope_id=str(alpha.project_id),
    )
    memory.save_long_term_memory(
        "project_database",
        "Beta PostgreSQL",
        "user_explicit",
        "Beta uses PostgreSQL.",
        scope="project",
        scope_id=str(beta.project_id),
    )

    brain.chat(alpha_chat.chat_id, "Which project database is used?")
    retrieved = _retrieved_memory_json(
        model.received_messages[0][0]["content"]
    )

    assert any(item["value"] == "Alpha SQLite" for item in retrieved)
    assert all(item["value"] != "Beta PostgreSQL" for item in retrieved)


def test_stream_failure_never_saves_partial_or_wrong_chat(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel(
        stream_chunks=["Partial"],
        stream_error=RuntimeError("stream failed"),
    )
    brain, _memory, _chats, _projects = _brain(tmp_path, model)
    first = brain.create_chat(title="First")
    second = brain.create_chat(title="Second")
    stream = brain.stream_chat(first.chat_id, "Question")

    assert next(stream) == "Partial"
    with pytest.raises(RuntimeError, match=r"stream failed"):
        next(stream)

    assert brain.get_chat(first.chat_id).messages == ()
    assert brain.get_chat(second.chat_id).messages == ()
    assert brain.is_chat_busy(first.chat_id) is False


def test_closing_stream_discards_partial_turn_and_releases_busy_guard(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel(stream_chunks=["One", "Two"])
    brain, _memory, _chats, _projects = _brain(tmp_path, model)
    chat = brain.create_chat(title="Cancelable")
    stream = brain.stream_chat(chat.chat_id, "Question")

    assert next(stream) == "One"
    assert brain.is_chat_busy(chat.chat_id) is True
    stream.close()

    assert brain.is_chat_busy(chat.chat_id) is False
    assert brain.get_chat(chat.chat_id).messages == ()


def test_busy_chat_rejects_second_generation_but_other_chat_can_run(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel(stream_chunks=["One", "Two"])
    brain, _memory, _chats, _projects = _brain(tmp_path, model)
    first = brain.create_chat(title="First")
    second = brain.create_chat(title="Second")
    first_stream = brain.stream_chat(first.chat_id, "First question")

    assert next(first_stream) == "One"
    blocked_stream = brain.stream_chat(first.chat_id, "Duplicate")
    with pytest.raises(ChatBusyError):
        next(blocked_stream)

    assert list(brain.stream_chat(second.chat_id, "Second question")) == [
        "One",
        "Two",
    ]
    first_stream.close()
    assert brain.get_chat(first.chat_id).messages == ()
    assert len(brain.get_chat(second.chat_id).messages) == 2


def test_chat_changed_during_stream_is_not_overwritten(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel(stream_chunks=["Complete reply"])
    brain, _memory, chats, _projects = _brain(tmp_path, model)
    chat = brain.create_chat(title="Original")
    stream = brain.stream_chat(chat.chat_id, "Question")

    assert next(stream) == "Complete reply"
    chats.rename_chat(chat.chat_id, "Renamed during generation")
    with pytest.raises(ChatChangedDuringGenerationError):
        next(stream)

    persisted = brain.get_chat(chat.chat_id)
    assert persisted.title == "Renamed during generation"
    assert persisted.messages == ()


def test_model_mismatch_fails_before_model_or_storage_work(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain, _memory, chats, _projects = _brain(tmp_path, model)
    wrong_model_chat = chats.create_chat(
        title="Other model",
        mode="chat",
        model_name="another-model",
    )

    with pytest.raises(ChatModelMismatchError, match=r"another-model"):
        brain.chat(wrong_model_chat.chat_id, "Question")

    assert model.received_messages == []
    assert chats.get_chat(wrong_model_chat.chat_id).messages == ()


def test_chat_summary_updates_only_named_chat(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    summarizer = FakeChatSummarizer()
    brain, _memory, _chats, _projects = _brain(
        tmp_path,
        model,
        summarizer=summarizer,
    )
    first = brain.create_chat(title="First")
    second = brain.create_chat(title="Second")
    brain.chat(first.chat_id, "First question")
    brain.chat(second.chat_id, "Second question")

    summary = brain.summarize_chat(first.chat_id)

    assert summary is not None
    assert len(summary.source_message_ids) == 2
    assert brain.get_chat(second.chat_id).summary is None
    assert [
        message["message"]
        for message in summarizer.calls[0][0]
    ] == ["First question", "Reply to First question"]
    assert brain.get_unsummarized_chat_message_count(first.chat_id) == 0
    assert brain.get_unsummarized_chat_message_count(second.chat_id) == 2


def test_chat_entry_requires_real_chat_id_and_connected_service(
    tmp_path: Path,
) -> None:
    model = RoutedChatModel()
    brain = Brain("fake-model", Memory(tmp_path), model)

    with pytest.raises(RuntimeError, match=r"service is not connected"):
        brain.chat(ChatId("chat_missing"), "Question")

    assert model.received_messages == []
