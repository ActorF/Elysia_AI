"""Test bounded, path-private document-loader domain invariants."""

from dataclasses import fields, replace

import pytest

from attachments import MAX_JSON_SAFE_INTEGER, AttachmentScope
from documents.domain import (
    DOCUMENT_SCHEMA_VERSION,
    MAX_DOCUMENT_TITLE_CODE_POINTS,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTable,
    DocumentTitle,
    LoadedDocument,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentTooLargeError,
    DocumentValidationError,
)


_DIGEST = "a" * 64


def _source(
    *,
    file_name: str = "lesson.md",
    media_type: str = "text/markdown",
    size_bytes: int = 4,
) -> DocumentSource:
    """Build one authorized source with no filesystem location."""

    return DocumentSource(
        scope=AttachmentScope(kind="chat", id="chat_lesson"),
        link_id="attachment_lesson",
        file_id=f"file_{_DIGEST}",
        file_name=file_name,
        media_type=media_type,
        size_bytes=size_bytes,
    )


def _limits(**changes: int) -> DocumentLoadLimits:
    """Build compact limits that focused tests can exceed deliberately."""

    defaults = {
        "max_source_bytes": 100,
        "max_expanded_bytes": 200,
        "max_text_code_points": 100,
        "max_blocks": 10,
        "max_pages": 10,
        "max_package_entries": 10,
        "max_table_cells": 20,
        "max_table_columns": 10,
        "max_cell_code_points": 20,
        "max_xml_elements": 100,
        "max_xml_depth": 10,
    }
    defaults.update(changes)
    return DocumentLoadLimits(**defaults)


def _loaded_document(
    *,
    source: DocumentSource | None = None,
    blocks: tuple[DocumentBlock, ...] | None = None,
    limits: DocumentLoadLimits | None = None,
    title: DocumentTitle | None = None,
) -> LoadedDocument:
    """Build one valid Markdown result for mutation-based domain tests."""

    return LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=_source() if source is None else source,
        document_format="markdown",
        loader_id="markdown",
        loader_version="1.0.0",
        blocks=(
            (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
            if blocks is None
            else blocks
        ),
        limits=_limits() if limits is None else limits,
        title=title,
    )


def test_document_values_are_frozen_path_private_and_versioned() -> None:
    """Keep every published record immutable and free of path capabilities."""

    values = (
        _source(),
        DocumentLoadLimits(),
        DocumentTitle(text="Lesson", source="embedded"),
        DocumentTable(rows=(("A", "B"),)),
        DocumentBlock(ordinal=0, kind="paragraph", text="Body"),
        _loaded_document(),
    )

    assert values[-1].schema_version == DOCUMENT_SCHEMA_VERSION
    for value in values:
        assert all("path" not in field.name.casefold() for field in fields(value))
        with pytest.raises((AttributeError, TypeError)):
            value.unexpected = "mutation"  # type: ignore[union-attr]


@pytest.mark.parametrize("schema_version", [2, True, 1.0, "1"])
def test_loaded_document_requires_exact_schema_version(
    schema_version: object,
) -> None:
    """Reject equal-looking booleans, floats, strings, and future schemas."""

    with pytest.raises(DocumentValidationError, match="schema version"):
        replace(
            _loaded_document(),
            schema_version=schema_version,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "field_name",
    [
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
    ],
)
@pytest.mark.parametrize("invalid_value", [0, -1, True, 1.0, MAX_JSON_SAFE_INTEGER + 1])
def test_every_document_limit_requires_an_exact_positive_safe_integer(
    field_name: str,
    invalid_value: object,
) -> None:
    """Apply the same non-ambiguous integer boundary to every resource limit."""

    with pytest.raises(DocumentValidationError, match=field_name):
        replace(
            DocumentLoadLimits(),
            **{field_name: invalid_value},  # type: ignore[arg-type]
        )


