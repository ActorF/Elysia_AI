"""Define the persistence contract for attachment and canonical file storage."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO, Protocol

from .domain import (
    AttachmentItem,
    AttachmentScope,
    AttachmentState,
    DerivedFileRelation,
    FileOrigin,
    FileCatalogSnapshot,
    FileOwnership,
    OriginalFileMetadata,
)


class AttachmentRepository(Protocol):
    """Persist attachment lifecycles behind one path-private boundary.

    The lifecycle members intentionally match the established attachment
    store. The file-record members extend that boundary for trusted loaders
    without exposing a filesystem path to the application or Renderer.
    """

    @property
    def max_file_bytes(self) -> int:
        """Return the byte limit applied to each newly imported original."""

        ...

    @property
    def max_file_count(self) -> int:
        """Return the active-file limit applied to each ownership scope."""

        ...

    def list_state(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str] = (),
    ) -> AttachmentState:
        """Return renderer-safe ready items after repository reconciliation."""

        ...

    def stage_files(
        self,
        scope: AttachmentScope,
        source_paths: Sequence[Path],
        referenced_ids: Iterable[str] = (),
        *,
        origin: FileOrigin = "local_import",
        cancel_requested: Callable[[], bool] | None = None,
    ) -> AttachmentState:
        """Import one atomic batch through the trusted native-path boundary."""

        ...

    def remove(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> AttachmentState:
        """Remove one mutable item from its exact ownership scope."""

        ...

    def remove_project_source(
        self,
        scope: AttachmentScope,
        link_id: str,
        expected_snapshot_fingerprint: str,
    ) -> FileCatalogSnapshot:
        """Remove one exact Project Source from an expected catalog snapshot."""

        ...

    def claim_chat(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> tuple[AttachmentItem, ...]:
        """Atomically reserve ready Chat attachments for one request."""

        ...

    def release_chat_claims(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Release reservations that did not reach canonical Chat commit."""

        ...

    def mark_committed(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Finalize ownership after its canonical owner has committed."""

        ...

    def delete_scope(self, scope: AttachmentScope) -> None:
        """Delete one exact ownership namespace and its unshared data."""

        ...

    def delete_scope_with(
        self,
        scope: AttachmentScope,
        delete_owner: Callable[[], None],
    ) -> None:
        """Coordinate owner deletion with repository rollback and cleanup."""

        ...

    def delete_scopes_with(
        self,
        scopes: Sequence[AttachmentScope],
        delete_owners: Callable[[], None],
    ) -> None:
        """Delete several owner scopes as one recoverable owner transaction."""

        ...

    def reconcile(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str],
    ) -> AttachmentState:
        """Repair one scope against its canonical persisted references."""

        ...

    def reconcile_owners(
        self,
        *,
        chat_ids: Iterable[str],
        project_ids: Iterable[str],
    ) -> None:
        """Remove namespaces whose canonical Chat or Project no longer exists."""

        ...

    def list_file_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """List canonical originals visible through one validated scope."""

        ...

    def list_file_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """List path-free ownership links in one validated scope."""

        ...

    def snapshot_files(self, scope: AttachmentScope) -> FileCatalogSnapshot:
        """Return originals and ownership links from one manifest revision."""

        ...

    def snapshot_file(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> FileCatalogSnapshot:
        """Return one exact ownership and original from one manifest read."""

        ...

    def _promote_chat_attachment(
        self,
        chat_scope: AttachmentScope,
        project_scope: AttachmentScope,
        attachment_id: str,
    ) -> FileOwnership:
        """Copy one committed Chat attachment for an authorizing coordinator.

        This primitive cannot prove the Chat belongs to the target Project, so
        it intentionally stays outside the public Attachment API.  Only a
        coordinator that resolves the canonical Chat-to-Project relationship
        may call it.
        """

        ...

    def list_derived_relations(
        self,
        scope: AttachmentScope,
    ) -> tuple[DerivedFileRelation, ...]:
        """List derived-file relationships owned by one validated scope."""

        ...

    def register_derived_relation(
        self,
        scope: AttachmentScope,
        relation: DerivedFileRelation,
    ) -> None:
        """Persist one path-free derived relation inside its owner scope."""

        ...

    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> AbstractContextManager[BinaryIO]:
        """Open a hash-verified original for trusted Backend consumers only."""

        ...

    def close(self) -> None:
        """Release repository locks and other process-owned resources."""

        ...
