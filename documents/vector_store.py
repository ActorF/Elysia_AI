"""Persist scope-isolated document embeddings in a bounded SQLite store.

The store is deliberately a persistence boundary rather than a complete
retriever.  It keeps validated chunk lineage and fixed-width float32 vectors,
and exposes one mechanical exact-scope cosine candidate scan.  Query policy,
deduplication, optional reranking, and final evidence publication remain in the
retrieval service.  Every write and cross-document scan uses one transaction so
a failure cannot expose a mixed embedding generation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
import struct
from threading import RLock
from time import monotonic
from typing import Any, Final, Literal, NoReturn, cast

from attachments.domain import (
    MAX_JSON_SAFE_INTEGER,
    AttachmentScope,
    AttachmentScopeKind,
    validate_attachment_id,
)

from .chunking import (
    CHUNKED_DOCUMENT_SCHEMA_VERSION,
    ChunkSourceMapping,
    ChunkedDocument,
    DocumentChunk,
    DocumentChunkingPolicy,
)
from .cleaning import (
    CLEANED_DOCUMENT_SCHEMA_VERSION,
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
    LoadedDocumentProvenance,
)
from .domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTitle,
)
from .embedding import (
    EMBEDDING_UNIT_NORM_TOLERANCE,
    EMBEDDED_DOCUMENT_SCHEMA_VERSION,
    EmbeddedQuery,
    EmbeddingBatchPolicy,
    EmbeddingError,
    EmbeddingModelIdentity,
    EmbeddingVector,
    EmbeddedChunk,
    EmbeddedDocument,
)
from .exceptions import (
    DocumentContentLimitError,
    DocumentCorruptError,
    DocumentError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)
from .retrieval import (
    ExpectedDocumentGeneration,
    RetrievalLimits,
    RetrievalMetadataFilter,
    RetrievalPolicy,
    StoredRetrievalCandidate,
)


VECTOR_STORE_SCHEMA_VERSION: Final[Literal[1]] = 1
VECTOR_ENCODING: Final = "float32-le-v1"
_SQLITE_APPLICATION_ID: Final = 0x454C5956  # ASCII-ish "ELYV" marker.
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_ID_PATTERN: Final = re.compile(r"^chunk_[0-9a-f]{64}$")
_TABLE_NAMES: Final = frozenset({
    "vector_store_metadata",
    "vector_documents",
    "vector_records",
})
_METADATA_COLUMNS: Final = (
    "singleton",
    "schema_version",
    "vector_encoding",
    "embedding_space_id",
    "dimensions",
    "identity_json",
    "identity_checksum",
)
_DOCUMENT_COLUMNS: Final = (
    "scope_kind",
    "scope_id",
    "link_id",
    "derivation_fingerprint",
    "embedding_space_id",
    "lineage_json",
    "lineage_checksum",
    "record_count",
)
_RECORD_COLUMNS: Final = (
    "scope_kind",
    "scope_id",
    "link_id",
    "ordinal",
    "chunk_id",
    "embedding_id",
    "chunk_json",
    "chunk_checksum",
    "vector_blob",
    "vector_checksum",
)
_METADATA_SCHEMA_SQL: Final = """
CREATE TABLE vector_store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    vector_encoding TEXT NOT NULL,
    embedding_space_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    identity_json TEXT NOT NULL,
    identity_checksum TEXT NOT NULL
)
""".strip()
_DOCUMENT_SCHEMA_SQL: Final = """
CREATE TABLE vector_documents (
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('chat','project')),
    scope_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    derivation_fingerprint TEXT NOT NULL,
    embedding_space_id TEXT NOT NULL,
    lineage_json TEXT NOT NULL,
    lineage_checksum TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    PRIMARY KEY (scope_kind, scope_id, link_id)
) WITHOUT ROWID
""".strip()
_RECORD_SCHEMA_SQL: Final = """
CREATE TABLE vector_records (
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    chunk_id TEXT NOT NULL,
    embedding_id TEXT NOT NULL,
    chunk_json TEXT NOT NULL,
    chunk_checksum TEXT NOT NULL,
    vector_blob BLOB NOT NULL,
    vector_checksum TEXT NOT NULL,
    PRIMARY KEY (scope_kind, scope_id, link_id, ordinal),
    UNIQUE (scope_kind, scope_id, chunk_id),
    UNIQUE (scope_kind, scope_id, embedding_id),
    FOREIGN KEY (scope_kind, scope_id, link_id)
        REFERENCES vector_documents(scope_kind, scope_id, link_id)
        ON DELETE CASCADE
) WITHOUT ROWID
""".strip()
_SCHEMA_STATEMENTS: Final = (
    _METADATA_SCHEMA_SQL,
    _DOCUMENT_SCHEMA_SQL,
    _RECORD_SCHEMA_SQL,
)
_EXPECTED_TABLE_SQL: Final = {
    "vector_store_metadata": _METADATA_SCHEMA_SQL,
    "vector_documents": _DOCUMENT_SCHEMA_SQL,
    "vector_records": _RECORD_SCHEMA_SQL,
}
_EXPECTED_COLUMN_SHAPES: Final = {
    "vector_store_metadata": (
        (0, "singleton", "INTEGER", 0, None, 1),
        (1, "schema_version", "INTEGER", 1, None, 0),
        (2, "vector_encoding", "TEXT", 1, None, 0),
        (3, "embedding_space_id", "TEXT", 1, None, 0),
        (4, "dimensions", "INTEGER", 1, None, 0),
        (5, "identity_json", "TEXT", 1, None, 0),
        (6, "identity_checksum", "TEXT", 1, None, 0),
    ),
    "vector_documents": (
        (0, "scope_kind", "TEXT", 1, None, 1),
        (1, "scope_id", "TEXT", 1, None, 2),
        (2, "link_id", "TEXT", 1, None, 3),
        (3, "derivation_fingerprint", "TEXT", 1, None, 0),
        (4, "embedding_space_id", "TEXT", 1, None, 0),
        (5, "lineage_json", "TEXT", 1, None, 0),
        (6, "lineage_checksum", "TEXT", 1, None, 0),
        (7, "record_count", "INTEGER", 1, None, 0),
    ),
    "vector_records": (
        (0, "scope_kind", "TEXT", 1, None, 1),
        (1, "scope_id", "TEXT", 1, None, 2),
        (2, "link_id", "TEXT", 1, None, 3),
        (3, "ordinal", "INTEGER", 1, None, 4),
        (4, "chunk_id", "TEXT", 1, None, 0),
        (5, "embedding_id", "TEXT", 1, None, 0),
        (6, "chunk_json", "TEXT", 1, None, 0),
        (7, "chunk_checksum", "TEXT", 1, None, 0),
        (8, "vector_blob", "BLOB", 1, None, 0),
        (9, "vector_checksum", "TEXT", 1, None, 0),
    ),
}
_EXPECTED_INDEX_SHAPES: Final = {
    "vector_store_metadata": frozenset(),
    "vector_documents": frozenset({
        (1, "pk", 0, ("scope_kind", "scope_id", "link_id")),
    }),
    "vector_records": frozenset({
        (
            1,
            "pk",
            0,
            ("scope_kind", "scope_id", "link_id", "ordinal"),
        ),
        (1, "u", 0, ("scope_kind", "scope_id", "chunk_id")),
        (1, "u", 0, ("scope_kind", "scope_id", "embedding_id")),
    }),
}
_EXPECTED_RECORD_FOREIGN_KEYS: Final = (
    (
        0,
        0,
        "vector_documents",
        "scope_kind",
        "scope_kind",
        "NO ACTION",
        "CASCADE",
        "NONE",
    ),
    (
        0,
        1,
        "vector_documents",
        "scope_id",
        "scope_id",
        "NO ACTION",
        "CASCADE",
        "NONE",
    ),
    (
        0,
        2,
        "vector_documents",
        "link_id",
        "link_id",
        "NO ACTION",
        "CASCADE",
        "NONE",
    ),
)
_EXPECTED_CATALOG_OBJECT_COUNT: Final = len(_TABLE_NAMES) + sum(
    1
    for expected_indexes in _EXPECTED_INDEX_SHAPES.values()
    for _, origin, _, _ in expected_indexes
    if origin == "u"
)
_MAX_EXPECTED_INDEX_COLUMNS: Final = max(
    len(columns)
    for expected_indexes in _EXPECTED_INDEX_SHAPES.values()
    for _, _, _, columns in expected_indexes
)
_DATABASE_SIDECAR_SUFFIXES: Final = ("", "-journal", "-wal", "-shm")
_SQLITE_PROGRESS_INSTRUCTION_INTERVAL: Final = 1_000
_MAX_STORED_JSON_NESTING: Final = 128


class VectorStoreStaleError(DocumentError):
    """Report that stored vectors do not match the requested derivation."""


class VectorStoreIncompleteError(DocumentError):
    """Report that an explicitly required document generation is absent."""


class VectorStoreModelMismatchError(DocumentError):
    """Report that a database belongs to another embedding space."""


class VectorStorePersistenceError(DocumentError):
    """Report a path-private SQLite open, transaction, or write failure."""


def _read_exact_slots(
    value: object,
    expected_type: type[object],
    field_names: tuple[str, ...],
    error_message: str,
) -> tuple[Any, ...]:
    """Read bounded slots and contain deleted frozen-dataclass fields."""

    if type(value) is not expected_type:
        raise DocumentValidationError(error_message)
    try:
        return tuple(getattr(value, field_name) for field_name in field_names)
    except AttributeError:
        # Frozen dataclasses can still be corrupted through object.__delattr__;
        # public persistence boundaries must keep that inside typed failures.
        raise DocumentValidationError(error_message) from None


@dataclass(frozen=True, slots=True)
class VectorStoreLimits:
    """Bound persistent graphs plus every public list and search operation."""

    max_records_per_document: int = 10_000
    max_list_records: int = 1_000
    max_rebuild_documents: int = 1_000
    max_rebuild_records: int = 100_000
    max_search_documents: int = 128
    max_search_records: int = 10_000
    max_search_results: int = 64
    max_search_payload_bytes: int = 128 * 1024 * 1024
    max_lineage_json_bytes: int = 256 * 1024
    max_chunk_json_bytes: int = 2 * 1024 * 1024
    max_document_metadata_bytes: int = 256 * 1024 * 1024
    max_document_vector_bytes: int = 64 * 1024 * 1024
    max_database_bytes: int = 2 * 1024 * 1024 * 1024
    max_database_pages: int = 524_288

    def __post_init__(self) -> None:
        """Reject ambiguous or incoherent resource ceilings."""

        for field in fields(self):
            value = getattr(self, field.name)
            if (
                type(value) is not int
                or value <= 0
                or value > MAX_JSON_SAFE_INTEGER
            ):
                raise DocumentValidationError(
                    f"{field.name} must be a positive JSON-safe integer."
                )
        if self.max_list_records > self.max_records_per_document:
            raise DocumentValidationError(
                "max_list_records cannot exceed max_records_per_document."
            )
        if self.max_rebuild_documents > self.max_rebuild_records:
            raise DocumentValidationError(
                "max_rebuild_documents cannot exceed max_rebuild_records."
            )
        if self.max_search_results > self.max_search_records:
            raise DocumentValidationError(
                "max_search_results cannot exceed max_search_records."
            )
        if self.max_database_bytes < 512:
            raise DocumentValidationError(
                "max_database_bytes cannot be smaller than one SQLite page."
            )
        if self.max_database_pages > self.max_database_bytes // 512:
            raise DocumentValidationError(
                "max_database_pages cannot exceed the database byte budget."
            )


def _snapshot_limits(value: object) -> VectorStoreLimits:
    """Rebuild persistence limits so callers cannot retain mutable aliases."""

    if type(value) is not VectorStoreLimits:
        raise TypeError("limits must be VectorStoreLimits.")
    try:
        return VectorStoreLimits(
            max_records_per_document=value.max_records_per_document,
            max_list_records=value.max_list_records,
            max_rebuild_documents=value.max_rebuild_documents,
            max_rebuild_records=value.max_rebuild_records,
            max_search_documents=value.max_search_documents,
            max_search_records=value.max_search_records,
            max_search_results=value.max_search_results,
            max_search_payload_bytes=value.max_search_payload_bytes,
            max_lineage_json_bytes=value.max_lineage_json_bytes,
            max_chunk_json_bytes=value.max_chunk_json_bytes,
            max_document_metadata_bytes=value.max_document_metadata_bytes,
            max_document_vector_bytes=value.max_document_vector_bytes,
            max_database_bytes=value.max_database_bytes,
            max_database_pages=value.max_database_pages,
        )
    except (AttributeError, DocumentError, TypeError, ValueError) as error:
        raise DocumentValidationError("Vector-store limits are invalid.") from error


def _require_scope(value: object) -> AttachmentScope:
    """Return one exact validated Chat or Project scope."""

    kind, scope_id = _read_exact_slots(
        value,
        AttachmentScope,
        ("kind", "id"),
        "scope must be AttachmentScope.",
    )
    if type(kind) is not str or type(scope_id) is not str:
        raise DocumentValidationError("scope must be AttachmentScope.")
    # Rebuilding the immutable value rejects object-graph tampering performed
    # through ``object.__setattr__`` by an injected caller.
    try:
        return AttachmentScope(
            kind=cast(AttachmentScopeKind, kind),
            id=scope_id,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise DocumentValidationError("scope is invalid.") from error


def _require_digest(value: object, field_name: str) -> str:
    """Return one canonical lowercase SHA-256 digest."""

    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise DocumentValidationError(
            f"{field_name} must be a lowercase SHA-256 digest."
        )
    return value


def _require_positive_integer(value: object, field_name: str) -> int:
    """Return one exact positive JSON-safe integer."""

    if (
        type(value) is not int
        or value <= 0
        or value > MAX_JSON_SAFE_INTEGER
    ):
        raise DocumentValidationError(
            f"{field_name} must be a positive JSON-safe integer."
        )
    return value


def _require_non_negative_integer(value: object, field_name: str) -> int:
    """Return one exact non-negative JSON-safe integer."""

    if (
        type(value) is not int
        or value < 0
        or value > MAX_JSON_SAFE_INTEGER
    ):
        raise DocumentValidationError(
            f"{field_name} must be a non-negative JSON-safe integer."
        )
    return value


def _canonical_json_bytes(value: object) -> bytes:
    """Serialize one explicitly shaped value as deterministic UTF-8 JSON."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate member names."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _require_bounded_json_nesting(value: str, field_name: str) -> None:
    """Reject excessive container depth before the platform JSON decoder.

    CPython's JSON implementations have differed in whether extremely deep
    arrays fail with ``RecursionError`` or decode successfully.  A small
    string-aware pre-scan gives persisted metadata one portable allocation
    boundary while leaving complete syntax validation to ``json.loads``.
    """

    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_STORED_JSON_NESTING:
                raise DocumentCorruptError(
                    f"Stored {field_name} metadata is invalid."
                )
        elif character in "]}" and depth > 0:
            depth -= 1


