"""Verify deterministic, grounded document retrieval and closed reranking.

These tests exercise the public retrieval service against the real SQLite
vector store.  A small deterministic embedding adapter keeps cosine geometry
auditable while the production cleaning, chunking, embedding, persistence,
filtering, deduplication, and provenance paths remain in use.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
import hashlib
from pathlib import Path
import sqlite3
import traceback
from typing import Literal

import pytest

from attachments.domain import AttachmentScope
from documents.chunking import (
    DocumentChunkingPolicy,
    StructureAwareDocumentChunker,
)
from documents.cleaning import ConservativeDocumentCleaner
from documents.domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentBlockKind,
    DocumentLoadLimits,
    DocumentSource,
    LoadedDocument,
)
from documents.embedding import (
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    MAX_EMBEDDING_DIMENSION,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    DocumentEmbeddingService,
    EmbeddedDocument,
    EmbeddedQuery,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingVector,
)
from documents.retrieval import (
    RERANKER_SCHEMA_VERSION,
    DocumentRetriever,
    ExpectedDocumentGeneration,
    RerankerBatch,
    RerankerFailedError,
    RerankerIdentity,
    RerankerPolicy,
    RerankerRequest,
    RerankerScore,
    RetrievalFailedError,
    RetrievalLimitError,
    RetrievalLimits,
    RetrievalMetadataFilter,
    RetrievalPolicy,
    RetrievalValidationError,
    StoredRetrievalCandidate,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentCorruptError,
    DocumentValidationError,
)
from documents.vector_store import SQLiteVectorStore


UnitVector = tuple[float, float]


class _StrAlias(str):
    """Represent a hostile string subclass crossing a public value seam."""


class _ControlledEmbeddingAdapter:
    """Return caller-selected two-dimensional unit vectors."""

    def __init__(
        self,
        identity: EmbeddingModelIdentity,
        document_vectors: dict[str, UnitVector],
        *,
        query_vector: UnitVector = (1.0, 0.0),
    ) -> None:
        """Store immutable model metadata and deterministic test geometry."""

        self._identity = identity
        self._policy = EmbeddingBatchPolicy()
        self._document_vectors = dict(document_vectors)
        self._query_vector = query_vector
        self.requests: list[EmbeddingRequest] = []

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the fixed embedding-space identity."""

        return self._identity

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the production v1 embedding batch policy."""

        return self._policy

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Embed each item without changing order or item identity."""

        self.requests.append(request)
        vectors = tuple(
            EmbeddingVector(
                item_id=item.item_id,
                values=(
                    self._query_vector
                    if request.purpose == "query"
                    else self._document_vectors[item.text]
                ),
            )
            for item in request.items
        )
        return EmbeddingBatch(
            schema_version=EMBEDDING_SCHEMA_VERSION,
            identity=self.identity,
            policy=self.policy,
            purpose=request.purpose,
            vectors=vectors,
        )


class _NeverCalledEmbedder:
    """Record accidental query embedding on a provably empty corpus."""

    def __init__(self) -> None:
        """Initialize the observable call count."""

        self.calls = 0

    def embed_query(self, text: str) -> EmbeddedQuery:
        """Fail if empty-corpus retrieval invokes the dependency."""

        self.calls += 1
        raise AssertionError(f"Unexpected query embedding: {text}")


class _NeverCalledStore:
    """Record accidental storage access on a provably empty corpus."""

    def __init__(self) -> None:
        """Initialize the observable call count."""

        self.calls = 0

    def search_scope(
        self,
        *,
        scope: AttachmentScope,
        query: EmbeddedQuery,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        metadata_filter: RetrievalMetadataFilter,
        policy: RetrievalPolicy,
        limits: RetrievalLimits,
    ) -> tuple[StoredRetrievalCandidate, ...]:
        """Fail if empty-corpus retrieval invokes the dependency."""

        del (
            scope,
            query,
            expected_documents,
            metadata_filter,
            policy,
            limits,
        )
        self.calls += 1
        raise AssertionError("Unexpected vector search")


class _ExplodingQueryEmbedder:
    """Raise an unexpected exception containing private diagnostic text."""

    def embed_query(self, text: str) -> EmbeddedQuery:
        """Simulate an untyped adapter failure without echoing the query."""

        del text
        raise RuntimeError("private-embedder-diagnostic-7d8535")


class _ReturningQueryEmbedder:
    """Return one prebuilt query object, including deliberately mutated ones."""

    def __init__(self, query: EmbeddedQuery) -> None:
        """Retain the exact query object selected by the test."""

        self._query = query

    def embed_query(self, text: str) -> EmbeddedQuery:
        """Return the retained object without interpreting caller text."""

        del text
        return self._query


class _ExplodingStore:
    """Raise an unexpected exception containing private diagnostic text."""

    def search_scope(
        self,
        *,
        scope: AttachmentScope,
        query: EmbeddedQuery,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        metadata_filter: RetrievalMetadataFilter,
        policy: RetrievalPolicy,
        limits: RetrievalLimits,
    ) -> tuple[StoredRetrievalCandidate, ...]:
        """Simulate an untyped persistence failure after discarding inputs."""

        del scope, query, expected_documents, metadata_filter, policy, limits
        raise RuntimeError("private-store-diagnostic-52f961")


class _ReturningCandidateStore:
    """Return one prebuilt candidate from a controllable store boundary."""

    def __init__(self, candidate: StoredRetrievalCandidate) -> None:
        """Retain the candidate selected by the test."""

        self._candidate = candidate

    def search_scope(
        self,
        *,
        scope: AttachmentScope,
        query: EmbeddedQuery,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        metadata_filter: RetrievalMetadataFilter,
        policy: RetrievalPolicy,
        limits: RetrievalLimits,
    ) -> tuple[StoredRetrievalCandidate, ...]:
        """Return the retained candidate after accepting the full contract."""

        del scope, query, expected_documents, metadata_filter, policy, limits
        return (self._candidate,)


