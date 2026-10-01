"""Define bounded, versioned local-embedding values and orchestration.

The document cleaning and chunking pipeline remains pure and model-free.  This
module adds the next explicit boundary: a model-independent embedding protocol
and a service that validates an injected adapter as untrusted code before
publishing scope-aware vectors.  It performs no persistence or network I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
import unicodedata
from typing import Final, Literal, NoReturn, Protocol, TypeAlias, cast

from attachments.domain import MAX_JSON_SAFE_INTEGER, AttachmentScope

from .chunking import (
    CHUNKED_DOCUMENT_SCHEMA_VERSION,
    ChunkSourceMapping,
    DocumentChunk,
    DocumentChunkKind,
    DocumentChunkingPolicy,
    ChunkedDocument,
)
from .cleaning import (
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
    LoadedDocumentProvenance,
)
from .domain import DocumentLoadLimits, DocumentSource, DocumentTitle
from .exceptions import DocumentOperationCancelledError


EMBEDDING_SCHEMA_VERSION: Final[Literal[1]] = 1
EMBEDDED_DOCUMENT_SCHEMA_VERSION: Final[Literal[1]] = 1
EMBEDDED_QUERY_SCHEMA_VERSION: Final[Literal[1]] = 1
DEFAULT_EMBEDDING_PROVIDER: Final = "ollama"
DEFAULT_EMBEDDING_ADAPTER_ID: Final = "ollama-http"
DEFAULT_EMBEDDING_ADAPTER_VERSION: Final = "1.0.0"
DEFAULT_EMBEDDING_MODEL_TAG: Final = "qwen3-embedding:0.6b"
DEFAULT_EMBEDDING_MODEL_DIGEST: Final = (
    "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d"
)
DEFAULT_EMBEDDING_DIMENSION: Final = 1_024
DEFAULT_EMBEDDING_NORMALIZATION: Final = "l2"
DOCUMENT_EMBEDDING_TEMPLATE_VERSION: Final = "raw-chunk-v1"
QUERY_EMBEDDING_TEMPLATE_VERSION: Final = "elysia-document-retrieval-v1"
DOCUMENT_EMBEDDING_INPUT_MODE: Final = "exact-chunk-text-v1"
QUERY_EMBEDDING_PREFIX: Final = (
    "Instruct: Given a user question, retrieve relevant passages from the "
    "user's local documents that answer it.\nQuery:"
)
DEFAULT_EMBEDDING_MAX_BATCH_ITEMS: Final = 16
DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS: Final = 2_000
DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS: Final = 32_000
MAX_EMBEDDING_DIMENSION: Final = 4_096
EMBEDDING_UNIT_NORM_TOLERANCE: Final = 1e-4

EmbeddingPurpose: TypeAlias = Literal["document", "query"]
EmbeddingNormalization: TypeAlias = Literal["l2"]

_PURPOSES: Final = ("document", "query")
_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_MODEL_TAG_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_ITEM_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_ID_PATTERN: Final = re.compile(r"^chunk_[0-9a-f]{64}$")
_EMBEDDING_ID_PATTERN: Final = re.compile(r"^embedding_[0-9a-f]{64}$")
_SPACE_FINGERPRINT_DOMAIN: Final = "elysia.embedding-space.v1"
_EMBEDDING_ID_DOMAIN: Final = "elysia.chunk-embedding.v1"


def _raise_if_embedding_cancelled(
    cancel_requested: Callable[[], bool] | None,
) -> None:
    """Stop between bounded model batches before publishing partial vectors."""

    if cancel_requested is not None and cancel_requested():
        raise DocumentOperationCancelledError(
            "Document embedding was cancelled before commit."
        )


class EmbeddingError(Exception):
    """Base class for stable failures exposed by the embedding boundary."""


class EmbeddingValidationError(EmbeddingError):
    """Report an invalid request, domain value, or adapter result."""


class EmbeddingLimitError(EmbeddingError):
    """Report work that exceeds an explicit embedding resource ceiling."""


class EmbeddingUnavailableError(EmbeddingError):
    """Report that the configured local embedding engine cannot run safely."""


class EmbeddingFailedError(EmbeddingError):
    """Report failure while an available engine processes a valid request."""


def _raise_sanitized_adapter_error(error: EmbeddingError) -> NoReturn:
    """Preserve an adapter failure category without trusting its message.

    Injected adapters are allowed to select a stable public error category, but
    their exception text may still contain document content, response bodies,
    or local paths.  Mapping only at the property/call seam keeps service-owned
    validation diagnostics useful while preventing adapter-authored text from
    crossing the public boundary.
    """

    if isinstance(error, EmbeddingValidationError):
        raise EmbeddingValidationError(
            "Embedding adapter reported an invalid operation."
        ) from None
    if isinstance(error, EmbeddingLimitError):
        raise EmbeddingLimitError(
            "Embedding adapter exceeded a resource limit."
        ) from None
    if isinstance(error, EmbeddingUnavailableError):
        raise EmbeddingUnavailableError(
            "Embedding adapter is unavailable."
        ) from None
    if isinstance(error, EmbeddingFailedError):
        raise EmbeddingFailedError(
            "Embedding adapter failed to produce a safe result."
        ) from None
    raise EmbeddingFailedError(
        "Embedding adapter failed without a recognized error category."
    ) from None


def _require_exact_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Return one bounded integer while rejecting booleans and floats."""

    if type(value) is not int or not minimum <= value <= maximum:
        raise EmbeddingValidationError(
            f"{field_name} must be an integer from {minimum} through {maximum}."
        )
    return value


