"""Exercise real, public-domain PDF and DOCX files through knowledge indexing."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import hashlib
from pathlib import Path

import pytest

from attachments import AttachmentScope, AttachmentService, JsonAttachmentStore
from documents import (
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    DocumentEmbeddingService,
    DocumentIndexingService,
    DocumentLoaderService,
    DocumentProcessingService,
    DocumentRetriever,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingVector,
    ExpectedDocumentGeneration,
    LoadedDocument,
    RetrievalPolicy,
    SQLiteVectorStore,
)


_FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "documents"
_PDF_FIXTURE = _FIXTURE_DIRECTORY / "atlas-release-notes.pdf"
_DOCX_FIXTURE = _FIXTURE_DIRECTORY / "lumen-operations-handbook.docx"
_FIXTURE_DIGESTS = {
    _PDF_FIXTURE: (
        "e9cb58f6e27427d49d62202e160cd5f1a99b87d4bda56c9c978689a79f3f76f0"
    ),
    _DOCX_FIXTURE: (
        "5931007fd91ba91df65a8cff25d663f07e2d2c09099a8313475ad3c0ce007d40"
    ),
}


def _digest(label: str) -> str:
    """Return one deterministic lowercase SHA-256 fixture identity."""

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class _UnitTextEmbedder:
    """Map every admitted input into one deterministic local vector space."""

    def __init__(self) -> None:
        """Create stable metadata without starting Ollama or network I/O."""

        self._identity = EmbeddingModelIdentity(
            provider="local",
            adapter_id="real-document-test",
            adapter_version="1.0.0",
            model_tag="fixture:1",
            model_digest=_digest("real-document-embedding-model"),
            dimension=2,
            normalization="l2",
            document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
            query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
        )
        self._policy = EmbeddingBatchPolicy()

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the immutable identity shared by documents and queries."""

        return self._identity

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the bounded no-truncation policy used by this fake."""

        return self._policy

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return ordered unit vectors while preserving every request ID."""

        return EmbeddingBatch(
            schema_version=EMBEDDING_SCHEMA_VERSION,
            identity=self._identity,
            policy=self._policy,
            purpose=request.purpose,
            vectors=tuple(
                EmbeddingVector(item_id=item.item_id, values=(1.0, 0.0))
                for item in request.items
            ),
        )


def _document_text(document: LoadedDocument) -> str:
    """Flatten loaded prose and table cells for exact fixture assertions."""

    pieces: list[str] = []
    for block in document.blocks:
        if block.text is not None:
            pieces.append(block.text)
        if block.table is not None:
            pieces.extend(cell for row in block.table.rows for cell in row)
    return "\n".join(pieces)


def _assert_path_private_graph(value: object, forbidden_path: Path) -> None:
    """Reject native-path field names or values in one published graph."""

    forbidden = str(forbidden_path.resolve())
    assert forbidden not in repr(value)
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if is_dataclass(current) and not isinstance(current, type):
            for field in fields(current):
                assert "path" not in field.name.casefold()
                pending.append(getattr(current, field.name))
        elif isinstance(current, tuple):
            pending.extend(current)


@pytest.mark.parametrize(
    ("fixture", "document_format", "title", "expected_text"),
    (
        (
            _PDF_FIXTURE,
            "pdf",
            "Atlas Release Notes",
            "SILVER BLOOM",
        ),
        (
            _DOCX_FIXTURE,
            "docx",
            "Lumen Operations Handbook",
            "42.75",
        ),
    ),
)
def test_real_fixtures_load_and_process_without_native_paths(
    tmp_path: Path,
    fixture: Path,
    document_format: str,
    title: str,
    expected_text: str,
) -> None:
    """Load real containers and retain their content without path capability."""

    scope = AttachmentScope(kind="project", id="project_real_documents")
    store = JsonAttachmentStore(tmp_path / "attachments", 1_000_000)
    attachments = AttachmentService(store)
    try:
        state = attachments.stage_files(scope, (fixture,))
        catalog = attachments.snapshot_files(scope)
        assert len(catalog.ownerships) == 1
        link_id = catalog.ownerships[0].link_id
        loader = DocumentLoaderService(attachments)

        loaded = loader.load(scope, link_id)
        processed = DocumentProcessingService(loader).process(scope, link_id)

        assert loaded.document_format == document_format
        assert loaded.title is not None
        assert loaded.title.text == title
        assert expected_text in _document_text(loaded)
        assert any(expected_text in chunk.text for chunk in processed.chunks)
        if document_format == "pdf":
            assert loaded.page_count == 1
            assert all(block.page_number == 1 for block in loaded.blocks)
        else:
            assert any(
                block.table is not None
                and "42.75" in {
                    cell for row in block.table.rows for cell in row
                }
                for block in loaded.blocks
            )
        for published in (state, catalog, loaded, processed):
            _assert_path_private_graph(published, fixture)
    finally:
        store.close()


def test_real_fixture_bytes_remain_deterministic() -> None:
    """Pin exact shareable fixture bytes so changes require explicit review."""

    for fixture, expected_digest in _FIXTURE_DIGESTS.items():
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == expected_digest


def test_real_pdf_and_docx_are_retrievable_inside_one_project(
    tmp_path: Path,
) -> None:
    """Index both real formats and retrieve their facts from one Project."""

    scope = AttachmentScope(kind="project", id="project_real_retrieval")
    store = JsonAttachmentStore(tmp_path / "attachments", 1_000_000)
    attachments = AttachmentService(store)
    try:
        attachments.stage_files(scope, (_PDF_FIXTURE, _DOCX_FIXTURE))
        catalog = attachments.snapshot_files(scope)
        link_ids = tuple(item.link_id for item in catalog.ownerships)
        assert len(link_ids) == 2

        loader = DocumentLoaderService(attachments)
        processor = DocumentProcessingService(loader)
        adapter = _UnitTextEmbedder()
        embeddings = DocumentEmbeddingService(adapter)
        vector_store = SQLiteVectorStore(
            tmp_path / "knowledge.sqlite3",
            adapter.identity,
        )
        indexing = DocumentIndexingService(
            processor,
            embeddings,
            vector_store,
        )
        indexed = indexing.rebuild_scope(scope, link_ids)
        generations = tuple(
            ExpectedDocumentGeneration(
                source=document.source,
                derivation_fingerprint=document.derivation_fingerprint,
            )
            for document in indexed
        )

        result = DocumentRetriever(embeddings, vector_store).retrieve(
            scope,
            "What are the Atlas code word and Lumen calibration value?",
            generations,
            policy=RetrievalPolicy(
                top_k=10,
                candidate_k=10,
                minimum_cosine_similarity=0.0,
            ),
        )

        combined = "\n".join(hit.text for hit in result.hits)
        assert "SILVER BLOOM" in combined
        assert "42.75" in combined
        assert {
            evidence.source.file_name
            for hit in result.hits
            for evidence in hit.evidence
        } == {_PDF_FIXTURE.name, _DOCX_FIXTURE.name}
        assert all(
            evidence.source.scope == scope
            for hit in result.hits
            for evidence in hit.evidence
        )
        for fixture in (_PDF_FIXTURE, _DOCX_FIXTURE):
            _assert_path_private_graph(result, fixture)
    finally:
        store.close()