def test_document_limits_reject_incoherent_nested_budgets() -> None:
    """Keep a single cell or row from exceeding its enclosing total budget."""

    with pytest.raises(DocumentValidationError, match="max_cell_code_points"):
        _limits(max_text_code_points=9, max_cell_code_points=10)
    with pytest.raises(DocumentValidationError, match="max_table_columns"):
        _limits(max_table_cells=2, max_table_columns=3)


@pytest.mark.parametrize("size_bytes", [0, -1, True, 1.0, MAX_JSON_SAFE_INTEGER + 1])
def test_document_source_requires_exact_positive_size(size_bytes: object) -> None:
    """Reject source sizes that cannot describe immutable stored bytes."""

    with pytest.raises(DocumentValidationError, match="size_bytes"):
        _source(size_bytes=size_bytes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes"),
    [
        {"scope": "chat_lesson"},
        {"link_id": r"C:\\private\\lesson.md"},
        {"file_id": f"file_{_DIGEST.upper()}"},
        {"file_name": r"C:\\private\\lesson.md"},
        {"media_type": "markdown"},
    ],
)
def test_document_source_rejects_invalid_or_path_bearing_metadata(
    changes: dict[str, object],
) -> None:
    """Keep ownership selectors and display metadata inside their safe grammar."""

    with pytest.raises(DocumentValidationError):
        replace(_source(), **changes)  # type: ignore[arg-type]


def test_document_table_preserves_ragged_rows_without_padding() -> None:
    """Retain each source row's exact width instead of inventing blank cells."""

    rows = (("name", "score"), ("Elysia",), ("Ying", "100", "extra"))

    table = DocumentTable(rows=rows)

    assert table.rows == rows
    assert tuple(len(row) for row in table.rows) == (2, 1, 3)


@pytest.mark.parametrize(
    "rows",
    [
        (),
        ((),),
        [["not", "a", "tuple"]],
        (("safe", "bad\x00cell"),),
        (("safe", 1),),
    ],
)
def test_document_table_rejects_missing_or_invalid_cells(rows: object) -> None:
    """Require explicit tuple rows while still allowing genuine empty cells."""

    with pytest.raises(DocumentValidationError):
        DocumentTable(rows=rows)  # type: ignore[arg-type]

    assert DocumentTable(rows=(("",),)).rows == (("",),)


def test_document_block_enforces_text_table_union() -> None:
    """Prevent blocks from dropping data or representing two variants at once."""

    table = DocumentTable(rows=(("A",),))
    assert DocumentBlock(ordinal=0, kind="table", table=table).table == table
    assert DocumentBlock(ordinal=0, kind="code", text="x = 1").text == "x = 1"

    invalid_blocks = (
        {"ordinal": 0, "kind": "table"},
        {"ordinal": 0, "kind": "table", "text": "A", "table": table},
        {"ordinal": 0, "kind": "paragraph", "text": "Body", "table": table},
        {"ordinal": 0, "kind": "paragraph"},
        {"ordinal": 0, "kind": "paragraph", "text": " \ufeff\n"},
        {"ordinal": 0, "kind": "unknown", "text": "Body"},
    )
    for values in invalid_blocks:
        with pytest.raises(DocumentValidationError):
            DocumentBlock(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("ordinal", [-1, True, 1.0, MAX_JSON_SAFE_INTEGER + 1])
def test_document_block_requires_exact_non_negative_ordinal(
    ordinal: object,
) -> None:
    """Keep block order deterministic and JSON-safe before aggregation."""

    with pytest.raises(DocumentValidationError, match="ordinal"):
        DocumentBlock(
            ordinal=ordinal,  # type: ignore[arg-type]
            kind="paragraph",
            text="Body",
        )


def test_heading_level_is_required_only_for_headings() -> None:
    """Preserve real outline depth without attaching it to plain paragraphs."""

    assert DocumentBlock(
        ordinal=0,
        kind="heading",
        text="Section",
        heading_level=9,
    ).heading_level == 9
    for level in (None, 0, 10, True, 1.0):
        with pytest.raises(DocumentValidationError, match="heading"):
            DocumentBlock(
                ordinal=0,
                kind="heading",
                text="Section",
                heading_level=level,  # type: ignore[arg-type]
            )
    with pytest.raises(DocumentValidationError, match="Only a heading"):
        DocumentBlock(
            ordinal=0,
            kind="paragraph",
            text="Body",
            heading_level=1,
        )


def test_pdf_requires_real_one_based_page_metadata() -> None:
    """Retain PDF locations while allowing pages with no extractable blocks."""

    source = _source(
        file_name="lesson.pdf",
        media_type="application/pdf",
    )
    document = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=source,
        document_format="pdf",
        loader_id="pdf",
        loader_version="1.0.0",
        blocks=(
            DocumentBlock(
                ordinal=0,
                kind="paragraph",
                text="Page two",
                page_number=2,
            ),
        ),
        limits=_limits(),
        page_count=3,
    )

    assert document.page_count == 3
    assert document.blocks[0].page_number == 2
    assert replace(document, blocks=(), page_count=1).blocks == ()

    with pytest.raises(DocumentValidationError, match="requires page_count"):
        replace(document, page_count=None)
    with pytest.raises(DocumentValidationError, match="source page"):
        replace(
            document,
            blocks=(DocumentBlock(ordinal=0, kind="paragraph", text="Body"),),
        )
    with pytest.raises(DocumentValidationError, match="exceeds page_count"):
        replace(
            document,
            blocks=(
                DocumentBlock(
                    ordinal=0,
                    kind="paragraph",
                    text="Page four",
                    page_number=4,
                ),
            ),
        )
    for invalid_page_count in (0, True, 1.0):
        with pytest.raises(DocumentValidationError, match="page_count"):
            replace(
                document,
                page_count=invalid_page_count,  # type: ignore[arg-type]
            )


