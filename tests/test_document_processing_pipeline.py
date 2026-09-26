"""Verify orchestration and trust boundaries for document processing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from attachments.domain import AttachmentScope
from documents.chunking import (
    ChunkSourceMapping,
    ChunkedDocument,
    DocumentChunk,
    DocumentChunkingPolicy,
    StructureAwareDocumentChunker,
    _build_chunk_id,
)
from documents.cleaning import (
    CleanedDocument,
    CleanedTableBlock,
    CleanedTextBlock,
    CleaningOmission,
    ConservativeDocumentCleaner,
    DocumentCleaningPolicy,
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
)
from documents.domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTable,
    LoadedDocument,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentNotFoundError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)
from documents.pipeline import DocumentProcessingService


def _source(
    *,
    scope: AttachmentScope | None = None,
    link_id: str = "attachment_pipeline",
) -> DocumentSource:
    """Build stable path-free source metadata for pipeline tests."""

    return DocumentSource(
        scope=(
            AttachmentScope(kind="chat", id="chat_pipeline")
            if scope is None
            else scope
        ),
        link_id=link_id,
        file_id="file_" + "d" * 64,
        file_name="source.txt",
        media_type="text/plain",
        size_bytes=8,
    )


def _document(
    blocks: tuple[DocumentBlock, ...],
    *,
    source: DocumentSource | None = None,
    document_format: str = "text",
    page_count: int | None = None,
) -> LoadedDocument:
    """Build one validated loaded-document fixture."""

    return LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=_source() if source is None else source,
        document_format=document_format,  # type: ignore[arg-type]
        loader_id="fixture",
        loader_version="1.0.0",
        blocks=blocks,
        limits=DocumentLoadLimits(),
        page_count=page_count,
    )


class _StaticLoader:
    """Return one fixed loaded result or raise one configured failure."""

    def __init__(
        self,
        result: object,
        *,
        error: BaseException | None = None,
        mutate_scope: Callable[[AttachmentScope], None] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.mutate_scope = mutate_scope
        self.calls: list[tuple[AttachmentScope, str]] = []

    def load(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> LoadedDocument:
        """Record the ownership request and return the configured result."""

        self.calls.append((scope, link_id))
        if self.mutate_scope is not None:
            self.mutate_scope(scope)
        if self.error is not None:
            raise self.error
        return self.result  # type: ignore[return-value]


class _StaticCleaner:
    """Expose real metadata while substituting output or a failure."""

    def __init__(
        self,
        *,
        result: object | None = None,
        error: BaseException | None = None,
        cleaner_id: str | None = None,
        mutate_input: Callable[[LoadedDocument], None] | None = None,
    ) -> None:
        self.delegate = ConservativeDocumentCleaner()
        self.result = result
        self.error = error
        self.id_override = cleaner_id
        self.mutate_input = mutate_input
        self.inputs: list[LoadedDocument] = []

    @property
    def cleaner_id(self) -> str:
        """Return the configured or real cleaner identity."""

        return (
            self.delegate.cleaner_id
            if self.id_override is None
            else self.id_override
        )

    @property
    def cleaner_version(self) -> str:
        """Return the built-in cleaner version."""

        return self.delegate.cleaner_version

    @property
    def policy(self) -> DocumentCleaningPolicy:
        """Return the built-in cleaning policy."""

        return self.delegate.policy

    @property
    def limits(self) -> DocumentProcessingLimits:
        """Return the built-in processing limits."""

        return self.delegate.limits

    def clean(self, document: LoadedDocument) -> CleanedDocument:
        """Record the input before returning or raising the configured value."""

        self.inputs.append(document)
        if self.mutate_input is not None:
            self.mutate_input(document)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result  # type: ignore[return-value]
        return self.delegate.clean(document)


class _StaticChunker:
    """Expose real metadata while substituting output or a failure."""

    def __init__(
        self,
        *,
        result: object | None = None,
        error: BaseException | None = None,
        chunker_id: str | None = None,
        policy: DocumentChunkingPolicy | None = None,
        mutate_input: Callable[[CleanedDocument], None] | None = None,
    ) -> None:
        self.delegate = StructureAwareDocumentChunker(policy=policy)
        self.result = result
        self.error = error
        self.id_override = chunker_id
        self.mutate_input = mutate_input
        self.inputs: list[CleanedDocument] = []

    @property
    def chunker_id(self) -> str:
        """Return the configured or real chunker identity."""

        return (
            self.delegate.chunker_id
            if self.id_override is None
            else self.id_override
        )

    @property
    def chunker_version(self) -> str:
        """Return the built-in chunker version."""

        return self.delegate.chunker_version

    @property
    def policy(self) -> DocumentChunkingPolicy:
        """Return the configured chunking policy."""

        return self.delegate.policy

    def chunk(self, document: CleanedDocument) -> ChunkedDocument:
        """Record the input before returning or raising the configured value."""

        self.inputs.append(document)
        if self.mutate_input is not None:
            self.mutate_input(document)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result  # type: ignore[return-value]
        return self.delegate.chunk(document)


def _process(
    loaded: LoadedDocument,
    *,
    cleaner: _StaticCleaner | None = None,
    chunker: _StaticChunker | None = None,
) -> ChunkedDocument:
    """Run one fixture through the public service boundary."""

    service = DocumentProcessingService(
        _StaticLoader(loaded),
        cleaner=cleaner,
        chunker=chunker,
    )
    return service.process(loaded.source.scope, loaded.source.link_id)


def _reidentify(
    chunked: ChunkedDocument,
    chunk: DocumentChunk,
) -> DocumentChunk:
    """Rebuild a valid chunk ID after one adversarial test mutation."""

    return replace(
        chunk,
        chunk_id=_build_chunk_id(
            chunked.derivation_fingerprint,
            ordinal=chunk.ordinal,
            kind=chunk.kind,
            text=chunk.text,
            page_number=chunk.page_number,
            mappings=chunk.source_mappings,
        ),
    )


def test_pipeline_composes_default_adapters_and_preserves_exact_sources() -> None:
    """The service publishes deterministic prose, code, and table chunks."""

    loaded = _document(
        (
            DocumentBlock(ordinal=0, kind="paragraph", text="Alpha"),
            DocumentBlock(ordinal=1, kind="code", text="x = 1\r\n"),
            DocumentBlock(
                ordinal=2,
                kind="table",
                table=DocumentTable(rows=(("a\nb", ""), ("中",))),
            ),
        )
    )

    result = _process(loaded)

    assert tuple(chunk.kind for chunk in result.chunks) == (
        "prose",
        "code",
        "table",
    )
    assert result.chunks[0].text == "Alpha"
    assert result.chunks[1].text == "x = 1\r\n"
    assert result.chunks[2].text == '["a\\nb",""]\n["中"]'
    assert result.provenance.source == loaded.source


def test_pipeline_passes_each_verified_result_to_the_next_adapter() -> None:
    """Adapters receive equivalent values isolated from authoritative state."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaner = _StaticCleaner()
    chunker = _StaticChunker()

    result = _process(loaded, cleaner=cleaner, chunker=chunker)

    assert cleaner.inputs == [loaded]
    assert cleaner.inputs[0] is not loaded
    assert cleaner.inputs[0].source is not loaded.source
    assert cleaner.inputs[0].source.scope is not loaded.source.scope
    assert chunker.inputs == [cleaner.delegate.clean(loaded)]
    assert chunker.inputs[0].provenance.source is not cleaner.inputs[0].source
    assert result == chunker.delegate.chunk(chunker.inputs[0])