class _FakeReranker:
    """Return controlled complete, partial, or failed reranker responses."""

    def __init__(
        self,
        mode: Literal[
            "reorder",
            "partial",
            "raise",
            "mutate-scores",
            "mutate-inputs",
            "mutate-request-schema",
            "replace-request-policy",
            "mutate-request-policy-object",
            "identity-score-alias",
            "identity-score-deleted",
        ],
    ) -> None:
        """Select one deterministic adapter behavior for a test."""

        self._mode = mode
        self._identity = RerankerIdentity(
            provider="fixture",
            adapter_id="fixture-reranker",
            adapter_version="1.0.0",
            model_tag="fixture-reranker:1",
            model_digest="c" * 64,
        )
        self._policy = RerankerPolicy()
        self.requests: list[RerankerRequest] = []

    @property
    def identity(self) -> RerankerIdentity:
        """Return the fixed reranker identity."""

        return self._identity

    @property
    def policy(self) -> RerankerPolicy:
        """Return the closed v1 reranker policy."""

        return self._policy

    def rerank(self, request: RerankerRequest) -> RerankerBatch:
        """Reorder all inputs or deliberately violate the closed boundary."""

        self.requests.append(request)
        if self._mode == "raise":
            raise RuntimeError("secret query and passage from adapter")
        original_inputs = request.inputs
        scores = tuple(
            RerankerScore(
                item_id=item.item_id,
                relevance=1.0 if item.text == "Lower cosine" else 0.1,
            )
            for item in original_inputs
        )
        if self._mode == "partial":
            scores = scores[:1]
        batch = RerankerBatch(
            schema_version=RERANKER_SCHEMA_VERSION,
            identity=self.identity,
            policy=self.policy,
            request_fingerprint=request.request_fingerprint,
            scores=scores,
        )
        # Frozen dataclasses are not a security boundary because untrusted
        # adapters can bypass them with object.__setattr__.  These modes prove
        # the service validates the post-call graph rather than trusting the
        # constructor that originally accepted it.
        if self._mode == "mutate-scores":
            object.__setattr__(batch, "scores", scores + (scores[0],))
        if self._mode == "mutate-inputs":
            object.__setattr__(
                request,
                "inputs",
                original_inputs + (original_inputs[0],),
            )
        if self._mode == "mutate-request-schema":
            object.__setattr__(request, "schema_version", 2)
        if self._mode == "replace-request-policy":
            replacement_policy = RerankerPolicy()
            object.__setattr__(replacement_policy, "max_inputs", 63)
            object.__setattr__(request, "policy", replacement_policy)
        if self._mode == "mutate-request-policy-object":
            object.__setattr__(request.policy, "max_inputs", 63)
        if self._mode == "identity-score-alias":
            object.__setattr__(
                self._identity,
                "score_semantics",
                _StrAlias("relevance-0-to-1"),
            )
        if self._mode == "identity-score-deleted":
            object.__delattr__(self._identity, "score_semantics")
        return batch


def _identity(*, digest_character: str = "a") -> EmbeddingModelIdentity:
    """Build one complete two-dimensional embedding-space identity."""

    return EmbeddingModelIdentity(
        provider="fixture",
        adapter_id="fixture-embedding",
        adapter_version="1.0.0",
        model_tag="fixture-embedding:1",
        model_digest=digest_character * 64,
        dimension=2,
        normalization="l2",
        document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
        query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
    )


def _scope(suffix: str) -> AttachmentScope:
    """Build one valid Chat attachment scope."""

    return AttachmentScope(kind="chat", id=f"chat_{suffix}")


def _embedded_document(
    service: DocumentEmbeddingService,
    scope: AttachmentScope,
    *,
    link_id: str,
    text: str,
    block_kind: DocumentBlockKind = "code",
    page_number: int | None = None,
    media_type: str = "text/plain",
) -> EmbeddedDocument:
    """Run one source through real cleaning, chunking, and embedding."""

    file_digest = hashlib.sha256(
        f"{scope.kind}|{scope.id}|{link_id}|{text}".encode("utf-8")
    ).hexdigest()
    source = DocumentSource(
        scope=scope,
        link_id=link_id,
        file_id=f"file_{file_digest}",
        file_name=f"{link_id}.txt",
        media_type=media_type,
        size_bytes=max(1, len(text.encode("utf-8"))),
    )
    loaded = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=source,
        document_format="pdf" if page_number is not None else "text",
        loader_id="retrieval-test-loader",
        loader_version="1.0.0",
        blocks=(
            DocumentBlock(
                ordinal=0,
                kind=block_kind,
                text=text,
                page_number=page_number,
            ),
        ),
        limits=DocumentLoadLimits(),
        page_count=page_number,
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    chunked = StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=2_000),
    ).chunk(cleaned)
    return service.embed_document(chunked)


def _expected(document: EmbeddedDocument) -> ExpectedDocumentGeneration:
    """Bind one fixture source to its exact persisted derivation."""

    return ExpectedDocumentGeneration(
        source=document.source,
        derivation_fingerprint=document.derivation_fingerprint,
    )


def _all_dataclass_field_names(value: object) -> set[str]:
    """Collect field names recursively without inspecting property code."""

    if is_dataclass(value) and not isinstance(value, type):
        names = {field.name for field in fields(value)}
        for field in fields(value):
            names.update(_all_dataclass_field_names(getattr(value, field.name)))
        return names
    if isinstance(value, tuple):
        tuple_names: set[str] = set()
        for item in value:
            tuple_names.update(_all_dataclass_field_names(item))
        return tuple_names
    return set()


