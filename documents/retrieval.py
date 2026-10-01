"""Define deterministic, scope-safe document retrieval and reranking.

This module is the model-independent second half of two-step RAG.  It embeds
one bounded query, asks an atomic vector-store boundary for candidates from an
explicit set of document generations, consolidates exact duplicate passages,
and optionally lets a reranker reorder that closed candidate pool.  It never
generates an answer and never invents evidence when the corpus is empty or no
candidate clears the configured cosine threshold.

All injected components are untrusted for shape, mutation, and diagnostics;
the configured embedding and reranking models nevertheless remain the semantic
authorities for the vectors and scores they compute.  The store is a trusted
semantic boundary responsible for authenticating persisted content and scores,
while this service still defensively reconstructs returned values and rechecks
ownership, generation lineage, and public shape.  Dependency-authored exception
messages are replaced at this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Final, Literal, NoReturn, Protocol, TypeAlias, cast

from attachments.domain import (
    MAX_JSON_SAFE_INTEGER,
    AttachmentScope,
    AttachmentScopeKind,
    validate_attachment_id,
    validate_file_id,
    validate_media_type,
)

from .chunking import ChunkSourceMapping, DocumentChunkKind
from .cleaning import DocumentTableCellSpan, DocumentTextSpan
from .domain import DocumentSource
from .embedding import (
    DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS,
    QUERY_EMBEDDING_PREFIX,
    EmbeddedQuery,
    EmbeddingBatchPolicy,
    EmbeddingError,
    EmbeddingFailedError,
    EmbeddingLimitError,
    EmbeddingModelIdentity,
    EmbeddingUnavailableError,
    EmbeddingValidationError,
    EmbeddingVector,
)
from .exceptions import (
    DocumentError,
    DocumentLimitError,
    DocumentOperationCancelledError,
)


RETRIEVAL_SCHEMA_VERSION: Final[Literal[1]] = 1
RERANKER_SCHEMA_VERSION: Final[Literal[1]] = 1
DEFAULT_RETRIEVAL_TOP_K: Final = 5
DEFAULT_RETRIEVAL_CANDIDATE_K: Final = 20
DEFAULT_MINIMUM_COSINE_SIMILARITY: Final = 0.35
MAX_RETRIEVAL_TOP_K: Final = 20
MAX_RETRIEVAL_CANDIDATE_K: Final = 64
MAX_RETRIEVAL_QUERY_CODE_POINTS: Final = (
    DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS - len(QUERY_EMBEDDING_PREFIX)
)
MAX_RETRIEVAL_DOCUMENTS: Final = 128
MAX_RETRIEVAL_FILTER_VALUES: Final = 256
MAX_RETRIEVAL_SCANNED_RECORDS: Final = 10_000
MAX_RETRIEVAL_SCANNED_PAYLOAD_BYTES: Final = 64 * 1024 * 1024
MAX_RETRIEVAL_CANDIDATE_TEXT_CODE_POINTS: Final = (
    MAX_RETRIEVAL_CANDIDATE_K * DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS
)
MAX_RETRIEVAL_SOURCE_MAPPINGS: Final = 100_000
DEFAULT_RERANKER_PROVIDER: Final = "local"
DEFAULT_RERANKER_MAX_INPUTS: Final = MAX_RETRIEVAL_CANDIDATE_K
DEFAULT_RERANKER_MAX_INPUT_CODE_POINTS: Final = (
    DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS
)
DEFAULT_RERANKER_MAX_BATCH_CODE_POINTS: Final = (
    MAX_RETRIEVAL_CANDIDATE_TEXT_CODE_POINTS
)

RerankerScoreSemantics: TypeAlias = Literal["relevance-0-to-1"]

_CHUNK_KINDS: Final = ("prose", "code", "table")
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_EMBEDDING_ID_PATTERN: Final = re.compile(r"^embedding_[0-9a-f]{64}$")
_CHUNK_ID_PATTERN: Final = re.compile(r"^chunk_[0-9a-f]{64}$")
_RERANKER_INPUT_ID_PATTERN: Final = re.compile(r"^candidate_[0-9a-f]{64}$")
_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)
_MODEL_TAG_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$"
)
_RERANKER_IDENTITY_DOMAIN: Final = "elysia.document-reranker.v1"
_RERANKER_REQUEST_DOMAIN: Final = "elysia.document-reranker-request.v1"
_RERANKER_INPUT_DOMAIN: Final = "elysia.document-reranker-input.v1"


def _raise_if_retrieval_cancelled(
    cancel_requested: Callable[[], bool] | None,
) -> None:
    """Stop between query, store, and reranker boundaries when requested."""

    if cancel_requested is not None and cancel_requested():
        raise DocumentOperationCancelledError(
            "Document retrieval was cancelled before answer publication."
        )


class RetrievalError(DocumentError):
    """Base class for stable document-retrieval boundary failures."""


class RetrievalValidationError(RetrievalError):
    """Report an invalid retrieval request or public domain value."""


class RetrievalLimitError(RetrievalError):
    """Report retrieval work that exceeds an explicit resource ceiling."""


class RetrievalUnavailableError(RetrievalError):
    """Report that a required local retrieval dependency is unavailable."""


class RetrievalFailedError(RetrievalError):
    """Report a sanitized query-embedding or vector-store failure."""


class RerankerFailedError(RetrievalError):
    """Report a configured reranker failure or malformed reranker result."""


def _read_exact_slots(
    value: object,
    expected_type: type[object],
    field_names: tuple[str, ...],
    error_message: str,
) -> tuple[Any, ...]:
    """Read bounded slots while containing deleted frozen-dataclass fields."""

    if type(value) is not expected_type:
        raise RetrievalValidationError(error_message)
    try:
        return tuple(getattr(value, field_name) for field_name in field_names)
    except AttributeError:
        # ``frozen=True`` blocks ordinary writes, but object.__delattr__ can
        # still remove a slot.  Public boundaries must keep that corruption
        # inside the documented typed error set.
        raise RetrievalValidationError(error_message) from None


def _canonical_digest(value: object) -> str:
    """Hash one explicitly shaped value as canonical UTF-8 JSON."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_exact_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Return one bounded integer while rejecting booleans and floats."""

    if type(value) is not int or not minimum <= value <= maximum:
        raise RetrievalValidationError(
            f"{field_name} must be an integer from {minimum} through {maximum}."
        )
    return value


