"""Coordinate attachment ownership without exposing repository paths."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO

from .domain import (
    AttachmentItem,
    AttachmentScope,
    AttachmentState,
    DerivedFileRelation,
    FileOrigin,
    FileCatalogSnapshot,
    FileOwnership,
    OriginalFileMetadata,
    validate_file_id,
)
from .exceptions import AttachmentValidationError
from .repository import AttachmentRepository


class AttachmentService:
    """Apply owner-aware application rules around an attachment repository.

    File paths enter only through ``stage_files`` at the trusted Backend
    boundary. All reads use an opaque file ID and a validated owner scope, so
    callers cannot turn this service into a general filesystem reader.
    """

    def __init__(self, repository: AttachmentRepository) -> None:
        """Store the single repository that owns lifecycle transactions."""

        if repository is None:
            raise TypeError("repository is required.")
        self._repository = repository

    @property
    def max_file_bytes(self) -> int:
        """Return the repository's byte limit for newly imported files."""

        return self._repository.max_file_bytes

    @property
    def max_file_count(self) -> int:
        """Return the repository's active-file limit for each scope."""

        return self._repository.max_file_count

    def list_state(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str] = (),
    ) -> AttachmentState:
        """Return one scope's renderer-safe mutable attachment state."""

        return self._repository.list_state(scope, tuple(referenced_ids))

    def stage_files(
        self,
        scope: AttachmentScope,
        source_paths: Sequence[Path],
        referenced_ids: Iterable[str] = (),
        *,
        origin: FileOrigin = "local_import",
        cancel_requested: Callable[[], bool] | None = None,
    ) -> AttachmentState:
        """Atomically import trusted native selections into one owner scope."""

        if isinstance(source_paths, (str, bytes)):
            raise AttachmentValidationError(
                "Attachment sources must be a sequence of file paths."
            )
        canonical_paths = tuple(source_paths)
        canonical_references = tuple(referenced_ids)
        if origin == "local_import" and cancel_requested is None:
            # Older injected test doubles implement the original lifecycle
            # surface. Keeping the default call shape preserves compatibility
            # while the concrete repository supports the richer contract.
            return self._repository.stage_files(
                scope,
                canonical_paths,
                canonical_references,
            )
        return self._repository.stage_files(
            scope,
            canonical_paths,
            canonical_references,
            origin=origin,
            cancel_requested=cancel_requested,
        )

    def remove(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> AttachmentState:
        """Remove one ready attachment without revealing its stored blob."""

        return self._repository.remove(scope, attachment_id)

    def claim_chat(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> tuple[AttachmentItem, ...]:
        """Reserve one immutable set of Chat drafts for a generation request."""

        return self._repository.claim_chat(scope, tuple(attachment_ids))

    def release_chat_claims(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Release exactly the reservations owned by an abandoned request."""

        return self._repository.release_chat_claims(
            scope,
            tuple(attachment_ids),
        )

    def mark_committed(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Finalize file ownership after the canonical owner commits."""

        return self._repository.mark_committed(
            scope,
            tuple(attachment_ids),
        )

    def reconcile(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str],
    ) -> AttachmentState:
        """Repair one scope from its canonical owner references."""

        return self._repository.reconcile(scope, tuple(referenced_ids))

    def reconcile_owners(
        self,
        *,
        chat_ids: Iterable[str],
        project_ids: Iterable[str],
    ) -> None:
        """Remove namespaces that no longer have a canonical owner."""

        self._repository.reconcile_owners(
            chat_ids=tuple(chat_ids),
            project_ids=tuple(project_ids),
        )

    def list_file_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """List canonical file records authorized for one owner scope."""

        return self._repository.list_file_records(scope)

    def list_file_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """List path-free attachment or source links in one owner scope."""

        return self._repository.list_file_ownerships(scope)

    def snapshot_files(self, scope: AttachmentScope) -> FileCatalogSnapshot:
        """Return one atomic path-free file authorization snapshot."""

        return self._repository.snapshot_files(scope)

    def snapshot_file(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> FileCatalogSnapshot:
        """Return one atomic path-free ownership/original snapshot."""

        return self._repository.snapshot_file(scope, attachment_id)

    def _promote_chat_attachment(
        self,
        chat_scope: AttachmentScope,
        project_scope: AttachmentScope,
        attachment_id: str,
    ) -> FileOwnership:
        """Expose the internal copy primitive to an authority coordinator.

        Callers must first prove the Chat owns the committed attachment and is
        canonically assigned to the target Project.  Keeping this method
        private prevents the generic Attachment service from becoming an
        authorization bypass.
        """

        return self._repository._promote_chat_attachment(
            chat_scope,
            project_scope,
            attachment_id,
        )

    def list_derived_relations(
        self,
        scope: AttachmentScope,
    ) -> tuple[DerivedFileRelation, ...]:
        """List generated-file relationships retained by one owner scope."""

        return self._repository.list_derived_relations(scope)

    def register_derived_relation(
        self,
        scope: AttachmentScope,
        relation: DerivedFileRelation,
    ) -> None:
        """Persist one derived relationship after enforcing exact ownership."""

        if not isinstance(relation, DerivedFileRelation):
            raise AttachmentValidationError(
                "Derived file relationship is invalid."
            )
        if relation.scope != scope:
            raise AttachmentValidationError(
                "Derived relation scope does not match its owner."
            )
        self._repository.register_derived_relation(scope, relation)

    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> AbstractContextManager[BinaryIO]:
        """Open verified bytes by opaque ID for a trusted loader."""

        try:
            safe_file_id = validate_file_id(file_id)
        except ValueError as error:
            raise AttachmentValidationError(
                "File identifier is invalid."
            ) from error
        return self._repository.open_verified_file(
            scope,
            safe_file_id,
        )

    def delete_owner(
        self,
        scope: AttachmentScope,
        delete_owner: Callable[[], None],
    ) -> None:
        """Delegate owner deletion to the repository's rollback boundary."""

        self._repository.delete_scope_with(scope, delete_owner)

    def delete_owners(
        self,
        scopes: Sequence[AttachmentScope],
        delete_owners: Callable[[], None],
    ) -> None:
        """Delete several owners through one repository rollback transaction."""

        canonical_scopes = tuple(scopes)
        if not canonical_scopes:
            raise ValueError("At least one attachment owner scope is required.")
        if not all(
            isinstance(scope, AttachmentScope) for scope in canonical_scopes
        ):
            raise ValueError("Every attachment owner scope must be canonical.")
        if len(canonical_scopes) != len(set(canonical_scopes)):
            raise ValueError("Attachment owner scopes must be unique.")
        self._repository.delete_scopes_with(
            canonical_scopes,
            delete_owners,
        )

    def delete_chat_owner(
        self,
        chat_id: str,
        delete_owner: Callable[[], None],
    ) -> None:
        """Delete one Chat and its attachments through one rollback boundary."""

        self.delete_owner(
            AttachmentScope(kind="chat", id=chat_id),
            delete_owner,
        )

    def delete_project_owner(
        self,
        project_id: str,
        delete_owner: Callable[[], None],
        *,
        linked_chat_ids: Iterable[str],
    ) -> None:
        """Delete a Project, linked Chats, and files in one rollback boundary."""

        if isinstance(linked_chat_ids, (str, bytes)):
            raise ValueError("linked_chat_ids must be an iterable of Chat IDs.")
        scopes = (
            AttachmentScope(kind="project", id=project_id),
            *(
                AttachmentScope(kind="chat", id=chat_id)
                for chat_id in linked_chat_ids
            ),
        )
        self.delete_owners(
            scopes,
            delete_owner,
        )

    def close(self) -> None:
        """Release the repository's process-owned resources."""

        self._repository.close()
