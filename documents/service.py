"""Coordinate trusted document loaders over verified attachment bytes."""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import PurePath
from typing import BinaryIO, Final, Protocol

from attachments import (
    AttachmentNotFoundError,
    AttachmentScope,
    AttachmentStorageError,
    AttachmentValidationError,
    FileOwnership,
    OriginalFileMetadata,
)
from attachments.domain import validate_attachment_id, validate_media_type

from .domain import (
    DocumentLoadLimits,
    DocumentSource,
    LoadedDocument,
)
from .exceptions import (
    DocumentContentLimitError,
    DocumentError,
    DocumentLoadFailedError,
    DocumentNotFoundError,
    DocumentReadError,
    DocumentTooLargeError,
    DocumentUnsupportedFormatError,
    DocumentValidationError,
)
from .protocol import DocumentLoader, DocumentRoute


_READ_BUFFER_BYTES: Final = 1024 * 1024
_LOADER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_LOADER_VERSION_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)


class DocumentFileRepository(Protocol):
    """Expose only the path-private file reads needed by document loading."""

    def list_file_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """List canonical originals authorized for one exact owner scope."""

        ...

    def list_file_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """List link-specific names and media types in one owner scope."""

        ...

    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> AbstractContextManager[BinaryIO]:
        """Open immutable verified bytes selected by Scope and File ID."""

        ...