def _require_finite_float(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Return one exact finite float inside a closed interval."""

    if (
        type(value) is not float
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise RetrievalValidationError(
            f"{field_name} must be a finite float from {minimum} through "
            f"{maximum}."
        )
    return value


def _validate_digest(value: object, field_name: str) -> str:
    """Return one canonical lowercase SHA-256 digest."""

    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise RetrievalValidationError(
            f"{field_name} must be a lowercase SHA-256 digest."
        )
    return value


def _validate_identifier(value: object, field_name: str) -> str:
    """Return one bounded lowercase adapter identifier."""

    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise RetrievalValidationError(
            f"{field_name} must be a stable lowercase identifier."
        )
    return value


def _validate_version(value: object, field_name: str) -> str:
    """Return one bounded version string used by a reranker identity."""

    if type(value) is not str or _VERSION_PATTERN.fullmatch(value) is None:
        raise RetrievalValidationError(
            f"{field_name} must be a stable non-empty version."
        )
    return value


def _validate_safe_text(
    value: object,
    field_name: str,
    *,
    maximum: int,
    meaningful: bool,
) -> str:
    """Validate bounded exact Unicode without trimming or normalization."""

    if type(value) is not str or not value:
        raise RetrievalValidationError(f"{field_name} must be a non-empty string.")
    if len(value) > maximum:
        raise RetrievalLimitError(f"{field_name} exceeds its code-point limit.")
    if meaningful and not any(
        not (character.isspace() or character == "\ufeff")
        for character in value
    ):
        raise RetrievalValidationError(
            f"{field_name} must contain meaningful text."
        )
    for character in value:
        category = unicodedata.category(character)
        if category == "Cs" or (
            category == "Cc" and character not in {"\t", "\n", "\r"}
        ):
            raise RetrievalValidationError(
                f"{field_name} contains an unsafe control character."
            )
    return value


def _validate_filter_tuple(
    values: object,
    field_name: str,
    validator: object,
) -> tuple[object, ...]:
    """Validate one exact, duplicate-free tuple through a scalar validator."""

    if type(values) is not tuple:
        raise RetrievalValidationError(f"{field_name} must be an exact tuple.")
    if len(values) > MAX_RETRIEVAL_FILTER_VALUES:
        raise RetrievalLimitError(f"{field_name} exceeds the filter-value limit.")
    checked: list[object] = []
    for value in values:
        try:
            checked_value = validator(value)  # type: ignore[operator]
            if isinstance(checked_value, str) and type(checked_value) is not str:
                raise RetrievalValidationError(
                    f"{field_name} contains a non-exact string."
                )
            checked.append(checked_value)
        except RetrievalError:
            raise
        except (TypeError, ValueError) as error:
            raise RetrievalValidationError(
                f"{field_name} contains an invalid value."
            ) from error
    if len(set(checked)) != len(checked):
        raise RetrievalValidationError(f"{field_name} must not contain duplicates.")
    return tuple(checked)


def _validate_chunk_kind(value: object) -> DocumentChunkKind:
    """Return one closed document chunk kind."""

    if type(value) is not str or value not in _CHUNK_KINDS:
        raise RetrievalValidationError("chunk kind is invalid.")
    return value  # type: ignore[return-value]


def _validate_page_number(value: object) -> int:
    """Return one positive one-based page number."""

    return _require_exact_integer(
        value,
        "page_number",
        minimum=1,
        maximum=MAX_JSON_SAFE_INTEGER,
    )


@dataclass(frozen=True, slots=True)
class ExpectedDocumentGeneration:
    """Bind one authorized source to the exact indexed derivation expected.

    Retrieval accepts an explicit corpus rather than discovering every row in
    a scope.  This generation token prevents stale vectors for a still-valid
    ownership link from being mistaken for the caller's current document.
    """

    source: DocumentSource
    derivation_fingerprint: str

    def __post_init__(self) -> None:
        """Require exact source and generation domain values."""

        if type(self.source) is not DocumentSource:
            raise RetrievalValidationError(
                "Expected document source must be an exact DocumentSource."
            )
        if type(self.source.scope) is not AttachmentScope:
            raise RetrievalValidationError(
                "Expected document scope must be an exact AttachmentScope."
            )
        _validate_digest(
            self.derivation_fingerprint,
            "derivation_fingerprint",
        )


@dataclass(frozen=True, slots=True)
class RetrievalMetadataFilter:
    """Restrict candidates by exact, closed metadata values.

    Empty tuples mean no restriction for that field.  Filters deliberately do
    not support substrings, paths, or caller-provided SQL; the same immutable
    value can therefore be enforced both by the store and by this service.
    """

    file_ids: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    chunk_kinds: tuple[DocumentChunkKind, ...] = ()
    page_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Require exact tuples containing valid unique scalar values."""

        _validate_filter_tuple(self.file_ids, "file_ids", validate_file_id)
        _validate_filter_tuple(
            self.media_types,
            "media_types",
            validate_media_type,
        )
        _validate_filter_tuple(
            self.chunk_kinds,
            "chunk_kinds",
            _validate_chunk_kind,
        )
        _validate_filter_tuple(
            self.page_numbers,
            "page_numbers",
            _validate_page_number,
        )


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """Select a bounded quantized-cosine pool and final result count.

    The store clamps valid dot products and rounds them to eight decimal
    places before applying this threshold, candidate retention, and ordering.
    Treating differences below that public precision as ties makes v1 results
    reproducible instead of exposing platform-level floating-point noise.
    """

    top_k: int = DEFAULT_RETRIEVAL_TOP_K
    candidate_k: int = DEFAULT_RETRIEVAL_CANDIDATE_K
    minimum_cosine_similarity: float = DEFAULT_MINIMUM_COSINE_SIMILARITY

    def __post_init__(self) -> None:
        """Require coherent counts and a non-negative sufficiency threshold."""

        top_k = _require_exact_integer(
            self.top_k,
            "top_k",
            minimum=1,
            maximum=MAX_RETRIEVAL_TOP_K,
        )
        candidate_k = _require_exact_integer(
            self.candidate_k,
            "candidate_k",
            minimum=1,
            maximum=MAX_RETRIEVAL_CANDIDATE_K,
        )
        if candidate_k < top_k:
            raise RetrievalValidationError(
                "candidate_k cannot be smaller than top_k."
            )
        _require_finite_float(
            self.minimum_cosine_similarity,
            "minimum_cosine_similarity",
            minimum=0.0,
            maximum=1.0,
        )


@dataclass(frozen=True, slots=True)
class RetrievalLimits:
    """Bound query, corpus, scan, candidate text, and provenance resources.

    The query ceiling is fixed to the embedding model's 2,000-code-point input
    budget minus its versioned instruction prefix.  Other values may be
    reduced by a caller but cannot enlarge the audited system hard limits.
    The store must enforce scan counts and serialized lineage/chunk/vector
    payload bytes during iteration; checking only its final candidate tuple
    would not bound rejected rows.  Fixed-width database identifiers and
    checksums are validated by exact type and encoded length instead.
    """

    max_query_code_points: int = MAX_RETRIEVAL_QUERY_CODE_POINTS
    max_documents: int = MAX_RETRIEVAL_DOCUMENTS
    max_filter_values: int = MAX_RETRIEVAL_FILTER_VALUES
    max_scanned_records: int = MAX_RETRIEVAL_SCANNED_RECORDS
    max_scanned_payload_bytes: int = MAX_RETRIEVAL_SCANNED_PAYLOAD_BYTES
    max_candidate_text_code_points: int = (
        MAX_RETRIEVAL_CANDIDATE_TEXT_CODE_POINTS
    )
    max_source_mappings: int = MAX_RETRIEVAL_SOURCE_MAPPINGS

    def __post_init__(self) -> None:
        """Reject enlarged or ambiguous resource limits."""

        if (
            type(self.max_query_code_points) is not int
            or self.max_query_code_points != MAX_RETRIEVAL_QUERY_CODE_POINTS
        ):
            raise RetrievalValidationError(
                "max_query_code_points must match the embedding input budget."
            )
        for field_name, maximum in (
            ("max_documents", MAX_RETRIEVAL_DOCUMENTS),
            ("max_filter_values", MAX_RETRIEVAL_FILTER_VALUES),
            ("max_scanned_records", MAX_RETRIEVAL_SCANNED_RECORDS),
            (
                "max_scanned_payload_bytes",
                MAX_RETRIEVAL_SCANNED_PAYLOAD_BYTES,
            ),
            (
                "max_candidate_text_code_points",
                MAX_RETRIEVAL_CANDIDATE_TEXT_CODE_POINTS,
            ),
            ("max_source_mappings", MAX_RETRIEVAL_SOURCE_MAPPINGS),
        ):
            _require_exact_integer(
                getattr(self, field_name),
                field_name,
                minimum=1,
                maximum=maximum,
            )


@dataclass(frozen=True, slots=True)
class StoredRetrievalCandidate:
    """Carry one store-scored chunk with complete citation lineage."""

    source: DocumentSource
    derivation_fingerprint: str
    embedding_space_id: str
    embedding_id: str
    chunk_id: str
    ordinal: int
    kind: DocumentChunkKind
    text: str
    page_number: int | None
    source_mappings: tuple[ChunkSourceMapping, ...]
    cosine_similarity: float

    def __post_init__(self) -> None:
        """Validate candidate identity, content, provenance, and score."""

        if type(self.source) is not DocumentSource:
            raise RetrievalValidationError(
                "Stored candidate source must be an exact DocumentSource."
            )
        if type(self.source.scope) is not AttachmentScope:
            raise RetrievalValidationError(
                "Stored candidate scope must be an exact AttachmentScope."
            )
        _validate_digest(
            self.derivation_fingerprint,
            "derivation_fingerprint",
        )
        _validate_digest(self.embedding_space_id, "embedding_space_id")
        if (
            type(self.embedding_id) is not str
            or _EMBEDDING_ID_PATTERN.fullmatch(self.embedding_id) is None
        ):
            raise RetrievalValidationError("embedding_id is invalid.")
        if (
            type(self.chunk_id) is not str
            or _CHUNK_ID_PATTERN.fullmatch(self.chunk_id) is None
        ):
            raise RetrievalValidationError("chunk_id is invalid.")
        _require_exact_integer(
            self.ordinal,
            "ordinal",
            minimum=0,
            maximum=MAX_JSON_SAFE_INTEGER,
        )
        _validate_chunk_kind(self.kind)
        _validate_safe_text(
            self.text,
            "candidate text",
            maximum=DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS,
            meaningful=False,
        )
        if self.page_number is not None:
            _validate_page_number(self.page_number)
        if type(self.source_mappings) is not tuple or not self.source_mappings:
            raise RetrievalValidationError(
                "source_mappings must be a non-empty exact tuple."
            )
        if not all(
            type(mapping) is ChunkSourceMapping
            for mapping in self.source_mappings
        ):
            raise RetrievalValidationError(
                "source_mappings contain an invalid mapping type."
            )
        for mapping in self.source_mappings:
            if type(mapping.source_span) not in (
                DocumentTextSpan,
                DocumentTableCellSpan,
            ):
                raise RetrievalValidationError(
                    "source_mappings contain an invalid source-span type."
                )
            if mapping.source_span.page_number != self.page_number:
                raise RetrievalValidationError(
                    "Candidate mappings changed the source page."
                )
        _require_finite_float(
            self.cosine_similarity,
            "cosine_similarity",
            minimum=-1.0,
            maximum=1.0,
        )


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    """Preserve one exact source occurrence behind a consolidated hit."""

    source: DocumentSource
    derivation_fingerprint: str
    embedding_id: str
    chunk_id: str
    ordinal: int
    page_number: int | None
    source_mappings: tuple[ChunkSourceMapping, ...]
    cosine_similarity: float

    def __post_init__(self) -> None:
        """Require complete validated provenance without path information."""

        if type(self.source) is not DocumentSource:
            raise RetrievalValidationError(
                "Retrieval evidence source must be an exact DocumentSource."
            )
        if type(self.source.scope) is not AttachmentScope:
            raise RetrievalValidationError(
                "Retrieval evidence scope must be an exact AttachmentScope."
            )
        _validate_digest(
            self.derivation_fingerprint,
            "derivation_fingerprint",
        )
        if (
            type(self.embedding_id) is not str
            or _EMBEDDING_ID_PATTERN.fullmatch(self.embedding_id) is None
        ):
            raise RetrievalValidationError("embedding_id is invalid.")
        if (
            type(self.chunk_id) is not str
            or _CHUNK_ID_PATTERN.fullmatch(self.chunk_id) is None
        ):
            raise RetrievalValidationError("chunk_id is invalid.")
        _require_exact_integer(
            self.ordinal,
            "ordinal",
            minimum=0,
            maximum=MAX_JSON_SAFE_INTEGER,
        )
        if self.page_number is not None:
            _validate_page_number(self.page_number)
        if type(self.source_mappings) is not tuple or not self.source_mappings:
            raise RetrievalValidationError(
                "Evidence source_mappings must be a non-empty exact tuple."
            )
        if not all(
            type(mapping) is ChunkSourceMapping
            for mapping in self.source_mappings
        ):
            raise RetrievalValidationError(
                "Evidence source_mappings contain an invalid mapping type."
            )
        _require_finite_float(
            self.cosine_similarity,
            "cosine_similarity",
            minimum=-1.0,
            maximum=1.0,
        )


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """Publish one exact passage and its admitted duplicate occurrences."""

    kind: DocumentChunkKind
    text: str
    evidence: tuple[RetrievalEvidence, ...]
    cosine_similarity: float
    reranker_score: float | None = None

    def __post_init__(self) -> None:
        """Validate deduplicated content, evidence, and ranking scores."""

        _validate_chunk_kind(self.kind)
        _validate_safe_text(
            self.text,
            "retrieval hit text",
            maximum=DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS,
            meaningful=False,
        )
        if type(self.evidence) is not tuple or not self.evidence or not all(
            type(item) is RetrievalEvidence for item in self.evidence
        ):
            raise RetrievalValidationError(
                "Retrieval hit evidence must be a non-empty exact tuple."
            )
        evidence_keys = tuple(
            (
                item.source.link_id,
                item.derivation_fingerprint,
                item.embedding_id,
            )
            for item in self.evidence
        )
        if len(set(evidence_keys)) != len(evidence_keys):
            raise RetrievalValidationError(
                "Retrieval hit evidence must not contain duplicates."
            )
        similarity = _require_finite_float(
            self.cosine_similarity,
            "cosine_similarity",
            minimum=-1.0,
            maximum=1.0,
        )
        if similarity != max(item.cosine_similarity for item in self.evidence):
            raise RetrievalValidationError(
                "Hit cosine similarity must equal its best evidence score."
            )
        if self.reranker_score is not None:
            _require_finite_float(
                self.reranker_score,
                "reranker_score",
                minimum=0.0,
                maximum=1.0,
            )


@dataclass(frozen=True, slots=True)
class RerankerIdentity:
    """Identify one immutable reranker and its closed score semantics."""

    provider: str
    adapter_id: str
    adapter_version: str
    model_tag: str
    model_digest: str
    score_semantics: RerankerScoreSemantics = "relevance-0-to-1"
    identity_fingerprint: str = ""

    def __post_init__(self) -> None:
        """Validate identity fields and derive their canonical fingerprint."""

        _validate_identifier(self.provider, "provider")
        _validate_identifier(self.adapter_id, "adapter_id")
        _validate_version(self.adapter_version, "adapter_version")
        if (
            type(self.model_tag) is not str
            or _MODEL_TAG_PATTERN.fullmatch(self.model_tag) is None
        ):
            raise RetrievalValidationError(
                "model_tag must be a bounded explicit model tag."
            )
        _validate_digest(self.model_digest, "model_digest")
        if self.score_semantics != "relevance-0-to-1":
            raise RetrievalValidationError(
                "Reranker score semantics must be relevance-0-to-1."
            )
        expected = _canonical_digest(
            {
                "fingerprint_domain": _RERANKER_IDENTITY_DOMAIN,
                "schema_version": RERANKER_SCHEMA_VERSION,
                "provider": self.provider,
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "model_tag": self.model_tag,
                "model_digest": self.model_digest,
                "score_semantics": self.score_semantics,
            }
        )
        if self.identity_fingerprint and self.identity_fingerprint != expected:
            raise RetrievalValidationError(
                "identity_fingerprint does not match the reranker identity."
            )
        object.__setattr__(self, "identity_fingerprint", expected)


@dataclass(frozen=True, slots=True)
class RerankerPolicy:
    """Fix reranker input and non-truncation budgets for reproducibility."""

    max_inputs: Literal[64] = DEFAULT_RERANKER_MAX_INPUTS
    max_query_code_points: int = MAX_RETRIEVAL_QUERY_CODE_POINTS
    max_input_code_points: Literal[2000] = (
        DEFAULT_RERANKER_MAX_INPUT_CODE_POINTS
    )
    max_batch_code_points: Literal[128000] = 128_000
    truncate: Literal[False] = False

    def __post_init__(self) -> None:
        """Reject policy drift that could change reranking semantics."""

        if (
            type(self.max_inputs) is not int
            or self.max_inputs != DEFAULT_RERANKER_MAX_INPUTS
            or type(self.max_query_code_points) is not int
            or self.max_query_code_points != MAX_RETRIEVAL_QUERY_CODE_POINTS
            or type(self.max_input_code_points) is not int
            or self.max_input_code_points
            != DEFAULT_RERANKER_MAX_INPUT_CODE_POINTS
            or type(self.max_batch_code_points) is not int
            or self.max_batch_code_points
            != DEFAULT_RERANKER_MAX_BATCH_CODE_POINTS
            or type(self.truncate) is not bool
            or self.truncate
        ):
            raise RetrievalValidationError(
                "Reranker policy must match the fixed v1 contract."
            )


@dataclass(frozen=True, slots=True)
class RerankerInput:
    """Associate one opaque candidate ID with exact passage text."""

    item_id: str
    text: str

    def __post_init__(self) -> None:
        """Require a content-derived ID and bounded unchanged text."""

        if (
            type(self.item_id) is not str
            or _RERANKER_INPUT_ID_PATTERN.fullmatch(self.item_id) is None
        ):
            raise RetrievalValidationError("Reranker input item_id is invalid.")
        _validate_safe_text(
            self.text,
            "reranker input text",
            maximum=DEFAULT_RERANKER_MAX_INPUT_CODE_POINTS,
            meaningful=False,
        )


def _reranker_request_fingerprint(
    query: str,
    inputs: tuple[RerankerInput, ...],
    policy: RerankerPolicy,
) -> str:
    """Bind a reranker response to exact ordered text and policy values."""

    return _canonical_digest(
        {
            "fingerprint_domain": _RERANKER_REQUEST_DOMAIN,
            "schema_version": RERANKER_SCHEMA_VERSION,
            "query": query,
            "inputs": [
                {"item_id": item.item_id, "text": item.text}
                for item in inputs
            ],
            "policy": {
                "max_inputs": policy.max_inputs,
                "max_query_code_points": policy.max_query_code_points,
                "max_input_code_points": policy.max_input_code_points,
                "max_batch_code_points": policy.max_batch_code_points,
                "truncate": policy.truncate,
            },
        }
    )


@dataclass(frozen=True, slots=True)
class RerankerRequest:
    """Carry one complete ordered reranker request and integrity digest."""

    schema_version: Literal[1]
    query: str
    inputs: tuple[RerankerInput, ...]
    policy: RerankerPolicy
    request_fingerprint: str = ""

    def __post_init__(self) -> None:
        """Validate request budgets and derive its canonical fingerprint."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != RERANKER_SCHEMA_VERSION
        ):
            raise RetrievalValidationError(
                "Unsupported reranker request schema version."
            )
        if type(self.policy) is not RerankerPolicy:
            raise RetrievalValidationError("Reranker request policy is invalid.")
        _validate_safe_text(
            self.query,
            "reranker query",
            maximum=self.policy.max_query_code_points,
            meaningful=True,
        )
        if (
            type(self.inputs) is not tuple
            or not self.inputs
            or len(self.inputs) > self.policy.max_inputs
            or not all(type(item) is RerankerInput for item in self.inputs)
        ):
            raise RetrievalValidationError(
                "Reranker request must contain bounded exact inputs."
            )
        item_ids = tuple(item.item_id for item in self.inputs)
        if len(set(item_ids)) != len(item_ids):
            raise RetrievalValidationError(
                "Reranker request item IDs must be unique."
            )
        if sum(len(item.text) for item in self.inputs) > (
            self.policy.max_batch_code_points
        ):
            raise RetrievalLimitError(
                "Reranker request exceeds its batch text limit."
            )
        expected = _reranker_request_fingerprint(
            self.query,
            self.inputs,
            self.policy,
        )
        if self.request_fingerprint and self.request_fingerprint != expected:
            raise RetrievalValidationError(
                "request_fingerprint does not match the reranker request."
            )
        object.__setattr__(self, "request_fingerprint", expected)


@dataclass(frozen=True, slots=True)
class RerankerScore:
    """Associate one candidate ID with normalized relevance."""

    item_id: str
    relevance: float

    def __post_init__(self) -> None:
        """Require a known-shaped ID and finite normalized score."""

        if (
            type(self.item_id) is not str
            or _RERANKER_INPUT_ID_PATTERN.fullmatch(self.item_id) is None
        ):
            raise RetrievalValidationError("Reranker score item_id is invalid.")
        _require_finite_float(
            self.relevance,
            "relevance",
            minimum=0.0,
            maximum=1.0,
        )


@dataclass(frozen=True, slots=True)
class RerankerBatch:
    """Publish a complete score permutation for one exact request."""

    schema_version: Literal[1]
    identity: RerankerIdentity
    policy: RerankerPolicy
    request_fingerprint: str
    scores: tuple[RerankerScore, ...]

    def __post_init__(self) -> None:
        """Validate batch metadata, fingerprint shape, and score uniqueness."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != RERANKER_SCHEMA_VERSION
        ):
            raise RetrievalValidationError(
                "Unsupported reranker batch schema version."
            )
        if type(self.identity) is not RerankerIdentity:
            raise RetrievalValidationError("Reranker batch identity is invalid.")
        if type(self.policy) is not RerankerPolicy:
            raise RetrievalValidationError("Reranker batch policy is invalid.")
        _validate_digest(self.request_fingerprint, "request_fingerprint")
        if (
            type(self.scores) is not tuple
            or not self.scores
            or len(self.scores) > self.policy.max_inputs
            or not all(type(score) is RerankerScore for score in self.scores)
        ):
            raise RetrievalValidationError(
                "Reranker batch must contain bounded exact scores."
            )
        item_ids = tuple(score.item_id for score in self.scores)
        if len(set(item_ids)) != len(item_ids):
            raise RetrievalValidationError(
                "Reranker batch item IDs must be unique."
            )