def _assert_sanitized_failure(
    error: BaseException,
    *,
    secrets: tuple[str, ...],
) -> None:
    """Require exception chaining and rendered diagnostics to hide secrets."""

    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error))
    for secret in secrets:
        assert secret not in rendered


def test_real_sqlite_retrieval_applies_cosine_top_k_and_threshold(
    tmp_path: Path,
) -> None:
    """Rank real stored vectors, bound Top-K, and reject weak evidence."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {
            "Best evidence": (1.0, 0.0),
            "Second evidence": (0.8, 0.6),
            "Unrelated evidence": (0.0, 1.0),
        },
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("ranking")
    documents = tuple(
        _embedded_document(
            service,
            scope,
            link_id=link_id,
            text=text,
        )
        for link_id, text in (
            ("attachment_best", "Best evidence"),
            ("attachment_second", "Second evidence"),
            ("attachment_unrelated", "Unrelated evidence"),
        )
    )
    store = SQLiteVectorStore(tmp_path / "ranking.sqlite3", identity)
    for document in documents:
        store.replace_document(scope, document)
    retriever = DocumentRetriever(service, store)

    result = retriever.retrieve(
        scope,
        "Which passage is relevant?",
        tuple(_expected(document) for document in documents),
        policy=RetrievalPolicy(
            top_k=1,
            candidate_k=2,
            minimum_cosine_similarity=0.5,
        ),
    )

    assert tuple(hit.text for hit in result.hits) == ("Best evidence",)
    assert result.candidate_count == 2
    assert result.hits[0].cosine_similarity == pytest.approx(1.0)

    strict = retriever.retrieve(
        scope,
        "Which passage is relevant?",
        tuple(_expected(document) for document in documents),
        policy=RetrievalPolicy(
            top_k=2,
            candidate_k=3,
            minimum_cosine_similarity=0.9,
        ),
    )
    assert tuple(hit.text for hit in strict.hits) == ("Best evidence",)
    assert strict.candidate_count == 1


def test_retrieval_enforces_exact_scope_and_explicit_document_allowlist(
    tmp_path: Path,
) -> None:
    """Never search an omitted document or accept a cross-scope generation."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {
            "Allowed": (1.0, 0.0),
            "Omitted": (1.0, 0.0),
            "Other scope": (1.0, 0.0),
        },
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("allowlist")
    other_scope = _scope("foreign")
    allowed = _embedded_document(
        service,
        scope,
        link_id="attachment_allowed",
        text="Allowed",
    )
    omitted = _embedded_document(
        service,
        scope,
        link_id="attachment_omitted",
        text="Omitted",
    )
    foreign = _embedded_document(
        service,
        other_scope,
        link_id="attachment_foreign",
        text="Other scope",
    )
    store = SQLiteVectorStore(tmp_path / "allowlist.sqlite3", identity)
    for document in (allowed, omitted, foreign):
        store.replace_document(document.source.scope, document)
    retriever = DocumentRetriever(service, store)

    result = retriever.retrieve(
        scope,
        "allowed",
        (_expected(allowed),),
        policy=RetrievalPolicy(
            top_k=5,
            candidate_k=5,
            minimum_cosine_similarity=0.0,
        ),
    )
    assert tuple(hit.text for hit in result.hits) == ("Allowed",)

    query_call_count = len(adapter.requests)
    with pytest.raises(RetrievalValidationError):
        retriever.retrieve(scope, "foreign", (_expected(foreign),))
    assert len(adapter.requests) == query_call_count


def test_selected_row_validation_ignores_omitted_candidate_data(
    tmp_path: Path,
) -> None:
    """Fail on selected corruption without letting omitted data affect results."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"Selected": (1.0, 0.0), "Omitted": (1.0, 0.0)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("selected-corruption")
    selected = _embedded_document(
        service,
        scope,
        link_id="attachment_selected",
        text="Selected",
    )
    omitted = _embedded_document(
        service,
        scope,
        link_id="attachment_omitted",
        text="Omitted",
    )
    database = tmp_path / "selected-corruption.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, selected)
    store.replace_document(scope, omitted)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE vector_records SET vector_checksum = ? WHERE link_id = ?",
            ("f" * 64, omitted.source.link_id),
        )
        connection.commit()
    finally:
        connection.close()

    retriever = DocumentRetriever(service, store)
    result = retriever.retrieve(
        scope,
        "selected",
        (_expected(selected),),
    )
    assert tuple(hit.text for hit in result.hits) == ("Selected",)

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE vector_records SET vector_checksum = ? WHERE link_id = ?",
            ("e" * 64, selected.source.link_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RetrievalFailedError) as captured:
        retriever.retrieve(scope, "selected", (_expected(selected),))
    assert "Selected" not in str(captured.value)


@pytest.mark.parametrize("column", ["chunk_json", "vector_blob"])
def test_record_storage_class_fails_before_full_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    column: Literal["chunk_json", "vector_blob"],
) -> None:
    """Reject a noncanonical JSON/BLOB class in the size-only SQL pass."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    )
    scope = _scope(f"record-class-{column}")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
    )
    database = tmp_path / f"record-class-{column}.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        corrupt_value: object = (
            sqlite3.Binary(b"{}")
            if column == "chunk_json"
            else "\U0001f600" * (identity.dimension * 4)
        )
        # ``column`` is a closed test parameter, never external SQL input.
        connection.execute(
            f"UPDATE vector_records SET {column} = ?",
            (corrupt_value,),
        )
        connection.commit()
    finally:
        connection.close()

    def _unexpected_full_projection(*args: object, **kwargs: object) -> object:
        """Fail if malformed variable payload reaches the full-row reader."""

        del args, kwargs
        raise AssertionError("Full record payload must not be projected")

    monkeypatch.setattr(
        SQLiteVectorStore,
        "_stored_record_sizes",
        _unexpected_full_projection,
    )
    with pytest.raises(DocumentCorruptError, match="record size is invalid"):
        store.search_scope(
            scope=scope,
            query=service.embed_query("evidence"),
            expected_documents=(_expected(document),),
            metadata_filter=RetrievalMetadataFilter(),
            policy=RetrievalPolicy(),
            limits=RetrievalLimits(),
        )


