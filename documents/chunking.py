"""Create deterministic structure-aware chunks from cleaned documents.

The chunker is a pure, versioned derivation step.  It measures every length
and offset in Unicode code points, never calls a tokenizer or embedding model,
and retains mappings back to the validated ``LoadedDocument`` block values
carried by :mod:`documents.cleaning`.  Table blocks use a canonical JSON-lines
projection whose generated punctuation is versioned with the chunker.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import re
from typing import Final, Literal, TypeAlias, cast

from attachments.domain import MAX_JSON_SAFE_INTEGER

from .cleaning import (
    CLEANED_DOCUMENT_SCHEMA_VERSION,
    CleanedDocument,
    CleanedTableBlock,
    CleanedTextBlock,
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
    LoadedDocumentProvenance,
)
from .domain import DocumentLoadLimits, DocumentTitle
from .exceptions import (
    DocumentContentLimitError,
    DocumentError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)


CHUNKED_DOCUMENT_SCHEMA_VERSION: Final[Literal[1]] = 1
STRUCTURE_AWARE_CHUNKER_ID: Final = "structure-codepoint"
STRUCTURE_AWARE_CHUNKER_VERSION: Final = "1.0.0"
TABLE_PROJECTION_VERSION: Final = "jsonl-v1"

DocumentChunkKind: TypeAlias = Literal["prose", "code", "table"]
ChunkSourceSpan: TypeAlias = DocumentTextSpan | DocumentTableCellSpan

_CHUNK_KINDS: Final = ("prose", "code", "table")
_PROSE_SEPARATOR: Final = "\n\n"
_SENTENCE_ENDINGS: Final = frozenset(".!?。！？")
_ASCII_HORIZONTAL_WHITESPACE: Final = frozenset({" ", "\t"})
_LINE_BREAKS: Final = frozenset({"\r", "\n", "\u2028", "\u2029"})
_TABLE_ESCAPES: Final = {
    '"': '\\"',
    "\\": "\\\\",
    "\t": "\\t",
    "\n": "\\n",
    "\r": "\\r",
}
_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_ID_PATTERN: Final = re.compile(r"^chunk_[0-9a-f]{64}$")
_DERIVATION_FINGERPRINT_DOMAIN: Final = (
    "elysia.document-chunk-derivation.v1"
)
_CHUNK_ID_DOMAIN: Final = "elysia.document-chunk.v1"


def _require_non_negative_safe_integer(value: object, field_name: str) -> int:
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


def _require_positive_safe_integer(value: object, field_name: str) -> int:
    """Return one exact positive JSON-safe integer."""

    parsed = _require_non_negative_safe_integer(value, field_name)
    if parsed == 0:
        raise DocumentValidationError(
            f"{field_name} must be a positive JSON-safe integer."
        )
    return parsed


def _validate_optional_page_number(value: object) -> int | None:
    """Validate one optional one-based page number."""

    if value is None:
        return None
    return _require_positive_safe_integer(value, "page_number")


def _validate_identifier(value: object, field_name: str) -> str:
    """Validate one stable lowercase producer identifier."""

    if (
        not isinstance(value, str)
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise DocumentValidationError(
            f"{field_name} must be a stable lowercase identifier."
        )
    return value


def _validate_version(value: object, field_name: str) -> str:
    """Validate one stable producer version."""

    if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
        raise DocumentValidationError(
            f"{field_name} must be a stable non-empty identifier."
        )
    return value


@dataclass(frozen=True, slots=True)
class DocumentChunkingPolicy:
    """Record every caller-visible boundary choice used by chunking.

    Overlap is deliberately fixed at zero in v1.  Exact source partitioning
    makes citation offsets and resource accounting unambiguous; adding overlap
    later requires a new chunker version rather than a silent policy change.
    """

    max_chunk_code_points: int = 2_000
    overlap_code_points: Literal[0] = 0
    prose_separator: Literal["\n\n"] = _PROSE_SEPARATOR
    table_projection_version: Literal["jsonl-v1"] = TABLE_PROJECTION_VERSION

    def __post_init__(self) -> None:
        """Require the v1 fixed policy and one exact positive chunk cap."""

        _require_positive_safe_integer(
            self.max_chunk_code_points,
            "max_chunk_code_points",
        )
        if type(self.overlap_code_points) is not int or self.overlap_code_points != 0:
            raise DocumentValidationError(
                "Chunk overlap must be exactly zero in chunker v1."
            )
        if self.prose_separator != _PROSE_SEPARATOR:
            raise DocumentValidationError(
                "Chunker v1 requires the canonical prose separator."
            )
        if self.table_projection_version != TABLE_PROJECTION_VERSION:
            raise DocumentValidationError(
                "Chunker v1 requires the canonical table projection."
            )


@dataclass(frozen=True, slots=True)
class ChunkSourceMapping:
    """Map a chunk-local half-open range to one cleaned source range.

    Text mappings preserve equal lengths.  A table mapping may cover a longer
    escaped projection or a zero-length source cell because JSON punctuation
    and escaping are derived text rather than characters in the loaded cell.
    """

    chunk_start_code_point: int
    chunk_end_code_point: int
    source_span: ChunkSourceSpan

    def __post_init__(self) -> None:
        """Validate one non-empty chunk range and a public source-span value."""

        start = _require_non_negative_safe_integer(
            self.chunk_start_code_point,
            "chunk_start_code_point",
        )
        end = _require_non_negative_safe_integer(
            self.chunk_end_code_point,
            "chunk_end_code_point",
        )
        if end <= start:
            raise DocumentValidationError(
                "A chunk source mapping must cover a non-empty chunk range."
            )
        if not isinstance(
            self.source_span,
            (DocumentTextSpan, DocumentTableCellSpan),
        ):
            raise DocumentValidationError(
                "source_span must be a public cleaning provenance span."
            )
        if isinstance(self.source_span, DocumentTextSpan) and (
            end - start
            != self.source_span.end_code_point
            - self.source_span.start_code_point
        ):
            raise DocumentValidationError(
                "A text mapping must preserve its exact code-point length."
            )


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """Carry one bounded derived text unit and all of its source mappings."""

    ordinal: int
    chunk_id: str
    kind: DocumentChunkKind
    text: str
    page_number: int | None
    source_mappings: tuple[ChunkSourceMapping, ...]

    def __post_init__(self) -> None:
        """Validate local identity, content, page, and mapping invariants."""

        _require_non_negative_safe_integer(self.ordinal, "ordinal")
        if (
            not isinstance(self.chunk_id, str)
            or _CHUNK_ID_PATTERN.fullmatch(self.chunk_id) is None
        ):
            raise DocumentValidationError(
                "chunk_id must contain one lowercase SHA-256 digest."
            )
        if not isinstance(self.kind, str) or self.kind not in _CHUNK_KINDS:
            raise DocumentValidationError("Document chunk kind is invalid.")
        if not isinstance(self.text, str) or not self.text:
            raise DocumentValidationError(
                "Document chunk text must be a non-empty string."
            )
        _validate_optional_page_number(self.page_number)
        if (
            not isinstance(self.source_mappings, tuple)
            or not self.source_mappings
            or not all(
                isinstance(mapping, ChunkSourceMapping)
                for mapping in self.source_mappings
            )
        ):
            raise DocumentValidationError(
                "source_mappings must contain ChunkSourceMapping values."
            )
        previous_end = 0
        previous_block = -1
        previous_span: ChunkSourceSpan | None = None
        for mapping in self.source_mappings:
            if (
                mapping.chunk_start_code_point < previous_end
                or mapping.chunk_end_code_point > len(self.text)
            ):
                raise DocumentValidationError(
                    "Chunk source mappings must be ordered and in bounds."
                )
            span = mapping.source_span
            if span.page_number != self.page_number:
                raise DocumentValidationError(
                    "A chunk source mapping changed its source page."
                )
            if span.block_ordinal < previous_block:
                raise DocumentValidationError(
                    "Chunk source blocks must remain in document order."
                )
            if self.kind == "table":
                if not isinstance(span, DocumentTableCellSpan):
                    raise DocumentValidationError(
                        "Table chunks require table-cell source mappings."
                    )
            elif not isinstance(span, DocumentTextSpan):
                raise DocumentValidationError(
                    "Text chunks require text-block source mappings."
                )
            if mapping.chunk_start_code_point > previous_end:
                gap = self.text[
                    previous_end:mapping.chunk_start_code_point
                ]
                if self.kind != "prose" or gap != _PROSE_SEPARATOR:
                    raise DocumentValidationError(
                        "Only the canonical prose separator may be unmapped."
                    )
            if previous_span is not None and (
                span.block_ordinal == previous_span.block_ordinal
            ):
                if isinstance(span, DocumentTextSpan) and isinstance(
                    previous_span,
                    DocumentTextSpan,
                ):
                    if span.start_code_point < previous_span.end_code_point:
                        raise DocumentValidationError(
                            "Text source mappings must move forward."
                        )
                elif isinstance(span, DocumentTableCellSpan) and isinstance(
                    previous_span,
                    DocumentTableCellSpan,
                ):
                    current_position = (
                        span.row_index,
                        span.column_index,
                        span.start_code_point,
                    )
                    previous_position = (
                        previous_span.row_index,
                        previous_span.column_index,
                        previous_span.end_code_point,
                    )
                    if current_position < previous_position:
                        raise DocumentValidationError(
                            "Table source mappings must move forward."
                        )
            previous_end = mapping.chunk_end_code_point
            previous_block = span.block_ordinal
            previous_span = span
        if (
            self.source_mappings[0].chunk_start_code_point != 0
            or previous_end != len(self.text)
        ):
            raise DocumentValidationError(
                "Chunk source mappings must cover the complete chunk."
            )


@dataclass(frozen=True, slots=True)
class ChunkedDocument:
    """Publish one validated versioned chunk derivation without embeddings."""

    schema_version: Literal[1]
    cleaned_schema_version: Literal[1]
    provenance: LoadedDocumentProvenance
    cleaner_id: str
    cleaner_version: str
    document_fingerprint: str
    cleaning_fingerprint: str
    chunker_id: str
    chunker_version: str
    policy: DocumentChunkingPolicy
    limits: DocumentProcessingLimits
    title: DocumentTitle | None
    page_count: int | None
    derivation_fingerprint: str
    chunks: tuple[DocumentChunk, ...]

    def __post_init__(self) -> None:
        """Validate lineage, budgets, pages, fingerprints, and chunk IDs."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != CHUNKED_DOCUMENT_SCHEMA_VERSION
        ):
            raise DocumentValidationError(
                "Unsupported chunked-document schema version."
            )
        if (
            type(self.cleaned_schema_version) is not int
            or self.cleaned_schema_version != CLEANED_DOCUMENT_SCHEMA_VERSION
        ):
            raise DocumentValidationError(
                "Unsupported cleaned-document schema version."
            )
        if not isinstance(self.provenance, LoadedDocumentProvenance):
            raise DocumentValidationError(
                "provenance must be LoadedDocumentProvenance."
            )
        _validate_identifier(self.cleaner_id, "cleaner_id")
        _validate_version(self.cleaner_version, "cleaner_version")
        _validate_identifier(self.chunker_id, "chunker_id")
        _validate_version(self.chunker_version, "chunker_version")
        for field_name in ("document_fingerprint", "cleaning_fingerprint"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or _DIGEST_PATTERN.fullmatch(value) is None
            ):
                raise DocumentValidationError(
                    f"{field_name} must be a lowercase SHA-256 digest."
                )
        if not isinstance(self.policy, DocumentChunkingPolicy):
            raise DocumentValidationError(
                "policy must be DocumentChunkingPolicy."
            )
        if not isinstance(self.limits, DocumentProcessingLimits):
            raise DocumentValidationError(
                "limits must be DocumentProcessingLimits."
            )
        if self.policy.max_chunk_code_points > self.limits.max_total_chunk_code_points:
            raise DocumentValidationError(
                "Chunk cap cannot exceed the total chunk-output limit."
            )
        if self.title is not None and not isinstance(self.title, DocumentTitle):
            raise DocumentValidationError("title must be DocumentTitle or None.")
        self._validate_pages()
        if (
            not isinstance(self.derivation_fingerprint, str)
            or _DIGEST_PATTERN.fullmatch(self.derivation_fingerprint) is None
        ):
            raise DocumentValidationError(
                "derivation_fingerprint must be a lowercase SHA-256 digest."
            )
        expected_derivation = _build_derivation_fingerprint(
            cleaned_schema_version=self.cleaned_schema_version,
            provenance=self.provenance,
            cleaner_id=self.cleaner_id,
            cleaner_version=self.cleaner_version,
            document_fingerprint=self.document_fingerprint,
            cleaning_fingerprint=self.cleaning_fingerprint,
            chunker_id=self.chunker_id,
            chunker_version=self.chunker_version,
            policy=self.policy,
            limits=self.limits,
            title=self.title,
            page_count=self.page_count,
        )
        if self.derivation_fingerprint != expected_derivation:
            raise DocumentValidationError(
                "derivation_fingerprint does not match the chunk lineage."
            )
        self._validate_chunks()

    def _validate_pages(self) -> None:
        """Preserve real PDF pages and reject invented pagination."""

        if self.provenance.document_format == "pdf":
            _require_positive_safe_integer(self.page_count, "page_count")
            return
        if self.page_count is not None:
            raise DocumentValidationError(
                "Only chunked PDF documents can carry page metadata."
            )

    def _validate_chunks(self) -> None:
        """Enforce ordered chunks and every configured object-graph budget."""

        if (
            not isinstance(self.chunks, tuple)
            or not all(isinstance(chunk, DocumentChunk) for chunk in self.chunks)
        ):
            raise DocumentValidationError(
                "chunks must contain only DocumentChunk values."
            )
        if tuple(chunk.ordinal for chunk in self.chunks) != tuple(
            range(len(self.chunks))
        ):
            raise DocumentValidationError(
                "Chunk ordinals must be contiguous from zero."
            )
        if len(self.chunks) > self.limits.max_chunks:
            raise DocumentContentLimitError(
                "Document chunk count exceeds the configured limit."
            )
        total_code_points = 0
        total_mappings = 0
        seen_ids: set[str] = set()
        for chunk in self.chunks:
            if len(chunk.text) > self.policy.max_chunk_code_points:
                raise DocumentContentLimitError(
                    "Document chunk exceeds the configured code-point limit."
                )
            if (
                len(chunk.source_mappings)
                > self.limits.max_source_mappings_per_chunk
            ):
                raise DocumentContentLimitError(
                    "Document chunk exceeds its source-mapping limit."
                )
            if self.provenance.document_format == "pdf":
                if (
                    chunk.page_number is None
                    or self.page_count is None
                    or chunk.page_number > self.page_count
                ):
                    raise DocumentValidationError(
                        "A PDF chunk has invalid source-page provenance."
                    )
            elif chunk.page_number is not None:
                raise DocumentValidationError(
                    "Only PDF chunks can carry page metadata."
                )
            expected_id = _build_chunk_id(
                self.derivation_fingerprint,
                ordinal=chunk.ordinal,
                kind=chunk.kind,
                text=chunk.text,
                page_number=chunk.page_number,
                mappings=chunk.source_mappings,
            )
            if chunk.chunk_id != expected_id:
                raise DocumentValidationError(
                    "chunk_id does not match the chunk derivation."
                )
            if chunk.chunk_id in seen_ids:
                raise DocumentValidationError("Document chunk IDs must be unique.")
            seen_ids.add(chunk.chunk_id)
            total_code_points += len(chunk.text)
            total_mappings += len(chunk.source_mappings)
            if total_code_points > self.limits.max_total_chunk_code_points:
                raise DocumentContentLimitError(
                    "Document chunks exceed the total code-point limit."
                )
            if total_mappings > self.limits.max_total_source_mappings:
                raise DocumentContentLimitError(
                    "Document chunks exceed the total source-mapping limit."
                )


