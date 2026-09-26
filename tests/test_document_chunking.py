"""Verify deterministic bounded chunking and exact source provenance."""

from __future__ import annotations

import builtins
from dataclasses import fields, replace

import pytest

from attachments import AttachmentScope
from documents.cleaning import (
    CLEANED_DOCUMENT_SCHEMA_VERSION,
    CleanedDocument,
    CleanedTextBlock,
    ConservativeDocumentCleaner,
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
)
from documents.chunking import (
    CHUNKED_DOCUMENT_SCHEMA_VERSION,
    STRUCTURE_AWARE_CHUNKER_ID,
    STRUCTURE_AWARE_CHUNKER_VERSION,
    ChunkSourceMapping,
    ChunkedDocument,
    DocumentChunk,
    DocumentChunkingPolicy,
    StructureAwareDocumentChunker,
)
from documents.domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTable,
    DocumentTitle,
    LoadedDocument,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentValidationError,
)


_FILE_ID = f"file_{'a' * 64}"


class _AlternateVersionChunker(StructureAwareDocumentChunker):
    """Expose a different producer version for invalidation tests."""

    @property
    def chunker_version(self) -> str:
        """Return a distinct otherwise-valid chunker version."""

        return "2.0.0"


def _source(
    *,
    scope_id: str = "chat_chunking",
    link_id: str = "attachment_chunking",
    file_name: str = "notes.md",
    media_type: str = "text/markdown",
) -> DocumentSource:
    """Build one path-private source identity for chunking tests."""

    return DocumentSource(
        scope=AttachmentScope(kind="chat", id=scope_id),
        link_id=link_id,
        file_id=_FILE_ID,
        file_name=file_name,
        media_type=media_type,
        size_bytes=1,
    )


def _loaded(
    blocks: tuple[DocumentBlock, ...],
    *,
    source: DocumentSource | None = None,
    document_format: str = "markdown",
    title: DocumentTitle | None = None,
    page_count: int | None = None,
    limits: DocumentLoadLimits | None = None,
) -> LoadedDocument:
    """Build a validated loaded document with exact caller-supplied blocks."""

    return LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=_source() if source is None else source,
        document_format=document_format,  # type: ignore[arg-type]
        loader_id="chunk-test-loader",
        loader_version="1.0.0",
        blocks=blocks,
        limits=DocumentLoadLimits() if limits is None else limits,
        title=title,
        page_count=page_count,
    )


def _clean(
    blocks: tuple[DocumentBlock, ...],
    *,
    processing_limits: DocumentProcessingLimits | None = None,
    source: DocumentSource | None = None,
    document_format: str = "markdown",
    title: DocumentTitle | None = None,
    page_count: int | None = None,
) -> CleanedDocument:
    """Load the fixture graph through the real conservative cleaner."""

    cleaner = ConservativeDocumentCleaner(limits=processing_limits)
    return cleaner.clean(
        _loaded(
            blocks,
            source=source,
            document_format=document_format,
            title=title,
            page_count=page_count,
        )
    )


def _chunk(
    document: CleanedDocument,
    cap: int = 2_000,
) -> ChunkedDocument:
    """Chunk one cleaned fixture with an explicit code-point cap."""

    return StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=cap),
    ).chunk(document)


def test_chunk_values_are_frozen_path_private_and_versioned() -> None:
    """Keep published chunk values immutable, versioned, and path-private."""

    cleaned = _clean((DocumentBlock(0, "paragraph", "Body"),))
    result = _chunk(cleaned)
    values = (
        DocumentChunkingPolicy(),
        result.chunks[0].source_mappings[0],
        result.chunks[0],
        result,
    )

    assert result.schema_version == CHUNKED_DOCUMENT_SCHEMA_VERSION
    assert result.cleaned_schema_version == CLEANED_DOCUMENT_SCHEMA_VERSION
    assert result.chunker_id == STRUCTURE_AWARE_CHUNKER_ID
    assert result.chunker_version == STRUCTURE_AWARE_CHUNKER_VERSION
    for value in values:
        assert all("path" not in field.name.casefold() for field in fields(value))
        with pytest.raises((AttributeError, TypeError)):
            value.unexpected = "mutation"  # type: ignore[union-attr]


