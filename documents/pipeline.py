"""Orchestrate and verify the pure loaded-to-cleaned-to-chunked pipeline.

The service treats cleaner and chunker implementations as untrusted adapters.
It independently verifies their lineage, lossless piece tables, fingerprints,
and source mappings before publishing chunks.  The pipeline performs no file
persistence, embedding, retrieval, model, network, or desktop UI work.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import Final

from attachments.domain import AttachmentScope

from .chunking import (
    ChunkSourceMapping,
    ChunkedDocument,
    DocumentChunk,
    DocumentChunkingPolicy,
    StructureAwareDocumentChunker,
)
from .cleaning import (
    CleanedBlock,
    CleanedDocument,
    CleanedTableBlock,
    CleanedTextBlock,
    CleaningOmission,
    ConservativeDocumentCleaner,
    DocumentCleaningPolicy,
    DocumentProcessingLimits,
    DocumentTableCellSpan,
    DocumentTextSpan,
    LoadedDocumentProvenance,
    _build_cleaning_fingerprint,
    _build_document_fingerprint,
)
from .domain import (
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTable,
    DocumentTitle,
    LoadedDocument,
)
from .exceptions import (
    DocumentContentLimitError,
    DocumentError,
    DocumentOperationCancelledError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)
from .protocol import DocumentChunker, DocumentCleaner, DocumentSourceLoader


_TABLE_ESCAPES: Final = {
    '"': '\\"',
    "\\": "\\\\",
    "\t": "\\t",
    "\n": "\\n",
    "\r": "\\r",
}
_ZERO_WIDTH_NO_BREAK_SPACE: Final = "\ufeff"


def _raise_if_processing_cancelled(
    cancel_requested: Callable[[], bool] | None,
) -> None:
    """Stop between bounded adapters before more document work is admitted."""

    if cancel_requested is not None and cancel_requested():
        raise DocumentOperationCancelledError(
            "Document processing was cancelled before commit."
        )


def _snapshot_scope(scope: object) -> AttachmentScope:
    """Copy one request scope so adapters cannot rewrite the authority value."""

    if not isinstance(scope, AttachmentScope):
        raise DocumentValidationError("scope must be AttachmentScope.")
    try:
        return AttachmentScope(kind=scope.kind, id=scope.id)
    except ValueError as error:
        raise DocumentValidationError("Attachment scope is invalid.") from error


def _snapshot_load_limits(limits: object) -> DocumentLoadLimits:
    """Rebuild loader limits without sharing a mutable dataclass instance."""

    if not isinstance(limits, DocumentLoadLimits):
        raise DocumentValidationError("limits must be DocumentLoadLimits.")
    return DocumentLoadLimits(
        max_source_bytes=limits.max_source_bytes,
        max_expanded_bytes=limits.max_expanded_bytes,
        max_text_code_points=limits.max_text_code_points,
        max_blocks=limits.max_blocks,
        max_pages=limits.max_pages,
        max_package_entries=limits.max_package_entries,
        max_table_cells=limits.max_table_cells,
        max_table_columns=limits.max_table_columns,
        max_cell_code_points=limits.max_cell_code_points,
        max_xml_elements=limits.max_xml_elements,
        max_xml_depth=limits.max_xml_depth,
    )


def _snapshot_processing_limits(limits: object) -> DocumentProcessingLimits:
    """Rebuild processing ceilings before exposing any input to an adapter."""

    if not isinstance(limits, DocumentProcessingLimits):
        raise DocumentValidationError(
            "Processing limits must be DocumentProcessingLimits."
        )
    return DocumentProcessingLimits(
        max_cleaned_code_points=limits.max_cleaned_code_points,
        max_cleaned_blocks=limits.max_cleaned_blocks,
        max_cleaning_omissions=limits.max_cleaning_omissions,
        max_chunks=limits.max_chunks,
        max_total_chunk_code_points=limits.max_total_chunk_code_points,
        max_source_mappings_per_chunk=limits.max_source_mappings_per_chunk,
        max_total_source_mappings=limits.max_total_source_mappings,
    )


def _snapshot_source(source: object) -> DocumentSource:
    """Copy ownership metadata, including its nested authorization scope."""

    if not isinstance(source, DocumentSource):
        raise DocumentValidationError("source must be DocumentSource.")
    return DocumentSource(
        scope=_snapshot_scope(source.scope),
        link_id=source.link_id,
        file_id=source.file_id,
        file_name=source.file_name,
        media_type=source.media_type,
        size_bytes=source.size_bytes,
    )


def _snapshot_title(title: object) -> DocumentTitle | None:
    """Copy optional title metadata while rerunning its public validation."""

    if title is None:
        return None
    if not isinstance(title, DocumentTitle):
        raise DocumentValidationError("title must be DocumentTitle or None.")
    return DocumentTitle(text=title.text, source=title.source)


def _snapshot_table(table: object) -> DocumentTable:
    """Copy the complete tuple shape while safely sharing immutable strings."""

    if not isinstance(table, DocumentTable):
        raise DocumentValidationError("table must be DocumentTable.")
    return DocumentTable(
        rows=tuple(tuple(cell for cell in row) for row in table.rows)
    )


def _snapshot_loaded_block(block: object) -> DocumentBlock:
    """Rebuild one loaded block and its mutable table domain wrapper."""

    if not isinstance(block, DocumentBlock):
        raise DocumentValidationError("Loaded blocks are invalid.")
    return DocumentBlock(
        ordinal=block.ordinal,
        kind=block.kind,
        text=block.text,
        table=(None if block.table is None else _snapshot_table(block.table)),
        page_number=block.page_number,
        heading_level=block.heading_level,
    )


def _preflight_loaded_graph(
    document: LoadedDocument,
    limits: DocumentLoadLimits,
) -> None:
    """Bound hostile loaded collections before allocating their snapshot."""

    if not isinstance(document.blocks, tuple):
        raise DocumentValidationError("Loaded blocks are invalid.")
    if len(document.blocks) > limits.max_blocks:
        raise DocumentContentLimitError(
            "Document block count exceeds the configured limit."
        )
    total_code_points = 0
    if document.title is not None:
        if not isinstance(document.title, DocumentTitle):
            raise DocumentValidationError("title must be DocumentTitle or None.")
        if not isinstance(document.title.text, str):
            raise DocumentValidationError("Document title text is invalid.")
        total_code_points += len(document.title.text)
    table_cells = 0
    for block in document.blocks:
        if not isinstance(block, DocumentBlock):
            raise DocumentValidationError("Loaded blocks are invalid.")
        if block.text is not None:
            if not isinstance(block.text, str):
                raise DocumentValidationError("Loaded block text is invalid.")
            total_code_points += len(block.text)
        if block.table is not None:
            if not isinstance(block.table, DocumentTable) or not isinstance(
                block.table.rows,
                tuple,
            ):
                raise DocumentValidationError("Loaded table data is invalid.")
            for row in block.table.rows:
                if not isinstance(row, tuple) or not row:
                    raise DocumentValidationError("Loaded table rows are invalid.")
                if len(row) > limits.max_table_columns:
                    raise DocumentContentLimitError(
                        "Document table exceeds the configured column limit."
                    )
                table_cells += len(row)
                if table_cells > limits.max_table_cells:
                    raise DocumentContentLimitError(
                        "Document tables exceed the configured cell limit."
                    )
                for cell in row:
                    if not isinstance(cell, str):
                        raise DocumentValidationError(
                            "Loaded table cells are invalid."
                        )
                    if len(cell) > limits.max_cell_code_points:
                        raise DocumentContentLimitError(
                            "Document table cell exceeds the configured limit."
                        )
                    total_code_points += len(cell)
                    if total_code_points > limits.max_text_code_points:
                        raise DocumentContentLimitError(
                            "Document text exceeds the configured code-point limit."
                        )
        if total_code_points > limits.max_text_code_points:
            raise DocumentContentLimitError(
                "Document text exceeds the configured code-point limit."
            )


def _snapshot_loaded_document(document: object) -> LoadedDocument:
    """Build a recursively isolated, revalidated loaded-document snapshot."""

    if not isinstance(document, LoadedDocument):
        raise DocumentValidationError(
            "Document source loader returned an invalid result."
        )
    limits = _snapshot_load_limits(document.limits)
    _preflight_loaded_graph(document, limits)
    return LoadedDocument(
        schema_version=document.schema_version,
        source=_snapshot_source(document.source),
        document_format=document.document_format,
        loader_id=document.loader_id,
        loader_version=document.loader_version,
        blocks=tuple(_snapshot_loaded_block(block) for block in document.blocks),
        limits=limits,
        title=_snapshot_title(document.title),
        page_count=document.page_count,
    )


def _snapshot_cleaning_policy(policy: object) -> DocumentCleaningPolicy:
    """Copy cleaner policy so the adapter cannot change captured metadata."""

    if not isinstance(policy, DocumentCleaningPolicy):
        raise DocumentValidationError("Document cleaner exposed an invalid policy.")
    return DocumentCleaningPolicy(
        repeated_pdf_header_min_pages=policy.repeated_pdf_header_min_pages,
        repeated_pdf_header_max_code_points=(
            policy.repeated_pdf_header_max_code_points
        ),
    )


def _snapshot_chunking_policy(policy: object) -> DocumentChunkingPolicy:
    """Copy chunker policy before invoking code that can mutate its owner."""

    if not isinstance(policy, DocumentChunkingPolicy):
        raise DocumentValidationError("Document chunker exposed an invalid policy.")
    return DocumentChunkingPolicy(
        max_chunk_code_points=policy.max_chunk_code_points,
        overlap_code_points=policy.overlap_code_points,
        prose_separator=policy.prose_separator,
        table_projection_version=policy.table_projection_version,
    )


def _snapshot_text_span(span: object) -> DocumentTextSpan:
    """Copy one non-empty source text range."""

    if not isinstance(span, DocumentTextSpan):
        raise DocumentValidationError("Text provenance spans are invalid.")
    return DocumentTextSpan(
        block_ordinal=span.block_ordinal,
        start_code_point=span.start_code_point,
        end_code_point=span.end_code_point,
        page_number=span.page_number,
    )


def _snapshot_table_span(span: object) -> DocumentTableCellSpan:
    """Copy one table-cell source range or empty projection anchor."""

    if not isinstance(span, DocumentTableCellSpan):
        raise DocumentValidationError("Table provenance spans are invalid.")
    return DocumentTableCellSpan(
        block_ordinal=span.block_ordinal,
        row_index=span.row_index,
        column_index=span.column_index,
        start_code_point=span.start_code_point,
        end_code_point=span.end_code_point,
        page_number=span.page_number,
    )


def _snapshot_provenance(provenance: object) -> LoadedDocumentProvenance:
    """Copy every mutable wrapper in loaded-document lineage."""

    if not isinstance(provenance, LoadedDocumentProvenance):
        raise DocumentValidationError(
            "provenance must be LoadedDocumentProvenance."
        )
    return LoadedDocumentProvenance(
        loaded_schema_version=provenance.loaded_schema_version,
        source=_snapshot_source(provenance.source),
        document_format=provenance.document_format,
        loader_id=provenance.loader_id,
        loader_version=provenance.loader_version,
        load_limits=_snapshot_load_limits(provenance.load_limits),
    )


def _snapshot_cleaned_block(block: object) -> CleanedBlock:
    """Copy one cleaned union variant and all nested provenance wrappers."""

    if isinstance(block, CleanedTextBlock):
        if not isinstance(block.retained_spans, tuple):
            raise DocumentValidationError("Cleaned text spans are invalid.")
        return CleanedTextBlock(
            ordinal=block.ordinal,
            source_block_ordinal=block.source_block_ordinal,
            kind=block.kind,
            text=block.text,
            retained_spans=tuple(
                _snapshot_text_span(span) for span in block.retained_spans
            ),
            page_number=block.page_number,
            heading_level=block.heading_level,
        )
    if isinstance(block, CleanedTableBlock):
        return CleanedTableBlock(
            ordinal=block.ordinal,
            source_block_ordinal=block.source_block_ordinal,
            table=_snapshot_table(block.table),
            page_number=block.page_number,
        )
    raise DocumentValidationError("Cleaned blocks are invalid.")


def _preflight_cleaned_graph(
    document: CleanedDocument,
    limits: DocumentProcessingLimits,
) -> None:
    """Bound cleaner collections before copying spans, omissions, or blocks."""

    if not isinstance(document.blocks, tuple) or not isinstance(
        document.omissions,
        tuple,
    ):
        raise DocumentValidationError("Cleaned document collections are invalid.")
    if len(document.blocks) > limits.max_cleaned_blocks:
        raise DocumentContentLimitError(
            "Cleaned block count exceeds the configured limit."
        )
    if len(document.omissions) > limits.max_cleaning_omissions:
        raise DocumentContentLimitError(
            "Cleaning omissions exceed the configured limit."
        )
    total_code_points = 0
    source_pieces = 0
    for block in document.blocks:
        if isinstance(block, CleanedTextBlock):
            if not isinstance(block.text, str) or not isinstance(
                block.retained_spans,
                tuple,
            ):
                raise DocumentValidationError("Cleaned text data is invalid.")
            total_code_points += len(block.text)
            source_pieces += len(block.retained_spans)
        elif isinstance(block, CleanedTableBlock):
            table = block.table
            if not isinstance(table, DocumentTable) or not isinstance(
                table.rows,
                tuple,
            ):
                raise DocumentValidationError("Cleaned table data is invalid.")
            for row in table.rows:
                if not isinstance(row, tuple) or not row:
                    raise DocumentValidationError("Cleaned table rows are invalid.")
                source_pieces += len(row)
                if source_pieces > limits.max_total_source_mappings:
                    raise DocumentContentLimitError(
                        "Cleaned provenance exceeds the configured mapping limit."
                    )
                for cell in row:
                    if not isinstance(cell, str):
                        raise DocumentValidationError(
                            "Cleaned table cells are invalid."
                        )
                    total_code_points += len(cell)
                    if total_code_points > limits.max_cleaned_code_points:
                        raise DocumentContentLimitError(
                            "Cleaned text exceeds the configured limit."
                        )
        else:
            raise DocumentValidationError("Cleaned blocks are invalid.")
        if total_code_points > limits.max_cleaned_code_points:
            raise DocumentContentLimitError(
                "Cleaned text exceeds the configured limit."
            )
        if source_pieces > limits.max_total_source_mappings:
            raise DocumentContentLimitError(
                "Cleaned provenance exceeds the configured mapping limit."
            )


def _snapshot_cleaned_document(
    document: object,
    *,
    resource_limits: DocumentProcessingLimits,
) -> CleanedDocument:
    """Build a recursively isolated cleaned snapshot in linear bounded work."""

    if not isinstance(document, CleanedDocument):
        raise DocumentValidationError(
            "Document cleaner returned an invalid result."
        )
    _preflight_cleaned_graph(document, resource_limits)
    omissions: list[CleaningOmission] = []
    for omission in document.omissions:
        if not isinstance(omission, CleaningOmission):
            raise DocumentValidationError("Cleaning omissions are invalid.")
        omissions.append(
            CleaningOmission(
                reason=omission.reason,
                source_span=_snapshot_text_span(omission.source_span),
            )
        )
    return CleanedDocument(
        schema_version=document.schema_version,
        provenance=_snapshot_provenance(document.provenance),
        cleaner_id=document.cleaner_id,
        cleaner_version=document.cleaner_version,
        policy=_snapshot_cleaning_policy(document.policy),
        limits=_snapshot_processing_limits(document.limits),
        document_fingerprint=document.document_fingerprint,
        blocks=tuple(_snapshot_cleaned_block(block) for block in document.blocks),
        omissions=tuple(omissions),
        title=_snapshot_title(document.title),
        page_count=document.page_count,
    )


def _snapshot_chunk_mapping(mapping: object) -> ChunkSourceMapping:
    """Copy one chunk mapping and its mutable source-span wrapper."""

    if not isinstance(mapping, ChunkSourceMapping):
        raise DocumentValidationError("Chunk source mappings are invalid.")
    span = mapping.source_span
    source_span = (
        _snapshot_text_span(span)
        if isinstance(span, DocumentTextSpan)
        else _snapshot_table_span(span)
    )
    return ChunkSourceMapping(
        chunk_start_code_point=mapping.chunk_start_code_point,
        chunk_end_code_point=mapping.chunk_end_code_point,
        source_span=source_span,
    )


def _snapshot_chunk(chunk: object) -> DocumentChunk:
    """Copy one chunk and every provenance mapping it publishes."""

    if not isinstance(chunk, DocumentChunk):
        raise DocumentValidationError("Document chunks are invalid.")
    if not isinstance(chunk.source_mappings, tuple):
        raise DocumentValidationError("Chunk source mappings are invalid.")
    return DocumentChunk(
        ordinal=chunk.ordinal,
        chunk_id=chunk.chunk_id,
        kind=chunk.kind,
        text=chunk.text,
        page_number=chunk.page_number,
        source_mappings=tuple(
            _snapshot_chunk_mapping(mapping)
            for mapping in chunk.source_mappings
        ),
    )


def _preflight_chunked_graph(
    document: ChunkedDocument,
    *,
    limits: DocumentProcessingLimits,
    policy: DocumentChunkingPolicy,
) -> None:
    """Bound chunk and mapping collections before building publication copies."""

    if not isinstance(document.chunks, tuple):
        raise DocumentValidationError("Document chunks are invalid.")
    if len(document.chunks) > limits.max_chunks:
        raise DocumentContentLimitError(
            "Document chunk count exceeds the configured limit."
        )
    total_code_points = 0
    total_mappings = 0
    for chunk in document.chunks:
        if not isinstance(chunk, DocumentChunk) or not isinstance(
            chunk.source_mappings,
            tuple,
        ):
            raise DocumentValidationError("Document chunks are invalid.")
        if not isinstance(chunk.text, str):
            raise DocumentValidationError("Document chunk text is invalid.")
        if len(chunk.text) > policy.max_chunk_code_points:
            raise DocumentContentLimitError(
                "Document chunk exceeds the configured code-point limit."
            )
        if len(chunk.source_mappings) > limits.max_source_mappings_per_chunk:
            raise DocumentContentLimitError(
                "Document chunk exceeds its source-mapping limit."
            )
        total_code_points += len(chunk.text)
        total_mappings += len(chunk.source_mappings)
        if total_code_points > limits.max_total_chunk_code_points:
            raise DocumentContentLimitError(
                "Document chunks exceed the total code-point limit."
            )
        if total_mappings > limits.max_total_source_mappings:
            raise DocumentContentLimitError(
                "Document chunks exceed the total source-mapping limit."
            )


def _snapshot_chunked_document(
    document: object,
    *,
    resource_limits: DocumentProcessingLimits,
    resource_policy: DocumentChunkingPolicy,
) -> ChunkedDocument:
    """Build the final isolated chunk graph before publishing it to callers."""

    if not isinstance(document, ChunkedDocument):
        raise DocumentValidationError(
            "Document chunker returned an invalid result."
        )
    _preflight_chunked_graph(
        document,
        limits=resource_limits,
        policy=resource_policy,
    )
    return ChunkedDocument(
        schema_version=document.schema_version,
        cleaned_schema_version=document.cleaned_schema_version,
        provenance=_snapshot_provenance(document.provenance),
        cleaner_id=document.cleaner_id,
        cleaner_version=document.cleaner_version,
        document_fingerprint=document.document_fingerprint,
        cleaning_fingerprint=document.cleaning_fingerprint,
        chunker_id=document.chunker_id,
        chunker_version=document.chunker_version,
        policy=_snapshot_chunking_policy(document.policy),
        limits=_snapshot_processing_limits(document.limits),
        title=_snapshot_title(document.title),
        page_count=document.page_count,
        derivation_fingerprint=document.derivation_fingerprint,
        chunks=tuple(_snapshot_chunk(chunk) for chunk in document.chunks),
    )


def _validate_requested_owner(
    scope: AttachmentScope,
    link_id: str,
    source: DocumentSource,
) -> None:
    """Reject any adapter result that drifts from the original request owner."""

    if source.scope != scope or source.link_id != link_id:
        raise DocumentValidationError(
            "Document processing changed the requested ownership."
        )


class DocumentProcessingService:
    """Run document adapters and reject unverifiable derived output.

    The loader remains the sole authority for scope-bound verified bytes.  A
    cleaner may only describe retained and deliberately omitted ranges from
    that loaded representation, while a chunker may only project those cleaned
    ranges into bounded text.  Revalidation here prevents a custom adapter from
    silently changing owner identity, policy, version lineage, or provenance.
    """

    def __init__(
        self,
        loader: DocumentSourceLoader,
        *,
        cleaner: DocumentCleaner | None = None,
        chunker: DocumentChunker | None = None,
    ) -> None:
        """Create a stateless pipeline around explicit or built-in adapters."""

        if loader is None:
            raise TypeError("loader is required.")
        self._loader = loader
        self._cleaner = (
            ConservativeDocumentCleaner() if cleaner is None else cleaner
        )
        self._chunker = (
            StructureAwareDocumentChunker() if chunker is None else chunker
        )

    def process(
        self,
        scope: AttachmentScope,
        link_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ChunkedDocument:
        """Load, clean, chunk, and verify one exact attachment ownership link.

        Cooperative cancellation is checked around every bounded adapter. A
        synchronous loader, cleaner, or chunker cannot be interrupted midway,
        so a cancellation observed during it wins before the next derived
        representation is admitted.
        """

        try:
            if not isinstance(link_id, str):
                raise DocumentValidationError("link_id must be a string.")
            if cancel_requested is not None and not callable(cancel_requested):
                raise DocumentValidationError(
                    "cancel_requested must be callable or None."
                )
            _raise_if_processing_cancelled(cancel_requested)
            requested_scope = _snapshot_scope(scope)
            loaded_result = self._loader.load(
                _snapshot_scope(requested_scope),
                link_id,
            )
            _raise_if_processing_cancelled(cancel_requested)
            loaded = self._validate_loaded_result(
                requested_scope,
                link_id,
                loaded_result,
            )

            cleaner_id = self._cleaner.cleaner_id
            cleaner_version = self._cleaner.cleaner_version
            cleaning_policy = _snapshot_cleaning_policy(self._cleaner.policy)
            processing_limits = _snapshot_processing_limits(self._cleaner.limits)
            if not isinstance(cleaner_id, str) or not isinstance(
                cleaner_version,
                str,
            ):
                raise DocumentValidationError(
                    "Document cleaner exposed invalid version metadata."
                )
            _raise_if_processing_cancelled(cancel_requested)
            cleaned_result = self._cleaner.clean(
                _snapshot_loaded_document(loaded)
            )
            _raise_if_processing_cancelled(cancel_requested)
            cleaned = self._validate_cleaned_result(
                requested_scope,
                link_id,
                loaded,
                cleaned_result,
                cleaner_id=cleaner_id,
                cleaner_version=cleaner_version,
                policy=cleaning_policy,
                limits=processing_limits,
            )

            chunker_id = self._chunker.chunker_id
            chunker_version = self._chunker.chunker_version
            chunking_policy = _snapshot_chunking_policy(self._chunker.policy)
            if not isinstance(chunker_id, str) or not isinstance(
                chunker_version,
                str,
            ):
                raise DocumentValidationError(
                    "Document chunker exposed invalid version metadata."
                )
            _raise_if_processing_cancelled(cancel_requested)
            chunked_result = self._chunker.chunk(
                _snapshot_cleaned_document(
                    cleaned,
                    resource_limits=cleaned.limits,
                )
            )
            _raise_if_processing_cancelled(cancel_requested)
            result = self._validate_chunked_result(
                requested_scope,
                link_id,
                loaded,
                cleaned,
                chunked_result,
                chunker_id=chunker_id,
                chunker_version=chunker_version,
                policy=chunking_policy,
            )
            _raise_if_processing_cancelled(cancel_requested)
            return result
        except DocumentError:
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document processing exceeded a safe resource limit."
            ) from error
        except Exception as error:
            raise DocumentProcessingFailedError(
                "Document processing failed without a safe typed result."
            ) from error

    @staticmethod
    def _validate_loaded_result(
        scope: AttachmentScope,
        link_id: str,
        loaded: object,
    ) -> LoadedDocument:
        """Return an authoritative deep snapshot for the requested owner."""

        if not isinstance(scope, AttachmentScope):
            raise DocumentValidationError("scope must be AttachmentScope.")
        if not isinstance(link_id, str):
            raise DocumentValidationError("link_id must be a string.")
        snapshot = _snapshot_loaded_document(loaded)
        _validate_requested_owner(scope, link_id, snapshot.source)
        return snapshot

    @classmethod
    def _validate_cleaned_result(
        cls,
        scope: AttachmentScope,
        link_id: str,
        loaded: LoadedDocument,
        cleaned: object,
        *,
        cleaner_id: object,
        cleaner_version: object,
        policy: DocumentCleaningPolicy,
        limits: DocumentProcessingLimits,
    ) -> CleanedDocument:
        """Verify and isolate cleaner lineage, identity, and lossless coverage."""

        if not isinstance(cleaned, CleanedDocument):
            raise DocumentValidationError(
                "Document cleaner returned an invalid result."
            )
        declared_cleaning_fingerprint = cleaned.cleaning_fingerprint
        cleaned = _snapshot_cleaned_document(
            cleaned,
            resource_limits=limits,
        )
        if cleaned.provenance != LoadedDocumentProvenance.from_document(loaded):
            raise DocumentValidationError(
                "Document cleaner changed loaded-document provenance."
            )
        _validate_requested_owner(scope, link_id, cleaned.provenance.source)
        if (
            cleaned.cleaner_id != cleaner_id
            or cleaned.cleaner_version != cleaner_version
        ):
            raise DocumentValidationError(
                "Document cleaner returned inconsistent version metadata."
            )
        if cleaned.policy != policy or cleaned.limits != limits:
            raise DocumentValidationError(
                "Document cleaner changed its active policy or limits."
            )
        if cleaned.title != loaded.title or cleaned.page_count != loaded.page_count:
            raise DocumentValidationError(
                "Document cleaner changed source document metadata."
            )

        cls._validate_piece_table(loaded, cleaned)
        document_fingerprint = _build_document_fingerprint(loaded)
        if cleaned.document_fingerprint != document_fingerprint:
            raise DocumentValidationError(
                "Document cleaner returned an inconsistent source fingerprint."
            )
        expected_cleaning_fingerprint = _build_cleaning_fingerprint(
            document_fingerprint=document_fingerprint,
            provenance=cleaned.provenance,
            cleaner_id=cleaned.cleaner_id,
            cleaner_version=cleaned.cleaner_version,
            policy=cleaned.policy,
            blocks=cleaned.blocks,
            omissions=cleaned.omissions,
        )
        if declared_cleaning_fingerprint != expected_cleaning_fingerprint:
            raise DocumentValidationError(
                "Document cleaner returned an inconsistent cleaning fingerprint."
            )
        return cleaned

    @staticmethod
    def _validate_piece_table(
        loaded: LoadedDocument,
        cleaned: CleanedDocument,
    ) -> None:
        """Prove retained and omitted spans partition every loaded block exactly."""

        if cleaned.omissions != _expected_cleaning_omissions(
            loaded,
            cleaned.policy,
        ):
            raise DocumentValidationError(
                "Cleaning omissions do not match the deterministic policy."
            )

        cleaned_by_source: dict[int, CleanedBlock] = {}
        for block in cleaned.blocks:
            source_ordinal = block.source_block_ordinal
            if source_ordinal >= len(loaded.blocks):
                raise DocumentValidationError(
                    "Cleaned block references a missing source block."
                )
            if source_ordinal in cleaned_by_source:
                raise DocumentValidationError(
                    "Multiple cleaned blocks reference one source block."
                )
            cleaned_by_source[source_ordinal] = block
        if set(cleaned_by_source) != set(range(len(loaded.blocks))):
            raise DocumentValidationError(
                "Cleaned blocks must preserve every loaded source block."
            )

        omissions_by_source: dict[int, list[DocumentTextSpan]] = defaultdict(list)
        for omission in cleaned.omissions:
            span = omission.source_span
            if span.block_ordinal >= len(loaded.blocks):
                raise DocumentValidationError(
                    "Cleaning omission references a missing source block."
                )
            source_block = loaded.blocks[span.block_ordinal]
            _validate_text_span_against_source(span, source_block)
            omissions_by_source[span.block_ordinal].append(span)

        retained_span_count = 0
        for source_block in loaded.blocks:
            cleaned_block = cleaned_by_source[source_block.ordinal]
            if isinstance(cleaned_block, CleanedTableBlock):
                if (
                    source_block.kind != "table"
                    or source_block.table is None
                    or cleaned_block.table != source_block.table
                    or cleaned_block.page_number != source_block.page_number
                    or omissions_by_source[source_block.ordinal]
                ):
                    raise DocumentValidationError(
                        "Cleaned table does not exactly preserve its source."
                    )
                continue
            if source_block.text is None or source_block.kind == "table":
                raise DocumentValidationError(
                    "Cleaned text block does not match its source variant."
                )
            if (
                cleaned_block.kind != source_block.kind
                or cleaned_block.page_number != source_block.page_number
                or cleaned_block.heading_level != source_block.heading_level
            ):
                raise DocumentValidationError(
                    "Cleaned text block changed source structure metadata."
                )
            cleaned_cursor = 0
            for span in cleaned_block.retained_spans:
                retained_span_count += 1
                if (
                    retained_span_count
                    > cleaned.limits.max_total_source_mappings
                ):
                    raise DocumentContentLimitError(
                        "Cleaning piece table exceeds the source-mapping limit."
                    )
                _validate_text_span_against_source(span, source_block)
                piece_length = span.end_code_point - span.start_code_point
                cleaned_end = cleaned_cursor + piece_length
                if cleaned_end > len(cleaned_block.text):
                    raise DocumentValidationError(
                        "Cleaned text is shorter than its retained source spans."
                    )
                for offset in range(piece_length):
                    if (
                        cleaned_block.text[cleaned_cursor + offset]
                        != source_block.text[span.start_code_point + offset]
                    ):
                        raise DocumentValidationError(
                            "Cleaned text cannot be rebuilt from retained source spans."
                        )
                cleaned_cursor = cleaned_end
            if cleaned_cursor != len(cleaned_block.text):
                raise DocumentValidationError(
                    "Cleaned text cannot be rebuilt from retained source spans."
                )
            _validate_text_partition(
                cleaned_block.retained_spans,
                tuple(omissions_by_source[source_block.ordinal]),
                len(source_block.text),
            )

    @classmethod
    def _validate_chunked_result(
        cls,
        scope: AttachmentScope,
        link_id: str,
        loaded: LoadedDocument,
        cleaned: CleanedDocument,
        chunked: object,
        *,
        chunker_id: object,
        chunker_version: object,
        policy: DocumentChunkingPolicy,
    ) -> ChunkedDocument:
        """Verify and isolate chunk lineage and mappings before publication."""

        if not isinstance(chunked, ChunkedDocument):
            raise DocumentValidationError(
                "Document chunker returned an invalid result."
            )
        chunked = _snapshot_chunked_document(
            chunked,
            resource_limits=cleaned.limits,
            resource_policy=policy,
        )
        if (
            chunked.cleaned_schema_version != cleaned.schema_version
            or chunked.provenance != cleaned.provenance
            or chunked.cleaner_id != cleaned.cleaner_id
            or chunked.cleaner_version != cleaned.cleaner_version
            or chunked.document_fingerprint != cleaned.document_fingerprint
            or chunked.cleaning_fingerprint != cleaned.cleaning_fingerprint
            or chunked.limits != cleaned.limits
            or chunked.title != cleaned.title
            or chunked.page_count != cleaned.page_count
        ):
            raise DocumentValidationError(
                "Document chunker changed cleaned-document lineage."
            )
        _validate_requested_owner(scope, link_id, chunked.provenance.source)
        if (
            chunked.chunker_id != chunker_id
            or chunked.chunker_version != chunker_version
        ):
            raise DocumentValidationError(
                "Document chunker returned inconsistent version metadata."
            )
        if chunked.policy != policy:
            raise DocumentValidationError(
                "Document chunker changed its active policy."
            )
        cls._validate_chunk_mappings(loaded, cleaned, chunked)
        return chunked

    @staticmethod
    def _validate_chunk_mappings(
        loaded: LoadedDocument,
        cleaned: CleanedDocument,
        chunked: ChunkedDocument,
    ) -> None:
        """Prove mapping bounds, output coverage, order, and table projection."""

        cleaned_by_source = {
            block.source_block_ordinal: block for block in cleaned.blocks
        }
        observed_text: dict[int, list[DocumentTextSpan]] = defaultdict(list)
        previous_source_ordinal = -1

        for chunk in chunked.chunks:
            previous_mapping = None
            table_ordinal: int | None = None
            for mapping in chunk.source_mappings:
                span = mapping.source_span
                if span.block_ordinal < previous_source_ordinal:
                    raise DocumentValidationError(
                        "Chunk mappings changed source document order."
                    )
                previous_source_ordinal = span.block_ordinal
                if previous_mapping is None:
                    if mapping.chunk_start_code_point != 0:
                        raise DocumentValidationError(
                            "A chunk has an unmapped leading range."
                        )
                else:
                    gap_start = previous_mapping.chunk_end_code_point
                    gap_end = mapping.chunk_start_code_point
                    previous_span = previous_mapping.source_span
                    if gap_end < gap_start:
                        raise DocumentValidationError(
                            "Chunk mappings overlap derived text."
                        )
                    if gap_end > gap_start and not (
                        chunk.kind == "prose"
                        and gap_end - gap_start
                        == len(chunked.policy.prose_separator)
                        and _text_range_equals(
                            chunk.text,
                            gap_start,
                            chunked.policy.prose_separator,
                            0,
                            gap_end - gap_start,
                        )
                        and previous_span.block_ordinal != span.block_ordinal
                    ):
                        raise DocumentValidationError(
                            "A chunk contains an unexplained mapping gap."
                        )

                source_block = _source_block(loaded, span.block_ordinal)
                cleaned_block = cleaned_by_source.get(span.block_ordinal)
                if isinstance(span, DocumentTextSpan):
                    if not isinstance(cleaned_block, CleanedTextBlock):
                        raise DocumentValidationError(
                            "Text mapping does not reference cleaned text."
                        )
                    _validate_text_span_against_source(span, source_block)
                    expected_kind = (
                        "code" if cleaned_block.kind == "code" else "prose"
                    )
                    if chunk.kind != expected_kind or source_block.text is None:
                        raise DocumentValidationError(
                            "Text chunk kind does not match its source block."
                        )
                    mapped_length = (
                        mapping.chunk_end_code_point
                        - mapping.chunk_start_code_point
                    )
                    if (
                        mapped_length
                        != span.end_code_point - span.start_code_point
                        or not _text_range_equals(
                            chunk.text,
                            mapping.chunk_start_code_point,
                            source_block.text,
                            span.start_code_point,
                            mapped_length,
                        )
                    ):
                        raise DocumentValidationError(
                            "Text mapping does not reproduce cleaned source text."
                        )
                    observed_text[span.block_ordinal].append(span)
                else:
                    if not isinstance(cleaned_block, CleanedTableBlock):
                        raise DocumentValidationError(
                            "Table mapping does not reference a cleaned table."
                        )
                    _validate_table_span(span, source_block)
                    if chunk.kind != "table":
                        raise DocumentValidationError(
                            "Table source mapping appears in a non-table chunk."
                        )
                    if table_ordinal is None:
                        table_ordinal = span.block_ordinal
                    elif table_ordinal != span.block_ordinal:
                        raise DocumentValidationError(
                            "One table chunk cannot combine separate tables."
                        )
                previous_mapping = mapping

            if (
                previous_mapping is None
                or previous_mapping.chunk_end_code_point != len(chunk.text)
            ):
                raise DocumentValidationError(
                    "A chunk has an unmapped trailing range."
                )

        for block in cleaned.blocks:
            if isinstance(block, CleanedTextBlock):
                _validate_retained_coverage(
                    block.retained_spans,
                    observed_text[block.source_block_ordinal],
                )
        _validate_table_projections(cleaned, chunked)


def _text_range_equals(
    left: str,
    left_start: int,
    right: str,
    right_start: int,
    length: int,
) -> bool:
    """Compare equal-length code-point ranges without allocating slices."""

    if (
        left_start < 0
        or right_start < 0
        or length < 0
        or left_start + length > len(left)
        or right_start + length > len(right)
    ):
        return False
    return all(
        left[left_start + offset] == right[right_start + offset]
        for offset in range(length)
    )


def _expected_cleaning_omissions(
    document: LoadedDocument,
    policy: DocumentCleaningPolicy,
) -> tuple[CleaningOmission, ...]:
    """Derive the unique omission set allowed by the conservative v1 policy."""

    if (
        document.document_format != "pdf"
        or document.page_count is None
        or document.page_count < policy.repeated_pdf_header_min_pages
    ):
        return ()
    blocks_by_page: dict[int, list[DocumentBlock]] = {
        page: [] for page in range(1, document.page_count + 1)
    }
    for block in document.blocks:
        if block.page_number is None:
            return ()
        blocks_by_page[block.page_number].append(block)
    if any(len(blocks) != 1 for blocks in blocks_by_page.values()):
        return ()

    candidate: tuple[str, int] | None = None
    omissions: list[CleaningOmission] = []
    for page in range(1, document.page_count + 1):
        block = blocks_by_page[page][0]
        if block.kind != "paragraph" or block.text is None:
            return ()
        first_line = _first_exact_line(block.text)
        if first_line is None:
            return ()
        header_end, retained_start = first_line
        if (
            header_end > policy.repeated_pdf_header_max_code_points
            or not _has_meaningful_range(block.text, 0, header_end)
            or not _has_meaningful_range(
                block.text,
                retained_start,
                len(block.text),
            )
        ):
            return ()
        if (
            document.title is not None
            and len(document.title.text) == header_end
            and _text_range_equals(
                block.text,
                0,
                document.title.text,
                0,
                header_end,
            )
        ):
            return ()
        if candidate is None:
            candidate = (block.text, header_end)
        elif (
            header_end != candidate[1]
            or not _text_range_equals(
                block.text,
                0,
                candidate[0],
                0,
                header_end,
            )
        ):
            return ()
        omissions.append(
            CleaningOmission(
                reason="repeated_pdf_page_header",
                source_span=DocumentTextSpan(
                    block_ordinal=block.ordinal,
                    start_code_point=0,
                    end_code_point=retained_start,
                    page_number=page,
                ),
            )
        )
    return tuple(omissions)


def _first_exact_line(text: str) -> tuple[int, int] | None:
    """Return first-line content and terminator ends without copying text."""

    for index, character in enumerate(text):
        if character == "\n":
            return index, index + 1
        if character == "\r":
            end = index + 2 if text[index:index + 2] == "\r\n" else index + 1
            return index, end
    return None


def _has_meaningful_range(value: str, start: int, end: int) -> bool:
    """Return whether one source range contains non-whitespace content."""

    return any(
        not (
            value[index].isspace()
            or value[index] == _ZERO_WIDTH_NO_BREAK_SPACE
        )
        for index in range(start, end)
    )


def _source_block(document: LoadedDocument, ordinal: int) -> DocumentBlock:
    """Resolve a source ordinal without accepting negative Python indexing."""

    if ordinal < 0 or ordinal >= len(document.blocks):
        raise DocumentValidationError(
            "Derived provenance references a missing source block."
        )
    return document.blocks[ordinal]


def _validate_text_span_against_source(
    span: DocumentTextSpan,
    source_block: DocumentBlock,
) -> None:
    """Require one text span to be in bounds and preserve source page identity."""

    if (
        source_block.text is None
        or span.block_ordinal != source_block.ordinal
        or span.page_number != source_block.page_number
        or span.end_code_point > len(source_block.text)
    ):
        raise DocumentValidationError(
            "Derived text span is outside its loaded source block."
        )


def _validate_table_span(
    span: DocumentTableCellSpan,
    source_block: DocumentBlock,
) -> None:
    """Require one table span to identify an existing ragged source cell."""

    table = source_block.table
    if (
        table is None
        or span.block_ordinal != source_block.ordinal
        or span.page_number != source_block.page_number
        or span.row_index >= len(table.rows)
        or span.column_index >= len(table.rows[span.row_index])
    ):
        raise DocumentValidationError(
            "Derived table span is outside its loaded source table."
        )
    cell = table.rows[span.row_index][span.column_index]
    if span.end_code_point > len(cell):
        raise DocumentValidationError(
            "Derived table span is outside its loaded source cell."
        )


def _validate_text_partition(
    retained: tuple[DocumentTextSpan, ...],
    omissions: tuple[DocumentTextSpan, ...],
    source_length: int,
) -> None:
    """Merge ordered retained and omitted ranges into one exact partition."""

    retained_index = 0
    omission_index = 0
    cursor = 0
    while retained_index < len(retained) or omission_index < len(omissions):
        retained_span = (
            retained[retained_index]
            if retained_index < len(retained)
            else None
        )
        omission_span = (
            omissions[omission_index]
            if omission_index < len(omissions)
            else None
        )
        if omission_span is None or (
            retained_span is not None
            and retained_span.start_code_point
            < omission_span.start_code_point
        ):
            if retained_span is None:
                raise DocumentValidationError(
                    "Cleaning spans do not cover the complete source text."
                )
            selected = retained_span
            retained_index += 1
        else:
            selected = omission_span
            omission_index += 1
        if (
            selected.start_code_point != cursor
            or selected.end_code_point <= selected.start_code_point
            or selected.end_code_point > source_length
        ):
            raise DocumentValidationError(
                "Cleaning spans must partition source text without gaps or overlap."
            )
        cursor = selected.end_code_point
    if cursor != source_length:
        raise DocumentValidationError(
            "Cleaning spans do not cover the complete source text."
        )


def _validate_retained_coverage(
    expected: tuple[DocumentTextSpan, ...],
    observed: list[DocumentTextSpan],
) -> None:
    """Require chunk mappings to cover every retained piece once and in order."""

    expected_index = 0
    cursor = expected[0].start_code_point if expected else 0
    for span in observed:
        if expected_index >= len(expected):
            raise DocumentValidationError(
                "Chunk mappings duplicate retained source text."
            )
        retained = expected[expected_index]
        if (
            span.block_ordinal != retained.block_ordinal
            or span.page_number != retained.page_number
            or span.start_code_point != cursor
            or span.end_code_point > retained.end_code_point
        ):
            raise DocumentValidationError(
                "Chunk mappings do not follow retained source text exactly."
            )
        cursor = span.end_code_point
        if cursor == retained.end_code_point:
            expected_index += 1
            if expected_index < len(expected):
                cursor = expected[expected_index].start_code_point
    if expected_index != len(expected):
        raise DocumentValidationError(
            "Chunk mappings omit retained source text."
        )


def _iter_table_mapping_pieces(
    chunked: ChunkedDocument,
) -> Iterator[tuple[DocumentTableCellSpan, str, int, int]]:
    """Yield table mappings in projection order without joining chunk text."""

    for chunk in chunked.chunks:
        if chunk.kind != "table":
            continue
        for mapping in chunk.source_mappings:
            span = mapping.source_span
            if not isinstance(span, DocumentTableCellSpan):
                raise DocumentValidationError(
                    "Table chunks require table-cell source mappings."
                )
            yield (
                span,
                chunk.text,
                mapping.chunk_start_code_point,
                mapping.chunk_end_code_point,
            )


def _table_cell_affixes(
    row_index: int,
    column_index: int,
    row_length: int,
) -> tuple[str, str]:
    """Return the constant-size prefix and suffix for one JSON-lines cell."""

    prefix = (
        ("[" if row_index == 0 else "\n[")
        if column_index == 0
        else ","
    )
    suffix = "]" if column_index == row_length - 1 else ""
    return prefix, suffix


def _escaped_projection_length(value: str) -> int:
    """Count escaped cell code points without constructing the projection."""

    return sum(len(_TABLE_ESCAPES.get(character, character)) for character in value)


def _iter_escaped_range(
    value: str,
    start: int,
    end: int,
) -> Iterator[str]:
    """Yield one raw range as canonical escaped projection code points."""

    for raw_index in range(start, end):
        for projected in _TABLE_ESCAPES.get(
            value[raw_index],
            value[raw_index],
        ):
            yield projected


def _iter_complete_cell_projection(
    prefix: str,
    cell: str,
    suffix: str,
) -> Iterator[str]:
    """Yield one complete cell projection with constant auxiliary memory."""

    yield from prefix
    yield '"'
    yield from _iter_escaped_range(cell, 0, len(cell))
    yield '"'
    yield from suffix


def _require_projection_exhausted(expected: Iterator[str]) -> None:
    """Reject a mapping group that leaves canonical projected text uncovered."""

    if next(expected, None) is not None:
        raise DocumentValidationError(
            "Table mapping split canonical cell provenance."
        )


def _validate_table_projections(
    cleaned: CleanedDocument,
    chunked: ChunkedDocument,
) -> None:
    """Stream canonical cells against mapped chunks with exact provenance."""

    pieces = _iter_table_mapping_pieces(chunked)
    current = next(pieces, None)
    for block in cleaned.blocks:
        if not isinstance(block, CleanedTableBlock):
            continue
        for row_index, row in enumerate(block.table.rows):
            for column_index, cell in enumerate(row):
                prefix, suffix = _table_cell_affixes(
                    row_index,
                    column_index,
                    len(row),
                )
                projection_length = (
                    len(prefix)
                    + 2
                    + _escaped_projection_length(cell)
                    + len(suffix)
                )
                projection_cursor = 0
                active_span: tuple[int, int] | None = None
                active_role: str | None = None
                active_expected: Iterator[str] | None = None
                active_source_end = 0
                phase = "begin"
                raw_cursor = 0
                while projection_cursor < projection_length:
                    if current is None:
                        raise DocumentValidationError(
                            "Table chunks omit the canonical projection."
                        )
                    span, chunk_text, chunk_start, chunk_end = current
                    if (
                        span.block_ordinal != block.source_block_ordinal
                        or span.row_index != row_index
                        or span.column_index != column_index
                    ):
                        raise DocumentValidationError(
                            "Table mapping changed canonical cell provenance."
                        )
                    piece_length = chunk_end - chunk_start
                    projection_end = projection_cursor + piece_length
                    if piece_length <= 0 or projection_end > projection_length:
                        raise DocumentValidationError(
                            "Table chunks do not reproduce the canonical projection."
                        )

                    span_key = (
                        span.start_code_point,
                        span.end_code_point,
                    )
                    if span_key != active_span:
                        if active_expected is not None:
                            _require_projection_exhausted(active_expected)
                            if active_role == "start":
                                phase = "payload"
                            elif active_role == "payload":
                                raw_cursor = active_source_end
                            elif active_role in {"end", "full", "empty"}:
                                phase = "done"
                        if (
                            phase == "begin"
                            and span_key == (0, len(cell))
                            and projection_cursor == 0
                            and projection_end == projection_length
                        ):
                            active_role = "full"
                            active_expected = _iter_complete_cell_projection(
                                prefix,
                                cell,
                                suffix,
                            )
                        elif len(cell) == 0 and phase == "begin" and span_key == (0, 0):
                            active_role = "empty"
                            active_expected = _iter_complete_cell_projection(
                                prefix,
                                cell,
                                suffix,
                            )
                        elif phase == "begin" and span_key == (0, 0):
                            active_role = "start"
                            active_expected = iter(prefix + '"')
                        elif (
                            phase == "payload"
                            and span.start_code_point == raw_cursor
                            and span.start_code_point < span.end_code_point
                        ):
                            active_role = "payload"
                            active_source_end = span.end_code_point
                            active_expected = _iter_escaped_range(
                                cell,
                                span.start_code_point,
                                span.end_code_point,
                            )
                        elif (
                            phase == "payload"
                            and raw_cursor == len(cell)
                            and span_key == (len(cell), len(cell))
                        ):
                            active_role = "end"
                            active_expected = iter('"' + suffix)
                        else:
                            raise DocumentValidationError(
                                "Table mapping changed canonical cell provenance."
                            )
                        active_span = span_key
                    if active_expected is None:
                        raise DocumentValidationError(
                            "Table mapping has no canonical cell projection."
                        )
                    for text_index in range(chunk_start, chunk_end):
                        expected_character = next(active_expected, None)
                        if (
                            expected_character is None
                            or chunk_text[text_index] != expected_character
                        ):
                            raise DocumentValidationError(
                                "Table chunks do not reproduce the canonical projection."
                            )
                    projection_cursor = projection_end
                    current = next(pieces, None)
                if active_expected is None:
                    raise DocumentValidationError(
                        "Table mapping does not cover canonical cell provenance."
                    )
                _require_projection_exhausted(active_expected)
                if active_role == "start":
                    phase = "payload"
                elif active_role == "payload":
                    raw_cursor = active_source_end
                elif active_role in {"end", "full", "empty"}:
                    phase = "done"
                if phase != "done":
                    raise DocumentValidationError(
                        "Table mapping does not cover canonical cell provenance."
                    )
                if current is not None:
                    next_span = current[0]
                    if (
                        next_span.block_ordinal == block.source_block_ordinal
                        and next_span.row_index == row_index
                        and next_span.column_index == column_index
                    ):
                        raise DocumentValidationError(
                            "Table mapping duplicates canonical cell provenance."
                        )
    if current is not None:
        raise DocumentValidationError(
            "Table chunks contain an unexpected canonical projection."
        )
