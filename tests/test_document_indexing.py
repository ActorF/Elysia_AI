"""Verify all-or-nothing document indexing and scoped rebuild orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event
from typing import cast

import pytest

from attachments.domain import AttachmentScope
from documents.chunking import (
    ChunkedDocument,
    StructureAwareDocumentChunker,
)
from documents.cleaning import ConservativeDocumentCleaner
from documents.domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    LoadedDocument,
)
from documents.embedding import (
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    DocumentEmbeddingService,
    EmbeddedDocument,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingUnavailableError,
    EmbeddingVector,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentNotFoundError,
    DocumentOperationCancelledError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)
from documents.indexing import (
    MAX_INDEX_REBUILD_CHUNKS,
    MAX_INDEX_REBUILD_DOCUMENTS,
    DocumentIndexingService,
)
from documents.vector_store import SQLiteVectorStore


_FILE_ID = "file_" + "7" * 64


def _chunked(
    scope: AttachmentScope,
    link_id: str,
    text: str = "Indexed text",
) -> ChunkedDocument:
    """Build one real validated chunk graph for an ownership link."""

    loaded = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=DocumentSource(
            scope=scope,
            link_id=link_id,
            file_id=_FILE_ID,
            file_name="index.txt",
            media_type="text/plain",
            size_bytes=len(text.encode("utf-8")),
        ),
        document_format="text",
        loader_id="index-test",
        loader_version="1.0.0",
        blocks=(DocumentBlock(0, "paragraph", text),),
        limits=DocumentLoadLimits(),
    )
    return StructureAwareDocumentChunker().chunk(
        ConservativeDocumentCleaner().clean(loaded)
    )


class _Processor:
    """Return mapped chunk graphs while recording scope-bound requests."""

    def __init__(
        self,
        results: Mapping[str, ChunkedDocument | BaseException],
    ) -> None:
        self.results = results
        self.calls: list[tuple[AttachmentScope, str]] = []

    def process(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> ChunkedDocument:
        """Return or raise the configured result for one ownership link."""

        self.calls.append((scope, link_id))
        result = self.results[link_id]
        if isinstance(result, BaseException):
            raise result
        return result


class _UnitTextEmbedder:
    """Return deterministic two-dimensional unit vectors without a runtime."""

    def __init__(self) -> None:
        self._identity = EmbeddingModelIdentity(
            provider="ollama",
            adapter_id="test-embedder",
            adapter_version="1.0.0",
            model_tag="fixture:1",
            model_digest="8" * 64,
            dimension=2,
            normalization="l2",
            document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
            query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
        )
        self._policy = EmbeddingBatchPolicy()

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the immutable fixture vector-space identity."""

        return self._identity

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the fixed production batch policy."""

        return self._policy

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return one ordered unit vector for each exact request item."""

        return EmbeddingBatch(
            schema_version=EMBEDDING_SCHEMA_VERSION,
            identity=self._identity,
            policy=self._policy,
            purpose=request.purpose,
            vectors=tuple(
                EmbeddingVector(item.item_id, (1.0, 0.0))
                for item in request.items
            ),
        )


class _UnavailableEmbedder:
    """Fail before persistence to exercise the rollback boundary."""

    def embed_document(self, document: ChunkedDocument) -> EmbeddedDocument:
        """Raise one stable local-runtime availability failure."""

        del document
        raise EmbeddingUnavailableError("Local embedding runtime unavailable.")


class _RecordingDocumentEmbedder:
    """Delegate valid embedding while recording completed model work."""

    def __init__(self) -> None:
        self.calls: list[ChunkedDocument] = []
        self._delegate = DocumentEmbeddingService(_UnitTextEmbedder())

    def embed_document(self, document: ChunkedDocument) -> EmbeddedDocument:
        """Record and embed one complete chunk graph."""

        self.calls.append(document)
        return self._delegate.embed_document(document)


