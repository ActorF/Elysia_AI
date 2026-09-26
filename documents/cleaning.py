"""Clean loaded documents conservatively while preserving exact provenance.

The cleaner is deliberately lossless for every valid character except one
closed, audited omission: an exact repeated first line on every PDF page.
Loaders already reject unsafe controls and ambiguous byte decoding, so this
layer never guesses at mojibake, Unicode normalization, or whitespace intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Final, Literal, TypeAlias

from attachments.domain import MAX_JSON_SAFE_INTEGER

from .domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentFormat,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTable,
    DocumentTitle,
    LoadedDocument,
)
from .exceptions import (
    DocumentContentLimitError,
    DocumentError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)


CLEANED_DOCUMENT_SCHEMA_VERSION: Final[Literal[1]] = 1
CONSERVATIVE_CLEANER_ID: Final = "conservative-piece-table"
CONSERVATIVE_CLEANER_VERSION: Final = "1.0.0"
_TEXT_BLOCK_KINDS: Final = frozenset(
    {"title", "heading", "paragraph", "code"}
)
_DOCUMENT_FORMATS: Final = frozenset(
    {"text", "markdown", "pdf", "docx", "csv", "code"}
)
_PRODUCER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PRODUCER_VERSION_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)
_FINGERPRINT_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_ZERO_WIDTH_NO_BREAK_SPACE: Final = "\ufeff"

CleaningOmissionReason: TypeAlias = Literal["repeated_pdf_page_header"]


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


def _has_meaningful_text(value: str) -> bool:
    """Return whether text contains content beyond whitespace and BOMs."""

    return any(
        not (character.isspace() or character == _ZERO_WIDTH_NO_BREAK_SPACE)
        for character in value
    )


def _validate_producer(value: object, field_name: str, pattern: re.Pattern[str]) -> str:
    """Validate a stable producer identifier without leaking input text."""

    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise DocumentValidationError(f"{field_name} is invalid.")
    return value


def _canonical_digest(value: object) -> str:
    """Hash an explicitly shaped JSON value with stable UTF-8 encoding."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentProcessingLimits:
    """Bound object counts and text retained by cleaning and chunking."""

    max_cleaned_code_points: int = 4_000_000
    max_cleaned_blocks: int = 50_000
    max_cleaning_omissions: int = 2_000
    max_chunks: int = 10_000
    # Canonical table JSONL can expand every admitted character once for
    # escaping and add bounded per-cell syntax.  Sixteen million covers the
    # worst-case projection admitted by the default loader ceilings while
    # retaining a hard aggregate cap.
    max_total_chunk_code_points: int = 16_000_000
    max_source_mappings_per_chunk: int = 4_096
    max_total_source_mappings: int = 1_000_000

    def __post_init__(self) -> None:
        """Reject booleans, floats, zeroes, and incoherent ceilings."""

        for field_name in (
            "max_cleaned_code_points",
            "max_cleaned_blocks",
            "max_cleaning_omissions",
            "max_chunks",
            "max_total_chunk_code_points",
            "max_source_mappings_per_chunk",
            "max_total_source_mappings",
        ):
            _require_positive_integer(getattr(self, field_name), field_name)
        if self.max_cleaned_blocks > self.max_total_source_mappings:
            raise DocumentValidationError(
                "max_cleaned_blocks cannot exceed max_total_source_mappings."
            )
        if self.max_source_mappings_per_chunk > self.max_total_source_mappings:
            raise DocumentValidationError(
                "Per-chunk mappings cannot exceed the total mapping limit."
            )


