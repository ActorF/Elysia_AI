"""Load bounded text, Markdown, CSV, and source code without filesystem I/O.

The adapter receives only a validated, path-free ``DocumentSource`` and an
immutable byte snapshot.  It deliberately performs structural extraction
only: whitespace cleaning, semantic chunking, syntax execution, rendering,
and external-resource resolution belong outside the loader boundary.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

from .domain import (
    DOCUMENT_SCHEMA_VERSION,
    MAX_DOCUMENT_TITLE_CODE_POINTS,
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
    DocumentCorruptError,
    DocumentEmptyError,
    DocumentReadError,
    DocumentTooLargeError,
    DocumentUnsupportedFormatError,
    DocumentValidationError,
)
from .protocol import DocumentRoute


_LOADER_ID: Final = "text-structure"
_LOADER_VERSION: Final = "1.0.0"

_TEXT_ROUTES: Final[frozenset[DocumentRoute]] = frozenset({
    (".txt", "text/plain"),
})
_MARKDOWN_ROUTES: Final[frozenset[DocumentRoute]] = frozenset({
    (".md", "text/markdown"),
    (".markdown", "text/markdown"),
})
_CSV_ROUTES: Final[frozenset[DocumentRoute]] = frozenset({
    (".csv", "text/csv"),
})
_CODE_ROUTES: Final[frozenset[DocumentRoute]] = frozenset({
    (".ini", "text/plain"),
    (".css", "text/css"),
    (".htm", "text/html"),
    (".html", "text/html"),
    (".js", "text/javascript"),
    (".jsx", "text/javascript"),
    (".json", "application/json"),
    (".py", "text/x-python"),
    (".sql", "application/sql"),
    (".toml", "application/toml"),
    (".ts", "text/typescript"),
    (".tsx", "text/typescript"),
    (".xml", "application/xml"),
    (".yaml", "application/yaml"),
    (".yml", "application/yaml"),
})
_ALL_ROUTES: Final = frozenset(
    (*_TEXT_ROUTES, *_MARKDOWN_ROUTES, *_CSV_ROUTES, *_CODE_ROUTES)
)

_ATX_HEADING = re.compile(
    r" {0,3}(?P<marker>#{1,6})(?:[ \t]+(?P<text>.*)|[ \t]*)"
)
_SETEXT_HEADING = re.compile(r" {0,3}(?P<marker>=+|-+)[ \t]*")
_FENCE_OPEN = re.compile(
    r" {0,3}(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)"
)
_TABLE_DELIMITER_CELL = re.compile(r":?-{3,}:?")
_LINE_BREAK = re.compile(r"[\r\n\u2028\u2029]")
_ALLOWED_CONTROL_CHARACTERS: Final = frozenset({"\t", "\n", "\r"})
_ZERO_WIDTH_NO_BREAK_SPACE: Final = "\ufeff"

_RouteKind: TypeAlias = Literal["text", "markdown", "csv", "code"]


@dataclass(frozen=True, slots=True)
class _LineSpan:
    """Identify one physical line without copying its source characters."""

    start: int
    content_end: int
    end: int


@dataclass(slots=True)
class _OutputBuilder:
    """Enforce output budgets before allocating the complete result graph."""

    limits: DocumentLoadLimits
    blocks: list[DocumentBlock]
    text_code_points: int = 0
    table_cells: int = 0

    def add_text(
        self,
        kind: Literal["title", "heading", "paragraph", "code"],
        text: str,
        *,
        heading_level: int | None = None,
    ) -> None:
        """Append one meaningful text block after checking aggregate limits."""

        if not _has_meaningful_text(text):
            return
        self._reserve_block()
        self._reserve_text(len(text))
        self.blocks.append(
            DocumentBlock(
                ordinal=len(self.blocks),
                kind=kind,
                text=text,
                heading_level=heading_level,
            )
        )

    def add_table(self, rows: list[tuple[str, ...]]) -> None:
        """Append one ragged table while enforcing row and cell budgets."""

        if not rows:
            return
        self._reserve_block()
        for row in rows:
            if not row:
                raise DocumentCorruptError(
                    "A parsed table row contains no cells."
                )
            if len(row) > self.limits.max_table_columns:
                raise DocumentContentLimitError(
                    "Document table exceeds the configured column limit."
                )
            for cell in row:
                if len(cell) > self.limits.max_cell_code_points:
                    raise DocumentContentLimitError(
                        "Document table cell exceeds the configured limit."
                    )
                self.table_cells += 1
                if self.table_cells > self.limits.max_table_cells:
                    raise DocumentContentLimitError(
                        "Document tables exceed the configured cell limit."
                    )
                self._reserve_text(len(cell))
        self.blocks.append(
            DocumentBlock(
                ordinal=len(self.blocks),
                kind="table",
                table=DocumentTable(rows=tuple(rows)),
            )
        )

    def reserve_title(self, text: str) -> DocumentTitle:
        """Count a heading-derived title separately from its source block."""

        if len(text) > MAX_DOCUMENT_TITLE_CODE_POINTS:
            raise DocumentContentLimitError(
                "Document title exceeds the configured limit."
            )
        self._reserve_text(len(text))
        return DocumentTitle(text=text, source="heading")

    def _reserve_block(self) -> None:
        if len(self.blocks) >= self.limits.max_blocks:
            raise DocumentContentLimitError(
                "Document block count exceeds the configured limit."
            )

    def _reserve_text(self, count: int) -> None:
        self.text_code_points += count
        if self.text_code_points > self.limits.max_text_code_points:
            raise DocumentContentLimitError(
                "Document text exceeds the configured code-point limit."
            )


class TextDocumentLoader:
    """Extract deterministic structure from trusted textual byte snapshots."""

    @property
    def loader_id(self) -> str:
        """Return the stable producer identity persisted with loader output."""

        return _LOADER_ID

    @property
    def loader_version(self) -> str:
        """Return the version governing decoding and structural rules."""

        return _LOADER_VERSION

    @property
    def routes(self) -> frozenset[DocumentRoute]:
        """Return exact lowercase suffix and canonical MIME route pairs."""

        return _ALL_ROUTES

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Decode and structurally load one path-free verified byte snapshot.

        The route is checked again even when a registry selected this adapter.
        This fail-closed check prevents a direct caller from using a harmless
        text suffix to bypass the MIME decision made during trusted import.
        """

        if not isinstance(source, DocumentSource):
            raise DocumentValidationError("source must be DocumentSource.")
        if not isinstance(data, bytes):
            raise DocumentValidationError("data must be immutable bytes.")
        if not isinstance(limits, DocumentLoadLimits):
            raise DocumentValidationError(
                "limits must be DocumentLoadLimits."
            )

        route = (_suffix(source.file_name), source.media_type)
        route_kind = _route_kind(route)
        if source.size_bytes != len(data):
            raise DocumentReadError(
                "Document bytes do not match their verified metadata."
            )
        if len(data) > limits.max_source_bytes:
            raise DocumentTooLargeError(
                "Document source exceeds the configured byte limit."
            )
        if not data:
            raise DocumentEmptyError("Document contains no loadable content.")

        text = _decode_text(data)
        _validate_decoded_text(text)
        # A CSV record can legitimately contain only empty or whitespace
        # cells.  Quoting must not decide whether the same cell is loadable,
        # so record-level emptiness belongs to the CSV parser itself.
        if route_kind != "csv" and not _has_meaningful_text(text):
            raise DocumentEmptyError("Document contains no loadable content.")

        builder = _OutputBuilder(limits=limits, blocks=[])
        title: DocumentTitle | None = None
        document_format: DocumentFormat
        if route_kind == "text":
            _load_plain_text(text, builder)
            document_format = "text"
        elif route_kind == "markdown":
            title = _load_markdown(text, builder)
            document_format = "markdown"
        elif route_kind == "csv":
            _load_csv(text, builder)
            document_format = "csv"
        else:
            builder.add_text("code", text)
            document_format = "code"

        if not builder.blocks:
            raise DocumentEmptyError("Document contains no loadable content.")
        return LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format=document_format,
            loader_id=self.loader_id,
            loader_version=self.loader_version,
            blocks=tuple(builder.blocks),
            limits=limits,
            title=title,
        )