@dataclass(slots=True)
class _PendingChunk:
    """Accumulate one bounded chunk before its stable identity is known."""

    kind: DocumentChunkKind
    page_number: int | None
    parts: list[str]
    length: int
    mappings: list[tuple[int, int, ChunkSourceSpan]]


class _ChunkEmitter:
    """Enforce aggregate budgets while incrementally publishing chunks."""

    def __init__(
        self,
        *,
        derivation_fingerprint: str,
        policy: DocumentChunkingPolicy,
        limits: DocumentProcessingLimits,
    ) -> None:
        self.derivation_fingerprint = derivation_fingerprint
        self.policy = policy
        self.limits = limits
        self.chunks: list[DocumentChunk] = []
        self.pending: _PendingChunk | None = None
        self.output_code_points = 0
        self.source_mappings = 0

    @property
    def remaining(self) -> int:
        """Return code-point capacity remaining in the pending chunk."""

        if self.pending is None:
            return self.policy.max_chunk_code_points
        return self.policy.max_chunk_code_points - self.pending.length

    def flush(self) -> None:
        """Publish the current non-empty chunk after all budget checks."""

        pending = self.pending
        if pending is None:
            return
        if len(self.chunks) >= self.limits.max_chunks:
            raise DocumentContentLimitError(
                "Document chunk count exceeds the configured limit."
            )
        if not pending.mappings:
            raise DocumentValidationError(
                "A pending chunk has no source provenance."
            )
        text = "".join(pending.parts)
        mappings = tuple(
            ChunkSourceMapping(start, end, span)
            for start, end, span in pending.mappings
        )
        ordinal = len(self.chunks)
        chunk_id = _build_chunk_id(
            self.derivation_fingerprint,
            ordinal=ordinal,
            kind=pending.kind,
            text=text,
            page_number=pending.page_number,
            mappings=mappings,
        )
        self.chunks.append(
            DocumentChunk(
                ordinal=ordinal,
                chunk_id=chunk_id,
                kind=pending.kind,
                text=text,
                page_number=pending.page_number,
                source_mappings=mappings,
            )
        )
        self.output_code_points += pending.length
        self.source_mappings += len(mappings)
        self.pending = None

    def _reserve_pending_slot(self) -> None:
        """Reject a new pending chunk before allocating its object graph."""

        if self.pending is None and len(self.chunks) >= self.limits.max_chunks:
            raise DocumentContentLimitError(
                "Document chunk count exceeds the configured limit."
            )

    def append_prose(
        self,
        text: str,
        mappings: tuple[ChunkSourceMapping, ...],
        *,
        page_number: int | None,
        starts_structure: bool,
        continues_block: bool,
    ) -> None:
        """Pack one prose fragment with the fixed inter-block separator."""

        self._reserve_pending_slot()
        if starts_structure or continues_block:
            self.flush()
            self._reserve_pending_slot()
        separator = ""
        if self.pending is not None:
            if self.pending.kind != "prose" or self.pending.page_number != page_number:
                self.flush()
            else:
                separator = _PROSE_SEPARATOR
        required = len(separator) + len(text)
        if self.pending is not None and (
            required > self.remaining
            or len(self.pending.mappings) + len(mappings)
            > self.limits.max_source_mappings_per_chunk
        ):
            self.flush()
            self._reserve_pending_slot()
            separator = ""
        self._append(
            kind="prose",
            page_number=page_number,
            text=separator + text,
            mappings=mappings,
            mapping_shift=len(separator),
        )

    def emit_isolated(
        self,
        kind: Literal["code", "table"],
        text: str,
        mappings: tuple[ChunkSourceMapping, ...],
        *,
        page_number: int | None,
    ) -> None:
        """Emit one code fragment without crossing any structural boundary."""

        self.flush()
        self._reserve_pending_slot()
        self._append(
            kind=kind,
            page_number=page_number,
            text=text,
            mappings=mappings,
            mapping_shift=0,
        )
        self.flush()

    def append_table_piece(
        self,
        text: str,
        source_span: DocumentTableCellSpan,
        *,
        page_number: int | None,
        prefer_boundary: bool = False,
    ) -> None:
        """Append one projected table piece, splitting only when unavoidable."""

        if not text:
            return
        self._reserve_pending_slot()
        if (
            prefer_boundary
            and self.pending is not None
            and len(text) <= self.policy.max_chunk_code_points
            and len(text) > self.remaining
        ):
            self.flush()
            self._reserve_pending_slot()
        cursor = 0
        while cursor < len(text):
            if self.pending is not None and (
                self.pending.kind != "table"
                or self.pending.page_number != page_number
            ):
                self.flush()
            if self.remaining == 0:
                self.flush()
                self._reserve_pending_slot()
            take = min(self.remaining, len(text) - cursor)
            piece = text[cursor:cursor + take]
            mapping = ChunkSourceMapping(0, len(piece), source_span)
            if self.pending is not None and (
                len(self.pending.mappings) + 1
                > self.limits.max_source_mappings_per_chunk
            ):
                self.flush()
                self._reserve_pending_slot()
                continue
            self._append(
                kind="table",
                page_number=page_number,
                text=piece,
                mappings=(mapping,),
                mapping_shift=0,
            )
            cursor += take

    def _append(
        self,
        *,
        kind: DocumentChunkKind,
        page_number: int | None,
        text: str,
        mappings: tuple[ChunkSourceMapping, ...],
        mapping_shift: int,
    ) -> None:
        """Append already bounded text after reserving aggregate resources."""

        if not text or len(text) > self.policy.max_chunk_code_points:
            raise DocumentValidationError(
                "Internal chunk fragment length is invalid."
            )
        self._reserve_pending_slot()
        if self.pending is None:
            self.pending = _PendingChunk(kind, page_number, [], 0, [])
        pending = self.pending
        if pending.kind != kind or pending.page_number != page_number:
            raise DocumentValidationError(
                "Internal chunk packing crossed a structural boundary."
            )
        if pending.length + len(text) > self.policy.max_chunk_code_points:
            raise DocumentValidationError(
                "Internal chunk packing exceeded the chunk cap."
            )
        projected_output = (
            self.output_code_points + pending.length + len(text)
        )
        if projected_output > self.limits.max_total_chunk_code_points:
            raise DocumentContentLimitError(
                "Document chunks exceed the total code-point limit."
            )
        shifted: list[tuple[int, int, ChunkSourceSpan]] = []
        base = pending.length + mapping_shift
        for mapping in mappings:
            shifted.append(
                (
                    base + mapping.chunk_start_code_point,
                    base + mapping.chunk_end_code_point,
                    mapping.source_span,
                )
            )
        projected_mapping_count = len(pending.mappings) + len(shifted)
        if projected_mapping_count > self.limits.max_source_mappings_per_chunk:
            raise DocumentContentLimitError(
                "Document chunk exceeds its source-mapping limit."
            )
        if (
            self.source_mappings + projected_mapping_count
            > self.limits.max_total_source_mappings
        ):
            raise DocumentContentLimitError(
                "Document chunks exceed the total source-mapping limit."
            )
        pending.parts.append(text)
        pending.mappings.extend(shifted)
        pending.length += len(text)