def _validate_identifier(value: object, field_name: str) -> str:
    """Return one stable lowercase producer identifier."""

    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise EmbeddingValidationError(
            f"{field_name} must be a stable lowercase identifier."
        )
    return value


def _validate_version(value: object, field_name: str) -> str:
    """Return one bounded version string suitable for canonical identity."""

    if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
        raise EmbeddingValidationError(
            f"{field_name} must be a stable non-empty version."
        )
    return value


def _validate_digest(value: object, field_name: str) -> str:
    """Return one full lowercase SHA-256 digest."""

    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise EmbeddingValidationError(
            f"{field_name} must be a lowercase SHA-256 digest."
        )
    return value


def _validate_item_id(value: object) -> str:
    """Return one bounded opaque item identifier."""

    if not isinstance(value, str) or _ITEM_ID_PATTERN.fullmatch(value) is None:
        raise EmbeddingValidationError(
            "Embedding item_id must be a stable lowercase identifier."
        )
    return value


def _validate_embedding_text(value: object) -> str:
    """Validate exact Unicode without trimming or normalizing source text."""

    if not isinstance(value, str) or not value:
        raise EmbeddingValidationError(
            "Embedding input text must be a non-empty string."
        )
    if len(value) > DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS:
        raise EmbeddingLimitError(
            "Embedding input exceeds the code-point limit."
        )
    for character in value:
        category = unicodedata.category(character)
        if category == "Cs" or (
            category == "Cc" and character not in {"\t", "\n", "\r"}
        ):
            raise EmbeddingValidationError(
                "Embedding input contains an unsafe control character."
            )
    return value