class _RecordingStore:
    """Record index mutations without touching SQLite."""

    def __init__(self) -> None:
        self.replace_calls: list[tuple[AttachmentScope, EmbeddedDocument]] = []
        self.rebuild_calls: list[
            tuple[AttachmentScope, tuple[EmbeddedDocument, ...]]
        ] = []
        self.delete_calls: list[tuple[AttachmentScope, str]] = []

    def replace_document(
        self,
        scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        """Record one complete replacement request."""

        self.replace_calls.append((scope, document))

    def rebuild(
        self,
        scope: AttachmentScope,
        documents: tuple[EmbeddedDocument, ...],
    ) -> None:
        """Record one complete scoped rebuild request."""

        self.rebuild_calls.append((scope, documents))

    def delete_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Record one exact scoped deletion and report a removed row."""

        self.delete_calls.append((scope, link_id))
        return True


def _service(
    processor: _Processor,
    store: _RecordingStore,
) -> DocumentIndexingService:
    """Compose the indexing service with a deterministic in-memory embedder."""

    return DocumentIndexingService(
        processor,
        DocumentEmbeddingService(_UnitTextEmbedder()),
        store,
    )


def test_index_document_persists_only_a_complete_matching_generation() -> None:
    """Preserve exact owner and lineage through processing and persistence."""

    scope = AttachmentScope("chat", "chat_indexing")
    link_id = "attachment_indexing"
    source = _chunked(scope, link_id)
    processor = _Processor({link_id: source})
    store = _RecordingStore()

    result = _service(processor, store).index_document(scope, link_id)

    assert result.chunked_document == source
    assert result.source.scope == scope
    assert processor.calls == [(scope, link_id)]
    assert store.replace_calls == [(scope, result)]


def test_prepare_document_finishes_embedding_without_store_mutation() -> None:
    """Expose a cancellation boundary after inference but before persistence."""

    scope = AttachmentScope("project", "project_prepare")
    link_id = "attachment_prepare"
    source = _chunked(scope, link_id)
    processor = _Processor({link_id: source})
    store = _RecordingStore()
    service = _service(processor, store)

    result = service.prepare_document(scope, link_id)

    assert result.chunked_document == source
    assert processor.calls == [(scope, link_id)]
    assert store.replace_calls == []


def test_prepare_propagates_cancel_into_processing_before_embedding() -> None:
    """Stop after a processing boundary that raises the shared Event."""

    scope = AttachmentScope("project", "project_cancel_prepare")
    link_id = "attachment_cancel_prepare"
    source = _chunked(scope, link_id)
    cancelled = Event()

    class _CancellingProcessor:
        """Raise cancellation after proving the callback reached processing."""

        def process(
            self,
            requested_scope: AttachmentScope,
            requested_link_id: str,
            *,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> ChunkedDocument:
            """Set the Event and return the valid result for the outer gate."""

            assert requested_scope == scope
            assert requested_link_id == link_id
            assert cancel_requested is not None
            cancelled.set()
            return source

    class _NeverEmbedder:
        """Fail if indexing admits embedding after cancellation is visible."""

        def embed_document(
            self,
            document: ChunkedDocument,
            *,
            cancel_requested: Callable[[], bool] | None = None,
        ) -> EmbeddedDocument:
            """Reject an unexpected embedding call after cancellation."""

            del document, cancel_requested
            raise AssertionError("Embedding must not start after cancellation.")

    store = _RecordingStore()
    service = DocumentIndexingService(
        _CancellingProcessor(),
        _NeverEmbedder(),
        store,
    )

    with pytest.raises(DocumentOperationCancelledError, match="cancelled"):
        service.prepare_document(
            scope,
            link_id,
            cancel_requested=cancelled.is_set,
        )

    assert store.replace_calls == []


def test_commit_document_requires_exact_type_and_matching_scope() -> None:
    """Reject untrusted or cross-owner prepared values before store mutation."""

    scope = AttachmentScope("project", "project_commit")
    link_id = "attachment_commit"
    store = _RecordingStore()
    service = _service(_Processor({link_id: _chunked(scope, link_id)}), store)
    prepared = service.prepare_document(scope, link_id)

    with pytest.raises(DocumentValidationError, match="exact EmbeddedDocument"):
        service.commit_document(scope, cast(EmbeddedDocument, object()))
    with pytest.raises(DocumentValidationError, match="scope"):
        service.commit_document(
            AttachmentScope("project", "project_foreign"),
            prepared,
        )

    assert store.replace_calls == []

    service.commit_document(scope, prepared)

    assert store.replace_calls == [(scope, prepared)]


def test_index_document_delegates_prepare_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the convenience operation ordered through both public boundaries."""

    scope = AttachmentScope("chat", "chat_delegate")
    link_id = "attachment_delegate"
    prepared = DocumentEmbeddingService(_UnitTextEmbedder()).embed_document(
        _chunked(scope, link_id)
    )
    service = _service(_Processor({}), _RecordingStore())
    calls: list[tuple[str, object]] = []

    def _prepare(
        requested_scope: AttachmentScope,
        requested_link_id: str,
    ) -> EmbeddedDocument:
        calls.append(("prepare", (requested_scope, requested_link_id)))
        return prepared

    def _commit(
        requested_scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        calls.append(("commit", (requested_scope, document)))

    monkeypatch.setattr(service, "prepare_document", _prepare)
    monkeypatch.setattr(service, "commit_document", _commit)

    assert service.index_document(scope, link_id) is prepared
    assert calls == [
        ("prepare", (scope, link_id)),
        ("commit", (scope, prepared)),
    ]


def test_index_document_round_trips_through_the_real_sqlite_store(
    tmp_path: Path,
) -> None:
    """Prove the three public boundaries share one compatible domain contract."""

    scope = AttachmentScope("project", "project_sqlite")
    link_id = "attachment_sqlite"
    chunked = _chunked(scope, link_id)
    adapter = _UnitTextEmbedder()
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", adapter.identity)
    service = DocumentIndexingService(
        _Processor({link_id: chunked}),
        DocumentEmbeddingService(adapter),
        store,
    )

    result = service.index_document(scope, link_id)

    assert store.list_document(
        scope,
        link_id,
        expected_derivation_fingerprint=result.derivation_fingerprint,
        expected_identity=adapter.identity,
    ) == result.chunks


def test_index_document_rejects_processor_scope_or_link_substitution() -> None:
    """Do not let an injected processor index another owner's chunks."""

    scope = AttachmentScope("chat", "chat_requested")
    link_id = "attachment_requested"
    foreign = _chunked(
        AttachmentScope("project", "project_foreign"),
        "attachment_foreign",
    )
    store = _RecordingStore()

    with pytest.raises(DocumentValidationError, match="ownership"):
        _service(_Processor({link_id: foreign}), store).index_document(
            scope,
            link_id,
        )

    assert store.replace_calls == []


def test_embedding_failure_is_typed_and_never_mutates_the_store() -> None:
    """Keep the previous generation intact when local inference is unavailable."""

    scope = AttachmentScope("chat", "chat_unavailable")
    link_id = "attachment_unavailable"
    processor = _Processor({link_id: _chunked(scope, link_id)})
    store = _RecordingStore()
    service = DocumentIndexingService(processor, _UnavailableEmbedder(), store)

    with pytest.raises(EmbeddingUnavailableError, match="unavailable"):
        service.index_document(scope, link_id)

    assert store.replace_calls == []


def test_rebuild_prepares_every_document_before_one_store_mutation() -> None:
    """Abort a failed multi-document rebuild before deleting the old scope."""

    scope = AttachmentScope("project", "project_rebuild")
    first = "attachment_first"
    second = "attachment_second"
    processor = _Processor({
        first: _chunked(scope, first, "First"),
        second: DocumentNotFoundError("Source no longer exists."),
    })
    store = _RecordingStore()

    with pytest.raises(DocumentNotFoundError, match="no longer exists"):
        _service(processor, store).rebuild_scope(scope, (first, second))

    assert store.rebuild_calls == []


def test_rebuild_publishes_one_exact_scope_set_in_input_order() -> None:
    """Hand the store one bounded tuple only after all embeddings succeed."""

    scope = AttachmentScope("project", "project_complete")
    first = "attachment_first"
    second = "attachment_second"
    store = _RecordingStore()
    service = _service(
        _Processor({
            first: _chunked(scope, first, "First"),
            second: _chunked(scope, second, "Second"),
        }),
        store,
    )

    result = service.rebuild_scope(scope, (first, second))

    assert [item.source.link_id for item in result] == [first, second]
    assert store.rebuild_calls == [(scope, result)]


def test_rebuild_rejects_duplicates_and_unbounded_input_before_work() -> None:
    """Bound and de-duplicate rebuild authority before parsing any source."""

    scope = AttachmentScope("chat", "chat_limits")
    processor = _Processor({})
    store = _RecordingStore()
    service = _service(processor, store)

    with pytest.raises(DocumentValidationError, match="duplicate"):
        service.rebuild_scope(
            scope,
            ("attachment_duplicate", "attachment_duplicate"),
        )
    too_many = tuple(
        f"attachment_item_{index}"
        for index in range(MAX_INDEX_REBUILD_DOCUMENTS + 1)
    )
    with pytest.raises(DocumentContentLimitError, match="document-count limit"):
        service.rebuild_scope(scope, too_many)

    assert processor.calls == []
    assert store.rebuild_calls == []


def test_rebuild_bounds_aggregate_chunks_before_next_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop aggregate rebuild work before inference exceeds its fixed budget."""

    assert MAX_INDEX_REBUILD_CHUNKS == 100_000
    monkeypatch.setattr("documents.indexing.MAX_INDEX_REBUILD_CHUNKS", 1)
    scope = AttachmentScope("project", "project_aggregate_limit")
    first = "attachment_first"
    second = "attachment_second"
    processor = _Processor({
        first: _chunked(scope, first, "First"),
        second: _chunked(scope, second, "Second"),
    })
    embedder = _RecordingDocumentEmbedder()
    store = _RecordingStore()
    service = DocumentIndexingService(processor, embedder, store)

    with pytest.raises(DocumentContentLimitError, match="aggregate chunk"):
        service.rebuild_scope(scope, (first, second))

    assert len(embedder.calls) == 1
    assert store.rebuild_calls == []


def test_delete_document_preserves_the_explicit_scope_filter() -> None:
    """Pass an independently validated scope and opaque link to deletion."""

    scope = AttachmentScope("project", "project_delete")
    store = _RecordingStore()
    service = _service(_Processor({}), store)

    assert service.delete_document(scope, "attachment_delete") is True
    assert store.delete_calls == [(scope, "attachment_delete")]


def test_unexpected_adapter_failure_is_sanitized_without_a_store_write() -> None:
    """Hide implementation diagnostics while retaining the original cause."""

    scope = AttachmentScope("chat", "chat_failure")
    link_id = "attachment_failure"
    secret = r"C:\private\source.txt"
    processor = _Processor({link_id: RuntimeError(secret)})
    store = _RecordingStore()

    with pytest.raises(DocumentProcessingFailedError) as caught:
        _service(processor, store).index_document(scope, link_id)

    assert secret not in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert store.replace_calls == []


def test_unexpected_commit_failure_remains_sanitized_through_index_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the existing closed error contract after splitting commit."""

    scope = AttachmentScope("chat", "chat_commit_failure")
    link_id = "attachment_commit_failure"
    secret = r"C:\private\vector-store.sqlite3"
    store = _RecordingStore()
    service = _service(
        _Processor({link_id: _chunked(scope, link_id)}),
        store,
    )

    def _fail_commit(
        requested_scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        del requested_scope, document
        raise RuntimeError(secret)

    monkeypatch.setattr(store, "replace_document", _fail_commit)

    with pytest.raises(DocumentProcessingFailedError) as caught:
        service.index_document(scope, link_id)

    assert secret not in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert store.replace_calls == []