class StructureAwareDocumentChunker:
    """Deterministically chunk cleaned blocks without models or I/O."""

    def __init__(
        self,
        *,
        policy: DocumentChunkingPolicy | None = None,
    ) -> None:
        """Configure one immutable output-affecting chunking policy."""

        self._policy = (
            DocumentChunkingPolicy() if policy is None else policy
        )
        if not isinstance(self._policy, DocumentChunkingPolicy):
            raise TypeError("policy must be DocumentChunkingPolicy.")

    @property
    def chunker_id(self) -> str:
        """Return the stable producer identity for structure-aware chunks."""

        return STRUCTURE_AWARE_CHUNKER_ID

    @property
    def chunker_version(self) -> str:
        """Return the version governing boundaries, projection, and IDs."""

        return STRUCTURE_AWARE_CHUNKER_VERSION

    @property
    def policy(self) -> DocumentChunkingPolicy:
        """Return the exact output-affecting chunking policy."""

        return self._policy

    def chunk(self, document: CleanedDocument) -> ChunkedDocument:
        """Chunk one cleaned document or raise a stable typed failure."""

        try:
            if not isinstance(document, CleanedDocument):
                raise DocumentValidationError(
                    "document must be CleanedDocument."
                )
            policy = self.policy
            if (
                policy.max_chunk_code_points
                > document.limits.max_total_chunk_code_points
            ):
                raise DocumentValidationError(
                    "Chunk cap cannot exceed the total chunk-output limit."
                )
            derivation_fingerprint = _build_derivation_fingerprint(
                cleaned_schema_version=document.schema_version,
                provenance=document.provenance,
                cleaner_id=document.cleaner_id,
                cleaner_version=document.cleaner_version,
                document_fingerprint=document.document_fingerprint,
                cleaning_fingerprint=document.cleaning_fingerprint,
                chunker_id=self.chunker_id,
                chunker_version=self.chunker_version,
                policy=policy,
                limits=document.limits,
                title=document.title,
                page_count=document.page_count,
            )
            emitter = _ChunkEmitter(
                derivation_fingerprint=derivation_fingerprint,
                policy=policy,
                limits=document.limits,
            )
            for block in document.blocks:
                if isinstance(block, CleanedTableBlock):
                    emitter.flush()
                    _emit_table(block, emitter, policy.max_chunk_code_points)
                    emitter.flush()
                    continue
                if block.kind == "code":
                    emitter.flush()
                    for mapped_start, mapped_end, mappings in (
                        _iter_mapped_block_ranges(
                            block,
                            max_code_points=policy.max_chunk_code_points,
                            prose=False,
                            max_mappings=(
                                document.limits.max_source_mappings_per_chunk
                            ),
                        )
                    ):
                        emitter.emit_isolated(
                            "code",
                            block.text[mapped_start:mapped_end],
                            mappings,
                            page_number=block.page_number,
                        )
                    continue
                starts_structure = block.kind in {"title", "heading"}
                first_fragment = True
                for mapped_start, mapped_end, mappings in (
                    _iter_mapped_block_ranges(
                        block,
                        max_code_points=policy.max_chunk_code_points,
                        prose=True,
                        max_mappings=(
                            document.limits.max_source_mappings_per_chunk
                        ),
                    )
                ):
                    emitter.append_prose(
                        block.text[mapped_start:mapped_end],
                        mappings,
                        page_number=block.page_number,
                        starts_structure=(
                            starts_structure and first_fragment
                        ),
                        continues_block=not first_fragment,
                    )
                    first_fragment = False
            emitter.flush()
            return ChunkedDocument(
                schema_version=CHUNKED_DOCUMENT_SCHEMA_VERSION,
                cleaned_schema_version=document.schema_version,
                provenance=document.provenance,
                cleaner_id=document.cleaner_id,
                cleaner_version=document.cleaner_version,
                document_fingerprint=document.document_fingerprint,
                cleaning_fingerprint=document.cleaning_fingerprint,
                chunker_id=self.chunker_id,
                chunker_version=self.chunker_version,
                policy=policy,
                limits=document.limits,
                title=document.title,
                page_count=document.page_count,
                derivation_fingerprint=derivation_fingerprint,
                chunks=tuple(emitter.chunks),
            )
        except DocumentError:
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document chunking exceeded a safe resource limit."
            ) from error
        except Exception as error:
            raise DocumentProcessingFailedError(
                "Document chunking failed without a safe typed result."
            ) from error