def _canonical_json_digest(value: object) -> str:
    """Hash an explicitly shaped value as canonical UTF-8 JSON."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_vector_bytes(values: tuple[float, ...]) -> bytes:
    """Encode vectors as portable little-endian IEEE-754 float32 bytes."""

    try:
        return struct.pack(f"<{len(values)}f", *values)
    except (OverflowError, struct.error) as error:
        raise EmbeddingValidationError(
            "Embedding vector cannot be represented as float32."
        ) from error


def _vector_checksum(values: tuple[float, ...]) -> str:
    """Return the integrity digest of canonical float32-le vector bytes."""

    return hashlib.sha256(_canonical_vector_bytes(values)).hexdigest()


def _unit_vector(values: tuple[float, ...], dimension: int) -> tuple[float, ...]:
    """Validate and canonicalize one finite non-zero L2 unit vector.

    The Ollama API promises normalized vectors.  We therefore verify its
    contract instead of silently changing model output.  Float32 conversion is
    explicit because the checksum and future store encoding use that portable
    representation rather than Python's platform-native object layout.
    """

    if len(values) != dimension:
        raise EmbeddingValidationError(
            "Embedding vector dimension does not match its model identity."
        )
    if not values or not all(
        isinstance(value, float) and math.isfinite(value) for value in values
    ):
        raise EmbeddingValidationError(
            "Embedding vector must contain only finite floats."
        )
    norm = math.hypot(*values)
    if norm == 0.0:
        raise EmbeddingValidationError("Embedding vector must not be zero.")
    if not math.isclose(
        norm,
        1.0,
        rel_tol=0.0,
        abs_tol=EMBEDDING_UNIT_NORM_TOLERANCE,
    ):
        raise EmbeddingValidationError(
            "Embedding vector must be L2-normalized."
        )
    packed = _canonical_vector_bytes(values)
    canonical = cast(
        tuple[float, ...],
        struct.unpack(f"<{dimension}f", packed),
    )
    return canonical


@dataclass(frozen=True, slots=True)
class EmbeddingModelIdentity:
    """Identify one immutable vector space and every semantic input rule."""

    provider: str
    adapter_id: str
    adapter_version: str
    model_tag: str
    model_digest: str
    dimension: int
    normalization: EmbeddingNormalization
    document_template_version: str
    query_template_version: str
    embedding_space_id: str = ""

    def __post_init__(self) -> None:
        """Validate fields and derive the canonical vector-space fingerprint."""

        _validate_identifier(self.provider, "provider")
        _validate_identifier(self.adapter_id, "adapter_id")
        _validate_version(self.adapter_version, "adapter_version")
        if (
            not isinstance(self.model_tag, str)
            or _MODEL_TAG_PATTERN.fullmatch(self.model_tag) is None
        ):
            raise EmbeddingValidationError(
                "model_tag must be one bounded explicit model tag."
            )
        _validate_digest(self.model_digest, "model_digest")
        _require_exact_integer(
            self.dimension,
            "dimension",
            minimum=1,
            maximum=MAX_EMBEDDING_DIMENSION,
        )
        if self.normalization != DEFAULT_EMBEDDING_NORMALIZATION:
            raise EmbeddingValidationError(
                "Embedding normalization must be l2."
            )
        _validate_version(
            self.document_template_version,
            "document_template_version",
        )
        _validate_version(self.query_template_version, "query_template_version")
        expected = _canonical_json_digest(
            {
                "fingerprint_domain": _SPACE_FINGERPRINT_DOMAIN,
                "schema_version": EMBEDDING_SCHEMA_VERSION,
                "provider": self.provider,
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "model_tag": self.model_tag,
                "model_digest": self.model_digest,
                "dimension": self.dimension,
                "normalization": self.normalization,
                "document_template_version": self.document_template_version,
                "query_template_version": self.query_template_version,
                # Batch and template mechanics change vector semantics even if
                # a maintainer forgets to bump their human-readable versions.
                # Binding the actual fixed values prevents incompatible
                # generations from sharing one persistent vector space.
                "batch_policy": {
                    "max_batch_items": DEFAULT_EMBEDDING_MAX_BATCH_ITEMS,
                    "max_input_code_points": (
                        DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS
                    ),
                    "max_batch_code_points": (
                        DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS
                    ),
                    "truncate": False,
                },
                "document_input_mode": DOCUMENT_EMBEDDING_INPUT_MODE,
                "query_template_prefix": QUERY_EMBEDDING_PREFIX,
            }
        )
        supplied = self.embedding_space_id
        if supplied and supplied != expected:
            raise EmbeddingValidationError(
                "embedding_space_id does not match the model identity."
            )
        object.__setattr__(self, "embedding_space_id", expected)


@dataclass(frozen=True, slots=True)
class EmbeddingBatchPolicy:
    """Fix the v1 item, text, and truncation budgets for every model call."""

    max_batch_items: Literal[16] = DEFAULT_EMBEDDING_MAX_BATCH_ITEMS
    max_input_code_points: Literal[2000] = (
        DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS
    )
    max_batch_code_points: Literal[32000] = (
        DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS
    )
    truncate: Literal[False] = False

    def __post_init__(self) -> None:
        """Reject policy drift that could silently change stored vectors."""

        if (
            type(self.max_batch_items) is not int
            or self.max_batch_items != DEFAULT_EMBEDDING_MAX_BATCH_ITEMS
            or type(self.max_input_code_points) is not int
            or self.max_input_code_points
            != DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS
            or type(self.max_batch_code_points) is not int
            or self.max_batch_code_points
            != DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS
            or type(self.truncate) is not bool
            or self.truncate
        ):
            raise EmbeddingValidationError(
                "Embedding batch policy must match the fixed v1 contract."
            )


@dataclass(frozen=True, slots=True)
class EmbeddingInput:
    """Carry one opaque ID and exact, bounded Unicode model input."""

    item_id: str
    text: str

    def __post_init__(self) -> None:
        """Reject unsafe IDs and text without changing source spelling."""

        _validate_item_id(self.item_id)
        _validate_embedding_text(self.text)


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """Describe one ordered document or query batch under the fixed policy."""

    purpose: EmbeddingPurpose
    items: tuple[EmbeddingInput, ...]

    def __post_init__(self) -> None:
        """Enforce closed purpose, unique IDs, and aggregate batch ceilings."""

        if not isinstance(self.purpose, str) or self.purpose not in _PURPOSES:
            raise EmbeddingValidationError(
                "Embedding purpose must be document or query."
            )
        if (
            not isinstance(self.items, tuple)
            or not self.items
            or not all(isinstance(item, EmbeddingInput) for item in self.items)
        ):
            raise EmbeddingValidationError(
                "Embedding request must contain validated inputs."
            )
        if len(self.items) > DEFAULT_EMBEDDING_MAX_BATCH_ITEMS:
            raise EmbeddingLimitError(
                "Embedding request exceeds the batch item limit."
            )
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise EmbeddingValidationError(
                "Embedding request item IDs must be unique."
            )
        if sum(len(item.text) for item in self.items) > (
            DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS
        ):
            raise EmbeddingLimitError(
                "Embedding request exceeds the batch code-point limit."
            )
        if self.purpose == "query" and len(self.items) != 1:
            raise EmbeddingValidationError(
                "A query embedding request must contain exactly one input."
            )


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """Associate one opaque item ID with finite vector coordinates."""

    item_id: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        """Reject malformed and unbounded coordinates before orchestration."""

        _validate_item_id(self.item_id)
        if (
            not isinstance(self.values, tuple)
            or not self.values
            or len(self.values) > MAX_EMBEDDING_DIMENSION
            or not all(
                isinstance(value, float) and math.isfinite(value)
                for value in self.values
            )
        ):
            raise EmbeddingValidationError(
                "Embedding vector must contain bounded finite floats."
            )


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Publish one ordered adapter result with its exact vector-space identity."""

    schema_version: Literal[1]
    identity: EmbeddingModelIdentity
    policy: EmbeddingBatchPolicy
    purpose: EmbeddingPurpose
    vectors: tuple[EmbeddingVector, ...]

    def __post_init__(self) -> None:
        """Validate result metadata and the fixed outer batch shape."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != EMBEDDING_SCHEMA_VERSION
        ):
            raise EmbeddingValidationError(
                "Unsupported embedding batch schema version."
            )
        if not isinstance(self.identity, EmbeddingModelIdentity):
            raise EmbeddingValidationError(
                "Embedding batch identity is invalid."
            )
        if not isinstance(self.policy, EmbeddingBatchPolicy):
            raise EmbeddingValidationError("Embedding batch policy is invalid.")
        if not isinstance(self.purpose, str) or self.purpose not in _PURPOSES:
            raise EmbeddingValidationError(
                "Embedding batch purpose is invalid."
            )
        if (
            not isinstance(self.vectors, tuple)
            or not self.vectors
            or len(self.vectors) > self.policy.max_batch_items
            or not all(
                isinstance(vector, EmbeddingVector) for vector in self.vectors
            )
        ):
            raise EmbeddingValidationError(
                "Embedding batch must contain bounded vectors."
            )
        item_ids = tuple(vector.item_id for vector in self.vectors)
        if len(set(item_ids)) != len(item_ids):
            raise EmbeddingValidationError(
                "Embedding batch item IDs must be unique."
            )
        if self.purpose == "query" and len(self.vectors) != 1:
            raise EmbeddingValidationError(
                "A query embedding batch must contain exactly one vector."
            )


class TextEmbedder(Protocol):
    """Embed exact prepared text without persistence, downloads, or mutation."""

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the immutable vector-space identity used by this adapter."""

        ...

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the exact resource and truncation policy used by the adapter."""

        ...

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Embed request text verbatim or raise a typed EmbeddingError."""

        ...


