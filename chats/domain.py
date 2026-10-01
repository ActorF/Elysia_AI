"""Define the stable domain model shared by every Elysia chat surface."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Literal, NewType
from uuid import uuid4

CHAT_SESSION_SCHEMA_VERSION: Final[Literal[1]] = 1
MAX_ATTACHMENTS_PER_MESSAGE: Final = 10
MAX_ATTACHMENT_FILE_NAME_LENGTH: Final = 255
MAX_ATTACHMENT_MEDIA_TYPE_LENGTH: Final = 255

_ATTACHMENT_ID_PATTERN = re.compile(
    r"^attachment_[A-Za-z0-9_-]{1,117}$"
)
_WINDOWS_RESERVED_FILE_STEMS: Final = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})
_BIDI_CONTROL_CODE_POINTS: Final = frozenset({
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
})
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)

ConversationMode = Literal["chat", "work"]
ChatMessageRole = Literal["system", "user", "assistant"]
ChatGroundedAnswerStatus = Literal["answered", "insufficient_evidence"]
ChatGroundedStatementKind = Literal[
    "source_fact",
    "model_summary",
    "inference",
]

# NewType prevents mypy from mixing identifiers that are all strings on disk.
ChatId = NewType("ChatId", str)
ChatMessageId = NewType("ChatMessageId", str)
AttachmentId = NewType("AttachmentId", str)
ProjectId = NewType("ProjectId", str)

_CITATION_ID_PATTERN = re.compile(r"^citation_[0-9a-f]{64}$")
_STATEMENT_ID_PATTERN = re.compile(r"^statement_[0-9]{3}$")
_GROUNDED_STATEMENT_KINDS: Final = frozenset(
    {"source_fact", "model_summary", "inference"}
)
MAX_GROUNDED_CHAT_STATEMENTS: Final = 32
MAX_GROUNDED_CHAT_CITATIONS: Final = 64
MAX_GROUNDED_CHAT_CONTEXT_PASSAGES: Final = 20
MAX_GROUNDED_CHAT_LOCATIONS: Final = 100_000
MAX_GROUNDED_CHAT_STATEMENT_LENGTH: Final = 4_000
MAX_GROUNDED_CHAT_TOTAL_STATEMENT_LENGTH: Final = 16_000
MAX_GROUNDED_CHAT_EXCERPT_LENGTH: Final = 2_000


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
        if _ATTACHMENT_ID_PATTERN.fullmatch(self.attachment_id) is None:
            raise ValueError("attachment_id has an invalid opaque-ID format.")
        _validate_non_empty_text(self.file_name, "file_name")
        if (
            self.file_name != self.file_name.strip()
            or self.file_name in {".", ".."}
            or self.file_name.endswith(".")
            or len(self.file_name) > MAX_ATTACHMENT_FILE_NAME_LENGTH
            or any(
                character in '<>:"/\\|?*'
                for character in self.file_name
            )
            or self.file_name.split(".", 1)[0].upper()
            in _WINDOWS_RESERVED_FILE_STEMS
            or any(
                ord(character) < 32
                or ord(character) == 127
                or ord(character) in _BIDI_CONTROL_CODE_POINTS
                for character in self.file_name
            )
        ):
            raise ValueError("file_name must be a safe display basename.")
        _validate_non_empty_text(self.media_type, "media_type")
        if (
            self.media_type != self.media_type.strip()
            or len(self.media_type) > MAX_ATTACHMENT_MEDIA_TYPE_LENGTH
            or _MEDIA_TYPE_PATTERN.fullmatch(self.media_type) is None
        ):
            raise ValueError("media_type must be a normalized media type.")

        # bool is an int subclass but never represents a meaningful file size.
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes <= 0
        ):
            raise ValueError(
                "size_bytes must be a positive integer."
            )


@dataclass(frozen=True, slots=True)
class ChatModelSettings:
    """Record the model selection attached to a specific chat."""

    model_name: str

    def __post_init__(self) -> None:
        """Require a usable model name."""

        _validate_non_empty_text(self.model_name, "model_name")


@dataclass(frozen=True, slots=True)
class ChatGroundedTextLocation:
    """Persist one exact text span without retaining a source path or hash."""

    block_ordinal: int
    source_start_code_point: int
    source_end_code_point: int

    def __post_init__(self) -> None:
        """Require one non-empty, zero-based half-open source range."""

        values = (
            self.block_ordinal,
            self.source_start_code_point,
            self.source_end_code_point,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Grounded text locations must use non-negative integers.")
        if self.source_end_code_point <= self.source_start_code_point:
            raise ValueError("Grounded text locations must contain a non-empty range.")


@dataclass(frozen=True, slots=True)
class ChatGroundedTableLocation:
    """Persist one table-cell span for keyboard-accessible citation details."""

    block_ordinal: int
    row_index: int
    column_index: int
    source_start_code_point: int
    source_end_code_point: int

    def __post_init__(self) -> None:
        """Require non-negative cell coordinates and a valid half-open range."""

        values = (
            self.block_ordinal,
            self.row_index,
            self.column_index,
            self.source_start_code_point,
            self.source_end_code_point,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Grounded table locations must use non-negative integers.")
        if self.source_end_code_point < self.source_start_code_point:
            raise ValueError("Grounded table locations contain an invalid range.")


ChatGroundedCitationLocation = (
    ChatGroundedTextLocation | ChatGroundedTableLocation
)


@dataclass(frozen=True, slots=True)
class ChatGroundedCitation:
    """Persist renderer-safe evidence and its source-document location."""

    citation_id: str
    kind: Literal["prose", "code", "table"]
    excerpt: str
    file_name: str
    media_type: str
    page_number: int | None
    locations: tuple[ChatGroundedCitationLocation, ...]

    def __post_init__(self) -> None:
        """Reject path-bearing labels, oversized evidence, and bad locations."""

        if _CITATION_ID_PATTERN.fullmatch(self.citation_id) is None:
            raise ValueError("citation_id has an invalid opaque-ID format.")
        if self.kind not in ("prose", "code", "table"):
            raise ValueError("Grounded citation kind is invalid.")
        if (
            not isinstance(self.excerpt, str)
            or not self.excerpt
            or len(self.excerpt) > MAX_GROUNDED_CHAT_EXCERPT_LENGTH
        ):
            raise ValueError("Grounded citation excerpt is invalid.")
        # Reuse AttachmentMetadata's hardened display-label checks without
        # inventing an attachment identity or retaining the original path.
        AttachmentMetadata(
            attachment_id=AttachmentId("attachment_citation"),
            file_name=self.file_name,
            media_type=self.media_type,
            size_bytes=1,
        )
        if self.page_number is not None and (
            type(self.page_number) is not int or self.page_number <= 0
        ):
            raise ValueError("Grounded citation page_number is invalid.")
        if (
            not isinstance(self.locations, tuple)
            or not self.locations
            or len(self.locations) > MAX_GROUNDED_CHAT_LOCATIONS
            or not all(
                isinstance(
                    location,
                    (ChatGroundedTextLocation, ChatGroundedTableLocation),
                )
                for location in self.locations
            )
        ):
            raise ValueError("Grounded citation locations are invalid.")
        if self.kind == "table" and not all(
            isinstance(location, ChatGroundedTableLocation)
            for location in self.locations
        ):
            raise ValueError("Table citations require table-cell locations.")
        if self.kind != "table" and not all(
            isinstance(location, ChatGroundedTextLocation)
            for location in self.locations
        ):
            raise ValueError("Text citations require text locations.")


@dataclass(frozen=True, slots=True)
class ChatGroundedStatement:
    """Persist one labeled answer statement and its closed citation set."""

    statement_id: str
    kind: ChatGroundedStatementKind
    text: str
    citation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require bounded meaningful text and unique opaque citations."""

        if _STATEMENT_ID_PATTERN.fullmatch(self.statement_id) is None:
            raise ValueError("statement_id has an invalid opaque-ID format.")
        if self.kind not in _GROUNDED_STATEMENT_KINDS:
            raise ValueError("Grounded statement kind is invalid.")
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or len(self.text) > MAX_GROUNDED_CHAT_STATEMENT_LENGTH
        ):
            raise ValueError("Grounded statement text is invalid.")
        if (
            not isinstance(self.citation_ids, tuple)
            or not self.citation_ids
            or len(self.citation_ids) > MAX_GROUNDED_CHAT_CITATIONS
            or any(
                _CITATION_ID_PATTERN.fullmatch(citation_id) is None
                for citation_id in self.citation_ids
            )
            or len(set(self.citation_ids)) != len(self.citation_ids)
        ):
            raise ValueError("Grounded statement citations are invalid.")


