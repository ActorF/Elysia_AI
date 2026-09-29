"""Atomic, scope-isolated storage for local attachment blobs and drafts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_module
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import SpooledTemporaryFile, mkstemp
from threading import RLock
from typing import BinaryIO, Final, Literal, cast
from uuid import uuid4

from .domain import (
    FILE_CATALOG_SNAPSHOT_SCHEMA_VERSION,
    FILE_METADATA_SCHEMA_VERSION,
    MAX_JSON_SAFE_INTEGER,
    MAX_SOURCE_PATH_LENGTH,
    AttachmentItem,
    AttachmentScope,
    AttachmentState,
    DerivedFileRelation,
    FileCatalogSnapshot,
    FileOrigin,
    FileOwnership,
    OriginalFileMetadata,
    file_id_from_sha256,
    validate_attachment_id,
    validate_file_id,
    validate_file_name,
    validate_media_type,
    validate_sha256,
)
from .exceptions import (
    AttachmentConflictError,
    AttachmentImportCancelledError,
    AttachmentNotFoundError,
    AttachmentStorageError,
    AttachmentValidationError,
)

DEFAULT_MAX_FILE_COUNT: Final = 10
ATTACHMENT_MANIFEST_SCHEMA_VERSION: Final = 2
LEGACY_ATTACHMENT_MANIFEST_SCHEMA_VERSION: Final = 1
_COPY_BUFFER_BYTES: Final = 1024 * 1024
_MAX_MANIFEST_BYTES: Final = 16 * 1024 * 1024
_MAX_STORED_FILE_BYTES: Final = 2_147_483_647
_VERIFIED_MEMORY_BYTES: Final = 8 * 1024 * 1024
_BLOB_SUFFIX: Final = ".blob"
_PROCESS_LOCK_FILE_NAME: Final = ".backend.lock"
_SCOPE_KINDS: Final[tuple[Literal["chat", "project"], ...]] = (
    "chat",
    "project",
)

# Storage still treats extensions only as closed routing hints and never parses
# or executes content. The separate ``documents`` layer rechecks the exact
# suffix/MIME pair and signature after a Scope-authorized verified read.
ALLOWED_ATTACHMENT_EXTENSIONS: Final[dict[str, str]] = {
    ".css": "text/css",
    ".csv": "text/csv",
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    ".gif": "image/gif",
    ".htm": "text/html",
    ".html": "text/html",
    ".ini": "text/plain",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript",
    ".jsx": "text/javascript",
    ".json": "application/json",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".py": "text/x-python",
    ".sql": "application/sql",
    ".toml": "application/toml",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".txt": "text/plain",
    ".webp": "image/webp",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}

_ManifestStatus = Literal["ready", "claimed", "committed"]
_PENDING_DELETE_PATTERN = re.compile(
    r"^\.(?P<scope_id>[A-Za-z0-9_-]+)\.delete-[0-9a-f]{32}\.pending$"
)
_COMMITTED_DELETE_PATTERN = re.compile(
    r"^\.(?P<scope_id>[A-Za-z0-9_-]+)\.delete-[0-9a-f]{32}\.tmp$"
)
# ``tempfile.mkstemp`` currently emits eight characters from this alphabet.
# Keeping that grammar closed prevents startup recovery from deleting an
# unrelated future-format file merely because its name happens to end in
# ``.tmp``.
_TEMPORARY_TOKEN_PATTERN: Final = r"[a-z0-9_]{8}"
_MANIFEST_TEMP_PATTERN = re.compile(
    rf"^\.manifest\.json\.{_TEMPORARY_TOKEN_PATTERN}\.tmp$"
)
_UPLOAD_TEMP_PATTERN = re.compile(
    rf"^\.upload-{_TEMPORARY_TOKEN_PATTERN}\.tmp$"
)


@dataclass(frozen=True, slots=True)
class _ManifestItem:
    """Persist one ownership link plus immutable canonical content metadata."""

    record_schema_version: Literal[1]
    attachment_id: str
    file_id: str
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    origin: FileOrigin
    imported_at: datetime
    link_file_name: str
    link_media_type: str
    linked_at: datetime
    role: Literal["chat_attachment", "project_source"]
    status: _ManifestStatus = "ready"

    def __post_init__(self) -> None:
        OriginalFileMetadata(
            schema_version=self.record_schema_version,
            file_id=self.file_id,
            sha256=self.sha256,
            file_name=self.file_name,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            origin=self.origin,
            imported_at=self.imported_at,
        )
        validate_attachment_id(self.attachment_id)
        if self.status not in ("ready", "claimed", "committed"):
            raise ValueError("Stored attachment status is invalid.")

    def to_public(self) -> AttachmentItem:
        """Expose metadata without internal digest or lifecycle state."""

        return AttachmentItem(
            attachment_id=self.attachment_id,
            file_name=self.link_file_name,
            media_type=self.link_media_type,
            size_bytes=self.size_bytes,
        )

    def to_original(self) -> OriginalFileMetadata:
        """Return path-free immutable metadata for this content object."""

        return OriginalFileMetadata(
            schema_version=self.record_schema_version,
            file_id=self.file_id,
            sha256=self.sha256,
            file_name=self.file_name,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            origin=self.origin,
            imported_at=self.imported_at,
        )

    def to_ownership(self, scope: AttachmentScope) -> FileOwnership:
        """Return the explicit Chat Attachment or Project Source link."""

        return FileOwnership(
            schema_version=self.record_schema_version,
            link_id=self.attachment_id,
            file_id=self.file_id,
            file_name=self.link_file_name,
            media_type=self.link_media_type,
            imported_at=self.linked_at,
            scope=scope,
            role=self.role,
        )


@dataclass(frozen=True, slots=True)
class _CopiedCandidate:
    temporary_path: Path
    file_id: str
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    origin: FileOrigin
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class _ManifestDocument:
    """Keep ownership links and future derived relations in one commit."""

    items: tuple[_ManifestItem, ...]
    derived: tuple[DerivedFileRelation, ...] = ()


@dataclass(frozen=True, slots=True)
class _ValidatedSource:
    """Pin the filesystem identity approved by source-path validation."""

    path: Path
    device: int
    inode: int


class JsonAttachmentStore:
    """Store opaque blobs plus one atomic draft manifest per scope.

    Chat manifests retain integrity metadata for both drafts and committed
    blobs. Project items stay available until explicitly removed or their
    scope is deleted.
    """

    def __init__(
        self,
        base_dir: Path,
        max_file_bytes: int,
        max_file_count: int = DEFAULT_MAX_FILE_COUNT,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        for value, field_name, maximum in (
            (
                max_file_bytes,
                "max_file_bytes",
                _MAX_STORED_FILE_BYTES,
            ),
            (max_file_count, "max_file_count", MAX_JSON_SAFE_INTEGER),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > maximum
            ):
                raise ValueError(
                    f"{field_name} must be a positive JSON-safe integer."
                )
        self._base_dir = Path(base_dir).absolute()
        self._max_file_bytes = max_file_bytes
        self._max_file_count = max_file_count
        self._clock: Callable[[], datetime] = (
            (lambda: datetime.now(timezone.utc))
            if clock is None
            else clock
        )
        if not callable(self._clock):
            raise TypeError("clock must be callable.")
        self._lock = RLock()
        self._process_lock_fd: int | None = None
        self._prepare_base_directory()
        self._acquire_process_lock()
        try:
            self._clean_startup_temporary_entries()
            self._migrate_legacy_manifests()
            self._release_startup_claims()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release this Backend's exclusive attachment-store lease."""

        with self._lock:
            descriptor = self._process_lock_fd
            if descriptor is None:
                return
            self._process_lock_fd = None
            try:
                self._unlock_process_descriptor(descriptor)
            finally:
                os.close(descriptor)

    def __del__(self) -> None:
        """Best-effort process-lock release for short-lived test stores."""

        try:
            self.close()
        except BaseException:
            pass

    @property
    def max_file_bytes(self) -> int:
        """Return the configured byte limit for each staged file."""

        return self._max_file_bytes

    @property
    def max_file_count(self) -> int:
        """Return the configured active-file limit for each scope."""

        return self._max_file_count

    def list_state(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str] = (),
    ) -> AttachmentState:
        """Reconcile and return renderer-safe ready items in one scope."""

        with self._lock:
            return self._reconcile_locked(
                scope,
                referenced_ids,
                release_claims=False,
            )

    def list_file_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """List each content object once without exposing its storage path."""

        self._require_scope(scope)
        with self._lock:
            document = self._load_manifest_document(scope)
            by_file_id: dict[str, OriginalFileMetadata] = {}
            for item in document.items:
                by_file_id.setdefault(item.file_id, item.to_original())
            return tuple(by_file_id.values())

    def list_file_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """List explicit Chat Attachment or Project Source ownership links."""

        self._require_scope(scope)
        with self._lock:
            return tuple(
                item.to_ownership(scope)
                for item in self._load_manifest_document(scope).items
            )

    def snapshot_files(self, scope: AttachmentScope) -> FileCatalogSnapshot:
        """Return originals and ownership links from one manifest read."""

        self._require_scope(scope)
        with self._lock:
            document = self._load_manifest_document(scope)
            originals_by_id: dict[str, OriginalFileMetadata] = {}
            for item in document.items:
                originals_by_id.setdefault(item.file_id, item.to_original())
            return FileCatalogSnapshot(
                schema_version=FILE_CATALOG_SNAPSHOT_SCHEMA_VERSION,
                scope=scope,
                originals=tuple(originals_by_id.values()),
                ownerships=tuple(
                    item.to_ownership(scope) for item in document.items
                ),
            )

    def snapshot_file(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> FileCatalogSnapshot:
        """Return one exact ownership without scanning it into a public list.

        Long-lived Chats may retain more committed history attachments than a
        bounded full-catalog consumer accepts. Promotion needs only the one
        canonical link, so this atomic projection avoids making old history a
        denial of service while preserving same-read original metadata.
        """

        self._require_scope(scope)
        try:
            safe_id = validate_attachment_id(attachment_id)
        except ValueError as error:
            raise AttachmentValidationError(
                "Attachment identifier is invalid."
            ) from error
        with self._lock:
            document = self._load_manifest_document(scope)
            item = next(
                (
                    candidate
                    for candidate in document.items
                    if candidate.attachment_id == safe_id
                ),
                None,
            )
            if item is None:
                raise AttachmentNotFoundError(
                    "Attachment does not exist in this scope."
                )
            return FileCatalogSnapshot(
                schema_version=FILE_CATALOG_SNAPSHOT_SCHEMA_VERSION,
                scope=scope,
                originals=(item.to_original(),),
                ownerships=(item.to_ownership(scope),),
            )

    def _promote_chat_attachment(
        self,
        chat_scope: AttachmentScope,
        project_scope: AttachmentScope,
        attachment_id: str,
    ) -> FileOwnership:
        """Copy one committed Chat attachment into a Project Source scope.

        Promotion creates a new ownership link and preserves the original Chat
        link.  The target manifest is the commit point, so an interrupted copy
        can leave at most an unreferenced content-addressed blob reclaimed by
        normal reconciliation; it can never publish a link with missing bytes.
        """

        self._require_chat_scope(chat_scope)
        self._require_scope(project_scope)
        if project_scope.kind != "project":
            raise AttachmentValidationError(
                "Attachment promotion requires a Project target."
            )
        try:
            safe_id = validate_attachment_id(attachment_id)
        except ValueError as error:
            raise AttachmentValidationError(
                "Attachment identifier is invalid."
            ) from error

        with self._lock:
            source_document = self._load_manifest_document(chat_scope)
            source = next(
                (
                    item
                    for item in source_document.items
                    if item.attachment_id == safe_id
                ),
                None,
            )
            if source is None:
                raise AttachmentNotFoundError(
                    "Attachment does not exist in this Chat."
                )
            # A ready or claimed draft is not yet part of canonical Chat
            # history.  Requiring committed ownership prevents an explicit
            # promotion click from racing message submission or cancellation.
            if source.status != "committed":
                raise AttachmentConflictError(
                    "Only a committed Chat attachment can be promoted."
                )

            target_document = self._load_manifest_document(project_scope)
            existing = next(
                (
                    item
                    for item in target_document.items
                    if item.file_id == source.file_id
                ),
                None,
            )
            if existing is not None:
                self._verify_blob(
                    self._original_blob_path(project_scope, existing.file_id),
                    existing,
                )
                return existing.to_ownership(project_scope)
            if len(target_document.items) >= self._max_file_count:
                raise AttachmentValidationError(
                    "This Project Source scope has reached its file limit."
                )

            target_path = self._original_blob_path(
                project_scope,
                source.file_id,
            )
            source_path = self._original_blob_path(chat_scope, source.file_id)
            descriptor, temporary_name = mkstemp(
                dir=target_path.parent,
                prefix=".upload-",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            published = False
            try:
                digest = hashlib.sha256()
                copied = 0
                with self._verified_blob_stream(source_path, source) as input_file:
                    with os.fdopen(descriptor, "wb") as output_file:
                        descriptor = -1
                        while True:
                            chunk = input_file.read(_COPY_BUFFER_BYTES)
                            if not chunk:
                                break
                            output_file.write(chunk)
                            digest.update(chunk)
                            copied += len(chunk)
                        output_file.flush()
                        os.fsync(output_file.fileno())
                if copied != source.size_bytes or digest.hexdigest() != source.sha256:
                    raise AttachmentStorageError(
                        "Promoted attachment data failed integrity validation."
                    )
                if target_path.exists() or target_path.is_symlink():
                    self._verify_blob(target_path, source)
                    self._unlink_required(temporary_path)
                else:
                    os.replace(temporary_path, target_path)
                    published = True
                    self._verify_blob(target_path, source)

                linked_at = self._utc_now()
                promoted = replace(
                    source,
                    attachment_id=f"attachment_{uuid4().hex}",
                    linked_at=linked_at,
                    role="project_source",
                    status="ready",
                )
                # Build and validate the public return value before committing
                # the manifest.  Once the manifest replace succeeds, no later
                # return-value construction failure may delete its live blob.
                promoted_ownership = promoted.to_ownership(project_scope)
                self._write_manifest(
                    project_scope,
                    (*target_document.items, promoted),
                    derived=target_document.derived,
                )
                return promoted_ownership
            except (AttachmentValidationError, AttachmentConflictError):
                if published:
                    self._best_effort_unlink(target_path)
                raise
            except Exception as error:
                if published:
                    self._best_effort_unlink(target_path)
                if isinstance(error, AttachmentStorageError):
                    raise
                raise AttachmentStorageError(
                    "The Chat attachment could not be promoted safely."
                ) from error
            finally:
                if descriptor != -1:
                    os.close(descriptor)
                self._best_effort_unlink(temporary_path)

    def list_derived_relations(
        self,
        scope: AttachmentScope,
    ) -> tuple[DerivedFileRelation, ...]:
        """List path-free derived-data relationships for one owner scope."""

        self._require_scope(scope)
        with self._lock:
            return self._load_manifest_document(scope).derived

    def register_derived_relation(
        self,
        scope: AttachmentScope,
        relation: DerivedFileRelation,
    ) -> None:
        """Persist one unique derived-to-original relation without content I/O.

        This store records only the relationship. Raw document loaders return
        in-memory structure; a later cleaning/chunking pipeline will publish
        derived bytes through a separate bounded writer.
        """

        self._require_scope(scope)
        if not isinstance(relation, DerivedFileRelation):
            raise AttachmentValidationError(
                "Derived file relationship is invalid."
            )
        if relation.scope != scope:
            raise AttachmentValidationError(
                "Derived file relationship does not match its owner scope."
            )
        with self._lock:
            document = self._load_manifest_document(scope)
            original_ids = {item.file_id for item in document.items}
            if relation.original_file_id not in original_ids:
                raise AttachmentNotFoundError(
                    "Derived file relationship has no original in this scope."
                )
            if any(
                existing.derived_file_id == relation.derived_file_id
                for existing in document.derived
            ):
                if relation in document.derived:
                    return
                raise AttachmentConflictError(
                    "Derived file identifier is already related in this scope."
                )
            self._write_manifest(
                scope,
                document.items,
                derived=(*document.derived, relation),
            )

    @contextmanager
    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> Iterator[BinaryIO]:
        """Yield a verified original by owner scope and opaque content ID."""

        self._require_scope(scope)
        try:
            safe_file_id = validate_file_id(file_id)
        except ValueError as error:
            raise AttachmentValidationError("File identifier is invalid.") from error
        with self._lock:
            document = self._load_manifest_document(scope)
            item = next(
                (
                    candidate
                    for candidate in document.items
                    if candidate.file_id == safe_file_id
                ),
                None,
            )
            if item is None:
                raise AttachmentNotFoundError(
                    "File does not exist in this owner scope."
                )
            with self._verified_blob_stream(
                self._original_blob_path(scope, safe_file_id),
                item,
            ) as stream:
                yield stream

    def stage_files(
        self,
        scope: AttachmentScope,
        source_paths: Sequence[Path],
        referenced_ids: Iterable[str] = (),
        *,
        origin: FileOrigin = "local_import",
        cancel_requested: Callable[[], bool] | None = None,
    ) -> AttachmentState:
        """Atomically add a batch, deduplicating equal content in the scope.

        Sources are copied and hashed into scoped temporary files before their
        opaque blobs are published. The manifest is replaced last, allowing an
        ordinary failure to remove the whole batch and startup reconciliation
        to discard any blobs left by a process interruption.
        """

        self._require_scope(scope)
        if isinstance(source_paths, (str, bytes)):
            raise AttachmentValidationError(
                "Attachment sources must be a sequence of file paths."
            )
        paths = tuple(Path(path) for path in source_paths)
        if not paths:
            return self.list_state(scope, referenced_ids)
        if len(paths) > self._max_file_count:
            raise AttachmentValidationError(
                "Too many files were selected for one attachment batch."
            )
        if origin not in ("local_import", "legacy_migration", "generated"):
            raise AttachmentValidationError("File origin is invalid.")
        if cancel_requested is not None and not callable(cancel_requested):
            raise AttachmentValidationError(
                "File import cancellation callback is invalid."
            )

        with self._lock:
            self._reconcile_locked(
                scope,
                referenced_ids,
                release_claims=False,
            )
            document = self._load_manifest_document(scope)
            items = list(document.items)
            active_file_ids = {
                item.file_id
                for item in items
                if item.status != "committed"
            }
            stored_file_ids = {item.file_id for item in items}
            new_blob_paths: list[Path] = []
            candidates: list[_CopiedCandidate] = []
            try:
                for source_path in paths:
                    self._raise_if_import_cancelled(cancel_requested)
                    candidate = self._copy_candidate(
                        scope,
                        source_path,
                        origin=origin,
                        cancel_requested=cancel_requested,
                    )
                    candidates.append(candidate)
                    if candidate.file_id in active_file_ids:
                        self._unlink_required(candidate.temporary_path)
                        candidates.remove(candidate)
                        continue
                    active_count = sum(
                        item.status != "committed" for item in items
                    )
                    if active_count >= self._max_file_count:
                        raise AttachmentValidationError(
                            "This attachment scope has reached its file limit."
                        )
                    attachment_id = f"attachment_{uuid4().hex}"
                    final_path = self._original_blob_path(
                        scope,
                        candidate.file_id,
                    )
                    canonical_original: _ManifestItem | None = None
                    if candidate.file_id in stored_file_ids:
                        existing = next(
                            item
                            for item in items
                            if item.file_id == candidate.file_id
                        )
                        self._verify_blob(final_path, existing)
                        self._unlink_required(candidate.temporary_path)
                        canonical_original = existing
                    elif final_path.exists() or final_path.is_symlink():
                        # A complete content object may remain after a crash
                        # between publication and manifest commit. Verify and
                        # reuse it instead of multiplying identical bytes.
                        probe = self._candidate_manifest_item(
                            scope,
                            attachment_id,
                            candidate,
                        )
                        self._verify_blob(final_path, probe)
                        self._unlink_required(candidate.temporary_path)
                        new_blob_paths.append(final_path)
                    else:
                        os.replace(candidate.temporary_path, final_path)
                        new_blob_paths.append(final_path)
                    candidates.remove(candidate)
                    item = _ManifestItem(
                        record_schema_version=FILE_METADATA_SCHEMA_VERSION,
                        attachment_id=attachment_id,
                        file_id=candidate.file_id,
                        file_name=(
                            candidate.file_name
                            if canonical_original is None
                            else canonical_original.file_name
                        ),
                        media_type=(
                            candidate.media_type
                            if canonical_original is None
                            else canonical_original.media_type
                        ),
                        size_bytes=candidate.size_bytes,
                        sha256=candidate.sha256,
                        origin=(
                            candidate.origin
                            if canonical_original is None
                            else canonical_original.origin
                        ),
                        imported_at=(
                            candidate.imported_at
                            if canonical_original is None
                            else canonical_original.imported_at
                        ),
                        link_file_name=candidate.file_name,
                        link_media_type=candidate.media_type,
                        linked_at=candidate.imported_at,
                        role=self._role_for_scope(scope),
                    )
                    items.append(item)
                    active_file_ids.add(item.file_id)
                    stored_file_ids.add(item.file_id)

                self._raise_if_import_cancelled(cancel_requested)
                self._write_manifest(
                    scope,
                    items,
                    derived=document.derived,
                )
            except (
                AttachmentValidationError,
                AttachmentConflictError,
            ):
                self._roll_back_stage(candidates, new_blob_paths)
                raise
            except Exception as error:
                self._roll_back_stage(candidates, new_blob_paths)
                if isinstance(error, AttachmentStorageError):
                    raise
                raise AttachmentStorageError(
                    "The attachment batch could not be stored safely."
                ) from error
            return self._state(scope, items)

    def remove(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> AttachmentState:
        """Remove one unclaimed draft without exposing its blob path."""

        self._require_scope(scope)
        try:
            safe_id = validate_attachment_id(attachment_id)
        except ValueError as error:
            raise AttachmentValidationError(
                "Attachment identifier is invalid."
            ) from error
        with self._lock:
            document = self._load_manifest_document(scope)
            items = list(document.items)
            item = next(
                (entry for entry in items if entry.attachment_id == safe_id),
                None,
            )
            if item is None:
                raise AttachmentNotFoundError(
                    "Attachment does not exist in this scope."
                )
            if item.status != "ready":
                raise AttachmentConflictError(
                    "Attachment is already in use by a Chat request."
                )
            remaining = [entry for entry in items if entry is not item]
            remaining_file_ids = {entry.file_id for entry in remaining}
            remaining_derived = tuple(
                relation
                for relation in document.derived
                if relation.original_file_id in remaining_file_ids
            )
            blob_path = self._original_blob_path(scope, item.file_id)
            try:
                # The manifest is the ownership commit boundary. Publishing it
                # before deleting bytes means a crash can leave only a safe
                # orphan, never a live record whose sole content disappeared.
                self._write_manifest(
                    scope,
                    remaining,
                    derived=remaining_derived,
                )
                if item.file_id not in remaining_file_ids:
                    self._best_effort_unlink(blob_path)
            except AttachmentStorageError:
                raise
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment could not be removed safely."
                ) from error
            return self._state(scope, remaining)

    def remove_project_source(
        self,
        scope: AttachmentScope,
        link_id: str,
        expected_snapshot_fingerprint: str,
    ) -> FileCatalogSnapshot:
        """Remove one exact Project Source after a catalog-fingerprint guard.

        The manifest remains the ownership commit boundary: it is replaced
        before an unshared original blob is reclaimed.  A crash can therefore
        leave only an unreachable blob, never a live ownership whose bytes were
        deleted.  The expected fingerprint prevents a delayed lifecycle job
        from deleting a link after any other ownership mutation.
        """

        self._require_scope(scope)
        if scope.kind != "project":
            raise AttachmentValidationError(
                "Project Source removal requires a Project scope."
            )
        try:
            safe_id = validate_attachment_id(link_id)
            expected_fingerprint = validate_sha256(
                expected_snapshot_fingerprint
            )
        except (TypeError, ValueError) as error:
            raise AttachmentValidationError(
                "Project Source removal identity is invalid."
            ) from error

        with self._lock:
            document = self._load_manifest_document(scope)
            item = next(
                (
                    entry
                    for entry in document.items
                    if entry.attachment_id == safe_id
                ),
                None,
            )
            if item is None:
                raise AttachmentNotFoundError(
                    "Project Source does not exist in this scope."
                )
            ownership = item.to_ownership(scope)
            if ownership.role != "project_source" or ownership.scope != scope:
                raise AttachmentValidationError(
                    "Ownership link is not an exact Project Source."
                )

            originals_by_id: dict[str, OriginalFileMetadata] = {}
            for existing in document.items:
                originals_by_id.setdefault(
                    existing.file_id,
                    existing.to_original(),
                )
            current_snapshot = FileCatalogSnapshot(
                schema_version=FILE_CATALOG_SNAPSHOT_SCHEMA_VERSION,
                scope=scope,
                originals=tuple(originals_by_id.values()),
                ownerships=tuple(
                    existing.to_ownership(scope)
                    for existing in document.items
                ),
            )
            if current_snapshot.snapshot_fingerprint != expected_fingerprint:
                raise AttachmentConflictError(
                    "Project Source catalog changed before removal."
                )

            remaining = tuple(
                existing for existing in document.items if existing is not item
            )
            remaining_file_ids = {
                existing.file_id for existing in remaining
            }
            remaining_derived = tuple(
                relation
                for relation in document.derived
                if relation.original_file_id in remaining_file_ids
            )
            remaining_originals: dict[str, OriginalFileMetadata] = {}
            for existing in remaining:
                remaining_originals.setdefault(
                    existing.file_id,
                    existing.to_original(),
                )
            result = FileCatalogSnapshot(
                schema_version=FILE_CATALOG_SNAPSHOT_SCHEMA_VERSION,
                scope=scope,
                originals=tuple(remaining_originals.values()),
                ownerships=tuple(
                    existing.to_ownership(scope) for existing in remaining
                ),
            )
            blob_path = self._original_blob_path(scope, item.file_id)
            self._write_manifest(
                scope,
                remaining,
                derived=remaining_derived,
            )
            if item.file_id not in remaining_file_ids:
                self._best_effort_unlink(blob_path)
            return result

    def claim_chat(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> tuple[AttachmentItem, ...]:
        """Atomically reserve ready Chat drafts for one generation request."""

        self._require_chat_scope(scope)
        safe_ids = self._validated_id_tuple(attachment_ids)
        if not safe_ids:
            return ()
        with self._lock:
            document = self._load_manifest_document(scope)
            items = list(document.items)
            by_id = {item.attachment_id: item for item in items}
            selected: list[_ManifestItem] = []
            for attachment_id in safe_ids:
                item = by_id.get(attachment_id)
                if item is None:
                    raise AttachmentNotFoundError(
                        "Attachment does not exist in this Chat."
                    )
                if item.status != "ready":
                    raise AttachmentConflictError(
                        "Attachment is already claimed by another request."
                    )
                selected.append(item)
            claimed_ids = set(safe_ids)
            updated = [
                replace(item, status="claimed")
                if item.attachment_id in claimed_ids
                else item
                for item in items
            ]
            self._write_manifest(
                scope,
                updated,
                derived=document.derived,
            )
            return tuple(item.to_public() for item in selected)

    def release_chat_claims(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Release only the named claims after a request fails to launch."""

        self._require_chat_scope(scope)
        safe_ids = set(self._validated_id_tuple(attachment_ids))
        with self._lock:
            document = self._load_manifest_document(scope)
            if not safe_ids:
                return self._state(scope, document.items)
            items = list(document.items)
            updated = [
                replace(item, status="ready")
                if item.attachment_id in safe_ids and item.status == "claimed"
                else item
                for item in items
            ]
            if updated != items:
                self._write_manifest(
                    scope,
                    updated,
                    derived=document.derived,
                )
            return self._state(scope, updated)

    def mark_committed(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Finalize claimed Chat links; Project Sources remain available.

        Original bytes are immutable and content-addressed, so committing a
        Chat changes only the ownership-link state. This avoids a blob move
        race and lets a later message reuse the same original safely.
        """

        self._require_scope(scope)
        safe_ids = self._validated_id_tuple(attachment_ids)
        with self._lock:
            document = self._load_manifest_document(scope)
            if not safe_ids:
                return self._state(scope, document.items)
            items = list(document.items)
            by_id = {item.attachment_id: item for item in items}
            for attachment_id in safe_ids:
                item = by_id.get(attachment_id)
                if item is None:
                    raise AttachmentNotFoundError(
                        "Attachment does not exist in this scope."
                    )
                if scope.kind == "chat" and item.status != "claimed":
                    raise AttachmentConflictError(
                        "Chat attachment was not claimed for commit."
                    )
            if scope.kind == "project":
                return self._state(scope, items)
            committed_ids = set(safe_ids)
            remaining = [
                replace(item, status="committed")
                if item.attachment_id in committed_ids
                else item
                for item in items
            ]
            self._write_manifest(
                scope,
                remaining,
                derived=document.derived,
            )
            return self._state(scope, remaining)

    def delete_scope(self, scope: AttachmentScope) -> None:
        """Atomically hide, then recursively remove one validated scope root."""

        self._require_scope(scope)
        with self._lock:
            scope_root = self._scope_root(scope, create=False)
            if not scope_root.exists() and not scope_root.is_symlink():
                return
            self._require_safe_directory(scope_root)
            tombstone = scope_root.with_name(
                f".{scope.id}.delete-{uuid4().hex}.tmp"
            )
            try:
                os.replace(scope_root, tombstone)
                self._purge_scope_tree(tombstone)
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment scope could not be deleted safely."
                ) from error

    def delete_scope_with(
        self,
        scope: AttachmentScope,
        delete_owner: Callable[[], None],
    ) -> None:
        """Hide one scope, run its owner deletion, and restore on failure.

        Purging happens only after the owning Chat/Project deletion succeeds.
        A purge interruption leaves a hidden ``.tmp`` tombstone that startup
        cleanup removes, never a visible unreferenced attachment namespace.
        """

        self.delete_scopes_with((scope,), delete_owner)

    def delete_scopes_with(
        self,
        scopes: Sequence[AttachmentScope],
        delete_owners: Callable[[], None],
    ) -> None:
        """Hide several scopes, delete their owners, and roll back together.

        A Project cascade can own both its Project Source namespace and every
        linked Chat namespace. Hiding all of them before the owner callback
        prevents a partial deletion from permanently discarding one Chat's
        files while the canonical Project transaction rolls back.
        """

        if isinstance(scopes, (str, bytes)) or not scopes:
            raise AttachmentValidationError(
                "Attachment owner scopes must be a non-empty sequence."
            )
        canonical_scopes = tuple(scopes)
        for scope in canonical_scopes:
            self._require_scope(scope)
        if len(canonical_scopes) != len(set(canonical_scopes)):
            raise AttachmentValidationError(
                "Attachment owner scopes must be unique."
            )
        if not callable(delete_owners):
            raise AttachmentValidationError(
                "Attachment owner deletion callback is invalid."
            )

        with self._lock:
            hidden: list[tuple[AttachmentScope, Path, Path, str]] = []
            try:
                for scope in canonical_scopes:
                    scope_root = self._scope_root(scope, create=False)
                    if not scope_root.exists() and not scope_root.is_symlink():
                        continue
                    self._require_safe_directory(scope_root)
                    operation_id = uuid4().hex
                    tombstone = scope_root.with_name(
                        f".{scope.id}.delete-{operation_id}.pending"
                    )
                    os.replace(scope_root, tombstone)
                    hidden.append(
                        (scope, scope_root, tombstone, operation_id)
                    )
            except (OSError, AttachmentStorageError) as error:
                self._restore_hidden_scopes(hidden)
                raise AttachmentStorageError(
                    "Attachment scopes could not be prepared for deletion."
                ) from error

            try:
                delete_owners()
            except Exception:
                self._restore_hidden_scopes(hidden)
                raise

            for scope, _scope_root, tombstone, operation_id in hidden:
                try:
                    committed_tombstone = tombstone.with_name(
                        f".{scope.id}.delete-{operation_id}.tmp"
                    )
                    os.replace(tombstone, committed_tombstone)
                    self._purge_scope_tree(committed_tombstone)
                except (OSError, AttachmentStorageError):
                    # Owner deletion already committed. A hidden pending entry
                    # is conservatively restored on startup and then removed
                    # by owner reconciliation; a committed entry is purged.
                    pass

    def reconcile(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str],
    ) -> AttachmentState:
        """Repair interrupted transitions and delete unreferenced blobs."""

        with self._lock:
            return self._reconcile_locked(
                scope,
                referenced_ids,
                release_claims=True,
            )

    def reconcile_owners(
        self,
        *,
        chat_ids: Iterable[str],
        project_ids: Iterable[str],
    ) -> None:
        """Remove scopes whose canonical Chat or Project owner is absent.

        Startup restores ambiguous pending-deletion tombstones first, choosing
        data preservation after a crash. This owner-aware pass then removes a
        restored scope only when the canonical repository proves its owner no
        longer exists.
        """

        owner_sets: dict[Literal["chat", "project"], set[str]] = {
            "chat": set(chat_ids),
            "project": set(project_ids),
        }
        for kind, owner_ids in owner_sets.items():
            try:
                for owner_id in owner_ids:
                    AttachmentScope(kind=kind, id=owner_id)
            except (TypeError, ValueError) as error:
                raise AttachmentValidationError(
                    "Canonical attachment owner identifiers are invalid."
                ) from error

        with self._lock:
            for kind, owner_ids in owner_sets.items():
                kind_root = self._kind_root(kind, create=False)
                if not kind_root.exists() and not kind_root.is_symlink():
                    continue
                for entry in tuple(kind_root.iterdir()):
                    if entry.name.startswith("."):
                        raise AttachmentStorageError(
                            "Attachment storage contains an unrecovered hidden entry."
                        )
                    self._require_safe_directory(entry)
                    try:
                        scope = AttachmentScope(kind=kind, id=entry.name)
                    except ValueError as error:
                        raise AttachmentStorageError(
                            "Attachment storage contains an invalid scope."
                        ) from error
                    if scope.id not in owner_ids:
                        self.delete_scope(scope)

    def _reconcile_locked(
        self,
        scope: AttachmentScope,
        referenced_ids: Iterable[str],
        *,
        release_claims: bool,
    ) -> AttachmentState:
        """Resolve manifest state against canonical message references.

        A persisted Chat reference wins over an interrupted draft/commit
        transition and therefore keeps exactly one verified committed blob.
        Conversely, committed entries with no persisted owner are discarded.
        Stale claims are released only for explicit startup reconciliation so
        a normal state read cannot steal an in-flight request's reservation.
        """

        self._require_scope(scope)
        references = set(self._validated_id_tuple(referenced_ids))
        scope_root = self._scope_root(scope, create=False)
        if not scope_root.exists() and not scope_root.is_symlink():
            if references:
                raise AttachmentStorageError(
                    "Referenced attachment metadata is missing from storage."
                )
            return self._state(scope, ())
        self._clean_scope_temporary_files(scope)
        document = self._load_manifest_document(scope)
        items = list(document.items)
        reconciled: list[_ManifestItem] = []
        manifest_ids = {item.attachment_id for item in items}
        missing_metadata = references - manifest_ids
        if missing_metadata:
            raise AttachmentStorageError(
                "Referenced attachment metadata is missing from storage."
            )

        for item in items:
            if scope.kind == "chat" and item.attachment_id in references:
                reconciled.append(replace(item, status="committed"))
                continue
            if item.status == "committed":
                continue
            reconciled.append(
                replace(item, status="ready")
                if release_claims and item.status == "claimed"
                else item
            )

        retained_file_ids = {item.file_id for item in reconciled}
        by_file_id: dict[str, _ManifestItem] = {}
        for item in reconciled:
            by_file_id.setdefault(item.file_id, item)
        for file_id, item in by_file_id.items():
            self._verify_blob(
                self._original_blob_path(scope, file_id),
                item,
            )
        retained_derived = tuple(
            relation
            for relation in document.derived
            if relation.original_file_id in retained_file_ids
        )
        if (
            tuple(reconciled) != document.items
            or retained_derived != document.derived
        ):
            self._write_manifest(
                scope,
                reconciled,
                derived=retained_derived,
            )
        # Cleanup follows the metadata commit. A crash can therefore leave an
        # unreferenced object for the next reconciliation, but cannot remove
        # the only bytes still named by the durable manifest.
        for legacy_directory in (
            scope_root / "drafts",
            scope_root / "committed",
        ):
            self._remove_orphan_blobs(legacy_directory, set())
        self._remove_orphan_blobs(
            self._originals_directory(scope),
            retained_file_ids,
        )
        return self._state(scope, reconciled)

    def _copy_candidate(
        self,
        scope: AttachmentScope,
        source_path: Path,
        *,
        origin: FileOrigin,
        cancel_requested: Callable[[], bool] | None,
    ) -> _CopiedCandidate:
        """Copy and hash one stable source into scoped original-file staging.

        The descriptor is opened without following links where supported, and
        its identity, size, and modification time are compared before and
        after the streaming copy. The streaming byte limit also catches a file
        that grows after the initial stat call.
        """

        validated_source = self._validate_source_path(source_path)
        source = validated_source.path
        file_name = source.name
        extension = source.suffix.casefold()
        media_type = ALLOWED_ATTACHMENT_EXTENSIONS.get(extension)
        if media_type is None:
            raise AttachmentValidationError(
                "The selected file type is not supported."
            )
        originals = self._originals_directory(scope)
        descriptor, temporary_name = mkstemp(
            dir=originals,
            prefix=".upload-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        source_descriptor = -1
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            source_descriptor = os.open(source, flags)
            before = os.fstat(source_descriptor)
            self._require_regular_stat(before)
            if (before.st_dev, before.st_ino) != (
                validated_source.device,
                validated_source.inode,
            ):
                # A pathname can be replaced after its components pass the
                # reparse checks. Pinning the approved identity prevents that
                # race from substituting a different local or internal file.
                raise AttachmentValidationError(
                    "The selected file changed after path validation."
                )
            if before.st_size <= 0:
                raise AttachmentValidationError(
                    "Empty files cannot be attached."
                )
            if before.st_size > self._max_file_bytes:
                raise AttachmentValidationError(
                    "The selected file exceeds the attachment size limit."
                )
            digest = hashlib.sha256()
            copied = 0
            with os.fdopen(source_descriptor, "rb") as input_file:
                source_descriptor = -1
                with os.fdopen(descriptor, "wb") as output_file:
                    descriptor = -1
                    while True:
                        self._raise_if_import_cancelled(cancel_requested)
                        chunk = input_file.read(_COPY_BUFFER_BYTES)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > self._max_file_bytes:
                            raise AttachmentValidationError(
                                "The selected file exceeds the attachment size limit."
                            )
                        output_file.write(chunk)
                        digest.update(chunk)
                    output_file.flush()
                    os.fsync(output_file.fileno())
                after = os.fstat(input_file.fileno())
            if (
                copied != before.st_size
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            ):
                raise AttachmentValidationError(
                    "The selected file changed while it was being copied."
                )
            imported_at = self._utc_now()
            sha256 = digest.hexdigest()
            return _CopiedCandidate(
                temporary_path=temporary_path,
                file_id=file_id_from_sha256(sha256),
                file_name=file_name,
                media_type=media_type,
                size_bytes=copied,
                sha256=sha256,
                origin=origin,
                imported_at=imported_at,
            )
        except (AttachmentValidationError, AttachmentImportCancelledError):
            self._best_effort_unlink(temporary_path)
            raise
        except (OSError, ValueError) as error:
            self._best_effort_unlink(temporary_path)
            raise AttachmentStorageError(
                "The selected file could not be copied safely."
            ) from error
        finally:
            if descriptor != -1:
                os.close(descriptor)
            if source_descriptor != -1:
                os.close(source_descriptor)

    def _validate_source_path(self, source_path: Path) -> _ValidatedSource:
        """Validate one source path and pin its approved file identity."""

        source = Path(source_path)
        raw_text = str(source)
        windows_text = raw_text.replace("/", "\\")
        if (
            not source.is_absolute()
            or not raw_text
            or len(raw_text) > MAX_SOURCE_PATH_LENGTH
            or "\x00" in raw_text
            or windows_text.startswith("\\\\")
        ):
            raise AttachmentValidationError(
                "The selected file path is invalid."
            )
        try:
            for candidate in (source, *source.parents):
                details = candidate.lstat()
                if candidate.is_symlink() or self._stat_is_reparse(details):
                    raise AttachmentValidationError(
                        "Linked or redirected files cannot be attached."
                    )
            details = source.lstat()
            self._require_regular_stat(details)
            resolved = source.resolve(strict=True)
            try:
                resolved.relative_to(self._base_dir.resolve())
            except ValueError:
                pass
            else:
                raise AttachmentValidationError(
                    "Internal application files cannot be attached."
                )
            validate_file_name(source.name)
            return _ValidatedSource(
                path=resolved,
                device=details.st_dev,
                inode=details.st_ino,
            )
        except AttachmentValidationError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise AttachmentValidationError(
                "The selected file is unavailable or unsafe."
            ) from error

    def _load_manifest(self, scope: AttachmentScope) -> tuple[_ManifestItem, ...]:
        """Compatibility helper returning ownership links from Manifest v2."""

        return self._load_manifest_document(scope).items

    def _load_manifest_document(
        self,
        scope: AttachmentScope,
    ) -> _ManifestDocument:
        """Load, validate, and when necessary atomically migrate one manifest."""

        manifest_path = self._manifest_path(scope, create=False)
        if not manifest_path.exists() and not manifest_path.is_symlink():
            return _ManifestDocument(items=())
        try:
            raw, details = self._read_manifest_json(manifest_path)
            if not isinstance(raw, dict):
                raise ValueError("manifest document")
            version = raw.get("schema_version")
            if type(version) is not int:
                raise ValueError("manifest schema version")
            self._require_manifest_scope(raw.get("scope"), scope)
            if version == LEGACY_ATTACHMENT_MANIFEST_SCHEMA_VERSION:
                if set(raw) != {"schema_version", "scope", "items"}:
                    raise ValueError("legacy manifest fields")
                raw_items = raw.get("items")
                if not isinstance(raw_items, list):
                    raise ValueError("legacy manifest items")
                imported_at = datetime.fromtimestamp(
                    details.st_mtime,
                    timezone.utc,
                )
                parsed_legacy_items = tuple(
                    self._legacy_manifest_item_from_value(
                        value,
                        scope=scope,
                        imported_at=imported_at,
                    )
                    for value in raw_items
                )
                canonical_by_file_id: dict[str, _ManifestItem] = {}
                normalized_legacy_items: list[_ManifestItem] = []
                for item in parsed_legacy_items:
                    canonical = canonical_by_file_id.setdefault(
                        item.file_id,
                        item,
                    )
                    if (
                        item.sha256 != canonical.sha256
                        or item.size_bytes != canonical.size_bytes
                    ):
                        raise ValueError("legacy content metadata mismatch")
                    normalized_legacy_items.append(
                        replace(
                            item,
                            file_name=canonical.file_name,
                            media_type=canonical.media_type,
                            origin=canonical.origin,
                            imported_at=canonical.imported_at,
                        )
                    )
                legacy_items = tuple(normalized_legacy_items)
                self._validate_manifest_records(
                    scope,
                    legacy_items,
                    (),
                )
                self._migrate_legacy_scope(scope, legacy_items)
                return self._load_manifest_document(scope)
            if version != ATTACHMENT_MANIFEST_SCHEMA_VERSION or set(raw) != {
                "schema_version",
                "scope",
                "items",
                "derived",
            }:
                raise ValueError("manifest fields or version")
            raw_items = raw.get("items")
            raw_derived = raw.get("derived")
            if not isinstance(raw_items, list) or not isinstance(
                raw_derived,
                list,
            ):
                raise ValueError("manifest records")
            items = tuple(
                self._manifest_item_from_value(value)
                for value in raw_items
            )
            derived = tuple(
                self._derived_relation_from_value(value)
                for value in raw_derived
            )
            self._validate_manifest_records(scope, items, derived)
            return _ManifestDocument(items=items, derived=derived)
        except AttachmentStorageError:
            raise
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ) as error:
            raise AttachmentStorageError(
                "Attachment manifest is unreadable or invalid."
            ) from error

    def _write_manifest(
        self,
        scope: AttachmentScope,
        items: Sequence[_ManifestItem],
        *,
        derived: Sequence[DerivedFileRelation] = (),
    ) -> None:
        canonical_items = tuple(items)
        canonical_derived = tuple(derived)
        self._validate_manifest_records(
            scope,
            canonical_items,
            canonical_derived,
        )
        manifest_path = self._manifest_path(scope, create=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": ATTACHMENT_MANIFEST_SCHEMA_VERSION,
            "scope": {"kind": scope.kind, "id": scope.id},
            "items": [
                self._manifest_item_to_data(item)
                for item in canonical_items
            ],
            "derived": [
                self._derived_relation_to_data(relation)
                for relation in canonical_derived
            ],
        }
        payload = (
            json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise AttachmentStorageError(
                "Attachment manifest exceeds its safe storage limit."
            )
        descriptor, temporary_name = mkstemp(
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(
                descriptor,
                "wb",
            ) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, manifest_path)
        except (OSError, TypeError, ValueError) as error:
            raise AttachmentStorageError(
                "Attachment manifest could not be written safely."
            ) from error
        finally:
            if descriptor != -1:
                os.close(descriptor)
            self._best_effort_unlink(temporary_path)

    def _manifest_item_from_value(self, value: object) -> _ManifestItem:
        if not isinstance(value, dict) or set(value) != {
            "record_schema_version",
            "attachment_id",
            "file_id",
            "file_name",
            "media_type",
            "size_bytes",
            "sha256",
            "origin",
            "imported_at",
            "link_file_name",
            "link_media_type",
            "linked_at",
            "role",
            "status",
        }:
            raise ValueError("manifest item fields")
        return _ManifestItem(
            record_schema_version=cast(Literal[1], value["record_schema_version"]),
            attachment_id=cast(str, value["attachment_id"]),
            file_id=cast(str, value["file_id"]),
            file_name=cast(str, value["file_name"]),
            media_type=cast(str, value["media_type"]),
            size_bytes=cast(int, value["size_bytes"]),
            sha256=cast(str, value["sha256"]),
            origin=cast(FileOrigin, value["origin"]),
            imported_at=self._timestamp_from_value(value["imported_at"]),
            link_file_name=cast(str, value["link_file_name"]),
            link_media_type=cast(str, value["link_media_type"]),
            linked_at=self._timestamp_from_value(value["linked_at"]),
            role=cast(
                Literal["chat_attachment", "project_source"],
                value["role"],
            ),
            status=cast(_ManifestStatus, value["status"]),
        )

    @staticmethod
    def _manifest_item_to_data(item: _ManifestItem) -> dict[str, object]:
        return {
            "record_schema_version": item.record_schema_version,
            "attachment_id": item.attachment_id,
            "file_id": item.file_id,
            "file_name": item.file_name,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "origin": item.origin,
            "imported_at": JsonAttachmentStore._timestamp_to_value(
                item.imported_at
            ),
            "link_file_name": item.link_file_name,
            "link_media_type": item.link_media_type,
            "linked_at": JsonAttachmentStore._timestamp_to_value(
                item.linked_at
            ),
            "role": item.role,
            "status": item.status,
        }

    def _legacy_manifest_item_from_value(
        self,
        value: object,
        *,
        scope: AttachmentScope,
        imported_at: datetime,
    ) -> _ManifestItem:
        """Convert one strict v1 record without inventing its original path."""

        if not isinstance(value, dict) or set(value) != {
            "attachment_id",
            "file_name",
            "media_type",
            "size_bytes",
            "sha256",
            "status",
        }:
            raise ValueError("legacy manifest item fields")
        sha256 = validate_sha256(value["sha256"])
        return _ManifestItem(
            record_schema_version=FILE_METADATA_SCHEMA_VERSION,
            attachment_id=cast(str, value["attachment_id"]),
            file_id=file_id_from_sha256(sha256),
            file_name=cast(str, value["file_name"]),
            media_type=cast(str, value["media_type"]),
            size_bytes=cast(int, value["size_bytes"]),
            sha256=sha256,
            origin="legacy_migration",
            imported_at=imported_at,
            link_file_name=cast(str, value["file_name"]),
            link_media_type=cast(str, value["media_type"]),
            linked_at=imported_at,
            role=self._role_for_scope(scope),
            status=cast(_ManifestStatus, value["status"]),
        )

    def _derived_relation_from_value(
        self,
        value: object,
    ) -> DerivedFileRelation:
        """Parse one exact path-free derived relationship record."""

        if not isinstance(value, dict) or set(value) != {
            "record_schema_version",
            "original_file_id",
            "derived_file_id",
            "scope",
            "derivation_kind",
            "producer_version",
            "created_at",
        }:
            raise ValueError("derived relation fields")
        raw_scope = value["scope"]
        if not isinstance(raw_scope, dict) or set(raw_scope) != {"kind", "id"}:
            raise ValueError("derived relation scope")
        return DerivedFileRelation(
            schema_version=cast(Literal[1], value["record_schema_version"]),
            original_file_id=cast(str, value["original_file_id"]),
            derived_file_id=cast(str, value["derived_file_id"]),
            scope=AttachmentScope(
                kind=cast(Literal["chat", "project"], raw_scope["kind"]),
                id=cast(str, raw_scope["id"]),
            ),
            derivation_kind=cast(str, value["derivation_kind"]),
            producer_version=cast(str, value["producer_version"]),
            created_at=self._timestamp_from_value(value["created_at"]),
        )

    @staticmethod
    def _derived_relation_to_data(
        relation: DerivedFileRelation,
    ) -> dict[str, object]:
        """Serialize one derived relationship without a storage path."""

        return {
            "record_schema_version": relation.schema_version,
            "original_file_id": relation.original_file_id,
            "derived_file_id": relation.derived_file_id,
            "scope": {
                "kind": relation.scope.kind,
                "id": relation.scope.id,
            },
            "derivation_kind": relation.derivation_kind,
            "producer_version": relation.producer_version,
            "created_at": JsonAttachmentStore._timestamp_to_value(
                relation.created_at
            ),
        }

    @staticmethod
    def _require_manifest_scope(
        value: object,
        scope: AttachmentScope,
    ) -> None:
        """Require a manifest to name exactly its containing owner scope."""

        if (
            not isinstance(value, dict)
            or set(value) != {"kind", "id"}
            or value.get("kind") != scope.kind
            or value.get("id") != scope.id
        ):
            raise ValueError("manifest scope")

    def _validate_manifest_records(
        self,
        scope: AttachmentScope,
        items: Sequence[_ManifestItem],
        derived: Sequence[DerivedFileRelation],
    ) -> None:
        """Enforce unique links, bounded drafts, and same-scope derivations."""

        ids = [item.attachment_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest duplicates")
        if sum(item.status != "committed" for item in items) > (
            self._max_file_count
        ):
            raise ValueError("manifest count")
        expected_role = self._role_for_scope(scope)
        original_ids = {item.file_id for item in items}
        canonical_originals: dict[str, OriginalFileMetadata] = {}
        for item in items:
            if item.role != expected_role:
                raise ValueError("manifest ownership role")
            original = item.to_original()
            existing_original = canonical_originals.setdefault(
                item.file_id,
                original,
            )
            if existing_original != original:
                raise ValueError("manifest original metadata mismatch")
            item.to_ownership(scope)
        derived_ids = [relation.derived_file_id for relation in derived]
        if len(derived_ids) != len(set(derived_ids)):
            raise ValueError("derived relation duplicates")
        for relation in derived:
            if (
                relation.scope != scope
                or relation.original_file_id not in original_ids
            ):
                raise ValueError("derived relation ownership")

    def _migrate_legacy_manifests(self) -> None:
        """Eagerly migrate every existing v1 scope while the lease is held."""

        for kind_value in _SCOPE_KINDS:
            kind_root = self._kind_root(kind_value, create=False)
            if not kind_root.exists() and not kind_root.is_symlink():
                continue
            for scope_root in tuple(kind_root.iterdir()):
                if scope_root.name.startswith("."):
                    raise AttachmentStorageError(
                        "Attachment storage contains an unrecovered hidden entry."
                    )
                self._require_safe_directory(scope_root)
                try:
                    scope = AttachmentScope(
                        kind=kind_value,
                        id=scope_root.name,
                    )
                except ValueError as error:
                    raise AttachmentStorageError(
                        "Attachment storage contains an invalid scope."
                    ) from error
                self._load_manifest_document(scope)

    def _migrate_legacy_scope(
        self,
        scope: AttachmentScope,
        items: Sequence[_ManifestItem],
    ) -> None:
        """Move v1 blobs into content-addressed originals and publish v2.

        Existing v1 paths are removed only after the v2 manifest commits. A
        crash after moving one blob is restartable because the verified target
        is accepted when its legacy source is absent; an ordinary exception
        restores every move before returning.
        """

        moved: list[tuple[Path, Path]] = []
        legacy_paths: set[Path] = set()
        originals = self._originals_directory(scope)
        try:
            for item in items:
                candidates = (
                    self._legacy_draft_blob_path(scope, item.attachment_id),
                    self._legacy_committed_blob_path(
                        scope,
                        item.attachment_id,
                    ),
                )
                existing = [
                    path
                    for path in candidates
                    if path.exists() or path.is_symlink()
                ]
                legacy_paths.update(existing)
                target = originals / f"{item.file_id}{_BLOB_SUFFIX}"
                if target.exists() or target.is_symlink():
                    self._verify_blob(target, item)
                elif existing:
                    source = existing[0]
                    self._verify_blob(source, item)
                    os.replace(source, target)
                    moved.append((source, target))
                    # Verification must follow the rename: another process can
                    # swap the source pathname after the first descriptor
                    # closes but before the filesystem move executes.
                    self._verify_blob(target, item)
                else:
                    raise AttachmentStorageError(
                        "Legacy attachment data is missing from storage."
                    )
                for duplicate in existing[1:]:
                    self._verify_blob(duplicate, item)
            self._write_manifest(scope, items, derived=())
        except Exception:
            self._restore_moves(moved)
            raise
        for path in legacy_paths:
            self._best_effort_unlink(path)

    @staticmethod
    def _timestamp_to_value(value: datetime) -> str:
        """Serialize one validated UTC timestamp in canonical Z form."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )

    @staticmethod
    def _timestamp_from_value(value: object) -> datetime:
        """Parse one canonical UTC timestamp and reject local/naive values."""

        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("timestamp must use UTC Z form")
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return parsed.astimezone(timezone.utc)

    def _utc_now(self) -> datetime:
        """Read the injected clock and normalize it to timezone-aware UTC."""

        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AttachmentStorageError(
                "Attachment storage clock must return an aware datetime."
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _role_for_scope(
        scope: AttachmentScope,
    ) -> Literal["chat_attachment", "project_source"]:
        """Map the closed Scope kind to its explicit ownership role."""

        return (
            "chat_attachment"
            if scope.kind == "chat"
            else "project_source"
        )

    def _candidate_manifest_item(
        self,
        scope: AttachmentScope,
        attachment_id: str,
        candidate: _CopiedCandidate,
    ) -> _ManifestItem:
        """Build a temporary record used to verify crash-left originals."""

        return _ManifestItem(
            record_schema_version=FILE_METADATA_SCHEMA_VERSION,
            attachment_id=attachment_id,
            file_id=candidate.file_id,
            file_name=candidate.file_name,
            media_type=candidate.media_type,
            size_bytes=candidate.size_bytes,
            sha256=candidate.sha256,
            origin=candidate.origin,
            imported_at=candidate.imported_at,
            link_file_name=candidate.file_name,
            link_media_type=candidate.media_type,
            linked_at=candidate.imported_at,
            role=self._role_for_scope(scope),
        )

    @staticmethod
    def _raise_if_import_cancelled(
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        """Stop only before manifest commit so cancellation is unambiguous."""

        if cancel_requested is not None and cancel_requested():
            raise AttachmentImportCancelledError(
                "File import was cancelled before it committed."
            )

    def _verify_blob(self, path: Path, item: _ManifestItem) -> None:
        """Verify one immutable content object through its pinned descriptor."""

        with self._verified_blob_stream(path, item):
            return

    @contextmanager
    def _verified_blob_stream(
        self,
        path: Path,
        item: _ManifestItem,
    ) -> Iterator[BinaryIO]:
        """Yield verified immutable bytes without a post-check mutation race.

        Hashing a descriptor and then returning that same descriptor would let
        another process overwrite it after verification. A bounded spooled
        snapshot preserves files imported under an older, higher user setting
        without retaining large payloads in memory.
        """

        descriptor = -1
        stream: BinaryIO | None = None
        verified = cast(
            BinaryIO,
            SpooledTemporaryFile(
                max_size=_VERIFIED_MEMORY_BYTES,
                mode="w+b",
            ),
        )
        try:
            linked = self._require_safe_regular_file(
                path,
                "Stored attachment data is not a safe regular file.",
            )
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            self._require_regular_storage_stat(opened)
            if (
                opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
            ):
                raise AttachmentStorageError(
                    "Stored attachment data changed while it was opened."
                )
            if (
                opened.st_size != item.size_bytes
                or opened.st_size > _MAX_STORED_FILE_BYTES
            ):
                raise AttachmentStorageError(
                    "Stored attachment data failed integrity validation."
                )
            stream = os.fdopen(descriptor, "rb")
            descriptor = -1
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = stream.read(_COPY_BUFFER_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                verified.write(chunk)
            after = os.fstat(stream.fileno())
            self._require_regular_storage_stat(after)
            if (
                after.st_dev != opened.st_dev
                or after.st_ino != opened.st_ino
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or size != item.size_bytes
                or digest.hexdigest() != item.sha256
            ):
                raise AttachmentStorageError(
                    "Stored attachment data failed integrity validation."
                )
            stream.close()
            stream = None
            verified.seek(0)
            yield verified
        except AttachmentStorageError:
            raise
        except (OSError, ValueError) as error:
            raise AttachmentStorageError(
                "Stored attachment data could not be verified."
            ) from error
        finally:
            if stream is not None:
                stream.close()
            verified.close()
            if descriptor != -1:
                os.close(descriptor)

    def _read_manifest_json(
        self,
        path: Path,
    ) -> tuple[object, os.stat_result]:
        """Parse one bounded manifest through a stable, non-linked descriptor.

        A crash or accidental workspace mutation can replace a path between
        separate validation and open operations. Pinning the file identity and
        rechecking its metadata after parsing makes that change fail closed.
        """

        descriptor = -1
        try:
            linked = self._require_safe_regular_file(
                path,
                "Attachment manifest is not a safe regular file.",
            )
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            self._require_regular_storage_stat(opened)
            if (
                opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
                or opened.st_size > _MAX_MANIFEST_BYTES
            ):
                raise AttachmentStorageError(
                    "Attachment manifest changed while it was opened."
                )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                payload = stream.read(_MAX_MANIFEST_BYTES + 1)
                after = os.fstat(stream.fileno())
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise AttachmentStorageError(
                    "Attachment manifest exceeds its safe storage limit."
                )
            raw: object = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_keys,
                parse_constant=self._reject_json_constant,
            )
            self._require_regular_storage_stat(after)
            if (
                after.st_dev != opened.st_dev
                or after.st_ino != opened.st_ino
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
            ):
                raise AttachmentStorageError(
                    "Attachment manifest changed while it was read."
                )
            return raw, opened
        finally:
            if descriptor != -1:
                os.close(descriptor)

    @classmethod
    def _require_safe_regular_file(
        cls,
        path: Path,
        message: str,
    ) -> os.stat_result:
        """Reject redirects, special files, and externally linked artifacts."""

        try:
            details = path.lstat()
        except OSError as error:
            raise AttachmentStorageError(message) from error
        if (
            stat_module.S_ISLNK(details.st_mode)
            or cls._stat_is_reparse(details)
            or not stat_module.S_ISREG(details.st_mode)
            or details.st_nlink != 1
        ):
            raise AttachmentStorageError(message)
        return details

    @classmethod
    def _require_regular_storage_stat(cls, details: os.stat_result) -> None:
        """Validate descriptor metadata for a managed immutable artifact."""

        if (
            not stat_module.S_ISREG(details.st_mode)
            or cls._stat_is_reparse(details)
            or details.st_nlink != 1
        ):
            raise AttachmentStorageError(
                "Stored attachment data is not a safe regular file."
            )

    def _remove_orphan_blobs(
        self,
        directory: Path,
        retained_ids: set[str],
    ) -> None:
        if not directory.exists():
            return
        self._require_safe_directory(directory)
        try:
            entries = tuple(directory.iterdir())
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment storage could not be reconciled."
            ) from error
        for entry in entries:
            if entry.name.endswith(".tmp"):
                if _UPLOAD_TEMP_PATTERN.fullmatch(entry.name) is None:
                    raise AttachmentStorageError(
                        "Attachment storage contains an unknown temporary entry."
                    )
                self._require_safe_regular_file(
                    entry,
                    "Attachment storage contains an unsafe temporary entry.",
                )
                self._unlink_required(entry)
                continue
            file_id = (
                entry.name[: -len(_BLOB_SUFFIX)]
                if entry.name.endswith(_BLOB_SUFFIX)
                else ""
            )
            if file_id not in retained_ids:
                self._require_safe_regular_file(
                    entry,
                    "Attachment storage contains an unsafe original entry.",
                )
                self._unlink_required(entry)

    def _state(
        self,
        scope: AttachmentScope,
        items: Sequence[_ManifestItem],
    ) -> AttachmentState:
        return AttachmentState(
            scope=scope,
            attachments=tuple(
                item.to_public() for item in items if item.status == "ready"
            ),
            max_file_bytes=self._max_file_bytes,
            max_file_count=self._max_file_count,
        )

    def _validated_id_tuple(self, values: Iterable[str]) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)):
            raise AttachmentValidationError(
                "Attachment identifiers must be an array."
            )
        try:
            safe_ids = tuple(validate_attachment_id(value) for value in values)
        except (TypeError, ValueError) as error:
            raise AttachmentValidationError(
                "Attachment identifier is invalid."
            ) from error
        if len(safe_ids) != len(set(safe_ids)):
            raise AttachmentValidationError(
                "Attachment identifiers must be unique."
            )
        return safe_ids

    @staticmethod
    def _require_scope(scope: AttachmentScope) -> None:
        if not isinstance(scope, AttachmentScope):
            raise AttachmentValidationError(
                "Attachment scope is invalid."
            )

    def _require_chat_scope(self, scope: AttachmentScope) -> None:
        self._require_scope(scope)
        if scope.kind != "chat":
            raise AttachmentValidationError(
                "Only Chat attachments can be claimed for a message."
            )

    def _prepare_base_directory(self) -> None:
        try:
            self._require_safe_directory_chain(self._base_dir)
            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._require_safe_directory_chain(self._base_dir)
        except AttachmentStorageError:
            raise
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment storage directory is unavailable."
            ) from error

    def _kind_root(
        self,
        kind: Literal["chat", "project"],
        *,
        create: bool,
    ) -> Path:
        self._require_open()
        self._require_safe_directory_chain(self._base_dir)
        root = self._base_dir / kind
        if create:
            try:
                root.mkdir(exist_ok=True)
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment kind directory is unavailable."
                ) from error
        if root.exists() or root.is_symlink():
            self._require_safe_directory(root)
        return root

    def _require_open(self) -> None:
        descriptor = self._process_lock_fd
        if descriptor is None:
            raise AttachmentStorageError(
                "Attachment storage is not open in this Backend."
            )
        try:
            opened = os.fstat(descriptor)
            self._require_regular_lock_stat(opened)
            linked = self._require_safe_lock_file(
                self._base_dir / _PROCESS_LOCK_FILE_NAME
            )
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment storage lock is unavailable."
            ) from error
        if linked.st_dev != opened.st_dev or linked.st_ino != opened.st_ino:
            raise AttachmentStorageError(
                "Attachment storage lock changed while in use."
            )

    def _acquire_process_lock(self) -> None:
        """Take the cross-process lease and defend its path from replacement.

        Device/inode checks ensure the opened descriptor still names the
        validated lock file. A single sentinel byte gives Windows a stable
        byte range to lock; the in-process ``RLock`` alone cannot exclude a
        second Backend process.
        """

        lock_path = self._base_dir / _PROCESS_LOCK_FILE_NAME
        descriptor = -1
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            if lock_path.exists() or lock_path.is_symlink():
                self._require_safe_lock_file(lock_path)
            descriptor = os.open(lock_path, flags, 0o600)
            opened = os.fstat(descriptor)
            self._require_regular_lock_stat(opened)
            linked = self._require_safe_lock_file(lock_path)
            if (
                linked.st_dev != opened.st_dev
                or linked.st_ino != opened.st_ino
            ):
                raise AttachmentStorageError(
                    "Attachment storage lock changed while it was opened."
                )
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        except AttachmentStorageError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise AttachmentStorageError(
                "Attachment storage lock is unavailable."
            ) from error

        try:
            self._lock_process_descriptor(descriptor)
        except OSError as error:
            os.close(descriptor)
            raise AttachmentStorageError(
                "Attachment storage is already in use by another Backend."
            ) from error
        self._process_lock_fd = descriptor

    @staticmethod
    def _lock_process_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        import fcntl  # type: ignore[import-not-found]

        fcntl.flock(  # type: ignore[attr-defined]
            descriptor,
            fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )

    @staticmethod
    def _unlock_process_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        import fcntl  # type: ignore[import-not-found]

        fcntl.flock(  # type: ignore[attr-defined]
            descriptor,
            fcntl.LOCK_UN,  # type: ignore[attr-defined]
        )

    @classmethod
    def _require_safe_lock_file(cls, path: Path) -> os.stat_result:
        try:
            details = path.lstat()
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment storage lock is unavailable."
            ) from error
        if (
            stat_module.S_ISLNK(details.st_mode)
            or cls._stat_is_reparse(details)
            or not stat_module.S_ISREG(details.st_mode)
            or details.st_nlink != 1
        ):
            raise AttachmentStorageError(
                "Attachment storage lock is unsafe."
            )
        return details

    @classmethod
    def _require_regular_lock_stat(cls, details: os.stat_result) -> None:
        if (
            not stat_module.S_ISREG(details.st_mode)
            or cls._stat_is_reparse(details)
            or details.st_nlink != 1
        ):
            raise AttachmentStorageError(
                "Attachment storage lock is unsafe."
            )

    def _scope_root(self, scope: AttachmentScope, *, create: bool = True) -> Path:
        self._require_scope(scope)
        kind_root = self._kind_root(scope.kind, create=create)
        root = kind_root / scope.id
        if create:
            try:
                root.mkdir(exist_ok=True)
                self._require_safe_directory(root)
            except AttachmentStorageError:
                raise
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment scope directory is unavailable."
                ) from error
        elif root.exists() or root.is_symlink():
            self._require_safe_directory(root)
        return root

    def _manifest_path(
        self,
        scope: AttachmentScope,
        *,
        create: bool = True,
    ) -> Path:
        return self._scope_root(scope, create=create) / "manifest.json"

    def _originals_directory(self, scope: AttachmentScope) -> Path:
        """Return the scope-local content-addressed original directory."""

        return self._scoped_data_directory(scope, "originals")

    def _drafts_directory(self, scope: AttachmentScope) -> Path:
        return self._scoped_data_directory(scope, "drafts")

    def _committed_directory(self, scope: AttachmentScope) -> Path:
        return self._scoped_data_directory(scope, "committed")

    def _scoped_data_directory(
        self,
        scope: AttachmentScope,
        name: Literal["originals", "drafts", "committed"],
    ) -> Path:
        directory = self._scope_root(scope) / name
        try:
            directory.mkdir(exist_ok=True)
            self._require_safe_directory(directory)
        except AttachmentStorageError:
            raise
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment data directory is unavailable."
            ) from error
        return directory

    def _original_blob_path(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> Path:
        validate_file_id(file_id)
        return self._originals_directory(scope) / f"{file_id}{_BLOB_SUFFIX}"

    def _legacy_draft_blob_path(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> Path:
        validate_attachment_id(attachment_id)
        return self._drafts_directory(scope) / f"{attachment_id}{_BLOB_SUFFIX}"

    def _legacy_committed_blob_path(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> Path:
        validate_attachment_id(attachment_id)
        return self._committed_directory(scope) / f"{attachment_id}{_BLOB_SUFFIX}"

    # These private aliases keep fault-injection tests and interrupted v1
    # recovery helpers readable while all v2 operations use ``originals``.
    _draft_blob_path = _legacy_draft_blob_path
    _committed_blob_path = _legacy_committed_blob_path

    @staticmethod
    def _delete_tombstone_scope(
        kind: Literal["chat", "project"],
        scope_id: str,
    ) -> AttachmentScope:
        """Validate that a deletion tombstone belongs under its kind root."""

        try:
            return AttachmentScope(kind=kind, id=scope_id)
        except ValueError as error:
            raise AttachmentStorageError(
                "Attachment storage contains an invalid deletion tombstone."
            ) from error

    def _clean_startup_temporary_entries(self) -> None:
        """Resolve deletion tombstones conservatively after an interrupted run.

        A ``.tmp`` tombstone means owner deletion committed and can be purged.
        A ``.pending`` tombstone is ambiguous, so it is restored first to avoid
        data loss; ``reconcile_owners`` later removes it only if the canonical
        repository confirms that its owner no longer exists.
        """

        try:
            for kind_value in _SCOPE_KINDS:
                kind_root = self._kind_root(kind_value, create=False)
                if not kind_root.exists() and not kind_root.is_symlink():
                    continue
                for scope_entry in tuple(kind_root.iterdir()):
                    committed_match = _COMMITTED_DELETE_PATTERN.fullmatch(
                        scope_entry.name
                    )
                    if committed_match is not None:
                        self._delete_tombstone_scope(
                            kind_value,
                            committed_match.group("scope_id"),
                        )
                        self._require_safe_directory(scope_entry)
                        self._purge_scope_tree(scope_entry)
                        continue
                    pending_match = _PENDING_DELETE_PATTERN.fullmatch(
                        scope_entry.name
                    )
                    if pending_match is not None:
                        self._require_safe_directory(scope_entry)
                        scope = self._delete_tombstone_scope(
                            kind_value,
                            pending_match.group("scope_id"),
                        )
                        destination = kind_root / scope.id
                        if destination.exists() or destination.is_symlink():
                            raise AttachmentStorageError(
                                "Pending attachment deletion conflicts with "
                                "a live scope."
                            )
                        os.replace(scope_entry, destination)
                        self._clean_scope_temporary_files(scope)
                        continue
                    if scope_entry.name.startswith("."):
                        raise AttachmentStorageError(
                            "Attachment storage contains an unknown hidden entry."
                        )
                    scope = AttachmentScope(
                        kind=kind_value,
                        id=scope_entry.name,
                    )
                    self._require_safe_directory(scope_entry)
                    self._clean_scope_temporary_files(scope)
        except AttachmentStorageError:
            raise
        except OSError as error:
            raise AttachmentStorageError(
                "Temporary attachment data could not be cleaned."
            ) from error

    def _release_startup_claims(self) -> None:
        """Release claims that cannot survive a Backend process restart."""

        try:
            for kind_value in _SCOPE_KINDS:
                kind_root = self._kind_root(kind_value, create=False)
                if not kind_root.exists() and not kind_root.is_symlink():
                    continue
                for scope_root in tuple(kind_root.iterdir()):
                    if scope_root.name.startswith("."):
                        raise AttachmentStorageError(
                            "Attachment storage contains an unrecovered hidden entry."
                        )
                    self._require_safe_directory(scope_root)
                    try:
                        scope = AttachmentScope(
                            kind=kind_value,
                            id=scope_root.name,
                        )
                    except ValueError as error:
                        raise AttachmentStorageError(
                            "Attachment storage contains an invalid scope."
                        ) from error
                    document = self._load_manifest_document(scope)
                    if any(
                        item.status == "claimed"
                        for item in document.items
                    ):
                        self._write_manifest(
                            scope,
                            [
                                replace(item, status="ready")
                                if item.status == "claimed"
                                else item
                                for item in document.items
                            ],
                            derived=document.derived,
                        )
        except AttachmentStorageError:
            raise
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment claims could not be recovered after restart."
            ) from error

    def _clean_scope_temporary_files(self, scope: AttachmentScope) -> None:
        root = self._scope_root(scope)
        self._clean_flat_temporary_files(
            root,
            allowed_directories={"originals", "drafts", "committed"},
            temporary_pattern=_MANIFEST_TEMP_PATTERN,
        )
        for name in ("originals", "drafts", "committed"):
            directory = root / name
            if directory.exists() or directory.is_symlink():
                self._clean_flat_temporary_files(
                    directory,
                    allowed_directories=set(),
                    temporary_pattern=_UPLOAD_TEMP_PATTERN,
                )

    def _clean_flat_temporary_files(
        self,
        directory: Path,
        *,
        allowed_directories: set[str],
        temporary_pattern: re.Pattern[str],
    ) -> None:
        """Remove only temporary files reserved by this store version.

        Unknown ``.tmp`` or hidden files may belong to a future schema. They
        are preserved and make startup fail closed so an older Backend cannot
        mutate storage whose recovery rules it does not understand.
        """

        self._require_safe_directory(directory)
        for entry in tuple(directory.iterdir()):
            details = entry.lstat()
            if entry.is_symlink() or self._stat_is_reparse(details):
                raise AttachmentStorageError(
                    "Attachment storage contains an unsafe redirected entry."
                )
            if stat_module.S_ISDIR(details.st_mode):
                if entry.name not in allowed_directories:
                    raise AttachmentStorageError(
                        "Attachment storage contains an unexpected directory."
                    )
                self._require_safe_directory(entry)
                continue
            if not stat_module.S_ISREG(details.st_mode):
                raise AttachmentStorageError(
                    "Attachment storage contains an unsafe entry."
                )
            if temporary_pattern.fullmatch(entry.name) is not None:
                self._unlink_required(entry)
                continue
            if entry.name.startswith(".") or entry.name.endswith(".tmp"):
                raise AttachmentStorageError(
                    "Attachment storage contains an unknown temporary entry."
                )

    def _purge_scope_tree(self, root: Path) -> None:
        """Delete one flat validated scope without following redirected paths."""

        self._require_safe_directory(root)
        try:
            for entry in tuple(root.iterdir()):
                details = entry.lstat()
                if entry.is_symlink() or self._stat_is_reparse(details):
                    raise AttachmentStorageError(
                        "Attachment deletion encountered a redirected entry."
                    )
                if stat_module.S_ISDIR(details.st_mode):
                    if entry.name not in {
                        "originals",
                        "drafts",
                        "committed",
                    }:
                        raise AttachmentStorageError(
                            "Attachment deletion encountered an unexpected directory."
                        )
                    self._require_safe_directory(entry)
                    for child in tuple(entry.iterdir()):
                        child_details = child.lstat()
                        if (
                            child.is_symlink()
                            or self._stat_is_reparse(child_details)
                            or not stat_module.S_ISREG(child_details.st_mode)
                        ):
                            raise AttachmentStorageError(
                                "Attachment deletion encountered an unsafe entry."
                            )
                        child.unlink()
                    entry.rmdir()
                    continue
                if not stat_module.S_ISREG(details.st_mode):
                    raise AttachmentStorageError(
                        "Attachment deletion encountered an unsafe entry."
                    )
                entry.unlink()
            root.rmdir()
        except AttachmentStorageError:
            raise
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment scope could not be purged safely."
            ) from error

    @staticmethod
    def _require_safe_directory(path: Path) -> None:
        try:
            details = path.lstat()
        except OSError as error:
            raise AttachmentStorageError(
                "Attachment storage directory is unavailable."
            ) from error
        if (
            path.is_symlink()
            or JsonAttachmentStore._stat_is_reparse(details)
            or not stat_module.S_ISDIR(details.st_mode)
        ):
            raise AttachmentStorageError(
                "Attachment storage directory is unsafe."
            )

    @classmethod
    def _require_safe_directory_chain(cls, path: Path) -> None:
        """Reject every existing redirected or non-directory path component."""

        for candidate in reversed((path, *path.parents)):
            try:
                details = candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment storage directory is unavailable."
                ) from error
            if (
                stat_module.S_ISLNK(details.st_mode)
                or cls._stat_is_reparse(details)
                or not stat_module.S_ISDIR(details.st_mode)
            ):
                raise AttachmentStorageError(
                    "Attachment storage directory chain is unsafe."
                )

    @staticmethod
    def _require_regular_stat(details: os.stat_result) -> None:
        if (
            not stat_module.S_ISREG(details.st_mode)
            or JsonAttachmentStore._stat_is_reparse(details)
            or details.st_nlink != 1
        ):
            raise AttachmentValidationError(
                "Only regular local files can be attached."
            )

    @staticmethod
    def _stat_is_reparse(details: os.stat_result) -> bool:
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(
            stat_module,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        )
        return bool(attributes & reparse_flag)

    @staticmethod
    def _reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    @staticmethod
    def _reject_json_constant(value: str) -> object:
        raise ValueError(f"invalid JSON constant: {value}")

    @staticmethod
    def _best_effort_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _unlink_required(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise AttachmentStorageError(
                "Temporary attachment data could not be cleaned."
            ) from error

    def _roll_back_stage(
        self,
        candidates: Sequence[_CopiedCandidate],
        blob_paths: Sequence[Path],
    ) -> None:
        for candidate in candidates:
            self._best_effort_unlink(candidate.temporary_path)
        for path in blob_paths:
            self._best_effort_unlink(path)

    @staticmethod
    def _restore_hidden_scopes(
        hidden: Sequence[tuple[AttachmentScope, Path, Path, str]],
    ) -> None:
        """Restore every prepared owner scope before reporting any failure."""

        first_error: BaseException | None = None
        for _scope, scope_root, tombstone, _operation_id in reversed(hidden):
            try:
                if tombstone.exists() or tombstone.is_symlink():
                    JsonAttachmentStore._require_safe_directory(tombstone)
                    if scope_root.exists() or scope_root.is_symlink():
                        raise AttachmentStorageError(
                            "Attachment deletion rollback found a live conflict."
                        )
                    os.replace(tombstone, scope_root)
            except (OSError, AttachmentStorageError) as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise AttachmentStorageError(
                "Attachment deletion rollback failed."
            ) from first_error

    @staticmethod
    def _restore_moves(moves: Sequence[tuple[Path, Path]]) -> None:
        for source, destination in reversed(moves):
            try:
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment commit rollback failed."
                ) from error