def test_retrieval_applies_closed_metadata_filters_in_store_and_service(
    tmp_path: Path,
) -> None:
    """Match exact file, media, chunk-kind, and page metadata together."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {
            "Code page": (1.0, 0.0),
            "Prose page": (0.8, 0.6),
        },
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("filters")
    code = _embedded_document(
        service,
        scope,
        link_id="attachment_code",
        text="Code page",
        block_kind="code",
    )
    prose = _embedded_document(
        service,
        scope,
        link_id="attachment_prose",
        text="Prose page",
        block_kind="paragraph",
        page_number=2,
        media_type="application/pdf",
    )
    store = SQLiteVectorStore(tmp_path / "filters.sqlite3", identity)
    store.replace_document(scope, code)
    store.replace_document(scope, prose)

    result = DocumentRetriever(service, store).retrieve(
        scope,
        "find prose",
        (_expected(code), _expected(prose)),
        metadata_filter=RetrievalMetadataFilter(
            file_ids=(prose.source.file_id,),
            media_types=("application/pdf",),
            chunk_kinds=("prose",),
            page_numbers=(2,),
        ),
        policy=RetrievalPolicy(
            top_k=2,
            candidate_k=2,
            minimum_cosine_similarity=0.0,
        ),
    )

    assert tuple(hit.text for hit in result.hits) == ("Prose page",)
    assert result.hits[0].evidence[0].page_number == 2
    assert result.metadata_filter.file_ids == (prose.source.file_id,)


@pytest.mark.parametrize("failure", ["missing", "stale"])
def test_missing_or_stale_generation_fails_the_complete_search(
    tmp_path: Path,
    failure: Literal["missing", "stale"],
) -> None:
    """Refuse partial retrieval when any authorized generation is unavailable."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"Present": (1.0, 0.0), "Absent": (0.8, 0.6)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope(f"generation-{failure}")
    present = _embedded_document(
        service,
        scope,
        link_id="attachment_present",
        text="Present",
    )
    absent = _embedded_document(
        service,
        scope,
        link_id="attachment_absent",
        text="Absent",
    )
    store = SQLiteVectorStore(tmp_path / f"{failure}.sqlite3", identity)
    store.replace_document(scope, present)
    second = (
        _expected(absent)
        if failure == "missing"
        else replace(
            _expected(present),
            derivation_fingerprint="f" * 64,
        )
    )
    expected = (
        (_expected(present), second)
        if failure == "missing"
        else (second,)
    )

    with pytest.raises(RetrievalFailedError) as captured:
        DocumentRetriever(service, store).retrieve(
            scope,
            "find present",
            expected,
        )
    assert "Present" not in str(captured.value)


def test_scan_budget_fails_instead_of_returning_a_partial_top_k(
    tmp_path: Path,
) -> None:
    """Treat an incomplete exact scan as a limit error, never as retrieval."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"First": (1.0, 0.0), "Second": (0.8, 0.6)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("scan-limit")
    documents = (
        _embedded_document(
            service,
            scope,
            link_id="attachment_first",
            text="First",
        ),
        _embedded_document(
            service,
            scope,
            link_id="attachment_second",
            text="Second",
        ),
    )
    store = SQLiteVectorStore(tmp_path / "scan-limit.sqlite3", identity)
    for document in documents:
        store.replace_document(scope, document)

    with pytest.raises(RetrievalLimitError, match="resource limit"):
        DocumentRetriever(service, store).retrieve(
            scope,
            "both",
            tuple(_expected(document) for document in documents),
            policy=RetrievalPolicy(
                top_k=1,
                candidate_k=1,
                minimum_cosine_similarity=0.0,
            ),
            limits=RetrievalLimits(max_scanned_records=1),
        )


def test_exact_kind_and_text_deduplication_preserves_every_evidence_source(
    tmp_path: Path,
) -> None:
    """Merge exact duplicate passages while keeping distinct-kind evidence."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"Shared evidence": (1.0, 0.0)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("dedupe")
    documents = (
        _embedded_document(
            service,
            scope,
            link_id="attachment_code_a",
            text="Shared evidence",
            block_kind="code",
        ),
        _embedded_document(
            service,
            scope,
            link_id="attachment_code_b",
            text="Shared evidence",
            block_kind="code",
        ),
        _embedded_document(
            service,
            scope,
            link_id="attachment_prose",
            text="Shared evidence",
            block_kind="paragraph",
        ),
    )
    store = SQLiteVectorStore(tmp_path / "dedupe.sqlite3", identity)
    for document in documents:
        store.replace_document(scope, document)

    result = DocumentRetriever(service, store).retrieve(
        scope,
        "shared",
        tuple(_expected(document) for document in reversed(documents)),
        policy=RetrievalPolicy(
            top_k=3,
            candidate_k=3,
            minimum_cosine_similarity=0.0,
        ),
    )

    assert result.candidate_count == 2
    code_hit = next(hit for hit in result.hits if hit.kind == "code")
    prose_hit = next(hit for hit in result.hits if hit.kind == "prose")
    assert tuple(
        evidence.source.link_id for evidence in code_hit.evidence
    ) == ("attachment_code_a", "attachment_code_b")
    assert len(prose_hit.evidence) == 1