@dataclass(frozen=True, slots=True)
class DocumentCleaningPolicy:
    """Configure only the deterministic omission rule that changes output."""

    repeated_pdf_header_min_pages: int = 3
    repeated_pdf_header_max_code_points: int = 256

    def __post_init__(self) -> None:
        """Require conservative positive bounds for header recognition."""

        _require_positive_integer(
            self.repeated_pdf_header_min_pages,
            "repeated_pdf_header_min_pages",
        )
        _require_positive_integer(
            self.repeated_pdf_header_max_code_points,
            "repeated_pdf_header_max_code_points",
        )
        if self.repeated_pdf_header_min_pages < 3:
            raise DocumentValidationError(
                "Repeated PDF header detection requires at least three pages."
            )


@dataclass(frozen=True, slots=True)
class LoadedDocumentProvenance:
    """Freeze the exact loaded representation consumed by later producers."""

    loaded_schema_version: Literal[1]
    source: DocumentSource
    document_format: DocumentFormat
    loader_id: str
    loader_version: str
    load_limits: DocumentLoadLimits

    def __post_init__(self) -> None:
        """Validate producer identity without claiming original-file offsets."""

        if (
            type(self.loaded_schema_version) is not int
            or self.loaded_schema_version != DOCUMENT_SCHEMA_VERSION
        ):
            raise DocumentValidationError(
                "Unsupported loaded-document provenance version."
            )
        if not isinstance(self.source, DocumentSource):
            raise DocumentValidationError("source must be DocumentSource.")
        if (
            not isinstance(self.document_format, str)
            or self.document_format not in _DOCUMENT_FORMATS
        ):
            raise DocumentValidationError("Document format is invalid.")
        _validate_producer(self.loader_id, "loader_id", _PRODUCER_ID_PATTERN)
        _validate_producer(
            self.loader_version,
            "loader_version",
            _PRODUCER_VERSION_PATTERN,
        )
        if not isinstance(self.load_limits, DocumentLoadLimits):
            raise DocumentValidationError(
                "load_limits must be DocumentLoadLimits."
            )

    @classmethod
    def from_document(cls, document: LoadedDocument) -> LoadedDocumentProvenance:
        """Capture immutable loader metadata from one validated document."""

        if not isinstance(document, LoadedDocument):
            raise DocumentValidationError("document must be LoadedDocument.")
        return cls(
            loaded_schema_version=document.schema_version,
            source=document.source,
            document_format=document.document_format,
            loader_id=document.loader_id,
            loader_version=document.loader_version,
            load_limits=document.limits,
        )


@dataclass(frozen=True, slots=True)
class DocumentTextSpan:
    """Locate a Unicode code-point range in one loaded non-table block."""

    block_ordinal: int
    start_code_point: int
    end_code_point: int
    page_number: int | None = None

    def __post_init__(self) -> None:
        """Require a non-empty zero-based half-open source range."""

        _require_non_negative_integer(self.block_ordinal, "block_ordinal")
        start = _require_non_negative_integer(
            self.start_code_point,
            "start_code_point",
        )
        end = _require_non_negative_integer(
            self.end_code_point,
            "end_code_point",
        )
        if start >= end:
            raise DocumentValidationError(
                "A document text span must be a non-empty half-open range."
            )
        if self.page_number is not None:
            _require_positive_integer(self.page_number, "page_number")


@dataclass(frozen=True, slots=True)
class DocumentTableCellSpan:
    """Locate a code-point range or empty position in one loaded table cell."""

    block_ordinal: int
    row_index: int
    column_index: int
    start_code_point: int
    end_code_point: int
    page_number: int | None = None

    def __post_init__(self) -> None:
        """Validate ragged-cell coordinates and a half-open cell range."""

        _require_non_negative_integer(self.block_ordinal, "block_ordinal")
        _require_non_negative_integer(self.row_index, "row_index")
        _require_non_negative_integer(self.column_index, "column_index")
        start = _require_non_negative_integer(
            self.start_code_point,
            "start_code_point",
        )
        end = _require_non_negative_integer(
            self.end_code_point,
            "end_code_point",
        )
        if start > end:
            raise DocumentValidationError(
                "A table-cell span must be a valid half-open range."
            )
        if self.page_number is not None:
            _require_positive_integer(self.page_number, "page_number")


