from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

import pytest

from chats import (
    CHAT_SESSION_SCHEMA_VERSION,
    AttachmentId,
    AttachmentMetadata,
    ChatId,
    ChatMessage,
    ChatMessageId,
    ChatMessageRole,
    ChatModelSettings,
    ChatSession,
    ChatSummary,
    ConversationMode,
    ProjectId,
    create_attachment_metadata,
    create_chat_message,
    create_chat_session,
)

BASE_TIME = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _message(
    message_id: str,
    *,
    content: str = "Hello",
    created_at: datetime = BASE_TIME,
) -> ChatMessage:
    return ChatMessage(
        message_id=ChatMessageId(message_id),
        role="user",
        content=content,
        created_at=created_at,
    )


def _session(
    *,
    messages: tuple[ChatMessage, ...] = (),
    summary: ChatSummary | None = None,
    updated_at: datetime = BASE_TIME,
) -> ChatSession:
    return ChatSession(
        schema_version=CHAT_SESSION_SCHEMA_VERSION,
        chat_id=ChatId("chat_test"),
        title="Elysia AI",
        mode="chat",
        created_at=BASE_TIME,
        updated_at=updated_at,
        messages=messages,
        summary=summary,
        project_id=ProjectId("project_test"),
        model_settings=ChatModelSettings(
            model_name="qwen3.5:9b",
        ),
    )


def test_create_chat_session_builds_complete_empty_chat() -> None:
    session = create_chat_session(
        title="New chat",
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=ProjectId("project_123"),
        created_at=BASE_TIME,
    )

    assert session.schema_version == 1
    assert session.chat_id.startswith("chat_")
    assert session.chat_id != session.title
    assert session.mode == "chat"
    assert session.created_at == BASE_TIME
    assert session.updated_at == BASE_TIME
    assert session.messages == ()
    assert session.summary is None
    assert session.project_id == "project_123"
    assert session.model_settings.model_name == "qwen3.5:9b"


def test_chat_ids_do_not_depend_on_titles() -> None:
    first = create_chat_session(
        title="Same title",
        mode="chat",
        model_name="qwen3.5:9b",
        created_at=BASE_TIME,
    )
    second = create_chat_session(
        title="Same title",
        mode="chat",
        model_name="qwen3.5:9b",
        created_at=BASE_TIME,
    )

    assert first.chat_id != second.chat_id


def test_attachment_ids_do_not_depend_on_file_names() -> None:
    first = create_attachment_metadata(
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=10,
    )
    second = create_attachment_metadata(
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=10,
    )

    assert first.attachment_id.startswith("attachment_")
    assert first.attachment_id != first.file_name
    assert first.attachment_id != second.attachment_id


def test_create_chat_message_supports_attachment_only_input() -> None:
    attachment = create_attachment_metadata(
        file_name="diagram.png",
        media_type="image/png",
        size_bytes=512,
    )

    message = create_chat_message(
        role="user",
        content="",
        attachments=[attachment],
        created_at=BASE_TIME,
    )

    assert message.message_id.startswith("message_")
    assert message.created_at == BASE_TIME
    assert message.attachments == (attachment,)


def test_message_id_survives_content_regeneration() -> None:
    original = create_chat_message(
        role="assistant",
        content="First reply",
        created_at=BASE_TIME,
    )

    regenerated = replace(
        original,
        content="Regenerated reply",
    )

    assert regenerated.message_id == original.message_id
    assert regenerated.content == "Regenerated reply"


def test_chat_session_meta_omits_heavy_chat_content() -> None:
    message = _message("message_1")
    session = _session(messages=(message,))

    metadata = session.to_meta()

    assert metadata.chat_id == session.chat_id
    assert metadata.title == session.title
    assert metadata.mode == session.mode
    assert metadata.message_count == 1
    assert metadata.project_id == session.project_id
    assert metadata.model_name == "qwen3.5:9b"
    assert not hasattr(metadata, "messages")
    assert not hasattr(metadata, "summary")