def test_non_pdf_document_rejects_invented_page_metadata() -> None:
    """Avoid claiming layout pages for formats whose loaders cannot know them."""

    document = _loaded_document()
    with pytest.raises(DocumentValidationError, match="Only PDF"):
        replace(document, page_count=1)
    with pytest.raises(DocumentValidationError, match="Only PDF"):
        replace(
            document,
            blocks=(
                DocumentBlock(
                    ordinal=0,
                    kind="paragraph",
                    text="Body",
                    page_number=1,
                ),
            ),
        )


def test_title_requires_safe_bounded_text_and_closed_provenance() -> None:
    """Reject ambiguous or unsafe title metadata without modifying valid text."""

    title = DocumentTitle(text="  Elysia lesson  ", source="embedded")
    assert title.text == "  Elysia lesson  "

    for invalid_text in ("", " \ufeff\n", "bad\x00title"):
        with pytest.raises(DocumentValidationError):
            DocumentTitle(text=invalid_text, source="embedded")
    with pytest.raises(DocumentValidationError, match="code-point"):
        DocumentTitle(
            text="x" * (MAX_DOCUMENT_TITLE_CODE_POINTS + 1),
            source="embedded",
        )
    with pytest.raises(DocumentValidationError, match="embedded or heading"):
        DocumentTitle(
            text="Lesson",
            source="filename",  # type: ignore[arg-type]
        )


def test_heading_derived_title_must_match_a_structural_block() -> None:
    """Prove heading provenance instead of silently promoting a filename."""

    title = DocumentTitle(text="Lesson", source="heading")
    document = _loaded_document(
        title=title,
        blocks=(
            DocumentBlock(
                ordinal=0,
                kind="heading",
                text="Lesson",
                heading_level=1,
            ),
        ),
    )
    assert document.title == title

    with pytest.raises(DocumentValidationError, match="must match"):
        replace(
            document,
            blocks=(DocumentBlock(ordinal=0, kind="paragraph", text="Lesson"),),
        )
    assert replace(
        document,
        title=DocumentTitle(text="Metadata", source="embedded"),
        blocks=(DocumentBlock(ordinal=0, kind="paragraph", text="Body"),),
    ).title == DocumentTitle(text="Metadata", source="embedded")


