"""Verify conservative document cleaning and exact source provenance."""

from __future__ import annotations

from dataclasses import replace

import pytest

from attachments.domain import AttachmentScope
from documents.cleaning import (
    CLEANED_DOCUMENT_SCHEMA_VERSION,
    CleanedDocument,
    CleanedTableBlock,
    CleanedTextBlock,
    ConservativeDocumentCleaner,
    DocumentCleaningPolicy,
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
    LoadedDocumentProvenance,
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
    DocumentProcessingFailedError,
    DocumentValidationError,
)


def _source(
    *,
    scope_id: str = "chat_clean",
    link_id: str = "attachment_clean",
) -> DocumentSource:
    """Build deterministic path-free metadata for cleaning fixtures."""

    return DocumentSource(
        scope=AttachmentScope(kind="chat", id=scope_id),
        link_id=link_id,
        file_id="file_" + "a" * 64,
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=8,
    )


def _document(
    blocks: tuple[DocumentBlock, ...],
    *,
    document_format: str = "text",
    source: DocumentSource | None = None,
    title: DocumentTitle | None = None,
    page_count: int | None = None,
    loader_version: str = "1.0.0",
) -> LoadedDocument:
    """Build one validated loader result with generous raw limits."""

    return LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=_source() if source is None else source,
        document_format=document_format,  # type: ignore[arg-type]
        loader_id="fixture",
        loader_version=loader_version,
        blocks=blocks,
        limits=DocumentLoadLimits(),
        title=title,
        page_count=page_count,
    )


class _AlternateVersionCleaner(ConservativeDocumentCleaner):
    """Expose a different producer version for invalidation tests."""

    @property
    def cleaner_version(self) -> str:
        """Return a distinct otherwise-valid cleaner version."""

        return "2.0.0"


def test_cleaner_preserves_valid_unicode_whitespace_code_and_ragged_tables() -> None:
    """Cleaning never guesses at meaningful whitespace or legal Unicode."""

    prose = " A\r\nB\u2028e\u0301👩\u200d💻\ufffd\u2066x\u2069 "
    code = "def f():\r\n\treturn 1  \r\n"
    table = DocumentTable(rows=(("", "  ", "x\r\ny"), ("=1+1",)))
    loaded = _document(
        (
            DocumentBlock(ordinal=0, kind="paragraph", text=prose),
            DocumentBlock(ordinal=1, kind="code", text=code),
            DocumentBlock(ordinal=2, kind="table", table=table),
        )
    )

    cleaned = ConservativeDocumentCleaner().clean(loaded)

    assert cleaned.schema_version == CLEANED_DOCUMENT_SCHEMA_VERSION
    assert isinstance(cleaned.blocks[0], CleanedTextBlock)
    assert cleaned.blocks[0].text == prose
    assert isinstance(cleaned.blocks[1], CleanedTextBlock)
    assert cleaned.blocks[1].text == code
    assert isinstance(cleaned.blocks[2], CleanedTableBlock)
    assert cleaned.blocks[2].table == table
    assert cleaned.omissions == ()


def test_cleaner_removes_only_an_exact_header_from_every_pdf_page() -> None:
    """A three-page exact first line is omitted with auditable source ranges."""

    loaded = _document(
        tuple(
            DocumentBlock(
                ordinal=index,
                kind="paragraph",
                text=f"Running header\r\nBody {index + 1}",
                page_number=index + 1,
            )
            for index in range(3)
        ),
        document_format="pdf",
        page_count=3,
    )

    cleaned = ConservativeDocumentCleaner().clean(loaded)

    assert tuple(
        block.text
        for block in cleaned.blocks
        if isinstance(block, CleanedTextBlock)
    ) == (
        "Body 1",
        "Body 2",
        "Body 3",
    )
    assert tuple(item.source_span for item in cleaned.omissions) == tuple(
        DocumentTextSpan(
            block_ordinal=index,
            start_code_point=0,
            end_code_point=len("Running header\r\n"),
            page_number=index + 1,
        )
        for index in range(3)
    )
    for index, block in enumerate(cleaned.blocks):
        assert isinstance(block, CleanedTextBlock)
        assert block.retained_spans == (
            DocumentTextSpan(
                block_ordinal=index,
                start_code_point=len("Running header\r\n"),
                end_code_point=len(f"Running header\r\nBody {index + 1}"),
                page_number=index + 1,
            ),
        )