@pytest.mark.parametrize("invalid", [0, -1, True, 1.0])
def test_policy_requires_an_exact_positive_code_point_cap(
    invalid: object,
) -> None:
    """Reject ambiguous or non-positive policy lengths before processing."""

    with pytest.raises(DocumentValidationError, match="max_chunk_code_points"):
        DocumentChunkingPolicy(
            max_chunk_code_points=invalid,  # type: ignore[arg-type]
        )


def test_v1_rejects_overlap_and_noncanonical_projection_policy() -> None:
    """Keep overlap, prose separators, and the table view fixed in v1."""

    with pytest.raises(DocumentValidationError, match="overlap"):
        DocumentChunkingPolicy(overlap_code_points=1)  # type: ignore[arg-type]
    with pytest.raises(DocumentValidationError, match="separator"):
        DocumentChunkingPolicy(prose_separator="\n")  # type: ignore[arg-type]
    with pytest.raises(DocumentValidationError, match="projection"):
        DocumentChunkingPolicy(
            table_projection_version="csv-v1",  # type: ignore[arg-type]
        )


def test_adjacent_prose_packs_with_fixed_separator_and_exact_mappings() -> None:
    """Pack adjacent prose while leaving generated separators unmapped."""

    cleaned = _clean((
        DocumentBlock(0, "paragraph", "Alpha"),
        DocumentBlock(1, "paragraph", "Beta"),
    ))

    result = _chunk(cleaned, cap=20)

    assert [chunk.text for chunk in result.chunks] == ["Alpha\n\nBeta"]
    mappings = result.chunks[0].source_mappings
    assert [(item.chunk_start_code_point, item.chunk_end_code_point) for item in mappings] == [
        (0, 5),
        (7, 11),
    ]
    assert [item.source_span.block_ordinal for item in mappings] == [0, 1]


def test_long_prose_uses_fixed_boundary_priority_without_added_overlap() -> None:
    """Prefer sentence boundaries and preserve one exact source partition."""

    text = "First. Next! Tail😀e\u0301"
    cleaned = _clean((DocumentBlock(0, "paragraph", text),))

    result = _chunk(cleaned, cap=10)

    assert [chunk.text for chunk in result.chunks] == [
        "First.",
        " Next!",
        " Tail😀e\u0301",
    ]
    assert "".join(chunk.text for chunk in result.chunks) == text
    spans = [chunk.source_mappings[0].source_span for chunk in result.chunks]
    assert [
        (span.start_code_point, span.end_code_point)
        for span in spans
        if isinstance(span, DocumentTextSpan)
    ] == [(0, 6), (6, 12), (12, len(text))]
    assert all(len(chunk.text) <= 10 for chunk in result.chunks)


def test_unicode_offsets_count_code_points_not_utf8_or_utf16_units() -> None:
    """Count astral and combining characters with Python code-point offsets."""

    text = "A😀e\u0301中"
    result = _chunk(
        _clean((DocumentBlock(0, "paragraph", text),)),
        cap=2,
    )

    assert [chunk.text for chunk in result.chunks] == ["A😀", "e\u0301", "中"]
    assert [
        (
            cast_span.start_code_point,
            cast_span.end_code_point,
        )
        for chunk in result.chunks
        for mapping in chunk.source_mappings
        for cast_span in [mapping.source_span]
        if isinstance(cast_span, DocumentTextSpan)
    ] == [(0, 2), (2, 4), (4, 5)]


def test_chinese_sentence_endings_are_preferred_before_hard_splits() -> None:
    """Recognize the fixed Chinese terminator set without language tooling."""

    text = "甲乙。丙丁戊"

    result = _chunk(
        _clean((DocumentBlock(0, "paragraph", text),)),
        cap=4,
    )

    assert [chunk.text for chunk in result.chunks] == ["甲乙。", "丙丁戊"]


def test_nonzero_and_discontiguous_retained_spans_map_exactly() -> None:
    """Translate cleaned offsets through nonzero source piece-table ranges."""

    base = _clean((DocumentBlock(0, "paragraph", "0123456789"),))
    piece_block = CleanedTextBlock(
        ordinal=0,
        source_block_ordinal=0,
        kind="paragraph",
        text="234567",
        retained_spans=(
            DocumentTextSpan(0, 2, 5),
            DocumentTextSpan(0, 5, 8),
        ),
    )
    cleaned = replace(base, blocks=(piece_block,))

    result = _chunk(cleaned, cap=4)

    assert cleaned.cleaning_fingerprint != base.cleaning_fingerprint
    assert result.derivation_fingerprint != (
        _chunk(base, cap=4).derivation_fingerprint
    )
    assert [chunk.text for chunk in result.chunks] == ["2345", "67"]
    assert [
        (span.start_code_point, span.end_code_point)
        for chunk in result.chunks
        for mapping in chunk.source_mappings
        for span in [mapping.source_span]
        if isinstance(span, DocumentTextSpan)
    ] == [(2, 5), (5, 6), (6, 8)]


