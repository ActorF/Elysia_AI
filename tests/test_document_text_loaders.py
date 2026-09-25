"""Verify deterministic, bounded, path-free loading of textual documents."""

from __future__ import annotations

import builtins
import codecs
import tracemalloc
from dataclasses import replace

import pytest

import documents.text as text_module
from attachments import AttachmentScope
from documents.domain import (
    MAX_DOCUMENT_TITLE_CODE_POINTS,
    DocumentLoadLimits,
    DocumentSource,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentCorruptError,
    DocumentEmptyError,
    DocumentReadError,
    DocumentTooLargeError,
    DocumentUnsupportedFormatError,
    DocumentValidationError,
)
from documents.protocol import DocumentLoader
from documents.text import TextDocumentLoader


_FILE_ID = f"file_{'a' * 64}"
_SCOPE = AttachmentScope(kind="project", id="project_text_loaders")


def _source(file_name: str, media_type: str, data: bytes) -> DocumentSource:
    return DocumentSource(
        scope=_SCOPE,
        link_id="attachment_text_source",
        file_id=_FILE_ID,
        file_name=file_name,
        media_type=media_type,
        size_bytes=len(data),
    )


def _load(
    file_name: str,
    media_type: str,
    data: bytes,
    limits: DocumentLoadLimits | None = None,
):
    return TextDocumentLoader().load(
        _source(file_name, media_type, data),
        data,
        DocumentLoadLimits() if limits is None else limits,
    )