class QueryEmbeddingService(Protocol):
    """Embed one exact query and publish its vector-space identity."""

    def embed_query(
        self,
        text: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> EmbeddedQuery:
        """Return one identity-bearing query or raise a typed EmbeddingError."""

        ...


class AtomicRetrievalStore(Protocol):
    """Authenticate and search one corpus in a consistent trusted snapshot.

    Implementations own the semantic integrity of persisted passage text,
    lineage, vectors, and cosine scores.  The retriever can revalidate public
    shape and ownership but cannot reconstruct those private store facts from
    a candidate alone.
    """

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
        """Return bounded cosine candidates or a path-private typed failure."""

        ...


class DocumentReranker(Protocol):
    """Score every candidate in one closed request without changing it."""

    @property
    def identity(self) -> RerankerIdentity:
        """Return the immutable reranker identity used by this adapter."""

        ...

    @property
    def policy(self) -> RerankerPolicy:
        """Return the fixed reranker resource and truncation policy."""

        ...

    def rerank(self, request: RerankerRequest) -> RerankerBatch:
        """Score every request input exactly once or raise an exception."""

        ...


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Publish deterministic grounded hits without retaining query text."""

    schema_version: Literal[1]
    scope: AttachmentScope
    policy: RetrievalPolicy
    metadata_filter: RetrievalMetadataFilter
    embedding_identity: EmbeddingModelIdentity | None
    reranker_identity: RerankerIdentity | None
    candidate_count: int
    hits: tuple[RetrievalHit, ...]

    def __post_init__(self) -> None:
        """Validate ownership, filtering, ranking, and ordered hit shape."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != RETRIEVAL_SCHEMA_VERSION
        ):
            raise RetrievalValidationError(
                "Unsupported retrieval result schema version."
            )
        if type(self.scope) is not AttachmentScope:
            raise RetrievalValidationError(
                "Retrieval result scope must be an exact AttachmentScope."
            )
        if type(self.policy) is not RetrievalPolicy:
            raise RetrievalValidationError("Retrieval result policy is invalid.")
        if type(self.metadata_filter) is not RetrievalMetadataFilter:
            raise RetrievalValidationError(
                "Retrieval result metadata filter is invalid."
            )
        if self.embedding_identity is not None and (
            type(self.embedding_identity) is not EmbeddingModelIdentity
        ):
            raise RetrievalValidationError(
                "Retrieval result embedding identity is invalid."
            )
        if self.reranker_identity is not None and (
            type(self.reranker_identity) is not RerankerIdentity
        ):
            raise RetrievalValidationError(
                "Retrieval result reranker identity is invalid."
            )
        candidate_count = _require_exact_integer(
            self.candidate_count,
            "candidate_count",
            minimum=0,
            maximum=self.policy.candidate_k,
        )
        if (
            type(self.hits) is not tuple
            or len(self.hits) > self.policy.top_k
            or not all(type(hit) is RetrievalHit for hit in self.hits)
        ):
            raise RetrievalValidationError(
                "Retrieval result contains invalid or excessive hits."
            )
        if len(self.hits) > candidate_count:
            raise RetrievalValidationError(
                "Retrieval result has more hits than candidates."
            )
        if self.embedding_identity is None and (
            candidate_count != 0
            or self.hits
            or self.reranker_identity is not None
        ):
            raise RetrievalValidationError(
                "An unembedded result must be empty."
            )
        if self.reranker_identity is not None and candidate_count == 0:
            raise RetrievalValidationError(
                "An empty result cannot carry a reranker identity."
            )
        if candidate_count > 0 and not self.hits:
            raise RetrievalValidationError(
                "A non-empty candidate set must publish at least one hit."
            )

        hit_keys = tuple((hit.kind, hit.text) for hit in self.hits)
        if len(set(hit_keys)) != len(hit_keys):
            raise RetrievalValidationError(
                "Retrieval result hits must be exactly deduplicated."
            )
        for hit in self.hits:
            if (
                self.metadata_filter.chunk_kinds
                and hit.kind not in self.metadata_filter.chunk_kinds
            ):
                raise RetrievalValidationError(
                    "Retrieval result contains a hit outside its filter."
                )
            for evidence in hit.evidence:
                if evidence.source.scope != self.scope:
                    raise RetrievalValidationError(
                        "Retrieval result evidence changed its scope."
                    )
                if (
                    self.metadata_filter.file_ids
                    and evidence.source.file_id
                    not in self.metadata_filter.file_ids
                ) or (
                    self.metadata_filter.media_types
                    and evidence.source.media_type
                    not in self.metadata_filter.media_types
                ) or (
                    self.metadata_filter.page_numbers
                    and evidence.page_number
                    not in self.metadata_filter.page_numbers
                ):
                    raise RetrievalValidationError(
                        "Retrieval result contains evidence outside its filter."
                    )
                if (
                    evidence.cosine_similarity
                    < self.policy.minimum_cosine_similarity
                ):
                    raise RetrievalValidationError(
                        "Retrieval result contains insufficient evidence."
                    )

        if self.reranker_identity is None:
            if any(hit.reranker_score is not None for hit in self.hits):
                raise RetrievalValidationError(
                    "Cosine-only results cannot carry reranker scores."
                )
            expected_order = tuple(
                sorted(
                    self.hits,
                    key=lambda hit: (
                        -hit.cosine_similarity,
                        *_hit_tie_key(hit),
                    ),
                )
            )
        else:
            if any(hit.reranker_score is None for hit in self.hits):
                raise RetrievalValidationError(
                    "Reranked results require a score for every hit."
                )
            expected_order = tuple(
                sorted(
                    self.hits,
                    key=lambda hit: (
                        -cast(float, hit.reranker_score),
                        -hit.cosine_similarity,
                        *_hit_tie_key(hit),
                    ),
                )
            )
        if self.hits != expected_order:
            raise RetrievalValidationError(
                "Retrieval result hits are not in deterministic rank order."
            )