def _build_embedding_id(
    chunk_id: str,
    derivation_fingerprint: str,
    embedding_space_id: str,
) -> str:
    """Derive stable chunk identity without depending on float bytes."""

    digest = _canonical_json_digest(
        {
            "embedding_id_domain": _EMBEDDING_ID_DOMAIN,
            "chunk_id": chunk_id,
            "derivation_fingerprint": derivation_fingerprint,
            "embedding_space_id": embedding_space_id,
        }
    )
    return f"embedding_{digest}"


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """Preserve one chunk, its provenance mappings, and a canonical vector."""

    embedding_id: str
    chunk_id: str
    ordinal: int
    kind: DocumentChunkKind
    text: str
    page_number: int | None
    source_mappings: tuple[ChunkSourceMapping, ...]
    derivation_fingerprint: str
    embedding_space_id: str
    vector: tuple[float, ...]
    vector_checksum: str

    def __post_init__(self) -> None:
        """Validate self-contained lineage, mappings, vector, and checksum."""

        if (
            not isinstance(self.embedding_id, str)
            or _EMBEDDING_ID_PATTERN.fullmatch(self.embedding_id) is None
        ):
            raise EmbeddingValidationError("embedding_id is invalid.")
        if (
            not isinstance(self.chunk_id, str)
            or _CHUNK_ID_PATTERN.fullmatch(self.chunk_id) is None
        ):
            raise EmbeddingValidationError("chunk_id is invalid.")
        _require_exact_integer(
            self.ordinal,
            "ordinal",
            minimum=0,
            maximum=MAX_JSON_SAFE_INTEGER,
        )
        if self.kind not in ("prose", "code", "table"):
            raise EmbeddingValidationError("Embedded chunk kind is invalid.")
        _validate_embedding_text(self.text)
        if self.page_number is not None:
            _require_exact_integer(
                self.page_number,
                "page_number",
                minimum=1,
                maximum=MAX_JSON_SAFE_INTEGER,
            )
        if (
            not isinstance(self.source_mappings, tuple)
            or not self.source_mappings
            or not all(
                isinstance(mapping, ChunkSourceMapping)
                for mapping in self.source_mappings
            )
        ):
            raise EmbeddingValidationError(
                "Embedded chunk source mappings are invalid."
            )
        _validate_digest(self.derivation_fingerprint, "derivation_fingerprint")
        _validate_digest(self.embedding_space_id, "embedding_space_id")
        if (
            not isinstance(self.vector, tuple)
            or not self.vector
            or len(self.vector) > MAX_EMBEDDING_DIMENSION
            or not all(
                isinstance(value, float) and math.isfinite(value)
                for value in self.vector
            )
        ):
            raise EmbeddingValidationError("Embedded chunk vector is invalid.")
        _validate_digest(self.vector_checksum, "vector_checksum")
        if self.vector_checksum != _vector_checksum(self.vector):
            raise EmbeddingValidationError(
                "vector_checksum does not match canonical float32 bytes."
            )
        expected_id = _build_embedding_id(
            self.chunk_id,
            self.derivation_fingerprint,
            self.embedding_space_id,
        )
        if self.embedding_id != expected_id:
            raise EmbeddingValidationError(
                "embedding_id does not match chunk lineage."
            )