def _line_break_end(text: str, index: int, hard_end: int) -> int | None:
    """Return one complete logical line-break end within a hard window."""

    character = text[index]
    if character not in _LINE_BREAKS:
        return None
    if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
        end = index + 2
    else:
        end = index + 1
    return end if end <= hard_end else None


def _soft_text_end(
    text: str,
    start: int,
    hard_end: int,
    *,
    prose: bool,
) -> int:
    """Choose the latest boundary in the highest-priority fixed category."""

    floor = start + 1
    paragraph_end: int | None = None
    line_end: int | None = None
    sentence_end: int | None = None
    whitespace_end: int | None = None
    previous_line_end: int | None = None
    horizontal_only_since_line = False
    index = start
    while index < hard_end:
        candidate_line_end = _line_break_end(text, index, hard_end)
        if candidate_line_end is not None:
            if candidate_line_end >= floor:
                line_end = candidate_line_end
            if (
                previous_line_end is not None
                and horizontal_only_since_line
                and candidate_line_end >= floor
            ):
                paragraph_end = candidate_line_end
            previous_line_end = candidate_line_end
            horizontal_only_since_line = True
            index = candidate_line_end
            continue
        character = text[index]
        if previous_line_end is not None and character not in _ASCII_HORIZONTAL_WHITESPACE:
            horizontal_only_since_line = False
        if prose and character in _SENTENCE_ENDINGS and index + 1 >= floor:
            sentence_end = index + 1
        if prose and character in _ASCII_HORIZONTAL_WHITESPACE and index + 1 >= floor:
            whitespace_end = index + 1
        index += 1
    if prose and paragraph_end is not None:
        return paragraph_end
    if line_end is not None:
        return line_end
    if prose and sentence_end is not None:
        return sentence_end
    if prose and whitespace_end is not None:
        return whitespace_end
    return hard_end