def test_pipeline_accepts_audited_pdf_omissions_and_nonzero_offsets() -> None:
    """Repeated headers remain omissions while body mappings retain offsets."""

    loaded = _document(
        tuple(
            DocumentBlock(
                ordinal=index,
                kind="paragraph",
                text=f"Header\r\nBody {index + 1}",
                page_number=index + 1,
            )
            for index in range(3)
        ),
        document_format="pdf",
        page_count=3,
    )

    result = _process(loaded)

    assert tuple(chunk.text for chunk in result.chunks) == (
        "Body 1",
        "Body 2",
        "Body 3",
    )
    assert tuple(
        chunk.source_mappings[0].source_span.start_code_point
        for chunk in result.chunks
    ) == (8, 8, 8)


def test_pipeline_rejects_loader_output_for_another_ownership() -> None:
    """A substitute source loader cannot cross the requested Scope or Link."""

    requested_scope = AttachmentScope(kind="chat", id="chat_requested")
    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),),
        source=_source(
            scope=AttachmentScope(kind="chat", id="chat_other"),
            link_id="attachment_other",
        ),
    )
    service = DocumentProcessingService(_StaticLoader(loaded))

    with pytest.raises(DocumentValidationError, match="ownership"):
        service.process(requested_scope, "attachment_requested")


