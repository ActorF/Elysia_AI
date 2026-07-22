from datetime import datetime
from pathlib import Path
from memory.json_store import load_json_or_default, write_json
from memory.file_manager import append_text
from typing import TypedDict

class ConversationMessage(TypedDict):
    timestamp: str
    speaker: str
    message: str

class ConversationData(TypedDict):
    messages: list[ConversationMessage]


def save_message(
    file_path: Path,
    speaker: str,
    message: str,
) -> None:
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
    if not speaker.strip():
        raise ValueError("Speaker cannot be empty")

    if not message.strip():
        raise ValueError("Message cannot be empty")

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