"""Test versioned document embedding without loading any model runtime."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import math
import struct
from threading import Event
import traceback
from typing import Callable

import pytest

from attachments import AttachmentScope
from documents.chunking import (
    ChunkedDocument,
    DocumentChunkingPolicy,
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
    DEFAULT_EMBEDDING_ADAPTER_ID,
    DEFAULT_EMBEDDING_ADAPTER_VERSION,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL_DIGEST,
    DEFAULT_EMBEDDING_MODEL_TAG,
    DEFAULT_EMBEDDING_NORMALIZATION,
    DEFAULT_EMBEDDING_PROVIDER,
    DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS,
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    EMBEDDED_QUERY_SCHEMA_VERSION,
    EMBEDDED_DOCUMENT_SCHEMA_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    QUERY_EMBEDDING_PREFIX,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    DocumentEmbeddingService,
    EmbeddedQuery,
    EmbeddedDocument,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingError,
    EmbeddingFailedError,
    EmbeddingInput,
    EmbeddingLimitError,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingUnavailableError,
    EmbeddingValidationError,
    EmbeddingVector,
)
from documents.exceptions import DocumentOperationCancelledError


_FILE_ID = f"file_{'a' * 64}"


def _identity(
    *,
    adapter_version: str = DEFAULT_EMBEDDING_ADAPTER_VERSION,
    digest: str = DEFAULT_EMBEDDING_MODEL_DIGEST,
) -> EmbeddingModelIdentity:
    """Build one production-shaped identity for deterministic fake tests."""

    return EmbeddingModelIdentity(
        provider=DEFAULT_EMBEDDING_PROVIDER,
        adapter_id=DEFAULT_EMBEDDING_ADAPTER_ID,
        adapter_version=adapter_version,
        model_tag=DEFAULT_EMBEDDING_MODEL_TAG,
        model_digest=digest,
        dimension=DEFAULT_EMBEDDING_DIMENSION,
        normalization=DEFAULT_EMBEDDING_NORMALIZATION,
        document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
        query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
    )


def _chunked(text: str = "Alpha\nBeta") -> ChunkedDocument:
    """Run one exact text fixture through the real cleaner and chunker."""

    source = DocumentSource(
        scope=AttachmentScope(kind="chat", id="chat_embedding"),
        link_id="attachment_embedding",
        file_id=_FILE_ID,
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=max(1, len(text.encode("utf-8"))),
    )
    loaded = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=source,
        document_format="text",
        loader_id="embedding-test-loader",
        loader_version="1.0.0",
        blocks=(DocumentBlock(0, "paragraph", text),),
        limits=DocumentLoadLimits(),
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    return StructureAwareDocumentChunker().chunk(cleaned)


def _many_chunks(count: int) -> ChunkedDocument:
    """Build a valid document with exactly ``count`` one-code-point chunks."""

    source = _chunked("x").provenance.source
    loaded = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=source,
        document_format="text",
        loader_id="embedding-test-loader",
        loader_version="1.0.0",
        blocks=(DocumentBlock(0, "paragraph", "x" * count),),
        limits=DocumentLoadLimits(),
    )
    cleaned = ConservativeDocumentCleaner().clean(loaded)
    return StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=1)
    ).chunk(cleaned)


def _one_hot(item_id: str, dimension: int) -> tuple[float, ...]:
    """Derive a deterministic unit vector from an opaque item ID."""

    index = int(hashlib.sha256(item_id.encode("ascii")).hexdigest(), 16)
    index %= dimension
    return tuple(1.0 if offset == index else 0.0 for offset in range(dimension))


class _FakeEmbedder:
    """Record requests and return deterministic or caller-forged batches."""

    def __init__(
        self,
        *,
        identity: EmbeddingModelIdentity | None = None,
        policy: EmbeddingBatchPolicy | None = None,
        result_factory: Callable[
            [EmbeddingRequest, EmbeddingModelIdentity, EmbeddingBatchPolicy],
            object,
        ]
        | None = None,
        failure: BaseException | None = None,
        identity_failure: BaseException | None = None,
        policy_failure: BaseException | None = None,
        mutate_metadata: Callable[[_FakeEmbedder], None] | None = None,
    ) -> None:
        self.current_identity = _identity() if identity is None else identity
        self.current_policy = EmbeddingBatchPolicy() if policy is None else policy
        self.result_factory = result_factory
        self.failure = failure
        self.identity_failure = identity_failure
        self.policy_failure = policy_failure
        self.mutate_metadata = mutate_metadata
        self.requests: list[EmbeddingRequest] = []
        self.returned_vectors: list[EmbeddingVector] = []

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the adapter identity visible at this instant."""

        if self.identity_failure is not None:
            raise self.identity_failure
        return self.current_identity

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the adapter policy visible at this instant."""

        if self.policy_failure is not None:
            raise self.policy_failure
        return self.current_policy

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Record one request and return deterministic or forged output."""

        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        if self.result_factory is not None:
            result = self.result_factory(
                request,
                self.current_identity,
                self.current_policy,
            )
        else:
            vectors = tuple(
                EmbeddingVector(
                    item_id=item.item_id,
                    values=_one_hot(
                        item.item_id,
                        self.current_identity.dimension,
                    ),
                )
                for item in request.items
            )
            self.returned_vectors.extend(vectors)
            result = EmbeddingBatch(
                schema_version=EMBEDDING_SCHEMA_VERSION,
                identity=self.current_identity,
                policy=self.current_policy,
                purpose=request.purpose,
                vectors=vectors,
            )
        if self.mutate_metadata is not None:
            self.mutate_metadata(self)
        return result  # type: ignore[return-value]