def test_pipeline_isolates_requested_scope_from_loader_mutation() -> None:
    """A loader cannot rewrite the authority value passed by the caller."""

    requested_scope = AttachmentScope(kind="chat", id="chat_scope_snapshot")
    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),),
        source=_source(scope=requested_scope),
    )

    def _mutate_scope(scope: AttachmentScope) -> None:
        """Simulate an adapter bypassing the frozen request dataclass."""

        object.__setattr__(scope, "id", "chat_loader_mutation")

    loader = _StaticLoader(loaded, mutate_scope=_mutate_scope)
    result = DocumentProcessingService(loader).process(
        requested_scope,
        loaded.source.link_id,
    )

    assert requested_scope.id == "chat_scope_snapshot"
    assert loader.calls[0][0].id == "chat_loader_mutation"
    assert result.provenance.source.scope.id == "chat_scope_snapshot"


def test_pipeline_rejects_cleaner_mutation_of_loaded_ownership() -> None:
    """Cleaner input mutation cannot replace the authoritative loaded owner."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    original_source = loaded.source
    substituted_source = _source(
        scope=AttachmentScope(kind="chat", id="chat_cleaner_mutation"),
        link_id="attachment_cleaner_mutation",
    )

    def _mutate_loaded(document: LoadedDocument) -> None:
        """Replace the adapter copy's owner after loader validation."""

        object.__setattr__(document, "source", substituted_source)

    cleaner = _StaticCleaner(mutate_input=_mutate_loaded)

    with pytest.raises(DocumentValidationError, match="provenance|ownership"):
        _process(loaded, cleaner=cleaner)

    assert loaded.source is original_source
    assert loaded.source.scope.id == "chat_pipeline"


def test_pipeline_snapshots_cleaner_policy_and_limits_before_call() -> None:
    """Cleaner code cannot retroactively alter captured adapter metadata."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaner = _StaticCleaner()

    def _mutate_metadata(_: LoadedDocument) -> None:
        """Change both mutable metadata wrappers during adapter execution."""

        object.__setattr__(
            cleaner.delegate.policy,
            "repeated_pdf_header_min_pages",
            4,
        )
        object.__setattr__(cleaner.delegate.limits, "max_chunks", 9_999)

    cleaner.mutate_input = _mutate_metadata

    with pytest.raises(DocumentValidationError, match="policy or limits"):
        _process(loaded, cleaner=cleaner)


def test_pipeline_rejects_chunker_mutation_of_nested_source() -> None:
    """Chunker input cannot mutate authoritative nested ownership objects."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    chunker = _StaticChunker()

    def _mutate_source(document: CleanedDocument) -> None:
        """Rewrite fields inside the copied provenance source and scope."""

        object.__setattr__(
            document.provenance.source.scope,
            "id",
            "chat_chunker_mutation",
        )
        object.__setattr__(
            document.provenance.source,
            "link_id",
            "attachment_chunker_mutation",
        )

    chunker.mutate_input = _mutate_source

    with pytest.raises(DocumentValidationError, match="lineage|ownership"):
        _process(loaded, chunker=chunker)

    assert loaded.source.scope.id == "chat_pipeline"
    assert loaded.source.link_id == "attachment_pipeline"