def test_title_heading_code_and_table_are_structural_boundaries() -> None:
    """Start headings and titles anew while isolating code and table blocks."""

    blocks = (
        DocumentBlock(0, "heading", "Heading", heading_level=1),
        DocumentBlock(1, "paragraph", "Body"),
        DocumentBlock(2, "code", "x = 1\ny = 2\n"),
        DocumentBlock(
            3,
            "table",
            table=DocumentTable((("A", "B"),)),
        ),
        DocumentBlock(4, "title", "Second"),
        DocumentBlock(5, "paragraph", "Tail"),
    )

    result = _chunk(_clean(blocks), cap=40)

    assert [(chunk.kind, chunk.text) for chunk in result.chunks] == [
        ("prose", "Heading\n\nBody"),
        ("code", "x = 1\ny = 2\n"),
        ("table", '["A","B"]'),
        ("prose", "Second\n\nTail"),
    ]


def test_code_prefers_line_boundaries_and_hard_splits_long_lines() -> None:
    """Preserve code spelling while selecting only line or hard boundaries."""

    text = "aa\nbb\n123456"
    result = _chunk(
        _clean((DocumentBlock(0, "code", text),), document_format="code"),
        cap=4,
    )

    assert [chunk.text for chunk in result.chunks] == [
        "aa\n",
        "bb\n",
        "1234",
        "56",
    ]
    assert "".join(chunk.text for chunk in result.chunks) == text
    assert all(chunk.kind == "code" for chunk in result.chunks)


@pytest.mark.parametrize("kind", ["paragraph", "code"])
def test_chunk_boundaries_do_not_split_crlf_when_the_cap_can_hold_it(
    kind: str,
) -> None:
    """Treat CRLF as one logical break unless a one-point cap forces a split."""

    document_format = "code" if kind == "code" else "text"
    result = _chunk(
        _clean(
            (DocumentBlock(0, kind, "A\r\nB"),),  # type: ignore[arg-type]
            document_format=document_format,
        ),
        cap=2,
    )

    assert [chunk.text for chunk in result.chunks] == ["A", "\r\n", "B"]


def test_pdf_chunks_never_cross_pages_and_keep_nonzero_cleaned_offsets() -> None:
    """Keep real pages separate after repeated headers are conservatively removed."""

    blocks = tuple(
        DocumentBlock(
            ordinal=index,
            kind="paragraph",
            text=f"Header\nPage {index + 1} body",
            page_number=index + 1,
        )
        for index in range(3)
    )
    cleaned = _clean(
        blocks,
        source=_source(file_name="notes.pdf", media_type="application/pdf"),
        document_format="pdf",
        page_count=3,
    )

    result = _chunk(cleaned, cap=100)

    assert [chunk.page_number for chunk in result.chunks] == [1, 2, 3]
    assert [chunk.text for chunk in result.chunks] == [
        "Page 1 body",
        "Page 2 body",
        "Page 3 body",
    ]
    assert [
        mapping.source_span.start_code_point
        for chunk in result.chunks
        for mapping in chunk.source_mappings
    ] == [7, 7, 7]


def test_table_projection_is_canonical_ragged_and_source_mapped() -> None:
    """Project ragged and empty cells with deterministic JSONL escaping."""

    table = DocumentTable((
        ("", 'a"b', "x\ny", "中"),
        ("only",),
    ))
    cleaned = _clean((DocumentBlock(0, "table", table=table),), document_format="csv")

    result = _chunk(cleaned, cap=2_000)

    assert [chunk.text for chunk in result.chunks] == [
        '["","a\\"b","x\\ny","中"]\n["only"]'
    ]
    spans = [
        mapping.source_span
        for mapping in result.chunks[0].source_mappings
    ]
    assert all(isinstance(span, DocumentTableCellSpan) for span in spans)
    assert [
        (
            span.row_index,
            span.column_index,
            span.start_code_point,
            span.end_code_point,
        )
        for span in spans
        if isinstance(span, DocumentTableCellSpan)
    ] == [
        (0, 0, 0, 0),
        (0, 1, 0, 3),
        (0, 2, 0, 3),
        (0, 3, 0, 1),
        (1, 0, 0, 4),
    ]