def test_chat_summary_links_to_owned_message_ids() -> None:
    message = _message("message_1")
    summary = ChatSummary(
        facts=("Ying is developing Elysia AI.",),
        decisions=("Use stable message IDs.",),
        action_items=("Implement chat storage next.",),
        unresolved_questions=(),
        source_message_ids=(message.message_id,),
        updated_at=BASE_TIME,
    )

    session = _session(
        messages=(message,),
        summary=summary,
    )

    assert session.summary == summary


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("chat_id", ChatId("")),
        ("title", "   "),
        ("mode", cast(ConversationMode, "code")),
        ("project_id", ProjectId("")),
    ],
)
def test_chat_session_rejects_invalid_identity_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    values: dict[str, object] = {
        "schema_version": CHAT_SESSION_SCHEMA_VERSION,
        "chat_id": ChatId("chat_test"),
        "title": "Elysia AI",
        "mode": "chat",
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
        "messages": (),
        "summary": None,
        "project_id": ProjectId("project_test"),
        "model_settings": ChatModelSettings(
            model_name="qwen3.5:9b",
        ),
    }
    values[field_name] = invalid_value

    with pytest.raises(ValueError):
        ChatSession(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_version", [2, True])
def test_chat_session_rejects_unsupported_schema_version(
    invalid_version: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"Unsupported chat session schema version: (2|True)\.",
    ):
        ChatSession(
            schema_version=cast(Literal[1], invalid_version),
            chat_id=ChatId("chat_test"),
            title="Elysia AI",
            mode="chat",
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
            messages=(),
            summary=None,
            project_id=None,
            model_settings=ChatModelSettings(
                model_name="qwen3.5:9b",
            ),
        )


def test_chat_session_rejects_naive_timestamp() -> None:
    naive_time = datetime(2026, 8, 14, 12, 0)

    with pytest.raises(
        ValueError,
        match=(
            r"created_at must be a timezone-aware datetime\."
        ),
    ):
        create_chat_session(
            title="New chat",
            mode="chat",
            model_name="qwen3.5:9b",
            created_at=naive_time,
        )


def test_chat_session_rejects_updated_time_before_creation() -> None:
    with pytest.raises(
        ValueError,
        match=r"updated_at cannot be earlier than created_at\.",
    ):
        replace(
            _session(),
            updated_at=BASE_TIME - timedelta(seconds=1),
        )


def test_chat_session_rejects_duplicate_message_ids() -> None:
    first = _message("message_same")
    second = _message("message_same")

    with pytest.raises(
        ValueError,
        match=r"Chat message IDs must be unique\.",
    ):
        _session(messages=(first, second))


def test_chat_session_rejects_out_of_order_messages() -> None:
    first = _message(
        "message_1",
        created_at=BASE_TIME + timedelta(seconds=2),
    )
    second = _message(
        "message_2",
        created_at=BASE_TIME + timedelta(seconds=1),
    )

    with pytest.raises(
        ValueError,
        match=r"Chat messages must be in chronological order\.",
    ):
        _session(
            messages=(first, second),
            updated_at=BASE_TIME + timedelta(seconds=2),
        )


def test_chat_session_rejects_unknown_summary_message_id() -> None:
    message = _message("message_1")
    summary = ChatSummary(
        facts=(),
        decisions=(),
        action_items=(),
        unresolved_questions=("What is next?",),
        source_message_ids=(
            ChatMessageId("message_unknown"),
        ),
        updated_at=BASE_TIME,
    )

    with pytest.raises(
        ValueError,
        match=(
            r"Summary references messages outside this chat\."
        ),
    ):
        _session(
            messages=(message,),
            summary=summary,
        )


def test_chat_message_rejects_empty_content_without_attachment() -> None:
    with pytest.raises(
        ValueError,
        match=r"A message must contain text or an attachment\.",
    ):
        create_chat_message(
            role="user",
            content="   ",
            created_at=BASE_TIME,
        )


def test_chat_message_rejects_invalid_role() -> None:
    with pytest.raises(
        ValueError,
        match=r"role must be system, user, or assistant\.",
    ):
        create_chat_message(
            role=cast(ChatMessageRole, "tool"),
            content="Result",
            created_at=BASE_TIME,
        )


@pytest.mark.parametrize(
    "invalid_size",
    [-1, True, 1.5],
)
def test_attachment_rejects_invalid_size(
    invalid_size: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"size_bytes must be a non-negative integer\.",
    ):
        AttachmentMetadata(
            attachment_id=AttachmentId("attachment_test"),
            file_name="notes.txt",
            media_type="text/plain",
            size_bytes=invalid_size,  # type: ignore[arg-type]
        )