def _suffix(file_name: str) -> str:
    """Return a lowercase final suffix without invoking filesystem APIs."""

    stem, separator, tail = file_name.rpartition(".")
    if not separator or not stem or not tail:
        return ""
    return f".{tail.casefold()}"


def _route_kind(route: DocumentRoute) -> _RouteKind:
    """Resolve one exact route or reject a suffix/MIME mismatch."""

    if route in _TEXT_ROUTES:
        return "text"
    if route in _MARKDOWN_ROUTES:
        return "markdown"
    if route in _CSV_ROUTES:
        return "csv"
    if route in _CODE_ROUTES:
        return "code"
    raise DocumentUnsupportedFormatError(
        "Document suffix and media type are not a supported text route."
    )


def _decode_text(data: bytes) -> str:
    """Decode only unambiguous Unicode encodings without replacement.

    UTF-32 signatures are checked before UTF-16 because both little-endian
    signatures begin with ``FF FE``.  UTF-32 is deliberately rejected in this
    loader version rather than being misread as NUL-bearing UTF-16.
    """

    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise DocumentCorruptError(
            "Document text encoding is unsupported or invalid."
        )
    try:
        if data.startswith(codecs.BOM_UTF8):
            return data[len(codecs.BOM_UTF8):].decode("utf-8", "strict")
        if data.startswith(codecs.BOM_UTF16_LE):
            return data[len(codecs.BOM_UTF16_LE):].decode(
                "utf-16-le",
                "strict",
            )
        if data.startswith(codecs.BOM_UTF16_BE):
            return data[len(codecs.BOM_UTF16_BE):].decode(
                "utf-16-be",
                "strict",
            )
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise DocumentCorruptError(
            "Document text encoding is unsupported or invalid."
        ) from error