def test_table_projection_respects_tiny_caps_and_reconstructs_exactly() -> None:
    """Bound every table chunk even across quotes, escapes, and empty cells."""

    table = DocumentTable((("", 'a"b', "x\ny"), ("tail",)))
    result = _chunk(
        _clean((DocumentBlock(0, "table", table=table),), document_format="csv"),
        cap=3,
    )

    assert all(0 < len(chunk.text) <= 3 for chunk in result.chunks)
    assert "".join(chunk.text for chunk in result.chunks) == (
        '["","a\\"b","x\\ny"]\n["tail"]'
    )
    assert all(
        isinstance(mapping.source_span, DocumentTableCellSpan)
        for chunk in result.chunks
        for mapping in chunk.source_mappings
    )


def test_large_table_cell_ranges_are_not_reused_after_capacity_splits() -> None:
    """Keep each unescaped payload slice tied to its exact source range."""

    table = DocumentTable((("abcde",),))
    result = _chunk(
        _clean(
            (DocumentBlock(0, "table", table=table),),
            document_format="csv",
        ),
        cap=4,
    )

    payload_spans = [
        mapping.source_span
        for chunk in result.chunks
        for mapping in chunk.source_mappings
        if isinstance(mapping.source_span, DocumentTableCellSpan)
        and mapping.source_span.start_code_point
        < mapping.source_span.end_code_point
    ]
    assert [
        (span.start_code_point, span.end_code_point)
        for span in payload_spans
    ] == [(0, 4), (4, 5)]
    assert "".join(chunk.text for chunk in result.chunks) == '["abcde"]'