def _forged_batch(
    request: EmbeddingRequest,
    identity: EmbeddingModelIdentity,
    policy: EmbeddingBatchPolicy,
    vectors: tuple[EmbeddingVector, ...],
) -> EmbeddingBatch:
    """Build one otherwise-consistent adapter result around forged vectors."""

    return EmbeddingBatch(
        schema_version=EMBEDDING_SCHEMA_VERSION,
        identity=identity,
        policy=policy,
        purpose=request.purpose,
        vectors=vectors,
    )


def _forged_empty_batch(
    request: EmbeddingRequest,
    identity: EmbeddingModelIdentity,
    policy: EmbeddingBatchPolicy,
) -> EmbeddingBatch:
    """Bypass frozen fields so the service receives an empty adapter result."""

    vector = EmbeddingVector(
        item_id=request.items[0].item_id,
        values=_one_hot(request.items[0].item_id, identity.dimension),
    )
    batch = _forged_batch(request, identity, policy, (vector,))
    object.__setattr__(batch, "vectors", ())
    return batch


def test_document_embedding_cancels_between_bounded_batches() -> None:
    """Discard a completed batch when cancellation wins before the next one."""

    cancelled = Event()
    adapter = _FakeEmbedder(
        mutate_metadata=lambda _adapter: cancelled.set(),
    )

    with pytest.raises(DocumentOperationCancelledError, match="cancelled"):
        DocumentEmbeddingService(adapter).embed_document(
            _many_chunks(17),
            cancel_requested=cancelled.is_set,
        )

    assert len(adapter.requests) == 1


def test_identity_and_embedding_ids_are_canonical_and_versioned() -> None:
    """Invalidate vector spaces on semantics changes, not float-byte changes."""

    first_identity = _identity()
    same_identity = _identity()
    next_identity = _identity(adapter_version="1.0.1")

    assert first_identity.embedding_space_id == same_identity.embedding_space_id
    assert first_identity.embedding_space_id != next_identity.embedding_space_id

    document = _chunked("Stable text")
    first = DocumentEmbeddingService(_FakeEmbedder()).embed_document(document)

    def alternate_vectors(
        request: EmbeddingRequest,
        identity: EmbeddingModelIdentity,
        policy: EmbeddingBatchPolicy,
    ) -> EmbeddingBatch:
        """Return a different unit vector in the same logical vector space."""

        vectors = tuple(
            EmbeddingVector(
                item_id=item.item_id,
                values=(0.0, 1.0) + (0.0,) * (identity.dimension - 2),
            )
            for item in request.items
        )
        return _forged_batch(request, identity, policy, vectors)

    second = DocumentEmbeddingService(
        _FakeEmbedder(result_factory=alternate_vectors)
    ).embed_document(document)

    assert first.chunks[0].embedding_id == second.chunks[0].embedding_id
    assert first.chunks[0].vector_checksum != second.chunks[0].vector_checksum