DocumentSourceSpan: TypeAlias = DocumentTextSpan | DocumentTableCellSpan


@dataclass(frozen=True, slots=True)
class CleanedTextBlock:
    """Preserve text as exact ordered slices of one loaded source block."""

    ordinal: int
    source_block_ordinal: int
    kind: Literal["title", "heading", "paragraph", "code"]
    text: str
    retained_spans: tuple[DocumentTextSpan, ...]
    page_number: int | None = None
    heading_level: int | None = None

    def __post_init__(self) -> None:
        """Require stable order, meaningful text, and matching source metadata."""

        _require_non_negative_integer(self.ordinal, "ordinal")
        _require_non_negative_integer(
            self.source_block_ordinal,
            "source_block_ordinal",
        )
        if self.kind not in _TEXT_BLOCK_KINDS:
            raise DocumentValidationError("Cleaned text block kind is invalid.")
        if not isinstance(self.text, str) or not _has_meaningful_text(self.text):
            raise DocumentValidationError(
                "Cleaned text block must contain meaningful text."
            )
        if not isinstance(self.retained_spans, tuple) or not self.retained_spans:
            raise DocumentValidationError(
                "Cleaned text block requires retained source spans."
            )
        previous_end = -1
        for span in self.retained_spans:
            if not isinstance(span, DocumentTextSpan):
                raise DocumentValidationError(
                    "Cleaned text spans must be DocumentTextSpan values."
                )
            if (
                span.block_ordinal != self.source_block_ordinal
                or span.page_number != self.page_number
                or span.start_code_point < previous_end
            ):
                raise DocumentValidationError(
                    "Cleaned text spans do not match their source block."
                )
            previous_end = span.end_code_point
        if self.page_number is not None:
            _require_positive_integer(self.page_number, "page_number")
        if self.kind == "heading":
            if (
                type(self.heading_level) is not int
                or not 1 <= self.heading_level <= 9
            ):
                raise DocumentValidationError(
                    "A cleaned heading requires a level from 1 through 9."
                )
        elif self.heading_level is not None:
            raise DocumentValidationError(
                "Only a cleaned heading can carry heading_level."
            )


@dataclass(frozen=True, slots=True)
class CleanedTableBlock:
    """Preserve one ragged table exactly while retaining its source ordinal."""

    ordinal: int
    source_block_ordinal: int
    table: DocumentTable
    page_number: int | None = None

    def __post_init__(self) -> None:
        """Validate stable ordinals, the table value, and optional page."""

        _require_non_negative_integer(self.ordinal, "ordinal")
        _require_non_negative_integer(
            self.source_block_ordinal,
            "source_block_ordinal",
        )
        if not isinstance(self.table, DocumentTable):
            raise DocumentValidationError("table must be DocumentTable.")
        if self.page_number is not None:
            _require_positive_integer(self.page_number, "page_number")


CleanedBlock: TypeAlias = CleanedTextBlock | CleanedTableBlock


@dataclass(frozen=True, slots=True)
class CleaningOmission:
    """Audit one exact source range deliberately omitted by the cleaner."""

    reason: CleaningOmissionReason
    source_span: DocumentTextSpan

    def __post_init__(self) -> None:
        """Keep omission reasons closed and source ranges explicit."""

        if self.reason != "repeated_pdf_page_header":
            raise DocumentValidationError("Cleaning omission reason is invalid.")
        if not isinstance(self.source_span, DocumentTextSpan):
            raise DocumentValidationError(
                "Cleaning omission requires a DocumentTextSpan."
            )


def _source_payload(source: DocumentSource) -> dict[str, object]:
    """Serialize every authorization-sensitive source identity field."""

    return {
        "scope_kind": source.scope.kind,
        "scope_id": source.scope.id,
        "link_id": source.link_id,
        "file_id": source.file_id,
        "file_name": source.file_name,
        "media_type": source.media_type,
        "size_bytes": source.size_bytes,
    }