def test_equal_cosine_ties_are_stable_across_caller_document_order(
    tmp_path: Path,
) -> None:
    """Break equal scores by stable persisted identities rather than input order."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"First by link": (1.0, 0.0), "Second by link": (1.0, 0.0)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("ties")
    first = _embedded_document(
        service,
        scope,
        link_id="attachment_a",
        text="First by link",
    )
    second = _embedded_document(
        service,
        scope,
        link_id="attachment_b",
        text="Second by link",
    )
    store = SQLiteVectorStore(tmp_path / "ties.sqlite3", identity)
    store.replace_document(scope, second)
    store.replace_document(scope, first)
    retriever = DocumentRetriever(service, store)
    policy = RetrievalPolicy(
        top_k=2,
        candidate_k=2,
        minimum_cosine_similarity=0.0,
    )

    forward = retriever.retrieve(
        scope,
        "tie",
        (_expected(first), _expected(second)),
        policy=policy,
    )
    reverse = retriever.retrieve(
        scope,
        "tie",
        (_expected(second), _expected(first)),
        policy=policy,
    )

    assert forward.hits == reverse.hits
    assert tuple(hit.text for hit in forward.hits) == (
        "First by link",
        "Second by link",
    )


def test_empty_corpus_returns_without_calling_external_dependencies() -> None:
    """Return truthful emptiness before embedding, storage, or reranking."""

    embedder = _NeverCalledEmbedder()
    store = _NeverCalledStore()
    reranker = _FakeReranker("raise")

    result = DocumentRetriever(embedder, store, reranker).retrieve(
        _scope("empty"),
        "valid query",
        (),
    )

    assert result.hits == ()
    assert result.candidate_count == 0
    assert result.embedding_identity is None
    assert result.reranker_identity is None
    assert embedder.calls == 0
    assert store.calls == 0
    assert reranker.requests == []


def test_insufficient_similarity_returns_empty_without_reranking(
    tmp_path: Path,
) -> None:
    """Publish no nearest-neighbor fallback when every passage is irrelevant."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"Orthogonal evidence": (0.0, 1.0)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("insufficient")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_orthogonal",
        text="Orthogonal evidence",
    )
    store = SQLiteVectorStore(tmp_path / "insufficient.sqlite3", identity)
    store.replace_document(scope, document)
    reranker = _FakeReranker("raise")

    result = DocumentRetriever(service, store, reranker).retrieve(
        scope,
        "unrelated query",
        (_expected(document),),
        policy=RetrievalPolicy(
            top_k=1,
            candidate_k=1,
            minimum_cosine_similarity=0.35,
        ),
    )

    assert result.hits == ()
    assert result.candidate_count == 0
    assert result.embedding_identity == identity
    assert result.reranker_identity is None
    assert reranker.requests == []


def test_optional_reranker_reorders_only_the_closed_candidate_pool(
    tmp_path: Path,
) -> None:
    """Let a complete reranker permutation override cosine ordering."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"Higher cosine": (1.0, 0.0), "Lower cosine": (0.8, 0.6)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope("rerank")
    documents = (
        _embedded_document(
            service,
            scope,
            link_id="attachment_high",
            text="Higher cosine",
        ),
        _embedded_document(
            service,
            scope,
            link_id="attachment_low",
            text="Lower cosine",
        ),
    )
    store = SQLiteVectorStore(tmp_path / "rerank.sqlite3", identity)
    for document in documents:
        store.replace_document(scope, document)
    reranker = _FakeReranker("reorder")

    result = DocumentRetriever(service, store, reranker).retrieve(
        scope,
        "rerank these",
        tuple(_expected(document) for document in documents),
        policy=RetrievalPolicy(
            top_k=2,
            candidate_k=2,
            minimum_cosine_similarity=0.0,
        ),
    )

    assert tuple(hit.text for hit in result.hits) == (
        "Lower cosine",
        "Higher cosine",
    )
    assert tuple(hit.reranker_score for hit in result.hits) == (1.0, 0.1)
    assert result.reranker_identity == reranker.identity
    assert tuple(item.text for item in reranker.requests[0].inputs) == (
        "Higher cosine",
        "Lower cosine",
    )

    with pytest.raises(RetrievalValidationError, match="changed its scope"):
        replace(result, scope=_scope("other-result-scope"))
    with pytest.raises(RetrievalValidationError, match="outside its filter"):
        replace(
            result,
            metadata_filter=RetrievalMetadataFilter(chunk_kinds=("prose",)),
        )
    with pytest.raises(RetrievalValidationError, match="every hit"):
        replace(
            result,
            hits=(replace(result.hits[0], reranker_score=None), result.hits[1]),
        )
    with pytest.raises(RetrievalValidationError, match="rank order"):
        replace(result, hits=tuple(reversed(result.hits)))
    with pytest.raises(RetrievalValidationError, match="reranker scores"):
        replace(result, reranker_identity=None)


@pytest.mark.parametrize("mode", ["partial", "raise"])
def test_malformed_or_failed_reranker_fails_closed_and_sanitized(
    tmp_path: Path,
    mode: Literal["partial", "raise"],
) -> None:
    """Publish no cosine fallback when a configured reranker is unsafe."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(
        identity,
        {"Higher cosine": (1.0, 0.0), "Lower cosine": (0.8, 0.6)},
    )
    service = DocumentEmbeddingService(adapter)
    scope = _scope(f"reranker-{mode}")
    documents = (
        _embedded_document(
            service,
            scope,
            link_id="attachment_high",
            text="Higher cosine",
        ),
        _embedded_document(
            service,
            scope,
            link_id="attachment_low",
            text="Lower cosine",
        ),
    )
    store = SQLiteVectorStore(tmp_path / f"reranker-{mode}.sqlite3", identity)
    for document in documents:
        store.replace_document(scope, document)

    with pytest.raises(RerankerFailedError) as captured:
        DocumentRetriever(
            service,
            store,
            _FakeReranker(mode),
        ).retrieve(
            scope,
            "secret query",
            tuple(_expected(document) for document in documents),
            policy=RetrievalPolicy(
                top_k=2,
                candidate_k=2,
                minimum_cosine_similarity=0.0,
            ),
        )
    assert str(captured.value) == (
        "The configured reranker returned no safe complete result."
    )
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    rendered_traceback = "".join(
        traceback.format_exception(captured.value)
    )
    assert "secret query" not in rendered_traceback
    assert "secret query and passage from adapter" not in rendered_traceback