def _decode_json_object(
    value: object,
    *,
    maximum_bytes: int,
    field_name: str,
) -> dict[str, object]:
    """Decode one bounded strict JSON object without echoing stored data."""

    if not isinstance(value, str):
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        )
    try:
        encoded = value.encode("utf-8")
    except (UnicodeError, MemoryError) as error:
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        ) from error
    if len(encoded) > maximum_bytes:
        raise DocumentCorruptError(
            f"Stored {field_name} metadata exceeds its limit."
        )
    _require_bounded_json_nesting(value, field_name)
    try:
        decoded = json.loads(value, object_pairs_hook=_strict_object)
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
        MemoryError,
    ) as error:
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        ) from error
    if not isinstance(decoded, dict):
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        )
    return cast(dict[str, object], decoded)


def _require_exact_fields(
    value: Mapping[str, object],
    expected: set[str],
    field_name: str,
) -> None:
    """Reject missing or unknown persisted fields for fail-closed decoding."""

    if set(value) != expected:
        raise DocumentCorruptError(
            f"Stored {field_name} metadata has an unsupported shape."
        )


def _dataclass_integer_data(value: object) -> dict[str, int]:
    """Serialize one validated integer-only policy dataclass."""

    return {
        field.name: cast(int, getattr(value, field.name))
        for field in fields(value)  # type: ignore[arg-type]
    }


def _scope_to_data(scope: AttachmentScope) -> dict[str, str]:
    """Serialize one validated scope without any filesystem information."""

    return {"kind": scope.kind, "id": scope.id}


def _source_to_data(source: DocumentSource) -> dict[str, object]:
    """Serialize one path-free document source identity."""

    return {
        "scope": _scope_to_data(source.scope),
        "link_id": source.link_id,
        "file_id": source.file_id,
        "file_name": source.file_name,
        "media_type": source.media_type,
        "size_bytes": source.size_bytes,
    }


def _lineage_to_data(document: ChunkedDocument) -> dict[str, object]:
    """Serialize complete chunk derivation metadata without chunk payloads."""

    provenance = document.provenance
    return {
        "schema_version": document.schema_version,
        "cleaned_schema_version": document.cleaned_schema_version,
        "provenance": {
            "loaded_schema_version": provenance.loaded_schema_version,
            "source": _source_to_data(provenance.source),
            "document_format": provenance.document_format,
            "loader_id": provenance.loader_id,
            "loader_version": provenance.loader_version,
            "load_limits": _dataclass_integer_data(provenance.load_limits),
        },
        "cleaner_id": document.cleaner_id,
        "cleaner_version": document.cleaner_version,
        "document_fingerprint": document.document_fingerprint,
        "cleaning_fingerprint": document.cleaning_fingerprint,
        "chunker_id": document.chunker_id,
        "chunker_version": document.chunker_version,
        "chunking_policy": {
            "max_chunk_code_points": document.policy.max_chunk_code_points,
            "overlap_code_points": document.policy.overlap_code_points,
            "prose_separator": document.policy.prose_separator,
            "table_projection_version": (
                document.policy.table_projection_version
            ),
        },
        "processing_limits": _dataclass_integer_data(document.limits),
        "title": (
            None
            if document.title is None
            else {
                "text": document.title.text,
                "source": document.title.source,
            }
        ),
        "page_count": document.page_count,
        "derivation_fingerprint": document.derivation_fingerprint,
    }


def _identity_to_data(identity: EmbeddingModelIdentity) -> dict[str, object]:
    """Serialize every field that defines one immutable embedding space."""

    return {
        "provider": identity.provider,
        "adapter_id": identity.adapter_id,
        "adapter_version": identity.adapter_version,
        "model_tag": identity.model_tag,
        "model_digest": identity.model_digest,
        "dimension": identity.dimension,
        "normalization": identity.normalization,
        "document_template_version": identity.document_template_version,
        "query_template_version": identity.query_template_version,
        "embedding_space_id": identity.embedding_space_id,
    }


def _policy_to_data(policy: EmbeddingBatchPolicy) -> dict[str, object]:
    """Serialize the fixed embedding batch and truncation policy."""

    return {
        "max_batch_items": policy.max_batch_items,
        "max_input_code_points": policy.max_input_code_points,
        "max_batch_code_points": policy.max_batch_code_points,
        "truncate": policy.truncate,
    }


def _embedded_lineage_to_data(document: EmbeddedDocument) -> dict[str, object]:
    """Serialize embedding and complete chunk-derivation lineage together."""

    chunks = document.chunked_document.chunks
    return {
        "embedded_schema_version": document.schema_version,
        "chunked_schema_version": document.chunked_schema_version,
        "embedding_policy": _policy_to_data(document.policy),
        "chunked_document": _lineage_to_data(document.chunked_document),
        # These aggregates let paged readers prove document-wide resource
        # budgets without materializing every separately stored chunk.
        "record_count": len(chunks),
        "total_chunk_code_points": sum(len(chunk.text) for chunk in chunks),
        "total_source_mappings": sum(
            len(chunk.source_mappings) for chunk in chunks
        ),
    }


def _span_to_data(
    span: DocumentTextSpan | DocumentTableCellSpan,
) -> dict[str, object]:
    """Serialize one exact LoadedDocument source span."""

    if isinstance(span, DocumentTextSpan):
        return {
            "kind": "block_text",
            "block_ordinal": span.block_ordinal,
            "start_code_point": span.start_code_point,
            "end_code_point": span.end_code_point,
            "page_number": span.page_number,
        }
    return {
        "kind": "table_cell",
        "block_ordinal": span.block_ordinal,
        "row_index": span.row_index,
        "column_index": span.column_index,
        "start_code_point": span.start_code_point,
        "end_code_point": span.end_code_point,
        "page_number": span.page_number,
    }


def _chunk_to_data(chunk: DocumentChunk) -> dict[str, object]:
    """Serialize citation text and every chunk-local source mapping."""

    return {
        "ordinal": chunk.ordinal,
        "chunk_id": chunk.chunk_id,
        "kind": chunk.kind,
        "text": chunk.text,
        "page_number": chunk.page_number,
        "source_mappings": [
            {
                "chunk_start_code_point": mapping.chunk_start_code_point,
                "chunk_end_code_point": mapping.chunk_end_code_point,
                "source_span": _span_to_data(mapping.source_span),
            }
            for mapping in chunk.source_mappings
        ],
    }


def _expected_chunk_id(
    chunk: DocumentChunk,
    derivation_fingerprint: str,
) -> str:
    """Recompute the v1 chunk identity from stored text and provenance."""

    digest = hashlib.sha256(_canonical_json_bytes({
        "chunk_id_domain": "elysia.document-chunk.v1",
        "derivation_fingerprint": derivation_fingerprint,
        "ordinal": chunk.ordinal,
        "kind": chunk.kind,
        "text": chunk.text,
        "page_number": chunk.page_number,
        "source_mappings": [
            {
                "chunk_start_code_point": mapping.chunk_start_code_point,
                "chunk_end_code_point": mapping.chunk_end_code_point,
                "source_span": _span_to_data(mapping.source_span),
            }
            for mapping in chunk.source_mappings
        ],
    })).hexdigest()
    return f"chunk_{digest}"


def _require_stored_string(value: object, field_name: str) -> str:
    """Return a stored string or report generic metadata corruption."""

    if not isinstance(value, str):
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        )
    return value


def _require_stored_integer(value: object, field_name: str) -> int:
    """Return a stored exact JSON-safe integer or report corruption."""

    if (
        type(value) is not int
        or value < 0
        or value > MAX_JSON_SAFE_INTEGER
    ):
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        )
    return value


def _parse_scope(value: object) -> AttachmentScope:
    """Rebuild and validate one persisted scope object."""

    if not isinstance(value, dict):
        raise DocumentCorruptError("Stored scope metadata is invalid.")
    data = cast(dict[str, object], value)
    _require_exact_fields(data, {"kind", "id"}, "scope")
    try:
        return AttachmentScope(
            kind=cast(Any, data["kind"]),
            id=_require_stored_string(data["id"], "scope"),
        )
    except (TypeError, ValueError) as error:
        raise DocumentCorruptError("Stored scope metadata is invalid.") from error


def _parse_source(value: object) -> DocumentSource:
    """Rebuild one persisted path-free document source."""

    if not isinstance(value, dict):
        raise DocumentCorruptError("Stored source metadata is invalid.")
    data = cast(dict[str, object], value)
    _require_exact_fields(
        data,
        {
            "scope",
            "link_id",
            "file_id",
            "file_name",
            "media_type",
            "size_bytes",
        },
        "source",
    )
    try:
        return DocumentSource(
            scope=_parse_scope(data["scope"]),
            link_id=_require_stored_string(data["link_id"], "source"),
            file_id=_require_stored_string(data["file_id"], "source"),
            file_name=_require_stored_string(data["file_name"], "source"),
            media_type=_require_stored_string(data["media_type"], "source"),
            size_bytes=_require_stored_integer(data["size_bytes"], "source"),
        )
    except DocumentError:
        raise
    except (TypeError, ValueError) as error:
        raise DocumentCorruptError("Stored source metadata is invalid.") from error


def _parse_integer_dataclass(
    value: object,
    value_type: type[Any],
    field_name: str,
) -> Any:
    """Rebuild one strict integer-only policy or limit dataclass."""

    if not isinstance(value, dict):
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        )
    data = cast(dict[str, object], value)
    expected = {field.name for field in fields(value_type)}
    _require_exact_fields(data, expected, field_name)
    parsed = {
        name: _require_stored_integer(data[name], field_name)
        for name in expected
    }
    try:
        return value_type(**parsed)
    except (DocumentError, TypeError, ValueError) as error:
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        ) from error


def _parse_optional_page_number(value: object, field_name: str) -> int | None:
    """Rebuild one optional one-based persisted page number."""

    if value is None:
        return None
    parsed = _require_stored_integer(value, field_name)
    if parsed == 0:
        raise DocumentCorruptError(
            f"Stored {field_name} metadata is invalid."
        )
    return parsed


def _parse_lineage_header(value: dict[str, object]) -> dict[str, object]:
    """Parse complete lineage values except for the separately stored chunks."""

    _require_exact_fields(
        value,
        {
            "schema_version",
            "cleaned_schema_version",
            "provenance",
            "cleaner_id",
            "cleaner_version",
            "document_fingerprint",
            "cleaning_fingerprint",
            "chunker_id",
            "chunker_version",
            "chunking_policy",
            "processing_limits",
            "title",
            "page_count",
            "derivation_fingerprint",
        },
        "lineage",
    )
    if value["schema_version"] != CHUNKED_DOCUMENT_SCHEMA_VERSION:
        raise DocumentCorruptError(
            "Stored chunked-document schema version is unsupported."
        )
    if value["cleaned_schema_version"] != CLEANED_DOCUMENT_SCHEMA_VERSION:
        raise DocumentCorruptError(
            "Stored cleaned-document schema version is unsupported."
        )
    provenance_value = value["provenance"]
    if not isinstance(provenance_value, dict):
        raise DocumentCorruptError("Stored provenance metadata is invalid.")
    provenance_data = cast(dict[str, object], provenance_value)
    _require_exact_fields(
        provenance_data,
        {
            "loaded_schema_version",
            "source",
            "document_format",
            "loader_id",
            "loader_version",
            "load_limits",
        },
        "provenance",
    )
    if provenance_data["loaded_schema_version"] != DOCUMENT_SCHEMA_VERSION:
        raise DocumentCorruptError(
            "Stored loaded-document schema version is unsupported."
        )
    source = _parse_source(provenance_data["source"])
    load_limits = cast(
        DocumentLoadLimits,
        _parse_integer_dataclass(
            provenance_data["load_limits"],
            DocumentLoadLimits,
            "load limits",
        ),
    )
    try:
        provenance = LoadedDocumentProvenance(
            loaded_schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format=cast(
                Any,
                _require_stored_string(
                    provenance_data["document_format"],
                    "provenance",
                ),
            ),
            loader_id=_require_stored_string(
                provenance_data["loader_id"],
                "provenance",
            ),
            loader_version=_require_stored_string(
                provenance_data["loader_version"],
                "provenance",
            ),
            load_limits=load_limits,
        )
    except (DocumentError, TypeError, ValueError) as error:
        raise DocumentCorruptError(
            "Stored provenance metadata is invalid."
        ) from error

    policy_value = value["chunking_policy"]
    if not isinstance(policy_value, dict):
        raise DocumentCorruptError(
            "Stored chunking-policy metadata is invalid."
        )
    policy_data = cast(dict[str, object], policy_value)
    _require_exact_fields(
        policy_data,
        {
            "max_chunk_code_points",
            "overlap_code_points",
            "prose_separator",
            "table_projection_version",
        },
        "chunking policy",
    )
    try:
        policy = DocumentChunkingPolicy(
            max_chunk_code_points=_require_stored_integer(
                policy_data["max_chunk_code_points"],
                "chunking policy",
            ),
            overlap_code_points=cast(
                Any,
                _require_stored_integer(
                    policy_data["overlap_code_points"],
                    "chunking policy",
                ),
            ),
            prose_separator=cast(
                Any,
                _require_stored_string(
                    policy_data["prose_separator"],
                    "chunking policy",
                ),
            ),
            table_projection_version=cast(
                Any,
                _require_stored_string(
                    policy_data["table_projection_version"],
                    "chunking policy",
                ),
            ),
        )
    except (DocumentError, TypeError, ValueError) as error:
        raise DocumentCorruptError(
            "Stored chunking-policy metadata is invalid."
        ) from error
    processing_limits = cast(
        DocumentProcessingLimits,
        _parse_integer_dataclass(
            value["processing_limits"],
            DocumentProcessingLimits,
            "processing limits",
        ),
    )
    title_value = value["title"]
    title: DocumentTitle | None
    if title_value is None:
        title = None
    elif isinstance(title_value, dict):
        title_data = cast(dict[str, object], title_value)
        _require_exact_fields(title_data, {"text", "source"}, "title")
        try:
            title = DocumentTitle(
                text=_require_stored_string(title_data["text"], "title"),
                source=cast(
                    Any,
                    _require_stored_string(title_data["source"], "title"),
                ),
            )
        except (DocumentError, TypeError, ValueError) as error:
            raise DocumentCorruptError(
                "Stored title metadata is invalid."
            ) from error
    else:
        raise DocumentCorruptError("Stored title metadata is invalid.")

    return {
        "schema_version": CHUNKED_DOCUMENT_SCHEMA_VERSION,
        "cleaned_schema_version": CLEANED_DOCUMENT_SCHEMA_VERSION,
        "provenance": provenance,
        "cleaner_id": _require_stored_string(
            value["cleaner_id"], "lineage"
        ),
        "cleaner_version": _require_stored_string(
            value["cleaner_version"], "lineage"
        ),
        "document_fingerprint": _require_stored_string(
            value["document_fingerprint"], "lineage"
        ),
        "cleaning_fingerprint": _require_stored_string(
            value["cleaning_fingerprint"], "lineage"
        ),
        "chunker_id": _require_stored_string(
            value["chunker_id"], "lineage"
        ),
        "chunker_version": _require_stored_string(
            value["chunker_version"], "lineage"
        ),
        "policy": policy,
        "limits": processing_limits,
        "title": title,
        "page_count": _parse_optional_page_number(
            value["page_count"], "page count"
        ),
        "derivation_fingerprint": _require_stored_string(
            value["derivation_fingerprint"], "lineage"
        ),
    }


