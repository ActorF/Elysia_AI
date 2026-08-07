"""Data contracts for Elysia's persistent long-term memory."""

from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

from .json_store import load_json_or_default, write_json

LongTermMemorySource = Literal[
    "user_explicit",
    "model_inferred",
]


class LongTermMemoryRecord(TypedDict):
    key: str
    value: str
    source_type: LongTermMemorySource
    source_text: str
    created_at: str


class LongTermMemoryData(TypedDict):
    memories: list[LongTermMemoryRecord]


def load_long_term_memory(
    file_path: Path,
) -> LongTermMemoryData:
    """Load persistent long-term memory or create an empty store."""
    default_data: LongTermMemoryData = {
        "memories": [],
    }

    return load_json_or_default(
        file_path,
        default_data,
    )


def save_long_term_memory_record(
    file_path: Path,
    key: str,
    value: str,
    source_type: LongTermMemorySource,
    source_text: str,
) -> LongTermMemoryRecord:
    """Save one persistent long-term memory record."""
    cleaned_key = key.strip()
    cleaned_value = value.strip()
    cleaned_source_text = source_text.strip()

    if not cleaned_key:
        raise ValueError("Memory key cannot be empty.")

    if not cleaned_value:
        raise ValueError("Memory value cannot be empty.")

    if not cleaned_source_text:
        raise ValueError("Memory source text cannot be empty.")

    if source_type not in (
        "user_explicit",
        "model_inferred",
    ):
        raise ValueError(
            "source_type must be user_explicit "
            "or model_inferred."
        )

    memory_data = load_long_term_memory(file_path)

    record: LongTermMemoryRecord = {
        "key": cleaned_key,
        "value": cleaned_value,
        "source_type": source_type,
        "source_text": cleaned_source_text,
        "created_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    memory_data["memories"].append(record)
    write_json(file_path, memory_data)

    return record