@pytest.mark.parametrize(
    "texts,title",
    (
        (("Header\nOne", "Header\nTwo"), None),
        (("Header\nOne", "Other\nTwo", "Header\nThree"), None),
        (("Header", "Header\nTwo", "Header\nThree"), None),
        (("Header\n ", "Header\nTwo", "Header\nThree"), None),
        (
            ("Header\nOne", "Header\nTwo", "Header\nThree"),
            DocumentTitle(text="Header", source="embedded"),
        ),
    ),
)
def test_cleaner_keeps_ambiguous_or_semantic_pdf_first_lines(
    texts: tuple[str, ...],
    title: DocumentTitle | None,
) -> None:
    """Short, inconsistent, title, or header-only candidates remain intact."""

    loaded = _document(
        tuple(
            DocumentBlock(
                ordinal=index,
                kind="paragraph",
                text=text,
                page_number=index + 1,
            )
            for index, text in enumerate(texts)
        ),
        document_format="pdf",
        title=title,
        page_count=len(texts),
    )

    cleaned = ConservativeDocumentCleaner().clean(loaded)

    assert tuple(
        block.text
        for block in cleaned.blocks
        if isinstance(block, CleanedTextBlock)
    ) == texts
    assert cleaned.omissions == ()


def test_cleaner_keeps_pdf_headers_with_empty_or_multi_block_pages() -> None:
    """Missing or ambiguous page layouts disable the first-line heuristic."""

    empty_page = _document(
        (
            DocumentBlock(
                ordinal=0,
                kind="paragraph",
                text="Header\nOne",
                page_number=1,
            ),
            DocumentBlock(
                ordinal=1,
                kind="paragraph",
                text="Header\nThree",
                page_number=3,
            ),
        ),
        document_format="pdf",
        page_count=3,
    )
    multi_block = _document(
        (
            DocumentBlock(ordinal=0, kind="paragraph", text="Header\nOne", page_number=1),
            DocumentBlock(ordinal=1, kind="paragraph", text="Extra", page_number=1),
            DocumentBlock(ordinal=2, kind="paragraph", text="Header\nTwo", page_number=2),
            DocumentBlock(ordinal=3, kind="paragraph", text="Header\nThree", page_number=3),
        ),
        document_format="pdf",
        page_count=3,
    )

    assert ConservativeDocumentCleaner().clean(empty_page).omissions == ()
    assert ConservativeDocumentCleaner().clean(multi_block).omissions == ()