class DocumentLoaderService:
    """Load one owned file without exposing or reopening a native path.

    The public selector is an attachment ownership ID because it preserves the
    name and media type chosen for that Chat Attachment or Project Source. The
    service resolves the private File ID internally and relies on the
    repository's Scope-authorized verified stream for the immutable bytes.
    """

    def __init__(
        self,
        repository: DocumentFileRepository,
        *,
        limits: DocumentLoadLimits | None = None,
        loaders: Sequence[DocumentLoader] | None = None,
    ) -> None:
        """Build a closed, conflict-free loader registry."""

        if repository is None:
            raise TypeError("repository is required.")
        self._repository = repository
        self._limits = DocumentLoadLimits() if limits is None else limits
        if not isinstance(self._limits, DocumentLoadLimits):
            raise TypeError("limits must be DocumentLoadLimits.")
        selected_loaders = (
            self._default_loaders()
            if loaders is None
            else tuple(loaders)
        )
        if not selected_loaders:
            raise ValueError("At least one document loader is required.")
        self._routes = self._build_registry(selected_loaders)

    @property
    def limits(self) -> DocumentLoadLimits:
        """Return the immutable resource policy used for every load."""

        return self._limits

    @property
    def supported_routes(self) -> frozenset[DocumentRoute]:
        """Return the exact suffix and media-type pairs accepted here."""

        return frozenset(self._routes)

    @classmethod
    def default_supported_routes(cls) -> frozenset[DocumentRoute]:
        """Return the closed routes used by the default trusted loaders.

        Authorization and lifecycle layers use this exact set to distinguish
        document Sources from other attachment formats before any index job is
        scheduled.  Custom loader compositions must publish their own route
        set instead of assuming these defaults.
        """

        return frozenset(
            route
            for loader in cls._default_loaders()
            for route in loader.routes
        )

    def load(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> LoadedDocument:
        """Load one authorized ownership link into bounded raw structure."""

        if not isinstance(scope, AttachmentScope):
            raise DocumentValidationError("scope must be AttachmentScope.")
        try:
            safe_link_id = validate_attachment_id(link_id)
        except ValueError as error:
            raise DocumentValidationError(
                "Document ownership identifier is invalid."
            ) from error

        ownerships = self._list_ownerships(scope)
        matching_ownerships = tuple(
            item for item in ownerships if item.link_id == safe_link_id
        )
        if not matching_ownerships:
            raise DocumentNotFoundError(
                "Document ownership does not exist in this scope."
            )
        if len(matching_ownerships) != 1:
            raise DocumentReadError(
                "Document ownership metadata is ambiguous in this scope."
            )
        ownership = matching_ownerships[0]
        if ownership.scope != scope:
            raise DocumentReadError(
                "Document ownership metadata does not match its scope."
            )
        records = self._list_records(scope)
        matching_records = tuple(
            item for item in records if item.file_id == ownership.file_id
        )
        if not matching_records:
            raise DocumentReadError(
                "Document metadata is incomplete in this scope."
            )
        if len(matching_records) != 1:
            raise DocumentReadError(
                "Document metadata is ambiguous in this scope."
            )
        record = matching_records[0]
        source = DocumentSource(
            scope=scope,
            link_id=ownership.link_id,
            file_id=ownership.file_id,
            file_name=ownership.file_name,
            media_type=ownership.media_type,
            size_bytes=record.size_bytes,
        )
        if source.size_bytes > self._limits.max_source_bytes:
            raise DocumentTooLargeError(
                "Document source exceeds the configured byte limit."
            )
        route = (
            PurePath(source.file_name).suffix.casefold(),
            source.media_type,
        )
        registration = self._routes.get(route)
        if registration is None:
            raise DocumentUnsupportedFormatError(
                "Document format is not supported by a trusted loader."
            )
        loader, loader_id, loader_version = registration

        # Metadata resolution and verified open are separate calls in today's
        # repository contract. The open reauthorizes Scope + File ID, so a
        # concurrent deletion fails closed. If linearizable metadata-and-bytes
        # snapshots become necessary, the repository contract must expose one
        # combined operation rather than adding a service-side check/open race.
        snapshot = self._read_verified_snapshot(scope, source)
        try:
            result = loader.load(source, snapshot, self._limits)
            self._validate_loader_result(
                loader_id,
                loader_version,
                source,
                result,
            )
        except DocumentError:
            raise
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document parsing exceeded a safe resource limit."
            ) from error
        except Exception as error:
            raise DocumentLoadFailedError(
                "Document loader failed without a safe typed result."
            ) from error
        return result

    def _list_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """Translate repository failures while retaining no native details."""

        try:
            ownerships = self._repository.list_file_ownerships(scope)
        except AttachmentNotFoundError as error:
            raise DocumentNotFoundError(
                "Document ownership does not exist in this scope."
            ) from error
        except (
            AttachmentStorageError,
            AttachmentValidationError,
            OSError,
        ) as error:
            raise DocumentReadError(
                "Document ownership metadata could not be read safely."
            ) from error
        except Exception as error:
            raise DocumentReadError(
                "Document ownership metadata could not be read safely."
            ) from error
        if not isinstance(ownerships, tuple) or not all(
            isinstance(item, FileOwnership) for item in ownerships
        ):
            raise DocumentReadError(
                "Document ownership metadata has an invalid repository shape."
            )
        return ownerships

    def _list_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """Read canonical metadata through the repository abstraction."""

        try:
            records = self._repository.list_file_records(scope)
        except AttachmentNotFoundError as error:
            raise DocumentNotFoundError(
                "Document source does not exist in this scope."
            ) from error
        except (
            AttachmentStorageError,
            AttachmentValidationError,
            OSError,
        ) as error:
            raise DocumentReadError(
                "Document metadata could not be read safely."
            ) from error
        except Exception as error:
            raise DocumentReadError(
                "Document metadata could not be read safely."
            ) from error
        if not isinstance(records, tuple) or not all(
            isinstance(item, OriginalFileMetadata) for item in records
        ):
            raise DocumentReadError(
                "Document metadata has an invalid repository shape."
            )
        return records

    def _read_verified_snapshot(
        self,
        scope: AttachmentScope,
        source: DocumentSource,
    ) -> bytes:
        """Read the verified stream with short-read and exact-size handling."""

        chunks: list[bytes] = []
        total = 0
        try:
            with self._repository.open_verified_file(
                scope,
                source.file_id,
            ) as stream:
                while True:
                    remaining = self._limits.max_source_bytes + 1 - total
                    if remaining <= 0:
                        raise DocumentTooLargeError(
                            "Document source exceeds the configured byte limit."
                        )
                    chunk = stream.read(min(_READ_BUFFER_BYTES, remaining))
                    if not isinstance(chunk, bytes):
                        raise DocumentReadError(
                            "Verified document data returned an invalid byte stream."
                        )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > self._limits.max_source_bytes:
                        raise DocumentTooLargeError(
                            "Document source exceeds the configured byte limit."
                        )
                    if total > source.size_bytes:
                        raise DocumentReadError(
                            "Verified document size exceeds its metadata."
                        )
        except DocumentError:
            raise
        except AttachmentNotFoundError as error:
            raise DocumentNotFoundError(
                "Document source does not exist in this scope."
            ) from error
        except (AttachmentStorageError, AttachmentValidationError) as error:
            raise DocumentReadError(
                "Document bytes could not be verified safely."
            ) from error
        except OSError as error:
            raise DocumentReadError(
                "Document bytes could not be read safely."
            ) from error
        except (MemoryError, RecursionError) as error:
            raise DocumentContentLimitError(
                "Document reading exceeded a safe resource limit."
            ) from error
        except Exception as error:
            raise DocumentReadError(
                "Document bytes could not be read safely."
            ) from error
        if total != source.size_bytes:
            raise DocumentReadError(
                "Verified document size does not match its metadata."
            )
        try:
            return b"".join(chunks)
        except MemoryError as error:
            raise DocumentContentLimitError(
                "Document reading exceeded a safe resource limit."
            ) from error

    def _validate_loader_result(
        self,
        loader_id: str,
        loader_version: str,
        source: DocumentSource,
        result: object,
    ) -> None:
        """Reject adapters that return data for another source or policy."""

        if not isinstance(result, LoadedDocument):
            raise DocumentValidationError(
                "Document loader returned an invalid result."
            )
        if result.source != source:
            raise DocumentValidationError(
                "Document loader changed the authorized source identity."
            )
        if result.limits != self._limits:
            raise DocumentValidationError(
                "Document loader changed the active resource policy."
            )
        if (
            result.loader_id != loader_id
            or result.loader_version != loader_version
        ):
            raise DocumentValidationError(
                "Document loader returned inconsistent version metadata."
            )

    @staticmethod
    def _build_registry(
        loaders: Sequence[DocumentLoader],
    ) -> dict[DocumentRoute, tuple[DocumentLoader, str, str]]:
        """Validate and index a closed set of non-overlapping routes."""

        registry: dict[
            DocumentRoute,
            tuple[DocumentLoader, str, str],
        ] = {}
        for loader in loaders:
            loader_id = loader.loader_id
            loader_version = loader.loader_version
            if (
                not isinstance(loader_id, str)
                or _LOADER_ID_PATTERN.fullmatch(loader_id) is None
                or not isinstance(loader_version, str)
                or _LOADER_VERSION_PATTERN.fullmatch(loader_version) is None
            ):
                raise ValueError("Document loader identity is invalid.")
            routes = loader.routes
            if not isinstance(routes, frozenset) or not routes:
                raise ValueError("Document loader routes must be a non-empty frozenset.")
            for route in routes:
                if (
                    not isinstance(route, tuple)
                    or len(route) != 2
                    or not all(isinstance(value, str) for value in route)
                ):
                    raise ValueError("Document loader route is invalid.")
                suffix, media_type = route
                try:
                    validate_media_type(media_type)
                except ValueError as error:
                    raise ValueError(
                        "Document loader media type is invalid."
                    ) from error
                if (
                    not suffix.startswith(".")
                    or suffix != suffix.casefold()
                    or any(character in suffix for character in ("/", "\\"))
                ):
                    raise ValueError("Document loader suffix is invalid.")
                if route in registry:
                    raise ValueError("Document loader routes must be unique.")
                registry[route] = (loader, loader_id, loader_version)
        return registry

    @staticmethod
    def _default_loaders() -> tuple[DocumentLoader, ...]:
        """Create the fixed built-in adapters without import-time side effects."""

        from .docx import DocxDocumentLoader
        from .pdf import PdfDocumentLoader
        from .text import TextDocumentLoader

        return (
            TextDocumentLoader(),
            PdfDocumentLoader(),
            DocxDocumentLoader(),
        )