def test_pipeline_rejects_chunker_mutation_of_nested_table() -> None:
    """Chunker table mutation cannot alter the authoritative source table."""

    source_table = DocumentTable(rows=(("source",),))
    loaded = _document(
        (
            DocumentBlock(
                ordinal=0,
                kind="table",
                table=source_table,
            ),
        )
    )
    chunker = _StaticChunker()

    def _mutate_table(document: CleanedDocument) -> None:
        """Replace rows on the adapter copy's nested table wrapper."""

        block = document.blocks[0]
        assert isinstance(block, CleanedTableBlock)
        object.__setattr__(block.table, "rows", (("forged",),))

    chunker.mutate_input = _mutate_table

    with pytest.raises(DocumentValidationError, match="canonical projection"):
        _process(loaded, chunker=chunker)

    assert source_table.rows == (("source",),)
    assert loaded.blocks[0].table is source_table


def test_pipeline_snapshots_chunker_policy_before_call() -> None:
    """Chunker code cannot retroactively change its advertised policy."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    chunker = _StaticChunker()

    def _mutate_policy(_: CleanedDocument) -> None:
        """Change the adapter-owned policy after the pipeline captures it."""

        object.__setattr__(
            chunker.delegate.policy,
            "max_chunk_code_points",
            32,
        )

    chunker.mutate_input = _mutate_policy

    with pytest.raises(DocumentValidationError, match="active policy"):
        _process(loaded, chunker=chunker)


def test_pipeline_returns_graph_isolated_from_chunker_objects() -> None:
    """Published chunks cannot be changed through adapter-retained objects."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    adapter_result = StructureAwareDocumentChunker().chunk(cleaned)
    chunker = _StaticChunker(result=adapter_result)

    result = _process(loaded, chunker=chunker)

    assert result == adapter_result
    assert result is not adapter_result
    assert result.provenance is not adapter_result.provenance
    assert result.provenance.source is not adapter_result.provenance.source
    assert result.provenance.source.scope is not adapter_result.provenance.source.scope
    assert result.policy is not adapter_result.policy
    assert result.limits is not adapter_result.limits
    assert result.chunks[0] is not adapter_result.chunks[0]
    assert (
        result.chunks[0].source_mappings[0]
        is not adapter_result.chunks[0].source_mappings[0]
    )
    assert (
        result.chunks[0].source_mappings[0].source_span
        is not adapter_result.chunks[0].source_mappings[0].source_span
    )

    object.__setattr__(adapter_result.provenance.source.scope, "id", "chat_after")
    object.__setattr__(adapter_result.chunks[0], "text", "changed")
    object.__setattr__(chunker.inputs[0].provenance.source, "link_id", "attachment_after")

    assert result.provenance.source.scope.id == "chat_pipeline"
    assert result.provenance.source.link_id == "attachment_pipeline"
    assert result.chunks[0].text == "Body"


def test_pipeline_recomputes_cleaning_fingerprints_independently() -> None:
    """A syntactically valid but forged cleaner digest is rejected."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    forged = replace(cleaned)
    # A hostile adapter can bypass frozen dataclass syntax at runtime; the
    # service therefore independently recomputes even init=False identities.
    object.__setattr__(forged, "cleaning_fingerprint", "0" * 64)

    with pytest.raises(DocumentValidationError, match="cleaning fingerprint"):
        _process(loaded, cleaner=_StaticCleaner(result=forged))


def test_pipeline_rejects_invalid_adapter_result_shapes() -> None:
    """Adapters cannot bypass the concrete cleaned and chunked domains."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )

    with pytest.raises(DocumentValidationError, match="cleaner returned"):
        _process(loaded, cleaner=_StaticCleaner(result=object()))
    with pytest.raises(DocumentValidationError, match="chunker returned"):
        _process(loaded, chunker=_StaticChunker(result=object()))


