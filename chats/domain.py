"""Define the stable domain model shared by every Elysia chat surface."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Literal, NewType
from uuid import uuid4

CHAT_SESSION_SCHEMA_VERSION: Final[Literal[1]] = 1

ConversationMode = Literal["chat", "work"]
ChatMessageRole = Literal["system", "user", "assistant"]

# NewType prevents mypy from mixing identifiers that are all strings on disk.
ChatId = NewType("ChatId", str)
ChatMessageId = NewType("ChatMessageId", str)
AttachmentId = NewType("AttachmentId", str)
ProjectId = NewType("ProjectId", str)


def _validate_identifier(value: object, field_name: str) -> None:
    """Require one non-empty string identifier."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


def _validate_non_empty_text(value: object, field_name: str) -> None:
    """Require one non-empty human-readable string."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


def _validate_timestamp(value: object, field_name: str) -> None:
    """Require an aware datetime so persisted ordering is unambiguous."""

    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime."
        )


def _validate_text_entries(
    entries: object,
    field_name: str,
) -> None:
    """Validate one immutable sequence of non-empty summary entries."""

    if (
        not isinstance(entries, tuple)
        or not all(
            isinstance(entry, str) and entry.strip()
            for entry in entries
        )
    ):
        raise ValueError(
            f"{field_name} must be a tuple of non-empty strings."
        )


def _generate_stable_id(prefix: str) -> str:
    """Create an opaque ID that does not depend on editable display text."""

    return f"{prefix}_{uuid4().hex}"


def generate_chat_id() -> ChatId:
    """Return a new stable identifier for one chat session."""

    return ChatId(_generate_stable_id("chat"))


def generate_chat_message_id() -> ChatMessageId:
    """Return a new stable identifier for one chat message."""

    return ChatMessageId(_generate_stable_id("message"))


def generate_attachment_id() -> AttachmentId:
    """Return a new stable identifier for one message attachment."""

    return AttachmentId(_generate_stable_id("attachment"))


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    """Describe an attachment without loading or embedding its file bytes."""

    attachment_id: AttachmentId
    file_name: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        """Reject invalid metadata immediately after construction."""

        _validate_identifier(
            self.attachment_id,
            "attachment_id",
        )
        _validate_non_empty_text(self.file_name, "file_name")
        _validate_non_empty_text(self.media_type, "media_type")

        # bool is an int subclass but never represents a meaningful file size.
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ValueError(
                "size_bytes must be a non-negative integer."
            )


@dataclass(frozen=True, slots=True)
class ChatModelSettings:
    """Record the model selection attached to a specific chat."""

    model_name: str

    def __post_init__(self) -> None:
        """Require a usable model name."""

        _validate_non_empty_text(self.model_name, "model_name")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Represent one stable, timestamped message in a chat session."""

    message_id: ChatMessageId
    role: ChatMessageRole
    content: str
    created_at: datetime
    attachments: tuple[AttachmentMetadata, ...] = ()

    def __post_init__(self) -> None:
        """Validate message identity, content, time, and attachments."""

        _validate_identifier(self.message_id, "message_id")

        if self.role not in ("system", "user", "assistant"):
            raise ValueError(
                "role must be system, user, or assistant."
            )

        if not isinstance(self.content, str):
            raise ValueError("content must be a string.")

        _validate_timestamp(self.created_at, "created_at")

        if (
            not isinstance(self.attachments, tuple)
            or not all(
                isinstance(attachment, AttachmentMetadata)
                for attachment in self.attachments
            )
        ):
            raise ValueError(
                "attachments must be a tuple of AttachmentMetadata."
            )

        # Attachment-only user messages are valid, but empty messages are not.
        if not self.content.strip() and not self.attachments:
            raise ValueError(
                "A message must contain text or an attachment."
            )

        attachment_ids = [
            attachment.attachment_id
            for attachment in self.attachments
        ]

        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError(
                "Message attachment IDs must be unique."
            )


