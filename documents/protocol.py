"""Define path-private loader, cleaner, and chunker adapter contracts."""

from __future__ import annotations

from typing import Protocol, TypeAlias

from attachments.domain import AttachmentScope

from .chunking import ChunkedDocument, DocumentChunkingPolicy
from .cleaning import (
    CleanedDocument,
    DocumentCleaningPolicy,
    DocumentProcessingLimits,
)
from .domain import DocumentLoadLimits, DocumentSource, LoadedDocument


DocumentRoute: TypeAlias = tuple[str, str]


class DocumentLoader(Protocol):
    """Parse verified immutable bytes without receiving a filesystem path.

    Routes contain exact ``(lowercase suffix, media type)`` pairs. The
    application service owns route selection and exception translation, while
    each adapter owns content-signature checks and incremental parser budgets.
    """

    @property
    def loader_id(self) -> str:
        """Return the stable lowercase identity of this loader."""

        ...

    @property
    def loader_version(self) -> str:
        """Return the version that determines this loader's raw output."""

        ...

    @property
    def routes(self) -> frozenset[DocumentRoute]:
        """Return exact suffix and stored-media-type pairs handled here."""

        ...

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Parse one verified byte snapshot or raise a typed DocumentError."""

        ...


class DocumentSourceLoader(Protocol):
    """Resolve one owned attachment link into validated loaded structure."""

    def load(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> LoadedDocument:
        """Load one exact ownership link or raise a typed DocumentError."""

        ...


class DocumentCleaner(Protocol):
    """Convert loaded structure into a versioned lossless piece table."""

    @property
    def cleaner_id(self) -> str:
        """Return the stable lowercase identity of this cleaner."""

        ...

    @property
    def cleaner_version(self) -> str:
        """Return the version governing cleaning behavior and identity."""

        ...

    @property
    def policy(self) -> DocumentCleaningPolicy:
        """Return the exact output-affecting cleaning policy."""

        ...

    @property
    def limits(self) -> DocumentProcessingLimits:
        """Return the resource ceilings enforced by this cleaner."""

        ...

    def clean(self, document: LoadedDocument) -> CleanedDocument:
        """Clean one loaded document or raise a typed DocumentError."""

        ...


class DocumentChunker(Protocol):
    """Derive deterministic bounded chunks from one cleaned document."""

    @property
    def chunker_id(self) -> str:
        """Return the stable lowercase identity of this chunker."""

        ...

    @property
    def chunker_version(self) -> str:
        """Return the version governing boundaries, mappings, and IDs."""

        ...

    @property
    def policy(self) -> DocumentChunkingPolicy:
        """Return the exact output-affecting chunking policy."""

        ...

    def chunk(self, document: CleanedDocument) -> ChunkedDocument:
        """Chunk one cleaned document or raise a typed DocumentError."""

        ...