def _snapshot_scope(scope: object) -> AttachmentScope:
    """Rebuild one exact scope before passing it to dependencies."""

    kind, scope_id = _read_exact_slots(
        scope,
        AttachmentScope,
        ("kind", "id"),
        "scope must be an exact AttachmentScope.",
    )
    if type(kind) is not str or type(scope_id) is not str:
        raise RetrievalValidationError("scope must be an exact AttachmentScope.")
    try:
        return AttachmentScope(
            kind=cast(AttachmentScopeKind, kind),
            id=scope_id,
        )
    except (TypeError, ValueError) as error:
        raise RetrievalValidationError("Attachment scope is invalid.") from error


def _snapshot_source(source: object) -> DocumentSource:
    """Rebuild path-private source metadata through public constructors."""

    (
        scope,
        link_id,
        file_id,
        file_name,
        media_type,
        size_bytes,
    ) = _read_exact_slots(
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
        "Document source has an invalid type.",
    )
    if (
        type(scope) is not AttachmentScope
        or type(link_id) is not str
        or type(file_id) is not str
        or type(file_name) is not str
        or type(media_type) is not str
        or type(size_bytes) is not int
    ):
        raise RetrievalValidationError("Document source has an invalid type.")
    try:
        return DocumentSource(
            scope=_snapshot_scope(scope),
            link_id=link_id,
            file_id=file_id,
            file_name=file_name,
            media_type=media_type,
            size_bytes=size_bytes,
        )
    except RetrievalError:
        raise
    except DocumentError as error:
        raise RetrievalValidationError(
            "Document source metadata is invalid."
        ) from error