def _parse_identity(value: dict[str, object]) -> EmbeddingModelIdentity:
    """Rebuild one complete persisted embedding-space identity."""

    _require_exact_fields(
        value,
        {
            "provider",
            "adapter_id",
            "adapter_version",
            "model_tag",
            "model_digest",
            "dimension",
            "normalization",
            "document_template_version",
            "query_template_version",
            "embedding_space_id",
        },
        "embedding identity",
    )
    try:
        return EmbeddingModelIdentity(
            provider=_require_stored_string(
                value["provider"], "embedding identity"
            ),
            adapter_id=_require_stored_string(
                value["adapter_id"], "embedding identity"
            ),
            adapter_version=_require_stored_string(
                value["adapter_version"], "embedding identity"
            ),
            model_tag=_require_stored_string(
                value["model_tag"], "embedding identity"
            ),
            model_digest=_require_stored_string(
                value["model_digest"], "embedding identity"
            ),
            dimension=_require_stored_integer(
                value["dimension"], "embedding identity"
            ),
            normalization=cast(
                Any,
                _require_stored_string(
                    value["normalization"], "embedding identity"
                ),
            ),
            document_template_version=_require_stored_string(
                value["document_template_version"], "embedding identity"
            ),
            query_template_version=_require_stored_string(
                value["query_template_version"], "embedding identity"
            ),
            embedding_space_id=_require_stored_string(
                value["embedding_space_id"], "embedding identity"
            ),
        )
    except (EmbeddingError, TypeError, ValueError) as error:
        raise DocumentCorruptError(
            "Stored embedding identity is invalid."
        ) from error


def _parse_policy(value: object) -> EmbeddingBatchPolicy:
    """Rebuild and validate the fixed persisted embedding batch policy."""

    if not isinstance(value, dict):
        raise DocumentCorruptError(
            "Stored embedding policy metadata is invalid."
        )
    data = cast(dict[str, object], value)
    _require_exact_fields(
        data,
        {
            "max_batch_items",
            "max_input_code_points",
            "max_batch_code_points",
            "truncate",
        },
        "embedding policy",
    )
    try:
        return EmbeddingBatchPolicy(
            max_batch_items=cast(
                Any,
                _require_stored_integer(
                    data["max_batch_items"], "embedding policy"
                ),
            ),
            max_input_code_points=cast(
                Any,
                _require_stored_integer(
                    data["max_input_code_points"], "embedding policy"
                ),
            ),
            max_batch_code_points=cast(
                Any,
                _require_stored_integer(
                    data["max_batch_code_points"], "embedding policy"
                ),
            ),
            truncate=cast(Any, data["truncate"]),
        )
    except (EmbeddingError, TypeError, ValueError) as error:
        raise DocumentCorruptError(
            "Stored embedding policy metadata is invalid."
        ) from error


def _parse_embedded_lineage(
    value: dict[str, object],
) -> tuple[
    dict[str, object],
    EmbeddingBatchPolicy,
    int,
    int,
    int,
]:
    """Parse embedded-document versions, policy, and chunk lineage header."""

    _require_exact_fields(
        value,
        {
            "embedded_schema_version",
            "chunked_schema_version",
            "embedding_policy",
            "chunked_document",
            "record_count",
            "total_chunk_code_points",
            "total_source_mappings",
        },
        "embedded lineage",
    )
    if value["embedded_schema_version"] != EMBEDDED_DOCUMENT_SCHEMA_VERSION:
        raise DocumentCorruptError(
            "Stored embedded-document schema version is unsupported."
        )
    if value["chunked_schema_version"] != CHUNKED_DOCUMENT_SCHEMA_VERSION:
        raise DocumentCorruptError(
            "Stored chunked-document schema version is unsupported."
        )
    raw_chunked = value["chunked_document"]
    if not isinstance(raw_chunked, dict):
        raise DocumentCorruptError("Stored chunk lineage is invalid.")
    return (
        _parse_lineage_header(cast(dict[str, object], raw_chunked)),
        _parse_policy(value["embedding_policy"]),
        _require_stored_integer(value["record_count"], "embedded lineage"),
        _require_stored_integer(
            value["total_chunk_code_points"],
            "embedded lineage",
        ),
        _require_stored_integer(
            value["total_source_mappings"],
            "embedded lineage",
        ),
    )


def _parse_span(value: object) -> DocumentTextSpan | DocumentTableCellSpan:
    """Rebuild one strict persisted text-block or table-cell source span."""

    if not isinstance(value, dict):
        raise DocumentCorruptError("Stored source-span metadata is invalid.")
    data = cast(dict[str, object], value)
    kind = data.get("kind")
    common = {
        "kind",
        "block_ordinal",
        "start_code_point",
        "end_code_point",
        "page_number",
    }
    try:
        if kind == "block_text":
            _require_exact_fields(data, common, "source span")
            return DocumentTextSpan(
                block_ordinal=_require_stored_integer(
                    data["block_ordinal"], "source span"
                ),
                start_code_point=_require_stored_integer(
                    data["start_code_point"], "source span"
                ),
                end_code_point=_require_stored_integer(
                    data["end_code_point"], "source span"
                ),
                page_number=_parse_optional_page_number(
                    data["page_number"], "source span"
                ),
            )
        if kind == "table_cell":
            _require_exact_fields(
                data,
                common | {"row_index", "column_index"},
                "source span",
            )
            return DocumentTableCellSpan(
                block_ordinal=_require_stored_integer(
                    data["block_ordinal"], "source span"
                ),
                row_index=_require_stored_integer(
                    data["row_index"], "source span"
                ),
                column_index=_require_stored_integer(
                    data["column_index"], "source span"
                ),
                start_code_point=_require_stored_integer(
                    data["start_code_point"], "source span"
                ),
                end_code_point=_require_stored_integer(
                    data["end_code_point"], "source span"
                ),
                page_number=_parse_optional_page_number(
                    data["page_number"], "source span"
                ),
            )
    except (DocumentError, TypeError, ValueError) as error:
        raise DocumentCorruptError(
            "Stored source-span metadata is invalid."
        ) from error
    raise DocumentCorruptError("Stored source-span metadata is invalid.")


def _parse_chunk(
    value: dict[str, object],
    *,
    maximum_mappings: int,
    maximum_text_code_points: int,
) -> DocumentChunk:
    """Rebuild one strict chunk and all citation source mappings."""

    _require_exact_fields(
        value,
        {
            "ordinal",
            "chunk_id",
            "kind",
            "text",
            "page_number",
            "source_mappings",
        },
        "chunk",
    )
    mappings_value = value["source_mappings"]
    if not isinstance(mappings_value, list):
        raise DocumentCorruptError("Stored chunk mappings are invalid.")
    if len(mappings_value) > maximum_mappings:
        raise DocumentCorruptError(
            "Stored chunk mappings exceed the lineage limit."
        )
    text = _require_stored_string(value["text"], "chunk")
    if len(text) > maximum_text_code_points:
        raise DocumentCorruptError(
            "Stored chunk text exceeds the lineage limit."
        )
    mappings: list[ChunkSourceMapping] = []
    for mapping_value in mappings_value:
        if not isinstance(mapping_value, dict):
            raise DocumentCorruptError("Stored chunk mappings are invalid.")
        mapping_data = cast(dict[str, object], mapping_value)
        _require_exact_fields(
            mapping_data,
            {
                "chunk_start_code_point",
                "chunk_end_code_point",
                "source_span",
            },
            "chunk mapping",
        )
        try:
            mappings.append(ChunkSourceMapping(
                chunk_start_code_point=_require_stored_integer(
                    mapping_data["chunk_start_code_point"],
                    "chunk mapping",
                ),
                chunk_end_code_point=_require_stored_integer(
                    mapping_data["chunk_end_code_point"],
                    "chunk mapping",
                ),
                source_span=_parse_span(mapping_data["source_span"]),
            ))
        except (DocumentError, TypeError, ValueError) as error:
            raise DocumentCorruptError(
                "Stored chunk mappings are invalid."
            ) from error
    try:
        return DocumentChunk(
            ordinal=_require_stored_integer(value["ordinal"], "chunk"),
            chunk_id=_require_stored_string(value["chunk_id"], "chunk"),
            kind=cast(
                Any,
                _require_stored_string(value["kind"], "chunk"),
            ),
            text=text,
            page_number=_parse_optional_page_number(
                value["page_number"], "chunk"
            ),
            source_mappings=tuple(mappings),
        )
    except (DocumentError, TypeError, ValueError) as error:
        raise DocumentCorruptError("Stored chunk metadata is invalid.") from error


def _snapshot_input_chunk(
    value: DocumentChunk | EmbeddedChunk,
    *,
    policy: DocumentChunkingPolicy,
    limits: DocumentProcessingLimits,
) -> DocumentChunk:
    """Deep-copy one untrusted input chunk through strict domain constructors."""

    try:
        # EmbeddedChunk intentionally repeats the exact citation fields carried
        # by DocumentChunk.  Serializing that common projection and parsing it
        # again prevents a frozen-dataclass bypass in any nested mapping/span
        # from becoming trusted persistence input.
        data = _chunk_to_data(cast(DocumentChunk, value))
        return _parse_chunk(
            data,
            maximum_mappings=limits.max_source_mappings_per_chunk,
            maximum_text_code_points=policy.max_chunk_code_points,
        )
    except (AttributeError, DocumentError, TypeError, ValueError) as error:
        raise DocumentValidationError(
            "Embedded document chunk lineage is invalid."
        ) from error


def _encode_vector(
    vector: Sequence[float],
    *,
    dimensions: int,
) -> tuple[bytes, tuple[float, ...], str]:
    """Canonicalize one finite vector as fixed little-endian float32 bytes."""

    if not isinstance(vector, tuple) or len(vector) != dimensions:
        raise DocumentValidationError(
            "Embedding vector does not match the configured dimensions."
        )
    parsed: list[float] = []
    for component in vector:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise DocumentValidationError(
                "Embedding vector must contain only finite numbers."
            )
        converted = float(component)
        if not math.isfinite(converted):
            raise DocumentValidationError(
                "Embedding vector must contain only finite numbers."
            )
        parsed.append(converted)
    try:
        packed = struct.pack(f"<{dimensions}f", *parsed)
        canonical = cast(
            tuple[float, ...],
            struct.unpack(f"<{dimensions}f", packed),
        )
    except (OverflowError, struct.error) as error:
        raise DocumentValidationError(
            "Embedding vector cannot be represented as float32."
        ) from error
    if not all(math.isfinite(component) for component in canonical):
        raise DocumentValidationError(
            "Embedding vector cannot be represented as finite float32 values."
        )
    if not any(component != 0.0 for component in canonical):
        raise DocumentValidationError("Embedding vector cannot be all zeroes.")
    if not math.isclose(
        math.hypot(*canonical),
        1.0,
        rel_tol=0.0,
        abs_tol=EMBEDDING_UNIT_NORM_TOLERANCE,
    ):
        raise DocumentValidationError(
            "Embedding vector must be L2-normalized."
        )
    checksum = hashlib.sha256(packed).hexdigest()
    return packed, canonical, checksum


def _decode_vector(
    value: object,
    checksum: object,
    *,
    dimensions: int,
) -> tuple[float, ...]:
    """Validate one stored fixed-width vector before exposing its values."""

    if not isinstance(value, bytes):
        raise DocumentCorruptError("Stored vector bytes are invalid.")
    expected_length = dimensions * 4
    if len(value) != expected_length:
        raise DocumentCorruptError("Stored vector byte length is invalid.")
    stored_checksum = _require_stored_string(checksum, "vector checksum")
    if _DIGEST_PATTERN.fullmatch(stored_checksum) is None:
        raise DocumentCorruptError("Stored vector checksum is invalid.")
    if not hashlib.sha256(value).hexdigest() == stored_checksum:
        raise DocumentCorruptError("Stored vector checksum does not match.")
    try:
        vector = cast(
            tuple[float, ...],
            struct.unpack(f"<{dimensions}f", value),
        )
    except struct.error as error:
        raise DocumentCorruptError("Stored vector bytes are invalid.") from error
    if (
        not all(math.isfinite(component) for component in vector)
        or not any(component != 0.0 for component in vector)
        or not math.isclose(
            math.hypot(*vector),
            1.0,
            rel_tol=0.0,
            abs_tol=EMBEDDING_UNIT_NORM_TOLERANCE,
        )
    ):
        raise DocumentCorruptError("Stored vector values are invalid.")
    return vector


def _is_reparse(details: Any) -> bool:
    """Return whether one Windows stat result identifies a reparse point."""

    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _require_safe_database_path(database_path: object) -> Path:
    """Resolve a trusted absolute database target without exposing its value."""

    if not isinstance(database_path, (str, Path)):
        raise DocumentValidationError("database_path must be a filesystem path.")
    path = Path(database_path)
    if not path.is_absolute():
        raise DocumentValidationError("database_path must be absolute.")
    try:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        parent_details = parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent_details.st_mode)
            or parent.is_symlink()
            or _is_reparse(parent_details)
        ):
            raise DocumentValidationError(
                "Vector-store parent directory is unsafe."
            )
        if path.exists() or path.is_symlink():
            details = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(details.st_mode)
                or path.is_symlink()
                or _is_reparse(details)
                or details.st_nlink != 1
            ):
                raise DocumentValidationError(
                    "Vector-store database target is unsafe."
                )
    except DocumentError:
        raise
    except OSError:
        raise VectorStorePersistenceError(
            "Vector-store storage could not be prepared."
        ) from None
    return path


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    """Hold one fully validated row ready for a SQLite transaction."""

    ordinal: int
    chunk_id: str
    embedding_id: str
    chunk_json: str
    chunk_checksum: str
    vector_blob: bytes
    vector_checksum: str


@dataclass(frozen=True, slots=True)
class _PreparedDocument:
    """Hold one validated document generation and its complete row set."""

    scope: AttachmentScope
    link_id: str
    derivation_fingerprint: str
    embedding_space_id: str
    lineage_json: str
    lineage_checksum: str
    records: tuple[_PreparedRecord, ...]


@dataclass(frozen=True, slots=True)
class _StoredDocumentMetadata:
    """Carry validated generation metadata used while decoding record rows."""

    chunked_document: ChunkedDocument
    policy: EmbeddingBatchPolicy
    derivation_fingerprint: str
    embedding_space_id: str
    record_count: int
    total_chunk_code_points: int
    total_source_mappings: int
    lineage_json_bytes: int