def _iter_text_ranges(
    text: str,
    *,
    max_code_points: int,
    prose: bool,
):
    """Yield deterministic non-overlapping ranges bounded by code points."""

    start = 0
    while start < len(text):
        hard_end = min(start + max_code_points, len(text))
        if (
            hard_end < len(text)
            and hard_end > start + 1
            and text[hard_end - 1:hard_end + 1] == "\r\n"
        ):
            # A CRLF pair is one logical break.  Backing up is possible when
            # the window contains other text; a one-code-point cap must still
            # hard-split the pair because no valid chunk can hold it.
            hard_end -= 1
        end = (
            hard_end
            if hard_end == len(text)
            else _soft_text_end(text, start, hard_end, prose=prose)
        )
        if end <= start:
            end = hard_end
        yield start, end
        start = end


def _iter_mapped_block_ranges(
    block: CleanedTextBlock,
    *,
    max_code_points: int,
    prose: bool,
    max_mappings: int,
):
    """Map every bounded text fragment in one forward piece-table pass.

    Restarting at the first retained span for each short chunk turns a valid
    many-piece document into quadratic work.  This merge-style walk advances
    text boundaries and retained spans together, visits each span once, and
    never holds more than ``max_mappings`` public mappings at a time.
    """

    retained_spans = block.retained_spans
    span_index = 0
    cleaned_cursor = 0
    for range_start, range_end in _iter_text_ranges(
        block.text,
        max_code_points=max_code_points,
        prose=prose,
    ):
        segment_start = range_start
        cursor = range_start
        mappings: list[ChunkSourceMapping] = []
        while cursor < range_end:
            if span_index >= len(retained_spans):
                raise DocumentValidationError(
                    "Cleaned text spans do not cover their published text."
                )
            retained = retained_spans[span_index]
            retained_length = (
                retained.end_code_point - retained.start_code_point
            )
            retained_cleaned_end = cleaned_cursor + retained_length
            if cursor < cleaned_cursor:
                raise DocumentValidationError(
                    "Cleaned text span traversal moved backwards."
                )
            if cursor >= retained_cleaned_end:
                cleaned_cursor = retained_cleaned_end
                span_index += 1
                continue
            if mappings and len(mappings) == max_mappings:
                yield segment_start, cursor, tuple(mappings)
                segment_start = cursor
                mappings = []
            intersection_end = min(range_end, retained_cleaned_end)
            source_start = retained.start_code_point + cursor - cleaned_cursor
            source_end = source_start + intersection_end - cursor
            mappings.append(
                ChunkSourceMapping(
                    chunk_start_code_point=cursor - segment_start,
                    chunk_end_code_point=intersection_end - segment_start,
                    source_span=DocumentTextSpan(
                        block_ordinal=retained.block_ordinal,
                        start_code_point=source_start,
                        end_code_point=source_end,
                        page_number=retained.page_number,
                    ),
                )
            )
            cursor = intersection_end
            if cursor == retained_cleaned_end:
                cleaned_cursor = retained_cleaned_end
                span_index += 1
        if not mappings:
            raise DocumentValidationError(
                "A cleaned text fragment has no retained source mapping."
            )
        yield segment_start, range_end, tuple(mappings)
    if cleaned_cursor != len(block.text) or span_index != len(retained_spans):
        raise DocumentValidationError(
            "Cleaned text spans do not match their published text length."
        )