def _snapshot_mapping(mapping: object) -> ChunkSourceMapping:
    """Rebuild one exact source mapping and its closed span variant."""

    chunk_start, chunk_end, span = _read_exact_slots(
        mapping,
        ChunkSourceMapping,
        (
            "chunk_start_code_point",
            "chunk_end_code_point",
            "source_span",
        ),
        "Source mapping has an invalid type.",
    )
    try:
        if type(span) is DocumentTextSpan:
            block, start, end, page = _read_exact_slots(
                span,
                DocumentTextSpan,
                (
                    "block_ordinal",
                    "start_code_point",
                    "end_code_point",
                    "page_number",
                ),
                "Source mapping span has an invalid type.",
            )
            copied_span: DocumentTextSpan | DocumentTableCellSpan = (
                DocumentTextSpan(
                    block_ordinal=block,
                    start_code_point=start,
                    end_code_point=end,
                    page_number=page,
                )
            )
        elif type(span) is DocumentTableCellSpan:
            block, row, column, start, end, page = _read_exact_slots(
                span,
                DocumentTableCellSpan,
                (
                    "block_ordinal",
                    "row_index",
                    "column_index",
                    "start_code_point",
                    "end_code_point",
                    "page_number",
                ),
                "Source mapping span has an invalid type.",
            )
            copied_span = DocumentTableCellSpan(
                block_ordinal=block,
                row_index=row,
                column_index=column,
                start_code_point=start,
                end_code_point=end,
                page_number=page,
            )
        else:
            raise RetrievalValidationError(
                "Source mapping span has an invalid type."
            )
        return ChunkSourceMapping(
            chunk_start_code_point=chunk_start,
            chunk_end_code_point=chunk_end,
            source_span=copied_span,
        )
    except RetrievalError:
        raise
    except DocumentError as error:
        raise RetrievalValidationError("Source mapping is invalid.") from error


def _snapshot_identity(identity: object) -> EmbeddingModelIdentity:
    """Rebuild an exact embedding identity returned by an untrusted service."""

    fields = _read_exact_slots(
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
        "Embedding identity has an invalid type.",
    )
    if (
        not all(type(value) is str for value in fields[:5])
        or type(fields[5]) is not int
        or not all(type(value) is str for value in fields[6:])
    ):
        raise RetrievalValidationError(
            "Embedding identity has an invalid type."
        )
    try:
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
    except EmbeddingError as error:
        raise RetrievalValidationError("Embedding identity is invalid.") from error


def _snapshot_embedding_policy(policy: object) -> EmbeddingBatchPolicy:
    """Rebuild the fixed query embedding policy through its constructor."""

    fields = _read_exact_slots(
        policy,
        EmbeddingBatchPolicy,
        (
            "max_batch_items",
            "max_input_code_points",
            "max_batch_code_points",
            "truncate",
        ),
        "Embedding policy has an invalid type.",
    )
    try:
        return EmbeddingBatchPolicy(
            max_batch_items=fields[0],
            max_input_code_points=fields[1],
            max_batch_code_points=fields[2],
            truncate=fields[3],
        )
    except EmbeddingError as error:
        raise RetrievalValidationError("Embedding policy is invalid.") from error


def _snapshot_embedded_query(query: object) -> EmbeddedQuery:
    """Rebuild an identity-bearing query before it reaches persistence."""

    schema_version, raw_identity, raw_policy, vector = _read_exact_slots(
        query,
        EmbeddedQuery,
        ("schema_version", "identity", "policy", "vector"),
        "Query embedder returned an invalid result type.",
    )
    identity = _snapshot_identity(raw_identity)
    policy = _snapshot_embedding_policy(raw_policy)
    item_id, values = _read_exact_slots(
        vector,
        EmbeddingVector,
        ("item_id", "values"),
        "Embedded query vector has an invalid type.",
    )
    if (
        type(item_id) is not str
        or type(values) is not tuple
        or len(values) != identity.dimension
    ):
        raise RetrievalValidationError("Embedded query vector has an invalid type.")
    # Check the bounded dimension before walking values returned by an adapter;
    # a mutated frozen dataclass must not bypass the embedding-space ceiling.
    if not all(type(value) is float for value in values):
        raise RetrievalValidationError("Embedded query vector has an invalid type.")
    try:
        return EmbeddedQuery(
            schema_version=schema_version,
            identity=identity,
            policy=policy,
            vector=EmbeddingVector(
                item_id=item_id,
                values=tuple(values),
            ),
        )
    except (EmbeddingError, TypeError, ValueError) as error:
        raise RetrievalValidationError("Embedded query is invalid.") from error


def _snapshot_expected_generation(
    generation: object,
) -> ExpectedDocumentGeneration:
    """Rebuild one expected source generation through exact value types."""

    source, fingerprint = _read_exact_slots(
        generation,
        ExpectedDocumentGeneration,
        ("source", "derivation_fingerprint"),
        "expected_documents contain an invalid generation type.",
    )
    return ExpectedDocumentGeneration(
        source=_snapshot_source(source),
        derivation_fingerprint=fingerprint,
    )


def _snapshot_filter(
    value: object,
    *,
    max_filter_values: int = MAX_RETRIEVAL_FILTER_VALUES,
) -> RetrievalMetadataFilter:
    """Canonicalize a filter only after its aggregate budget is proven."""

    file_ids, media_types, chunk_kinds, page_numbers = _read_exact_slots(
        value,
        RetrievalMetadataFilter,
        ("file_ids", "media_types", "chunk_kinds", "page_numbers"),
        "metadata_filter must be an exact RetrievalMetadataFilter.",
    )
    raw_values = (file_ids, media_types, chunk_kinds, page_numbers)
    if not all(type(items) is tuple for items in raw_values):
        raise RetrievalValidationError(
            "metadata_filter must contain exact tuples."
        )
    if sum(len(items) for items in raw_values) > max_filter_values:
        raise RetrievalLimitError(
            "Metadata filters exceed the configured value limit."
        )
    validated = RetrievalMetadataFilter(
        file_ids=file_ids,
        media_types=media_types,
        chunk_kinds=chunk_kinds,
        page_numbers=page_numbers,
    )
    return RetrievalMetadataFilter(
        file_ids=tuple(sorted(validated.file_ids)),
        media_types=tuple(sorted(validated.media_types)),
        chunk_kinds=tuple(sorted(validated.chunk_kinds)),
        page_numbers=tuple(sorted(validated.page_numbers)),
    )