def _validate_decoded_text(text: str) -> None:
    """Reject binary controls while preserving all safe source spelling."""

    for character in text:
        if (
            unicodedata.category(character) == "Cs"
            or (
                unicodedata.category(character) == "Cc"
                and character not in _ALLOWED_CONTROL_CHARACTERS
            )
        ):
            raise DocumentCorruptError(
                "Document contains binary or unsafe control characters."
            )


def _has_meaningful_text(text: str) -> bool:
    """Return whether text contains content beyond whitespace and a BOM."""

    return any(
        not (character.isspace() or character == _ZERO_WIDTH_NO_BREAK_SPACE)
        for character in text
    )


def _line_span_at(text: str, start: int) -> _LineSpan | None:
    """Return one CR/LF-aware line span using constant auxiliary memory."""

    if start >= len(text):
        return None
    match = _LINE_BREAK.search(text, start)
    if match is None:
        return _LineSpan(start=start, content_end=len(text), end=len(text))
    # ``str.splitlines(keepends=True)`` historically treated Unicode line and
    # paragraph separators as line boundaries while leaving them visible to
    # this loader's CR/LF-only structural grammar.  Preserve that output rule
    # without recreating its unbounded list of copied line strings.
    content_end = (
        match.end()
        if text[match.start()] in {"\u2028", "\u2029"}
        else match.start()
    )
    end = match.end()
    if (
        text[match.start()] == "\r"
        and end < len(text)
        and text[end] == "\n"
    ):
        end += 1
    return _LineSpan(start=start, content_end=content_end, end=end)


def _is_blank_span(text: str, line: _LineSpan) -> bool:
    """Use Markdown's fixed ASCII blank-line grammar without slicing text."""

    return _span_is_ascii_space(text, line.start, line.content_end)


def _span_is_ascii_space(text: str, start: int, end: int) -> bool:
    """Return whether a source span contains only ASCII space or tab."""

    return all(text[index] in " \t" for index in range(start, end))