def test_identity_binds_actual_batch_and_template_mechanics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalidate spaces when effective policy or template text drifts."""

    baseline = _identity().embedding_space_id

    monkeypatch.setattr(
        "documents.embedding.DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS",
        31_999,
    )
    assert _identity().embedding_space_id != baseline

    monkeypatch.setattr(
        "documents.embedding.DEFAULT_EMBEDDING_MAX_BATCH_CODE_POINTS",
        32_000,
    )
    monkeypatch.setattr(
        "documents.embedding.QUERY_EMBEDDING_PREFIX",
        f"{QUERY_EMBEDDING_PREFIX} ",
    )
    assert _identity().embedding_space_id != baseline


def test_document_embedding_batches_exact_text_and_preserves_full_lineage() -> None:
    """Keep source text and every citation mapping unchanged across the seam."""

    document = _chunked("  e\u0301xact text  ")
    embedder = _FakeEmbedder()

    result = DocumentEmbeddingService(embedder).embed_document(document)

    assert result.schema_version == EMBEDDED_DOCUMENT_SCHEMA_VERSION
    assert result.source == document.provenance.source
    assert result.derivation_fingerprint == document.derivation_fingerprint
    assert result.chunked_document == document
    assert result.chunked_document is not document
    assert tuple(item.text for item in embedder.requests[0].items) == tuple(
        chunk.text for chunk in document.chunks
    )
    assert tuple(chunk.text for chunk in result.chunks) == tuple(
        chunk.text for chunk in document.chunks
    )
    assert tuple(chunk.source_mappings for chunk in result.chunks) == tuple(
        chunk.source_mappings for chunk in document.chunks
    )
    expected_bytes = struct.pack(
        f"<{DEFAULT_EMBEDDING_DIMENSION}f",
        *result.chunks[0].vector,
    )
    assert result.chunks[0].vector_checksum == hashlib.sha256(
        expected_bytes
    ).hexdigest()


def test_document_embedding_uses_fixed_batches_without_partial_publication() -> None:
    """Split at sixteen inputs and raise instead of returning a partial result."""

    document = _many_chunks(17)
    embedder = _FakeEmbedder()
    result = DocumentEmbeddingService(embedder).embed_document(document)

    assert [len(request.items) for request in embedder.requests] == [16, 1]
    assert len(result.chunks) == 17

    class _SecondBatchFailure(_FakeEmbedder):
        """Fail only after one valid batch has already completed."""

        def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
            """Return the first batch and reject the second batch."""

            if self.requests:
                raise EmbeddingFailedError("sanitized failure")
            return super().embed(request)

    with pytest.raises(
        EmbeddingFailedError,
        match="failed to produce a safe result",
    ):
        DocumentEmbeddingService(_SecondBatchFailure()).embed_document(document)


def test_query_embedding_binds_identity_without_publishing_query_text() -> None:
    """Bind the fixed query instruction to its complete semantic vector space."""

    embedder = _FakeEmbedder()
    result = DocumentEmbeddingService(embedder).embed_query("保留原样 e\u0301 ")

    assert isinstance(result, EmbeddedQuery)
    assert result.schema_version == EMBEDDED_QUERY_SCHEMA_VERSION
    assert result.identity == embedder.current_identity
    assert result.policy == embedder.current_policy
    assert result.vector.item_id == "query"
    assert len(result.vector.values) == DEFAULT_EMBEDDING_DIMENSION
    assert embedder.requests[0].purpose == "query"
    assert embedder.requests[0].items[0].text == (
        f"{QUERY_EMBEDDING_PREFIX}保留原样 e\u0301 "
    )
    assert not hasattr(result, "text")


def test_query_embedding_rejects_adapter_owned_identity_scalar_alias() -> None:
    """Keep adapter-defined string behavior out of a published query identity."""

    class _StringAlias(str):
        """Represent a hostile scalar subclass retained by loose validators."""

    identity = _identity()
    object.__setattr__(identity, "provider", _StringAlias(identity.provider))

    with pytest.raises(EmbeddingValidationError, match="invalid identity"):
        DocumentEmbeddingService(
            _FakeEmbedder(identity=identity)
        ).embed_query("bounded query")


def test_query_and_request_limits_fail_before_adapter_inference() -> None:
    """Bound effective template text, item count, and aggregate code points."""

    embedder = _FakeEmbedder()
    service = DocumentEmbeddingService(embedder)
    maximum_query_code_points = (
        DEFAULT_EMBEDDING_MAX_INPUT_CODE_POINTS - len(QUERY_EMBEDDING_PREFIX)
    )
    for text in (
        "x" * (maximum_query_code_points + 1),
        " " * (maximum_query_code_points + 1),
    ):
        with pytest.raises(EmbeddingLimitError, match="code-point"):
            service.embed_query(text)
    assert embedder.requests == []

    item = EmbeddingInput(item_id="item", text="x" * 2_000)
    with pytest.raises(EmbeddingLimitError, match="batch item"):
        EmbeddingRequest(purpose="document", items=(item,) * 17)


def test_query_embedding_rejects_string_subclasses_before_formatting() -> None:
    """Keep caller-defined string formatting behavior outside the service."""

    class _QueryAlias(str):
        """Represent a hostile public-input scalar subclass."""

    embedder = _FakeEmbedder()
    with pytest.raises(EmbeddingValidationError, match="meaningful text"):
        DocumentEmbeddingService(embedder).embed_query(_QueryAlias("query"))
    assert embedder.requests == []


def test_service_rejects_mutated_oversized_vector_shape() -> None:
    """Reject adapter-expanded vectors before traversing their values."""

    def forged(
        request: EmbeddingRequest,
        identity: EmbeddingModelIdentity,
        policy: EmbeddingBatchPolicy,
    ) -> EmbeddingBatch:
        """Replace one valid frozen vector with an over-dimensional tuple."""

        vector = EmbeddingVector(
            item_id=request.items[0].item_id,
            values=_one_hot(request.items[0].item_id, identity.dimension),
        )
        batch = _forged_batch(request, identity, policy, (vector,))
        object.__setattr__(
            vector,
            "values",
            (1.0,) + (0.0,) * identity.dimension,
        )
        return batch

    with pytest.raises(EmbeddingValidationError, match="dimension"):
        DocumentEmbeddingService(
            _FakeEmbedder(result_factory=forged)
        ).embed_query("query")


@pytest.mark.parametrize(
    "result_factory, message",
    [
        (
            lambda request, identity, policy: object(),
            "invalid batch",
        ),
        (
            _forged_empty_batch,
            "inconsistent batch metadata",
        ),
        (
            lambda request, identity, policy: _forged_batch(
                request,
                identity,
                policy,
                (
                    EmbeddingVector(
                        item_id="different",
                        values=_one_hot("different", identity.dimension),
                    ),
                ),
            ),
            "order or identity",
        ),
        (
            lambda request, identity, policy: _forged_batch(
                request,
                identity,
                policy,
                (
                    EmbeddingVector(
                        item_id=request.items[0].item_id,
                        values=(1.0,),
                    ),
                ),
            ),
            "dimension",
        ),
        (
            lambda request, identity, policy: _forged_batch(
                request,
                identity,
                policy,
                (
                    EmbeddingVector(
                        item_id=request.items[0].item_id,
                        values=(0.0,) * identity.dimension,
                    ),
                ),
            ),
            "must not be zero",
        ),
        (
            lambda request, identity, policy: _forged_batch(
                request,
                identity,
                policy,
                (
                    EmbeddingVector(
                        item_id=request.items[0].item_id,
                        values=(2.0,) + (0.0,) * (identity.dimension - 1),
                    ),
                ),
            ),
            "L2-normalized",
        ),
    ],
)
def test_service_rejects_hostile_adapter_result_shapes(
    result_factory: Callable[
        [EmbeddingRequest, EmbeddingModelIdentity, EmbeddingBatchPolicy],
        object,
    ],
    message: str,
) -> None:
    """Reject missing, mismatched, wrong-dimensional, and non-unit results."""

    with pytest.raises((EmbeddingValidationError, EmbeddingFailedError), match=message):
        DocumentEmbeddingService(
            _FakeEmbedder(result_factory=result_factory)
        ).embed_document(_chunked("x"))


def test_service_rejects_non_finite_vector_from_bypassed_dataclass() -> None:
    """Treat frozen dataclasses as bypassable and revalidate every coordinate."""

    def forged(
        request: EmbeddingRequest,
        identity: EmbeddingModelIdentity,
        policy: EmbeddingBatchPolicy,
    ) -> EmbeddingBatch:
        """Mutate a valid frozen vector to simulate hostile adapter code."""

        vector = EmbeddingVector(
            item_id=request.items[0].item_id,
            values=_one_hot(request.items[0].item_id, identity.dimension),
        )
        batch = _forged_batch(request, identity, policy, (vector,))
        object.__setattr__(
            vector,
            "values",
            (math.nan,) + (0.0,) * (identity.dimension - 1),
        )
        return batch

    with pytest.raises(EmbeddingValidationError, match="finite"):
        DocumentEmbeddingService(
            _FakeEmbedder(result_factory=forged)
        ).embed_document(_chunked("x"))


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("boolean-schema", "invalid batch"),
        ("list-vectors", "invalid batch"),
        ("list-values", "invalid vector"),
    ],
)
def test_service_rejects_mutable_shapes_from_frozen_dataclass_bypass(
    mutation: str,
    message: str,
) -> None:
    """Require exact immutable result containers despite frozen-field bypass."""

    def forged(
        request: EmbeddingRequest,
        identity: EmbeddingModelIdentity,
        policy: EmbeddingBatchPolicy,
    ) -> EmbeddingBatch:
        """Mutate one valid graph into an otherwise plausible hostile result."""

        vector = EmbeddingVector(
            item_id=request.items[0].item_id,
            values=_one_hot(request.items[0].item_id, identity.dimension),
        )
        batch = _forged_batch(request, identity, policy, (vector,))
        if mutation == "boolean-schema":
            object.__setattr__(batch, "schema_version", True)
        elif mutation == "list-vectors":
            object.__setattr__(batch, "vectors", [vector])
        else:
            object.__setattr__(vector, "values", list(vector.values))
        return batch

    with pytest.raises(EmbeddingValidationError, match=message):
        DocumentEmbeddingService(
            _FakeEmbedder(result_factory=forged)
        ).embed_document(_chunked("x"))


def test_service_rejects_adapter_identity_and_policy_drift() -> None:
    """Fail closed when adapter metadata changes during a model call."""

    def mutate_identity(embedder: _FakeEmbedder) -> None:
        """Change semantic adapter identity after returning a valid batch."""

        embedder.current_identity = _identity(adapter_version="2.0.0")

    with pytest.raises(EmbeddingValidationError, match="changed"):
        DocumentEmbeddingService(
            _FakeEmbedder(mutate_metadata=mutate_identity)
        ).embed_document(_chunked("x"))

    def mutate_policy(embedder: _FakeEmbedder) -> None:
        """Bypass a frozen policy to simulate mutable hostile metadata."""

        object.__setattr__(embedder.current_policy, "truncate", True)

    with pytest.raises(EmbeddingValidationError, match="fixed v1 contract"):
        DocumentEmbeddingService(
            _FakeEmbedder(mutate_metadata=mutate_policy)
        ).embed_document(_chunked("x"))


def test_service_deep_snapshots_adapter_and_document_graphs() -> None:
    """Prevent post-return mutation from changing published vectors or lineage."""

    document = _chunked("Snapshot")
    embedder = _FakeEmbedder()
    result = DocumentEmbeddingService(embedder).embed_document(document)
    published_text = result.chunks[0].text
    published_vector = result.chunks[0].vector

    object.__setattr__(document.chunks[0], "text", "changed")
    object.__setattr__(
        embedder.returned_vectors[0],
        "values",
        (0.0,) * DEFAULT_EMBEDDING_DIMENSION,
    )

    assert result.chunks[0].text == published_text
    assert result.chunked_document.chunks[0].text == published_text
    assert result.chunks[0].vector == published_vector


def test_embedded_document_rejects_any_chunk_lineage_change() -> None:
    """Require stored chunk content and mappings to match the full source graph."""

    result = DocumentEmbeddingService(_FakeEmbedder()).embed_document(
        _chunked("Lineage")
    )
    changed = replace(result.chunks[0], text="changed")
    with pytest.raises(EmbeddingValidationError, match="exact source"):
        replace(result, chunks=(changed,))


def test_unknown_adapter_failure_is_sanitized() -> None:
    """Hide raw adapter diagnostics and private input from stable exceptions."""

    private = "PRIVATE_DOCUMENT_TEXT"
    embedder = _FakeEmbedder(
        failure=RuntimeError(f"{private} at C:/private/model.bin")
    )
    with pytest.raises(EmbeddingFailedError) as captured:
        DocumentEmbeddingService(embedder).embed_document(_chunked(private))

    message = str(captured.value)
    rendered = "".join(traceback.format_exception(captured.value))
    assert private not in message
    assert "model.bin" not in message
    assert private not in rendered
    assert "model.bin" not in rendered
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert message == "Document embedding failed without a safe typed result."


def test_unknown_query_adapter_failure_is_sanitized() -> None:
    """Remove adapter diagnostics from a public query failure traceback."""

    private = "PRIVATE_QUERY_DIAGNOSTIC_XYZ"
    embedder = _FakeEmbedder(failure=RuntimeError(private))

    with pytest.raises(EmbeddingFailedError) as captured:
        DocumentEmbeddingService(embedder).embed_query("safe query")

    rendered = "".join(traceback.format_exception(captured.value))
    assert private not in rendered
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert str(captured.value) == (
        "Query embedding failed without a safe typed result."
    )


@pytest.mark.parametrize(
    "failure, expected_type, expected_message",
    [
        (
            EmbeddingValidationError("PRIVATE validation"),
            EmbeddingValidationError,
            "Embedding adapter reported an invalid operation.",
        ),
        (
            EmbeddingLimitError("PRIVATE limit"),
            EmbeddingLimitError,
            "Embedding adapter exceeded a resource limit.",
        ),
        (
            EmbeddingUnavailableError("PRIVATE unavailable"),
            EmbeddingUnavailableError,
            "Embedding adapter is unavailable.",
        ),
        (
            EmbeddingFailedError("PRIVATE failure"),
            EmbeddingFailedError,
            "Embedding adapter failed to produce a safe result.",
        ),
        (
            EmbeddingError("PRIVATE unknown category"),
            EmbeddingFailedError,
            "Embedding adapter failed without a recognized error category.",
        ),
    ],
)
def test_typed_adapter_failures_keep_category_but_not_private_text(
    failure: EmbeddingError,
    expected_type: type[EmbeddingError],
    expected_message: str,
) -> None:
    """Do not treat an adapter-selected exception class as trusted text."""

    with pytest.raises(expected_type) as captured:
        DocumentEmbeddingService(
            _FakeEmbedder(failure=failure)
        ).embed_document(_chunked("PRIVATE_DOCUMENT"))
    assert str(captured.value) == expected_message
    assert "PRIVATE" not in str(captured.value)


@pytest.mark.parametrize("surface", ["identity", "policy"])
def test_adapter_metadata_failures_are_sanitized_at_the_property_seam(
    surface: str,
) -> None:
    """Sanitize typed failures raised while reading adapter metadata."""

    secret = "PRIVATE C:/model/path"
    failure = EmbeddingUnavailableError(secret)
    embedder = _FakeEmbedder(
        identity_failure=failure if surface == "identity" else None,
        policy_failure=failure if surface == "policy" else None,
    )

    with pytest.raises(EmbeddingUnavailableError) as captured:
        DocumentEmbeddingService(embedder).embed_document(_chunked("x"))
    assert str(captured.value) == "Embedding adapter is unavailable."
    assert secret not in str(captured.value)


def test_embedding_domain_rejects_ambiguous_values() -> None:
    """Reject booleans, mutable IDs, bad digests, and mismatched space IDs."""

    with pytest.raises(EmbeddingValidationError, match="dimension"):
        replace(_identity(), dimension=True)  # type: ignore[arg-type]
    with pytest.raises(EmbeddingValidationError, match="model_digest"):
        replace(_identity(), model_digest="short")
    with pytest.raises(EmbeddingValidationError, match="space_id"):
        replace(_identity(), embedding_space_id="0" * 64)
    with pytest.raises(EmbeddingValidationError, match="item_id"):
        EmbeddingInput(item_id="../private", text="x")