def test_pipeline_preflights_hostile_graphs_before_snapshot_copying() -> None:
    """Authority limits reject enlarged tuples before cloning their members."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    object.__setattr__(
        loaded,
        "limits",
        replace(loaded.limits, max_blocks=1),
    )
    object.__setattr__(loaded, "blocks", (loaded.blocks[0], loaded.blocks[0]))
    with pytest.raises(DocumentContentLimitError, match="block count"):
        _process(loaded)

    source = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaning_limits = replace(
        DocumentProcessingLimits(),
        max_cleaned_blocks=1,
        max_chunks=1,
    )
    cleaner_delegate = ConservativeDocumentCleaner(limits=cleaning_limits)
    enlarged_cleaned = cleaner_delegate.clean(source)
    object.__setattr__(
        enlarged_cleaned,
        "blocks",
        (enlarged_cleaned.blocks[0], enlarged_cleaned.blocks[0]),
    )
    hostile_cleaner = _StaticCleaner(result=enlarged_cleaned)
    hostile_cleaner.delegate = cleaner_delegate
    with pytest.raises(DocumentContentLimitError, match="block count"):
        _process(source, cleaner=hostile_cleaner)

    valid_cleaned = cleaner_delegate.clean(source)
    enlarged_chunked = StructureAwareDocumentChunker().chunk(valid_cleaned)
    object.__setattr__(
        enlarged_chunked,
        "chunks",
        (enlarged_chunked.chunks[0], enlarged_chunked.chunks[0]),
    )
    bounded_cleaner = _StaticCleaner(result=valid_cleaned)
    bounded_cleaner.delegate = cleaner_delegate
    with pytest.raises(DocumentContentLimitError, match="chunk count"):
        _process(
            source,
            cleaner=bounded_cleaner,
            chunker=_StaticChunker(result=enlarged_chunked),
        )


def test_pipeline_rejects_cleaner_provenance_policy_and_limit_changes() -> None:
    """A cleaner result must match its exact source and advertised settings."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    other = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),),
        source=_source(
            scope=AttachmentScope(kind="chat", id="chat_other_lineage"),
            link_id="attachment_other_lineage",
        ),
    )
    wrong_provenance = ConservativeDocumentCleaner().clean(other)
    with pytest.raises(DocumentValidationError, match="provenance"):
        _process(
            loaded,
            cleaner=_StaticCleaner(result=wrong_provenance),
        )

    alternate = ConservativeDocumentCleaner(
        policy=DocumentCleaningPolicy(
            repeated_pdf_header_min_pages=4,
            repeated_pdf_header_max_code_points=128,
        ),
        limits=replace(
            DocumentProcessingLimits(),
            max_chunks=9_999,
        ),
    ).clean(loaded)
    with pytest.raises(DocumentValidationError, match="policy or limits"):
        _process(loaded, cleaner=_StaticCleaner(result=alternate))


def test_pipeline_requires_cleaning_spans_to_partition_the_source() -> None:
    """A cleaner cannot hide an unreported source suffix or overlap omission."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="abcdef"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    block = cleaned.blocks[0]
    assert isinstance(block, CleanedTextBlock)
    truncated = replace(
        block,
        text="abc",
        retained_spans=(
            DocumentTextSpan(
                block_ordinal=0,
                start_code_point=0,
                end_code_point=3,
            ),
        ),
    )
    missing = replace(cleaned, blocks=(truncated,))
    with pytest.raises(DocumentValidationError, match="complete source"):
        _process(loaded, cleaner=_StaticCleaner(result=missing))

    pdf = _document(
        tuple(
            DocumentBlock(
                ordinal=index,
                kind="paragraph",
                text=f"Header\nBody {index + 1}",
                page_number=index + 1,
            )
            for index in range(3)
        ),
        document_format="pdf",
        page_count=3,
    )
    pdf_cleaned = ConservativeDocumentCleaner().clean(pdf)
    first = pdf_cleaned.blocks[0]
    assert isinstance(first, CleanedTextBlock)
    overlapping = replace(
        pdf_cleaned,
        blocks=(
            replace(
                first,
                text="\nBody 1",
                retained_spans=(
                    DocumentTextSpan(
                        block_ordinal=0,
                        start_code_point=6,
                        end_code_point=len("Header\nBody 1"),
                        page_number=1,
                    ),
                ),
            ),
            *pdf_cleaned.blocks[1:],
        ),
    )
    with pytest.raises(DocumentValidationError, match="without gaps or overlap"):
        _process(pdf, cleaner=_StaticCleaner(result=overlapping))


def test_pipeline_rejects_arbitrary_prefix_omission() -> None:
    """A complete piece table cannot label arbitrary prose as a PDF header."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="abcdef"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    block = cleaned.blocks[0]
    assert isinstance(block, CleanedTextBlock)
    forged = replace(
        cleaned,
        blocks=(
            replace(
                block,
                text="def",
                retained_spans=(DocumentTextSpan(0, 3, 6),),
            ),
        ),
        omissions=(
            CleaningOmission(
                reason="repeated_pdf_page_header",
                source_span=DocumentTextSpan(0, 0, 3),
            ),
        ),
    )

    with pytest.raises(DocumentValidationError, match="deterministic policy"):
        _process(loaded, cleaner=_StaticCleaner(result=forged))