def test_loader_exposes_the_closed_text_route_set() -> None:
    """Advertise every existing textual attachment route and no binary route."""

    loader: DocumentLoader = TextDocumentLoader()

    assert loader.loader_id == "text-structure"
    assert loader.loader_version == "1.0.0"
    assert loader.routes == frozenset({
        (".txt", "text/plain"),
        (".md", "text/markdown"),
        (".markdown", "text/markdown"),
        (".csv", "text/csv"),
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


@pytest.mark.parametrize(
    ("file_name", "media_type"),
    (
        ("settings.INI", "text/plain"),
        ("site.css", "text/css"),
        ("index.htm", "text/html"),
        ("index.HTML", "text/html"),
        ("app.js", "text/javascript"),
        ("view.jsx", "text/javascript"),
        ("data.json", "application/json"),
        ("main.py", "text/x-python"),
        ("query.sql", "application/sql"),
        ("pyproject.toml", "application/toml"),
        ("main.ts", "text/typescript"),
        ("view.tsx", "text/typescript"),
        ("data.xml", "application/xml"),
        ("config.yaml", "application/yaml"),
        ("config.yml", "application/yaml"),
    ),
)
def test_code_routes_remain_one_literal_code_block(
    file_name: str,
    media_type: str,
) -> None:
    """Treat code-like formats as inert text without syntax execution."""

    data = b"<script>fetch('https://example.invalid')</script>\n"

    loaded = _load(file_name, media_type, data)

    assert loaded.document_format == "code"
    assert len(loaded.blocks) == 1
    assert loaded.blocks[0].kind == "code"
    assert loaded.blocks[0].text == data.decode("utf-8")


def test_route_requires_both_the_suffix_and_canonical_media_type() -> None:
    """Fail closed when a direct caller supplies one mismatched routing key."""

    data = b"safe"
    loader = TextDocumentLoader()

    with pytest.raises(DocumentUnsupportedFormatError, match="suffix"):
        loader.load(
            _source("notes.txt", "text/markdown", data),
            data,
            DocumentLoadLimits(),
        )
    with pytest.raises(DocumentUnsupportedFormatError, match="suffix"):
        loader.load(
            _source("notes.exe", "text/plain", data),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "data",
    (
        b"\xff",
        b"h\x00i\x00",
        codecs.BOM_UTF32_LE + "hello".encode("utf-32-le"),
        b"safe\x00binary",
        b"safe\x07control",
    ),
)
def test_loader_rejects_ambiguous_or_binary_text(data: bytes) -> None:
    """Reject invalid UTF-8, BOM-less UTF-16, UTF-32, NUL, and controls."""

    with pytest.raises(DocumentCorruptError):
        _load("notes.txt", "text/plain", data)


@pytest.mark.parametrize(
    ("data", "expected"),
    (
        ("你好 UTF-8".encode("utf-8"), "你好 UTF-8"),
        (codecs.BOM_UTF8 + "BOM text".encode("utf-8"), "BOM text"),
        (
            codecs.BOM_UTF16_LE + "小记".encode("utf-16-le"),
            "小记",
        ),
        (
            codecs.BOM_UTF16_BE + "Notes".encode("utf-16-be"),
            "Notes",
        ),
    ),
)
def test_loader_decodes_only_explicit_unambiguous_unicode(
    data: bytes,
    expected: str,
) -> None:
    """Decode strict UTF-8 and BOM-declared UTF-16 without replacement."""

    loaded = _load("notes.txt", "text/plain", data)

    assert [block.text for block in loaded.blocks] == [expected]


def test_plain_text_preserves_paragraph_spelling_and_line_endings() -> None:
    """Separate paragraphs without normalizing their retained source text."""

    text = "first\r\ncontinued\r\n \t\r\nsecond\n"

    loaded = _load("notes.TXT", "text/plain", text.encode())

    assert loaded.document_format == "text"
    assert [block.kind for block in loaded.blocks] == [
        "paragraph",
        "paragraph",
    ]
    assert [block.text for block in loaded.blocks] == [
        "first\r\ncontinued\r\n",
        "second\n",
    ]


def test_unicode_line_separator_keeps_existing_structural_semantics() -> None:
    """Preserve raw output while replacing splitlines with bounded spans."""

    data = "# first\u2028second".encode("utf-8")

    loaded = _load("separator.md", "text/markdown", data)

    assert loaded.title is not None
    assert loaded.title.text == "first\u2028"
    assert tuple(block.text for block in loaded.blocks) == (
        "first\u2028",
        "second",
    )


def test_unicode_line_separator_at_end_does_not_read_past_source() -> None:
    """Preserve a final Unicode separator without indexing beyond the text."""

    data = "last\u2028".encode("utf-8")

    loaded = _load("separator.txt", "text/plain", data)

    assert tuple(block.text for block in loaded.blocks) == ("last\u2028",)


def test_markdown_preserves_headings_paragraphs_and_fenced_code() -> None:
    """Extract headings and fences while leaving inline Markdown unrendered."""

    text = (
        "# Guide #\n\n"
        "Keep *inline* [links](https://example.invalid) literal.\n\n"
        "```python\n"
        "# not a heading\n"
        "| not | a table |\n"
        "```\n\n"
        "Details\n"
        "---\n"
    )

    loaded = _load("guide.md", "text/markdown", text.encode())

    assert loaded.document_format == "markdown"
    assert loaded.title is not None
    assert (loaded.title.text, loaded.title.source) == ("Guide", "heading")
    assert [block.kind for block in loaded.blocks] == [
        "heading",
        "paragraph",
        "code",
        "heading",
    ]
    assert [block.heading_level for block in loaded.blocks] == [1, None, None, 2]
    assert loaded.blocks[1].text == (
        "Keep *inline* [links](https://example.invalid) literal.\n"
    )
    assert loaded.blocks[2].text == "# not a heading\n| not | a table |\n"
    assert loaded.blocks[3].text == "Details"


def test_markdown_preserves_multiline_setext_heading_structure() -> None:
    """Treat all consecutive paragraph lines before an underline as heading."""

    loaded = _load(
        "heading.md",
        "text/markdown",
        b"First line\nSecond line\n===\n",
    )

    assert loaded.title is not None
    assert loaded.title.text == "First line\nSecond line"
    assert tuple(
        (block.kind, block.heading_level, block.text)
        for block in loaded.blocks
    ) == (("heading", 1, "First line\nSecond line"),)


def test_markdown_pipe_table_preserves_escaped_and_ragged_cells() -> None:
    """Parse a conservative pipe table without padding or truncating rows."""

    text = (
        "| Name | Expression |\n"
        "| :--- | ---: |\n"
        "| a\\|b | `x|y` |\n"
        "| only one |\n"
    )

    loaded = _load("table.markdown", "text/markdown", text.encode())

    assert len(loaded.blocks) == 1
    table = loaded.blocks[0].table
    assert table is not None
    assert table.rows == (
        ("Name", "Expression"),
        (r"a\|b", "`x|y`"),
        ("only one",),
    )


def test_malformed_markdown_table_remains_literal_paragraph_text() -> None:
    """Avoid inventing table semantics when the delimiter row is invalid."""

    text = "| A | B |\n| -- | nope |\n| 1 | 2 |\n"

    loaded = _load("table.md", "text/markdown", text.encode())

    assert len(loaded.blocks) == 1
    assert loaded.blocks[0].kind == "paragraph"
    assert loaded.blocks[0].text == text


def test_pipe_prose_does_not_inherit_table_candidate_limits() -> None:
    """Apply table budgets only after a following line establishes syntax."""

    data = b"oversized|candidate\nordinary prose\n"
    loaded = _load(
        "notes.md",
        "text/markdown",
        data,
        replace(
            DocumentLoadLimits(),
            max_table_cells=1,
            max_table_columns=1,
            max_cell_code_points=1,
        ),
    )

    assert tuple(block.text for block in loaded.blocks) == (data.decode(),)


def test_non_table_markdown_does_not_inherit_delimiter_column_limits() -> None:
    """Keep ordinary prose literal when only its next line resembles a table."""

    data = b"intro\n---|---|---\n"
    loaded = _load(
        "notes.md",
        "text/markdown",
        data,
        replace(
            DocumentLoadLimits(),
            max_table_columns=2,
            max_table_cells=2,
        ),
    )

    assert tuple(block.text for block in loaded.blocks) == (data.decode(),)


def test_markdown_table_reserves_block_before_collecting_body_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop at a full block budget before retaining table body data."""

    original_parse = text_module._parse_pipe_row
    parse_calls = 0

    def counted_parse(*args: object, **kwargs: object):
        """Reject a second row parse, which would mean body collection began."""

        nonlocal parse_calls
        parse_calls += 1
        if parse_calls > 1:
            raise AssertionError("table body parsing began after a full budget")
        return original_parse(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(text_module, "_parse_pipe_row", counted_parse)

    with pytest.raises(DocumentContentLimitError, match="block"):
        _load(
            "table.md",
            "text/markdown",
            b"# intro\n| heading |\n| --- |\n| body |\n",
            replace(DocumentLoadLimits(), max_blocks=1),
        )

    assert parse_calls == 1


def test_ignored_unicode_whitespace_does_not_reserve_a_phantom_block() -> None:
    """Do not charge an unpublished whitespace paragraph before a table."""

    data = "\u00a0\n| heading |\n| --- |\n| body |\n".encode("utf-8")

    loaded = _load(
        "table.md",
        "text/markdown",
        data,
        replace(DocumentLoadLimits(), max_blocks=1),
    )

    assert len(loaded.blocks) == 1
    assert loaded.blocks[0].kind == "table"


def test_unclosed_markdown_fence_is_bounded_code_until_eof() -> None:
    """Treat an unclosed fence deterministically instead of reparsing its body."""

    loaded = _load(
        "code.md",
        "text/markdown",
        b"~~~ts\n# still code\n",
    )

    assert [(block.kind, block.text) for block in loaded.blocks] == [
        ("code", "# still code\n")
    ]


def test_csv_preserves_records_quotes_newlines_and_formula_text() -> None:
    """Parse CSV format syntax but never execute spreadsheet expressions."""

    text = (
        "name,value\r\n"
        '"alpha,beta","line1\r\nline2"\r\n'
        '=SUM(A1:A2),"""quoted"""\r\n'
        ",\r\n"
    )

    loaded = _load("table.csv", "text/csv", text.encode())

    assert loaded.document_format == "csv"
    assert len(loaded.blocks) == 1
    table = loaded.blocks[0].table
    assert table is not None
    assert table.rows == (
        ("name", "value"),
        ("alpha,beta", "line1\r\nline2"),
        ("=SUM(A1:A2)", '"quoted"'),
        ("", ""),
    )


def test_csv_whitespace_cell_is_independent_of_quoting() -> None:
    """Preserve equivalent whitespace cells whether quoted or unquoted."""

    unquoted = _load("plain.csv", "text/csv", b" ")
    quoted = _load("quoted.csv", "text/csv", b'" "')

    assert unquoted.blocks[0].table is not None
    assert quoted.blocks[0].table is not None
    assert unquoted.blocks[0].table.rows == ((" ",),)
    assert quoted.blocks[0].table.rows == ((" ",),)


@pytest.mark.parametrize(
    "text",
    (
        'a"b,c',
        '"a"x,b',
        '"unterminated',
    ),
)
def test_csv_rejects_malformed_quote_state(text: str) -> None:
    """Reject ambiguous quotes instead of recovering with altered records."""

    with pytest.raises(DocumentCorruptError, match="CSV"):
        _load("bad.csv", "text/csv", text.encode())


def test_source_and_text_budgets_fail_without_partial_results() -> None:
    """Enforce byte and aggregate output budgets at their exact boundaries."""

    data = b"abcd"
    base = DocumentLoadLimits()

    accepted = _load(
        "notes.txt",
        "text/plain",
        data,
        replace(
            base,
            max_source_bytes=len(data),
            max_text_code_points=4,
            max_cell_code_points=4,
        ),
    )
    assert accepted.blocks[0].text == "abcd"

    with pytest.raises(DocumentTooLargeError):
        _load(
            "notes.txt",
            "text/plain",
            data,
            replace(base, max_source_bytes=len(data) - 1),
        )
    with pytest.raises(DocumentContentLimitError, match="code-point"):
        _load(
            "notes.txt",
            "text/plain",
            data,
            replace(base, max_text_code_points=3, max_cell_code_points=3),
        )


def test_structure_and_table_budgets_are_enforced_during_parsing() -> None:
    """Bound blocks, columns, cells, and individual cells independently."""

    base = DocumentLoadLimits()
    with pytest.raises(DocumentContentLimitError, match="block"):
        _load(
            "notes.txt",
            "text/plain",
            b"one\n\ntwo",
            replace(base, max_blocks=1),
        )
    with pytest.raises(DocumentContentLimitError, match="column"):
        _load(
            "table.csv",
            "text/csv",
            b"a,b,c",
            replace(base, max_table_columns=2),
        )
    with pytest.raises(DocumentContentLimitError, match="cell"):
        _load(
            "table.csv",
            "text/csv",
            b"a,b\nc,d",
            replace(base, max_table_cells=3, max_table_columns=3),
        )
    with pytest.raises(DocumentContentLimitError, match="cell"):
        _load(
            "table.csv",
            "text/csv",
            b"abcd",
            replace(base, max_cell_code_points=3),
        )
    with pytest.raises(DocumentContentLimitError, match="cell"):
        _load(
            "table.md",
            "text/markdown",
            b"| a | b |\n| --- | --- |\n| c | d |",
            replace(base, max_table_cells=3, max_table_columns=3),
        )


def test_title_budget_counts_heading_metadata_and_its_source_block() -> None:
    """Reject an oversized Markdown title before publishing an invalid result."""

    title = "x" * (MAX_DOCUMENT_TITLE_CODE_POINTS + 1)

    with pytest.raises(DocumentContentLimitError, match="title"):
        _load("title.md", "text/markdown", f"# {title}".encode())


@pytest.mark.parametrize(
    "data",
    (
        b" \t\r\n",
        codecs.BOM_UTF8,
        b"```\n```\n",
    ),
)
def test_loader_rejects_sources_without_loadable_content(data: bytes) -> None:
    """Do not manufacture a block for whitespace, a BOM, or an empty fence."""

    with pytest.raises(DocumentEmptyError):
        _load("empty.md", "text/markdown", data)


def test_loader_is_deterministic_and_performs_no_filesystem_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Produce equal results from bytes alone even when all open calls fail."""

    data = b"# Stable\n\nSame bytes.\n"
    loader = TextDocumentLoader()
    source = _source("stable.md", "text/markdown", data)
    limits = DocumentLoadLimits()

    def reject_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("A byte loader must not open a path.")

    monkeypatch.setattr(builtins, "open", reject_open)

    assert loader.load(source, data, limits) == loader.load(source, data, limits)


def test_loader_rejects_mutable_or_size_mismatched_snapshots() -> None:
    """Require the exact immutable snapshot described by verified metadata."""

    data = b"notes"
    loader = TextDocumentLoader()
    source = _source("notes.txt", "text/plain", data)

    with pytest.raises(DocumentValidationError, match="immutable"):
        loader.load(source, bytearray(data), DocumentLoadLimits())  # type: ignore[arg-type]
    with pytest.raises(DocumentReadError, match="metadata"):
        loader.load(
            replace(source, size_bytes=len(data) + 1),
            data,
            DocumentLoadLimits(),
        )


def test_many_short_lines_do_not_create_a_per_line_object_graph() -> None:
    """Keep paragraph scanning memory flat across many retained short lines."""

    data = b"x\n" * 250_000
    limits = replace(
        DocumentLoadLimits(),
        max_text_code_points=len(data),
    )

    tracemalloc.start()
    try:
        loaded = _load("many-lines.txt", "text/plain", data, limits)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(loaded.blocks) == 1
    assert loaded.blocks[0].text == data.decode("utf-8")
    # A split-lines list for 250,000 entries is tens of MiB.  This generous
    # ceiling allows interpreter variance while still detecting that shape.
    assert peak_bytes < 8 * 1024 * 1024


def test_million_pipe_header_hits_column_budget_before_offset_allocation() -> None:
    """Reject an adversarial table header without retaining all separators."""

    data = (("|" * 1_000_000) + "\n| --- |\n").encode("utf-8")
    limits = replace(
        DocumentLoadLimits(),
        max_table_columns=8,
        max_table_cells=64,
    )

    tracemalloc.start()
    try:
        with pytest.raises(DocumentContentLimitError, match="column"):
            _load("many-pipes.md", "text/markdown", data, limits)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # The decoded source itself is about one MiB.  An unbounded separator list
    # would exceed this ceiling by a wide margin before checking eight columns.
    assert peak_bytes < 8 * 1024 * 1024