def test_query_embedding_identity_mismatch_fails_before_results(
    tmp_path: Path,
) -> None:
    """Reject a same-shaped query from a different semantic vector space."""

    store_identity = _identity(digest_character="a")
    store_adapter = _ControlledEmbeddingAdapter(
        store_identity,
        {"Grounded": (1.0, 0.0)},
    )
    store_service = DocumentEmbeddingService(store_adapter)
    scope = _scope("identity")
    document = _embedded_document(
        store_service,
        scope,
        link_id="attachment_grounded",
        text="Grounded",
    )
    store = SQLiteVectorStore(tmp_path / "identity.sqlite3", store_identity)
    store.replace_document(scope, document)
    query_service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(
            _identity(digest_character="b"),
            {},
        )
    )

    with pytest.raises(RetrievalFailedError) as captured:
        DocumentRetriever(query_service, store).retrieve(
            scope,
            "same dimension but different model",
            (_expected(document),),
        )
    assert str(captured.value) == (
        "The vector store could not complete the exact search."
    )


def test_retrieval_result_exposes_provenance_but_no_vector_or_path(
    tmp_path: Path,
) -> None:
    """Keep evidence citation-ready without leaking vectors or file paths."""

    identity = _identity()
    adapter = _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    service = DocumentEmbeddingService(adapter)
    scope = _scope("publication")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
        page_number=3,
        media_type="application/pdf",
    )
    store = SQLiteVectorStore(tmp_path / "publication.sqlite3", identity)
    store.replace_document(scope, document)

    result = DocumentRetriever(service, store).retrieve(
        scope,
        "evidence",
        (_expected(document),),
    )

    assert result.hits[0].evidence[0].source == document.source
    assert result.hits[0].evidence[0].source_mappings
    field_names = _all_dataclass_field_names(result)
    assert "vector" not in field_names
    assert "path" not in field_names
    assert "query" not in field_names


def test_unexpected_embedder_exception_is_fully_sanitized() -> None:
    """Hide an untyped embedder's message, traceback, and exception cause."""

    identity = _identity()
    fixture_service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Corpus": (1.0, 0.0)})
    )
    scope = _scope("embedder-sanitization")
    document = _embedded_document(
        fixture_service,
        scope,
        link_id="attachment_corpus",
        text="Corpus",
    )
    private_query = "private-query-value-e2e241"

    with pytest.raises(RetrievalFailedError) as captured:
        DocumentRetriever(
            _ExplodingQueryEmbedder(),
            _NeverCalledStore(),
        ).retrieve(
            scope,
            private_query,
            (_expected(document),),
        )

    assert str(captured.value) == (
        "The query embedding service failed without a safe result."
    )
    _assert_sanitized_failure(
        captured.value,
        secrets=(
            private_query,
            "private-embedder-diagnostic-7d8535",
        ),
    )


def test_unexpected_store_exception_is_fully_sanitized() -> None:
    """Hide an untyped store's message, traceback, and exception cause."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Corpus": (1.0, 0.0)})
    )
    scope = _scope("store-sanitization")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_corpus",
        text="Corpus",
    )
    private_query = "private-store-query-696ca4"

    with pytest.raises(RetrievalFailedError) as captured:
        DocumentRetriever(service, _ExplodingStore()).retrieve(
            scope,
            private_query,
            (_expected(document),),
        )

    assert str(captured.value) == (
        "The vector store failed without a safe result."
    )
    _assert_sanitized_failure(
        captured.value,
        secrets=(private_query, "private-store-diagnostic-52f961"),
    )


def test_oversized_mutated_embedded_query_fails_before_store_access() -> None:
    """Reject an adapter-expanded vector before copying it into the store."""

    identity = _identity()
    fixture_service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Corpus": (1.0, 0.0)})
    )
    scope = _scope("oversized-query")
    document = _embedded_document(
        fixture_service,
        scope,
        link_id="attachment_corpus",
        text="Corpus",
    )
    query = fixture_service.embed_query("seed")
    object.__setattr__(
        query.vector,
        "values",
        (1.0,) * (MAX_EMBEDDING_DIMENSION + 1),
    )
    store = _NeverCalledStore()

    with pytest.raises(RetrievalFailedError, match="no safe result"):
        DocumentRetriever(_ReturningQueryEmbedder(query), store).retrieve(
            scope,
            "query",
            (_expected(document),),
        )
    assert store.calls == 0


def test_mutated_candidate_mapping_count_respects_reduced_caller_limit() -> None:
    """Bound provenance before traversing a store-mutated mapping tuple."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    )
    scope = _scope("mapping-limit")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
    )
    chunk = document.chunks[0]
    candidate = StoredRetrievalCandidate(
        source=document.source,
        derivation_fingerprint=document.derivation_fingerprint,
        embedding_space_id=document.identity.embedding_space_id,
        embedding_id=chunk.embedding_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        kind=chunk.kind,
        text=chunk.text,
        page_number=chunk.page_number,
        source_mappings=chunk.source_mappings,
        cosine_similarity=1.0,
    )
    object.__setattr__(
        candidate,
        "source_mappings",
        candidate.source_mappings + candidate.source_mappings,
    )

    with pytest.raises(RetrievalLimitError, match="mapping limit"):
        DocumentRetriever(
            service,
            _ReturningCandidateStore(candidate),
        ).retrieve(
            scope,
            "evidence",
            (_expected(document),),
            limits=RetrievalLimits(max_source_mappings=1),
        )


