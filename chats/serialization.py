"""Convert validated chat domain objects to and from JSON-shaped data."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Final, Literal, cast

from .domain import (
    CHAT_SESSION_SCHEMA_VERSION,
    AttachmentId,
    AttachmentMetadata,
    ChatId,
    ChatGroundedAnswer,
    ChatGroundedCitation,
    ChatGroundedStatement,
    ChatGroundedTableLocation,
    ChatGroundedTextLocation,
    ChatMessage,
    ChatMessageId,
    ChatMessageRole,
    ChatModelSettings,
    ChatSession,
    ChatSessionMeta,
    ChatSummary,
    ConversationMode,
    ProjectId,
)
from .storage import JsonObject

CHAT_INDEX_SCHEMA_VERSION: Final[Literal[1]] = 1


def _required(
    data: Mapping[str, object],
    field_name: str,
) -> object:
    """Return one required stored field."""

    if field_name not in data:
        raise ValueError(f"Missing required field: {field_name}.")

    return data[field_name]


def _as_object(value: object, field_name: str) -> JsonObject:
    """Require a dictionary with string keys."""

    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{field_name} must be an object.")

    return cast(JsonObject, value)


def _as_list(value: object, field_name: str) -> list[object]:
    """Require a JSON array."""

    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array.")

    return cast(list[object], value)


def _as_string(value: object, field_name: str) -> str:
    """Require a stored string value."""

    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")

    return value


def _as_integer(value: object, field_name: str) -> int:
    """Require an integer while rejecting booleans."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")

    return value


def _as_boolean(value: object, field_name: str) -> bool:
    """Require a real JSON boolean."""

    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")

    return value