def _text_span_payload(span: DocumentTextSpan) -> dict[str, object]:
    """Serialize one source span with explicit code-point units."""

    return {
        "kind": "block_text",
        "block_ordinal": span.block_ordinal,
        "start_code_point": span.start_code_point,
        "end_code_point": span.end_code_point,
        "page_number": span.page_number,
    }


def _cleaned_block_payload(block: CleanedBlock) -> dict[str, object]:
    """Serialize one cleaned block without relying on dataclass repr order."""

    if isinstance(block, CleanedTextBlock):
        return {
            "variant": "text",
            "ordinal": block.ordinal,
            "source_block_ordinal": block.source_block_ordinal,
            "kind": block.kind,
            "text": block.text,
            "page_number": block.page_number,
            "heading_level": block.heading_level,
            "retained_spans": [
                _text_span_payload(span) for span in block.retained_spans
            ],
        }
    return {
        "variant": "table",
        "ordinal": block.ordinal,
        "source_block_ordinal": block.source_block_ordinal,
        "page_number": block.page_number,
        "rows": [list(row) for row in block.table.rows],
    }


def _loaded_document_payload(document: LoadedDocument) -> dict[str, object]:
    """Build the canonical semantic preimage of one loaded document."""

    blocks: list[dict[str, object]] = []
    for block in document.blocks:
        value: dict[str, object] = {
            "ordinal": block.ordinal,
            "kind": block.kind,
            "page_number": block.page_number,
            "heading_level": block.heading_level,
        }
        if block.table is None:
            value["text"] = block.text
        else:
            value["table_rows"] = [list(row) for row in block.table.rows]
        blocks.append(value)
    return {
        "domain": "elysia.loaded-document.semantic.v1",
        "schema_version": document.schema_version,
        "source": _source_payload(document.source),
        "document_format": document.document_format,
        "loader_id": document.loader_id,
        "loader_version": document.loader_version,
        "title": (
            None
            if document.title is None
            else {
                "text": document.title.text,
                "source": document.title.source,
            }
        ),
        "page_count": document.page_count,
        "blocks": blocks,
    }


def _build_document_fingerprint(document: LoadedDocument) -> str:
    """Bind one fingerprint to the exact validated loaded representation."""

    return _canonical_digest(_loaded_document_payload(document))


def _build_cleaning_fingerprint(
    *,
    document_fingerprint: str,
    provenance: LoadedDocumentProvenance,
    cleaner_id: str,
    cleaner_version: str,
    policy: DocumentCleaningPolicy,
    blocks: tuple[CleanedBlock, ...],
    omissions: tuple[CleaningOmission, ...],
) -> str:
    """Bind cleaning identity to lineage, policy, output, and omissions."""

    return _canonical_digest({
        "domain": "elysia.cleaned-document.v1",
        "document_fingerprint": document_fingerprint,
        "source": _source_payload(provenance.source),
        "cleaner_id": cleaner_id,
        "cleaner_version": cleaner_version,
        "policy": {
            "repeated_pdf_header_min_pages": (
                policy.repeated_pdf_header_min_pages
            ),
            "repeated_pdf_header_max_code_points": (
                policy.repeated_pdf_header_max_code_points
            ),
        },
        "blocks": [_cleaned_block_payload(block) for block in blocks],
        "omissions": [
            {
                "reason": omission.reason,
                "span": _text_span_payload(omission.source_span),
            }
            for omission in omissions
        ],
    })