@pytest.mark.parametrize("mode", ["mutate-scores", "mutate-inputs"])
def test_reranker_mutated_container_lengths_fail_closed(
    tmp_path: Path,
    mode: Literal["mutate-scores", "mutate-inputs"],
) -> None:
    """Reject post-construction growth in result scores or request inputs."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    )
    scope = _scope(f"reranker-{mode}")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
    )
    store = SQLiteVectorStore(tmp_path / f"{mode}.sqlite3", identity)
    store.replace_document(scope, document)

    with pytest.raises(RerankerFailedError) as captured:
        DocumentRetriever(service, store, _FakeReranker(mode)).retrieve(
            scope,
            "evidence",
            (_expected(document),),
        )
    assert str(captured.value) == (
        "The configured reranker returned no safe complete result."
    )
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "mode",
    (
        "mutate-request-schema",
        "replace-request-policy",
        "mutate-request-policy-object",
        "identity-score-alias",
        "identity-score-deleted",
    ),
)
def test_reranker_mutated_request_and_identity_values_fail_closed(
    tmp_path: Path,
    mode: Literal[
        "mutate-request-schema",
        "replace-request-policy",
        "mutate-request-policy-object",
        "identity-score-alias",
        "identity-score-deleted",
    ],
) -> None:
    """Publish no result from post-call request or identity graph mutation."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    )
    scope = _scope(f"reranker-hardening-{mode}")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
    )
    store = SQLiteVectorStore(
        tmp_path / f"reranker-hardening-{mode}.sqlite3",
        identity,
    )
    store.replace_document(scope, document)
    reranker = _FakeReranker(mode)

    with pytest.raises(RerankerFailedError) as captured:
        DocumentRetriever(service, store, reranker).retrieve(
            scope,
            "evidence",
            (_expected(document),),
        )

    assert reranker.requests
    assert str(captured.value) == (
        "The configured reranker returned no safe complete result."
    )
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True


def test_direct_store_search_enforces_reduced_filter_and_candidate_text_limits(
    tmp_path: Path,
) -> None:
    """Honor caller-reduced filter and retained-text budgets inside SQLite."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(
            identity,
            {"Budgeted evidence": (1.0, 0.0)},
        )
    )
    scope = _scope("direct-limits")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_budgeted",
        text="Budgeted evidence",
    )
    store = SQLiteVectorStore(tmp_path / "direct-limits.sqlite3", identity)
    store.replace_document(scope, document)
    query = service.embed_query("evidence")
    expectation = (_expected(document),)
    policy = RetrievalPolicy(
        top_k=1,
        candidate_k=1,
        minimum_cosine_similarity=0.0,
    )

    with pytest.raises(
        DocumentContentLimitError,
        match="metadata-filter limit",
    ):
        store.search_scope(
            scope=scope,
            query=query,
            expected_documents=expectation,
            metadata_filter=RetrievalMetadataFilter(
                file_ids=(document.source.file_id,),
                chunk_kinds=("code",),
            ),
            policy=policy,
            limits=RetrievalLimits(max_filter_values=1),
        )

    with pytest.raises(
        DocumentContentLimitError,
        match="candidate-text limit",
    ):
        store.search_scope(
            scope=scope,
            query=query,
            expected_documents=expectation,
            metadata_filter=RetrievalMetadataFilter(),
            policy=policy,
            limits=RetrievalLimits(max_candidate_text_code_points=1),
        )


def test_retriever_rejects_reduced_filter_budget_before_dependencies() -> None:
    """Apply caller-reduced aggregate filter limits before external work."""

    scope = _scope("reduced-filter-budget")
    digest = hashlib.sha256(b"reduced-filter-budget").hexdigest()
    source = DocumentSource(
        scope=scope,
        link_id="attachment_filter_budget",
        file_id=f"file_{digest}",
        file_name="filter-budget.txt",
        media_type="text/plain",
        size_bytes=1,
    )
    generation = ExpectedDocumentGeneration(
        source=source,
        derivation_fingerprint=digest,
    )
    embedder = _NeverCalledEmbedder()
    store = _NeverCalledStore()

    with pytest.raises(
        RetrievalLimitError,
        match="Metadata filters exceed the configured value limit",
    ):
        DocumentRetriever(embedder, store).retrieve(
            scope,
            "query",
            (generation,),
            metadata_filter=RetrievalMetadataFilter(
                file_ids=(source.file_id,),
                chunk_kinds=("code",),
            ),
            limits=RetrievalLimits(max_filter_values=1),
        )

    assert embedder.calls == 0
    assert store.calls == 0


def test_retriever_contains_deleted_attachment_scope_fields() -> None:
    """Convert a deleted frozen scope slot into the public validation error."""

    scope = _scope("deleted-id")
    object.__delattr__(scope, "id")
    embedder = _NeverCalledEmbedder()
    store = _NeverCalledStore()

    with pytest.raises(RetrievalValidationError):
        DocumentRetriever(embedder, store).retrieve(
            scope,
            "query",
            (),
        )
    assert embedder.calls == 0
    assert store.calls == 0


@pytest.mark.parametrize(
    ("target_name", "field_name"),
    (
        ("query", "vector"),
        ("generation", "source"),
        ("filter", "file_ids"),
        ("policy", "top_k"),
        ("limits", "max_documents"),
    ),
)
def test_direct_store_contains_deleted_public_value_fields(
    tmp_path: Path,
    target_name: Literal[
        "query",
        "generation",
        "filter",
        "policy",
        "limits",
    ],
    field_name: str,
) -> None:
    """Return one closed store error for every deleted search-domain slot."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    )
    scope = _scope(f"deleted-{target_name}")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
    )
    store = SQLiteVectorStore(tmp_path / f"deleted-{target_name}.sqlite3", identity)
    store.replace_document(scope, document)
    query = service.embed_query("evidence")
    generation = _expected(document)
    metadata_filter = RetrievalMetadataFilter()
    policy = RetrievalPolicy()
    limits = RetrievalLimits()
    targets: dict[str, object] = {
        "query": query,
        "generation": generation,
        "filter": metadata_filter,
        "policy": policy,
        "limits": limits,
    }
    object.__delattr__(targets[target_name], field_name)

    with pytest.raises(DocumentValidationError):
        store.search_scope(
            scope=scope,
            query=query,
            expected_documents=(generation,),
            metadata_filter=metadata_filter,
            policy=policy,
            limits=limits,
        )