def _escaped_cell_text(value: str) -> str:
    """Return the canonical non-ASCII-preserving JSON string payload."""

    return "".join(_TABLE_ESCAPES.get(character, character) for character in value)


def _escaped_cell_length(value: str) -> int:
    """Count a canonical cell payload without allocating its projection."""

    return sum(2 if character in _TABLE_ESCAPES else 1 for character in value)


def _row_projection_length(row: tuple[str, ...]) -> int:
    """Return the exact code-point length of one canonical JSON row."""

    return (
        2
        + max(0, len(row) - 1)
        + 2 * len(row)
        + sum(_escaped_cell_length(cell) for cell in row)
    )


def _cell_span(
    block: CleanedTableBlock,
    row_index: int,
    column_index: int,
    start: int,
    end: int,
) -> DocumentTableCellSpan:
    """Build one table-cell provenance span for a projected slice."""

    return DocumentTableCellSpan(
        block_ordinal=block.source_block_ordinal,
        row_index=row_index,
        column_index=column_index,
        start_code_point=start,
        end_code_point=end,
        page_number=block.page_number,
    )


def _emit_large_cell_payload(
    block: CleanedTableBlock,
    emitter: _ChunkEmitter,
    *,
    row_index: int,
    column_index: int,
    value: str,
    max_code_points: int,
) -> None:
    """Project a large cell in bounded raw ranges with exact cell offsets."""

    raw_start = 0
    encoded_parts: list[str] = []
    encoded_length = 0
    for raw_index, character in enumerate(value):
        encoded = _TABLE_ESCAPES.get(character, character)
        if encoded_parts and encoded_length + len(encoded) > max_code_points:
            emitter.append_table_piece(
                "".join(encoded_parts),
                _cell_span(
                    block,
                    row_index,
                    column_index,
                    raw_start,
                    raw_index,
                ),
                page_number=block.page_number,
                prefer_boundary=True,
            )
            raw_start = raw_index
            encoded_parts = []
            encoded_length = 0
        encoded_parts.append(encoded)
        encoded_length += len(encoded)
    if encoded_parts:
        emitter.append_table_piece(
            "".join(encoded_parts),
            _cell_span(
                block,
                row_index,
                column_index,
                raw_start,
                len(value),
            ),
            page_number=block.page_number,
            prefer_boundary=True,
        )


