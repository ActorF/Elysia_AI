"""Define bounded, path-private values produced by document loaders."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

from attachments.domain import (
    MAX_JSON_SAFE_INTEGER,
    AttachmentScope,
    validate_attachment_id,
    validate_file_id,
    validate_file_name,
    validate_media_type,
)

from .exceptions import (
    DocumentContentLimitError,
    DocumentTooLargeError,
    DocumentValidationError,
)


DOCUMENT_SCHEMA_VERSION: Final[Literal[1]] = 1
MAX_DOCUMENT_TITLE_CODE_POINTS: Final = 4_096
MAX_LOADER_ID_LENGTH: Final = 64
MAX_LOADER_VERSION_LENGTH: Final = 128

DocumentFormat: TypeAlias = Literal[
    "text",
    "markdown",
    "pdf",
    "docx",
    "csv",
    "code",
]
DocumentTitleSource: TypeAlias = Literal["embedded", "heading"]
DocumentBlockKind: TypeAlias = Literal[
    "title",
    "heading",
    "paragraph",
    "code",
    "table",
]

_DOCUMENT_FORMATS: Final = (
    "text",
    "markdown",
    "pdf",
    "docx",
    "csv",
    "code",
)
_TITLE_SOURCES: Final = ("embedded", "heading")
_BLOCK_KINDS: Final = ("title", "heading", "paragraph", "code", "table")
_LOADER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_LOADER_VERSION_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)
_ZERO_WIDTH_NO_BREAK_SPACE: Final = "\ufeff"


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


def _has_meaningful_text(value: str) -> bool:
    """Return whether text contains content beyond whitespace and a BOM."""

    return any(
        not (character.isspace() or character == _ZERO_WIDTH_NO_BREAK_SPACE)
        for character in value
    )


def _validate_text(
    value: object,
    field_name: str,
    *,
    allow_empty: bool,
) -> str:
    """Validate extracted Unicode without modifying its source spelling."""

    if not isinstance(value, str):
        raise DocumentValidationError(f"{field_name} must be a string.")
    if not allow_empty and not _has_meaningful_text(value):
        raise DocumentValidationError(
            f"{field_name} must contain meaningful text."
        )
    for character in value:
        category = unicodedata.category(character)
        if category == "Cs" or (
            category == "Cc" and character not in {"\t", "\n", "\r"}
        ):
            raise DocumentValidationError(
                f"{field_name} contains an unsafe control character."
            )
    return value


@dataclass(frozen=True, slots=True)
class DocumentLoadLimits:
    """Bound source, expansion, structure, and extracted text resources.

    The limits are carried with a loaded result so the application service can
    prove that a loader did not publish output under a different budget. XML
    and expansion budgets must be enforced while parsing, because checking only
    the final output would not stop a compressed or deeply nested input first.
    """

    max_source_bytes: int = 16 * 1024 * 1024
    max_expanded_bytes: int = 64 * 1024 * 1024
    max_text_code_points: int = 4_000_000
    max_blocks: int = 50_000
    max_pages: int = 2_000
    max_package_entries: int = 4_096
    max_table_cells: int = 1_000_000
    max_table_columns: int = 256
    max_cell_code_points: int = 32_768
    max_xml_elements: int = 250_000
    max_xml_depth: int = 128

    def __post_init__(self) -> None:
        """Reject ambiguous booleans, floats, zeroes, and incoherent budgets."""

        for field_name in (
            "max_source_bytes",
            "max_expanded_bytes",
            "max_text_code_points",
            "max_blocks",
            "max_pages",
            "max_package_entries",
            "max_table_cells",
            "max_table_columns",
            "max_cell_code_points",
            "max_xml_elements",
            "max_xml_depth",
        ):
            _require_positive_integer(getattr(self, field_name), field_name)
        if self.max_cell_code_points > self.max_text_code_points:
            raise DocumentValidationError(
                "max_cell_code_points cannot exceed max_text_code_points."
            )
        if self.max_table_columns > self.max_table_cells:
            raise DocumentValidationError(
                "max_table_columns cannot exceed max_table_cells."
            )


@dataclass(frozen=True, slots=True)
class DocumentSource:
    """Identify one authorized attachment link without retaining any path."""

    scope: AttachmentScope
    link_id: str
    file_id: str
    file_name: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        """Validate exact ownership and immutable file identity metadata."""

        if not isinstance(self.scope, AttachmentScope):
            raise DocumentValidationError("scope must be AttachmentScope.")
        try:
            validate_attachment_id(self.link_id)
            validate_file_id(self.file_id)
            validate_file_name(self.file_name)
            validate_media_type(self.media_type)
        except ValueError as error:
            raise DocumentValidationError(
                "Document source metadata is invalid."
            ) from error
        _require_positive_integer(self.size_bytes, "size_bytes")


@dataclass(frozen=True, slots=True)
class DocumentTitle:
    """Preserve an explicit title together with its extraction provenance."""

    text: str
    source: DocumentTitleSource

    def __post_init__(self) -> None:
        """Require bounded meaningful text and a closed provenance value."""

        title = _validate_text(self.text, "title text", allow_empty=False)
        if len(title) > MAX_DOCUMENT_TITLE_CODE_POINTS:
            raise DocumentValidationError(
                "Document title exceeds the code-point limit."
            )
        if not isinstance(self.source, str) or self.source not in _TITLE_SOURCES:
            raise DocumentValidationError(
                "Document title source must be embedded or heading."
            )


@dataclass(frozen=True, slots=True)
class DocumentTable:
    """Preserve table cells row by row without inventing missing columns.

    Ragged rows are intentional: padding a CSV, Markdown, or DOCX row would
    manufacture source data, while truncating it would lose content. The final
    loaded document applies aggregate row-width, cell-count, and text budgets.
    """

    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        """Require at least one row and one validated cell in every row."""

        if not isinstance(self.rows, tuple) or not self.rows:
            raise DocumentValidationError(
                "Document table must contain at least one row."
            )
        for row in self.rows:
            if not isinstance(row, tuple) or not row:
                raise DocumentValidationError(
                    "Every document table row must contain at least one cell."
                )
            for cell in row:
                _validate_text(cell, "table cell", allow_empty=True)


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """Represent one ordered text or table unit with optional page metadata."""

    ordinal: int
    kind: DocumentBlockKind
    text: str | None = None
    table: DocumentTable | None = None
    page_number: int | None = None
    heading_level: int | None = None

    def __post_init__(self) -> None:
        """Enforce the closed block union and one-based location metadata."""

        if (
            type(self.ordinal) is not int
            or self.ordinal < 0
            or self.ordinal > MAX_JSON_SAFE_INTEGER
        ):
            raise DocumentValidationError(
                "Document block ordinal must be a non-negative safe integer."
            )
        if not isinstance(self.kind, str) or self.kind not in _BLOCK_KINDS:
            raise DocumentValidationError("Document block kind is invalid.")

        if self.kind == "table":
            if self.text is not None or not isinstance(self.table, DocumentTable):
                raise DocumentValidationError(
                    "A table block must contain only DocumentTable data."
                )
        else:
            if self.table is not None:
                raise DocumentValidationError(
                    "A text block cannot contain table data."
                )
            _validate_text(self.text, "block text", allow_empty=False)

        if self.page_number is not None:
            _require_positive_integer(self.page_number, "page_number")

        if self.kind == "heading":
            if (
                type(self.heading_level) is not int
                or not 1 <= self.heading_level <= 9
            ):
                raise DocumentValidationError(
                    "A heading block requires a level from 1 through 9."
                )
        elif self.heading_level is not None:
            raise DocumentValidationError(
                "Only a heading block can carry heading_level."
            )


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    """Carry one fully validated loader result for later cleaning and chunking.

    Block ordinals preserve extracted order but deliberately are not Chunk IDs
    or source offsets. Those identities depend on the cleaning/chunking version
    and belong to the next module rather than this raw loader contract.
    """

    schema_version: Literal[1]
    source: DocumentSource
    document_format: DocumentFormat
    loader_id: str
    loader_version: str
    blocks: tuple[DocumentBlock, ...]
    limits: DocumentLoadLimits
    title: DocumentTitle | None = None
    page_count: int | None = None

    def __post_init__(self) -> None:
        """Validate source identity, structure, page data, and total budgets."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != DOCUMENT_SCHEMA_VERSION
        ):
            raise DocumentValidationError(
                "Unsupported loaded-document schema version."
            )
        if not isinstance(self.source, DocumentSource):
            raise DocumentValidationError("source must be DocumentSource.")
        if (
            not isinstance(self.document_format, str)
            or self.document_format not in _DOCUMENT_FORMATS
        ):
            raise DocumentValidationError("Document format is invalid.")
        if (
            not isinstance(self.loader_id, str)
            or len(self.loader_id) > MAX_LOADER_ID_LENGTH
            or _LOADER_ID_PATTERN.fullmatch(self.loader_id) is None
        ):
            raise DocumentValidationError(
                "loader_id must be a stable lowercase identifier."
            )
        if (
            not isinstance(self.loader_version, str)
            or len(self.loader_version) > MAX_LOADER_VERSION_LENGTH
            or _LOADER_VERSION_PATTERN.fullmatch(self.loader_version) is None
        ):
            raise DocumentValidationError(
                "loader_version must be a stable non-empty identifier."
            )
        if not isinstance(self.limits, DocumentLoadLimits):
            raise DocumentValidationError("limits must be DocumentLoadLimits.")
        if self.source.size_bytes > self.limits.max_source_bytes:
            raise DocumentTooLargeError(
                "Document source exceeds the configured byte limit."
            )
        if self.title is not None and not isinstance(self.title, DocumentTitle):
            raise DocumentValidationError(
                "title must be DocumentTitle or None."
            )
        if (
            not isinstance(self.blocks, tuple)
            or not all(isinstance(block, DocumentBlock) for block in self.blocks)
        ):
            raise DocumentValidationError(
                "blocks must contain only DocumentBlock values."
            )
        if len(self.blocks) > self.limits.max_blocks:
            raise DocumentContentLimitError(
                "Document block count exceeds the configured limit."
            )
        if tuple(block.ordinal for block in self.blocks) != tuple(
            range(len(self.blocks))
        ):
            raise DocumentValidationError(
                "Document block ordinals must be contiguous from zero."
            )

        self._validate_page_metadata()
        self._validate_content_budgets()
        self._validate_heading_title()

    def _validate_page_metadata(self) -> None:
        """Require real PDF page metadata and reject invented pagination."""

        if self.document_format == "pdf":
            if self.page_count is None:
                raise DocumentValidationError(
                    "A PDF document requires page_count."
                )
            page_count = _require_positive_integer(
                self.page_count,
                "page_count",
            )
            if page_count > self.limits.max_pages:
                raise DocumentContentLimitError(
                    "Document page count exceeds the configured limit."
                )
            if any(block.page_number is None for block in self.blocks):
                raise DocumentValidationError(
                    "Every PDF block must identify its source page."
                )
        else:
            if self.page_count is not None or any(
                block.page_number is not None for block in self.blocks
            ):
                raise DocumentValidationError(
                    "Only PDF documents can carry page metadata."
                )
            return

        if any(
            block.page_number is not None
            and block.page_number > page_count
            for block in self.blocks
        ):
            raise DocumentValidationError(
                "Document block page_number exceeds page_count."
            )

    def _validate_content_budgets(self) -> None:
        """Count every published string and table cell against exact limits."""

        text_code_points = 0 if self.title is None else len(self.title.text)
        table_cells = 0
        for block in self.blocks:
            if block.text is not None:
                text_code_points += len(block.text)
            if block.table is not None:
                for row in block.table.rows:
                    if len(row) > self.limits.max_table_columns:
                        raise DocumentContentLimitError(
                            "Document table exceeds the configured column limit."
                        )
                    table_cells += len(row)
                    if table_cells > self.limits.max_table_cells:
                        raise DocumentContentLimitError(
                            "Document tables exceed the configured cell limit."
                        )
                    for cell in row:
                        if len(cell) > self.limits.max_cell_code_points:
                            raise DocumentContentLimitError(
                                "Document table cell exceeds the configured limit."
                            )
                        text_code_points += len(cell)
            if text_code_points > self.limits.max_text_code_points:
                raise DocumentContentLimitError(
                    "Document text exceeds the configured code-point limit."
                )

        if text_code_points > self.limits.max_text_code_points:
            raise DocumentContentLimitError(
                "Document text exceeds the configured code-point limit."
            )

    def _validate_heading_title(self) -> None:
        """Prove that a heading-derived title still exists in the block stream."""

        if self.title is None or self.title.source != "heading":
            return
        if not any(
            block.kind in {"title", "heading"}
            and block.text == self.title.text
            for block in self.blocks
        ):
            raise DocumentValidationError(
                "A heading-derived title must match a title or heading block."
            )
