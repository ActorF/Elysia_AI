"""Test Brain orchestration, streaming, cancellation, and persistence."""

from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event

import pytest

import core.brain as brain_module
from attachments import AttachmentScope
from chats import (
    ChatGroundedAnswer,
    ChatGroundedCitation,
    ChatGroundedStatement,
    ChatGroundedStatementKind,
    ChatGroundedTextLocation,
    ChatId,
    ChatSession,
    JsonChatRepository,
    create_attachment_metadata,
)
from documents import (
    GROUNDED_ANSWER_SCHEMA_VERSION,
    DocumentOperationCancelledError,
    GroundedAnswerResult,
)
from core import (
    ActiveConversationService,
    Brain,
    GenerationCancelledError,
)
from core.chat_model import ChatMessage
from memory import Memory, ShortTermMemory
from project_sources import (
    PROJECT_SOURCE_ANSWER_SCHEMA_VERSION,
    ProjectSourceAnswer,
)
from projects import JsonProjectRepository, ProjectChatService

class FakeChatModel:
    """Capture prompts and serve configured sync or streaming model output."""
    def __init__(
        self,
        reply: str,
        stream_chunks: list[str] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        """Initialize deterministic state for this test double."""
        self._reply = reply
        self._stream_chunks = (
            stream_chunks
            if stream_chunks is not None
            else [reply]
        )
        self._stream_error = stream_error
        self.received_messages: list[ChatMessage] | None = None

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        """Return the configured deterministic model reply."""
        self.received_messages = messages
        return self._reply

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        """Yield the configured deterministic model reply chunks."""
        self.received_messages = messages
        yield from self._stream_chunks

        if self._stream_error is not None:
            raise self._stream_error


def _active_brain(
    tmp_path: Path,
    chat_model: FakeChatModel,
    *,
    short_term_memory: ShortTermMemory | None = None,
) -> tuple[Brain, Memory, ChatSession]:
    """Compose a Brain around one persisted Chat and an optional token budget."""
    memory = Memory(tmp_path)
    chats = JsonChatRepository(tmp_path / "data" / "chats")
    projects = JsonProjectRepository(tmp_path / "data" / "projects")
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
        short_term_memory=short_term_memory,
        active_conversation_service=ActiveConversationService(
            chats,
            projects,
        ),
    )
    chat = brain.create_chat(title="Test Chat")
    return brain, memory, chat


def _project_brain(
    tmp_path: Path,
) -> tuple[Brain, ChatSession]:
    """Compose one Project Chat for grounded-answer orchestration tests."""

    memory = Memory(tmp_path)
    chats = JsonChatRepository(tmp_path / "data" / "chats")
    projects = JsonProjectRepository(tmp_path / "data" / "projects")
    active = ActiveConversationService(chats, projects)
    brain = Brain(
        "fake-model",
        memory,
        active_conversation_service=active,
        project_service=ProjectChatService(
            projects,
            chats,
            is_chat_busy=active.is_chat_busy,
        ),
    )
    project = brain.create_project(name="Grounded Project")
    return brain, brain.create_chat(
        title="Grounded Chat",
        project_id=project.project_id,
    )


def _grounded_answer_with_statement(
    *,
    kind: ChatGroundedStatementKind,
    text: str,
    excerpt: str,
) -> ChatGroundedAnswer:
    """Build one closed grounded answer for normalization boundary tests."""

    citation_id = "citation_" + "a" * 64
    return ChatGroundedAnswer(
        status="answered",
        context_passage_count=1,
        statements=(
            ChatGroundedStatement(
                statement_id="statement_001",
                kind=kind,
                text=text,
                citation_ids=(citation_id,),
            ),
        ),
        citations=(
            ChatGroundedCitation(
                citation_id=citation_id,
                kind="prose",
                excerpt=excerpt,
                file_name="source.txt",
                media_type="text/plain",
                page_number=None,
                locations=(
                    ChatGroundedTextLocation(
                        block_ordinal=0,
                        source_start_code_point=0,
                        source_end_code_point=len(excerpt),
                    ),
                ),
            ),
        ),
    )


def test_grounded_source_fact_remains_exact_evidence() -> None:
    """Never simplify proof text away from its cited source excerpt."""

    answer = _grounded_answer_with_statement(
        kind="source_fact",
        text="乾坤與乾燥。",
        excerpt="原文写着：乾坤與乾燥。",
    )

    normalized = brain_module._normalize_grounded_answer(answer)

    assert normalized.statements[0].text == "乾坤與乾燥。"
    assert normalized.citations[0].excerpt == "原文写着：乾坤與乾燥。"