def _emit_table(
    block: CleanedTableBlock,
    emitter: _ChunkEmitter,
    max_code_points: int,
) -> None:
    """Stream one ragged table through the versioned canonical JSONL view."""

    for row_index, row in enumerate(block.table.rows):
        row_prefix = "[" if row_index == 0 else "\n["
        row_length = _row_projection_length(row) + (0 if row_index == 0 else 1)
        if emitter.pending is not None and row_length > emitter.remaining:
            emitter.flush()
        for column_index, cell in enumerate(row):
            prefix = row_prefix if column_index == 0 else ","
            suffix = "]" if column_index == len(row) - 1 else ""
            escaped_length = _escaped_cell_length(cell)
            projected_length = len(prefix) + 2 + escaped_length + len(suffix)
            full_span = _cell_span(
                block,
                row_index,
                column_index,
                0,
                len(cell),
            )
            if projected_length <= max_code_points:
                emitter.append_table_piece(
                    f'{prefix}"{_escaped_cell_text(cell)}"{suffix}',
                    full_span,
                    page_number=block.page_number,
                    prefer_boundary=True,
                )
                continue
            start_anchor = _cell_span(
                block,
                row_index,
                column_index,
                0,
                0,
            )
            emitter.append_table_piece(
                f'{prefix}"',
                start_anchor,
                page_number=block.page_number,
            )
            _emit_large_cell_payload(
                block,
                emitter,
                row_index=row_index,
                column_index=column_index,
                value=cell,
                max_code_points=max_code_points,
            )
            end_anchor = _cell_span(
                block,
                row_index,
                column_index,
                len(cell),
                len(cell),
            )
            emitter.append_table_piece(
                f'"{suffix}',
                end_anchor,
                page_number=block.page_number,
            )


