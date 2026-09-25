"""Define the path-private adapter contract for document format loaders."""

from __future__ import annotations

from typing import Protocol, TypeAlias

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