def test_pipeline_requires_cleaner_tables_to_remain_exact() -> None:
    """Cleaning cannot pad, truncate, or rewrite a ragged source table."""

    loaded = _document(
        (
            DocumentBlock(
                ordinal=0,
                kind="table",
                table=DocumentTable(rows=(("a", ""), ("b",))),
            ),
        )
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    block = cleaned.blocks[0]
    assert isinstance(block, CleanedTableBlock)
    changed = replace(
        cleaned,
        blocks=(replace(block, table=DocumentTable(rows=(("changed",),))),),
    )

    with pytest.raises(DocumentValidationError, match="exactly preserve"):
        _process(loaded, cleaner=_StaticCleaner(result=changed))


def test_pipeline_rejects_cleaner_and_chunker_metadata_substitution() -> None:
    """Adapter-declared identities must equal the returned lineage fields."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    chunked = StructureAwareDocumentChunker().chunk(cleaned)

    with pytest.raises(DocumentValidationError, match="version metadata"):
        _process(
            loaded,
            cleaner=_StaticCleaner(
                result=cleaned,
                cleaner_id="other-cleaner",
            ),
        )
    with pytest.raises(DocumentValidationError, match="version metadata"):
        _process(
            loaded,
            chunker=_StaticChunker(
                result=chunked,
                chunker_id="other-chunker",
            ),
        )

    alternate_chunker = StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=32)
    )
    alternate_chunks = alternate_chunker.chunk(cleaned)
    with pytest.raises(DocumentValidationError, match="active policy"):
        _process(
            loaded,
            chunker=_StaticChunker(result=alternate_chunks),
        )


def test_pipeline_rejects_text_mapping_to_the_wrong_retained_slice() -> None:
    """Chunk text must equal the exact LoadedDocument code-point range."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="abcxyz"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    real_chunker = StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=3)
    )
    chunked = real_chunker.chunk(cleaned)
    first = chunked.chunks[0]
    wrong_mapping = ChunkSourceMapping(
        chunk_start_code_point=0,
        chunk_end_code_point=3,
        source_span=DocumentTextSpan(
            block_ordinal=0,
            start_code_point=3,
            end_code_point=6,
        ),
    )
    forged = _reidentify(
        chunked,
        replace(first, source_mappings=(wrong_mapping,)),
    )
    forged_document = replace(
        chunked,
        chunks=(forged, *chunked.chunks[1:]),
    )

    with pytest.raises(DocumentValidationError, match="reproduce"):
        _process(
            loaded,
            chunker=_StaticChunker(
                result=forged_document,
                policy=real_chunker.policy,
            ),
        )


