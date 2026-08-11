"""Data contracts for saved conversation summaries."""

from pathlib import Path
from typing import Final, Literal, TypedDict, cast

from .json_store import load_json_or_default, write_json

CONVERSATION_SUMMARY_SCHEMA_VERSION: Final = 1


class ConversationSummaryContent(TypedDict):
    """Structured information compressed from a conversation."""

    facts: list[str]
    decisions: list[str]
    action_items: list[str]
    unresolved_questions: list[str]


class ConversationSummary(TypedDict):
    """One summary linked to the raw messages it covers."""

    content: ConversationSummaryContent
    source_message_count: int
    source_start_timestamp: str
    source_end_timestamp: str
    updated_at: str


class ConversationSummaryData(TypedDict):
    """Top-level JSON structure stored on disk."""

    schema_version: Literal[1]
    summary: ConversationSummary | None


_CONVERSATION_SUMMARY_CONTENT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "facts",
        "decisions",
        "action_items",
        "unresolved_questions",
    }
)


_CONVERSATION_SUMMARY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "content",
        "source_message_count",
        "source_start_timestamp",
        "source_end_timestamp",
        "updated_at",
    }
)


_CONVERSATION_SUMMARY_DATA_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "summary",
    }
)


def validate_conversation_summary_content(
    data: object,
) -> ConversationSummaryContent:
    """Validate the four structured summary categories."""

    if not isinstance(data, dict):
        raise ValueError(
            "Conversation summary content must be a JSON object."
        )

    if not all(
        isinstance(field_name, str)
        for field_name in data
    ):
        raise ValueError(
            "Conversation summary content field names "
            "must be strings."
        )

    content_data = cast(dict[str, object], data)
    actual_fields = set(content_data)

    missing_fields = (
        _CONVERSATION_SUMMARY_CONTENT_FIELDS
        - actual_fields
    )
    unknown_fields = (
        actual_fields
        - _CONVERSATION_SUMMARY_CONTENT_FIELDS
    )

    if missing_fields:
        raise ValueError(
            "Conversation summary content is missing "
            "required fields: "
            f"{', '.join(sorted(missing_fields))}."
        )

    if unknown_fields:
        raise ValueError(
            "Conversation summary content contains "
            "unknown fields: "
            f"{', '.join(sorted(unknown_fields))}."
        )

    for field_name in (
        "facts",
        "decisions",
        "action_items",
        "unresolved_questions",
    ):
        entries = content_data[field_name]

        if (
            not isinstance(entries, list)
            or not all(
                isinstance(entry, str)
                and entry.strip() != ""
                for entry in entries
            )
        ):
            raise ValueError(
                f"{field_name} must be a list of "
                "non-empty strings."
            )

    return cast(
        ConversationSummaryContent,
        content_data,
    )


def validate_conversation_summary(
    data: object,
) -> ConversationSummary:
    """Validate one summary and its source-message metadata."""

    if not isinstance(data, dict):
        raise ValueError(
            "Conversation summary must be a JSON object."
        )

    if not all(
        isinstance(field_name, str)
        for field_name in data
    ):
        raise ValueError(
            "Conversation summary field names must be strings."
        )

    summary_data = cast(dict[str, object], data)
    actual_fields = set(summary_data)

    missing_fields = (
        _CONVERSATION_SUMMARY_FIELDS
        - actual_fields
    )
    unknown_fields = (
        actual_fields
        - _CONVERSATION_SUMMARY_FIELDS
    )

    if missing_fields:
        raise ValueError(
            "Conversation summary is missing required fields: "
            f"{', '.join(sorted(missing_fields))}."
        )

    if unknown_fields:
        raise ValueError(
            "Conversation summary contains unknown fields: "
            f"{', '.join(sorted(unknown_fields))}."
        )

    validate_conversation_summary_content(
        summary_data["content"]
    )

    source_message_count = summary_data[
        "source_message_count"
    ]

    if (
        not isinstance(source_message_count, int)
        or isinstance(source_message_count, bool)
        or source_message_count <= 0
    ):
        raise ValueError(
            "source_message_count must be a positive integer."
        )

    for field_name in (
        "source_start_timestamp",
        "source_end_timestamp",
        "updated_at",
    ):
        timestamp = summary_data[field_name]

        if (
            not isinstance(timestamp, str)
            or timestamp.strip() == ""
        ):
            raise ValueError(
                f"{field_name} must be a non-empty string."
            )

    return cast(
        ConversationSummary,
        summary_data,
    )


def validate_conversation_summary_data(
    data: object,
) -> ConversationSummaryData:
    """Validate the top-level conversation summary file data."""

    if not isinstance(data, dict):
        raise ValueError(
            "Conversation summary data must be a JSON object."
        )

    if not all(
        isinstance(field_name, str)
        for field_name in data
    ):
        raise ValueError(
            "Conversation summary data field names must be strings."
        )

    file_data = cast(dict[str, object], data)
    actual_fields = set(file_data)

    missing_fields = (
        _CONVERSATION_SUMMARY_DATA_FIELDS
        - actual_fields
    )
    unknown_fields = (
        actual_fields
        - _CONVERSATION_SUMMARY_DATA_FIELDS
    )

    if missing_fields:
        raise ValueError(
            "Conversation summary data is missing required fields: "
            f"{', '.join(sorted(missing_fields))}."
        )

    if unknown_fields:
        raise ValueError(
            "Conversation summary data contains unknown fields: "
            f"{', '.join(sorted(unknown_fields))}."
        )

    schema_version = file_data["schema_version"]

    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version
        != CONVERSATION_SUMMARY_SCHEMA_VERSION
    ):
        raise ValueError(
            "Conversation summary schema_version must be "
            f"{CONVERSATION_SUMMARY_SCHEMA_VERSION}."
        )

    summary = file_data["summary"]

    if summary is not None:
        validate_conversation_summary(summary)

    return cast(
        ConversationSummaryData,
        file_data,
    )


def load_conversation_summary(
    file_path: Path,
) -> ConversationSummaryData:
    """Load saved summary data or create an empty summary store."""

    default_data: ConversationSummaryData = {
        "schema_version": CONVERSATION_SUMMARY_SCHEMA_VERSION,
        "summary": None,
    }

    loaded_data = load_json_or_default(
        file_path,
        default_data,
    )

    return validate_conversation_summary_data(
        loaded_data
    )


def save_conversation_summary(
    file_path: Path,
    summary: object,
) -> None:
    """Validate and save one conversation summary."""

    validated_summary = validate_conversation_summary(
        summary
    )

    summary_data: ConversationSummaryData = {
        "schema_version": CONVERSATION_SUMMARY_SCHEMA_VERSION,
        "summary": validated_summary,
    }

    validate_conversation_summary_data(
        summary_data
    )

    write_json(
        file_path,
        summary_data,
    )