def _trim_ascii_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Trim structural ASCII padding by returning adjusted source offsets."""

    while start < end and text[start] in " \t":
        start += 1
    while end > start and text[end - 1] in " \t":
        end -= 1
    return start, end


def _span_has_meaningful_text(text: str, start: int, end: int) -> bool:
    """Check a source span for meaningful text without allocating a slice."""

    return any(
        not (
            text[index].isspace()
            or text[index] == _ZERO_WIDTH_NO_BREAK_SPACE
        )
        for index in range(start, end)
    )


def _require_pending_text_budget(
    builder: _OutputBuilder,
    pending_code_points: int,
) -> None:
    """Reject a growing span before it can become an oversized string."""

    if (
        builder.text_code_points + pending_code_points
        > builder.limits.max_text_code_points
    ):
        raise DocumentContentLimitError(
            "Document text exceeds the configured code-point limit."
        )


def _load_plain_text(text: str, builder: _OutputBuilder) -> None:
    """Preserve paragraphs with one cursor and no per-line accumulation."""

    paragraph_start: int | None = None
    paragraph_end = 0
    cursor = 0
    while (line := _line_span_at(text, cursor)) is not None:
        if _is_blank_span(text, line):
            _flush_paragraph_span(
                text,
                paragraph_start,
                paragraph_end,
                builder,
            )
            paragraph_start = None
        else:
            if paragraph_start is None:
                paragraph_start = line.start
            paragraph_end = line.end
            _require_pending_text_budget(
                builder,
                paragraph_end - paragraph_start,
            )
        cursor = line.end
    _flush_paragraph_span(text, paragraph_start, paragraph_end, builder)


def _flush_paragraph_span(
    text: str,
    start: int | None,
    end: int,
    builder: _OutputBuilder,
) -> None:
    """Publish one already-budgeted paragraph from its source span."""

    if start is not None:
        builder.add_text("paragraph", text[start:end])


def _load_markdown(
    text: str,
    builder: _OutputBuilder,
) -> DocumentTitle | None:
    """Scan Markdown with a cursor and at most one line of lookahead."""

    paragraph_start: int | None = None
    paragraph_end = 0
    title: DocumentTitle | None = None
    cursor = 0
    while (line := _line_span_at(text, cursor)) is not None:
        next_line = _line_span_at(text, line.end)
        if _is_blank_span(text, line):
            _flush_paragraph_span(
                text,
                paragraph_start,
                paragraph_end,
                builder,
            )
            paragraph_start = None
            cursor = line.end
            continue

        fence = _FENCE_OPEN.fullmatch(text, line.start, line.content_end)
        if fence is not None and _valid_fence_info(text, fence):
            _flush_paragraph_span(
                text,
                paragraph_start,
                paragraph_end,
                builder,
            )
            paragraph_start = None
            cursor = _load_fenced_code(text, line, fence, builder)
            continue

        heading = _atx_heading(text, line)
        if heading is not None:
            _flush_paragraph_span(
                text,
                paragraph_start,
                paragraph_end,
                builder,
            )
            paragraph_start = None
            level, heading_start, heading_end = heading
            _require_pending_text_budget(
                builder,
                heading_end - heading_start,
            )
            heading_text = text[heading_start:heading_end]
            builder.add_text(
                "heading",
                heading_text,
                heading_level=level,
            )
            if level == 1 and title is None:
                title = builder.reserve_title(heading_text)
            cursor = line.end
            continue

        if next_line is not None:
            setext = _setext_level(text, next_line)
            if setext is not None:
                heading_start, heading_end = _trim_ascii_span(
                    text,
                    (
                        line.start
                        if paragraph_start is None
                        else paragraph_start
                    ),
                    line.content_end,
                )
                if _span_has_meaningful_text(
                    text,
                    heading_start,
                    heading_end,
                ):
                    # CommonMark permits a Setext heading to contain multiple
                    # consecutive source lines.  Folding the pending paragraph
                    # into this span preserves that structure without copying
                    # or double-counting the earlier lines.
                    paragraph_start = None
                    _require_pending_text_budget(
                        builder,
                        heading_end - heading_start,
                    )
                    heading_text = text[heading_start:heading_end]
                    builder.add_text(
                        "heading",
                        heading_text,
                        heading_level=setext,
                    )
                    if setext == 1 and title is None:
                        title = builder.reserve_title(heading_text)
                    cursor = next_line.end
                    continue

            pending_blocks = 0
            pending_text_code_points = 0
            if (
                paragraph_start is not None
                and _span_has_meaningful_text(
                    text,
                    paragraph_start,
                    paragraph_end,
                )
            ):
                pending_blocks = 1
                pending_text_code_points = paragraph_end - paragraph_start
            table = _markdown_table_start(
                text,
                line,
                next_line,
                builder,
                pending_blocks=pending_blocks,
                pending_text_code_points=pending_text_code_points,
            )
            if table is not None:
                _flush_paragraph_span(
                    text,
                    paragraph_start,
                    paragraph_end,
                    builder,
                )
                paragraph_start = None
                rows, cursor = table
                builder.add_table(rows)
                continue

        if paragraph_start is None:
            paragraph_start = line.start
        paragraph_end = line.end
        _require_pending_text_budget(
            builder,
            paragraph_end - paragraph_start,
        )
        cursor = line.end

    _flush_paragraph_span(text, paragraph_start, paragraph_end, builder)
    return title


def _atx_heading(
    text: str,
    line: _LineSpan,
) -> tuple[int, int, int] | None:
    """Return one meaningful ATX heading as level and source offsets."""

    match = _ATX_HEADING.fullmatch(text, line.start, line.content_end)
    if match is None or match.start("text") == -1:
        return None
    start, end = _trim_ascii_span(
        text,
        match.start("text"),
        match.end("text"),
    )
    hash_start = end
    while hash_start > start and text[hash_start - 1] == "#":
        hash_start -= 1
    if hash_start < end and hash_start > start and text[hash_start - 1] in " \t":
        _, end = _trim_ascii_span(text, start, hash_start)
    if not _span_has_meaningful_text(text, start, end):
        return None
    return match.end("marker") - match.start("marker"), start, end


def _setext_level(text: str, line: _LineSpan) -> int | None:
    """Return the heading level represented by one Setext underline."""

    match = _SETEXT_HEADING.fullmatch(text, line.start, line.content_end)
    if match is None:
        return None
    return 1 if text[match.start("marker")] == "=" else 2


def _valid_fence_info(text: str, match: re.Match[str]) -> bool:
    """Reject a backtick info span containing another backtick."""

    marker_is_backtick = text[match.start("marker")] == "`"
    return not marker_is_backtick or text.find(
        "`",
        match.start("info"),
        match.end("info"),
    ) == -1


def _load_fenced_code(
    text: str,
    opener_line: _LineSpan,
    opener: re.Match[str],
    builder: _OutputBuilder,
) -> int:
    """Load a fence through spans so short lines never form an auxiliary list."""

    marker_character = text[opener.start("marker")]
    minimum_length = opener.end("marker") - opener.start("marker")
    code_start = opener_line.end
    cursor = code_start
    while (line := _line_span_at(text, cursor)) is not None:
        if _is_fence_close(text, line, marker_character, minimum_length):
            _require_pending_text_budget(builder, line.start - code_start)
            builder.add_text("code", text[code_start:line.start])
            return line.end
        _require_pending_text_budget(builder, line.end - code_start)
        cursor = line.end
    _require_pending_text_budget(builder, len(text) - code_start)
    builder.add_text("code", text[code_start:])
    return len(text)


def _is_fence_close(
    text: str,
    line: _LineSpan,
    marker_character: str,
    minimum_length: int,
) -> bool:
    """Recognize one matching fence close without copying its line."""

    cursor = line.start
    indentation = 0
    while cursor < line.content_end and text[cursor] == " " and indentation <= 3:
        cursor += 1
        indentation += 1
    if indentation > 3:
        return False
    marker_start = cursor
    while cursor < line.content_end and text[cursor] == marker_character:
        cursor += 1
    if cursor - marker_start < minimum_length:
        return False
    return _span_is_ascii_space(text, cursor, line.content_end)


def _markdown_table_start(
    text: str,
    header_line: _LineSpan,
    delimiter_line: _LineSpan,
    builder: _OutputBuilder,
    *,
    pending_blocks: int,
    pending_text_code_points: int,
) -> tuple[list[tuple[str, ...]], int] | None:
    """Parse a GFM table while reserving unpublished paragraph budgets."""

    # Validate the delimiter before materializing a candidate header.  Ordinary
    # prose can contain arbitrarily many pipes or long spans, and those must not
    # inherit table budgets unless the following line establishes table syntax.
    delimiter_columns = _count_delimiter_columns(
        text,
        delimiter_line,
        builder.limits,
    )
    if delimiter_columns is None:
        return None

    header = _parse_pipe_row(text, header_line, builder.limits)
    if header is None or len(header) != delimiter_columns:
        return None

    # A pending paragraph has already been scanned but not published.  Reserve
    # both it and this table before retaining any body rows so a full block or
    # text budget cannot cause a large temporary table allocation.
    if len(builder.blocks) + pending_blocks + 1 > builder.limits.max_blocks:
        raise DocumentContentLimitError(
            "Document block count exceeds the configured limit."
        )

    _validate_pending_table_row(
        header,
        builder,
        added_cells=0,
        added_text=0,
        pending_text_code_points=pending_text_code_points,
    )
    rows = [header]
    added_cells = len(header)
    added_text = sum(len(cell) for cell in header)
    cursor = delimiter_line.end
    while (line := _line_span_at(text, cursor)) is not None:
        if _is_blank_span(text, line):
            break
        row = _parse_pipe_row(text, line, builder.limits)
        if row is None:
            break
        _validate_pending_table_row(
            row,
            builder,
            added_cells=added_cells,
            added_text=added_text,
            pending_text_code_points=pending_text_code_points,
        )
        rows.append(row)
        added_cells += len(row)
        added_text += sum(len(cell) for cell in row)
        cursor = line.end
    return rows, cursor


def _validate_pending_table_row(
    row: tuple[str, ...],
    builder: _OutputBuilder,
    *,
    added_cells: int,
    added_text: int,
    pending_text_code_points: int,
) -> None:
    """Enforce Markdown table budgets before retaining another parsed row."""

    if len(row) > builder.limits.max_table_columns:
        raise DocumentContentLimitError(
            "Document table exceeds the configured column limit."
        )
    if (
        builder.table_cells + added_cells + len(row)
        > builder.limits.max_table_cells
    ):
        raise DocumentContentLimitError(
            "Document tables exceed the configured cell limit."
        )
    row_text = 0
    for cell in row:
        if len(cell) > builder.limits.max_cell_code_points:
            raise DocumentContentLimitError(
                "Document table cell exceeds the configured limit."
            )
        row_text += len(cell)
    if (
        builder.text_code_points
        + pending_text_code_points
        + added_text
        + row_text
        > builder.limits.max_text_code_points
    ):
        raise DocumentContentLimitError(
            "Document text exceeds the configured code-point limit."
        )


def _count_delimiter_columns(
    text: str,
    line: _LineSpan,
    limits: DocumentLoadLimits,
) -> int | None:
    """Validate a delimiter row without allocating its cell strings."""

    count = 0
    for start, end in _iter_pipe_cell_spans(
        text,
        line,
        limits.max_table_columns + 1,
    ):
        if _TABLE_DELIMITER_CELL.fullmatch(text, start, end) is None:
            return None
        count += 1
        # A header that fits the configured width can never match a wider
        # delimiter.  Return a mismatch sentinel instead of misclassifying
        # ordinary Markdown as an over-limit table.
        if count > limits.max_table_columns:
            return count
    return count or None


def _parse_pipe_row(
    text: str,
    line: _LineSpan,
    limits: DocumentLoadLimits,
) -> tuple[str, ...] | None:
    """Materialize only a budgeted number of cells from one pipe row."""

    cells: list[str] = []
    for start, end in _iter_pipe_cell_spans(
        text,
        line,
        limits.max_table_columns,
    ):
        if end - start > limits.max_cell_code_points:
            raise DocumentContentLimitError(
                "Document table cell exceeds the configured limit."
            )
        cells.append(text[start:end])
    return tuple(cells) if cells else None


def _iter_pipe_cell_spans(
    text: str,
    line: _LineSpan,
    max_columns: int,
) -> Iterator[tuple[int, int]]:
    """Yield trimmed cells while bounding columns before each new cell."""

    cell_start = line.start
    active_code_ticks: int | None = None
    backslash_run = 0
    saw_separator = False
    yielded = 0
    cursor = line.start
    while cursor < line.content_end:
        character = text[cursor]
        if character == "\\":
            backslash_run += 1
            cursor += 1
            continue
        if character == "`":
            tick_end = cursor + 1
            while tick_end < line.content_end and text[tick_end] == "`":
                tick_end += 1
            tick_count = tick_end - cursor
            if active_code_ticks is None:
                active_code_ticks = tick_count
            elif active_code_ticks == tick_count:
                active_code_ticks = None
            backslash_run = 0
            cursor = tick_end
            continue
        if (
            character == "|"
            and active_code_ticks is None
            and backslash_run % 2 == 0
        ):
            if saw_separator or not _span_is_ascii_space(
                text,
                cell_start,
                cursor,
            ):
                if yielded >= max_columns:
                    raise DocumentContentLimitError(
                        "Document table exceeds the configured column limit."
                    )
                yielded += 1
                yield _trim_ascii_span(text, cell_start, cursor)
            saw_separator = True
            cell_start = cursor + 1
        backslash_run = 0
        cursor += 1

    if not saw_separator:
        return
    if not _span_is_ascii_space(text, cell_start, line.content_end):
        if yielded >= max_columns:
            raise DocumentContentLimitError(
                "Document table exceeds the configured column limit."
            )
        yield _trim_ascii_span(text, cell_start, line.content_end)


def _load_csv(text: str, builder: _OutputBuilder) -> None:
    """Parse strict comma-separated records with a local linear state machine.

    A local parser avoids ``csv.field_size_limit``, whose process-global value
    would let concurrent callers change this loader's configured cell budget.
    Spreadsheet formula prefixes are ordinary text and are never evaluated.
    """

    rows: list[tuple[str, ...]] = []
    row: list[str] = []
    field: list[str] = []
    in_quotes = False
    after_quote = False
    record_open = False
    parsed_cells = 0
    parsed_text_code_points = 0
    index = 0
    while index < len(text):
        character = text[index]
        if in_quotes:
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    parsed_text_code_points = _append_csv_character(
                        field,
                        '"',
                        parsed_text_code_points=parsed_text_code_points,
                        limits=builder.limits,
                    )
                    index += 2
                    continue
                in_quotes = False
                after_quote = True
            else:
                parsed_text_code_points = _append_csv_character(
                    field,
                    character,
                    parsed_text_code_points=parsed_text_code_points,
                    limits=builder.limits,
                )
            index += 1
            continue

        if after_quote:
            if character == ",":
                _finish_csv_field(row, field, builder.limits)
                after_quote = False
                record_open = True
                index += 1
                continue
            newline_width = _csv_newline_width(text, index)
            if newline_width:
                _finish_csv_field(row, field, builder.limits)
                parsed_cells = _finish_csv_row(
                    rows,
                    row,
                    parsed_cells,
                    builder.limits,
                )
                after_quote = False
                record_open = False
                index += newline_width
                continue
            raise DocumentCorruptError(
                "CSV contains characters after a closing quote."
            )

        if character == '"':
            if field:
                raise DocumentCorruptError(
                    "CSV contains a quote inside an unquoted field."
                )
            in_quotes = True
            record_open = True
            index += 1
            continue
        if character == ",":
            _finish_csv_field(row, field, builder.limits)
            record_open = True
            index += 1
            continue
        newline_width = _csv_newline_width(text, index)
        if newline_width:
            _finish_csv_field(row, field, builder.limits)
            parsed_cells = _finish_csv_row(
                rows,
                row,
                parsed_cells,
                builder.limits,
            )
            record_open = False
            index += newline_width
            continue
        parsed_text_code_points = _append_csv_character(
            field,
            character,
            parsed_text_code_points=parsed_text_code_points,
            limits=builder.limits,
        )
        record_open = True
        index += 1

    if in_quotes:
        raise DocumentCorruptError("CSV contains an unterminated quoted field.")
    if record_open or row or field or after_quote:
        _finish_csv_field(row, field, builder.limits)
        _finish_csv_row(rows, row, parsed_cells, builder.limits)
    if not rows:
        raise DocumentEmptyError("CSV contains no loadable records.")
    builder.add_table(rows)


def _append_csv_character(
    field: list[str],
    character: str,
    *,
    parsed_text_code_points: int,
    limits: DocumentLoadLimits,
) -> int:
    """Append one cell character without allowing an oversized allocation."""

    if len(field) >= limits.max_cell_code_points:
        raise DocumentContentLimitError(
            "Document table cell exceeds the configured limit."
        )
    parsed_text_code_points += 1
    if parsed_text_code_points > limits.max_text_code_points:
        raise DocumentContentLimitError(
            "Document text exceeds the configured code-point limit."
        )
    field.append(character)
    return parsed_text_code_points


def _finish_csv_field(
    row: list[str],
    field: list[str],
    limits: DocumentLoadLimits,
) -> None:
    """Finish one field while bounding the current row width."""

    if len(row) >= limits.max_table_columns:
        raise DocumentContentLimitError(
            "Document table exceeds the configured column limit."
        )
    row.append("".join(field))
    field.clear()


def _finish_csv_row(
    rows: list[tuple[str, ...]],
    row: list[str],
    parsed_cells: int,
    limits: DocumentLoadLimits,
) -> int:
    """Finish one non-empty structural row and bound aggregate cells early."""

    candidate = tuple(row)
    row.clear()
    if not candidate:
        candidate = ("",)
    parsed_cells += len(candidate)
    if parsed_cells > limits.max_table_cells:
        raise DocumentContentLimitError(
            "Document tables exceed the configured cell limit."
        )
    rows.append(candidate)
    return parsed_cells


def _csv_newline_width(text: str, index: int) -> int:
    """Return the width of one CRLF, CR, or LF record terminator."""

    if text[index] == "\r":
        return 2 if index + 1 < len(text) and text[index + 1] == "\n" else 1
    return 1 if text[index] == "\n" else 0
