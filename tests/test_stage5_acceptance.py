"""End-to-end acceptance gate for Stage 5 Chats and Projects."""

from collections.abc import Iterator
from pathlib import Path

from chats import JsonChatRepository
from core import ActiveConversationService, Brain
from core.chat_model import ChatMessage
from memory import (
    ConversationMessage,
    ConversationSummaryContent,
    Memory,
    MemoryRetriever,
    ShortTermMemory,
)
from projects import JsonProjectRepository, ProjectSettings
from recovery import DataPortabilityService


class AcceptanceChatModel:
    """Provide deterministic normal and streaming replies."""

    def generate_reply(self, messages: list[ChatMessage]) -> str:
        return f"Reply to {messages[-1]['content']}"

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        yield "Reply to "
        yield messages[-1]["content"]


class AcceptanceSummarizer:
    """Return one deterministic structured summary per Chat."""

    def summarize(
        self,
        messages: list[ConversationMessage],
        previous_content: ConversationSummaryContent | None = None,
    ) -> ConversationSummaryContent:
        return {
            "facts": [f"Summarized {len(messages)} messages"],
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
        }


def build_brain(
    base_dir: Path,
) -> tuple[Brain, Memory, JsonChatRepository, JsonProjectRepository]:
    memory = Memory(base_dir)
    chats = JsonChatRepository(base_dir / "workspace" / "chats")
    projects = JsonProjectRepository(base_dir / "workspace" / "projects")
    brain = Brain(
        "acceptance-model",
        memory,
        AcceptanceChatModel(),
        short_term_memory=ShortTermMemory(2_048),
        conversation_summarizer=AcceptanceSummarizer(),
        memory_retriever=MemoryRetriever(10),
        active_conversation_service=ActiveConversationService(
            chats,
            projects,
        ),
    )
    return brain, memory, chats, projects


def test_stage5_multi_chat_restart_retrieval_and_recovery_gate(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    brain, memory, chats, projects = build_brain(source_dir)
    first_project = projects.create_project(
        name="First Project",
        settings=ProjectSettings(custom_instructions="Use Alpha context."),
    )
    second_project = projects.create_project(
        name="Second Project",
        settings=ProjectSettings(custom_instructions="Use Beta context."),
    )
    first_chat = brain.create_chat(
        title="Alpha Chat",
        project_id=first_project.project_id,
    )
    second_chat = brain.create_chat(
        title="Beta Chat",
        project_id=second_project.project_id,
    )
    memory.save_long_term_memory(
        "alpha_topic",
        "Alpha project architecture",
        "user_explicit",
        "Remember Alpha architecture",
        scope="project",
        scope_id=str(first_project.project_id),
    )
    memory.save_long_term_memory(
        "beta_topic",
        "Beta project deployment",
        "user_explicit",
        "Remember Beta deployment",
        scope="project",
        scope_id=str(second_project.project_id),
    )

    assert "".join(brain.stream_chat(first_chat.chat_id, "Alpha question")) == (
        "Reply to Alpha question"
    )
    assert "".join(brain.stream_chat(second_chat.chat_id, "Beta question")) == (
        "Reply to Beta question"
    )
    assert brain.summarize_chat(first_chat.chat_id) is not None
    assert brain.summarize_chat(second_chat.chat_id) is not None

    restarted, _memory, restarted_chats, restarted_projects = build_brain(
        source_dir
    )
    restored_first = restarted.get_chat(first_chat.chat_id)
    restored_second = restarted.get_chat(second_chat.chat_id)
    assert [message.content for message in restored_first.messages] == [
        "Alpha question",
        "Reply to Alpha question",
    ]
    assert [message.content for message in restored_second.messages] == [
        "Beta question",
        "Reply to Beta question",
    ]
    assert restored_first.summary is not None
    assert restored_second.summary is not None
    alpha_results = restarted.retrieve_relevant_memories_for_chat(
        "Alpha architecture",
        restored_first,
    )
    beta_results = restarted.retrieve_relevant_memories_for_chat(
        "Beta deployment",
        restored_second,
    )
    assert any(result["key"] == "alpha_topic" for result in alpha_results)
    assert all(result["key"] != "beta_topic" for result in alpha_results)
    assert any(result["key"] == "beta_topic" for result in beta_results)
    assert all(result["key"] != "alpha_topic" for result in beta_results)

    portability = DataPortabilityService(
        base_dir=source_dir,
        chat_repository=restarted_chats,
        project_repository=restarted_projects,
    )
    export_file = tmp_path / "stage5-backup.json"
    portability.export_all_user_data(export_file)
    target_dir = tmp_path / "restored"
    target_chats = JsonChatRepository(target_dir / "workspace" / "chats")
    target_projects = JsonProjectRepository(
        target_dir / "workspace" / "projects"
    )
    target_portability = DataPortabilityService(
        base_dir=target_dir,
        chat_repository=target_chats,
        project_repository=target_projects,
    )

    result = target_portability.import_bundle(export_file)

    assert len(result.project_ids) == 2
    assert len(result.chat_ids) == 2
    assert target_projects.get_project(first_project.project_id) == first_project
    assert target_projects.get_project(second_project.project_id) == second_project
    assert target_chats.get_chat(first_chat.chat_id) == restored_first
    assert target_chats.get_chat(second_chat.chat_id) == restored_second
