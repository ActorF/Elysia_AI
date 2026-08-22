"""Data contracts and operations for scoped persistent memory."""

from datetime import datetime
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

from .json_store import load_json_or_default, write_json
from .scope import MemoryScope, MemoryScopeRef

LONG_TERM_MEMORY_SCHEMA_VERSION: Final[Literal[1]] = 1

LongTermMemorySource = Literal[
    "user_explicit",
    "model_inferred",
]


class LongTermMemoryRecord(TypedDict):
    """Describe one durable fact, its origin, and its exact scope."""

    key: str
    value: str
    source_type: LongTermMemorySource
    source_text: str
    created_at: str
    scope: MemoryScope
    scope_id: str | None


class LongTermMemoryData(TypedDict):
    """Describe the versioned top-level long-term-memory store."""

    schema_version: Literal[1]
    memories: list[LongTermMemoryRecord]


class LongTermMemorySearchResult(TypedDict):
    """Pair a one-based filtered-view number with one matching record."""

    number: int
    memory: LongTermMemoryRecord


_LEGACY_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "key",
        "value",
        "source_type",
        "source_text",
        "created_at",
    }
)
_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        *_LEGACY_RECORD_FIELDS,
        "scope",
        "scope_id",
    }
)
_DATA_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "memories",
    }
)


def validate_long_term_memory_record(
    data: object,
) -> LongTermMemoryRecord:
    """Validate one decoded record and its scope relationship."""

    if not isinstance(data, dict):
        raise ValueError(
            "Long-term memory record must be a JSON object."
        )

    if not all(isinstance(field, str) for field in data):
        raise ValueError(
            "Long-term memory record field names must be strings."
        )

    record_data = cast(dict[str, object], data)
    if set(record_data) != _RECORD_FIELDS:
        raise ValueError(
            "Long-term memory record does not match its schema."
        )

    for field_name in (
        "key",
        "value",
        "source_text",
        "created_at",
    ):
        field_value = record_data[field_name]
        if (
            not isinstance(field_value, str)
            or not field_value.strip()
        ):
            raise ValueError(
                f"{field_name} must be a non-empty string."
            )

    source_type = record_data["source_type"]
    if source_type not in ("user_explicit", "model_inferred"):
        raise ValueError(
            "source_type must be user_explicit or model_inferred."
        )

    scope = record_data["scope"]
    scope_id = record_data["scope_id"]
    if not isinstance(scope, str):
        raise ValueError("scope must be a string.")

    if scope_id is not None and not isinstance(scope_id, str):
        raise ValueError(
            "scope_id must be a string or None."
        )

    MemoryScopeRef(
        scope=cast(MemoryScope, scope),
        scope_id=cast(str | None, scope_id),
    )

    return cast(LongTermMemoryRecord, record_data)


def validate_long_term_memory_data(
    data: object,
) -> LongTermMemoryData:
    """Validate the complete versioned long-term-memory store."""

    if not isinstance(data, dict):
        raise ValueError(
            "Long-term memory data must be a JSON object."
        )

    if not all(isinstance(field, str) for field in data):
        raise ValueError(
            "Long-term memory field names must be strings."
        )

    memory_data = cast(dict[str, object], data)
    if set(memory_data) != _DATA_FIELDS:
        raise ValueError(
            "Long-term memory data does not match its schema."
        )

    schema_version = memory_data["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != LONG_TERM_MEMORY_SCHEMA_VERSION
    ):
        raise ValueError(
            "Unsupported long-term memory schema version."
        )

    memories = memory_data["memories"]
    if not isinstance(memories, list):
        raise ValueError("memories must be a list.")

    for record in cast(list[object], memories):
        validate_long_term_memory_record(record)

    return cast(LongTermMemoryData, memory_data)


def load_long_term_memory(
    file_path: Path,
) -> LongTermMemoryData:
    """Load scoped memory and migrate the known versionless global format."""

    default_data: LongTermMemoryData = {
        "schema_version": LONG_TERM_MEMORY_SCHEMA_VERSION,
        "memories": [],
    }
    loaded_data = load_json_or_default(
        file_path,
        default_data,
    )

    migrated_data = _migrate_legacy_data(loaded_data)
    validated_data = validate_long_term_memory_data(
        migrated_data
    )

    if migrated_data is not loaded_data:
        write_json(file_path, validated_data)

    return validated_data


def save_long_term_memory_record(
    file_path: Path,
    key: str,
    value: str,
    source_type: LongTermMemorySource,
    source_text: str,
    *,
    scope: MemoryScope = "global",
    scope_id: str | None = None,
) -> LongTermMemoryRecord:
    """Validate and append one record to an exact memory scope."""

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

    scope_ref = MemoryScopeRef(scope, scope_id)
    memory_data = load_long_term_memory(file_path)
    record: LongTermMemoryRecord = {
        "key": cleaned_key,
        "value": cleaned_value,
        "source_type": source_type,
        "source_text": cleaned_source_text,
        "created_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "scope": scope_ref.scope,
        "scope_id": scope_ref.scope_id,
    }

    memory_data["memories"].append(record)
    write_json(file_path, memory_data)
    return record


def filter_long_term_memory_records(
    records: list[LongTermMemoryRecord],
    *,
    scope: MemoryScope | None = None,
    scope_id: str | None = None,
) -> list[LongTermMemoryRecord]:
    """Return copies of records belonging to one optional exact scope."""

    return [
        record.copy()
        for _, record in _select_record_positions(
            records,
            scope=scope,
            scope_id=scope_id,
        )
    ]