@dataclass(frozen=True, slots=True)
class CleanedDocument:
    """Carry deterministic lossless blocks and audited cleaning omissions."""

    schema_version: Literal[1]
    provenance: LoadedDocumentProvenance
    cleaner_id: str
    cleaner_version: str
    policy: DocumentCleaningPolicy
    limits: DocumentProcessingLimits
    document_fingerprint: str
    blocks: tuple[CleanedBlock, ...]
    omissions: tuple[CleaningOmission, ...] = ()
    title: DocumentTitle | None = None
    page_count: int | None = None
    cleaning_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        """Validate producer identity, budgets, ordering, and page metadata."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != CLEANED_DOCUMENT_SCHEMA_VERSION
        ):
            raise DocumentValidationError(
                "Unsupported cleaned-document schema version."
            )
        if not isinstance(self.provenance, LoadedDocumentProvenance):
            raise DocumentValidationError(
                "provenance must be LoadedDocumentProvenance."
            )
        _validate_producer(self.cleaner_id, "cleaner_id", _PRODUCER_ID_PATTERN)
        _validate_producer(
            self.cleaner_version,
            "cleaner_version",
            _PRODUCER_VERSION_PATTERN,
        )
        if not isinstance(self.policy, DocumentCleaningPolicy):
            raise DocumentValidationError(
                "policy must be DocumentCleaningPolicy."
            )
        if not isinstance(self.limits, DocumentProcessingLimits):
            raise DocumentValidationError(
                "limits must be DocumentProcessingLimits."
            )
        if (
            not isinstance(self.document_fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(self.document_fingerprint) is None
        ):
            raise DocumentValidationError("document_fingerprint is invalid.")
        if self.title is not None and not isinstance(self.title, DocumentTitle):
            raise DocumentValidationError("title must be DocumentTitle or None.")
        if not isinstance(self.blocks, tuple) or not all(
            isinstance(block, (CleanedTextBlock, CleanedTableBlock))
            for block in self.blocks
        ):
            raise DocumentValidationError("Cleaned blocks are invalid.")
        if len(self.blocks) > self.limits.max_cleaned_blocks:
            raise DocumentContentLimitError(
                "Cleaned block count exceeds the configured limit."
            )
        if tuple(block.ordinal for block in self.blocks) != tuple(
            range(len(self.blocks))
        ):
            raise DocumentValidationError(
                "Cleaned block ordinals must be contiguous from zero."
            )
        source_ordinals = tuple(
            block.source_block_ordinal for block in self.blocks
        )
        if source_ordinals != tuple(sorted(set(source_ordinals))):
            raise DocumentValidationError(
                "Cleaned source-block ordinals must be unique and ordered."
            )
        if not isinstance(self.omissions, tuple) or not all(
            isinstance(item, CleaningOmission) for item in self.omissions
        ):
            raise DocumentValidationError("Cleaning omissions are invalid.")
        if len(self.omissions) > self.limits.max_cleaning_omissions:
            raise DocumentContentLimitError(
                "Cleaning omissions exceed the configured limit."
            )
        self._validate_pages()
        self._validate_output_budget()
        object.__setattr__(
            self,
            "cleaning_fingerprint",
            _build_cleaning_fingerprint(
                document_fingerprint=self.document_fingerprint,
                provenance=self.provenance,
                cleaner_id=self.cleaner_id,
                cleaner_version=self.cleaner_version,
                policy=self.policy,
                blocks=self.blocks,
                omissions=self.omissions,
            ),
        )

    def _validate_pages(self) -> None:
        """Preserve only real PDF page values from the loaded representation."""

        if self.provenance.document_format == "pdf":
            if self.page_count is None:
                raise DocumentValidationError(
                    "A cleaned PDF requires page_count."
                )
            page_count = _require_positive_integer(
                self.page_count,
                "page_count",
            )
            if any(
                block.page_number is None or block.page_number > page_count
                for block in self.blocks
            ):
                raise DocumentValidationError(
                    "Cleaned PDF blocks require valid source pages."
                )
        elif self.page_count is not None or any(
            block.page_number is not None for block in self.blocks
        ):
            raise DocumentValidationError(
                "Only cleaned PDFs can carry page metadata."
            )

    def _validate_output_budget(self) -> None:
        """Count retained text, table cells, and provenance pieces exactly."""

        total = 0
        retained_spans = 0
        for block in self.blocks:
            if isinstance(block, CleanedTextBlock):
                total += len(block.text)
                retained_spans += len(block.retained_spans)
            else:
                total += sum(
                    len(cell)
                    for row in block.table.rows
                    for cell in row
                )
            if total > self.limits.max_cleaned_code_points:
                raise DocumentContentLimitError(
                    "Cleaned text exceeds the configured limit."
                )
            if retained_spans > self.limits.max_total_source_mappings:
                raise DocumentContentLimitError(
                    "Cleaned provenance exceeds the configured mapping limit."
                )


class ConservativeDocumentCleaner:
    """Produce lossless block slices plus exact repeated-header omissions."""

    def __init__(
        self,
        *,
        policy: DocumentCleaningPolicy | None = None,
        limits: DocumentProcessingLimits | None = None,
    ) -> None:
        """Create a stateless cleaner with immutable policy and ceilings."""

        self._policy = (
            DocumentCleaningPolicy() if policy is None else policy
        )
        self._limits = (
            DocumentProcessingLimits() if limits is None else limits
        )
        if not isinstance(self._policy, DocumentCleaningPolicy):
            raise TypeError("policy must be DocumentCleaningPolicy.")
        if not isinstance(self._limits, DocumentProcessingLimits):
            raise TypeError("limits must be DocumentProcessingLimits.")

    @property
    def cleaner_id(self) -> str:
        """Return the stable identity of the conservative cleaner."""

        return CONSERVATIVE_CLEANER_ID

    @property
    def cleaner_version(self) -> str:
        """Return the version governing omission and fingerprint behavior."""

        return CONSERVATIVE_CLEANER_VERSION

    @property
    def policy(self) -> DocumentCleaningPolicy:
        """Return the exact output-affecting cleaning policy."""

        return self._policy

    @property
    def limits(self) -> DocumentProcessingLimits:
        """Return the resource ceilings enforced while cleaning."""

        return self._limits

    def clean(self, document: LoadedDocument) -> CleanedDocument:
        """Clean one loaded document or raise a stable typed failure."""

        try:
            return self._clean_document(document)
        except DocumentError:
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document cleaning exceeded a safe resource limit."
            ) from error
        except Exception as error:
            raise DocumentProcessingFailedError(
                "Document cleaning failed without a safe typed result."
            ) from error

    def _clean_document(self, document: LoadedDocument) -> CleanedDocument:
        """Build the deterministic piece table after public error translation."""

        if not isinstance(document, LoadedDocument):
            raise DocumentValidationError("document must be LoadedDocument.")
        provenance = LoadedDocumentProvenance.from_document(document)
        removal_ends = self._repeated_pdf_header_ends(document)
        blocks: list[CleanedBlock] = []
        omissions: list[CleaningOmission] = []
        total_code_points = 0

        for source_block in document.blocks:
            if len(blocks) >= self._limits.max_cleaned_blocks:
                raise DocumentContentLimitError(
                    "Cleaned block count exceeds the configured limit."
                )
            if source_block.kind == "table":
                if source_block.table is None:
                    raise DocumentValidationError(
                        "Loaded table block is internally inconsistent."
                    )
                total_code_points += sum(
                    len(cell)
                    for row in source_block.table.rows
                    for cell in row
                )
                cleaned: CleanedBlock = CleanedTableBlock(
                    ordinal=len(blocks),
                    source_block_ordinal=source_block.ordinal,
                    table=source_block.table,
                    page_number=source_block.page_number,
                )
            else:
                if source_block.text is None:
                    raise DocumentValidationError(
                        "Loaded text block is internally inconsistent."
                    )
                retained_start = removal_ends.get(source_block.ordinal, 0)
                retained_text = source_block.text[retained_start:]
                if not _has_meaningful_text(retained_text):
                    raise DocumentValidationError(
                        "Cleaning cannot remove the only meaningful source text."
                    )
                span = DocumentTextSpan(
                    block_ordinal=source_block.ordinal,
                    start_code_point=retained_start,
                    end_code_point=len(source_block.text),
                    page_number=source_block.page_number,
                )
                if retained_start:
                    if len(omissions) >= self._limits.max_cleaning_omissions:
                        raise DocumentContentLimitError(
                            "Cleaning omissions exceed the configured limit."
                        )
                    omissions.append(
                        CleaningOmission(
                            reason="repeated_pdf_page_header",
                            source_span=DocumentTextSpan(
                                block_ordinal=source_block.ordinal,
                                start_code_point=0,
                                end_code_point=retained_start,
                                page_number=source_block.page_number,
                            ),
                        )
                    )
                total_code_points += len(retained_text)
                cleaned = CleanedTextBlock(
                    ordinal=len(blocks),
                    source_block_ordinal=source_block.ordinal,
                    kind=source_block.kind,
                    text=retained_text,
                    retained_spans=(span,),
                    page_number=source_block.page_number,
                    heading_level=source_block.heading_level,
                )
            if total_code_points > self._limits.max_cleaned_code_points:
                raise DocumentContentLimitError(
                    "Cleaned text exceeds the configured limit."
                )
            blocks.append(cleaned)

        document_fingerprint = _build_document_fingerprint(document)
        return CleanedDocument(
            schema_version=CLEANED_DOCUMENT_SCHEMA_VERSION,
            provenance=provenance,
            cleaner_id=self.cleaner_id,
            cleaner_version=self.cleaner_version,
            policy=self._policy,
            limits=self._limits,
            document_fingerprint=document_fingerprint,
            blocks=tuple(blocks),
            omissions=tuple(omissions),
            title=document.title,
            page_count=document.page_count,
        )

    def _repeated_pdf_header_ends(
        self,
        document: LoadedDocument,
    ) -> dict[int, int]:
        """Return exact first-line ends only when every PDF page proves the rule."""

        if (
            document.document_format != "pdf"
            or document.page_count is None
            or document.page_count < self._policy.repeated_pdf_header_min_pages
        ):
            return {}
        blocks_by_page: dict[int, list[DocumentBlock]] = {
            page: [] for page in range(1, document.page_count + 1)
        }
        for block in document.blocks:
            if block.page_number is None:
                return {}
            blocks_by_page[block.page_number].append(block)
        if any(len(items) != 1 for items in blocks_by_page.values()):
            # Empty pages or ambiguous multi-block layouts have no reliable
            # first extracted line, so the cleaner keeps the entire source.
            return {}

        candidate: str | None = None
        removal_ends: dict[int, int] = {}
        for page in range(1, document.page_count + 1):
            block = blocks_by_page[page][0]
            if (
                block.kind != "paragraph"
                or block.text is None
            ):
                return {}
            line_end = self._first_line_end(block.text)
            if line_end is None:
                return {}
            header_text, retained_start = line_end
            if (
                not _has_meaningful_text(header_text)
                or len(header_text)
                > self._policy.repeated_pdf_header_max_code_points
                or not _has_meaningful_text(block.text[retained_start:])
            ):
                return {}
            if document.title is not None and header_text == document.title.text:
                return {}
            if candidate is None:
                candidate = header_text
            elif header_text != candidate:
                return {}
            removal_ends[block.ordinal] = retained_start
        return removal_ends

    @staticmethod
    def _first_line_end(text: str) -> tuple[str, int] | None:
        """Find one exact CRLF, CR, or LF boundary without Unicode guessing."""

        for index, character in enumerate(text):
            if character == "\n":
                return text[:index], index + 1
            if character == "\r":
                after = index + 2 if text[index : index + 2] == "\r\n" else index + 1
                return text[:index], after
        return None