def _snapshot_policy(value: object) -> RetrievalPolicy:
    """Rebuild one retrieval policy before giving it to the store."""

    top_k, candidate_k, minimum_similarity = _read_exact_slots(
        value,
        RetrievalPolicy,
        ("top_k", "candidate_k", "minimum_cosine_similarity"),
        "policy must be an exact RetrievalPolicy.",
    )
    return RetrievalPolicy(
        top_k=top_k,
        candidate_k=candidate_k,
        minimum_cosine_similarity=minimum_similarity,
    )


def _snapshot_limits(value: object) -> RetrievalLimits:
    """Rebuild every retrieval resource ceiling through validation."""

    fields = _read_exact_slots(
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
        "limits must be exact RetrievalLimits.",
    )
    return RetrievalLimits(
        max_query_code_points=fields[0],
        max_documents=fields[1],
        max_filter_values=fields[2],
        max_scanned_records=fields[3],
        max_scanned_payload_bytes=fields[4],
        max_candidate_text_code_points=fields[5],
        max_source_mappings=fields[6],
    )


def _snapshot_candidate(
    value: object,
    *,
    remaining_text_code_points: int,
    remaining_source_mappings: int,
) -> StoredRetrievalCandidate:
    """Rebuild one store candidate without traversing over-budget payloads."""

    fields = _read_exact_slots(
        value,
        StoredRetrievalCandidate,
        (
            "source",
            "derivation_fingerprint",
            "embedding_space_id",
            "embedding_id",
            "chunk_id",
            "ordinal",
            "kind",
            "text",
            "page_number",
            "source_mappings",
            "cosine_similarity",
        ),
        "Vector store returned an invalid candidate type.",
    )
    text = fields[7]
    source_mappings = fields[9]
    if type(text) is not str:
        raise RetrievalValidationError(
            "Vector store returned invalid candidate text."
        )
    if len(text) > remaining_text_code_points:
        raise RetrievalLimitError(
            "Candidate text exceeds the retrieval output limit."
        )
    if type(source_mappings) is not tuple:
        raise RetrievalValidationError(
            "Vector store returned invalid candidate provenance."
        )
    if len(source_mappings) > remaining_source_mappings:
        raise RetrievalLimitError(
            "Candidate provenance exceeds the retrieval mapping limit."
        )
    return StoredRetrievalCandidate(
        source=_snapshot_source(fields[0]),
        derivation_fingerprint=fields[1],
        embedding_space_id=fields[2],
        embedding_id=fields[3],
        chunk_id=fields[4],
        ordinal=fields[5],
        kind=fields[6],
        text=text,
        page_number=fields[8],
        source_mappings=tuple(
            _snapshot_mapping(mapping) for mapping in source_mappings
        ),
        cosine_similarity=fields[10],
    )


def _snapshot_reranker_identity(value: object) -> RerankerIdentity:
    """Rebuild one reranker identity without trusting frozen storage."""

    fields = _read_exact_slots(
        value,
        RerankerIdentity,
        (
            "provider",
            "adapter_id",
            "adapter_version",
            "model_tag",
            "model_digest",
            "score_semantics",
            "identity_fingerprint",
        ),
        "Reranker identity has an invalid type.",
    )
    if not all(type(field) is str for field in fields):
        raise RetrievalValidationError("Reranker identity has an invalid type.")
    return RerankerIdentity(
        provider=fields[0],
        adapter_id=fields[1],
        adapter_version=fields[2],
        model_tag=fields[3],
        model_digest=fields[4],
        score_semantics=fields[5],
        identity_fingerprint=fields[6],
    )


def _snapshot_reranker_policy(value: object) -> RerankerPolicy:
    """Rebuild one fixed reranker policy returned by an adapter."""

    fields = _read_exact_slots(
        value,
        RerankerPolicy,
        (
            "max_inputs",
            "max_query_code_points",
            "max_input_code_points",
            "max_batch_code_points",
            "truncate",
        ),
        "Reranker policy has an invalid type.",
    )
    return RerankerPolicy(
        max_inputs=fields[0],
        max_query_code_points=fields[1],
        max_input_code_points=fields[2],
        max_batch_code_points=fields[3],
        truncate=fields[4],
    )


def _empty_result(
    scope: AttachmentScope,
    policy: RetrievalPolicy,
    metadata_filter: RetrievalMetadataFilter,
) -> RetrievalResult:
    """Build an unembedded result for a provably empty eligible corpus."""

    return RetrievalResult(
        schema_version=RETRIEVAL_SCHEMA_VERSION,
        scope=scope,
        policy=policy,
        metadata_filter=metadata_filter,
        embedding_identity=None,
        reranker_identity=None,
        candidate_count=0,
        hits=(),
    )


def _raise_sanitized_embedding_error(error: EmbeddingError) -> NoReturn:
    """Map query-embedding categories without exposing adapter messages."""

    if isinstance(error, EmbeddingLimitError):
        raise RetrievalLimitError(
            "Query embedding exceeded a retrieval resource limit."
        ) from None
    if isinstance(error, EmbeddingUnavailableError):
        raise RetrievalUnavailableError(
            "The configured query embedding service is unavailable."
        ) from None
    if isinstance(error, (EmbeddingValidationError, EmbeddingFailedError)):
        raise RetrievalFailedError(
            "The query embedding service returned no safe result."
        ) from None
    raise RetrievalFailedError(
        "The query embedding service failed without a recognized category."
    ) from None


def _matches_filter(
    candidate: StoredRetrievalCandidate,
    metadata_filter: RetrievalMetadataFilter,
) -> bool:
    """Apply the same closed exact-match predicate expected from the store."""

    return (
        (not metadata_filter.file_ids or candidate.source.file_id in metadata_filter.file_ids)
        and (
            not metadata_filter.media_types
            or candidate.source.media_type in metadata_filter.media_types
        )
        and (
            not metadata_filter.chunk_kinds
            or candidate.kind in metadata_filter.chunk_kinds
        )
        and (
            not metadata_filter.page_numbers
            or candidate.page_number in metadata_filter.page_numbers
        )
    )


def _candidate_sort_key(
    candidate: StoredRetrievalCandidate,
) -> tuple[float, str, int, str, str]:
    """Order equal cosine scores by stable ownership and chunk identities."""

    return (
        -candidate.cosine_similarity,
        candidate.source.link_id,
        candidate.ordinal,
        candidate.chunk_id,
        candidate.embedding_id,
    )


def _evidence_from_candidate(
    candidate: StoredRetrievalCandidate,
) -> RetrievalEvidence:
    """Detach citation evidence from one validated stored candidate."""

    return RetrievalEvidence(
        source=_snapshot_source(candidate.source),
        derivation_fingerprint=candidate.derivation_fingerprint,
        embedding_id=candidate.embedding_id,
        chunk_id=candidate.chunk_id,
        ordinal=candidate.ordinal,
        page_number=candidate.page_number,
        source_mappings=tuple(
            _snapshot_mapping(mapping) for mapping in candidate.source_mappings
        ),
        cosine_similarity=candidate.cosine_similarity,
    )


def _hit_tie_key(hit: RetrievalHit) -> tuple[str, int, str, str]:
    """Return a stable content-independent tie key for one consolidated hit."""

    first = hit.evidence[0]
    return (
        first.source.link_id,
        first.ordinal,
        first.chunk_id,
        hashlib.sha256(hit.text.encode("utf-8")).hexdigest(),
    )


def _reranker_item_id(hit: RetrievalHit) -> str:
    """Derive an opaque stable ID for one exact deduplicated passage."""

    digest = _canonical_digest(
        {
            "fingerprint_domain": _RERANKER_INPUT_DOMAIN,
            "kind": hit.kind,
            "text": hit.text,
        }
    )
    return f"candidate_{digest}"