def test_grounded_model_statement_cannot_normalize_to_empty() -> None:
    """Fail closed when a model-only cue has no publishable answer text."""

    answer = _grounded_answer_with_statement(
        kind="model_summary",
        text="（輕笑一聲）",
        excerpt="Evidence for the summary.",
    )

    with pytest.raises(ValueError, match="cannot be empty"):
        brain_module._normalize_grounded_answer(answer)


def test_grounded_chat_persists_structured_insufficient_result(
    tmp_path: Path,
) -> None:
    """Keep a verified refusal attached to the same atomic Chat turn."""

    brain, chat = _project_brain(tmp_path)

    def answerer(chat_id: ChatId, query: str) -> ProjectSourceAnswer:
        """Return deterministic model-free insufficiency in the Chat Project."""

        assert chat_id == chat.chat_id
        assert query == "What does the source say?"
        project_id = brain.get_chat(chat.chat_id).project_id
        assert project_id is not None
        return ProjectSourceAnswer(
            schema_version=PROJECT_SOURCE_ANSWER_SCHEMA_VERSION,
            chat_id=chat.chat_id,
            project_id=project_id,
            answer=GroundedAnswerResult(
                schema_version=GROUNDED_ANSWER_SCHEMA_VERSION,
                scope=AttachmentScope(
                    kind="project",
                    id=str(project_id),
                ),
                status="insufficient_evidence",
                generator_identity=None,
                context_passage_count=0,
                statements=(),
                citations=(),
            ),
        )

    chunks = list(
        brain.stream_grounded_chat(
            chat.chat_id,
            "What does the source say?",
            answerer,
        )
    )

    assert chunks == [
        "I couldn't find enough evidence in this Project's current sources."
    ]
    persisted = brain.get_chat(chat.chat_id)
    assert persisted.messages[-1].grounded_answer is not None
    assert persisted.messages[-1].grounded_answer.status == (
        "insufficient_evidence"
    )