@dataclass(frozen=True, slots=True)
class ChatGroundedAnswer:
    """Persist one verified Project answer beside its canonical Chat text."""

    status: ChatGroundedAnswerStatus
    context_passage_count: int
    statements: tuple[ChatGroundedStatement, ...]
    citations: tuple[ChatGroundedCitation, ...]

    def __post_init__(self) -> None:
        """Enforce answered/refusal coherence and a closed citation union."""

        if self.status not in ("answered", "insufficient_evidence"):
            raise ValueError("Grounded answer status is invalid.")
        if (
            type(self.context_passage_count) is not int
            or not 0
            <= self.context_passage_count
            <= MAX_GROUNDED_CHAT_CONTEXT_PASSAGES
        ):
            raise ValueError("Grounded context passage count is invalid.")
        if (
            not isinstance(self.statements, tuple)
            or len(self.statements) > MAX_GROUNDED_CHAT_STATEMENTS
            or not all(
                isinstance(statement, ChatGroundedStatement)
                for statement in self.statements
            )
        ):
            raise ValueError("Grounded answer statements are invalid.")
        if sum(len(statement.text) for statement in self.statements) > (
            MAX_GROUNDED_CHAT_TOTAL_STATEMENT_LENGTH
        ):
            raise ValueError("Grounded answer statement text is too large.")
        if (
            not isinstance(self.citations, tuple)
            or len(self.citations) > MAX_GROUNDED_CHAT_CITATIONS
            or not all(
                isinstance(citation, ChatGroundedCitation)
                for citation in self.citations
            )
        ):
            raise ValueError("Grounded answer citations are invalid.")
        citation_ids = tuple(
            citation.citation_id for citation in self.citations
        )
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("Grounded answer citation IDs must be unique.")
        if sum(len(citation.locations) for citation in self.citations) > (
            MAX_GROUNDED_CHAT_LOCATIONS
        ):
            raise ValueError("Grounded answer has too many citation locations.")
        referenced = {
            citation_id
            for statement in self.statements
            for citation_id in statement.citation_ids
        }
        if referenced != set(citation_ids):
            raise ValueError("Grounded answer citations must form a closed union.")
        if self.status == "answered" and not self.statements:
            raise ValueError("An answered grounded result needs statements.")
        if self.status == "insufficient_evidence" and (
            self.statements or self.citations
        ):
            raise ValueError("An insufficient grounded result cannot cite evidence.")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Represent one stable, timestamped message in a chat session."""

    message_id: ChatMessageId
    role: ChatMessageRole
    content: str
    created_at: datetime
    attachments: tuple[AttachmentMetadata, ...] = ()
    grounded_answer: ChatGroundedAnswer | None = None

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
        if len(self.attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError(
                "A message cannot contain more than "
                f"{MAX_ATTACHMENTS_PER_MESSAGE} attachments."
            )
        if self.grounded_answer is not None and (
            self.role != "assistant"
            or not isinstance(self.grounded_answer, ChatGroundedAnswer)
        ):
            raise ValueError(
                "Only assistant messages can carry a grounded answer."
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

        attachment_ids = [
            attachment.attachment_id
            for message in self.messages
            for attachment in message.attachments
        ]
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError(
                "Attachment IDs must be unique across one Chat."
            )

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
    grounded_answer: ChatGroundedAnswer | None = None,
    created_at: datetime | None = None,
) -> ChatMessage:
    """Create a message with a stable ID and an optional aware timestamp.

    When no timestamp is supplied, the current UTC time is used.
    """

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
        grounded_answer=grounded_answer,
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