class SQLiteVectorStore:
    """Persist one embedding space with mandatory Chat/Project isolation.

    A database is permanently bound to an embedding-space identity and vector
    dimension at creation.  Callers therefore cannot accidentally mix vectors
    from different models, normalization rules, or prompt templates in one
    index.  The class opens short-lived SQLite connections under an in-process
    lock; SQLite transactions provide cross-process serialization and crash
    rollback without keeping a path-bearing handle in public state.
    """

    def __init__(
        self,
        database_path: str | Path,
        model_identity: EmbeddingModelIdentity,
        *,
        limits: VectorStoreLimits | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Open or create one database for the exact supplied model identity."""

        self._database_path = _require_safe_database_path(database_path)
        self._limits = _snapshot_limits(
            VectorStoreLimits() if limits is None else limits
        )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
            or timeout_seconds > 60
        ):
            raise DocumentValidationError(
                "timeout_seconds must be finite and between 0 and 60."
            )
        self._timeout_seconds = float(timeout_seconds)
        self._model_identity = model_identity
        self._embedding_space_id, self._dimensions = (
            self._validate_model_identity(model_identity)
        )
        if self._dimensions * 4 > self._limits.max_document_vector_bytes:
            raise DocumentValidationError(
                "One vector exceeds the configured document vector budget."
            )
        self._lock = RLock()
        self._initialize_database()

    @property
    def model_identity(self) -> EmbeddingModelIdentity:
        """Return the immutable embedding-space identity bound to this store."""

        return self._snapshot_identity(self._model_identity)

    @property
    def limits(self) -> VectorStoreLimits:
        """Return the exact persistence and public-read resource ceilings."""

        return _snapshot_limits(self._limits)

    def search_scope(
        self,
        *,
        scope: AttachmentScope,
        query: EmbeddedQuery,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        metadata_filter: RetrievalMetadataFilter,
        policy: RetrievalPolicy,
        limits: RetrievalLimits,
    ) -> tuple[StoredRetrievalCandidate, ...]:
        """Return bounded cosine candidates from one exact-scope snapshot.

        Every expected generation is an authorization and freshness claim from
        the caller.  Headers and records are therefore authenticated together
        inside one read transaction; a missing, stale, corrupt, or over-budget
        generation aborts the complete search instead of publishing a partial
        Top-K that could be mistaken for an exhaustive result.
        """

        canonical_scope = _require_scope(scope)
        canonical_query = self._snapshot_search_query(query)
        canonical_limits = self._snapshot_search_limits(limits)
        if type(expected_documents) is tuple and len(expected_documents) > min(
            canonical_limits.max_documents,
            self._limits.max_search_documents,
        ):
            # Reject the outer graph before copying attacker-controlled nested
            # source values; otherwise the validation pass itself could exceed
            # the corpus budget it is supposed to enforce.
            raise DocumentContentLimitError(
                "Vector search exceeds the document limit."
            )
        canonical_documents = self._snapshot_search_documents(
            canonical_scope,
            expected_documents,
        )
        canonical_filter = self._snapshot_search_filter(
            metadata_filter,
            canonical_limits.max_filter_values,
        )
        canonical_policy = self._snapshot_search_policy(policy)
        if not canonical_documents:
            return ()
        if len(canonical_documents) > min(
            canonical_limits.max_documents,
            self._limits.max_search_documents,
        ):
            raise DocumentContentLimitError(
                "Vector search exceeds the document limit."
            )
        if canonical_policy.candidate_k > self._limits.max_search_results:
            raise DocumentContentLimitError(
                "Vector search exceeds the result limit."
            )

        # Sorting the allowlist makes output independent of caller order and
        # lets the record stream prove contiguous generations deterministically.
        ordered_documents = tuple(
            sorted(
                canonical_documents,
                key=lambda item: item.source.link_id,
            )
        )
        expected_by_link = {
            item.source.link_id: item for item in ordered_documents
        }
        link_ids = tuple(expected_by_link)
        placeholders = ",".join("?" for _ in link_ids)

        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            deadline_exceeded = False
            deadline = monotonic() + self._timeout_seconds

            def _interrupt_search_after_deadline() -> int:
                """Interrupt SQLite work after the bounded search deadline."""

                nonlocal deadline_exceeded
                deadline_exceeded = monotonic() >= deadline
                return 1 if deadline_exceeded else 0

            progress_installed = False
            try:
                connection.set_progress_handler(
                    _interrupt_search_after_deadline,
                    _SQLITE_PROGRESS_INSTRUCTION_INTERVAL,
                )
                progress_installed = True
                connection.execute("BEGIN")
                header_size_cursor = connection.execute(
                    f"""
                    SELECT link_id,
                           CASE
                             WHEN typeof(lineage_json) = 'text'
                             THEN length(CAST(lineage_json AS BLOB))
                           END AS lineage_size
                    FROM vector_documents
                    WHERE scope_kind = ? AND scope_id = ?
                      AND link_id IN ({placeholders})
                    ORDER BY link_id
                    LIMIT ?
                    """,
                    (
                        canonical_scope.kind,
                        canonical_scope.id,
                        *link_ids,
                        len(link_ids) + 1,
                    ),
                )
                scanned_payload_bytes = 0
                lineage_sizes: dict[str, int] = {}
                while True:
                    self._require_search_time(deadline)
                    row = header_size_cursor.fetchone()
                    if row is None:
                        break
                    link_id = row["link_id"]
                    lineage_size = row["lineage_size"]
                    if (
                        not isinstance(link_id, str)
                        or link_id not in expected_by_link
                        or link_id in lineage_sizes
                        or type(lineage_size) is not int
                        or lineage_size < 0
                        or lineage_size
                        > self._limits.max_lineage_json_bytes
                    ):
                        raise DocumentCorruptError(
                            "Stored vector lineage size is invalid."
                        )
                    scanned_payload_bytes += lineage_size
                    if scanned_payload_bytes > min(
                        canonical_limits.max_scanned_payload_bytes,
                        self._limits.max_search_payload_bytes,
                    ):
                        # The size-only pass prevents SQLite from transferring
                        # an over-budget lineage value into Python first.
                        raise DocumentContentLimitError(
                            "Vector search exceeds the payload limit."
                        )
                    lineage_sizes[link_id] = lineage_size
                if len(lineage_sizes) != len(expected_by_link):
                    raise VectorStoreIncompleteError(
                        "A required vector generation is not indexed."
                    )

                header_cursor = connection.execute(
                    f"""
                    SELECT link_id,
                           CASE
                             WHEN typeof(derivation_fingerprint) = 'text'
                              AND length(CAST(derivation_fingerprint AS BLOB)) = 64
                             THEN derivation_fingerprint
                           END AS derivation_fingerprint,
                           CASE
                             WHEN typeof(embedding_space_id) = 'text'
                              AND length(CAST(embedding_space_id AS BLOB)) = 64
                             THEN embedding_space_id
                           END AS embedding_space_id,
                           CASE
                             WHEN typeof(lineage_json) = 'text'
                             THEN lineage_json
                           END AS lineage_json,
                           CASE
                             WHEN typeof(lineage_checksum) = 'text'
                              AND length(CAST(lineage_checksum AS BLOB)) = 64
                             THEN lineage_checksum
                           END AS lineage_checksum,
                           CASE
                             WHEN typeof(record_count) = 'integer'
                             THEN record_count
                           END AS record_count
                    FROM vector_documents
                    WHERE scope_kind = ? AND scope_id = ?
                      AND link_id IN ({placeholders})
                    ORDER BY link_id
                    LIMIT ?
                    """,
                    (
                        canonical_scope.kind,
                        canonical_scope.id,
                        *link_ids,
                        len(link_ids) + 1,
                    ),
                )
                metadata_by_link: dict[str, _StoredDocumentMetadata] = {}
                scanned_records = 0
                declared_source_mappings = 0
                while True:
                    self._require_search_time(deadline)
                    row = header_cursor.fetchone()
                    if row is None:
                        break
                    link_id = row["link_id"]
                    if (
                        not isinstance(link_id, str)
                        or link_id not in expected_by_link
                        or link_id in metadata_by_link
                    ):
                        raise DocumentCorruptError(
                            "Stored vector search headers are inconsistent."
                        )
                    expectation = expected_by_link[link_id]
                    metadata = self._decode_expected_document_metadata_row(
                        row,
                        canonical_scope,
                        link_id,
                        expectation.derivation_fingerprint,
                        canonical_query.identity.embedding_space_id,
                    )
                    if (
                        metadata.chunked_document.provenance.source
                        != expectation.source
                    ):
                        raise VectorStoreStaleError(
                            "Stored vectors do not match the requested source."
                        )
                    if metadata.lineage_json_bytes != lineage_sizes[link_id]:
                        raise DocumentCorruptError(
                            "Stored vector lineage size is inconsistent."
                        )
                    metadata_by_link[link_id] = metadata
                    scanned_records += metadata.record_count
                    declared_source_mappings += (
                        metadata.total_source_mappings
                    )
                    if scanned_records > min(
                        canonical_limits.max_scanned_records,
                        self._limits.max_search_records,
                    ):
                        raise DocumentContentLimitError(
                            "Vector search exceeds the record limit."
                        )
                    if declared_source_mappings > (
                        canonical_limits.max_source_mappings
                    ):
                        raise DocumentContentLimitError(
                            "Vector search exceeds the source-mapping limit."
                        )
                if len(metadata_by_link) != len(expected_by_link):
                    raise VectorStoreIncompleteError(
                        "A required vector generation is not indexed."
                    )

                record_size_cursor = connection.execute(
                    f"""
                    SELECT link_id,
                           CASE
                             WHEN typeof(chunk_json) = 'text'
                             THEN length(CAST(chunk_json AS BLOB))
                           END AS chunk_size,
                           CASE
                             WHEN typeof(vector_blob) = 'blob'
                             THEN length(vector_blob)
                           END AS vector_size
                    FROM vector_records
                    WHERE scope_kind = ? AND scope_id = ?
                      AND link_id IN ({placeholders})
                    ORDER BY link_id, ordinal
                    LIMIT ?
                    """,
                    (
                        canonical_scope.kind,
                        canonical_scope.id,
                        *link_ids,
                        scanned_records + 1,
                    ),
                )
                sized_record_counts = dict.fromkeys(link_ids, 0)
                while True:
                    self._require_search_time(deadline)
                    row = record_size_cursor.fetchone()
                    if row is None:
                        break
                    link_id = row["link_id"]
                    chunk_size = row["chunk_size"]
                    vector_size = row["vector_size"]
                    if (
                        not isinstance(link_id, str)
                        or link_id not in metadata_by_link
                        or type(chunk_size) is not int
                        or chunk_size < 0
                        or chunk_size > self._limits.max_chunk_json_bytes
                        or type(vector_size) is not int
                        or vector_size != self._dimensions * 4
                    ):
                        raise DocumentCorruptError(
                            "Stored vector record size is invalid."
                        )
                    sized_record_counts[link_id] += 1
                    scanned_payload_bytes += chunk_size + vector_size
                    if scanned_payload_bytes > min(
                        canonical_limits.max_scanned_payload_bytes,
                        self._limits.max_search_payload_bytes,
                    ):
                        # As with headers, reject from scalar sizes before the
                        # full JSON/BLOB columns cross into Python memory.
                        raise DocumentContentLimitError(
                            "Vector search exceeds the payload limit."
                        )
                if any(
                    sized_record_counts[link_id]
                    != metadata_by_link[link_id].record_count
                    for link_id in link_ids
                ):
                    raise DocumentCorruptError(
                        "Stored vector generation is incomplete."
                    )

                record_cursor = connection.execute(
                    f"""
                    SELECT link_id,
                           CASE WHEN typeof(ordinal) = 'integer'
                                THEN ordinal END AS ordinal,
                           CASE
                             WHEN typeof(chunk_id) = 'text'
                              AND length(CAST(chunk_id AS BLOB)) = 70
                             THEN chunk_id
                           END AS chunk_id,
                           CASE
                             WHEN typeof(embedding_id) = 'text'
                              AND length(CAST(embedding_id AS BLOB)) = 74
                             THEN embedding_id
                           END AS embedding_id,
                           CASE
                             WHEN typeof(chunk_json) = 'text'
                             THEN chunk_json
                           END AS chunk_json,
                           CASE
                             WHEN typeof(chunk_checksum) = 'text'
                              AND length(CAST(chunk_checksum AS BLOB)) = 64
                             THEN chunk_checksum
                           END AS chunk_checksum,
                           CASE
                             WHEN typeof(vector_blob) = 'blob'
                             THEN vector_blob
                           END AS vector_blob,
                           CASE
                             WHEN typeof(vector_checksum) = 'text'
                              AND length(CAST(vector_checksum AS BLOB)) = 64
                             THEN vector_checksum
                           END AS vector_checksum
                    FROM vector_records
                    WHERE scope_kind = ? AND scope_id = ?
                      AND link_id IN ({placeholders})
                    ORDER BY link_id, ordinal
                    LIMIT ?
                    """,
                    (
                        canonical_scope.kind,
                        canonical_scope.id,
                        *link_ids,
                        scanned_records + 1,
                    ),
                )
                actual_counts = dict.fromkeys(link_ids, 0)
                actual_code_points = dict.fromkeys(link_ids, 0)
                actual_mappings = dict.fromkeys(link_ids, 0)
                total_mappings = 0
                candidate_text_code_points = 0
                candidates: list[StoredRetrievalCandidate] = []
                while True:
                    self._require_search_time(deadline)
                    row = record_cursor.fetchone()
                    if row is None:
                        break
                    link_id = row["link_id"]
                    if not isinstance(link_id, str) or link_id not in (
                        metadata_by_link
                    ):
                        raise DocumentCorruptError(
                            "Stored vector search records are inconsistent."
                        )
                    metadata = metadata_by_link[link_id]
                    expected_ordinal = actual_counts[link_id]
                    if (
                        expected_ordinal >= metadata.record_count
                        or row["ordinal"] != expected_ordinal
                    ):
                        raise DocumentCorruptError(
                            "Stored vector generation is incomplete."
                        )
                    self._stored_record_sizes(row)
                    chunk = self._decode_record(
                        row,
                        metadata,
                        maximum_mappings=(
                            canonical_limits.max_source_mappings
                            - total_mappings
                        ),
                    )
                    actual_counts[link_id] += 1
                    actual_code_points[link_id] += len(chunk.text)
                    mapping_count = len(chunk.source_mappings)
                    actual_mappings[link_id] += mapping_count
                    total_mappings += mapping_count
                    if total_mappings > canonical_limits.max_source_mappings:
                        raise DocumentContentLimitError(
                            "Vector search exceeds the source-mapping limit."
                        )
                    source = metadata.chunked_document.provenance.source
                    if not self._matches_search_filter(
                        source,
                        chunk,
                        canonical_filter,
                    ):
                        continue
                    raw_score = math.fsum(
                        left * right
                        for left, right in zip(
                            canonical_query.vector.values,
                            chunk.vector,
                            strict=True,
                        )
                    )
                    score_tolerance = EMBEDDING_UNIT_NORM_TOLERANCE * 3
                    if (
                        not math.isfinite(raw_score)
                        or raw_score < -1.0 - score_tolerance
                        or raw_score > 1.0 + score_tolerance
                    ):
                        raise DocumentCorruptError(
                            "Stored vector produced an invalid cosine score."
                        )
                    score = round(max(-1.0, min(1.0, raw_score)), 8)
                    if score == 0.0:
                        score = 0.0
                    if score < canonical_policy.minimum_cosine_similarity:
                        continue
                    candidate = StoredRetrievalCandidate(
                        source=source,
                        derivation_fingerprint=(
                            metadata.derivation_fingerprint
                        ),
                        embedding_space_id=metadata.embedding_space_id,
                        embedding_id=chunk.embedding_id,
                        chunk_id=chunk.chunk_id,
                        ordinal=chunk.ordinal,
                        kind=chunk.kind,
                        text=chunk.text,
                        page_number=chunk.page_number,
                        source_mappings=chunk.source_mappings,
                        cosine_similarity=score,
                    )
                    candidate_text_code_points += self._retain_search_candidate(
                        candidates,
                        candidate,
                        canonical_policy.candidate_k,
                    )
                    if candidate_text_code_points > (
                        canonical_limits.max_candidate_text_code_points
                    ):
                        raise DocumentContentLimitError(
                            "Vector search exceeds the candidate-text limit."
                        )

                for link_id, metadata in metadata_by_link.items():
                    if (
                        actual_counts[link_id] != metadata.record_count
                        or actual_code_points[link_id]
                        != metadata.total_chunk_code_points
                        or actual_mappings[link_id]
                        != metadata.total_source_mappings
                    ):
                        raise DocumentCorruptError(
                            "Stored vector generation is incomplete."
                        )
                self._require_search_time(deadline)
                result = tuple(
                    sorted(candidates, key=self._search_candidate_order_key)
                )
                self._validate_database_resource_limits(connection)
                self._require_search_time(deadline)
                connection.commit()
                operation_failed = False
                return result
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error:
                self._rollback_quietly(connection)
                if deadline_exceeded:
                    raise DocumentContentLimitError(
                        "Vector search exceeded its time limit."
                    ) from None
                raise VectorStorePersistenceError(
                    "Vector-store search failed."
                ) from None
            finally:
                progress_removal_failed = False
                if progress_installed:
                    try:
                        connection.set_progress_handler(None, 0)
                    except sqlite3.Error:
                        progress_removal_failed = not operation_failed
                self._close_connection(
                    connection,
                    suppress_errors=(
                        operation_failed or progress_removal_failed
                    ),
                )
                if progress_removal_failed:
                    raise VectorStorePersistenceError(
                        "Vector-store search could not be bounded."
                    ) from None

    def _snapshot_search_query(self, query: object) -> EmbeddedQuery:
        """Copy an identity-bearing canonical query through strict domains."""

        schema_version, raw_identity, raw_policy, raw_vector = _read_exact_slots(
            query,
            EmbeddedQuery,
            ("schema_version", "identity", "policy", "vector"),
            "Vector search query is invalid.",
        )
        if (
            type(raw_identity) is not EmbeddingModelIdentity
            or type(raw_policy) is not EmbeddingBatchPolicy
            or type(raw_vector) is not EmbeddingVector
        ):
            raise DocumentValidationError("Vector search query is invalid.")
        identity = self._snapshot_identity(raw_identity)
        if identity != self._model_identity:
            raise VectorStoreModelMismatchError(
                "Vector store uses a different embedding model identity."
            )
        item_id, values = _read_exact_slots(
            raw_vector,
            EmbeddingVector,
            ("item_id", "values"),
            "Vector search query is invalid.",
        )
        policy_fields = _read_exact_slots(
            raw_policy,
            EmbeddingBatchPolicy,
            (
                "max_batch_items",
                "max_input_code_points",
                "max_batch_code_points",
                "truncate",
            ),
            "Vector search query is invalid.",
        )
        if (
            type(item_id) is not str
            or type(values) is not tuple
            or len(values) != identity.dimension
        ):
            raise DocumentValidationError("Vector search query is invalid.")
        # Validate the bounded dimension before iterating adapter-controlled
        # values so a mutated frozen query cannot create unbounded validation.
        if not all(type(value) is float for value in values):
            raise DocumentValidationError("Vector search query is invalid.")
        try:
            return EmbeddedQuery(
                schema_version=schema_version,
                identity=identity,
                policy=EmbeddingBatchPolicy(
                    max_batch_items=policy_fields[0],
                    max_input_code_points=policy_fields[1],
                    max_batch_code_points=policy_fields[2],
                    truncate=policy_fields[3],
                ),
                vector=EmbeddingVector(
                    item_id=item_id,
                    values=tuple(values),
                ),
            )
        except (AttributeError, EmbeddingError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Vector search query is invalid."
            ) from error

    def _snapshot_search_documents(
        self,
        scope: AttachmentScope,
        documents: object,
    ) -> tuple[ExpectedDocumentGeneration, ...]:
        """Copy the exact authorized generation allowlist and reject aliases."""

        if type(documents) is not tuple:
            raise DocumentValidationError(
                "Vector search documents must be a tuple."
            )
        copied: list[ExpectedDocumentGeneration] = []
        seen_links: set[str] = set()
        for document in documents:
            source, derivation_fingerprint = _read_exact_slots(
                document,
                ExpectedDocumentGeneration,
                ("source", "derivation_fingerprint"),
                "Vector search document expectation is invalid.",
            )
            if type(source) is not DocumentSource:
                raise DocumentValidationError(
                    "Vector search document expectation is invalid."
                )
            source_fields = _read_exact_slots(
                source,
                DocumentSource,
                (
                    "scope",
                    "link_id",
                    "file_id",
                    "file_name",
                    "media_type",
                    "size_bytes",
                ),
                "Vector search document expectation is invalid.",
            )
            if (
                type(source_fields[0]) is not AttachmentScope
                or not all(type(value) is str for value in source_fields[1:5])
                or type(source_fields[5]) is not int
                or type(derivation_fingerprint) is not str
            ):
                raise DocumentValidationError(
                    "Vector search document expectation is invalid."
                )
            try:
                source_snapshot = DocumentSource(
                    scope=_require_scope(source_fields[0]),
                    link_id=source_fields[1],
                    file_id=source_fields[2],
                    file_name=source_fields[3],
                    media_type=source_fields[4],
                    size_bytes=source_fields[5],
                )
                generation = ExpectedDocumentGeneration(
                    source=source_snapshot,
                    derivation_fingerprint=derivation_fingerprint,
                )
            except (AttributeError, DocumentError, TypeError, ValueError) as error:
                raise DocumentValidationError(
                    "Vector search document expectation is invalid."
                ) from error
            if source_snapshot.scope != scope:
                raise DocumentValidationError(
                    "Vector search document crosses the requested scope."
                )
            if source_snapshot.link_id in seen_links:
                raise DocumentValidationError(
                    "Vector search documents contain a duplicate link."
                )
            seen_links.add(source_snapshot.link_id)
            copied.append(generation)
        return tuple(copied)

    @staticmethod
    def _snapshot_search_filter(
        value: object,
        max_filter_values: int,
    ) -> RetrievalMetadataFilter:
        """Copy a closed filter only after enforcing its caller budget."""

        file_ids, media_types, chunk_kinds, page_numbers = _read_exact_slots(
            value,
            RetrievalMetadataFilter,
            ("file_ids", "media_types", "chunk_kinds", "page_numbers"),
            "Vector search filter is invalid.",
        )
        values = (file_ids, media_types, chunk_kinds, page_numbers)
        if not all(type(items) is tuple for items in values):
            raise DocumentValidationError("Vector search filter is invalid.")
        if sum(len(items) for items in values) > max_filter_values:
            raise DocumentContentLimitError(
                "Vector search exceeds the metadata-filter limit."
            )
        try:
            return RetrievalMetadataFilter(
                file_ids=file_ids,
                media_types=media_types,
                chunk_kinds=chunk_kinds,
                page_numbers=page_numbers,
            )
        except (AttributeError, DocumentError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Vector search filter is invalid."
            ) from error

    @staticmethod
    def _snapshot_search_policy(value: object) -> RetrievalPolicy:
        """Copy the deterministic candidate and threshold policy."""

        top_k, candidate_k, minimum_similarity = _read_exact_slots(
            value,
            RetrievalPolicy,
            ("top_k", "candidate_k", "minimum_cosine_similarity"),
            "Vector search policy is invalid.",
        )
        try:
            return RetrievalPolicy(
                top_k=top_k,
                candidate_k=candidate_k,
                minimum_cosine_similarity=minimum_similarity,
            )
        except (AttributeError, DocumentError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Vector search policy is invalid."
            ) from error

    @staticmethod
    def _snapshot_search_limits(value: object) -> RetrievalLimits:
        """Copy every retrieval resource ceiling before opening SQLite."""

        limit_fields = _read_exact_slots(
            value,
            RetrievalLimits,
            (
                "max_query_code_points",
                "max_documents",
                "max_filter_values",
                "max_scanned_records",
                "max_scanned_payload_bytes",
                "max_candidate_text_code_points",
                "max_source_mappings",
            ),
            "Vector search limits are invalid.",
        )
        try:
            return RetrievalLimits(
                max_query_code_points=limit_fields[0],
                max_documents=limit_fields[1],
                max_filter_values=limit_fields[2],
                max_scanned_records=limit_fields[3],
                max_scanned_payload_bytes=limit_fields[4],
                max_candidate_text_code_points=limit_fields[5],
                max_source_mappings=limit_fields[6],
            )
        except (AttributeError, DocumentError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Vector search limits are invalid."
            ) from error

    @staticmethod
    def _matches_search_filter(
        source: DocumentSource,
        chunk: EmbeddedChunk,
        metadata_filter: RetrievalMetadataFilter,
    ) -> bool:
        """Apply exact AND-across-fields retrieval metadata filters."""

        return (
            (not metadata_filter.file_ids or source.file_id in metadata_filter.file_ids)
            and (
                not metadata_filter.media_types
                or source.media_type in metadata_filter.media_types
            )
            and (
                not metadata_filter.chunk_kinds
                or chunk.kind in metadata_filter.chunk_kinds
            )
            and (
                not metadata_filter.page_numbers
                or chunk.page_number in metadata_filter.page_numbers
            )
        )

    @staticmethod
    def _search_candidate_order_key(
        candidate: StoredRetrievalCandidate,
    ) -> tuple[float, str, int, str]:
        """Return the portable cosine-descending candidate order."""

        return (
            -candidate.cosine_similarity,
            candidate.source.link_id,
            candidate.ordinal,
            candidate.chunk_id,
        )

    @classmethod
    def _retain_search_candidate(
        cls,
        candidates: list[StoredRetrievalCandidate],
        candidate: StoredRetrievalCandidate,
        limit: int,
    ) -> int:
        """Keep a fixed Top-K buffer and return its text-size delta."""

        if len(candidates) < limit:
            candidates.append(candidate)
            return len(candidate.text)
        worst_index = max(
            range(len(candidates)),
            key=lambda index: cls._search_candidate_order_key(
                candidates[index]
            ),
        )
        if cls._search_candidate_order_key(candidate) < (
            cls._search_candidate_order_key(candidates[worst_index])
        ):
            replaced_text_size = len(candidates[worst_index].text)
            candidates[worst_index] = candidate
            return len(candidate.text) - replaced_text_size
        return 0

    @staticmethod
    def _require_search_time(deadline: float) -> None:
        """Stop Python-side decoding once the deterministic time budget ends."""

        if monotonic() >= deadline:
            raise DocumentContentLimitError(
                "Vector search exceeded its time limit."
            )

    def replace_document(
        self,
        scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        """Atomically replace one ownership link's complete vector generation."""

        canonical_scope = _require_scope(scope)
        prepared = self._prepare_document(canonical_scope, document)
        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._delete_document_rows(
                    connection,
                    canonical_scope,
                    prepared.link_id,
                )
                self._insert_prepared_document(connection, prepared)
                self._commit_bounded_write(connection)
                operation_failed = False
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error as error:
                self._rollback_quietly(connection)
                self._raise_write_error(
                    error,
                    "Vector-store document replacement failed.",
                )
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    def list_document(
        self,
        scope: AttachmentScope,
        link_id: str,
        *,
        expected_derivation_fingerprint: str,
        expected_identity: EmbeddingModelIdentity,
        limit: int = 100,
        after_ordinal: int | None = None,
    ) -> tuple[EmbeddedChunk, ...]:
        """Return one bounded page after proving exact generation identities."""

        canonical_scope = _require_scope(scope)
        canonical_link_id = self._validate_link_id(link_id)
        derivation = _require_digest(
            expected_derivation_fingerprint,
            "expected_derivation_fingerprint",
        )
        expected_space_id = self._require_expected_model(expected_identity)
        page_size = _require_positive_integer(limit, "limit")
        if page_size > self._limits.max_list_records:
            raise DocumentContentLimitError(
                "Vector-store list limit exceeds the configured maximum."
            )
        cursor = -1
        if after_ordinal is not None:
            cursor = _require_non_negative_integer(
                after_ordinal,
                "after_ordinal",
            )
        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN")
                metadata = self._load_expected_document_metadata(
                    connection,
                    canonical_scope,
                    canonical_link_id,
                    derivation,
                    expected_space_id,
                )
                if metadata is None:
                    connection.commit()
                    operation_failed = False
                    return ()
                cursor_result = connection.execute(
                    """
                    SELECT ordinal, chunk_id, embedding_id, chunk_json,
                           chunk_checksum, vector_blob, vector_checksum
                    FROM vector_records
                    WHERE scope_kind = ? AND scope_id = ? AND link_id = ?
                      AND ordinal > ?
                    ORDER BY ordinal
                    LIMIT ?
                    """,
                    (
                        canonical_scope.kind,
                        canonical_scope.id,
                        canonical_link_id,
                        cursor,
                        page_size,
                    ),
                )
                decoded: list[EmbeddedChunk] = []
                metadata_bytes = 0
                vector_bytes = 0
                page_code_points = 0
                page_source_mappings = 0
                while True:
                    row = cursor_result.fetchone()
                    if row is None:
                        break
                    chunk_size, vector_size = self._stored_record_sizes(row)
                    metadata_bytes += chunk_size
                    vector_bytes += vector_size
                    if metadata_bytes > (
                        self._limits.max_document_metadata_bytes
                    ):
                        raise DocumentCorruptError(
                            "Stored vector page exceeds its metadata limit."
                        )
                    if vector_bytes > self._limits.max_document_vector_bytes:
                        raise DocumentCorruptError(
                            "Stored vector page exceeds its vector limit."
                        )
                    decoded_chunk = self._decode_record(row, metadata)
                    page_code_points += len(decoded_chunk.text)
                    page_source_mappings += len(
                        decoded_chunk.source_mappings
                    )
                    if (
                        page_code_points > metadata.total_chunk_code_points
                        or page_source_mappings
                        > metadata.total_source_mappings
                    ):
                        raise DocumentCorruptError(
                            "Stored vector page exceeds its lineage totals."
                        )
                    decoded.append(decoded_chunk)
                result = tuple(decoded)
                connection.commit()
                operation_failed = False
                return result
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error:
                self._rollback_quietly(connection)
                raise VectorStorePersistenceError(
                    "Vector-store document read failed."
                ) from None
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    def get_record(
        self,
        scope: AttachmentScope,
        link_id: str,
        chunk_id: str,
        *,
        expected_derivation_fingerprint: str,
        expected_identity: EmbeddingModelIdentity,
    ) -> EmbeddedChunk | None:
        """Read one scoped chunk only after proving its current generation."""

        canonical_scope = _require_scope(scope)
        canonical_link_id = self._validate_link_id(link_id)
        if (
            not isinstance(chunk_id, str)
            or _CHUNK_ID_PATTERN.fullmatch(chunk_id) is None
        ):
            raise DocumentValidationError("chunk_id is invalid.")
        derivation = _require_digest(
            expected_derivation_fingerprint,
            "expected_derivation_fingerprint",
        )
        expected_space_id = self._require_expected_model(expected_identity)
        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN")
                metadata = self._load_expected_document_metadata(
                    connection,
                    canonical_scope,
                    canonical_link_id,
                    derivation,
                    expected_space_id,
                )
                if metadata is None:
                    connection.commit()
                    operation_failed = False
                    return None
                row = connection.execute(
                    """
                    SELECT ordinal, chunk_id, embedding_id, chunk_json,
                           chunk_checksum, vector_blob, vector_checksum
                    FROM vector_records
                    WHERE scope_kind = ? AND scope_id = ? AND link_id = ?
                      AND chunk_id = ?
                    """,
                    (
                        canonical_scope.kind,
                        canonical_scope.id,
                        canonical_link_id,
                        chunk_id,
                    ),
                ).fetchone()
                if row is None:
                    result = None
                else:
                    chunk_size, vector_size = self._stored_record_sizes(row)
                    if (
                        chunk_size
                        > self._limits.max_document_metadata_bytes
                        or vector_size
                        > self._limits.max_document_vector_bytes
                    ):
                        raise DocumentCorruptError(
                            "Stored vector record exceeds its document limit."
                        )
                    result = self._decode_record(row, metadata)
                    if (
                        len(result.text) > metadata.total_chunk_code_points
                        or len(result.source_mappings)
                        > metadata.total_source_mappings
                    ):
                        raise DocumentCorruptError(
                            "Stored vector record exceeds its lineage totals."
                        )
                connection.commit()
                operation_failed = False
                return result
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error:
                self._rollback_quietly(connection)
                raise VectorStorePersistenceError(
                    "Vector-store record read failed."
                ) from None
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    def delete_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Delete one exact scoped ownership link and all of its vectors."""

        canonical_scope = _require_scope(scope)
        canonical_link_id = self._validate_link_id(link_id)
        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN IMMEDIATE")
                deleted = self._delete_document_rows(
                    connection,
                    canonical_scope,
                    canonical_link_id,
                )
                self._commit_bounded_write(connection)
                operation_failed = False
                return deleted
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error as error:
                self._rollback_quietly(connection)
                self._raise_write_error(
                    error,
                    "Vector-store document deletion failed.",
                )
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    def delete_scope(self, scope: AttachmentScope) -> int:
        """Delete all document generations in one exact Chat or Project scope."""

        canonical_scope = _require_scope(scope)
        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    DELETE FROM vector_documents
                    WHERE scope_kind = ? AND scope_id = ?
                    """,
                    (canonical_scope.kind, canonical_scope.id),
                )
                deleted = cursor.rowcount
                self._commit_bounded_write(connection)
                operation_failed = False
                return deleted
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error as error:
                self._rollback_quietly(connection)
                self._raise_write_error(
                    error,
                    "Vector-store scope deletion failed.",
                )
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    def rebuild(
        self,
        scope: AttachmentScope,
        documents: Iterable[EmbeddedDocument],
    ) -> None:
        """Atomically replace the exact document set for one isolated scope."""

        canonical_scope = _require_scope(scope)
        prepared_documents: list[_PreparedDocument] = []
        total_records = 0
        seen_links: set[str] = set()
        try:
            iterator = iter(documents)
        except TypeError as error:
            raise DocumentValidationError(
                "documents must be an iterable of EmbeddedDocument values."
            ) from error
        for document in iterator:
            if len(prepared_documents) >= self._limits.max_rebuild_documents:
                raise DocumentContentLimitError(
                    "Vector-store rebuild exceeds its document limit."
                )
            prepared = self._prepare_document(canonical_scope, document)
            if prepared.link_id in seen_links:
                raise DocumentValidationError(
                    "Vector-store rebuild contains duplicate ownership links."
                )
            seen_links.add(prepared.link_id)
            total_records += len(prepared.records)
            if total_records > self._limits.max_rebuild_records:
                raise DocumentContentLimitError(
                    "Vector-store rebuild exceeds its record limit."
                )
            prepared_documents.append(prepared)
        with self._lock:
            connection = self._open_validated_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    DELETE FROM vector_documents
                    WHERE scope_kind = ? AND scope_id = ?
                    """,
                    (canonical_scope.kind, canonical_scope.id),
                )
                for prepared in prepared_documents:
                    self._insert_prepared_document(connection, prepared)
                self._commit_bounded_write(connection)
                operation_failed = False
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error as error:
                self._rollback_quietly(connection)
                self._raise_write_error(error, "Vector-store rebuild failed.")
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    @staticmethod
    def _snapshot_identity(
        identity: object,
    ) -> EmbeddingModelIdentity:
        """Reconstruct an identity so mutated frozen objects cannot pass."""

        identity_fields = _read_exact_slots(
            identity,
            EmbeddingModelIdentity,
            (
                "provider",
                "adapter_id",
                "adapter_version",
                "model_tag",
                "model_digest",
                "dimension",
                "normalization",
                "document_template_version",
                "query_template_version",
                "embedding_space_id",
            ),
            "model_identity must be EmbeddingModelIdentity.",
        )
        if (
            not all(type(value) is str for value in identity_fields[:5])
            or type(identity_fields[5]) is not int
            or not all(type(value) is str for value in identity_fields[6:])
        ):
            raise DocumentValidationError(
                "model_identity must be EmbeddingModelIdentity."
            )
        try:
            return EmbeddingModelIdentity(
                provider=identity_fields[0],
                adapter_id=identity_fields[1],
                adapter_version=identity_fields[2],
                model_tag=identity_fields[3],
                model_digest=identity_fields[4],
                dimension=identity_fields[5],
                normalization=identity_fields[6],
                document_template_version=identity_fields[7],
                query_template_version=identity_fields[8],
                embedding_space_id=identity_fields[9],
            )
        except (AttributeError, EmbeddingError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Embedding model identity is invalid."
            ) from error

    def _validate_model_identity(
        self,
        identity: object,
    ) -> tuple[str, int]:
        """Validate and snapshot the model bound to this database."""

        snapshot = self._snapshot_identity(identity)
        self._model_identity = snapshot
        return snapshot.embedding_space_id, snapshot.dimension

    def _require_expected_model(
        self,
        identity: object,
    ) -> str:
        """Require callers to name the exact model identity on every read."""

        snapshot = self._snapshot_identity(identity)
        if snapshot != self._model_identity:
            raise VectorStoreModelMismatchError(
                "Vector store uses a different embedding model identity."
            )
        return snapshot.embedding_space_id

    @staticmethod
    def _validate_link_id(link_id: object) -> str:
        """Validate one opaque ownership link without exposing its value."""

        try:
            return validate_attachment_id(link_id)
        except ValueError as error:
            raise DocumentValidationError("link_id is invalid.") from error

    def _prepare_document(
        self,
        scope: AttachmentScope,
        document: object,
    ) -> _PreparedDocument:
        """Snapshot and validate a complete generation before opening a write."""

        if not isinstance(document, EmbeddedDocument):
            raise DocumentValidationError(
                "document must be EmbeddedDocument."
            )
        identity = self._snapshot_identity(document.identity)
        if identity != self._model_identity:
            raise VectorStoreModelMismatchError(
                "Embedded document uses a different model identity."
            )
        try:
            policy = EmbeddingBatchPolicy(
                max_batch_items=document.policy.max_batch_items,
                max_input_code_points=document.policy.max_input_code_points,
                max_batch_code_points=document.policy.max_batch_code_points,
                truncate=document.policy.truncate,
            )
        except (AttributeError, EmbeddingError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Embedded document policy is invalid."
            ) from error
        source = document.source
        if not isinstance(source, DocumentSource):
            raise DocumentValidationError("Embedded document source is invalid.")
        try:
            source_snapshot = DocumentSource(
                scope=_require_scope(source.scope),
                link_id=source.link_id,
                file_id=source.file_id,
                file_name=source.file_name,
                media_type=source.media_type,
                size_bytes=source.size_bytes,
            )
        except (DocumentError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Embedded document source is invalid."
            ) from error
        if source_snapshot.scope != scope:
            raise DocumentValidationError(
                "Embedded document scope does not match the requested scope."
            )
        link_id = self._validate_link_id(source_snapshot.link_id)
        derivation = _require_digest(
            document.derivation_fingerprint,
            "derivation_fingerprint",
        )
        if not isinstance(document.chunks, tuple):
            raise DocumentValidationError(
                "Embedded document chunks must be a tuple."
            )
        if len(document.chunks) > self._limits.max_records_per_document:
            raise DocumentContentLimitError(
                "Embedded document exceeds the vector-store record limit."
            )

        raw_chunked = document.chunked_document
        if (
            not isinstance(raw_chunked, ChunkedDocument)
            or not isinstance(raw_chunked.chunks, tuple)
            or len(raw_chunked.chunks) != len(document.chunks)
        ):
            raise DocumentValidationError(
                "Embedded document chunk lineage is invalid."
            )
        try:
            lineage_data = _lineage_to_data(raw_chunked)
            parsed_header = _parse_lineage_header(lineage_data)
            parsed_policy = cast(
                DocumentChunkingPolicy,
                parsed_header["policy"],
            )
            parsed_limits = cast(
                DocumentProcessingLimits,
                parsed_header["limits"],
            )
            source_chunks = tuple(
                _snapshot_input_chunk(
                    raw_source_chunk,
                    policy=parsed_policy,
                    limits=parsed_limits,
                )
                for raw_source_chunk in raw_chunked.chunks
            )
            chunked_snapshot = ChunkedDocument(
                **cast(Any, parsed_header),
                chunks=source_chunks,
            )
        except (DocumentError, AttributeError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Embedded document chunk lineage is invalid."
            ) from error

        embedded_chunks: list[EmbeddedChunk] = []
        prepared_records: list[_PreparedRecord] = []
        metadata_bytes = 0
        vector_bytes = 0
        seen_chunk_ids: set[str] = set()
        seen_embedding_ids: set[str] = set()
        for expected_ordinal, raw_chunk in enumerate(document.chunks):
            if not isinstance(raw_chunk, EmbeddedChunk):
                raise DocumentValidationError(
                    "Embedded document contains an invalid chunk."
                )
            source_chunk = _snapshot_input_chunk(
                raw_chunk,
                policy=chunked_snapshot.policy,
                limits=chunked_snapshot.limits,
            )
            if source_chunk.ordinal != expected_ordinal:
                raise DocumentValidationError(
                    "Embedded chunk ordinals must be contiguous from zero."
                )
            if source_chunk != source_chunks[expected_ordinal]:
                raise DocumentValidationError(
                    "Embedded chunk changed its exact source lineage."
                )
            packed, canonical_vector, checksum = _encode_vector(
                raw_chunk.vector,
                dimensions=self._dimensions,
            )
            if raw_chunk.vector_checksum != checksum:
                raise DocumentValidationError(
                    "Embedded vector checksum does not match its values."
                )
            try:
                embedded_chunk = EmbeddedChunk(
                    embedding_id=raw_chunk.embedding_id,
                    chunk_id=source_chunk.chunk_id,
                    ordinal=source_chunk.ordinal,
                    kind=source_chunk.kind,
                    text=source_chunk.text,
                    page_number=source_chunk.page_number,
                    source_mappings=source_chunk.source_mappings,
                    derivation_fingerprint=raw_chunk.derivation_fingerprint,
                    embedding_space_id=raw_chunk.embedding_space_id,
                    vector=canonical_vector,
                    vector_checksum=checksum,
                )
            except (EmbeddingError, TypeError, ValueError) as error:
                raise DocumentValidationError(
                    "Embedded chunk metadata is invalid."
                ) from error
            if source_chunk.chunk_id in seen_chunk_ids:
                raise DocumentValidationError(
                    "Embedded chunk IDs must be unique."
                )
            if embedded_chunk.embedding_id in seen_embedding_ids:
                raise DocumentValidationError(
                    "Embedded vector IDs must be unique."
                )
            seen_chunk_ids.add(source_chunk.chunk_id)
            seen_embedding_ids.add(embedded_chunk.embedding_id)
            chunk_bytes = _canonical_json_bytes(_chunk_to_data(source_chunk))
            if len(chunk_bytes) > self._limits.max_chunk_json_bytes:
                raise DocumentContentLimitError(
                    "Embedded chunk metadata exceeds the configured limit."
                )
            metadata_bytes += len(chunk_bytes)
            vector_bytes += len(packed)
            if metadata_bytes > self._limits.max_document_metadata_bytes:
                raise DocumentContentLimitError(
                    "Embedded document metadata exceeds the configured limit."
                )
            if vector_bytes > self._limits.max_document_vector_bytes:
                raise DocumentContentLimitError(
                    "Embedded document vectors exceed the configured limit."
                )
            chunk_checksum = hashlib.sha256(chunk_bytes).hexdigest()
            prepared_records.append(_PreparedRecord(
                ordinal=source_chunk.ordinal,
                chunk_id=source_chunk.chunk_id,
                embedding_id=embedded_chunk.embedding_id,
                chunk_json=chunk_bytes.decode("utf-8"),
                chunk_checksum=chunk_checksum,
                vector_blob=packed,
                vector_checksum=checksum,
            ))
            embedded_chunks.append(embedded_chunk)
        try:
            embedded_snapshot = EmbeddedDocument(
                schema_version=document.schema_version,
                chunked_schema_version=document.chunked_schema_version,
                source=source_snapshot,
                derivation_fingerprint=derivation,
                identity=identity,
                policy=policy,
                chunked_document=chunked_snapshot,
                chunks=tuple(embedded_chunks),
            )
        except (EmbeddingError, TypeError, ValueError) as error:
            raise DocumentValidationError(
                "Embedded document lineage is invalid."
            ) from error
        lineage_bytes = _canonical_json_bytes(
            _embedded_lineage_to_data(embedded_snapshot)
        )
        if len(lineage_bytes) > self._limits.max_lineage_json_bytes:
            raise DocumentContentLimitError(
                "Embedded document lineage exceeds the configured limit."
            )
        return _PreparedDocument(
            scope=scope,
            link_id=link_id,
            derivation_fingerprint=derivation,
            embedding_space_id=identity.embedding_space_id,
            lineage_json=lineage_bytes.decode("utf-8"),
            lineage_checksum=hashlib.sha256(lineage_bytes).hexdigest(),
            records=tuple(prepared_records),
        )

    @staticmethod
    def _rollback_quietly(connection: sqlite3.Connection) -> None:
        """Attempt rollback without replacing the operation's primary error."""

        try:
            connection.rollback()
        except sqlite3.Error:
            pass

    def _commit_bounded_write(self, connection: sqlite3.Connection) -> None:
        """Recheck database resources immediately before a durable commit."""

        # SQLite's connection-local max_page_count prevents page allocation
        # beyond policy.  The physical sample additionally counts a rollback
        # journal or WAL created by this transaction before it can publish.
        self._validate_database_resource_limits(connection)
        connection.commit()

    @staticmethod
    def _raise_write_error(
        error: sqlite3.Error,
        persistence_message: str,
        *,
        database_error_is_corrupt: bool = False,
    ) -> NoReturn:
        """Map one failed write without exposing SQLite or filesystem text."""

        if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            raise DocumentContentLimitError(
                "Vector-store database exceeds the configured storage limit."
            ) from None
        if database_error_is_corrupt and isinstance(error, sqlite3.DatabaseError):
            raise DocumentCorruptError(
                "Vector-store database is invalid."
            ) from None
        raise VectorStorePersistenceError(persistence_message) from None

    @staticmethod
    def _close_connection(
        connection: sqlite3.Connection,
        *,
        suppress_errors: bool,
    ) -> None:
        """Close one handle or expose only a fixed path-private failure."""

        try:
            connection.close()
        except sqlite3.Error:
            if not suppress_errors:
                raise VectorStorePersistenceError(
                    "Vector-store database connection could not be closed."
                ) from None

    def _initialize_database(self) -> None:
        """Create the v1 schema once or validate an existing database."""

        with self._lock:
            connection = self._open_connection()
            operation_failed = True
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._run_bounded_validation(
                    connection,
                    self._initialize_or_validate_database,
                )
                self._commit_bounded_write(connection)
                operation_failed = False
            except DocumentError:
                self._rollback_quietly(connection)
                raise
            except sqlite3.Error as error:
                self._rollback_quietly(connection)
                self._raise_write_error(
                    error,
                    "Vector-store database could not be initialized.",
                    database_error_is_corrupt=True,
                )
            finally:
                self._close_connection(
                    connection,
                    suppress_errors=operation_failed,
                )

    def _open_connection(self) -> sqlite3.Connection:
        """Open one hardened short-lived SQLite connection."""

        _require_safe_database_path(self._database_path)
        self._require_database_files_within_limit()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._timeout_seconds,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                f"PRAGMA busy_timeout = {int(self._timeout_seconds * 1000)}"
            )
            if hasattr(connection, "setlimit"):
                maximum_value = max(
                    self._limits.max_chunk_json_bytes,
                    self._limits.max_lineage_json_bytes,
                    self._dimensions * 4,
                ) + 1024
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, maximum_value)
            return connection
        except BaseException as error:
            # A successful ``connect`` can still be followed by a failing
            # PRAGMA or runtime-limit call.  Always release that partial
            # handle, including during cancellation, so Windows does not keep
            # the database path locked after a failed open.
            if connection is not None:
                self._close_connection(connection, suppress_errors=True)
            if isinstance(error, sqlite3.Error):
                raise VectorStorePersistenceError(
                    "Vector-store database could not be opened."
                ) from None
            raise

    def _open_validated_connection(self) -> sqlite3.Connection:
        """Open and revalidate schema/model identity against path replacement."""

        connection = self._open_connection()
        try:
            self._run_bounded_validation(
                connection,
                self._validate_existing_database,
            )
        except BaseException as error:
            self._close_connection(connection, suppress_errors=True)
            if isinstance(error, DocumentError):
                raise
            if isinstance(error, sqlite3.DatabaseError):
                raise DocumentCorruptError(
                    "Vector-store database is invalid."
                ) from None
            if isinstance(error, sqlite3.Error):
                raise VectorStorePersistenceError(
                    "Vector-store database could not be validated."
                ) from None
            raise
        return connection

    def _require_database_files_within_limit(self) -> None:
        """Bound trusted main/sidecar bytes without following filesystem links."""

        total_bytes = 0
        main_exists = False
        sidecar_exists = False
        for suffix in _DATABASE_SIDECAR_SUFFIXES:
            candidate = self._database_path.with_name(
                f"{self._database_path.name}{suffix}"
            )
            try:
                details = candidate.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                raise VectorStorePersistenceError(
                    "Vector-store database files could not be inspected."
                ) from None
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_ISLNK(details.st_mode)
                or _is_reparse(details)
                or details.st_nlink != 1
            ):
                raise DocumentCorruptError(
                    "Vector-store database files are unsafe."
                )
            if suffix:
                sidecar_exists = True
            else:
                main_exists = True
            total_bytes += details.st_size
            if total_bytes > self._limits.max_database_bytes:
                raise DocumentContentLimitError(
                    "Vector-store database exceeds the configured byte limit."
                )
        # SQLite sidecars have meaning only beside their main database.  A
        # stale journal or WAL must not be adopted by creating a new main file.
        if sidecar_exists and not main_exists:
            raise DocumentCorruptError(
                "Vector-store database files are inconsistent."
            )

    def _validate_database_resource_limits(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Double-check physical and logical database size across validation."""

        self._require_database_files_within_limit()
        page_count = self._read_pragma_integer(connection, "page_count")
        page_size = self._read_pragma_integer(connection, "page_size")
        if (
            page_count < 0
            or page_size < 512
            or page_size > 65_536
            or page_size & (page_size - 1) != 0
        ):
            raise DocumentCorruptError(
                "Vector-store database size metadata is invalid."
            )
        if page_count > self._limits.max_database_pages:
            raise DocumentContentLimitError(
                "Vector-store database exceeds the configured page limit."
            )
        if page_count * page_size > self._limits.max_database_bytes:
            raise DocumentContentLimitError(
                "Vector-store database exceeds the configured byte limit."
            )
        effective_max_pages = min(
            self._limits.max_database_pages,
            self._limits.max_database_bytes // page_size,
        )
        if effective_max_pages < 1:
            raise DocumentContentLimitError(
                "Vector-store database byte limit cannot hold one page."
            )
        maximum_row = connection.execute(
            f"PRAGMA max_page_count = {effective_max_pages}"
        ).fetchone()
        if (
            maximum_row is None
            or type(maximum_row[0]) is not int
            or maximum_row[0] != effective_max_pages
        ):
            raise VectorStorePersistenceError(
                "Vector-store database page limit could not be installed."
            )
        # Repeat the filesystem check after both PRAGMAs so a concurrent WAL
        # or journal growth cannot bypass the pre-query physical-byte sample.
        self._require_database_files_within_limit()

    def _run_bounded_validation(
        self,
        connection: sqlite3.Connection,
        operation: Callable[[sqlite3.Connection], None],
    ) -> None:
        """Run SQLite validation under one monotonic wall-clock deadline."""

        deadline = monotonic() + self._timeout_seconds
        deadline_exceeded = False

        def _interrupt_after_deadline() -> int:
            """Interrupt SQLite VM work once the validation budget expires."""

            nonlocal deadline_exceeded
            deadline_exceeded = monotonic() >= deadline
            return 1 if deadline_exceeded else 0

        try:
            connection.set_progress_handler(
                _interrupt_after_deadline,
                _SQLITE_PROGRESS_INSTRUCTION_INTERVAL,
            )
        except sqlite3.Error:
            raise VectorStorePersistenceError(
                "Vector-store validation could not be bounded."
            ) from None

        operation_failed = True
        try:
            try:
                operation(connection)
            except sqlite3.Error:
                if deadline_exceeded:
                    raise DocumentContentLimitError(
                        "Vector-store validation exceeded the configured "
                        "time limit."
                    ) from None
                raise
            if monotonic() >= deadline:
                deadline_exceeded = True
                raise DocumentContentLimitError(
                    "Vector-store validation exceeded the configured time "
                    "limit."
                )
            operation_failed = False
        finally:
            try:
                connection.set_progress_handler(None, 0)
            except sqlite3.Error:
                # Preserve a primary corruption/timeout exception.  On a
                # successful validation, inability to remove the callback is
                # itself a persistence failure because the handle is reused.
                if not operation_failed:
                    raise VectorStorePersistenceError(
                        "Vector-store validation could not be bounded."
                    ) from None

    def _initialize_or_validate_database(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Create only a truly empty store, then validate its complete state."""

        self._validate_database_resource_limits(connection)
        self._validate_database_encoding(connection)
        schema_objects = self._read_schema_objects(connection)
        version = self._read_pragma_integer(connection, "user_version")
        application_id = self._read_pragma_integer(
            connection,
            "application_id",
        )
        if not schema_objects and version == 0 and application_id == 0:
            self._create_schema(connection)
        self._validate_schema(connection)
        self._validate_database_identity(connection)
        self._validate_database_resource_limits(connection)

    def _validate_existing_database(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Validate one reopened store plus both resource-boundary samples."""

        self._validate_database_resource_limits(connection)
        self._validate_database_encoding(connection)
        self._validate_schema(connection)
        self._validate_database_identity(connection)
        self._validate_database_resource_limits(connection)

    @staticmethod
    def _read_pragma_integer(
        connection: sqlite3.Connection,
        name: Literal[
            "user_version",
            "application_id",
            "page_count",
            "page_size",
        ],
    ) -> int:
        """Read one allowlisted integer PRAGMA value."""

        row = connection.execute(f"PRAGMA {name}").fetchone()
        if row is None or type(row[0]) is not int:
            raise DocumentCorruptError(
                "Vector-store schema metadata is invalid."
            )
        return cast(int, row[0])

    @staticmethod
    def _validate_database_encoding(connection: sqlite3.Connection) -> None:
        """Require UTF-8 so fixed encoded-length checks have one meaning."""

        row = connection.execute("PRAGMA encoding").fetchone()
        if row is None or type(row[0]) is not str or row[0] != "UTF-8":
            # SQLite fixes a database's text encoding when its schema is first
            # created.  Adopting an empty UTF-16 file would make ASCII digest
            # byte lengths differ from the v1 search integrity contract.
            raise DocumentCorruptError(
                "Vector-store database encoding is unsupported."
            )

    @staticmethod
    def _read_schema_objects(
        connection: sqlite3.Connection,
    ) -> frozenset[tuple[str, str]]:
        """Sample at most one catalog object to prove whether a file is empty."""

        row = connection.execute(
            "SELECT type, name FROM sqlite_schema LIMIT 1"
        ).fetchone()
        if row is None:
            return frozenset()
        if not isinstance(row[0], str) or not isinstance(row[1], str):
            raise DocumentCorruptError("Vector-store schema is invalid.")
        return frozenset({(cast(str, row[0]), cast(str, row[1]))})

    @staticmethod
    def _fetch_bounded_rows(
        cursor: sqlite3.Cursor,
        maximum_rows: int,
    ) -> tuple[sqlite3.Row, ...]:
        """Read at most one row beyond an exact schema allowlist ceiling."""

        rows: list[sqlite3.Row] = []
        for _ in range(maximum_rows + 1):
            row = cursor.fetchone()
            if row is None:
                break
            rows.append(row)
        return tuple(rows)

    @staticmethod
    def _read_table_names(connection: sqlite3.Connection) -> frozenset[str]:
        """Return user table names while excluding SQLite internals."""

        rows = SQLiteVectorStore._fetch_bounded_rows(
            connection.execute(
                """
                SELECT name FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                LIMIT ?
                """,
                (len(_TABLE_NAMES) + 1,),
            ),
            len(_TABLE_NAMES),
        )
        if not all(isinstance(row[0], str) for row in rows):
            raise DocumentCorruptError("Vector-store schema is invalid.")
        return frozenset(cast(str, row[0]) for row in rows)

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        """Create the fixed schema and bind its immutable model identity."""

        # ``executescript`` commits a pending transaction before running its
        # script.  Individual allowlisted statements keep schema creation and
        # identity binding inside the caller's BEGIN IMMEDIATE transaction, so
        # even a mid-DDL failure cannot leave a partially initialized store.
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        identity_bytes = _canonical_json_bytes(
            _identity_to_data(self._model_identity)
        )
        connection.execute(
            """
            INSERT INTO vector_store_metadata (
                singleton, schema_version, vector_encoding,
                embedding_space_id, dimensions, identity_json,
                identity_checksum
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                VECTOR_STORE_SCHEMA_VERSION,
                VECTOR_ENCODING,
                self._embedding_space_id,
                self._dimensions,
                identity_bytes.decode("utf-8"),
                hashlib.sha256(identity_bytes).hexdigest(),
            ),
        )
        connection.execute(
            f"PRAGMA application_id = {_SQLITE_APPLICATION_ID}"
        )
        connection.execute(
            f"PRAGMA user_version = {VECTOR_STORE_SCHEMA_VERSION}"
        )

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        """Reject any database that is not the exact allowlisted v1 schema."""

        if self._read_pragma_integer(connection, "user_version") != (
            VECTOR_STORE_SCHEMA_VERSION
        ):
            raise DocumentCorruptError(
                "Vector-store schema version is unsupported."
            )
        if self._read_pragma_integer(connection, "application_id") != (
            _SQLITE_APPLICATION_ID
        ):
            raise DocumentCorruptError(
                "Vector-store application identity is invalid."
            )
        if self._read_table_names(connection) != _TABLE_NAMES:
            raise DocumentCorruptError("Vector-store schema is invalid.")

        schema_rows = self._fetch_bounded_rows(
            connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema
                WHERE type IN ('table', 'index', 'view', 'trigger')
                LIMIT ?
                """,
                (_EXPECTED_CATALOG_OBJECT_COUNT + 1,),
            ),
            _EXPECTED_CATALOG_OBJECT_COUNT,
        )
        if len(schema_rows) != _EXPECTED_CATALOG_OBJECT_COUNT:
            raise DocumentCorruptError("Vector-store schema is invalid.")
        seen_tables: set[str] = set()
        for row in schema_rows:
            object_type = row["type"]
            name = row["name"]
            table_name = row["tbl_name"]
            definition = row["sql"]
            if object_type == "table":
                if (
                    not isinstance(name, str)
                    or name != table_name
                    or name not in _EXPECTED_TABLE_SQL
                    or not isinstance(definition, str)
                    or self._normalize_schema_sql(definition)
                    != self._normalize_schema_sql(_EXPECTED_TABLE_SQL[name])
                ):
                    raise DocumentCorruptError(
                        "Vector-store schema is invalid."
                    )
                seen_tables.add(name)
            elif object_type == "index":
                # SQLite owns only NULL-SQL ``sqlite_autoindex_*`` indexes.
                # Any user-created index, view, or trigger expands the trusted
                # schema surface and is rejected even when queries ignore it.
                if (
                    not isinstance(name, str)
                    or not name.startswith("sqlite_autoindex_")
                    or table_name not in _TABLE_NAMES
                    or definition is not None
                ):
                    raise DocumentCorruptError(
                        "Vector-store schema is invalid."
                    )
            else:
                raise DocumentCorruptError("Vector-store schema is invalid.")
        if seen_tables != _TABLE_NAMES:
            raise DocumentCorruptError("Vector-store schema is invalid.")

        for table_name, expected_shape in _EXPECTED_COLUMN_SHAPES.items():
            rows = self._fetch_bounded_rows(
                connection.execute(f"PRAGMA table_info({table_name})"),
                len(expected_shape),
            )
            shape = tuple(tuple(row) for row in rows)
            if shape != expected_shape:
                raise DocumentCorruptError("Vector-store schema is invalid.")

        for table_name, expected_shapes in _EXPECTED_INDEX_SHAPES.items():
            index_rows = self._fetch_bounded_rows(
                connection.execute(f"PRAGMA index_list({table_name})"),
                len(expected_shapes),
            )
            shapes: list[tuple[object, object, object, tuple[object, ...]]] = []
            for index_row in index_rows:
                index_name = index_row[1]
                if not isinstance(index_name, str):
                    raise DocumentCorruptError(
                        "Vector-store schema is invalid."
                    )
                # This table-valued PRAGMA accepts a bound name, avoiding SQL
                # construction from an untrusted catalog identifier.
                index_columns = self._fetch_bounded_rows(
                    connection.execute(
                        "SELECT seqno, cid, name FROM pragma_index_info(?) "
                        "LIMIT ?",
                        (index_name, _MAX_EXPECTED_INDEX_COLUMNS + 1),
                    ),
                    _MAX_EXPECTED_INDEX_COLUMNS,
                )
                columns = tuple(column_row[2] for column_row in index_columns)
                if not all(isinstance(column, str) for column in columns):
                    raise DocumentCorruptError(
                        "Vector-store schema is invalid."
                    )
                shapes.append(
                    (index_row[2], index_row[3], index_row[4], columns)
                )
            if (
                len(shapes) != len(expected_shapes)
                or frozenset(shapes) != expected_shapes
            ):
                raise DocumentCorruptError("Vector-store schema is invalid.")

        metadata_foreign_keys = self._fetch_bounded_rows(
            connection.execute(
                "PRAGMA foreign_key_list(vector_store_metadata)"
            ),
            0,
        )
        document_foreign_keys = self._fetch_bounded_rows(
            connection.execute("PRAGMA foreign_key_list(vector_documents)"),
            0,
        )
        record_foreign_keys = self._fetch_bounded_rows(
            connection.execute("PRAGMA foreign_key_list(vector_records)"),
            len(_EXPECTED_RECORD_FOREIGN_KEYS),
        )
        if (
            metadata_foreign_keys
            or document_foreign_keys
            or tuple(tuple(row) for row in record_foreign_keys)
            != _EXPECTED_RECORD_FOREIGN_KEYS
        ):
            raise DocumentCorruptError("Vector-store schema is invalid.")
        # ``quick_check`` verifies B-tree structure but deliberately omits
        # referential integrity.  A database edited while foreign keys were
        # disabled must not expose orphaned vectors as a valid store.
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DocumentCorruptError(
                "Vector-store database integrity check failed."
            )
        check = connection.execute("PRAGMA quick_check(1)").fetchone()
        if check is None or check[0] != "ok":
            raise DocumentCorruptError(
                "Vector-store database integrity check failed."
            )

    @staticmethod
    def _normalize_schema_sql(value: str) -> str:
        """Normalize harmless formatting before exact allowlist comparison."""

        return " ".join(value.rstrip("; \t\r\n").split())

    def _validate_database_identity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Decode and compare complete persisted model identity metadata."""

        rows = self._fetch_bounded_rows(
            connection.execute(
                """
                SELECT schema_version, vector_encoding, embedding_space_id,
                       dimensions, identity_json, identity_checksum
                FROM vector_store_metadata
                LIMIT 2
                """
            ),
            1,
        )
        if len(rows) != 1:
            raise DocumentCorruptError(
                "Vector-store model metadata is invalid."
            )
        row = rows[0]
        if (
            row["schema_version"] != VECTOR_STORE_SCHEMA_VERSION
            or row["vector_encoding"] != VECTOR_ENCODING
            or type(row["dimensions"]) is not int
        ):
            raise DocumentCorruptError(
                "Vector-store model metadata is invalid."
            )
        identity_json = row["identity_json"]
        checksum = row["identity_checksum"]
        if not isinstance(identity_json, str) or not isinstance(checksum, str):
            raise DocumentCorruptError(
                "Vector-store model metadata is invalid."
            )
        identity_bytes = identity_json.encode("utf-8")
        if (
            _DIGEST_PATTERN.fullmatch(checksum) is None
            or hashlib.sha256(identity_bytes).hexdigest() != checksum
        ):
            raise DocumentCorruptError(
                "Vector-store model identity checksum does not match."
            )
        identity_data = _decode_json_object(
            identity_json,
            maximum_bytes=self._limits.max_lineage_json_bytes,
            field_name="embedding identity",
        )
        stored_identity = _parse_identity(identity_data)
        if (
            row["embedding_space_id"] != stored_identity.embedding_space_id
            or row["dimensions"] != stored_identity.dimension
        ):
            raise DocumentCorruptError(
                "Vector-store model metadata is inconsistent."
            )
        if stored_identity != self._model_identity:
            raise VectorStoreModelMismatchError(
                "Vector store uses a different embedding model identity."
            )

    @staticmethod
    def _delete_document_rows(
        connection: sqlite3.Connection,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Delete one document row and rely on the verified cascade."""

        cursor = connection.execute(
            """
            DELETE FROM vector_documents
            WHERE scope_kind = ? AND scope_id = ? AND link_id = ?
            """,
            (scope.kind, scope.id, link_id),
        )
        return cursor.rowcount != 0

    @staticmethod
    def _insert_prepared_document(
        connection: sqlite3.Connection,
        document: _PreparedDocument,
    ) -> None:
        """Insert one prevalidated generation inside the caller transaction."""

        connection.execute(
            """
            INSERT INTO vector_documents (
                scope_kind, scope_id, link_id, derivation_fingerprint,
                embedding_space_id, lineage_json, lineage_checksum,
                record_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.scope.kind,
                document.scope.id,
                document.link_id,
                document.derivation_fingerprint,
                document.embedding_space_id,
                document.lineage_json,
                document.lineage_checksum,
                len(document.records),
            ),
        )
        connection.executemany(
            """
            INSERT INTO vector_records (
                scope_kind, scope_id, link_id, ordinal, chunk_id,
                embedding_id, chunk_json, chunk_checksum,
                vector_blob, vector_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    document.scope.kind,
                    document.scope.id,
                    document.link_id,
                    record.ordinal,
                    record.chunk_id,
                    record.embedding_id,
                    record.chunk_json,
                    record.chunk_checksum,
                    record.vector_blob,
                    record.vector_checksum,
                )
                for record in document.records
            ),
        )

    def _load_expected_document_metadata(
        self,
        connection: sqlite3.Connection,
        scope: AttachmentScope,
        link_id: str,
        expected_derivation_fingerprint: str,
        expected_embedding_space_id: str,
    ) -> _StoredDocumentMetadata | None:
        """Load one document header and reject stale or corrupt generations."""

        row = connection.execute(
            """
            SELECT derivation_fingerprint, embedding_space_id,
                   lineage_json, lineage_checksum, record_count
            FROM vector_documents
            WHERE scope_kind = ? AND scope_id = ? AND link_id = ?
            """,
            (scope.kind, scope.id, link_id),
        ).fetchone()
        if row is None:
            return None
        metadata = self._decode_expected_document_metadata_row(
            row,
            scope,
            link_id,
            expected_derivation_fingerprint,
            expected_embedding_space_id,
        )
        ordinal_rows = connection.execute(
            """
            SELECT ordinal
            FROM vector_records
            WHERE scope_kind = ? AND scope_id = ? AND link_id = ?
            ORDER BY ordinal
            LIMIT ?
            """,
            (
                scope.kind,
                scope.id,
                link_id,
                self._limits.max_records_per_document + 1,
            ),
        ).fetchall()
        ordinals = tuple(row["ordinal"] for row in ordinal_rows)
        if ordinals != tuple(range(metadata.record_count)):
            raise DocumentCorruptError(
                "Stored vector generation is incomplete."
            )
        return metadata

    def _decode_expected_document_metadata_row(
        self,
        row: sqlite3.Row,
        scope: AttachmentScope,
        link_id: str,
        expected_derivation_fingerprint: str,
        expected_embedding_space_id: str,
    ) -> _StoredDocumentMetadata:
        """Validate one selected header without issuing another SQL query.

        The split lets batch retrieval validate all headers and all records in
        two streaming queries under one SQLite snapshot.  Existing point reads
        call this same decoder and then retain their explicit ordinal check.
        """

        derivation = row["derivation_fingerprint"]
        embedding_space_id = row["embedding_space_id"]
        if (
            not isinstance(derivation, str)
            or _DIGEST_PATTERN.fullmatch(derivation) is None
            or not isinstance(embedding_space_id, str)
            or _DIGEST_PATTERN.fullmatch(embedding_space_id) is None
        ):
            raise DocumentCorruptError(
                "Stored vector generation identity is invalid."
            )
        if embedding_space_id != self._embedding_space_id:
            raise DocumentCorruptError(
                "Stored vector generation uses an inconsistent embedding space."
            )
        if (
            derivation != expected_derivation_fingerprint
            or embedding_space_id != expected_embedding_space_id
        ):
            raise VectorStoreStaleError(
                "Stored vectors do not match the requested derivation."
            )
        lineage_json = row["lineage_json"]
        lineage_checksum = row["lineage_checksum"]
        if not isinstance(lineage_json, str) or not isinstance(
            lineage_checksum,
            str,
        ):
            raise DocumentCorruptError(
                "Stored vector lineage metadata is invalid."
            )
        lineage_bytes = lineage_json.encode("utf-8")
        if (
            len(lineage_bytes) > self._limits.max_lineage_json_bytes
            or _DIGEST_PATTERN.fullmatch(lineage_checksum) is None
            or hashlib.sha256(lineage_bytes).hexdigest() != lineage_checksum
        ):
            raise DocumentCorruptError(
                "Stored vector lineage checksum does not match."
            )
        lineage_data = _decode_json_object(
            lineage_json,
            maximum_bytes=self._limits.max_lineage_json_bytes,
            field_name="vector lineage",
        )
        (
            header,
            policy,
            lineage_record_count,
            total_chunk_code_points,
            total_source_mappings,
        ) = _parse_embedded_lineage(lineage_data)
        try:
            header_document = ChunkedDocument(
                **cast(Any, header),
                chunks=(),
            )
        except (DocumentError, TypeError, ValueError) as error:
            raise DocumentCorruptError(
                "Stored vector lineage is invalid."
            ) from error
        source = header_document.provenance.source
        if (
            source.scope != scope
            or source.link_id != link_id
            or header_document.derivation_fingerprint != derivation
        ):
            raise DocumentCorruptError(
                "Stored vector lineage changed its owner or derivation."
            )
        record_count = row["record_count"]
        if (
            type(record_count) is not int
            or record_count < 0
            or record_count > self._limits.max_records_per_document
            or record_count > header_document.limits.max_chunks
            or record_count != lineage_record_count
        ):
            raise DocumentCorruptError(
                "Stored vector record count is invalid."
            )
        if (
            total_chunk_code_points
            > header_document.limits.max_total_chunk_code_points
            or total_source_mappings
            > header_document.limits.max_total_source_mappings
            or total_chunk_code_points < record_count
            or total_source_mappings < record_count
            or (
                record_count == 0
                and (
                    total_chunk_code_points != 0
                    or total_source_mappings != 0
                )
            )
        ):
            raise DocumentCorruptError(
                "Stored vector lineage totals are invalid."
            )
        return _StoredDocumentMetadata(
            chunked_document=header_document,
            policy=policy,
            derivation_fingerprint=derivation,
            embedding_space_id=embedding_space_id,
            record_count=record_count,
            total_chunk_code_points=total_chunk_code_points,
            total_source_mappings=total_source_mappings,
            lineage_json_bytes=len(lineage_bytes),
        )

    def _decode_record(
        self,
        row: sqlite3.Row,
        metadata: _StoredDocumentMetadata,
        *,
        maximum_mappings: int | None = None,
    ) -> EmbeddedChunk:
        """Validate one row before exceeding an optional mapping allowance."""

        ordinal = row["ordinal"]
        chunk_id = row["chunk_id"]
        embedding_id = row["embedding_id"]
        if (
            type(ordinal) is not int
            or ordinal < 0
            or ordinal >= metadata.record_count
            or not isinstance(chunk_id, str)
            or _CHUNK_ID_PATTERN.fullmatch(chunk_id) is None
            or not isinstance(embedding_id, str)
        ):
            raise DocumentCorruptError("Stored vector record identity is invalid.")
        chunk_json = row["chunk_json"]
        chunk_checksum = row["chunk_checksum"]
        if not isinstance(chunk_json, str) or not isinstance(
            chunk_checksum,
            str,
        ):
            raise DocumentCorruptError("Stored chunk metadata is invalid.")
        chunk_bytes = chunk_json.encode("utf-8")
        if (
            len(chunk_bytes) > self._limits.max_chunk_json_bytes
            or _DIGEST_PATTERN.fullmatch(chunk_checksum) is None
            or hashlib.sha256(chunk_bytes).hexdigest() != chunk_checksum
        ):
            raise DocumentCorruptError(
                "Stored chunk metadata checksum does not match."
            )
        chunk_data = _decode_json_object(
            chunk_json,
            maximum_bytes=self._limits.max_chunk_json_bytes,
            field_name="chunk",
        )
        header = metadata.chunked_document
        mapping_limit = header.limits.max_source_mappings_per_chunk
        if maximum_mappings is not None:
            if type(maximum_mappings) is not int or maximum_mappings < 0:
                raise DocumentContentLimitError(
                    "Vector record mapping allowance is invalid."
                )
            mapping_limit = min(mapping_limit, maximum_mappings)
        chunk = _parse_chunk(
            chunk_data,
            maximum_mappings=mapping_limit,
            maximum_text_code_points=header.policy.max_chunk_code_points,
        )
        document_format = header.provenance.document_format
        if document_format == "pdf":
            if (
                chunk.page_number is None
                or header.page_count is None
                or chunk.page_number > header.page_count
            ):
                raise DocumentCorruptError(
                    "Stored chunk page lineage is invalid."
                )
        elif chunk.page_number is not None:
            raise DocumentCorruptError(
                "Stored chunk page lineage is invalid."
            )
        if (
            chunk.ordinal != ordinal
            or chunk.chunk_id != chunk_id
            or chunk.chunk_id
            != _expected_chunk_id(chunk, metadata.derivation_fingerprint)
        ):
            raise DocumentCorruptError(
                "Stored chunk identity does not match its lineage."
            )
        vector = _decode_vector(
            row["vector_blob"],
            row["vector_checksum"],
            dimensions=self._dimensions,
        )
        try:
            return EmbeddedChunk(
                embedding_id=embedding_id,
                chunk_id=chunk.chunk_id,
                ordinal=chunk.ordinal,
                kind=chunk.kind,
                text=chunk.text,
                page_number=chunk.page_number,
                source_mappings=chunk.source_mappings,
                derivation_fingerprint=metadata.derivation_fingerprint,
                embedding_space_id=metadata.embedding_space_id,
                vector=vector,
                vector_checksum=cast(str, row["vector_checksum"]),
            )
        except (EmbeddingError, TypeError, ValueError) as error:
            raise DocumentCorruptError(
                "Stored embedded chunk is invalid."
            ) from error

    def _stored_record_sizes(self, row: sqlite3.Row) -> tuple[int, int]:
        """Measure one row before decoding or fetching another page item."""

        chunk_json = row["chunk_json"]
        vector_blob = row["vector_blob"]
        if not isinstance(chunk_json, str) or not isinstance(vector_blob, bytes):
            raise DocumentCorruptError(
                "Stored vector record bytes are invalid."
            )
        try:
            chunk_size = len(chunk_json.encode("utf-8"))
        except (UnicodeError, MemoryError) as error:
            raise DocumentCorruptError(
                "Stored chunk metadata is invalid."
            ) from error
        if chunk_size > self._limits.max_chunk_json_bytes:
            raise DocumentCorruptError(
                "Stored chunk metadata exceeds its limit."
            )
        return chunk_size, len(vector_blob)
