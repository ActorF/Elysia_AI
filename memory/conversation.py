from datetime import datetime
from pathlib import Path

from memory.file_manager import append_text


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