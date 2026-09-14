"""Atomic, scope-isolated storage for local attachment blobs and drafts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_module
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import mkstemp
from threading import RLock
from typing import Final, Literal, cast
from uuid import uuid4

from .domain import (
    MAX_JSON_SAFE_INTEGER,
    MAX_SOURCE_PATH_LENGTH,
    AttachmentItem,
    AttachmentScope,
    AttachmentState,
    validate_attachment_id,
    validate_file_name,
    validate_media_type,
)
from .exceptions import (
    AttachmentConflictError,
    AttachmentNotFoundError,
    AttachmentStorageError,
    AttachmentValidationError,
)

DEFAULT_MAX_FILE_COUNT: Final = 10
ATTACHMENT_MANIFEST_SCHEMA_VERSION: Final = 1
_COPY_BUFFER_BYTES: Final = 1024 * 1024
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BLOB_SUFFIX: Final = ".blob"
_PROCESS_LOCK_FILE_NAME: Final = ".backend.lock"

# An extension is only a routing hint in this release: file content is stored,
# never parsed or executed. Stage 8 loaders will perform format-level checks.
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


@dataclass(frozen=True, slots=True)
class _ManifestItem:
    attachment_id: str
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    status: _ManifestStatus = "ready"

    def __post_init__(self) -> None:
        validate_attachment_id(self.attachment_id)
        validate_file_name(self.file_name)
        validate_media_type(self.media_type)
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes <= 0
            or self.size_bytes > MAX_JSON_SAFE_INTEGER
        ):
            raise ValueError("Stored attachment size is invalid.")
        if not isinstance(self.sha256, str) or _HASH_PATTERN.fullmatch(
            self.sha256
        ) is None:
            raise ValueError("Stored attachment digest is invalid.")
        if self.status not in ("ready", "claimed", "committed"):
            raise ValueError("Stored attachment status is invalid.")

    def to_public(self) -> AttachmentItem:
        """Expose metadata without internal digest or lifecycle state."""

        return AttachmentItem(
            attachment_id=self.attachment_id,
            file_name=self.file_name,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
        )


@dataclass(frozen=True, slots=True)
class _CopiedCandidate:
    temporary_path: Path
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str


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
    ) -> None:
        for value, field_name in (
            (max_file_bytes, "max_file_bytes"),
            (max_file_count, "max_file_count"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > MAX_JSON_SAFE_INTEGER
            ):
                raise ValueError(
                    f"{field_name} must be a positive JSON-safe integer."
                )
        self._base_dir = Path(base_dir).absolute()
        self._max_file_bytes = max_file_bytes
        self._max_file_count = max_file_count
        self._lock = RLock()
        self._process_lock_fd: int | None = None
        self._prepare_base_directory()
        self._acquire_process_lock()
        try:
            self._clean_startup_temporary_entries()
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

    def stage_files(
        self,
        scope: AttachmentScope,
        source_paths: Sequence[Path],
        referenced_ids: Iterable[str] = (),
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

        with self._lock:
            self._reconcile_locked(
                scope,
                referenced_ids,
                release_claims=False,
            )
            items = list(self._load_manifest(scope))
            digest_index = {
                (item.sha256, item.size_bytes): item
                for item in items
                if item.status != "committed"
            }
            new_blob_paths: list[Path] = []
            candidates: list[_CopiedCandidate] = []
            try:
                for source_path in paths:
                    candidate = self._copy_candidate(scope, source_path)
                    candidates.append(candidate)
                    duplicate = digest_index.get(
                        (candidate.sha256, candidate.size_bytes)
                    )
                    if duplicate is not None:
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
                    final_path = self._draft_blob_path(scope, attachment_id)
                    if final_path.exists() or final_path.is_symlink():
                        raise AttachmentConflictError(
                            "A generated attachment identifier already exists."
                        )
                    os.replace(candidate.temporary_path, final_path)
                    candidates.remove(candidate)
                    new_blob_paths.append(final_path)
                    item = _ManifestItem(
                        attachment_id=attachment_id,
                        file_name=candidate.file_name,
                        media_type=candidate.media_type,
                        size_bytes=candidate.size_bytes,
                        sha256=candidate.sha256,
                    )
                    items.append(item)
                    digest_index[(item.sha256, item.size_bytes)] = item

                self._write_manifest(scope, items)
            except (AttachmentValidationError, AttachmentConflictError):
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
            items = list(self._load_manifest(scope))
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
            blob_path = self._draft_blob_path(scope, safe_id)
            tombstone = blob_path.with_name(
                f".{safe_id}.remove-{uuid4().hex}.tmp"
            )
            try:
                os.replace(blob_path, tombstone)
                remaining = [entry for entry in items if entry is not item]
                try:
                    self._write_manifest(scope, remaining)
                except Exception:
                    os.replace(tombstone, blob_path)
                    raise
                self._unlink_required(tombstone)
            except AttachmentStorageError:
                raise
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment could not be removed safely."
                ) from error
            return self._state(scope, remaining)

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
            items = list(self._load_manifest(scope))
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
            self._write_manifest(scope, updated)
            return tuple(item.to_public() for item in selected)

    def release_chat_claims(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Release only the named claims after a request fails to launch."""

        self._require_chat_scope(scope)
        safe_ids = set(self._validated_id_tuple(attachment_ids))
        if not safe_ids:
            return self.list_state(scope)
        with self._lock:
            items = list(self._load_manifest(scope))
            updated = [
                replace(item, status="ready")
                if item.attachment_id in safe_ids and item.status == "claimed"
                else item
                for item in items
            ]
            if updated != items:
                self._write_manifest(scope, updated)
            return self._state(scope, updated)

    def mark_committed(
        self,
        scope: AttachmentScope,
        attachment_ids: Iterable[str],
    ) -> AttachmentState:
        """Finalize claimed Chat blobs; Project items remain available.

        Chat blobs move before the manifest is published. A manifest failure
        reverses those moves, while reconciliation uses persisted message
        references to finish a transition interrupted by process termination.
        """

        self._require_scope(scope)
        safe_ids = self._validated_id_tuple(attachment_ids)
        if not safe_ids:
            return self._state(scope, self._load_manifest(scope))
        with self._lock:
            items = list(self._load_manifest(scope))
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

            moved: list[tuple[Path, Path]] = []
            try:
                for attachment_id in safe_ids:
                    source = self._draft_blob_path(scope, attachment_id)
                    destination = self._committed_blob_path(
                        scope,
                        attachment_id,
                    )
                    if destination.exists() or destination.is_symlink():
                        raise AttachmentConflictError(
                            "Committed attachment data already exists."
                        )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, destination)
                    moved.append((source, destination))
                committed_ids = set(safe_ids)
                remaining = [
                    replace(item, status="committed")
                    if item.attachment_id in committed_ids
                    else item
                    for item in items
                ]
                try:
                    self._write_manifest(scope, remaining)
                except Exception:
                    self._restore_moves(moved)
                    raise
            except AttachmentConflictError:
                self._restore_moves(moved)
                raise
            except AttachmentStorageError:
                raise
            except OSError as error:
                self._restore_moves(moved)
                raise AttachmentStorageError(
                    "Chat attachments could not be committed safely."
                ) from error
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

        self._require_scope(scope)
        if not callable(delete_owner):
            raise AttachmentValidationError(
                "Attachment owner deletion callback is invalid."
            )
        with self._lock:
            scope_root = self._scope_root(scope, create=False)
            tombstone: Path | None = None
            if scope_root.exists() or scope_root.is_symlink():
                self._require_safe_directory(scope_root)
                operation_id = uuid4().hex
                tombstone = scope_root.with_name(
                    f".{scope.id}.delete-{operation_id}.pending"
                )
                try:
                    os.replace(scope_root, tombstone)
                except OSError as error:
                    raise AttachmentStorageError(
                        "Attachment scope could not be prepared for deletion."
                    ) from error
            try:
                delete_owner()
            except Exception:
                if tombstone is not None:
                    try:
                        os.replace(tombstone, scope_root)
                    except OSError as restore_error:
                        raise AttachmentStorageError(
                            "Attachment deletion rollback failed."
                        ) from restore_error
                raise
            if tombstone is not None:
                try:
                    committed_tombstone = tombstone.with_name(
                        f".{scope.id}.delete-{operation_id}.tmp"
                    )
                    os.replace(tombstone, committed_tombstone)
                    self._purge_scope_tree(committed_tombstone)
                except (OSError, AttachmentStorageError):
                    # Once the owner is deleted, a hidden tombstone is no
                    # longer live data. Startup either restores an ambiguous
                    # pending rename for owner reconciliation or removes a
                    # committed .tmp tombstone deterministically.
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
        self._clean_scope_temporary_files(scope)
        items = list(self._load_manifest(scope))
        reconciled: list[_ManifestItem] = []
        manifest_ids = {item.attachment_id for item in items}
        missing_metadata = references - manifest_ids
        if missing_metadata:
            raise AttachmentStorageError(
                "Referenced attachment metadata is missing from storage."
            )

        for item in items:
            draft = self._draft_blob_path(scope, item.attachment_id)
            committed = self._committed_blob_path(scope, item.attachment_id)
            if scope.kind == "chat" and item.attachment_id in references:
                self._finish_referenced_transition(item, draft, committed)
                reconciled.append(replace(item, status="committed"))
                continue
            if item.status == "committed":
                if draft.exists() or draft.is_symlink():
                    self._unlink_required(draft)
                if committed.exists() or committed.is_symlink():
                    self._unlink_required(committed)
                continue
            self._restore_uncommitted_transition(draft, committed)
            if not draft.exists() or draft.is_symlink():
                if draft.is_symlink():
                    self._unlink_required(draft)
                continue
            self._verify_blob(draft, item)
            reconciled.append(
                replace(item, status="ready")
                if release_claims and item.status == "claimed"
                else item
            )

        self._remove_orphan_blobs(
            self._drafts_directory(scope),
            {item.attachment_id for item in reconciled},
        )
        committed_references = {
            item.attachment_id
            for item in reconciled
            if item.status == "committed"
        }
        self._remove_orphan_blobs(
            self._committed_directory(scope),
            committed_references,
        )
        self._write_manifest(scope, reconciled)
        return self._state(scope, reconciled)

    def _finish_referenced_transition(
        self,
        item: _ManifestItem,
        draft: Path,
        committed: Path,
    ) -> None:
        """Leave one verified committed copy for a referenced Chat item."""

        if draft.exists() and committed.exists():
            self._verify_blob(draft, item)
            self._verify_blob(committed, item)
            self._unlink_required(draft)
        elif draft.exists():
            self._verify_blob(draft, item)
            committed.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(draft, committed)
            except OSError as error:
                raise AttachmentStorageError(
                    "Referenced attachment data could not be finalized."
                ) from error
        elif committed.exists():
            self._verify_blob(committed, item)
        else:
            raise AttachmentStorageError(
                "Referenced attachment data is missing from storage."
            )

    def _restore_uncommitted_transition(
        self,
        draft: Path,
        committed: Path,
    ) -> None:
        """Restore an unreferenced interrupted move to its draft location."""

        if committed.exists() and draft.exists():
            self._unlink_required(committed)
        elif committed.exists():
            draft.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(committed, draft)
            except OSError as error:
                raise AttachmentStorageError(
                    "Interrupted attachment state could not be recovered."
                ) from error

    def _copy_candidate(
        self,
        scope: AttachmentScope,
        source_path: Path,
    ) -> _CopiedCandidate:
        """Copy and hash one stable regular source into scoped temporary data.

        The descriptor is opened without following links where supported, and
        its identity, size, and modification time are compared before and
        after the streaming copy. The streaming byte limit also catches a file
        that grows after the initial stat call.
        """

        source = self._validate_source_path(source_path)
        file_name = source.name
        extension = source.suffix.casefold()
        media_type = ALLOWED_ATTACHMENT_EXTENSIONS.get(extension)
        if media_type is None:
            raise AttachmentValidationError(
                "The selected file type is not supported."
            )
        drafts = self._drafts_directory(scope)
        drafts.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = mkstemp(
            dir=drafts,
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
            return _CopiedCandidate(
                temporary_path=temporary_path,
                file_name=file_name,
                media_type=media_type,
                size_bytes=copied,
                sha256=digest.hexdigest(),
            )
        except AttachmentValidationError:
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

    def _validate_source_path(self, source_path: Path) -> Path:
        """Reject redirected, UNC, internal, or non-regular sources."""

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
            return resolved
        except AttachmentValidationError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise AttachmentValidationError(
                "The selected file is unavailable or unsafe."
            ) from error

    def _load_manifest(self, scope: AttachmentScope) -> tuple[_ManifestItem, ...]:
        """Load a strict manifest whose transitions can be reconciled uniquely.

        Exact fields, unique IDs, and unique active digests keep recovery and
        content deduplication unambiguous after an interrupted operation.
        """

        manifest_path = self._manifest_path(scope)
        if not manifest_path.exists() and not manifest_path.is_symlink():
            return ()
        if manifest_path.is_symlink():
            raise AttachmentStorageError(
                "Attachment manifest is not a safe regular file."
            )
        try:
            with manifest_path.open("r", encoding="utf-8") as stream:
                raw: object = json.load(
                    stream,
                    object_pairs_hook=self._reject_duplicate_keys,
                    parse_constant=self._reject_json_constant,
                )
            if not isinstance(raw, dict) or set(raw) != {
                "schema_version",
                "scope",
                "items",
            }:
                raise ValueError("manifest fields")
            if raw["schema_version"] != ATTACHMENT_MANIFEST_SCHEMA_VERSION:
                raise ValueError("manifest version")
            raw_scope = raw["scope"]
            if (
                not isinstance(raw_scope, dict)
                or set(raw_scope) != {"kind", "id"}
                or raw_scope["kind"] != scope.kind
                or raw_scope["id"] != scope.id
            ):
                raise ValueError("manifest scope")
            raw_items = raw["items"]
            if not isinstance(raw_items, list):
                raise ValueError("manifest items")
            items = tuple(self._manifest_item_from_value(value) for value in raw_items)
            ids = [item.attachment_id for item in items]
            draft_digests = [
                (item.sha256, item.size_bytes)
                for item in items
                if item.status != "committed"
            ]
            if (
                len(ids) != len(set(ids))
                or len(draft_digests) != len(set(draft_digests))
            ):
                raise ValueError("manifest duplicates")
            if sum(item.status != "committed" for item in items) > (
                self._max_file_count
            ):
                raise ValueError("manifest count")
            return items
        except AttachmentStorageError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise AttachmentStorageError(
                "Attachment manifest is unreadable or invalid."
            ) from error

    def _write_manifest(
        self,
        scope: AttachmentScope,
        items: Sequence[_ManifestItem],
    ) -> None:
        manifest_path = self._manifest_path(scope)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = mkstemp(
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        document = {
            "schema_version": ATTACHMENT_MANIFEST_SCHEMA_VERSION,
            "scope": {"kind": scope.kind, "id": scope.id},
            "items": [self._manifest_item_to_data(item) for item in items],
        }
        try:
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                descriptor = -1
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
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
            "attachment_id",
            "file_name",
            "media_type",
            "size_bytes",
            "sha256",
            "status",
        }:
            raise ValueError("manifest item fields")
        return _ManifestItem(
            attachment_id=cast(str, value["attachment_id"]),
            file_name=cast(str, value["file_name"]),
            media_type=cast(str, value["media_type"]),
            size_bytes=cast(int, value["size_bytes"]),
            sha256=cast(str, value["sha256"]),
            status=cast(_ManifestStatus, value["status"]),
        )

    @staticmethod
    def _manifest_item_to_data(item: _ManifestItem) -> dict[str, object]:
        return {
            "attachment_id": item.attachment_id,
            "file_name": item.file_name,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "status": item.status,
        }

    def _verify_blob(self, path: Path, item: _ManifestItem) -> None:
        """Verify regular-file identity, byte count, and digest from storage."""

        try:
            details = path.lstat()
            if path.is_symlink() or self._stat_is_reparse(details):
                raise AttachmentStorageError(
                    "Stored attachment data is not a safe regular file."
                )
            try:
                self._require_regular_stat(details)
            except AttachmentValidationError as error:
                raise AttachmentStorageError(
                    "Stored attachment data is not a safe regular file."
                ) from error
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(_COPY_BUFFER_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
            if size != item.size_bytes or digest.hexdigest() != item.sha256:
                raise AttachmentStorageError(
                    "Stored attachment data failed integrity validation."
                )
        except AttachmentStorageError:
            raise
        except (OSError, ValueError) as error:
            raise AttachmentStorageError(
                "Stored attachment data could not be verified."
            ) from error

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
                self._unlink_required(entry)
                continue
            attachment_id = (
                entry.name[: -len(_BLOB_SUFFIX)]
                if entry.name.endswith(_BLOB_SUFFIX)
                else ""
            )
            if attachment_id not in retained_ids:
                if entry.is_dir() and not entry.is_symlink():
                    raise AttachmentStorageError(
                        "Attachment storage contains an unexpected directory."
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

    def _manifest_path(self, scope: AttachmentScope) -> Path:
        return self._scope_root(scope) / "manifest.json"

    def _drafts_directory(self, scope: AttachmentScope) -> Path:
        return self._scoped_data_directory(scope, "drafts")

    def _committed_directory(self, scope: AttachmentScope) -> Path:
        return self._scoped_data_directory(scope, "committed")

    def _scoped_data_directory(
        self,
        scope: AttachmentScope,
        name: Literal["drafts", "committed"],
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

    def _draft_blob_path(self, scope: AttachmentScope, attachment_id: str) -> Path:
        validate_attachment_id(attachment_id)
        return self._drafts_directory(scope) / f"{attachment_id}{_BLOB_SUFFIX}"

    def _committed_blob_path(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> Path:
        validate_attachment_id(attachment_id)
        return self._committed_directory(scope) / f"{attachment_id}{_BLOB_SUFFIX}"

    def _clean_startup_temporary_entries(self) -> None:
        """Resolve deletion tombstones conservatively after an interrupted run.

        A ``.tmp`` tombstone means owner deletion committed and can be purged.
        A ``.pending`` tombstone is ambiguous, so it is restored first to avoid
        data loss; ``reconcile_owners`` later removes it only if the canonical
        repository confirms that its owner no longer exists.
        """

        try:
            for kind in ("chat", "project"):
                kind_value = cast(Literal["chat", "project"], kind)
                kind_root = self._kind_root(kind_value, create=False)
                if not kind_root.exists() and not kind_root.is_symlink():
                    continue
                for scope_entry in tuple(kind_root.iterdir()):
                    if (
                        scope_entry.name.startswith(".")
                        and scope_entry.name.endswith(".tmp")
                    ):
                        self._require_safe_directory(scope_entry)
                        self._purge_scope_tree(scope_entry)
                        continue
                    pending_match = _PENDING_DELETE_PATTERN.fullmatch(
                        scope_entry.name
                    )
                    if pending_match is not None:
                        self._require_safe_directory(scope_entry)
                        scope = AttachmentScope(
                            kind=kind_value,
                            id=pending_match.group("scope_id"),
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
            for kind in ("chat", "project"):
                kind_value = cast(Literal["chat", "project"], kind)
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
                    items = self._load_manifest(scope)
                    if any(item.status == "claimed" for item in items):
                        self._write_manifest(
                            scope,
                            [
                                replace(item, status="ready")
                                if item.status == "claimed"
                                else item
                                for item in items
                            ],
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
            allowed_directories={"drafts", "committed"},
        )
        for name in ("drafts", "committed"):
            directory = root / name
            if directory.exists() or directory.is_symlink():
                self._clean_flat_temporary_files(
                    directory,
                    allowed_directories=set(),
                )

    def _clean_flat_temporary_files(
        self,
        directory: Path,
        *,
        allowed_directories: set[str],
    ) -> None:
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
            if entry.name.endswith(".tmp"):
                self._unlink_required(entry)

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
                    if entry.name not in {"drafts", "committed"}:
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
    def _restore_moves(moves: Sequence[tuple[Path, Path]]) -> None:
        for source, destination in reversed(moves):
            try:
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            except OSError as error:
                raise AttachmentStorageError(
                    "Attachment commit rollback failed."
                ) from error