def test_pipeline_allows_only_the_canonical_unmapped_prose_separator() -> None:
    """Unmapped generated text cannot be injected between prose sources."""

    loaded = _document(
        (
            DocumentBlock(ordinal=0, kind="paragraph", text="a"),
            DocumentBlock(ordinal=1, kind="paragraph", text="b"),
        )
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    chunked = StructureAwareDocumentChunker().chunk(cleaned)
    original = chunked.chunks[0]
    assert original.text == "a\n\nb"
    forged = replace(original)
    object.__setattr__(forged, "text", "a--b")
    object.__setattr__(
        forged,
        "chunk_id",
        _build_chunk_id(
            chunked.derivation_fingerprint,
            ordinal=forged.ordinal,
            kind=forged.kind,
            text=forged.text,
            page_number=forged.page_number,
            mappings=forged.source_mappings,
        ),
    )
    forged_document = replace(chunked, chunks=(forged,))

    with pytest.raises(
        DocumentValidationError,
        match="mapping gap|canonical prose separator",
    ):
        _process(loaded, chunker=_StaticChunker(result=forged_document))


def test_pipeline_accepts_expanded_table_escapes_but_checks_projection() -> None:
    """Table spans may repeat for escapes, while canonical JSONL stays exact."""

    loaded = _document(
        (
            DocumentBlock(
                ordinal=0,
                kind="table",
                table=DocumentTable(rows=(("\n",),)),
            ),
        )
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    tiny = StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=1)
    )
    valid = tiny.chunk(cleaned)
    assert _process(
        loaded,
        chunker=_StaticChunker(result=valid, policy=tiny.policy),
    ) == valid

    first = valid.chunks[0]
    replacement = "x" if first.text != "x" else "y"
    forged = _reidentify(valid, replace(first, text=replacement))
    forged_document = replace(valid, chunks=(forged, *valid.chunks[1:]))
    with pytest.raises(DocumentValidationError, match="canonical projection"):
        _process(
            loaded,
            chunker=_StaticChunker(
                result=forged_document,
                policy=tiny.policy,
            ),
        )


def test_pipeline_rejects_table_projection_mapped_to_the_wrong_cell() -> None:
    """Canonical text alone cannot forge every table citation to one cell."""

    loaded = _document(
        (
            DocumentBlock(
                ordinal=0,
                kind="table",
                table=DocumentTable(rows=(("", "secret"),)),
            ),
        )
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    chunked = StructureAwareDocumentChunker().chunk(cleaned)
    original = chunked.chunks[0]
    wrong_cell = DocumentTableCellSpan(
        block_ordinal=0,
        row_index=0,
        column_index=0,
        start_code_point=0,
        end_code_point=0,
    )
    mappings = tuple(
        replace(mapping, source_span=wrong_cell)
        for mapping in original.source_mappings
    )
    forged = _reidentify(
        chunked,
        replace(original, source_mappings=mappings),
    )
    forged_document = replace(chunked, chunks=(forged,))

    with pytest.raises(DocumentValidationError, match="cell provenance"):
        _process(loaded, chunker=_StaticChunker(result=forged_document))


def test_pipeline_rejects_chunkers_that_omit_cleaned_content() -> None:
    """A valid empty chunk tuple cannot discard a non-empty cleaned document."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    chunked = StructureAwareDocumentChunker().chunk(cleaned)
    empty = replace(chunked, chunks=())

    with pytest.raises(DocumentValidationError, match="omit retained"):
        _process(loaded, chunker=_StaticChunker(result=empty))


@pytest.mark.parametrize("error", (MemoryError(), RecursionError()))
def test_pipeline_maps_resource_failures_to_content_limits(
    error: BaseException,
) -> None:
    """Resource exhaustion never escapes as a native runtime exception."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )

    with pytest.raises(DocumentContentLimitError, match="safe resource"):
        _process(loaded, cleaner=_StaticCleaner(error=error))


def test_pipeline_preserves_typed_errors_and_sanitizes_unknown_failures() -> None:
    """Known failures pass through while unknown adapter errors are wrapped."""

    loaded = _document(
        (DocumentBlock(ordinal=0, kind="paragraph", text="Body"),)
    )
    typed = DocumentNotFoundError("missing")
    service = DocumentProcessingService(
        _StaticLoader(loaded, error=typed)
    )

    with pytest.raises(DocumentNotFoundError) as captured:
        service.process(loaded.source.scope, loaded.source.link_id)
    assert captured.value is typed

    with pytest.raises(DocumentProcessingFailedError, match="safe typed"):
        _process(
            loaded,
            chunker=_StaticChunker(error=RuntimeError("private details")),
        )
