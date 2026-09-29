"""Compose document derivation, embedding, and atomic vector persistence.

This application service is intentionally independent from the desktop
composition root.  It turns one verified ownership link into a complete
embedded generation, or prepares every generation for a scoped rebuild before
the store is allowed to replace existing data.  That ordering keeps a loader
or model failure from deleting the last usable index.
"""

from __future__ import annotations

from typing import Final, Protocol

from attachments.domain import AttachmentScope, validate_attachment_id

from .chunking import ChunkedDocument
from .embedding import EmbeddedDocument, EmbeddingError
from .exceptions import (
    DocumentContentLimitError,
    DocumentError,
    DocumentProcessingFailedError,
    DocumentValidationError,
)


MAX_INDEX_REBUILD_DOCUMENTS: Final = 1_000
MAX_INDEX_REBUILD_CHUNKS: Final = 100_000


class _DocumentProcessor(Protocol):
    """Describe the existing scope-bound load, clean, and chunk boundary."""

    def process(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> ChunkedDocument:
        """Return one validated chunk derivation for an ownership link."""

        ...


class _DocumentEmbedder(Protocol):
    """Describe the complete-document embedding boundary used here."""

    def embed_document(self, document: ChunkedDocument) -> EmbeddedDocument:
        """Embed every exact chunk without persisting a partial result."""

        ...


class _DocumentVectorStore(Protocol):
    """Describe the atomic persistence operations required by indexing."""

    def replace_document(
        self,
        scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        """Replace one document generation inside an exact scope."""

        ...

    def rebuild(
        self,
        scope: AttachmentScope,
        documents: tuple[EmbeddedDocument, ...],
    ) -> None:
        """Replace the complete document set for an exact scope."""

        ...

    def delete_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Delete one exact scoped ownership link and its vectors."""

        ...


def _snapshot_scope(scope: object) -> AttachmentScope:
    """Rebuild one authority value before passing it to injected services."""

    if not isinstance(scope, AttachmentScope):
        raise DocumentValidationError("scope must be AttachmentScope.")
    try:
        return AttachmentScope(kind=scope.kind, id=scope.id)
    except (TypeError, ValueError) as error:
        raise DocumentValidationError("Attachment scope is invalid.") from error


def _validate_link_id(link_id: object) -> str:
    """Return one canonical ownership-link ID without exposing storage paths."""

    try:
        return validate_attachment_id(link_id)
    except ValueError as error:
        raise DocumentValidationError(
            "link_id must be a valid opaque attachment identifier."
        ) from error


def _require_chunk_owner(
    document: object,
    scope: AttachmentScope,
    link_id: str,
) -> ChunkedDocument:
    """Reject a processor result that crosses the requested ownership scope."""

    if not isinstance(document, ChunkedDocument):
        raise DocumentValidationError(
            "Document processor returned an invalid chunked result."
        )
    source = document.provenance.source
    if source.scope != scope or source.link_id != link_id:
        raise DocumentValidationError(
            "Document processor changed the requested ownership identity."
        )
    return document


def _require_embedded_lineage(
    document: object,
    chunked: ChunkedDocument,
) -> EmbeddedDocument:
    """Require embedding output to preserve the exact verified chunk graph."""

    if type(document) is not EmbeddedDocument:
        raise DocumentValidationError(
            "Document embedder returned an invalid embedded result."
        )
    if (
        document.source != chunked.provenance.source
        or document.derivation_fingerprint
        != chunked.derivation_fingerprint
        or document.chunked_document != chunked
    ):
        raise DocumentValidationError(
            "Document embedder changed the verified chunk lineage."
        )
    return document


def _require_embedded_scope(
    document: object,
    scope: AttachmentScope,
) -> EmbeddedDocument:
    """Reject non-canonical embedded values or a cross-scope commit attempt.

    The commit boundary is intentionally callable independently from
    preparation so a lifecycle coordinator can revalidate authority between
    the two operations.  It therefore cannot trust that the embedded value
    came from this service, even when the persistence adapter would perform
    its own validation later.
    """

    if type(document) is not EmbeddedDocument:
        raise DocumentValidationError(
            "document must be an exact EmbeddedDocument value."
        )
    try:
        source = document.source
        chunked_source = document.chunked_document.provenance.source
    except (AttributeError, TypeError) as error:
        raise DocumentValidationError(
            "Embedded document lineage is invalid."
        ) from error
    if (
        _snapshot_scope(source.scope) != scope
        or _snapshot_scope(chunked_source.scope) != scope
        or chunked_source != source
        or _validate_link_id(source.link_id) != source.link_id
        or _validate_link_id(chunked_source.link_id) != source.link_id
    ):
        raise DocumentValidationError(
            "Embedded document scope does not match the requested scope."
        )
    return document


class DocumentIndexingService:
    """Build and persist complete scope-bound embedding generations.

    The processor and embedder finish before any store mutation.  A scoped
    rebuild likewise prepares every requested document first, then performs a
    single store transaction.  This is a deliberate rollback boundary: one
    bad source or failed model batch leaves the previous generation intact.
    """

    def __init__(
        self,
        processor: _DocumentProcessor,
        embedder: _DocumentEmbedder,
        store: _DocumentVectorStore,
    ) -> None:
        """Compose explicit derivation, embedding, and persistence services."""

        if processor is None or embedder is None or store is None:
            raise TypeError("processor, embedder, and store are required.")
        self._processor = processor
        self._embedder = embedder
        self._store = store

    def index_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> EmbeddedDocument:
        """Create and atomically replace one exact ownership-link generation."""

        embedded = self.prepare_document(scope, link_id)
        self.commit_document(scope, embedded)
        return embedded

    def prepare_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> EmbeddedDocument:
        """Prepare one complete generation without mutating the vector store.

        Keeping inference separate from persistence gives lifecycle callers a
        safe cancellation and authority-revalidation point before any vector
        generation is replaced.
        """

        try:
            canonical_scope = _snapshot_scope(scope)
            canonical_link_id = _validate_link_id(link_id)
            chunked = _require_chunk_owner(
                self._processor.process(
                    _snapshot_scope(canonical_scope),
                    canonical_link_id,
                ),
                canonical_scope,
                canonical_link_id,
            )
            embedded = _require_embedded_lineage(
                self._embedder.embed_document(chunked),
                chunked,
            )
            return embedded
        except (DocumentError, EmbeddingError):
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document indexing exceeded a safe resource limit."
            ) from error
        except Exception as error:
            # Adapters may raise arbitrary implementation exceptions.  The
            # public boundary must not leak paths, source text, or model data.
            raise DocumentProcessingFailedError(
                "Document indexing failed without a safe typed result."
            ) from error

    def commit_document(
        self,
        scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        """Atomically persist one exact embedded generation in its own scope.

        This method performs no processing or embedding.  Callers must treat
        it as the mutation boundary and revalidate external ownership or
        cancellation immediately before invoking it.
        """

        try:
            canonical_scope = _snapshot_scope(scope)
            canonical_document = _require_embedded_scope(
                document,
                canonical_scope,
            )
            self._store.replace_document(
                _snapshot_scope(canonical_scope),
                canonical_document,
            )
        except (DocumentError, EmbeddingError):
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document indexing exceeded a safe resource limit."
            ) from error
        except Exception as error:
            # Persistence adapters are untrusted at this boundary.  Keep
            # source text, local paths, and model diagnostics out of callers.
            raise DocumentProcessingFailedError(
                "Document indexing failed without a safe typed result."
            ) from error

    def rebuild_scope(
        self,
        scope: AttachmentScope,
        link_ids: tuple[str, ...],
    ) -> tuple[EmbeddedDocument, ...]:
        """Prepare then atomically replace one scope's complete index set."""

        try:
            canonical_scope = _snapshot_scope(scope)
            if not isinstance(link_ids, tuple) or not all(
                isinstance(link_id, str) for link_id in link_ids
            ):
                raise DocumentValidationError(
                    "link_ids must be a tuple of opaque attachment IDs."
                )
            if len(link_ids) > MAX_INDEX_REBUILD_DOCUMENTS:
                raise DocumentContentLimitError(
                    "Document rebuild exceeds its document-count limit."
                )
            canonical_links = tuple(
                _validate_link_id(link_id) for link_id in link_ids
            )
            if len(set(canonical_links)) != len(canonical_links):
                raise DocumentValidationError(
                    "Document rebuild cannot contain duplicate ownership links."
                )

            # Do all potentially failing parsing and inference before the
            # store begins its delete-and-insert transaction.  The aggregate
            # limit is checked before embedding each document because a mere
            # document-count ceiling could otherwise admit millions of model
            # calls and retain their vectors only for the store to reject the
            # generation after all expensive work had already completed.
            prepared: list[EmbeddedDocument] = []
            total_chunks = 0
            for link_id in canonical_links:
                chunked = _require_chunk_owner(
                    self._processor.process(
                        _snapshot_scope(canonical_scope),
                        link_id,
                    ),
                    canonical_scope,
                    link_id,
                )
                total_chunks += len(chunked.chunks)
                if total_chunks > MAX_INDEX_REBUILD_CHUNKS:
                    raise DocumentContentLimitError(
                        "Document rebuild exceeds its aggregate chunk limit."
                    )
                prepared.append(
                    _require_embedded_lineage(
                        self._embedder.embed_document(chunked),
                        chunked,
                    )
                )
            result = tuple(prepared)
            self._store.rebuild(_snapshot_scope(canonical_scope), result)
            return result
        except (DocumentError, EmbeddingError):
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document rebuild exceeded a safe resource limit."
            ) from error
        except Exception as error:
            raise DocumentProcessingFailedError(
                "Document rebuild failed without a safe typed result."
            ) from error

    def delete_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Delete one document only from the explicitly supplied scope."""

        try:
            return self._store.delete_document(
                _snapshot_scope(scope),
                _validate_link_id(link_id),
            )
        except (DocumentError, EmbeddingError):
            raise
        except Exception as error:
            raise DocumentProcessingFailedError(
                "Document index deletion failed without a safe typed result."
            ) from error