def test_chunking_is_repeatable_pure_and_scope_link_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeat exact IDs without I/O and separate authorization identities."""

    document = _clean((DocumentBlock(0, "paragraph", "Stable body"),))

    def reject_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Chunking must not open a filesystem path.")

    monkeypatch.setattr(builtins, "open", reject_open)
    first = _chunk(document, cap=8)
    second = _chunk(document, cap=8)
    other = _chunk(
        _clean(
            (DocumentBlock(0, "paragraph", "Stable body"),),
            source=_source(
                scope_id="chat_other",
                link_id="attachment_other",
            ),
        ),
        cap=8,
    )

    assert first == second
    assert first.derivation_fingerprint != other.derivation_fingerprint
    assert [chunk.chunk_id for chunk in first.chunks] != [
        chunk.chunk_id for chunk in other.chunks
    ]


def test_policy_change_changes_derivation_and_chunk_identity() -> None:
    """Invalidate chunk lineage whenever the resolved boundary cap changes."""

    cleaned = _clean((DocumentBlock(0, "paragraph", "alpha beta gamma"),))

    wide = _chunk(cleaned, cap=20)
    narrow = _chunk(cleaned, cap=8)

    assert wide.derivation_fingerprint != narrow.derivation_fingerprint
    assert wide.chunks[0].chunk_id != narrow.chunks[0].chunk_id


def test_chunker_version_changes_derivation_and_chunk_identity() -> None:
    """Invalidate every downstream identity when chunker behavior is versioned."""

    cleaned = _clean((DocumentBlock(0, "paragraph", "stable body"),))

    default = StructureAwareDocumentChunker().chunk(cleaned)
    changed = _AlternateVersionChunker().chunk(cleaned)

    assert default.derivation_fingerprint != changed.derivation_fingerprint
    assert default.chunks[0].chunk_id != changed.chunks[0].chunk_id


def test_chunk_count_and_total_output_budgets_fail_incrementally() -> None:
    """Reject the first object or code point beyond configured ceilings."""

    count_limits = DocumentProcessingLimits(
        max_chunks=1,
        max_total_chunk_code_points=100,
    )
    count_cleaned = _clean(
        (DocumentBlock(0, "paragraph", "abcdefgh"),),
        processing_limits=count_limits,
    )
    with pytest.raises(DocumentContentLimitError, match="chunk count"):
        _chunk(count_cleaned, cap=4)

    output_limits = DocumentProcessingLimits(
        max_chunks=10,
        max_total_chunk_code_points=5,
    )
    output_cleaned = _clean(
        (DocumentBlock(0, "paragraph", "abcdef"),),
        processing_limits=output_limits,
    )
    with pytest.raises(DocumentContentLimitError, match="total code-point"):
        _chunk(output_cleaned, cap=5)


def test_table_mapping_budget_counts_projection_pieces_before_publication() -> None:
    """Stop a split table before it can publish an oversized mapping graph."""

    limits = DocumentProcessingLimits(
        max_cleaned_blocks=1,
        max_chunks=100,
        max_total_chunk_code_points=100,
        max_source_mappings_per_chunk=1,
        max_total_source_mappings=1,
    )
    cleaned = _clean(
        (DocumentBlock(0, "table", table=DocumentTable((("abcdef",),))),),
        processing_limits=limits,
        document_format="csv",
    )

    with pytest.raises(DocumentContentLimitError, match="source-mapping"):
        _chunk(cleaned, cap=3)


def test_piece_table_is_split_to_respect_the_per_chunk_mapping_limit() -> None:
    """Cut at retained-piece boundaries instead of rejecting mappable text."""

    limits = DocumentProcessingLimits(
        max_cleaned_blocks=1,
        max_chunks=10,
        max_total_chunk_code_points=10,
        max_source_mappings_per_chunk=1,
        max_total_source_mappings=3,
    )
    base = _clean(
        (DocumentBlock(0, "paragraph", "abc"),),
        processing_limits=limits,
    )
    block = CleanedTextBlock(
        ordinal=0,
        source_block_ordinal=0,
        kind="paragraph",
        text="abc",
        retained_spans=(
            DocumentTextSpan(0, 0, 1),
            DocumentTextSpan(0, 1, 2),
            DocumentTextSpan(0, 2, 3),
        ),
    )

    result = _chunk(replace(base, blocks=(block,)), cap=10)

    assert [chunk.text for chunk in result.chunks] == ["a", "b", "c"]
    assert all(len(chunk.source_mappings) == 1 for chunk in result.chunks)


def test_domain_rejects_forged_chunk_ids_and_out_of_bounds_mappings() -> None:
    """Fail closed on forged identities and invalid chunk-local provenance."""

    span = DocumentTextSpan(0, 0, 1)
    mapping = ChunkSourceMapping(0, 1, span)
    with pytest.raises(DocumentValidationError, match="chunk_id"):
        DocumentChunk(0, "chunk_bad", "prose", "x", None, (mapping,))
    with pytest.raises(DocumentValidationError, match="in bounds"):
        DocumentChunk(
            0,
            f"chunk_{'0' * 64}",
            "prose",
            "x",
            None,
            (
                ChunkSourceMapping(
                    0,
                    2,
                    DocumentTextSpan(0, 0, 2),
                ),
            ),
        )
    with pytest.raises(DocumentValidationError, match="exact code-point length"):
        ChunkSourceMapping(0, 1, DocumentTextSpan(0, 0, 2))
    with pytest.raises(DocumentValidationError, match="canonical prose separator"):
        DocumentChunk(
            0,
            f"chunk_{'0' * 64}",
            "prose",
            "a--b",
            None,
            (
                ChunkSourceMapping(0, 1, DocumentTextSpan(0, 0, 1)),
                ChunkSourceMapping(3, 4, DocumentTextSpan(1, 0, 1)),
            ),
        )
    with pytest.raises(DocumentValidationError, match="complete chunk"):
        DocumentChunk(
            0,
            f"chunk_{'0' * 64}",
            "code",
            "ab",
            None,
            (ChunkSourceMapping(0, 1, DocumentTextSpan(0, 0, 1)),),
        )


def test_golden_derivation_and_chunk_ids_freeze_canonical_encoding() -> None:
    """Detect accidental drift in canonical lineage and ID preimages."""

    cleaned = _clean((
        DocumentBlock(0, "heading", "Topic", heading_level=1),
        DocumentBlock(1, "paragraph", "Alpha😀 beta"),
    ))

    result = _chunk(cleaned, cap=9)

    assert result.derivation_fingerprint == (
        "57aca3f235f415ce37806593247aab42a062c1d99cdca15ad66da7d156811244"
    )
    assert [chunk.chunk_id for chunk in result.chunks] == [
        "chunk_6fcbeb9c8aedc80d6069f567490b080c6261e79639f236c97951a37c770fa704",
        "chunk_c1fdc08727b0b76433f5589f15a0773e98c2b9e24c7d9dd035e7723666ed8f9d",
        "chunk_c97f4e2c2cea78cc21ff51afc841c1c291e06e15564b91884f26a4424e90f19a",
    ]