def _canonical_limits(
    limits: DocumentLoadLimits | DocumentProcessingLimits,
) -> dict[str, int]:
    """Serialize one dataclass of exact integer limits in declaration order."""

    return {
        field.name: cast(int, getattr(limits, field.name))
        for field in fields(limits)
    }


def _canonical_source_span(span: ChunkSourceSpan) -> dict[str, object]:
    """Serialize one public provenance variant without using repr."""

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


def _canonical_mapping(mapping: ChunkSourceMapping) -> dict[str, object]:
    """Serialize one chunk-local mapping for stable identity hashing."""

    return {
        "chunk_start_code_point": mapping.chunk_start_code_point,
        "chunk_end_code_point": mapping.chunk_end_code_point,
        "source_span": _canonical_source_span(mapping.source_span),
    }


def _canonical_policy(policy: DocumentChunkingPolicy) -> dict[str, object]:
    """Serialize every behavior-affecting v1 chunking policy field."""

    return {
        "max_chunk_code_points": policy.max_chunk_code_points,
        "overlap_code_points": policy.overlap_code_points,
        "prose_separator": policy.prose_separator,
        "table_projection_version": policy.table_projection_version,
        "soft_boundary_minimum_fraction": "none",
        "prose_sentence_endings": "".join(sorted(_SENTENCE_ENDINGS)),
        "prose_ascii_whitespace": " \\t",
        "line_breaks": ["CRLF", "CR", "LF", "U+2028", "U+2029"],
    }


def _canonical_json_digest(value: object) -> str:
    """Hash canonical UTF-8 JSON without locale or randomized ordering."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_derivation_fingerprint(
    *,
    cleaned_schema_version: int,
    provenance: LoadedDocumentProvenance,
    cleaner_id: str,
    cleaner_version: str,
    document_fingerprint: str,
    cleaning_fingerprint: str,
    chunker_id: str,
    chunker_version: str,
    policy: DocumentChunkingPolicy,
    limits: DocumentProcessingLimits,
    title: DocumentTitle | None,
    page_count: int | None,
) -> str:
    """Hash scope-aware lineage and every resolved chunking input."""

    source = provenance.source
    return _canonical_json_digest({
        "fingerprint_domain": _DERIVATION_FINGERPRINT_DOMAIN,
        "schema_version": CHUNKED_DOCUMENT_SCHEMA_VERSION,
        "cleaned_schema_version": cleaned_schema_version,
        "source": {
            "scope": {"kind": source.scope.kind, "id": source.scope.id},
            "link_id": source.link_id,
            "file_id": source.file_id,
            "file_name": source.file_name,
            "media_type": source.media_type,
            "size_bytes": source.size_bytes,
        },
        "loader": {
            "schema_version": provenance.loaded_schema_version,
            "document_format": provenance.document_format,
            "loader_id": provenance.loader_id,
            "loader_version": provenance.loader_version,
            "limits": _canonical_limits(provenance.load_limits),
        },
        "cleaner": {
            "cleaner_id": cleaner_id,
            "cleaner_version": cleaner_version,
            "document_fingerprint": document_fingerprint,
            "cleaning_fingerprint": cleaning_fingerprint,
        },
        "chunker": {
            "chunker_id": chunker_id,
            "chunker_version": chunker_version,
            "policy": _canonical_policy(policy),
            "processing_limits": _canonical_limits(limits),
        },
        "document_metadata": {
            "title": (
                None
                if title is None
                else {"text": title.text, "source": title.source}
            ),
            "page_count": page_count,
        },
    })


def _build_chunk_id(
    derivation_fingerprint: str,
    *,
    ordinal: int,
    kind: DocumentChunkKind,
    text: str,
    page_number: int | None,
    mappings: tuple[ChunkSourceMapping, ...],
) -> str:
    """Derive one scope-aware chunk ID from canonical text and provenance."""

    digest = _canonical_json_digest({
        "chunk_id_domain": _CHUNK_ID_DOMAIN,
        "derivation_fingerprint": derivation_fingerprint,
        "ordinal": ordinal,
        "kind": kind,
        "text": text,
        "page_number": page_number,
        "source_mappings": [
            _canonical_mapping(mapping) for mapping in mappings
        ],
    })
    return f"chunk_{digest}"