def search_long_term_memory_records(
    file_path: Path,
    query: str,
    *,
    scope: MemoryScope | None = None,
    scope_id: str | None = None,
) -> list[LongTermMemorySearchResult]:
    """Find records within one exact scope using case-insensitive text."""

    cleaned_query = query.strip().casefold()
    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")

    memory_data = load_long_term_memory(file_path)
    selected_records = _select_record_positions(
        memory_data["memories"],
        scope=scope,
        scope_id=scope_id,
    )
    results: list[LongTermMemorySearchResult] = []

    for number, (_, record) in enumerate(
        selected_records,
        start=1,
    ):
        searchable_values = (
            record["key"],
            record["value"],
            record["source_type"],
            record["source_text"],
            record["created_at"],
            record["scope"],
            record["scope_id"] or "",
        )

        if any(
            cleaned_query in item.casefold()
            for item in searchable_values
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
    *,
    scope: MemoryScope | None = None,
    scope_id: str | None = None,
) -> LongTermMemoryRecord:
    """Edit one filtered record while preserving provenance and scope."""

    cleaned_key = key.strip()
    cleaned_value = value.strip()

    if not cleaned_key:
        raise ValueError("Memory key cannot be empty.")

    if not cleaned_value:
        raise ValueError("Memory value cannot be empty.")

    memory_data = load_long_term_memory(file_path)
    selected_records = _select_record_positions(
        memory_data["memories"],
        scope=scope,
        scope_id=scope_id,
    )
    selected_index = _resolve_memory_index(
        memory_number,
        len(selected_records),
    )
    storage_index, existing_record = selected_records[selected_index]
    updated_record: LongTermMemoryRecord = {
        "key": cleaned_key,
        "value": cleaned_value,
        "source_type": existing_record["source_type"],
        "source_text": existing_record["source_text"],
        "created_at": existing_record["created_at"],
        "scope": existing_record["scope"],
        "scope_id": existing_record["scope_id"],
    }

    memory_data["memories"][storage_index] = updated_record
    write_json(file_path, memory_data)
    return updated_record


def delete_long_term_memory_record(
    file_path: Path,
    memory_number: int,
    *,
    scope: MemoryScope | None = None,
    scope_id: str | None = None,
) -> LongTermMemoryRecord:
    """Delete and return one record selected within a filtered view."""

    memory_data = load_long_term_memory(file_path)
    selected_records = _select_record_positions(
        memory_data["memories"],
        scope=scope,
        scope_id=scope_id,
    )
    selected_index = _resolve_memory_index(
        memory_number,
        len(selected_records),
    )
    storage_index, _ = selected_records[selected_index]
    deleted_record = memory_data["memories"].pop(storage_index)
    write_json(file_path, memory_data)
    return deleted_record


def export_long_term_memory(
    file_path: Path,
    export_file: Path,
    *,
    overwrite: bool = False,
    scope: MemoryScope | None = None,
    scope_id: str | None = None,
) -> Path:
    """Export all memories or one exact scope as versioned JSON."""

    if file_path.resolve() == export_file.resolve():
        raise ValueError(
            "Export file must be different from the "
            "long-term memory store."
        )

    if export_file.exists() and not overwrite:
        raise FileExistsError("Export file already exists.")

    memory_data = load_long_term_memory(file_path)
    export_data: LongTermMemoryData = {
        "schema_version": LONG_TERM_MEMORY_SCHEMA_VERSION,
        "memories": filter_long_term_memory_records(
            memory_data["memories"],
            scope=scope,
            scope_id=scope_id,
        ),
    }
    write_json(export_file, export_data)
    return export_file


def _migrate_legacy_data(data: object) -> object:
    """Promote the known versionless Stage 4 store to Global scope."""

    if not isinstance(data, dict) or "schema_version" in data:
        return data

    if set(data) != {"memories"}:
        return data

    memories = data["memories"]
    if not isinstance(memories, list):
        return data

    migrated_records: list[LongTermMemoryRecord] = []
    for item in cast(list[object], memories):
        if (
            not isinstance(item, dict)
            or set(item) != _LEGACY_RECORD_FIELDS
        ):
            return data

        legacy_record = cast(dict[str, object], item)
        migrated_record = {
            **legacy_record,
            "scope": "global",
            "scope_id": None,
        }
        migrated_records.append(
            validate_long_term_memory_record(
                migrated_record
            )
        )

    migrated_data: LongTermMemoryData = {
        "schema_version": LONG_TERM_MEMORY_SCHEMA_VERSION,
        "memories": migrated_records,
    }
    return migrated_data


def _select_record_positions(
    records: list[LongTermMemoryRecord],
    *,
    scope: MemoryScope | None,
    scope_id: str | None,
) -> list[tuple[int, LongTermMemoryRecord]]:
    """Return storage positions visible through one optional scope filter."""

    if scope is None:
        if scope_id is not None:
            raise ValueError(
                "scope_id cannot be used without scope."
            )
        return list(enumerate(records))

    scope_ref = MemoryScopeRef(scope, scope_id)
    return [
        (index, record)
        for index, record in enumerate(records)
        if (
            record["scope"] == scope_ref.scope
            and record["scope_id"] == scope_ref.scope_id
        )
    ]


def _resolve_memory_index(
    memory_number: int,
    memory_count: int,
) -> int:
    """Validate a one-based filtered-view number and return its index."""

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