@dataclass(frozen=True, slots=True)
class EmbeddedDocument:
    """Publish embedded chunks with their complete reconstructable lineage."""

    schema_version: Literal[1]
    chunked_schema_version: Literal[1]
    source: DocumentSource
    derivation_fingerprint: str
    identity: EmbeddingModelIdentity
    policy: EmbeddingBatchPolicy
    chunked_document: ChunkedDocument
    chunks: tuple[EmbeddedChunk, ...]

    def __post_init__(self) -> None:
        """Validate owner lineage and consistency of every embedded chunk."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != EMBEDDED_DOCUMENT_SCHEMA_VERSION
        ):
            raise EmbeddingValidationError(
                "Unsupported embedded-document schema version."
            )
        if (
            type(self.chunked_schema_version) is not int
            or self.chunked_schema_version != CHUNKED_DOCUMENT_SCHEMA_VERSION
        ):
            raise EmbeddingValidationError(
                "Unsupported source chunked-document schema version."
            )
        if not isinstance(self.source, DocumentSource):
            raise EmbeddingValidationError("Embedded document source is invalid.")
        _validate_digest(self.derivation_fingerprint, "derivation_fingerprint")
        if not isinstance(self.identity, EmbeddingModelIdentity):
            raise EmbeddingValidationError(
                "Embedded document identity is invalid."
            )
        if not isinstance(self.policy, EmbeddingBatchPolicy):
            raise EmbeddingValidationError(
                "Embedded document policy is invalid."
            )
        if not isinstance(self.chunked_document, ChunkedDocument):
            raise EmbeddingValidationError(
                "Embedded document requires its validated chunked source."
            )
        if (
            self.chunked_schema_version != self.chunked_document.schema_version
            or self.source != self.chunked_document.provenance.source
            or self.derivation_fingerprint
            != self.chunked_document.derivation_fingerprint
        ):
            raise EmbeddingValidationError(
                "Embedded document metadata changed its chunked lineage."
            )
        if not isinstance(self.chunks, tuple) or not all(
            isinstance(chunk, EmbeddedChunk) for chunk in self.chunks
        ):
            raise EmbeddingValidationError(
                "Embedded document chunks are invalid."
            )
        if tuple(chunk.ordinal for chunk in self.chunks) != tuple(
            range(len(self.chunks))
        ):
            raise EmbeddingValidationError(
                "Embedded chunk ordinals must be contiguous from zero."
            )
        if len({chunk.embedding_id for chunk in self.chunks}) != len(self.chunks):
            raise EmbeddingValidationError(
                "Embedded chunk identities must be unique."
            )
        if len(self.chunks) != len(self.chunked_document.chunks):
            raise EmbeddingValidationError(
                "Embedded document changed its source chunk count."
            )
        for chunk, source_chunk in zip(
            self.chunks,
            self.chunked_document.chunks,
            strict=True,
        ):
            if (
                chunk.derivation_fingerprint != self.derivation_fingerprint
                or chunk.embedding_space_id != self.identity.embedding_space_id
            ):
                raise EmbeddingValidationError(
                    "Embedded chunk lineage does not match its document."
                )
            if (
                chunk.chunk_id != source_chunk.chunk_id
                or chunk.ordinal != source_chunk.ordinal
                or chunk.kind != source_chunk.kind
                or chunk.text != source_chunk.text
                or chunk.page_number != source_chunk.page_number
                or chunk.source_mappings != source_chunk.source_mappings
            ):
                raise EmbeddingValidationError(
                    "Embedded chunk changed its exact source content or mappings."
                )
            _unit_vector(chunk.vector, self.identity.dimension)


@dataclass(frozen=True, slots=True)
class EmbeddedQuery:
    """Publish one transient query vector with its exact vector-space identity.

    A bare vector can prove its dimension and norm but cannot prove which model,
    prompt template, or normalization contract produced it.  Retrieval therefore
    carries the complete identity and batch policy beside the vector so a store
    can reject a same-shaped query from an incompatible semantic space.
    """

    schema_version: Literal[1]
    identity: EmbeddingModelIdentity
    policy: EmbeddingBatchPolicy
    vector: EmbeddingVector

    def __post_init__(self) -> None:
        """Require one canonical unit query without retaining its source text."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != EMBEDDED_QUERY_SCHEMA_VERSION
        ):
            raise EmbeddingValidationError(
                "Unsupported embedded-query schema version."
            )
        if type(self.identity) is not EmbeddingModelIdentity:
            raise EmbeddingValidationError(
                "Embedded query identity is invalid."
            )
        if type(self.policy) is not EmbeddingBatchPolicy:
            raise EmbeddingValidationError("Embedded query policy is invalid.")
        if type(self.vector) is not EmbeddingVector:
            raise EmbeddingValidationError("Embedded query vector is invalid.")
        if self.vector.item_id != "query":
            raise EmbeddingValidationError(
                "Embedded query vector must use the query item identity."
            )
        canonical = _unit_vector(
            self.vector.values,
            self.identity.dimension,
        )
        if canonical != self.vector.values:
            raise EmbeddingValidationError(
                "Embedded query vector must use canonical float32 values."
            )


def _snapshot_scope(scope: AttachmentScope) -> AttachmentScope:
    """Copy one attachment scope through its public constructor."""

    return AttachmentScope(kind=scope.kind, id=scope.id)


def _snapshot_source(source: DocumentSource) -> DocumentSource:
    """Copy path-private source metadata for stable publication."""

    return DocumentSource(
        scope=_snapshot_scope(source.scope),
        link_id=source.link_id,
        file_id=source.file_id,
        file_name=source.file_name,
        media_type=source.media_type,
        size_bytes=source.size_bytes,
    )


def _snapshot_load_limits(limits: DocumentLoadLimits) -> DocumentLoadLimits:
    """Copy all loader ceilings so adapter mutation cannot alter lineage."""

    return DocumentLoadLimits(
        max_source_bytes=limits.max_source_bytes,
        max_expanded_bytes=limits.max_expanded_bytes,
        max_text_code_points=limits.max_text_code_points,
        max_blocks=limits.max_blocks,
        max_pages=limits.max_pages,
        max_package_entries=limits.max_package_entries,
        max_table_cells=limits.max_table_cells,
        max_table_columns=limits.max_table_columns,
        max_cell_code_points=limits.max_cell_code_points,
        max_xml_elements=limits.max_xml_elements,
        max_xml_depth=limits.max_xml_depth,
    )


def _snapshot_processing_limits(
    limits: DocumentProcessingLimits,
) -> DocumentProcessingLimits:
    """Copy all cleaning and chunking aggregate resource ceilings."""

    return DocumentProcessingLimits(
        max_cleaned_code_points=limits.max_cleaned_code_points,
        max_cleaned_blocks=limits.max_cleaned_blocks,
        max_cleaning_omissions=limits.max_cleaning_omissions,
        max_chunks=limits.max_chunks,
        max_total_chunk_code_points=limits.max_total_chunk_code_points,
        max_source_mappings_per_chunk=limits.max_source_mappings_per_chunk,
        max_total_source_mappings=limits.max_total_source_mappings,
    )