def test_cleaning_identity_is_repeatable_and_owner_sensitive() -> None:
    """Exact inputs repeat while loader or ownership changes invalidate output."""

    blocks = (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    cleaner = ConservativeDocumentCleaner()
    first = cleaner.clean(_document(blocks))
    second = cleaner.clean(_document(blocks))
    other_owner = cleaner.clean(
        _document(
            blocks,
            source=_source(
                scope_id="chat_other",
                link_id="attachment_other",
            ),
        )
    )
    other_loader = cleaner.clean(_document(blocks, loader_version="2.0.0"))

    assert first == second
    assert first.document_fingerprint == second.document_fingerprint
    assert first.cleaning_fingerprint == second.cleaning_fingerprint
    assert first.cleaning_fingerprint != other_owner.cleaning_fingerprint
    assert first.cleaning_fingerprint != other_loader.cleaning_fingerprint


def test_cleaning_policy_changes_the_derivation_identity() -> None:
    """Every output-affecting policy value participates in the fingerprint."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    default = ConservativeDocumentCleaner().clean(loaded)
    changed = ConservativeDocumentCleaner(
        policy=DocumentCleaningPolicy(
            repeated_pdf_header_min_pages=4,
            repeated_pdf_header_max_code_points=128,
        )
    ).clean(loaded)

    assert default.blocks == changed.blocks
    assert default.cleaning_fingerprint != changed.cleaning_fingerprint


def test_cleaner_version_changes_the_derivation_identity() -> None:
    """Invalidate cleaned output identity when cleaner behavior is versioned."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )

    default = ConservativeDocumentCleaner().clean(loaded)
    changed = _AlternateVersionCleaner().clean(loaded)

    assert default.blocks == changed.blocks
    assert default.cleaning_fingerprint != changed.cleaning_fingerprint


def test_cleaning_enforces_block_text_and_omission_budgets() -> None:
    """Every result allocation is admitted by explicit processing ceilings."""

    two_blocks = _document(
        (
            DocumentBlock(ordinal=0, kind="paragraph", text="One"),
            DocumentBlock(ordinal=1, kind="paragraph", text="Two"),
        )
    )
    with pytest.raises(DocumentContentLimitError, match="block count"):
        ConservativeDocumentCleaner(
            limits=replace(DocumentProcessingLimits(), max_cleaned_blocks=1)
        ).clean(two_blocks)
    with pytest.raises(DocumentContentLimitError, match="Cleaned text"):
        ConservativeDocumentCleaner(
            limits=replace(DocumentProcessingLimits(), max_cleaned_code_points=5)
        ).clean(two_blocks)

    pdf = _document(
        tuple(
            DocumentBlock(
                ordinal=index,
                kind="paragraph",
                text=f"Header\nBody {index}",
                page_number=index + 1,
            )
            for index in range(3)
        ),
        document_format="pdf",
        page_count=3,
    )
    with pytest.raises(DocumentContentLimitError, match="omissions"):
        ConservativeDocumentCleaner(
            limits=replace(DocumentProcessingLimits(), max_cleaning_omissions=2)
        ).clean(pdf)


def test_cleaned_domain_bounds_retained_piece_table_size() -> None:
    """Reject adversarial piece tables before pipeline validation can sort them."""

    limits = DocumentProcessingLimits(
        max_cleaned_blocks=1,
        max_chunks=2,
        max_total_chunk_code_points=10,
        max_source_mappings_per_chunk=1,
        max_total_source_mappings=1,
    )
    base = ConservativeDocumentCleaner(limits=limits).clean(
        _document((DocumentBlock(0, "paragraph", "ab"),))
    )
    block = base.blocks[0]
    assert isinstance(block, CleanedTextBlock)

    with pytest.raises(DocumentContentLimitError, match="provenance"):
        replace(
            base,
            blocks=(
                replace(
                    block,
                    retained_spans=(
                        DocumentTextSpan(0, 0, 1),
                        DocumentTextSpan(0, 1, 2),
                    ),
                ),
            ),
        )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: DocumentProcessingLimits(max_chunks=True),
        lambda: DocumentProcessingLimits(max_cleaned_blocks=0),
        lambda: DocumentCleaningPolicy(repeated_pdf_header_min_pages=2),
        lambda: DocumentTextSpan(
            block_ordinal=0,
            start_code_point=1,
            end_code_point=1,
        ),
        lambda: DocumentTableCellSpan(
            block_ordinal=0,
            row_index=0,
            column_index=0,
            start_code_point=2,
            end_code_point=1,
        ),
    ),
)
def test_cleaning_values_reject_ambiguous_or_invalid_bounds(factory: object) -> None:
    """Domain values reject bool-as-int, zeroes, and reversed ranges."""

    with pytest.raises(DocumentValidationError):
        factory()  # type: ignore[operator]


def test_loaded_provenance_rejects_non_document_input() -> None:
    """The public provenance constructor cannot bless an arbitrary object."""

    with pytest.raises(DocumentValidationError):
        LoadedDocumentProvenance.from_document(object())  # type: ignore[arg-type]


def test_cleaner_translates_resource_and_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep internal failures behind the public typed error boundary."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )

    def fail_with_memory(
        _self: ConservativeDocumentCleaner,
        _document_value: LoadedDocument,
    ) -> CleanedDocument:
        raise MemoryError

    monkeypatch.setattr(
        ConservativeDocumentCleaner,
        "_clean_document",
        fail_with_memory,
    )
    with pytest.raises(DocumentContentLimitError, match="safe resource"):
        ConservativeDocumentCleaner().clean(loaded)

    def fail_unexpected(
        _self: ConservativeDocumentCleaner,
        _document_value: LoadedDocument,
    ) -> CleanedDocument:
        raise RuntimeError("private detail")

    monkeypatch.setattr(
        ConservativeDocumentCleaner,
        "_clean_document",
        fail_unexpected,
    )
    with pytest.raises(DocumentProcessingFailedError, match="typed result"):
        ConservativeDocumentCleaner().clean(loaded)