@dataclass(frozen=True, slots=True)
class ChatSummary:
    """Hold structured summary content linked to stable source messages."""

    facts: tuple[str, ...]
    decisions: tuple[str, ...]
    action_items: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    source_message_ids: tuple[ChatMessageId, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        """Validate summary categories and their source-message references."""

        for field_name in (
            "facts",
            "decisions",
            "action_items",
            "unresolved_questions",
        ):
            _validate_text_entries(
                getattr(self, field_name),
                field_name,
            )

        if (
            not isinstance(self.source_message_ids, tuple)
            or not self.source_message_ids
        ):
            raise ValueError(
                "source_message_ids must be a non-empty tuple."
            )

        for message_id in self.source_message_ids:
            _validate_identifier(message_id, "source_message_id")

        if len(self.source_message_ids) != len(
            set(self.source_message_ids)
        ):
            raise ValueError(
                "Summary source message IDs must be unique."
            )

        _validate_timestamp(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ChatSessionMeta:
    """Provide lightweight chat data for lists without loading messages."""

    schema_version: Literal[1]
    chat_id: ChatId
    title: str
    mode: ConversationMode
    created_at: datetime
    updated_at: datetime
    message_count: int
    project_id: ProjectId | None
    model_name: str
    is_pinned: bool = False
    is_archived: bool = False

    def __post_init__(self) -> None:
        """Validate list metadata independently from the full chat entity."""

        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != CHAT_SESSION_SCHEMA_VERSION
        ):
            raise ValueError(
                "Unsupported chat session schema version: "
                f"{self.schema_version}."
            )

        _validate_identifier(self.chat_id, "chat_id")
        _validate_non_empty_text(self.title, "title")

        if not isinstance(self.is_pinned, bool):
            raise ValueError("is_pinned must be a boolean.")

        if not isinstance(self.is_archived, bool):
            raise ValueError("is_archived must be a boolean.")
        
        if self.mode not in ("chat", "work"):
            raise ValueError("mode must be chat or work.")

        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.updated_at, "updated_at")

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot be earlier than created_at."
            )

        if (
            not isinstance(self.message_count, int)
            or isinstance(self.message_count, bool)
            or self.message_count < 0
        ):
            raise ValueError(
                "message_count must be a non-negative integer."
            )

        if self.project_id is not None:
            _validate_identifier(self.project_id, "project_id")

        _validate_non_empty_text(self.model_name, "model_name")