def _snapshot_chunk_policy(policy: DocumentChunkingPolicy) -> DocumentChunkingPolicy:
    """Copy every output-affecting chunk policy field."""

    return DocumentChunkingPolicy(
        max_chunk_code_points=policy.max_chunk_code_points,
        overlap_code_points=policy.overlap_code_points,
        prose_separator=policy.prose_separator,
        table_projection_version=policy.table_projection_version,
    )


def _snapshot_mapping(mapping: ChunkSourceMapping) -> ChunkSourceMapping:
    """Copy one mapping and its concrete source-span variant."""

    span = mapping.source_span
    copied_span: DocumentTextSpan | DocumentTableCellSpan
    if isinstance(span, DocumentTextSpan):
        copied_span = DocumentTextSpan(
            block_ordinal=span.block_ordinal,
            start_code_point=span.start_code_point,
            end_code_point=span.end_code_point,
            page_number=span.page_number,
        )
    elif isinstance(span, DocumentTableCellSpan):
        copied_span = DocumentTableCellSpan(
            block_ordinal=span.block_ordinal,
            row_index=span.row_index,
            column_index=span.column_index,
            start_code_point=span.start_code_point,
            end_code_point=span.end_code_point,
            page_number=span.page_number,
        )
    else:
        raise EmbeddingValidationError(
            "Chunk mapping contains an invalid source span."
        )
    return ChunkSourceMapping(
        chunk_start_code_point=mapping.chunk_start_code_point,
        chunk_end_code_point=mapping.chunk_end_code_point,
        source_span=copied_span,
    )


def _snapshot_chunk(chunk: DocumentChunk) -> DocumentChunk:
    """Copy one validated chunk and all nested provenance mappings."""

    return DocumentChunk(
        ordinal=chunk.ordinal,
        chunk_id=chunk.chunk_id,
        kind=chunk.kind,
        text=chunk.text,
        page_number=chunk.page_number,
        source_mappings=tuple(
            _snapshot_mapping(mapping) for mapping in chunk.source_mappings
        ),
    )


def _snapshot_chunked_document(document: ChunkedDocument) -> ChunkedDocument:
    """Rebuild one document graph so later adapter mutation cannot escape."""

    provenance = document.provenance
    copied_provenance = LoadedDocumentProvenance(
        loaded_schema_version=provenance.loaded_schema_version,
        source=_snapshot_source(provenance.source),
        document_format=provenance.document_format,
        loader_id=provenance.loader_id,
        loader_version=provenance.loader_version,
        load_limits=_snapshot_load_limits(provenance.load_limits),
    )
    copied_title = (
        None
        if document.title is None
        else DocumentTitle(
            text=document.title.text,
            source=document.title.source,
        )
    )
    return ChunkedDocument(
        schema_version=document.schema_version,
        cleaned_schema_version=document.cleaned_schema_version,
        provenance=copied_provenance,
        cleaner_id=document.cleaner_id,
        cleaner_version=document.cleaner_version,
        document_fingerprint=document.document_fingerprint,
        cleaning_fingerprint=document.cleaning_fingerprint,
        chunker_id=document.chunker_id,
        chunker_version=document.chunker_version,
        policy=_snapshot_chunk_policy(document.policy),
        limits=_snapshot_processing_limits(document.limits),
        title=copied_title,
        page_count=document.page_count,
        derivation_fingerprint=document.derivation_fingerprint,
        chunks=tuple(_snapshot_chunk(chunk) for chunk in document.chunks),
    )


def _snapshot_identity(identity: object) -> EmbeddingModelIdentity:
    """Copy and revalidate every vector-space identity field."""

    if type(identity) is not EmbeddingModelIdentity:
        raise EmbeddingValidationError(
            "Embedding adapter exposed an invalid identity."
        )
    try:
        fields = (
            identity.provider,
            identity.adapter_id,
            identity.adapter_version,
            identity.model_tag,
            identity.model_digest,
            identity.dimension,
            identity.normalization,
            identity.document_template_version,
            identity.query_template_version,
            identity.embedding_space_id,
        )
    except AttributeError:
        raise EmbeddingValidationError(
            "Embedding adapter exposed an invalid identity."
        ) from None
    if (
        not all(type(value) is str for value in fields[:5])
        or type(fields[5]) is not int
        or not all(type(value) is str for value in fields[6:])
    ):
        raise EmbeddingValidationError(
            "Embedding adapter exposed an invalid identity."
        )
    return EmbeddingModelIdentity(
        provider=fields[0],
        adapter_id=fields[1],
        adapter_version=fields[2],
        model_tag=fields[3],
        model_digest=fields[4],
        dimension=fields[5],
        normalization=fields[6],
        document_template_version=fields[7],
        query_template_version=fields[8],
        embedding_space_id=fields[9],
    )


def _snapshot_policy(policy: object) -> EmbeddingBatchPolicy:
    """Copy and revalidate the fixed v1 batch policy."""

    if type(policy) is not EmbeddingBatchPolicy:
        raise EmbeddingValidationError(
            "Embedding adapter exposed an invalid batch policy."
        )
    try:
        return EmbeddingBatchPolicy(
            max_batch_items=policy.max_batch_items,
            max_input_code_points=policy.max_input_code_points,
            max_batch_code_points=policy.max_batch_code_points,
            truncate=policy.truncate,
        )
    except AttributeError:
        raise EmbeddingValidationError(
            "Embedding adapter exposed an invalid batch policy."
        ) from None