def _datetime_to_text(value: datetime) -> str:
    """Serialize an aware datetime in normalized UTC ISO 8601 form."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Stored timestamps must be timezone-aware.")

    return value.astimezone(timezone.utc).isoformat()


def _datetime_from_value(
    value: object,
    field_name: str,
) -> datetime:
    """Parse one timezone-aware ISO 8601 timestamp."""

    timestamp_text = _as_string(value, field_name)

    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be an ISO 8601 datetime."
        ) from error

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone.")

    return timestamp


def _attachment_to_data(
    attachment: AttachmentMetadata,
) -> JsonObject:
    """Serialize attachment metadata without file bytes."""

    return {
        "attachment_id": str(attachment.attachment_id),
        "file_name": attachment.file_name,
        "media_type": attachment.media_type,
        "size_bytes": attachment.size_bytes,
    }


def _attachment_from_value(value: object) -> AttachmentMetadata:
    """Build validated attachment metadata from stored JSON."""

    data = _as_object(value, "attachment")
    if set(data) != {
        "attachment_id",
        "file_name",
        "media_type",
        "size_bytes",
    }:
        raise ValueError("attachment has invalid fields.")
    return AttachmentMetadata(
        attachment_id=AttachmentId(
            _as_string(
                _required(data, "attachment_id"),
                "attachment_id",
            )
        ),
        file_name=_as_string(
            _required(data, "file_name"),
            "file_name",
        ),
        media_type=_as_string(
            _required(data, "media_type"),
            "media_type",
        ),
        size_bytes=_as_integer(
            _required(data, "size_bytes"),
            "size_bytes",
        ),
    )


def _grounded_location_to_data(
    location: ChatGroundedTextLocation | ChatGroundedTableLocation,
) -> JsonObject:
    """Serialize one citation location with an explicit shape discriminator."""

    shared: JsonObject = {
        "block_ordinal": location.block_ordinal,
        "source_start_code_point": location.source_start_code_point,
        "source_end_code_point": location.source_end_code_point,
    }
    if isinstance(location, ChatGroundedTableLocation):
        return {
            "kind": "table",
            **shared,
            "row_index": location.row_index,
            "column_index": location.column_index,
        }
    return {"kind": "text", **shared}


def _grounded_location_from_value(
    value: object,
) -> ChatGroundedTextLocation | ChatGroundedTableLocation:
    """Build one validated citation location from persisted JSON."""

    data = _as_object(value, "grounded citation location")
    kind = _as_string(_required(data, "kind"), "location kind")
    shared = {
        "block_ordinal": _as_integer(
            _required(data, "block_ordinal"),
            "block_ordinal",
        ),
        "source_start_code_point": _as_integer(
            _required(data, "source_start_code_point"),
            "source_start_code_point",
        ),
        "source_end_code_point": _as_integer(
            _required(data, "source_end_code_point"),
            "source_end_code_point",
        ),
    }
    if kind == "text":
        if set(data) != {
            "kind",
            "block_ordinal",
            "source_start_code_point",
            "source_end_code_point",
        }:
            raise ValueError("grounded text location has invalid fields.")
        return ChatGroundedTextLocation(**shared)
    if kind == "table":
        if set(data) != {
            "kind",
            "block_ordinal",
            "row_index",
            "column_index",
            "source_start_code_point",
            "source_end_code_point",
        }:
            raise ValueError("grounded table location has invalid fields.")
        return ChatGroundedTableLocation(
            **shared,
            row_index=_as_integer(
                _required(data, "row_index"),
                "row_index",
            ),
            column_index=_as_integer(
                _required(data, "column_index"),
                "column_index",
            ),
        )
    raise ValueError("grounded citation location kind is invalid.")


def _grounded_answer_to_data(answer: ChatGroundedAnswer) -> JsonObject:
    """Serialize renderer-safe grounded statements and citation evidence."""

    return {
        "status": answer.status,
        "context_passage_count": answer.context_passage_count,
        "statements": [
            {
                "statement_id": statement.statement_id,
                "kind": statement.kind,
                "text": statement.text,
                "citation_ids": list(statement.citation_ids),
            }
            for statement in answer.statements
        ],
        "citations": [
            {
                "citation_id": citation.citation_id,
                "kind": citation.kind,
                "excerpt": citation.excerpt,
                "file_name": citation.file_name,
                "media_type": citation.media_type,
                "page_number": citation.page_number,
                "locations": [
                    _grounded_location_to_data(location)
                    for location in citation.locations
                ],
            }
            for citation in answer.citations
        ],
    }


def _grounded_answer_from_value(value: object) -> ChatGroundedAnswer:
    """Build one closed grounded answer from backward-compatible storage."""

    data = _as_object(value, "grounded_answer")
    if set(data) != {
        "status",
        "context_passage_count",
        "statements",
        "citations",
    }:
        raise ValueError("grounded_answer has invalid fields.")
    statements: list[ChatGroundedStatement] = []
    for value_statement in _as_list(
        _required(data, "statements"),
        "grounded statements",
    ):
        statement = _as_object(value_statement, "grounded statement")
        if set(statement) != {
            "statement_id",
            "kind",
            "text",
            "citation_ids",
        }:
            raise ValueError("grounded statement has invalid fields.")
        statements.append(
            ChatGroundedStatement(
                statement_id=_as_string(
                    _required(statement, "statement_id"),
                    "statement_id",
                ),
                kind=cast(
                    Literal["source_fact", "model_summary", "inference"],
                    _as_string(_required(statement, "kind"), "kind"),
                ),
                text=_as_string(_required(statement, "text"), "text"),
                citation_ids=_string_tuple_from_value(
                    _required(statement, "citation_ids"),
                    "citation_id",
                ),
            )
        )
    citations: list[ChatGroundedCitation] = []
    for value_citation in _as_list(
        _required(data, "citations"),
        "grounded citations",
    ):
        citation = _as_object(value_citation, "grounded citation")
        if set(citation) != {
            "citation_id",
            "kind",
            "excerpt",
            "file_name",
            "media_type",
            "page_number",
            "locations",
        }:
            raise ValueError("grounded citation has invalid fields.")
        page_number = _required(citation, "page_number")
        citations.append(
            ChatGroundedCitation(
                citation_id=_as_string(
                    _required(citation, "citation_id"),
                    "citation_id",
                ),
                kind=cast(
                    Literal["prose", "code", "table"],
                    _as_string(_required(citation, "kind"), "kind"),
                ),
                excerpt=_as_string(
                    _required(citation, "excerpt"),
                    "excerpt",
                ),
                file_name=_as_string(
                    _required(citation, "file_name"),
                    "file_name",
                ),
                media_type=_as_string(
                    _required(citation, "media_type"),
                    "media_type",
                ),
                page_number=(
                    None
                    if page_number is None
                    else _as_integer(page_number, "page_number")
                ),
                locations=tuple(
                    _grounded_location_from_value(location)
                    for location in _as_list(
                        _required(citation, "locations"),
                        "grounded citation locations",
                    )
                ),
            )
        )
    return ChatGroundedAnswer(
        status=cast(
            Literal["answered", "insufficient_evidence"],
            _as_string(_required(data, "status"), "status"),
        ),
        context_passage_count=_as_integer(
            _required(data, "context_passage_count"),
            "context_passage_count",
        ),
        statements=tuple(statements),
        citations=tuple(citations),
    )


def _message_to_data(message: ChatMessage) -> JsonObject:
    """Serialize one chat message and its attachment metadata."""

    data: JsonObject = {
        "message_id": str(message.message_id),
        "role": message.role,
        "content": message.content,
        "created_at": _datetime_to_text(message.created_at),
        "attachments": [
            _attachment_to_data(attachment)
            for attachment in message.attachments
        ],
    }
    if message.grounded_answer is not None:
        data["grounded_answer"] = _grounded_answer_to_data(
            message.grounded_answer
        )
    return data


def _message_from_value(value: object) -> ChatMessage:
    """Build one validated chat message from stored JSON."""

    data = _as_object(value, "message")
    role = _as_string(_required(data, "role"), "role")
    attachments = _as_list(
        _required(data, "attachments"),
        "attachments",
    )
    grounded_value = data.get("grounded_answer")

    return ChatMessage(
        message_id=ChatMessageId(
            _as_string(
                _required(data, "message_id"),
                "message_id",
            )
        ),
        role=cast(ChatMessageRole, role),
        content=_as_string(
            _required(data, "content"),
            "content",
        ),
        created_at=_datetime_from_value(
            _required(data, "created_at"),
            "created_at",
        ),
        attachments=tuple(
            _attachment_from_value(attachment)
            for attachment in attachments
        ),
        grounded_answer=(
            None
            if grounded_value is None
            else _grounded_answer_from_value(grounded_value)
        ),
    )


def _summary_to_data(summary: ChatSummary) -> JsonObject:
    """Serialize a structured summary and stable source references."""

    return {
        "facts": list(summary.facts),
        "decisions": list(summary.decisions),
        "action_items": list(summary.action_items),
        "unresolved_questions": list(summary.unresolved_questions),
        "source_message_ids": [
            str(message_id)
            for message_id in summary.source_message_ids
        ],
        "updated_at": _datetime_to_text(summary.updated_at),
    }


def _string_tuple_from_value(
    value: object,
    field_name: str,
) -> tuple[str, ...]:
    """Convert a JSON string array into an immutable tuple."""

    return tuple(
        _as_string(entry, field_name)
        for entry in _as_list(value, field_name)
    )


def _summary_from_value(value: object) -> ChatSummary:
    """Build one validated summary from stored JSON."""

    data = _as_object(value, "summary")
    source_ids = _as_list(
        _required(data, "source_message_ids"),
        "source_message_ids",
    )

    return ChatSummary(
        facts=_string_tuple_from_value(
            _required(data, "facts"),
            "facts",
        ),
        decisions=_string_tuple_from_value(
            _required(data, "decisions"),
            "decisions",
        ),
        action_items=_string_tuple_from_value(
            _required(data, "action_items"),
            "action_items",
        ),
        unresolved_questions=_string_tuple_from_value(
            _required(data, "unresolved_questions"),
            "unresolved_questions",
        ),
        source_message_ids=tuple(
            ChatMessageId(
                _as_string(message_id, "source_message_id")
            )
            for message_id in source_ids
        ),
        updated_at=_datetime_from_value(
            _required(data, "updated_at"),
            "updated_at",
        ),
    )


def session_to_data(session: ChatSession) -> JsonObject:
    """Serialize a complete chat session for its detail file."""

    summary_data: JsonObject | None = None
    if session.summary is not None:
        summary_data = _summary_to_data(session.summary)

    return {
        "schema_version": session.schema_version,
        "chat_id": str(session.chat_id),
        "title": session.title,
        "mode": session.mode,
        "created_at": _datetime_to_text(session.created_at),
        "updated_at": _datetime_to_text(session.updated_at),
        "messages": [
            _message_to_data(message)
            for message in session.messages
        ],
        "summary": summary_data,
        "project_id": (
            None
            if session.project_id is None
            else str(session.project_id)
        ),
        "model_settings": {
            "model_name": session.model_settings.model_name,
        },
        "is_pinned": session.is_pinned,
        "is_archived": session.is_archived,
    }


def session_from_data(data: Mapping[str, object]) -> ChatSession:
    """Build a complete validated chat session from detail JSON."""

    schema_version = _as_integer(
        _required(data, "schema_version"),
        "schema_version",
    )
    if schema_version != CHAT_SESSION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported chat session schema version: {schema_version}."
        )

    mode = _as_string(_required(data, "mode"), "mode")
    messages = _as_list(
        _required(data, "messages"),
        "messages",
    )
    summary_value = _required(data, "summary")
    project_value = _required(data, "project_id")
    model_data = _as_object(
        _required(data, "model_settings"),
        "model_settings",
    )

    return ChatSession(
        schema_version=CHAT_SESSION_SCHEMA_VERSION,
        chat_id=ChatId(
            _as_string(_required(data, "chat_id"), "chat_id")
        ),
        title=_as_string(_required(data, "title"), "title"),
        mode=cast(ConversationMode, mode),
        created_at=_datetime_from_value(
            _required(data, "created_at"),
            "created_at",
        ),
        updated_at=_datetime_from_value(
            _required(data, "updated_at"),
            "updated_at",
        ),
        messages=tuple(
            _message_from_value(message)
            for message in messages
        ),
        summary=(
            None
            if summary_value is None
            else _summary_from_value(summary_value)
        ),
        project_id=(
            None
            if project_value is None
            else ProjectId(
                _as_string(project_value, "project_id")
            )
        ),
        model_settings=ChatModelSettings(
            model_name=_as_string(
                _required(model_data, "model_name"),
                "model_name",
            )
        ),
        is_pinned=_as_boolean(
            _required(data, "is_pinned"),
            "is_pinned",
        ),
        is_archived=_as_boolean(
            _required(data, "is_archived"),
            "is_archived",
        ),
    )


def metadata_to_data(metadata: ChatSessionMeta) -> JsonObject:
    """Serialize one lightweight chat index entry."""

    return {
        "schema_version": metadata.schema_version,
        "chat_id": str(metadata.chat_id),
        "title": metadata.title,
        "mode": metadata.mode,
        "created_at": _datetime_to_text(metadata.created_at),
        "updated_at": _datetime_to_text(metadata.updated_at),
        "message_count": metadata.message_count,
        "project_id": (
            None
            if metadata.project_id is None
            else str(metadata.project_id)
        ),
        "model_name": metadata.model_name,
        "is_pinned": metadata.is_pinned,
        "is_archived": metadata.is_archived,
    }


def metadata_from_value(value: object) -> ChatSessionMeta:
    """Build one validated lightweight index entry."""

    data = _as_object(value, "chat metadata")
    schema_version = _as_integer(
        _required(data, "schema_version"),
        "schema_version",
    )
    if schema_version != CHAT_SESSION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported chat session schema version: {schema_version}."
        )

    mode = _as_string(_required(data, "mode"), "mode")
    project_value = _required(data, "project_id")

    return ChatSessionMeta(
        schema_version=CHAT_SESSION_SCHEMA_VERSION,
        chat_id=ChatId(
            _as_string(_required(data, "chat_id"), "chat_id")
        ),
        title=_as_string(_required(data, "title"), "title"),
        mode=cast(ConversationMode, mode),
        created_at=_datetime_from_value(
            _required(data, "created_at"),
            "created_at",
        ),
        updated_at=_datetime_from_value(
            _required(data, "updated_at"),
            "updated_at",
        ),
        message_count=_as_integer(
            _required(data, "message_count"),
            "message_count",
        ),
        project_id=(
            None
            if project_value is None
            else ProjectId(
                _as_string(project_value, "project_id")
            )
        ),
        model_name=_as_string(
            _required(data, "model_name"),
            "model_name",
        ),
        is_pinned=_as_boolean(
            _required(data, "is_pinned"),
            "is_pinned",
        ),
        is_archived=_as_boolean(
            _required(data, "is_archived"),
            "is_archived",
        ),
    )


def index_to_data(
    metadata_entries: Iterable[ChatSessionMeta],
) -> JsonObject:
    """Serialize the complete lightweight chat index."""

    return {
        "schema_version": CHAT_INDEX_SCHEMA_VERSION,
        "chats": [
            metadata_to_data(metadata)
            for metadata in metadata_entries
        ],
    }


def index_from_data(
    data: Mapping[str, object],
) -> tuple[ChatSessionMeta, ...]:
    """Build and validate all entries from the lightweight index."""

    schema_version = _as_integer(
        _required(data, "schema_version"),
        "schema_version",
    )
    if schema_version != CHAT_INDEX_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported chat index schema version: {schema_version}."
        )

    metadata_entries = tuple(
        metadata_from_value(entry)
        for entry in _as_list(_required(data, "chats"), "chats")
    )
    chat_ids = [entry.chat_id for entry in metadata_entries]

    if len(chat_ids) != len(set(chat_ids)):
        raise ValueError("Chat index contains duplicate chat IDs.")

    return metadata_entries