def test_loaded_document_requires_contiguous_zero_based_ordinals() -> None:
    """Keep parser order stable without creating premature Chunk identities."""

    document = _loaded_document(
        blocks=(
            DocumentBlock(ordinal=0, kind="paragraph", text="One"),
            DocumentBlock(ordinal=1, kind="paragraph", text="Two"),
        )
    )
    assert [block.ordinal for block in document.blocks] == [0, 1]

    for blocks in (
        (DocumentBlock(ordinal=1, kind="paragraph", text="One"),),
        (
            DocumentBlock(ordinal=0, kind="paragraph", text="One"),
            DocumentBlock(ordinal=0, kind="paragraph", text="Two"),
        ),
    ):
        with pytest.raises(DocumentValidationError, match="contiguous"):
            replace(document, blocks=blocks)


def test_loaded_document_enforces_source_block_and_page_limits() -> None:
    """Reject each top-level resource before it can reach later chunking."""

    with pytest.raises(DocumentTooLargeError, match="byte limit"):
        _loaded_document(
            source=_source(size_bytes=5),
            limits=_limits(max_source_bytes=4),
        )
    with pytest.raises(DocumentContentLimitError, match="block count"):
        _loaded_document(
            blocks=(
                DocumentBlock(ordinal=0, kind="paragraph", text="One"),
                DocumentBlock(ordinal=1, kind="paragraph", text="Two"),
            ),
            limits=_limits(max_blocks=1),
        )

    pdf = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=_source(
            file_name="lesson.pdf",
            media_type="application/pdf",
        ),
        document_format="pdf",
        loader_id="pdf",
        loader_version="1.0.0",
        blocks=(),
        limits=_limits(max_pages=1),
        page_count=1,
    )
    with pytest.raises(DocumentContentLimitError, match="page count"):
        replace(pdf, page_count=2)


def test_loaded_document_enforces_all_table_budgets() -> None:
    """Count ragged widths, aggregate cells, and cell text independently."""

    table_block = DocumentBlock(
        ordinal=0,
        kind="table",
        table=DocumentTable(rows=(("aa", "b"), ("c",))),
    )
    assert _loaded_document(blocks=(table_block,)).blocks[0] == table_block

    with pytest.raises(DocumentContentLimitError, match="column limit"):
        _loaded_document(
            blocks=(table_block,),
            limits=_limits(max_table_columns=1),
        )
    with pytest.raises(DocumentContentLimitError, match="cell limit"):
        _loaded_document(
            blocks=(table_block,),
            limits=_limits(max_table_cells=2, max_table_columns=2),
        )
    with pytest.raises(DocumentContentLimitError, match="table cell"):
        _loaded_document(
            blocks=(table_block,),
            limits=_limits(max_cell_code_points=1),
        )


def test_total_text_budget_counts_title_blocks_and_table_cells() -> None:
    """Prevent any structured string field from bypassing the aggregate cap."""

    title = DocumentTitle(text="Hi", source="embedded")
    blocks = (
        DocumentBlock(ordinal=0, kind="paragraph", text="abc"),
        DocumentBlock(
            ordinal=1,
            kind="table",
            table=DocumentTable(rows=(("de",),)),
        ),
    )
    assert _loaded_document(
        title=title,
        blocks=blocks,
        limits=_limits(max_text_code_points=7, max_cell_code_points=2),
    ).blocks == blocks

    with pytest.raises(DocumentContentLimitError, match="Document text"):
        _loaded_document(
            title=title,
            blocks=blocks,
            limits=_limits(max_text_code_points=6, max_cell_code_points=2),
        )


def test_loaded_document_rejects_invalid_format_loader_and_block_container() -> None:
    """Keep adapter identity and output containers deterministic and bounded."""

    document = _loaded_document()
    for changes in (
        {"document_format": "html"},
        {"loader_id": "Markdown Loader"},
        {"loader_version": ""},
        {"blocks": [DocumentBlock(ordinal=0, kind="paragraph", text="Body")]},
        {"limits": None},
    ):
        with pytest.raises(DocumentValidationError):
            replace(document, **changes)  # type: ignore[arg-type]