class DocumentRetriever:
    """Retrieve grounded passages from an explicit scope-safe corpus.

    Exact ``(kind, text)`` equality is the v1 duplicate rule.  Case folding or
    Unicode normalization could merge source code or prose with different
    meaning, so duplicates retain original spelling and every source occurrence
    admitted to the bounded raw candidate pool.  An optional reranker receives
    only this already-closed pool;
    it must score every item exactly once and can neither add evidence nor
    rescue a passage below the cosine sufficiency threshold.
    """

    def __init__(
        self,
        embedder: QueryEmbeddingService,
        store: AtomicRetrievalStore,
        reranker: DocumentReranker | None = None,
    ) -> None:
        """Compose required query/store boundaries and an optional reranker."""

        if embedder is None or store is None:
            raise TypeError("embedder and store are required.")
        self._embedder = embedder
        self._store = store
        self._reranker = reranker

    def retrieve(
        self,
        scope: AttachmentScope,
        query: str,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        *,
        metadata_filter: RetrievalMetadataFilter = RetrievalMetadataFilter(),
        policy: RetrievalPolicy = RetrievalPolicy(),
        limits: RetrievalLimits = RetrievalLimits(),
        cancel_requested: Callable[[], bool] | None = None,
    ) -> RetrievalResult:
        """Return deterministic grounded Top-K hits or a truthful empty result.

        An empty eligible corpus returns before invoking the embedder, store,
        or reranker.  For a non-empty corpus, absence of candidates above the
        minimum cosine threshold also returns no hits; there is deliberately no
        fallback passage from which a later answer layer could invent support.
        """

        if cancel_requested is not None and not callable(cancel_requested):
            raise RetrievalValidationError(
                "cancel_requested must be callable or None."
            )
        _raise_if_retrieval_cancelled(cancel_requested)
        canonical_scope = _snapshot_scope(scope)
        canonical_policy = _snapshot_policy(policy)
        canonical_limits = _snapshot_limits(limits)
        canonical_filter = _snapshot_filter(
            metadata_filter,
            max_filter_values=canonical_limits.max_filter_values,
        )
        canonical_query = _validate_safe_text(
            query,
            "query",
            maximum=canonical_limits.max_query_code_points,
            meaningful=True,
        )
        if canonical_policy.candidate_k > canonical_limits.max_scanned_records:
            raise RetrievalValidationError(
                "candidate_k cannot exceed max_scanned_records."
            )
        total_filter_values = sum(
            len(values)
            for values in (
                canonical_filter.file_ids,
                canonical_filter.media_types,
                canonical_filter.chunk_kinds,
                canonical_filter.page_numbers,
            )
        )
        if total_filter_values > canonical_limits.max_filter_values:
            raise RetrievalLimitError(
                "Metadata filters exceed the configured value limit."
            )
        if type(expected_documents) is not tuple:
            raise RetrievalValidationError(
                "expected_documents must be an exact tuple."
            )
        if len(expected_documents) > canonical_limits.max_documents:
            raise RetrievalLimitError(
                "Expected document generations exceed the corpus limit."
            )
        generations = tuple(
            _snapshot_expected_generation(value)
            for value in expected_documents
        )
        link_ids = tuple(item.source.link_id for item in generations)
        if len(set(link_ids)) != len(link_ids):
            raise RetrievalValidationError(
                "Expected document link IDs must be unique."
            )
        if any(item.source.scope != canonical_scope for item in generations):
            raise RetrievalValidationError(
                "Expected documents must belong to the requested scope."
            )
        generations = tuple(
            sorted(generations, key=lambda item: item.source.link_id)
        )
        eligible_generations = tuple(
            item
            for item in generations
            if (
                not canonical_filter.file_ids
                or item.source.file_id in canonical_filter.file_ids
            )
            and (
                not canonical_filter.media_types
                or item.source.media_type in canonical_filter.media_types
            )
        )
        if not eligible_generations:
            _raise_if_retrieval_cancelled(cancel_requested)
            return _empty_result(
                canonical_scope,
                canonical_policy,
                canonical_filter,
            )

        embedded_query = self._embed_query(
            canonical_query,
            cancel_requested=cancel_requested,
        )
        _raise_if_retrieval_cancelled(cancel_requested)
        candidates = self._search_store(
            canonical_scope,
            embedded_query,
            eligible_generations,
            canonical_filter,
            canonical_policy,
            canonical_limits,
            cancel_requested=cancel_requested,
        )
        _raise_if_retrieval_cancelled(cancel_requested)
        hits = self._consolidate_candidates(
            candidates,
            canonical_scope,
            embedded_query,
            eligible_generations,
            canonical_filter,
            canonical_policy,
            canonical_limits,
        )
        reranker_identity: RerankerIdentity | None = None
        if hits and self._reranker is not None:
            hits, reranker_identity = self._rerank(
                canonical_query,
                hits,
                cancel_requested=cancel_requested,
            )
        _raise_if_retrieval_cancelled(cancel_requested)
        selected = hits[:canonical_policy.top_k]
        return RetrievalResult(
            schema_version=RETRIEVAL_SCHEMA_VERSION,
            scope=_snapshot_scope(canonical_scope),
            policy=_snapshot_policy(canonical_policy),
            metadata_filter=_snapshot_filter(
                canonical_filter,
                max_filter_values=canonical_limits.max_filter_values,
            ),
            embedding_identity=_snapshot_identity(embedded_query.identity),
            reranker_identity=reranker_identity,
            candidate_count=len(hits),
            hits=selected,
        )

    def _embed_query(
        self,
        query: str,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> EmbeddedQuery:
        """Call and validate the injected query embedder with sanitized errors."""

        try:
            embedded = (
                self._embedder.embed_query(query)
                if cancel_requested is None
                else self._embedder.embed_query(
                    query,
                    cancel_requested=cancel_requested,
                )
            )
            _raise_if_retrieval_cancelled(cancel_requested)
            return _snapshot_embedded_query(embedded)
        except DocumentOperationCancelledError:
            raise
        except EmbeddingError as error:
            _raise_sanitized_embedding_error(error)
        except RetrievalError:
            raise RetrievalFailedError(
                "The query embedding service returned no safe result."
            ) from None
        except (MemoryError, RecursionError):
            raise RetrievalLimitError(
                "Query embedding exceeded a safe resource limit."
            ) from None
        except Exception:
            raise RetrievalFailedError(
                "The query embedding service failed without a safe result."
            ) from None

    def _search_store(
        self,
        scope: AttachmentScope,
        query: EmbeddedQuery,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        metadata_filter: RetrievalMetadataFilter,
        policy: RetrievalPolicy,
        limits: RetrievalLimits,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> tuple[StoredRetrievalCandidate, ...]:
        """Call one atomic store snapshot and sanitize every failure message."""

        store_scope = _snapshot_scope(scope)
        store_query = _snapshot_embedded_query(query)
        store_documents = tuple(
            _snapshot_expected_generation(item)
            for item in expected_documents
        )
        store_filter = _snapshot_filter(
            metadata_filter,
            max_filter_values=limits.max_filter_values,
        )
        store_policy = _snapshot_policy(policy)
        store_limits = _snapshot_limits(limits)
        try:
            _raise_if_retrieval_cancelled(cancel_requested)
            result = self._store.search_scope(
                scope=store_scope,
                query=store_query,
                expected_documents=store_documents,
                metadata_filter=store_filter,
                policy=store_policy,
                limits=store_limits,
            )
            _raise_if_retrieval_cancelled(cancel_requested)
        except DocumentOperationCancelledError:
            raise
        except RetrievalError:
            raise RetrievalFailedError(
                "The vector store returned no safe retrieval result."
            ) from None
        except DocumentLimitError:
            raise RetrievalLimitError(
                "The vector search exceeded a retrieval resource limit."
            ) from None
        except DocumentError:
            raise RetrievalFailedError(
                "The vector store could not complete the exact search."
            ) from None
        except (MemoryError, RecursionError):
            raise RetrievalLimitError(
                "Vector retrieval exceeded a safe resource limit."
            ) from None
        except Exception:
            raise RetrievalFailedError(
                "The vector store failed without a safe result."
            ) from None
        try:
            if type(result) is not tuple:
                raise RetrievalValidationError(
                    "Vector store returned an invalid result container."
                )
            if len(result) > policy.candidate_k:
                raise RetrievalValidationError(
                    "Vector store returned too many candidates."
                )
            remaining_text = limits.max_candidate_text_code_points
            remaining_mappings = limits.max_source_mappings
            candidates: list[StoredRetrievalCandidate] = []
            for item in result:
                candidate = _snapshot_candidate(
                    item,
                    remaining_text_code_points=remaining_text,
                    remaining_source_mappings=remaining_mappings,
                )
                candidates.append(candidate)
                remaining_text -= len(candidate.text)
                remaining_mappings -= len(candidate.source_mappings)
            return tuple(candidates)
        except RetrievalLimitError:
            raise
        except RetrievalError:
            raise RetrievalFailedError(
                "The vector store returned no safe retrieval result."
            ) from None
        except (MemoryError, RecursionError):
            raise RetrievalLimitError(
                "Vector retrieval exceeded a safe resource limit."
            ) from None
        except Exception:
            raise RetrievalFailedError(
                "The vector store returned no safe retrieval result."
            ) from None

    def _consolidate_candidates(
        self,
        candidates: tuple[StoredRetrievalCandidate, ...],
        scope: AttachmentScope,
        query: EmbeddedQuery,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        metadata_filter: RetrievalMetadataFilter,
        policy: RetrievalPolicy,
        limits: RetrievalLimits,
    ) -> tuple[RetrievalHit, ...]:
        """Validate, deterministically sort, and exactly deduplicate candidates."""

        expected_by_link = {
            item.source.link_id: item for item in expected_documents
        }
        seen_candidates: set[tuple[str, str, str]] = set()
        total_text = 0
        total_mappings = 0
        validated: list[StoredRetrievalCandidate] = []
        for candidate in candidates:
            expected = expected_by_link.get(candidate.source.link_id)
            if (
                candidate.source.scope != scope
                or expected is None
                or candidate.source != expected.source
                or candidate.derivation_fingerprint
                != expected.derivation_fingerprint
                or candidate.embedding_space_id
                != query.identity.embedding_space_id
                or not _matches_filter(candidate, metadata_filter)
                or candidate.cosine_similarity
                < policy.minimum_cosine_similarity
            ):
                raise RetrievalFailedError(
                    "The vector store returned a candidate outside the request."
                )
            identity_key = (
                candidate.source.link_id,
                candidate.derivation_fingerprint,
                candidate.embedding_id,
            )
            if identity_key in seen_candidates:
                raise RetrievalFailedError(
                    "The vector store returned a duplicate candidate identity."
                )
            seen_candidates.add(identity_key)
            total_text += len(candidate.text)
            total_mappings += len(candidate.source_mappings)
            if total_text > limits.max_candidate_text_code_points:
                raise RetrievalLimitError(
                    "Candidate text exceeds the retrieval output limit."
                )
            if total_mappings > limits.max_source_mappings:
                raise RetrievalLimitError(
                    "Candidate provenance exceeds the retrieval mapping limit."
                )
            validated.append(candidate)

        # Sorting again prevents row order, query plans, or an injected store
        # from making ties nondeterministic across otherwise identical calls.
        validated.sort(key=_candidate_sort_key)
        groups: dict[
            tuple[DocumentChunkKind, str],
            list[StoredRetrievalCandidate],
        ] = {}
        for candidate in validated:
            groups.setdefault((candidate.kind, candidate.text), []).append(
                candidate
            )
        hits = tuple(
            RetrievalHit(
                kind=key[0],
                text=key[1],
                evidence=tuple(
                    _evidence_from_candidate(candidate)
                    for candidate in group
                ),
                cosine_similarity=max(
                    candidate.cosine_similarity for candidate in group
                ),
            )
            for key, group in groups.items()
        )
        return tuple(
            sorted(
                hits,
                key=lambda hit: (-hit.cosine_similarity, *_hit_tie_key(hit)),
            )
        )

    def _rerank(
        self,
        query: str,
        hits: tuple[RetrievalHit, ...],
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> tuple[tuple[RetrievalHit, ...], RerankerIdentity]:
        """Apply a complete score permutation or fail closed and sanitized."""

        reranker = self._reranker
        if reranker is None:
            raise AssertionError("Configured reranker unexpectedly disappeared.")
        try:
            _raise_if_retrieval_cancelled(cancel_requested)
            identity = _snapshot_reranker_identity(reranker.identity)
            policy = _snapshot_reranker_policy(reranker.policy)
            request_policy = _snapshot_reranker_policy(policy)
            inputs = tuple(
                RerankerInput(item_id=_reranker_item_id(hit), text=hit.text)
                for hit in hits
            )
            request = RerankerRequest(
                schema_version=RERANKER_SCHEMA_VERSION,
                query=query,
                inputs=inputs,
                policy=request_policy,
            )
            expected_ids = tuple(item.item_id for item in inputs)
            expected_texts = tuple(item.text for item in inputs)
            expected_request_fingerprint = request.request_fingerprint
            batch = reranker.rerank(request)
            _raise_if_retrieval_cancelled(cancel_requested)
            if (
                type(batch) is not RerankerBatch
                or type(batch.identity) is not RerankerIdentity
                or type(batch.policy) is not RerankerPolicy
                or type(batch.request_fingerprint) is not str
                or type(batch.scores) is not tuple
                or len(batch.scores) != len(expected_ids)
            ):
                raise RetrievalValidationError(
                    "Reranker returned an invalid batch type."
                )
            snapshot_batch = RerankerBatch(
                schema_version=batch.schema_version,
                identity=_snapshot_reranker_identity(batch.identity),
                policy=_snapshot_reranker_policy(batch.policy),
                request_fingerprint=batch.request_fingerprint,
                scores=tuple(
                    RerankerScore(
                        item_id=score.item_id,
                        relevance=score.relevance,
                    )
                    for score in batch.scores
                    if type(score) is RerankerScore
                ),
            )
            if len(snapshot_batch.scores) != len(batch.scores):
                raise RetrievalValidationError(
                    "Reranker returned an invalid score type."
                )
            if (
                snapshot_batch.identity != identity
                or snapshot_batch.policy != policy
                or snapshot_batch.request_fingerprint
                != expected_request_fingerprint
                or _snapshot_reranker_identity(reranker.identity) != identity
                or _snapshot_reranker_policy(reranker.policy) != policy
            ):
                raise RetrievalValidationError(
                    "Reranker changed identity, policy, or request lineage."
                )
            (
                request_schema,
                request_query,
                request_inputs,
                returned_request_policy,
                returned_request_fingerprint,
            ) = _read_exact_slots(
                request,
                RerankerRequest,
                (
                    "schema_version",
                    "query",
                    "inputs",
                    "policy",
                    "request_fingerprint",
                ),
                "Reranker mutated the request during scoring.",
            )
            if (
                type(request_schema) is not int
                or request_schema != RERANKER_SCHEMA_VERSION
                or type(request_query) is not str
                or request_query != query
                or type(request_inputs) is not tuple
                or len(request_inputs) != len(expected_ids)
                or type(returned_request_fingerprint) is not str
                or returned_request_fingerprint
                != expected_request_fingerprint
                or _snapshot_reranker_policy(returned_request_policy)
                != policy
            ):
                raise RetrievalValidationError(
                    "Reranker mutated the request during scoring."
                )
            returned_request_inputs: list[RerankerInput] = []
            for item in request_inputs:
                item_id, text = _read_exact_slots(
                    item,
                    RerankerInput,
                    ("item_id", "text"),
                    "Reranker mutated the request during scoring.",
                )
                if type(item_id) is not str or type(text) is not str:
                    raise RetrievalValidationError(
                        "Reranker mutated the request during scoring."
                    )
                returned_request_inputs.append(
                    RerankerInput(item_id=item_id, text=text)
                )
            if (
                tuple(item.item_id for item in returned_request_inputs)
                != expected_ids
                or tuple(item.text for item in returned_request_inputs)
                != expected_texts
            ):
                raise RetrievalValidationError(
                    "Reranker mutated the request during scoring."
                )
            returned_ids = tuple(score.item_id for score in snapshot_batch.scores)
            if returned_ids != expected_ids:
                raise RetrievalValidationError(
                    "Reranker must score every candidate once in request order."
                )
            scores = {
                score.item_id: score.relevance
                for score in snapshot_batch.scores
            }
            scored = tuple(
                RetrievalHit(
                    kind=hit.kind,
                    text=hit.text,
                    evidence=hit.evidence,
                    cosine_similarity=hit.cosine_similarity,
                    reranker_score=scores[_reranker_item_id(hit)],
                )
                for hit in hits
            )
            ordered = tuple(
                sorted(
                    scored,
                    key=lambda hit: (
                        -(
                            hit.reranker_score
                            if hit.reranker_score is not None
                            else -1.0
                        ),
                        -hit.cosine_similarity,
                        *_hit_tie_key(hit),
                    ),
                )
            )
            return ordered, identity
        except DocumentOperationCancelledError:
            raise
        except (MemoryError, RecursionError):
            raise RetrievalLimitError(
                "Reranking exceeded a safe resource limit."
            ) from None
        except Exception:
            # A configured reranker is part of the requested ranking contract.
            # Falling back to cosine after it fails would silently publish a
            # different policy, while its message may contain query or passage
            # text.  Fail closed and replace all adapter-authored diagnostics.
            raise RerankerFailedError(
                "The configured reranker returned no safe complete result."
            ) from None