def test_grounded_chat_translates_deep_domain_cancellation(
    tmp_path: Path,
) -> None:
    """Propagate Brain's callback and preserve its request-cancel contract."""

    brain, chat = _project_brain(tmp_path)
    cancelled = Event()

    def answerer(
        chat_id: ChatId,
        query: str,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ProjectSourceAnswer:
        """Model cancellation winning inside the grounded dependency graph."""

        assert chat_id == chat.chat_id
        assert query == "Cancel grounded work"
        assert should_cancel is not None
        cancelled.set()
        assert should_cancel()
        raise DocumentOperationCancelledError("deep cancellation")

    with pytest.raises(GenerationCancelledError, match="cancelled"):
        list(
            brain.stream_grounded_chat(
                chat.chat_id,
                "Cancel grounded work",
                answerer,
                should_cancel=cancelled.is_set,
            )
        )

    assert brain.get_chat(chat.chat_id).messages == ()


def test_chat_returns_reply_and_saves_messages(
    tmp_path: Path,
) -> None:
    """Verify that chat returns reply and saves messages."""
    chat_model = FakeChatModel("Hello, Ying!")
    brain, memory, chat = _active_brain(tmp_path, chat_model)

    reply = brain.chat(chat.chat_id, "  Hello, Elysia!  ")

    assert reply == "Hello, Ying!"

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert len(received_messages) == 2

    assert received_messages[0]["role"] == "system"
    assert (
        "在 Elysia AI 的角色叙事中，你是《崩坏3》中的爱莉希雅"
        in received_messages[0]["content"]
    )
    assert (
        "USER_PROFILE_JSON:"
        in received_messages[0]["content"]
    )
    assert (
        '"user_name": "Ying"'
        in received_messages[0]["content"]
    )

    assert received_messages[1] == {
        "role": "user",
        "content": "Hello, Elysia!",
    }
    messages = brain.get_chat(chat.chat_id).messages

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello, Elysia!"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hello, Ying!"
    assert memory.get_recent_messages() == []


def test_chat_simplifies_reply_and_removes_stage_direction(
    tmp_path: Path,
) -> None:
    """Normalize non-streaming model prose before returning or persistence."""

    chat_model = FakeChatModel(
        "（輕笑一聲，眼神裡滿是驚喜）很高興見到你。"
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    reply = brain.chat(chat.chat_id, "你好")

    assert reply == "很高兴见到你。"
    assert brain.get_chat(chat.chat_id).messages[-1].content == reply


def test_chat_preserves_stage_content_for_explicit_fiction_request(
    tmp_path: Path,
) -> None:
    """Keep narration as work product only when the current turn requests it."""

    chat_model = FakeChatModel(
        "（輕笑一聲）她把故事的最後一頁翻開。"
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    reply = brain.chat(chat.chat_id, "请续写这个故事，并保留动作描写。")

    assert reply == "（轻笑一声）她把故事的最后一页翻开。"
    assert brain.get_chat(chat.chat_id).messages[-1].content == reply


def test_chat_negation_does_not_enable_stage_content(
    tmp_path: Path,
) -> None:
    """Treat an explicit no-narration instruction as ordinary spoken chat."""

    chat_model = FakeChatModel("（輕笑一聲）好，我会直接回答。")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    reply = brain.chat(chat.chat_id, "不要写动作旁白，直接回答。")

    assert reply == "好，我会直接回答。"


def test_chat_creative_adjective_negation_still_allows_stage_content(
    tmp_path: Path,
) -> None:
    """Do not confuse a story adjective with a no-narration instruction."""

    chat_model = FakeChatModel("（輕輕一笑）故事有了新的方向。")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    reply = brain.chat(
        chat.chat_id,
        "请写一个不要悲伤的故事，并保留动作描写。",
    )

    assert reply == "（轻轻一笑）故事有了新的方向。"


def test_chat_english_plural_stage_negation_removes_narration(
    tmp_path: Path,
) -> None:
    """Recognize plural English stage-direction opt-outs."""

    chat_model = FakeChatModel("(smiling) Here is the story.")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    reply = brain.chat(
        chat.chat_id,
        "Write a story without stage directions.",
    )

    assert reply == "Here is the story."


def test_stream_chat_normalizes_across_model_chunk_edges(
    tmp_path: Path,
) -> None:
    """Expose, save, and return one identical context-aware simplified reply."""

    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=[
            "（輕笑一",
            "聲，眼神裡很開心）",
            "乾",
            "坤與乾",
            "燥。",
        ],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    emitted = "".join(brain.stream_chat(chat.chat_id, "說點什麼"))

    assert emitted == "乾坤与干燥。"
    assert brain.get_chat(chat.chat_id).messages[-1].content == emitted


def test_stream_chat_preserves_requested_script_actions(
    tmp_path: Path,
) -> None:
    """Apply the creative-content exception across streamed chunk boundaries."""

    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["（輕笑", "一聲）", "她轉身離開。"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    emitted = "".join(
        brain.stream_chat(chat.chat_id, "请创作一段剧本动作描写。")
    )

    assert emitted == "（轻笑一声）她转身离开。"
    assert brain.get_chat(chat.chat_id).messages[-1].content == emitted


def test_chat_rejects_empty_user_message(
    tmp_path: Path,
) -> None:
    """Verify that chat rejects empty user message."""
    chat_model = FakeChatModel("Unused reply")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    with pytest.raises(
        ValueError,
        match=r"User message cannot be empty\.",
    ):
        brain.chat(chat.chat_id, "   ")

    assert chat_model.received_messages is None


def test_chat_rejects_empty_model_reply(
    tmp_path: Path,
) -> None:
    """Verify that chat rejects empty model reply."""
    chat_model = FakeChatModel("   ")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    with pytest.raises(
        ValueError,
        match=r"Model reply cannot be empty\.",
    ):
        brain.chat(chat.chat_id, "Hello")

    assert brain.get_chat(chat.chat_id).messages == ()


def test_build_recent_context_maps_message_roles(
    tmp_path: Path,
) -> None:
    """Verify that build recent context maps message roles."""
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
    )

    memory.save_message("Ying", "First message")
    memory.save_message("Elysia", "Second message")
    memory.save_message("Unknown", "Ignored message")

    profile = memory.load_profile()

    context = brain._build_recent_context(
        profile,
        limit=3,
    )

    assert context == [
        {
            "role": "user",
            "content": "First message",
        },
        {
            "role": "assistant",
            "content": "Second message",
        },
    ]

def test_build_recent_context_respects_limit(
    tmp_path: Path,
) -> None:
    """Verify that build recent context respects limit."""
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
    )

    memory.save_message("Ying", "First")
    memory.save_message("Elysia", "Second")
    memory.save_message("Ying", "Third")
    memory.save_message("Elysia", "Fourth")

    profile = memory.load_profile()

    context = brain._build_recent_context(
        profile,
        limit=2,
    )

    assert context == [
        {
            "role": "user",
            "content": "Third",
        },
        {
            "role": "assistant",
            "content": "Fourth",
        },
    ]

def test_build_chat_messages_orders_context(
    tmp_path: Path,
) -> None:
    """Verify that build chat messages orders context."""
    memory = Memory(tmp_path)
    brain = Brain(
        "fake-model",
        memory,
    )

    memory.save_message("Ying", "Previous question")
    memory.save_message("Elysia", "Previous answer")

    profile = memory.load_profile()

    messages = brain._build_chat_messages(
        profile,
        "Current question",
        limit=2,
    )

    assert messages[0]["role"] == "system"

    assert messages[1:] == [
        {
            "role": "user",
            "content": "Previous question",
        },
        {
            "role": "assistant",
            "content": "Previous answer",
        },
        {
            "role": "user",
            "content": "Current question",
        },
    ]


def test_chat_includes_previous_turn_in_context(
    tmp_path: Path,
) -> None:
    """Verify that chat includes previous turn in context."""
    chat_model = FakeChatModel("First reply")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    brain.chat(chat.chat_id, "First question")
    brain.chat(chat.chat_id, "Second question")

    received_messages = chat_model.received_messages

    assert received_messages is not None

    assert received_messages[1:] == [
        {
            "role": "user",
            "content": "First question",
        },
        {
            "role": "assistant",
            "content": "First reply",
        },
        {
            "role": "user",
            "content": "Second question",
        },
    ]


def test_stream_chat_yields_chunks_and_saves_complete_turn(
    tmp_path: Path,
) -> None:
    """Verify that stream chat yields chunks and saves complete turn."""
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Hello", " ", "Ying!"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    chunks = list(
        brain.stream_chat(chat.chat_id, "  Hello, Elysia!  ")
    )

    assert chunks == ["Hello Ying!"]

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert received_messages[-1] == {
        "role": "user",
        "content": "Hello, Elysia!",
    }

    messages = brain.get_chat(chat.chat_id).messages

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello, Elysia!"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hello Ying!"


def test_stream_chat_exposes_exactly_the_canonical_persisted_reply(
    tmp_path: Path,
) -> None:
    """Keep streamed, returned, and stored text identical across chunk edges."""

    raw_chunks = [
        " \t\n\u00a0",
        "  Hello",
        " ",
        "\t",
        "world  ",
        "\r\n\u2003",
    ]
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=raw_chunks,
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    emitted = "".join(brain.stream_chat(chat.chat_id, "Question"))
    persisted = brain.get_chat(chat.chat_id).messages[-1].content

    assert emitted == "".join(raw_chunks).strip()
    assert persisted == emitted


def test_stream_chat_rejects_an_all_whitespace_model_stream(
    tmp_path: Path,
) -> None:
    """Reject a stream whose canonical form is empty without persisting it."""

    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["  ", "\t\n", ""],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    with pytest.raises(ValueError, match=r"Model reply cannot be empty\."):
        list(brain.stream_chat(chat.chat_id, "Question"))

    assert brain.get_chat(chat.chat_id).messages == ()


def test_stream_chat_uses_metadata_only_and_commits_attachment(
    tmp_path: Path,
) -> None:
    """Verify that stream chat uses metadata only and commits attachment."""
    chat_model = FakeChatModel(
        "Stored locally.",
        stream_chunks=["Stored locally."],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    attachment = create_attachment_metadata(
        file_name="course notes.md",
        media_type="text/markdown",
        size_bytes=128,
    )

    chunks = list(
        brain.stream_chat(
            chat.chat_id,
            "",
            attachments=(attachment,),
        )
    )

    assert chunks == ["Stored locally."]
    assert chat_model.received_messages is not None
    prompt = chat_model.received_messages[-1]["content"]
    assert "course notes.md" in prompt
    assert "have not been read, parsed, or indexed" in prompt
    persisted = brain.get_chat(chat.chat_id).messages[0]
    assert persisted.content == ""
    assert persisted.attachments == (attachment,)


def test_stream_chat_does_not_save_partial_turn_on_stream_error(
    tmp_path: Path,
) -> None:
    """Verify that stream chat does not save partial turn on stream error."""
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Partial reply"],
        stream_error=RuntimeError(
            "Streaming interrupted."
        ),
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    stream = brain.stream_chat(chat.chat_id, "Hello")

    with pytest.raises(
        RuntimeError,
        match=r"Streaming interrupted\.",
    ):
        list(stream)

    assert brain.get_chat(chat.chat_id).messages == ()


def test_stream_retry_atomically_replaces_the_persisted_tail(
    tmp_path: Path,
) -> None:
    """Replace exactly the persisted tail pair only after retry generation succeeds."""
    chat_model = FakeChatModel(
        "Original answer",
        stream_chunks=["Replacement", " answer"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    brain.chat(chat.chat_id, "Original question")
    original = brain.get_chat(chat.chat_id)
    user_record, assistant_record = original.messages

    chunks = list(
        brain.stream_retry(
            chat.chat_id,
            user_record.message_id,
            assistant_record.message_id,
            "Edited question",
        )
    )

    assert chunks == ["Replacement answer"]
    retried = brain.get_chat(chat.chat_id)
    assert [message.content for message in retried.messages] == [
        "Edited question",
        "Replacement answer",
    ]
    assert [message.message_id for message in retried.messages] == [
        user_record.message_id,
        assistant_record.message_id,
    ]
    assert chat_model.received_messages is not None
    assert chat_model.received_messages[-1] == {
        "role": "user",
        "content": "Edited question",
    }
    assert all(
        message["content"] != "Original answer"
        for message in chat_model.received_messages
    )


def test_stream_retry_uses_the_same_canonical_text_for_output_and_storage(
    tmp_path: Path,
) -> None:
    """Apply outer-whitespace normalization identically during retry."""

    raw_chunks = [" \n", "Replacement ", " answer", "\t "]
    chat_model = FakeChatModel(
        "Original answer",
        stream_chunks=raw_chunks,
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    brain.chat(chat.chat_id, "Original question")
    original_user, original_assistant = brain.get_chat(chat.chat_id).messages

    emitted = "".join(
        brain.stream_retry(
            chat.chat_id,
            original_user.message_id,
            original_assistant.message_id,
        )
    )
    persisted = brain.get_chat(chat.chat_id).messages[-1].content

    assert emitted == "".join(raw_chunks).strip()
    assert persisted == emitted


def test_stream_retry_without_edit_reuses_the_original_user_text(
    tmp_path: Path,
) -> None:
    """Verify that stream retry without edit reuses the original user text."""
    chat_model = FakeChatModel(
        "Original answer",
        stream_chunks=["Regenerated answer"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    brain.chat(chat.chat_id, "Keep this question")
    original = brain.get_chat(chat.chat_id)
    user_record, assistant_record = original.messages

    assert list(
        brain.stream_retry(
            chat.chat_id,
            user_record.message_id,
            assistant_record.message_id,
        )
    ) == ["Regenerated answer"]

    retried = brain.get_chat(chat.chat_id)
    assert [message.content for message in retried.messages] == [
        "Keep this question",
        "Regenerated answer",
    ]
    assert chat_model.received_messages is not None
    assert chat_model.received_messages[-1] == {
        "role": "user",
        "content": "Keep this question",
    }


def test_stream_retry_failure_keeps_the_original_pair(
    tmp_path: Path,
) -> None:
    """Preserve the original turn when a retry stream fails before commit."""
    chat_model = FakeChatModel(
        "Original answer",
        stream_chunks=["Partial replacement"],
        stream_error=RuntimeError("Retry failed."),
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    brain.chat(chat.chat_id, "Original question")
    original = brain.get_chat(chat.chat_id)
    user_record, assistant_record = original.messages
    stream = brain.stream_retry(
        chat.chat_id,
        user_record.message_id,
        assistant_record.message_id,
        "Edited question",
    )

    with pytest.raises(RuntimeError, match=r"Retry failed"):
        list(stream)

    assert brain.get_chat(chat.chat_id) == original


def test_cancelled_retry_keeps_the_original_pair(
    tmp_path: Path,
) -> None:
    """Verify that cancelled retry keeps the original pair."""
    chat_model = FakeChatModel(
        "Original answer",
        stream_chunks=["Partial replacement。", "Not emitted"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    brain.chat(chat.chat_id, "Original question")
    original = brain.get_chat(chat.chat_id)
    user_record, assistant_record = original.messages
    cancelled = False
    stream = brain.stream_retry(
        chat.chat_id,
        user_record.message_id,
        assistant_record.message_id,
        should_cancel=lambda: cancelled,
    )

    assert next(stream) == "Partial replacement。"
    cancelled = True
    with pytest.raises(GenerationCancelledError, match=r"cancelled"):
        next(stream)

    assert brain.get_chat(chat.chat_id) == original


def test_cancelled_stream_never_persists_a_partial_turn(
    tmp_path: Path,
) -> None:
    """Keep streamed partial text transient when cancellation precedes commit."""
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["First。", "Second"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    cancelled = False
    stream = brain.stream_chat(
        chat.chat_id,
        "Question",
        should_cancel=lambda: cancelled,
    )

    assert next(stream) == "First。"
    cancelled = True
    with pytest.raises(GenerationCancelledError, match=r"cancelled"):
        next(stream)

    assert brain.get_chat(chat.chat_id).messages == ()
    assert brain.is_chat_busy(chat.chat_id) is False


def test_pre_cancelled_stream_never_calls_the_model(
    tmp_path: Path,
) -> None:
    """Verify that pre cancelled stream never calls the model."""
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Never requested"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)

    with pytest.raises(GenerationCancelledError, match=r"cancelled"):
        next(
            brain.stream_chat(
                chat.chat_id,
                "Question",
                should_cancel=lambda: True,
            )
        )

    assert chat_model.received_messages is None
    assert brain.get_chat(chat.chat_id).messages == ()
    assert brain.is_chat_busy(chat.chat_id) is False


def test_stream_cleanup_error_does_not_replace_cancellation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Preserve cancellation as the public failure even if iterator cleanup fails."""
    class CloseFailingIterator:
        """Emit one chunk, then fail if Brain attempts iterator cleanup."""

        def __init__(self) -> None:
            """Start before the iterator's sole partial chunk."""
            self._sent = False

        def __iter__(self) -> Iterator[str]:
            """Return this stateful iterator."""
            return self

        def __next__(self) -> str:
            """Return the partial chunk once before stopping iteration."""
            if self._sent:
                raise StopIteration
            self._sent = True
            return "Partial"

        def close(self) -> None:
            """Simulate a cleanup failure that must not hide cancellation."""
            raise RuntimeError("Cleanup failed.")

    class CloseFailingModel(FakeChatModel):
        """Return a stream whose cleanup path raises an unrelated error."""

        def stream_reply(
            self,
            messages: list[ChatMessage],
        ) -> Iterator[str]:
            """Capture the prompt and return the close-failing iterator."""
            self.received_messages = messages
            return CloseFailingIterator()

    chat_model = CloseFailingModel("Unused")
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    cancelled = False
    stream = brain.stream_chat(
        chat.chat_id,
        "Question",
        should_cancel=lambda: cancelled,
    )

    assert next(stream) == "Partial"
    cancelled = True
    with pytest.raises(GenerationCancelledError, match=r"cancelled"):
        next(stream)

    assert "Chat model stream cleanup failed" in caplog.text
    assert brain.get_chat(chat.chat_id).messages == ()


def test_commit_gate_gives_cancel_priority_before_persistence(
    tmp_path: Path,
) -> None:
    """Let a last-moment cancellation veto persistence at the commit gate."""
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Complete answer"],
    )
    brain, _memory, chat = _active_brain(tmp_path, chat_model)
    commit_claims = 0

    def reject_commit() -> bool:
        """Count and reject the generation's transition into commit."""
        nonlocal commit_claims
        commit_claims += 1
        return False

    with pytest.raises(GenerationCancelledError, match=r"cancelled"):
        list(
            brain.stream_chat(
                chat.chat_id,
                "Question",
                should_cancel=lambda: False,
                begin_commit=reject_commit,
            )
        )

    assert commit_claims == 1
    assert brain.get_chat(chat.chat_id).messages == ()

def test_active_chat_does_not_reuse_shared_short_term_turns(
    tmp_path: Path,
) -> None:
    """Verify that active chat does not reuse shared short term turns."""
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    short_term_memory.remember_turn(
        "Previous question",
        "Previous answer",
    )

    chat_model = FakeChatModel("Current answer")
    brain, _memory, chat = _active_brain(
        tmp_path,
        chat_model,
        short_term_memory=short_term_memory,
    )

    reply = brain.chat(chat.chat_id, "Current question")

    assert reply == "Current answer"

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert received_messages[1:] == [
        {
            "role": "user",
            "content": "Current question",
        },
    ]

    assert short_term_memory.get_turns() == [
        {
            "user_message": "Previous question",
            "assistant_message": "Previous answer",
        },
    ]
    assert len(brain.get_chat(chat.chat_id).messages) == 2


def test_short_term_memory_trimming_changes_context(
    tmp_path: Path,
) -> None:
    """Verify that short term memory trimming changes context."""
    memory = Memory(tmp_path)
    short_term_memory = ShortTermMemory(
        token_budget=4,
    )

    short_term_memory.remember_turn("aaaa", "bbbb")
    short_term_memory.remember_turn("cccc", "dddd")
    short_term_memory.remember_turn("eeee", "ffff")

    brain = Brain(
        "fake-model",
        memory,
        short_term_memory=short_term_memory,
    )

    messages = brain._build_chat_messages(
        memory.load_profile(),
        "gggg",
    )

    assert messages[1:] == [
        {
            "role": "user",
            "content": "cccc",
        },
        {
            "role": "assistant",
            "content": "dddd",
        },
        {
            "role": "user",
            "content": "eeee",
        },
        {
            "role": "assistant",
            "content": "ffff",
        },
        {
            "role": "user",
            "content": "gggg",
        },
    ]


def test_new_short_term_session_ignores_saved_history(
    tmp_path: Path,
) -> None:
    """Verify that new short term session ignores saved history."""
    memory = Memory(tmp_path)
    memory.save_message("Ying", "Old question")
    memory.save_message("Elysia", "Old answer")

    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    chat_model = FakeChatModel("Current answer")

    brain, _memory, chat = _active_brain(
        tmp_path,
        chat_model,
        short_term_memory=short_term_memory,
    )

    brain.chat(chat.chat_id, "Current question")

    received_messages = chat_model.received_messages

    assert received_messages is not None
    assert received_messages[1:] == [
        {
            "role": "user",
            "content": "Current question",
        }
    ]


def test_chat_does_not_save_failed_short_term_turn(
    tmp_path: Path,
) -> None:
    """Verify that chat does not save failed short term turn."""
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    brain, _memory, chat = _active_brain(
        tmp_path,
        FakeChatModel("   "),
        short_term_memory=short_term_memory,
    )

    with pytest.raises(
        ValueError,
        match=r"Model reply cannot be empty\.",
    ):
        brain.chat(chat.chat_id, "Hello")

    assert short_term_memory.get_turns() == []
    assert brain.get_chat(chat.chat_id).messages == ()


def test_stream_chat_saves_complete_short_term_turn(
    tmp_path: Path,
) -> None:
    """Verify that stream chat saves complete short term turn."""
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Hello", " ", "Ying!"],
    )
    brain, _memory, chat = _active_brain(
        tmp_path,
        chat_model,
        short_term_memory=short_term_memory,
    )

    chunks = list(
        brain.stream_chat(chat.chat_id, "Hello, Elysia!")
    )

    assert chunks == ["Hello Ying!"]
    assert short_term_memory.get_turns() == []
    assert [
        message.content
        for message in brain.get_chat(chat.chat_id).messages
    ] == ["Hello, Elysia!", "Hello Ying!"]


def test_stream_chat_does_not_save_partial_short_term_turn(
    tmp_path: Path,
) -> None:
    """Verify that stream chat does not save partial short term turn."""
    short_term_memory = ShortTermMemory(
        token_budget=100,
    )
    chat_model = FakeChatModel(
        "Unused reply",
        stream_chunks=["Partial reply"],
        stream_error=RuntimeError(
            "Streaming interrupted."
        ),
    )
    brain, _memory, chat = _active_brain(
        tmp_path,
        chat_model,
        short_term_memory=short_term_memory,
    )

    stream = brain.stream_chat(chat.chat_id, "Hello")

    with pytest.raises(
        RuntimeError,
        match=r"Streaming interrupted\.",
    ):
        list(stream)

    assert short_term_memory.get_turns() == []
    assert brain.get_chat(chat.chat_id).messages == ()