def test_embedding_identity_string_alias_is_rejected_at_both_query_seams(
    tmp_path: Path,
) -> None:
    """Reject a post-construction str subclass before it can become canonical."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(identity, {"Evidence": (1.0, 0.0)})
    )
    scope = _scope("identity-alias")
    document = _embedded_document(
        service,
        scope,
        link_id="attachment_evidence",
        text="Evidence",
    )
    store = SQLiteVectorStore(tmp_path / "identity-alias.sqlite3", identity)
    store.replace_document(scope, document)
    query = service.embed_query("evidence")
    object.__setattr__(
        query.identity,
        "provider",
        _StrAlias(query.identity.provider),
    )
    never_called_store = _NeverCalledStore()

    with pytest.raises(RetrievalFailedError):
        DocumentRetriever(
            _ReturningQueryEmbedder(query),
            never_called_store,
        ).retrieve(
            scope,
            "evidence",
            (_expected(document),),
        )
    assert never_called_store.calls == 0

    with pytest.raises(DocumentValidationError):
        store.search_scope(
            scope=scope,
            query=query,
            expected_documents=(_expected(document),),
            metadata_filter=RetrievalMetadataFilter(),
            policy=RetrievalPolicy(),
            limits=RetrievalLimits(),
        )


def test_header_budgets_fail_before_record_semantic_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop at header and record ceilings before semantic row decoding."""

    identity = _identity()
    service = DocumentEmbeddingService(
        _ControlledEmbeddingAdapter(
            identity,
            {"First evidence": (1.0, 0.0), "Second evidence": (0.8, 0.6)},
        )
    )
    scope = _scope("header-budgets")
    documents = (
        _embedded_document(
            service,
            scope,
            link_id="attachment_first",
            text="First evidence",
        ),
        _embedded_document(
            service,
            scope,
            link_id="attachment_second",
            text="Second evidence",
        ),
    )
    store = SQLiteVectorStore(tmp_path / "header-budgets.sqlite3", identity)
    for document in documents:
        store.replace_document(scope, document)
    query = service.embed_query("evidence")
    expectations = tuple(_expected(document) for document in documents)
    policy = RetrievalPolicy(
        top_k=2,
        candidate_k=2,
        minimum_cosine_similarity=0.0,
    )
    metadata_decode_calls = 0

    def _unexpected_metadata_decode(*args: object, **kwargs: object) -> object:
        """Record forbidden lineage decoding beyond a failed byte budget."""

        nonlocal metadata_decode_calls
        del args, kwargs
        metadata_decode_calls += 1
        raise AssertionError("Lineage decoder must not run")

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteVectorStore,
            "_decode_expected_document_metadata_row",
            _unexpected_metadata_decode,
        )
        with pytest.raises(DocumentContentLimitError, match="payload limit"):
            store.search_scope(
                scope=scope,
                query=query,
                expected_documents=expectations,
                metadata_filter=RetrievalMetadataFilter(),
                policy=policy,
                limits=RetrievalLimits(max_scanned_payload_bytes=1),
            )
    assert metadata_decode_calls == 0

    record_decode_calls = 0

    def _unexpected_record_decode(*args: object, **kwargs: object) -> object:
        """Record forbidden row decoding beyond a failed mapping budget."""

        nonlocal record_decode_calls
        del args, kwargs
        record_decode_calls += 1
        raise AssertionError("Record decoder must not run")

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteVectorStore,
            "_decode_record",
            _unexpected_record_decode,
        )
        with pytest.raises(
            DocumentContentLimitError,
            match="source-mapping limit",
        ):
            store.search_scope(
                scope=scope,
                query=query,
                expected_documents=expectations,
                metadata_filter=RetrievalMetadataFilter(),
                policy=policy,
                limits=RetrievalLimits(max_source_mappings=1),
            )
    assert record_decode_calls == 0

    connection = sqlite3.connect(tmp_path / "header-budgets.sqlite3")
    try:
        lineage_payload_bytes = connection.execute(
            "SELECT sum(length(CAST(lineage_json AS BLOB))) "
            "FROM vector_documents"
        ).fetchone()[0]
    finally:
        connection.close()
    assert type(lineage_payload_bytes) is int
    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteVectorStore,
            "_decode_record",
            _unexpected_record_decode,
        )
        with pytest.raises(DocumentContentLimitError, match="payload limit"):
            store.search_scope(
                scope=scope,
                query=query,
                expected_documents=expectations,
                metadata_filter=RetrievalMetadataFilter(),
                policy=policy,
                limits=RetrievalLimits(
                    max_scanned_payload_bytes=lineage_payload_bytes,
                ),
            )
    assert record_decode_calls == 0