def _validate_service_identity(identity: EmbeddingModelIdentity) -> None:
    """Require identity metadata for the service-owned fixed templates."""

    if (
        identity.document_template_version
        != DOCUMENT_EMBEDDING_TEMPLATE_VERSION
        or identity.query_template_version != QUERY_EMBEDDING_TEMPLATE_VERSION
    ):
        raise EmbeddingValidationError(
            "Embedding adapter template identity is incompatible."
        )


class DocumentEmbeddingService:
    """Embed exact chunks and queries while policing an untrusted adapter."""

    def __init__(self, embedder: TextEmbedder) -> None:
        """Capture an explicit adapter without performing model or network I/O."""

        if embedder is None:
            raise TypeError("embedder is required.")
        self._embedder = embedder

    def _adapter_identity(self) -> EmbeddingModelIdentity:
        """Read and snapshot identity while sanitizing adapter exceptions."""

        try:
            exposed = self._embedder.identity
        except EmbeddingError as error:
            _raise_sanitized_adapter_error(error)
        return _snapshot_identity(exposed)

    def _adapter_policy(self) -> EmbeddingBatchPolicy:
        """Read and snapshot policy while sanitizing adapter exceptions."""

        try:
            exposed = self._embedder.policy
        except EmbeddingError as error:
            _raise_sanitized_adapter_error(error)
        return _snapshot_policy(exposed)

    def _call_adapter(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Invoke the injected adapter without trusting typed error text."""

        try:
            return self._embedder.embed(request)
        except EmbeddingError as error:
            _raise_sanitized_adapter_error(error)

    def embed_document(
        self,
        document: ChunkedDocument,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> EmbeddedDocument:
        """Embed exact chunks in bounded, cooperatively cancellable batches."""

        try:
            if not isinstance(document, ChunkedDocument):
                raise EmbeddingValidationError(
                    "document must be a validated ChunkedDocument."
                )
            if cancel_requested is not None and not callable(cancel_requested):
                raise EmbeddingValidationError(
                    "cancel_requested must be callable or None."
                )
            _raise_if_embedding_cancelled(cancel_requested)
            snapshot = _snapshot_chunked_document(document)
            identity = self._adapter_identity()
            policy = self._adapter_policy()
            _validate_service_identity(identity)

            published: list[EmbeddedChunk] = []
            for start in range(0, len(snapshot.chunks), policy.max_batch_items):
                _raise_if_embedding_cancelled(cancel_requested)
                chunk_batch = snapshot.chunks[
                    start : start + policy.max_batch_items
                ]
                request = EmbeddingRequest(
                    purpose="document",
                    items=tuple(
                        EmbeddingInput(item_id=chunk.chunk_id, text=chunk.text)
                        for chunk in chunk_batch
                    ),
                )
                vectors = self._run_batch(
                    request,
                    identity,
                    policy,
                    cancel_requested=cancel_requested,
                )
                _raise_if_embedding_cancelled(cancel_requested)
                for chunk, vector in zip(chunk_batch, vectors, strict=True):
                    canonical = _unit_vector(vector.values, identity.dimension)
                    published.append(
                        EmbeddedChunk(
                            embedding_id=_build_embedding_id(
                                chunk.chunk_id,
                                snapshot.derivation_fingerprint,
                                identity.embedding_space_id,
                            ),
                            chunk_id=chunk.chunk_id,
                            ordinal=chunk.ordinal,
                            kind=chunk.kind,
                            text=chunk.text,
                            page_number=chunk.page_number,
                            source_mappings=tuple(
                                _snapshot_mapping(mapping)
                                for mapping in chunk.source_mappings
                            ),
                            derivation_fingerprint=(
                                snapshot.derivation_fingerprint
                            ),
                            embedding_space_id=identity.embedding_space_id,
                            vector=canonical,
                            vector_checksum=_vector_checksum(canonical),
                        )
                    )

            self._require_adapter_unchanged(identity, policy)
            _raise_if_embedding_cancelled(cancel_requested)
            return EmbeddedDocument(
                schema_version=EMBEDDED_DOCUMENT_SCHEMA_VERSION,
                chunked_schema_version=snapshot.schema_version,
                source=_snapshot_source(snapshot.provenance.source),
                derivation_fingerprint=snapshot.derivation_fingerprint,
                identity=_snapshot_identity(identity),
                policy=_snapshot_policy(policy),
                chunked_document=_snapshot_chunked_document(snapshot),
                chunks=tuple(published),
            )
        except (EmbeddingError, DocumentOperationCancelledError):
            raise
        except (MemoryError, RecursionError):
            raise EmbeddingLimitError(
                "Document embedding exceeded a safe resource limit."
            ) from None
        except Exception:
            raise EmbeddingFailedError(
                "Document embedding failed without a safe typed result."
            ) from None

    def embed_query(
        self,
        text: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> EmbeddedQuery:
        """Embed one cancellable query and bind it to the exact model identity."""

        try:
            if type(text) is not str or not text:
                raise EmbeddingValidationError(
                    "Embedding query must contain meaningful text."
                )
            if cancel_requested is not None and not callable(cancel_requested):
                raise EmbeddingValidationError(
                    "cancel_requested must be callable or None."
                )
            _raise_if_embedding_cancelled(cancel_requested)
            maximum_query_code_points = (
                DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS
                - len(QUERY_EMBEDDING_PREFIX)
            )
            if len(text) > maximum_query_code_points:
                # Check the caller-owned string before scanning or prefixing it;
                # otherwise an oversized query could force unbounded work and
                # a second allocation before EmbeddingInput rejects it.
                raise EmbeddingLimitError(
                    "Embedding query exceeds the code-point limit."
                )
            if not any(
                not (character.isspace() or character == "\ufeff")
                for character in text
            ):
                raise EmbeddingValidationError(
                    "Embedding query must contain meaningful text."
                )
            prepared = f"{QUERY_EMBEDDING_PREFIX}{text}"
            identity = self._adapter_identity()
            policy = self._adapter_policy()
            _validate_service_identity(identity)
            request = EmbeddingRequest(
                purpose="query",
                items=(EmbeddingInput(item_id="query", text=prepared),),
            )
            vectors = self._run_batch(
                request,
                identity,
                policy,
                cancel_requested=cancel_requested,
            )
            _raise_if_embedding_cancelled(cancel_requested)
            canonical = _unit_vector(vectors[0].values, identity.dimension)
            self._require_adapter_unchanged(identity, policy)
            return EmbeddedQuery(
                schema_version=EMBEDDED_QUERY_SCHEMA_VERSION,
                identity=_snapshot_identity(identity),
                policy=_snapshot_policy(policy),
                vector=EmbeddingVector(item_id="query", values=canonical),
            )
        except (EmbeddingError, DocumentOperationCancelledError):
            raise
        except (MemoryError, RecursionError):
            raise EmbeddingLimitError(
                "Query embedding exceeded a safe resource limit."
            ) from None
        except Exception:
            raise EmbeddingFailedError(
                "Query embedding failed without a safe typed result."
            ) from None

    def _run_batch(
        self,
        request: EmbeddingRequest,
        identity: EmbeddingModelIdentity,
        policy: EmbeddingBatchPolicy,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[EmbeddingVector, ...]:
        """Call one adapter batch with cancellation gates on both sides."""

        _raise_if_embedding_cancelled(cancel_requested)
        self._require_adapter_unchanged(identity, policy)
        result = self._call_adapter(request)
        _raise_if_embedding_cancelled(cancel_requested)
        self._require_adapter_unchanged(identity, policy)
        if type(result) is not EmbeddingBatch:
            raise EmbeddingValidationError(
                "Embedding adapter returned an invalid batch."
            )
        # Frozen dataclasses are a convenience, not a trust boundary: adapter
        # code can bypass them with ``object.__setattr__``.  Require the exact
        # immutable container types before reading or copying attacker-owned
        # fields so list mutation, Boolean schema aliases, and subclasses with
        # surprising accessors cannot cross the service boundary.
        if (
            type(result.schema_version) is not int
            or type(result.identity) is not EmbeddingModelIdentity
            or type(result.policy) is not EmbeddingBatchPolicy
            or type(result.purpose) is not str
            or type(result.vectors) is not tuple
        ):
            raise EmbeddingValidationError(
                "Embedding adapter returned an invalid batch."
            )
        result_identity = _snapshot_identity(result.identity)
        result_policy = _snapshot_policy(result.policy)
        if (
            result.schema_version != EMBEDDING_SCHEMA_VERSION
            or result_identity != identity
            or result_policy != policy
            or result.purpose != request.purpose
            or len(result.vectors) != len(request.items)
        ):
            raise EmbeddingValidationError(
                "Embedding adapter returned inconsistent batch metadata."
            )

        copied_vectors: list[EmbeddingVector] = []
        for expected, vector in zip(request.items, result.vectors, strict=True):
            if (
                type(vector) is not EmbeddingVector
                or type(vector.item_id) is not str
                or type(vector.values) is not tuple
            ):
                raise EmbeddingValidationError(
                    "Embedding adapter returned an invalid vector."
                )
            # Frozen result values can be replaced by adapter code.  The O(1)
            # dimension check must precede traversal of that tuple.
            if len(vector.values) != identity.dimension:
                raise EmbeddingValidationError(
                    "Embedding vector dimension does not match its model "
                    "identity."
                )
            if not all(type(value) is float for value in vector.values):
                raise EmbeddingValidationError(
                    "Embedding adapter returned an invalid vector."
                )
            if vector.item_id != expected.item_id:
                raise EmbeddingValidationError(
                    "Embedding adapter changed item order or identity."
                )
            values = _unit_vector(vector.values, identity.dimension)
            copied_vectors.append(
                EmbeddingVector(item_id=vector.item_id, values=values)
            )
        # Reconstruct the complete result graph to rerun domain validation on a
        # service-owned snapshot.  Callers never retain adapter-owned vectors.
        copied_result = EmbeddingBatch(
            schema_version=result.schema_version,
            identity=result_identity,
            policy=result_policy,
            purpose=result.purpose,
            vectors=tuple(copied_vectors),
        )
        return copied_result.vectors

    def _require_adapter_unchanged(
        self,
        identity: EmbeddingModelIdentity,
        policy: EmbeddingBatchPolicy,
    ) -> None:
        """Fail closed if mutable adapter metadata drifts during one operation."""

        if (
            self._adapter_identity() != identity
            or self._adapter_policy() != policy
        ):
            raise EmbeddingValidationError(
                "Embedding adapter identity or policy changed during inference."
            )
