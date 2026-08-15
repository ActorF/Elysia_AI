"""Data contracts and operations for persistent long-term memory."""

from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

from .json_store import load_json_or_default, write_json

LongTermMemorySource = Literal[
    "user_explicit",
    "model_inferred",
]


class LongTermMemoryRecord(TypedDict):
    """Describe one durable fact together with its origin and timestamp."""

    key: str
    value: str
    source_type: LongTermMemorySource
    source_text: str
    created_at: str


class LongTermMemoryData(TypedDict):
    """Describe the top-level JSON structure for long-term memory."""

    memories: list[LongTermMemoryRecord]


class LongTermMemorySearchResult(TypedDict):
    """Pair a one-based UI number with its matching memory record."""

    number: int
    memory: LongTermMemoryRecord


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
    """Validate and append one persistent long-term memory record.

    Raises:
        ValueError: If required text is empty or ``source_type`` is invalid.
    """

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


def search_long_term_memory_records(
    file_path: Path,
    query: str,
) -> list[LongTermMemorySearchResult]:
    """Find records using case-insensitive text matching."""
    cleaned_query = query.strip().casefold()

    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")

    memory_data = load_long_term_memory(file_path)
    results: list[LongTermMemorySearchResult] = []

    for number, record in enumerate(
        memory_data["memories"],
        start=1,
    ):
        searchable_values = (
            record["key"],
            record["value"],
            record["source_type"],
            record["source_text"],
            record["created_at"],
        )

        if any(
            cleaned_query in value.casefold()
            for value in searchable_values
        ):
            results.append(
                {
                    "number": number,
                    "memory": record.copy(),
                }
            )

    return results


def edit_long_term_memory_record(
    file_path: Path,
    memory_number: int,
    key: str,
    value: str,
) -> LongTermMemoryRecord:
    """Edit one record while preserving its source metadata."""
    cleaned_key = key.strip()
    cleaned_value = value.strip()

    if not cleaned_key:
        raise ValueError("Memory key cannot be empty.")

    if not cleaned_value:
        raise ValueError("Memory value cannot be empty.")

    memory_data = load_long_term_memory(file_path)
    memory_index = _resolve_memory_index(
        memory_number,
        len(memory_data["memories"]),
    )
    existing_record = memory_data["memories"][
        memory_index
    ]

    # Editing user-facing content must not rewrite provenance metadata.
    updated_record: LongTermMemoryRecord = {
        "key": cleaned_key,
        "value": cleaned_value,
        "source_type": existing_record["source_type"],
        "source_text": existing_record["source_text"],
        "created_at": existing_record["created_at"],
    }

    memory_data["memories"][memory_index] = (
        updated_record
    )
    write_json(file_path, memory_data)

    return updated_record


def delete_long_term_memory_record(
    file_path: Path,
    memory_number: int,
) -> LongTermMemoryRecord:
    """Delete and return one numbered long-term memory."""
    memory_data = load_long_term_memory(file_path)
    memory_index = _resolve_memory_index(
        memory_number,
        len(memory_data["memories"]),
    )
    deleted_record = memory_data["memories"].pop(
        memory_index
    )

    write_json(file_path, memory_data)

    return deleted_record


def export_long_term_memory(
    file_path: Path,
    export_file: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Export a portable JSON copy of long-term memories."""
    if file_path.resolve() == export_file.resolve():
        raise ValueError(
            "Export file must be different from the "
            "long-term memory store."
        )

    if export_file.exists() and not overwrite:
        raise FileExistsError(
            "Export file already exists."
        )

    memory_data = load_long_term_memory(file_path)
    # Copy records so the exported container shares no mutable dictionaries.
    export_data: LongTermMemoryData = {
        "memories": [
            record.copy()
            for record in memory_data["memories"]
        ],
    }

    write_json(export_file, export_data)

    return export_file


def _resolve_memory_index(
    memory_number: int,
    memory_count: int,
) -> int:
    """Validate a one-based UI number and convert it to a list index.

    Raises:
        ValueError: If the supplied number is not a positive integer.
        IndexError: If the number exceeds the current record count.
    """

    # ``bool`` must be rejected because it is an ``int`` subclass in Python.
    if (
        not isinstance(memory_number, int)
        or isinstance(memory_number, bool)
        or memory_number <= 0
    ):
        raise ValueError(
            "Memory number must be a positive integer."
        )

    memory_index = memory_number - 1

    if memory_index >= memory_count:
        raise IndexError(
            "Long-term memory number does not exist."
        )

    return memory_index
