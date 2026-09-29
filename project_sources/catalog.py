"""Project exact attachment catalogs into authorized document sources.

These helpers form the shared, path-free projection boundary used by Project
Source answering and lifecycle orchestration.  Keeping the projection in one
module prevents those consumers from disagreeing about which attachment routes
belong to the text corpus or how its authorization fingerprint is calculated.
"""

from __future__ import annotations

from pathlib import PurePath

from attachments import (
    AttachmentScope,
    FileCatalogSnapshot,
    FileOwnership,
    MAX_FILE_CATALOG_ITEMS,
    OriginalFileMetadata,
)
from documents import DocumentRoute, DocumentSource

from .exceptions import (
    ProjectSourceAuthorizationError,
    ProjectSourceValidationError,
)


def snapshot_file_catalog(value: object) -> FileCatalogSnapshot:
    """Detach and revalidate one atomic attachment-catalog adapter result."""

    if type(value) is not FileCatalogSnapshot:
        raise ProjectSourceValidationError(
            "Project Source ownership snapshot is invalid."
        )
    if (
        type(value.originals) is not tuple
        or type(value.ownerships) is not tuple
        or len(value.originals) > MAX_FILE_CATALOG_ITEMS
        or len(value.ownerships) > MAX_FILE_CATALOG_ITEMS
    ):
        raise ProjectSourceValidationError(
            "Project Source ownership snapshot exceeds its safe limit."
        )
    try:
        scope = AttachmentScope(kind=value.scope.kind, id=value.scope.id)
        originals = tuple(
            OriginalFileMetadata(
                schema_version=item.schema_version,
                file_id=item.file_id,
                sha256=item.sha256,
                file_name=item.file_name,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
                origin=item.origin,
                imported_at=item.imported_at,
            )
            for item in value.originals
        )
        ownerships = tuple(
            FileOwnership(
                schema_version=item.schema_version,
                link_id=item.link_id,
                file_id=item.file_id,
                file_name=item.file_name,
                media_type=item.media_type,
                scope=AttachmentScope(
                    kind=item.scope.kind,
                    id=item.scope.id,
                ),
                role=item.role,
                imported_at=item.imported_at,
            )
            for item in value.ownerships
        )
        return FileCatalogSnapshot(
            schema_version=value.schema_version,
            scope=scope,
            originals=originals,
            ownerships=ownerships,
            snapshot_fingerprint=value.snapshot_fingerprint,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ProjectSourceValidationError(
            "Project Source ownership snapshot is invalid."
        ) from error


def sources_from_catalog(
    catalog: FileCatalogSnapshot,
    scope: AttachmentScope,
) -> tuple[DocumentSource, ...]:
    """Build exact Project document sources from one ownership snapshot."""

    if catalog.scope != scope or scope.kind != "project":
        raise ProjectSourceAuthorizationError(
            "Project Source ownership crosses the authorized Project."
        )
    originals = {item.file_id: item for item in catalog.originals}
    sources: list[DocumentSource] = []
    for ownership in catalog.ownerships:
        if ownership.role != "project_source" or ownership.scope != scope:
            raise ProjectSourceAuthorizationError(
                "Project Source ownership is not authorized."
            )
        original = originals.get(ownership.file_id)
        if original is None:
            raise ProjectSourceValidationError(
                "Project Source ownership has no immutable original."
            )
        sources.append(
            DocumentSource(
                scope=scope,
                link_id=ownership.link_id,
                file_id=ownership.file_id,
                file_name=ownership.file_name,
                media_type=ownership.media_type,
                size_bytes=original.size_bytes,
            )
        )
    return tuple(sorted(sources, key=lambda source: source.link_id))


def select_indexable_file_catalog(
    catalog: FileCatalogSnapshot,
    supported_routes: frozenset[DocumentRoute],
) -> FileCatalogSnapshot:
    """Return the exact catalog subset accepted by the document pipeline.

    Attachment storage also accepts images for UI and future vision features.
    Excluding routes without a trusted document loader keeps such ownerships
    from making the text corpus permanently stale.
    """

    ownerships = tuple(
        ownership
        for ownership in catalog.ownerships
        if (
            PurePath(ownership.file_name).suffix.casefold(),
            ownership.media_type,
        )
        in supported_routes
    )
    selected_file_ids = {ownership.file_id for ownership in ownerships}
    originals = tuple(
        original
        for original in catalog.originals
        if original.file_id in selected_file_ids
    )
    try:
        return FileCatalogSnapshot(
            schema_version=catalog.schema_version,
            scope=catalog.scope,
            originals=originals,
            ownerships=ownerships,
        )
    except (TypeError, ValueError) as error:
        raise ProjectSourceValidationError(
            "Indexable Project Source ownership is invalid."
        ) from error
