"""Define conversation records and persist individual chat messages."""

from datetime import datetime
from pathlib import Path
from memory.json_store import load_json_or_default, write_json
from memory.file_manager import append_text
from typing import TypedDict


class ConversationMessage(TypedDict):
    """Represent one timestamped message in persistent conversation history."""

    timestamp: str
    speaker: str
    message: str


class ConversationData(TypedDict):
    """Describe the top-level JSON object that stores chat history."""

    messages: list[ConversationMessage]


def save_message(
    file_path: Path,
    speaker: str,
    message: str,
) -> None:
    """Append one human-readable conversation line to a text file.

    Raises:
        ValueError: If either the speaker or message contains no text.
    """

    if not speaker.strip():
        raise ValueError("Speaker cannot be empty")

    if not message.strip():
        raise ValueError("Message cannot be empty")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = f"[{timestamp}] {speaker}: {message}"

    append_text(file_path, record)

def save_json_message(
    file_path: Path,
    speaker: str,
    message: str,
) -> None:
    """Append one validated message to the structured JSON conversation log.

    The JSON store is created on first use. Existing messages retain their
    original order, and the new message receives the current local timestamp.

    Raises:
        ValueError: If either the speaker or message contains no text.
    """

    if not speaker.strip():
        raise ValueError("Speaker cannot be empty")

    if not message.strip():
        raise ValueError("Message cannot be empty")

    # Supplying a typed default lets first-run sessions create a valid store.
    default_data: ConversationData = {
        "messages": [],
    }

    conversation_data: ConversationData = load_json_or_default(
        file_path,
        default_data,
    )

    record: ConversationMessage = {
        "timestamp": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "speaker": speaker,
        "message": message,
    }

    conversation_data["messages"].append(record)
    write_json(file_path, conversation_data)
