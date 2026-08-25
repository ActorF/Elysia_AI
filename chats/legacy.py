"""Convert and compare the legacy conversation message format."""

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Final

from .domain import ChatMessage, ChatMessageRole, create_chat_message

_LEGACY_TIMESTAMP_FORMAT: Final = "%Y-%m-%d %H:%M:%S"


class LegacyConversationFormatError(ValueError):
    """Report invalid data in the pre-Chat conversation format."""


def legacy_conversation_messages_from_data(
    data: object,
) -> tuple[ChatMessage, ...]:
    """Validate and convert one legacy conversation JSON object."""

    if not isinstance(data, dict) or set(data) != {"messages"}:
        raise LegacyConversationFormatError(
            "Legacy conversation.json does not match its expected schema."
        )
    raw_messages = data["messages"]
    if not isinstance(raw_messages, list):
        raise LegacyConversationFormatError(
            "Legacy messages must be an array."
        )

    converted: list[ChatMessage] = []
    for position, raw_message in enumerate(raw_messages, start=1):
        if not isinstance(raw_message, dict) or set(raw_message) != {
            "timestamp",
            "speaker",
            "message",
        }:
            raise LegacyConversationFormatError(
                f"Legacy message {position} does not match its schema."
            )
        timestamp = raw_message["timestamp"]
        speaker = raw_message["speaker"]
        content = raw_message["message"]
        if (
            not all(
                isinstance(value, str)
                for value in (timestamp, speaker, content)
            )
            or not speaker.strip()
            or not content.strip()
        ):
            raise LegacyConversationFormatError(
                f"Legacy message {position} contains invalid text."
            )
        try:
            created_at = datetime.strptime(
                timestamp,
                _LEGACY_TIMESTAMP_FORMAT,
            ).replace(tzinfo=timezone.utc)
        except ValueError as error:
            raise LegacyConversationFormatError(
                f"Legacy message {position} has an invalid timestamp."
            ) from error
        role: ChatMessageRole = (
            "user"
            if speaker.strip().casefold() in {"user", "human", "ying"}
            else "assistant"
        )
        converted.append(
            create_chat_message(
                role=role,
                content=content,
                created_at=created_at,
            )
        )

    for previous, current in zip(converted, converted[1:]):
        if current.created_at < previous.created_at:
            raise LegacyConversationFormatError(
                "Legacy messages are not in chronological order."
            )
    return tuple(converted)


def chat_messages_match_legacy_prefix(
    messages: Sequence[ChatMessage],
    source_messages: Sequence[ChatMessage],
) -> bool:
    """Return whether a Chat preserves every semantic legacy message field."""

    return len(messages) >= len(source_messages) and all(
        migrated.role == source.role
        and migrated.content == source.content
        and migrated.created_at == source.created_at
        and migrated.attachments == source.attachments
        for migrated, source in zip(messages, source_messages)
    )