@dataclass(frozen=True, slots=True)
class ChatSession:
    """Represent the complete persistable conversation aggregate."""

    schema_version: Literal[1]
    chat_id: ChatId
    title: str
    mode: ConversationMode
    created_at: datetime
    updated_at: datetime
    messages: tuple[ChatMessage, ...]
    summary: ChatSummary | None
    project_id: ProjectId | None
    model_settings: ChatModelSettings
    is_pinned: bool = False
    is_archived: bool = False

    def __post_init__(self) -> None:
        """Enforce invariants across the complete chat aggregate."""

        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != CHAT_SESSION_SCHEMA_VERSION
        ):
            raise ValueError(
                "Unsupported chat session schema version: "
                f"{self.schema_version}."
            )

        _validate_identifier(self.chat_id, "chat_id")
        _validate_non_empty_text(self.title, "title")

        if self.mode not in ("chat", "work"):
            raise ValueError("mode must be chat or work.")

        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.updated_at, "updated_at")

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot be earlier than created_at."
            )

        if (
            not isinstance(self.messages, tuple)
            or not all(
                isinstance(message, ChatMessage)
                for message in self.messages
            )
        ):
            raise ValueError(
                "messages must be a tuple of ChatMessage."
            )

        if (
            self.summary is not None
            and not isinstance(self.summary, ChatSummary)
        ):
            raise ValueError(
                "summary must be ChatSummary or None."
            )

        if self.project_id is not None:
            _validate_identifier(self.project_id, "project_id")

        if not isinstance(self.model_settings, ChatModelSettings):
            raise ValueError(
                "model_settings must be ChatModelSettings."
            )

        if not isinstance(self.is_pinned, bool):
            raise ValueError("is_pinned must be a boolean.")

        if not isinstance(self.is_archived, bool):
            raise ValueError("is_archived must be a boolean.")
        
        self._validate_message_identity_and_time()
        self._validate_summary_references()

    def _validate_message_identity_and_time(self) -> None:
        """Require unique message IDs in chronological session order."""

        message_ids = [
            message.message_id
            for message in self.messages
        ]

        if len(message_ids) != len(set(message_ids)):
            raise ValueError("Chat message IDs must be unique.")

        previous_timestamp = self.created_at

        for message in self.messages:
            if message.created_at < previous_timestamp:
                raise ValueError(
                    "Chat messages must be in chronological order."
                )

            if message.created_at > self.updated_at:
                raise ValueError(
                    "Message created_at cannot be later than "
                    "the chat updated_at."
                )

            previous_timestamp = message.created_at

    def _validate_summary_references(self) -> None:
        """Ensure a summary only references messages owned by this chat."""

        if self.summary is None:
            return

        message_ids = {
            message.message_id
            for message in self.messages
        }
        unknown_ids = (
            set(self.summary.source_message_ids)
            - message_ids
        )

        if unknown_ids:
            raise ValueError(
                "Summary references messages outside this chat."
            )

        if not (
            self.created_at
            <= self.summary.updated_at
            <= self.updated_at
        ):
            raise ValueError(
                "Summary updated_at must fall within the chat lifetime."
            )

    def to_meta(self) -> ChatSessionMeta:
        """Create lightweight metadata without copying messages or summary."""

        return ChatSessionMeta(
            schema_version=self.schema_version,
            chat_id=self.chat_id,
            title=self.title,
            mode=self.mode,
            created_at=self.created_at,
            updated_at=self.updated_at,
            message_count=len(self.messages),
            project_id=self.project_id,
            model_name=self.model_settings.model_name,
            is_pinned=self.is_pinned,
            is_archived=self.is_archived,
        )


def create_attachment_metadata(
    *,
    file_name: str,
    media_type: str,
    size_bytes: int,
) -> AttachmentMetadata:
    """Create attachment metadata with an opaque stable ID."""

    return AttachmentMetadata(
        attachment_id=generate_attachment_id(),
        file_name=file_name,
        media_type=media_type,
        size_bytes=size_bytes,
    )


def create_chat_message(
    *,
    role: ChatMessageRole,
    content: str,
    attachments: Iterable[AttachmentMetadata] = (),
    created_at: datetime | None = None,
) -> ChatMessage:
    """Create a message with a stable ID and UTC timestamp."""

    message_created_at = (
        datetime.now(timezone.utc)
        if created_at is None
        else created_at
    )

    return ChatMessage(
        message_id=generate_chat_message_id(),
        role=role,
        content=content,
        created_at=message_created_at,
        attachments=tuple(attachments),
    )


def create_chat_session(
    *,
    title: str,
    mode: ConversationMode,
    model_name: str,
    project_id: ProjectId | None = None,
    created_at: datetime | None = None,
) -> ChatSession:
    """Create an empty versioned chat with a stable ID and model settings."""

    session_created_at = (
        datetime.now(timezone.utc)
        if created_at is None
        else created_at
    )

    return ChatSession(
        schema_version=CHAT_SESSION_SCHEMA_VERSION,
        chat_id=generate_chat_id(),
        title=title,
        mode=mode,
        created_at=session_created_at,
        updated_at=session_created_at,
        messages=(),
        summary=None,
        project_id=project_id,
        model_settings=ChatModelSettings(
            model_name=model_name,
        ),
    )
