"""Export verified Project Source originals to a trusted native destination.

Export is deliberately separate from the mutating lifecycle journal.  It
copies only the user-owned original bytes, never vectors, prompts, internal
paths, or catalog identifiers, and it revalidates ownership before making the
destination visible.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat as stat_module
import sys
from tempfile import mkstemp
from threading import Lock
from typing import BinaryIO, cast, Final, Protocol
from uuid import uuid4

from attachments import AttachmentScope, FileCatalogSnapshot
from chats import ProjectId
from projects import Project
from projects.exceptions import ProjectNotFoundError

from .exceptions import (
    KnowledgeExportCancelledError,
    KnowledgeLifecycleConflictError,
    KnowledgeLifecycleError,
    KnowledgeLifecycleNotFoundError,
    KnowledgeLifecycleStorageError,
    KnowledgeLifecycleValidationError,
)


_COPY_BUFFER_BYTES: Final = 1024 * 1024
_EXPORT_INTENT_SCHEMA_VERSION: Final = 1
_MAX_EXPORT_INTENT_BYTES: Final = 4096
_MAX_EXPORT_INTENTS: Final = 128
_MAX_EXPORT_PATH_CHARACTERS: Final = 32767
_EXPORT_INTENT_FILE_PATTERN: Final = re.compile(
    r"^export-[0-9a-f]{32}\.json$"
)
_PENDING_INTENT_FILE_PATTERN: Final = re.compile(
    r"^\.intent\.[A-Za-z0-9_-]{6,64}\.tmp$"
)
_TEMPORARY_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_EXPORT_INTENT_KEYS: Final = frozenset(
    {
        "schema_version",
        "target_name",
        "temporary_path",
        "parent_path",
        "temporary_device",
        "temporary_inode",
        "parent_device",
        "parent_inode",
    }
)


class _ProjectReader(Protocol):
    """Read canonical Projects without exposing repository storage."""

    def get_project(self, project_id: ProjectId) -> Project:
        """Return one canonical Project or raise a typed not-found error."""

        ...


class _VerifiedOriginalReader(Protocol):
    """Read atomic metadata and verified original bytes by opaque identity."""

    def snapshot_file(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> FileCatalogSnapshot:
        """Return one exact ownership/original snapshot."""

        ...

    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> AbstractContextManager[BinaryIO]:
        """Open a hash-verified original inside its authorized scope."""

        ...


class _MutationLease(Protocol):
    """Serialize export revalidation with Project Source mutations."""

    def hold_mutation(self) -> AbstractContextManager[None]:
        """Hold the shared Project Source mutation lease."""

        ...


@dataclass(frozen=True, slots=True)
class _ExportCleanupIntent:
    """Pin one external temporary export and its private durable record."""

    record_path: Path
    record_device: int
    record_inode: int
    target_name: str
    temporary_path: Path
    parent_path: Path
    temporary_device: int
    temporary_inode: int
    parent_device: int
    parent_inode: int


@dataclass(frozen=True, slots=True)
class KnowledgeExportResult:
    """Report a completed original-byte export without leaking internal IDs."""

    file_name: str
    media_type: str
    bytes_written: int

    def __post_init__(self) -> None:
        """Require a non-empty public label and exact non-negative byte count."""

        if type(self.file_name) is not str or not self.file_name:
            raise KnowledgeLifecycleValidationError(
                "Exported file name is invalid."
            )
        if type(self.media_type) is not str or not self.media_type:
            raise KnowledgeLifecycleValidationError(
                "Exported media type is invalid."
            )
        if type(self.bytes_written) is not int or self.bytes_written < 0:
            raise KnowledgeLifecycleValidationError(
                "Exported byte count is invalid."
            )


class KnowledgeExportService:
    """Copy one owned Project Source through a verified, atomic boundary."""

    def __init__(
        self,
        project_repository: _ProjectReader,
        attachment_service: _VerifiedOriginalReader,
        mutation_lease: _MutationLease,
        intent_directory: Path | None = None,
    ) -> None:
        """Compose boundaries and configure private cleanup-intent storage.

        ``intent_directory`` is optional for compatibility with embedded
        callers. Production supplies one private absolute directory so a hard
        process termination cannot strand original bytes in an external
        destination directory without a safely verifiable cleanup record.
        External paths are deliberately untouched during construction;
        callers schedule :meth:`recover_pending_exports` outside startup's
        request-processing thread.
        """

        if any(
            dependency is None
            for dependency in (
                project_repository,
                attachment_service,
                mutation_lease,
            )
        ):
            raise TypeError("Knowledge export dependencies are required.")
        self._project_repository = project_repository
        self._attachment_service = attachment_service
        self._mutation_lease = mutation_lease
        self._intent_lock = Lock()
        self._intent_directory: Path | None = None
        self._intent_directory_identity: tuple[int, int] | None = None
        if intent_directory is not None:
            self._configure_intent_directory(intent_directory)

    def export_original(
        self,
        project_id: ProjectId,
        link_id: str,
        destination: Path,
        *,
        overwrite: bool = False,
        should_cancel: Callable[[], bool] | None = None,
    ) -> KnowledgeExportResult:
        """Atomically export one verified original chosen by the native UI.

        The destination must be absolute and its parent must already exist.
        A no-overwrite export uses a hard-link publication inside that parent,
        which is atomic and cannot replace a file created by another process.
        ``should_cancel`` is an optional cooperative predicate: cancellation
        that wins before atomic publication raises
        :class:`KnowledgeExportCancelledError` and leaves no target behind.
        """

        if not isinstance(overwrite, bool):
            raise KnowledgeLifecycleValidationError(
                "overwrite must be a boolean."
            )
        if should_cancel is not None and not callable(should_cancel):
            raise KnowledgeLifecycleValidationError(
                "should_cancel must be callable or None."
            )
        self._raise_if_cancelled(should_cancel)
        target = self._validate_destination(destination)
        scope = self._active_project_scope(project_id)
        temporary_path: Path | None = None
        temporary_identity: tuple[int, int] | None = None
        parent_identity: tuple[int, int] | None = None
        cleanup_intent: _ExportCleanupIntent | None = None
        descriptor = -1
        try:
            with self._intent_lock, self._mutation_lease.hold_mutation():
                before = self._snapshot_file(scope, link_id)
                ownership = before.ownerships[0]
                original = before.originals[0]
                if ownership.role != "project_source":
                    raise KnowledgeLifecycleValidationError(
                        "Export requires a Project Source ownership."
                    )
                # Cancellation is checked immediately before creating an
                # externally visible temporary file so an already-cancelled
                # request leaves the destination directory untouched.
                self._raise_if_cancelled(should_cancel)
                descriptor, temporary_name = mkstemp(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".export.tmp",
                )
                temporary_path = Path(temporary_name)
                temporary_identity, parent_identity = (
                    self._pin_new_temporary_export(
                        descriptor,
                        temporary_path,
                        target,
                    )
                )
                cleanup_intent = self._record_export_intent(
                    temporary_path,
                    target,
                    temporary_identity,
                    parent_identity,
                )
                # Revalidate after durable intent publication and before any
                # original byte is read. A swapped path therefore either
                # fails with an actionable recovery record or leaves only the
                # still-open empty descriptor behind.
                self._require_external_identity(
                    temporary_path,
                    target.parent,
                    temporary_identity,
                    parent_identity,
                )
                bytes_written = 0
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    with self._attachment_service.open_verified_file(
                        scope,
                        ownership.file_id,
                    ) as source:
                        while True:
                            self._raise_if_cancelled(should_cancel)
                            block = source.read(_COPY_BUFFER_BYTES)
                            # A verified reader may block in native or
                            # filesystem I/O.  Recheck before copying the
                            # returned bytes so cancellation that arrived
                            # during that wait cannot advance publication.
                            self._raise_if_cancelled(should_cancel)
                            if not block:
                                break
                            bytes_written += len(block)
                            if bytes_written > original.size_bytes:
                                raise KnowledgeLifecycleStorageError(
                                    "Verified export exceeded its declared size."
                                )
                            output.write(block)
                            self._raise_if_cancelled(should_cancel)
                    if bytes_written != original.size_bytes:
                        raise KnowledgeLifecycleStorageError(
                            "Verified export did not match its declared size."
                        )
                    output.flush()
                    os.fsync(output.fileno())

                # A deletion or replacement must not race between the verified
                # read and publication.  The shared lease is the primary guard;
                # the same-read fingerprint check also detects a hostile or
                # incorrectly composed adapter.
                after = self._snapshot_file(scope, link_id)
                if after.snapshot_fingerprint != before.snapshot_fingerprint:
                    raise KnowledgeLifecycleConflictError(
                        "Project Source changed during export."
                    )
                # Publication is the irreversible boundary.  The final check
                # ensures cancellation can only win while the temporary file
                # is still removable by the outer ``finally`` block.
                self._raise_if_cancelled(should_cancel)
                if temporary_identity is None or parent_identity is None:
                    raise KnowledgeLifecycleStorageError(
                        "Export temporary identity was not retained."
                    )
                # Copying can take long enough for a removable drive or an
                # external directory to be renamed and recreated. Re-pin the
                # path immediately before publication so `_publish_export`
                # cannot publish an attacker-controlled replacement while the
                # verified descriptor points at the original directory.
                self._require_external_identity(
                    temporary_path,
                    target.parent,
                    temporary_identity,
                    parent_identity,
                )
                self._publish_export(
                    temporary_path,
                    target,
                    overwrite=overwrite,
                )
                temporary_path = None
                if cleanup_intent is not None:
                    try:
                        self._clear_export_intent(cleanup_intent)
                    except KnowledgeLifecycleStorageError:
                        # Publication is already irreversible and complete.
                        # Reporting failure here would invite a retry against
                        # a target that actually exists. Retaining the intent
                        # is safe: startup recovery sees the missing temp under
                        # the pinned parent and clears the private record.
                        pass
                    cleanup_intent = None
                return KnowledgeExportResult(
                    file_name=ownership.file_name,
                    media_type=ownership.media_type,
                    bytes_written=bytes_written,
                )
        except (
            KnowledgeExportCancelledError,
            KnowledgeLifecycleConflictError,
            KnowledgeLifecycleNotFoundError,
            KnowledgeLifecycleStorageError,
            KnowledgeLifecycleValidationError,
        ):
            raise
        except FileExistsError:
            raise KnowledgeLifecycleConflictError(
                "Export destination already exists."
            ) from None
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Project Source export failed safely."
            ) from None
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Project Source export failed safely."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)
            if temporary_path is not None:
                if cleanup_intent is not None:
                    self._cleanup_recorded_temporary(cleanup_intent)
                    cleanup_intent = None
                elif temporary_identity is not None and parent_identity is not None:
                    self._cleanup_unrecorded_temporary(
                        temporary_path,
                        target.parent,
                        temporary_identity,
                        parent_identity,
                    )

    def _configure_intent_directory(self, directory: object) -> None:
        """Create and pin the private directory that owns cleanup intents."""

        if (
            not isinstance(directory, Path)
            or not directory.is_absolute()
            or ".." in directory.parts
        ):
            raise KnowledgeLifecycleValidationError(
                "Export cleanup intent directory must be an absolute Path."
            )
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self._require_safe_directory_chain(directory)
            details = directory.lstat()
        except (KnowledgeLifecycleError, OSError):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent storage is unavailable."
            ) from None
        if (
            not stat_module.S_ISDIR(details.st_mode)
            or stat_module.S_ISLNK(details.st_mode)
            or self._is_reparse(details)
            or details.st_ino <= 0
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent storage is unsafe."
            )
        self._intent_directory = directory
        self._intent_directory_identity = (details.st_dev, details.st_ino)

    def recover_pending_exports(self) -> None:
        """Recover durable external temps outside the startup request thread.

        The caller owns scheduling. Each intent is fail-closed: corruption,
        an unavailable destination, or any identity mismatch raises a
        sanitized storage error while preserving both the record and the path.
        """

        if self._intent_directory is None:
            return
        with self._intent_lock:
            self._recover_export_intents_locked()

    def _recover_export_intents_locked(self) -> None:
        """Remove only external temps proven by durable pinned identities."""

        directory = self._require_intent_directory()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intents cannot be enumerated."
            ) from None
        if len(entries) > _MAX_EXPORT_INTENTS:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent capacity is exceeded."
            )
        failures = 0
        first_failure: KnowledgeLifecycleStorageError | None = None
        for entry in entries:
            if _PENDING_INTENT_FILE_PATTERN.fullmatch(entry.name) is not None:
                # The atomic writer publishes its intent before reading any
                # original byte. A leftover writer temp therefore contains no
                # authority to an external file and is safe to remove only
                # after its own private identity has been pinned.
                try:
                    self._remove_private_pending_intent(entry)
                except KnowledgeLifecycleStorageError as error:
                    failures += 1
                    first_failure = first_failure or error
                continue
            if _EXPORT_INTENT_FILE_PATTERN.fullmatch(entry.name) is None:
                failures += 1
                first_failure = first_failure or KnowledgeLifecycleStorageError(
                    "Export cleanup intent storage contains an unsafe entry."
                )
                continue
            try:
                payload, record_identity = self._read_intent_bytes(entry)
                intent = self._decode_export_intent(
                    entry,
                    record_identity,
                    payload,
                )
                self._cleanup_recorded_temporary(intent)
            except KnowledgeLifecycleStorageError as error:
                # One unreachable drive or corrupt record must not retain
                # unrelated verified originals. Preserve the failing intent,
                # continue the bounded scan, then report one path-free
                # aggregate maintenance failure to the background caller.
                failures += 1
                first_failure = first_failure or error
        if failures:
            if failures == 1 and first_failure is not None:
                raise first_failure
            raise KnowledgeLifecycleStorageError(
                "One or more export cleanup intents require attention."
            )

    def _pin_new_temporary_export(
        self,
        descriptor: int,
        temporary_path: Path,
        target: Path,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        """Pin the exact new temp and parent before recording cleanup intent."""

        if (
            descriptor < 0
            or not temporary_path.is_absolute()
            or temporary_path.parent != target.parent
            or not self._is_export_temporary_name(
                temporary_path.name,
                target.name,
            )
        ):
            raise KnowledgeLifecycleStorageError(
                "Export temporary file identity is invalid."
            )
        self._require_safe_directory_chain(target.parent)
        try:
            opened = os.fstat(descriptor)
            linked = temporary_path.lstat()
            parent = target.parent.lstat()
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export temporary file identity is unavailable."
            ) from None
        if (
            not stat_module.S_ISREG(opened.st_mode)
            or not stat_module.S_ISREG(linked.st_mode)
            or stat_module.S_ISLNK(linked.st_mode)
            or self._is_reparse(linked)
            or opened.st_dev != linked.st_dev
            or opened.st_ino != linked.st_ino
            or opened.st_ino <= 0
            or opened.st_nlink != 1
            or not stat_module.S_ISDIR(parent.st_mode)
            or stat_module.S_ISLNK(parent.st_mode)
            or self._is_reparse(parent)
            or parent.st_ino <= 0
        ):
            raise KnowledgeLifecycleStorageError(
                "Export temporary file identity is unsafe."
            )
        return (
            (opened.st_dev, opened.st_ino),
            (parent.st_dev, parent.st_ino),
        )

    def _record_export_intent(
        self,
        temporary_path: Path,
        target: Path,
        temporary_identity: tuple[int, int],
        parent_identity: tuple[int, int],
    ) -> _ExportCleanupIntent | None:
        """Atomically persist and fsync one cleanup intent before byte copy."""

        if self._intent_directory is None:
            return None
        directory = self._require_intent_directory()
        try:
            entry_count = 0
            for _entry in directory.iterdir():
                entry_count += 1
                if entry_count >= _MAX_EXPORT_INTENTS:
                    break
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent capacity is unavailable."
            ) from None
        if entry_count >= _MAX_EXPORT_INTENTS:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent capacity is exceeded."
            )
        document = {
            "schema_version": _EXPORT_INTENT_SCHEMA_VERSION,
            "target_name": target.name,
            "temporary_path": str(temporary_path),
            "parent_path": str(target.parent),
            "temporary_device": temporary_identity[0],
            "temporary_inode": temporary_identity[1],
            "parent_device": parent_identity[0],
            "parent_inode": parent_identity[1],
        }
        try:
            payload = (
                json.dumps(
                    document,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        except (MemoryError, RecursionError, TypeError, ValueError):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent cannot be serialized safely."
            ) from None
        if len(payload) > _MAX_EXPORT_INTENT_BYTES:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent exceeds its storage limit."
            )

        intent_path = directory / f"export-{uuid4().hex}.json"
        descriptor = -1
        pending_path: Path | None = None
        try:
            descriptor, pending_name = mkstemp(
                dir=directory,
                prefix=".intent.",
                suffix=".tmp",
            )
            pending_path = Path(pending_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._require_intent_directory()
            os.replace(pending_path, intent_path)
            pending_path = None
            self._fsync_directory(directory)
            stored_payload, record_identity = self._read_intent_bytes(
                intent_path
            )
            if stored_payload != payload:
                raise KnowledgeLifecycleStorageError(
                    "Export cleanup intent changed during publication."
                )
            return self._decode_export_intent(
                intent_path,
                record_identity,
                stored_payload,
            )
        except KnowledgeLifecycleStorageError:
            raise
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent could not be published safely."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)
            if pending_path is not None:
                try:
                    pending_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _read_intent_bytes(
        self,
        path: Path,
    ) -> tuple[bytes, tuple[int, int]]:
        """Read one bounded private intent through a pinned no-follow handle."""

        directory = self._require_intent_directory()
        if (
            path.parent != directory
            or _EXPORT_INTENT_FILE_PATTERN.fullmatch(path.name) is None
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent path is invalid."
            )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            linked = path.lstat()
            if (
                not stat_module.S_ISREG(opened.st_mode)
                or stat_module.S_ISLNK(linked.st_mode)
                or self._is_reparse(linked)
                or opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
                or opened.st_ino <= 0
                or opened.st_nlink != 1
                or opened.st_size > _MAX_EXPORT_INTENT_BYTES
            ):
                raise KnowledgeLifecycleStorageError(
                    "Export cleanup intent storage is unsafe."
                )
            chunks: list[bytes] = []
            remaining = _MAX_EXPORT_INTENT_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > _MAX_EXPORT_INTENT_BYTES:
                raise KnowledgeLifecycleStorageError(
                    "Export cleanup intent exceeds its storage limit."
                )
            opened_after = os.fstat(descriptor)
            linked_after = path.lstat()
            if (
                opened.st_dev != opened_after.st_dev
                or opened.st_ino != opened_after.st_ino
                or opened.st_size != opened_after.st_size
                or opened.st_mtime_ns != opened_after.st_mtime_ns
                or opened.st_dev != linked_after.st_dev
                or opened.st_ino != linked_after.st_ino
                or stat_module.S_ISLNK(linked_after.st_mode)
                or self._is_reparse(linked_after)
                or opened_after.st_nlink != 1
            ):
                raise KnowledgeLifecycleStorageError(
                    "Export cleanup intent changed during its read."
                )
            return payload, (opened.st_dev, opened.st_ino)
        except KnowledgeLifecycleStorageError:
            raise
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent is unreadable."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)

    @staticmethod
    def _unique_json_object(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        """Reject duplicate JSON fields instead of accepting ambiguous intent."""

        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate export cleanup intent field.")
            result[key] = value
        return result

    def _decode_export_intent(
        self,
        record_path: Path,
        record_identity: tuple[int, int],
        payload: bytes,
    ) -> _ExportCleanupIntent:
        """Validate one closed cleanup-intent document without path effects."""

        try:
            value = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=self._unique_json_object,
            )
        except (
            MemoryError,
            RecursionError,
            UnicodeDecodeError,
            ValueError,
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent is corrupted."
            ) from None
        if type(value) is not dict or set(value) != _EXPORT_INTENT_KEYS:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent schema is invalid."
            )
        raw = value
        if (
            type(raw["schema_version"]) is not int
            or raw["schema_version"] != _EXPORT_INTENT_SCHEMA_VERSION
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent version is unsupported."
            )
        string_fields = (
            raw["target_name"],
            raw["temporary_path"],
            raw["parent_path"],
        )
        identity_fields = (
            raw["temporary_device"],
            raw["temporary_inode"],
            raw["parent_device"],
            raw["parent_inode"],
        )
        if any(type(item) is not str or not item for item in string_fields):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent paths are invalid."
            )
        if any(
            type(item) is not int or item < 0
            for item in identity_fields
        ) or cast(int, raw["temporary_inode"]) <= 0 or cast(
            int,
            raw["parent_inode"],
        ) <= 0:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent identities are invalid."
            )
        target_name = cast(str, raw["target_name"])
        temporary_text = cast(str, raw["temporary_path"])
        parent_text = cast(str, raw["parent_path"])
        if (
            len(target_name) > 255
            or len(temporary_text) > _MAX_EXPORT_PATH_CHARACTERS
            or len(parent_text) > _MAX_EXPORT_PATH_CHARACTERS
            or Path(target_name).name != target_name
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent paths exceed safe limits."
            )
        temporary_path = Path(temporary_text)
        parent_path = Path(parent_text)
        if (
            not temporary_path.is_absolute()
            or not parent_path.is_absolute()
            or ".." in temporary_path.parts
            or ".." in parent_path.parts
            or temporary_path.parent != parent_path
            or not self._is_export_temporary_name(
                temporary_path.name,
                target_name,
            )
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent paths are inconsistent."
            )
        return _ExportCleanupIntent(
            record_path=record_path,
            record_device=record_identity[0],
            record_inode=record_identity[1],
            target_name=target_name,
            temporary_path=temporary_path,
            parent_path=parent_path,
            temporary_device=cast(int, raw["temporary_device"]),
            temporary_inode=cast(int, raw["temporary_inode"]),
            parent_device=cast(int, raw["parent_device"]),
            parent_inode=cast(int, raw["parent_inode"]),
        )

    def _cleanup_recorded_temporary(
        self,
        intent: _ExportCleanupIntent,
    ) -> None:
        """Delete one recorded temp only while every pinned identity matches."""

        parent_identity = (intent.parent_device, intent.parent_inode)
        self._require_external_parent(intent.parent_path, parent_identity)
        try:
            details = intent.temporary_path.lstat()
        except FileNotFoundError:
            # Publication moves or unlinks the temp. Missing is safe only after
            # the recorded parent identity proves the lookup stayed in the
            # same destination directory.
            self._clear_export_intent(intent)
            return
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Recorded export temporary file is unreadable."
            ) from None
        temporary_identity = (
            intent.temporary_device,
            intent.temporary_inode,
        )
        self._validate_external_temporary(
            intent.temporary_path,
            intent.target_name,
            details,
            temporary_identity,
        )
        try:
            confirmed = intent.temporary_path.lstat()
            self._validate_external_temporary(
                intent.temporary_path,
                intent.target_name,
                confirmed,
                temporary_identity,
            )
            intent.temporary_path.unlink()
            self._fsync_directory(intent.parent_path)
        except KnowledgeLifecycleStorageError:
            raise
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Recorded export temporary file could not be removed."
            ) from None
        self._clear_export_intent(intent)

    def _cleanup_unrecorded_temporary(
        self,
        temporary_path: Path,
        parent_path: Path,
        temporary_identity: tuple[int, int],
        parent_identity: tuple[int, int],
    ) -> None:
        """Remove an empty pre-intent temp without trusting its path alone."""

        self._require_external_parent(parent_path, parent_identity)
        try:
            details = temporary_path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Unrecorded export temporary file is unreadable."
            ) from None
        if (
            not stat_module.S_ISREG(details.st_mode)
            or stat_module.S_ISLNK(details.st_mode)
            or self._is_reparse(details)
            or (details.st_dev, details.st_ino) != temporary_identity
        ):
            raise KnowledgeLifecycleStorageError(
                "Unrecorded export temporary file identity changed."
            )
        try:
            temporary_path.unlink()
            self._fsync_directory(parent_path)
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Unrecorded export temporary file could not be removed."
            ) from None

    def _require_external_identity(
        self,
        temporary_path: Path,
        parent_path: Path,
        temporary_identity: tuple[int, int],
        parent_identity: tuple[int, int],
    ) -> None:
        """Revalidate an export temp and parent after intent publication."""

        self._require_external_parent(parent_path, parent_identity)
        try:
            details = temporary_path.lstat()
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export temporary file identity changed before copy."
            ) from None
        self._validate_external_temporary(
            temporary_path,
            self._target_name_from_temporary(temporary_path.name),
            details,
            temporary_identity,
        )

    def _require_external_parent(
        self,
        parent_path: Path,
        expected_identity: tuple[int, int],
    ) -> None:
        """Require the original no-follow parent and every safe ancestor."""

        try:
            self._require_safe_directory_chain(parent_path)
            details = parent_path.lstat()
        except (KnowledgeLifecycleError, OSError):
            raise KnowledgeLifecycleStorageError(
                "Recorded export parent identity is unavailable."
            ) from None
        if (
            not stat_module.S_ISDIR(details.st_mode)
            or stat_module.S_ISLNK(details.st_mode)
            or self._is_reparse(details)
            or (details.st_dev, details.st_ino) != expected_identity
        ):
            raise KnowledgeLifecycleStorageError(
                "Recorded export parent identity changed."
            )

    def _validate_external_temporary(
        self,
        path: Path,
        target_name: str,
        details: os.stat_result,
        expected_identity: tuple[int, int],
    ) -> None:
        """Reject redirected, renamed, or identity-replaced external temps."""

        if (
            not self._is_export_temporary_name(path.name, target_name)
            or not stat_module.S_ISREG(details.st_mode)
            or stat_module.S_ISLNK(details.st_mode)
            or self._is_reparse(details)
            or (details.st_dev, details.st_ino) != expected_identity
        ):
            raise KnowledgeLifecycleStorageError(
                "Recorded export temporary file identity changed."
            )

    def _clear_export_intent(self, intent: _ExportCleanupIntent) -> None:
        """Atomically remove one private intent only by its pinned identity."""

        directory = self._require_intent_directory()
        if (
            intent.record_path.parent != directory
            or _EXPORT_INTENT_FILE_PATTERN.fullmatch(
                intent.record_path.name
            )
            is None
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent record path is invalid."
            )
        try:
            details = intent.record_path.lstat()
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent record disappeared."
            ) from None
        if (
            not stat_module.S_ISREG(details.st_mode)
            or stat_module.S_ISLNK(details.st_mode)
            or self._is_reparse(details)
            or details.st_nlink != 1
            or (details.st_dev, details.st_ino)
            != (intent.record_device, intent.record_inode)
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent record identity changed."
            )
        try:
            intent.record_path.unlink()
            self._fsync_directory(directory)
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent record could not be cleared."
            ) from None

    def _remove_private_pending_intent(self, path: Path) -> None:
        """Remove only a regular atomic-writer temp in the pinned directory."""

        directory = self._require_intent_directory()
        if (
            path.parent != directory
            or _PENDING_INTENT_FILE_PATTERN.fullmatch(path.name) is None
        ):
            raise KnowledgeLifecycleStorageError(
                "Pending export cleanup intent path is invalid."
            )
        try:
            first = path.lstat()
            second = path.lstat()
            if (
                not stat_module.S_ISREG(first.st_mode)
                or stat_module.S_ISLNK(first.st_mode)
                or self._is_reparse(first)
                or first.st_nlink != 1
                or (first.st_dev, first.st_ino)
                != (second.st_dev, second.st_ino)
            ):
                raise KnowledgeLifecycleStorageError(
                    "Pending export cleanup intent is unsafe."
                )
            path.unlink()
            self._fsync_directory(directory)
        except KnowledgeLifecycleStorageError:
            raise
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Pending export cleanup intent cannot be removed."
            ) from None

    def _require_intent_directory(self) -> Path:
        """Return the app-private directory only if its identity is unchanged."""

        directory = self._intent_directory
        identity = self._intent_directory_identity
        if directory is None or identity is None:
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent storage is not configured."
            )
        try:
            self._require_safe_directory_chain(directory)
            details = directory.lstat()
        except (KnowledgeLifecycleError, OSError):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent storage is unavailable."
            ) from None
        if (
            not stat_module.S_ISDIR(details.st_mode)
            or stat_module.S_ISLNK(details.st_mode)
            or self._is_reparse(details)
            or (details.st_dev, details.st_ino) != identity
        ):
            raise KnowledgeLifecycleStorageError(
                "Export cleanup intent storage identity changed."
            )
        return directory

    @staticmethod
    def _is_export_temporary_name(name: str, target_name: str) -> bool:
        """Accept only the exact mkstemp naming shape used by export."""

        prefix = f".{target_name}."
        suffix = ".export.tmp"
        if not name.startswith(prefix) or not name.endswith(suffix):
            return False
        token = name[len(prefix) : -len(suffix)]
        return _TEMPORARY_TOKEN_PATTERN.fullmatch(token) is not None

    @staticmethod
    def _target_name_from_temporary(name: str) -> str:
        """Recover the target prefix from a freshly generated temp name."""

        suffix = ".export.tmp"
        body = name[1 : -len(suffix)]
        target_name, _separator, _token = body.rpartition(".")
        return target_name

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Durably order directory-entry changes where the platform permits."""

        if sys.platform == "win32":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _raise_if_cancelled(
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        """Raise a typed failure while export remains safely unpublished."""

        if should_cancel is not None and should_cancel():
            raise KnowledgeExportCancelledError(
                "Project Source export was cancelled before publication."
            )

    def _active_project_scope(self, project_id: object) -> AttachmentScope:
        """Resolve one active canonical Project into its attachment scope."""

        if type(project_id) is not str:
            raise KnowledgeLifecycleValidationError(
                "Project identifier is invalid."
            )
        try:
            project = self._project_repository.get_project(ProjectId(project_id))
        except ProjectNotFoundError:
            raise KnowledgeLifecycleNotFoundError(
                "Project is unavailable."
            ) from None
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Project authority could not be loaded."
            ) from None
        if (
            type(project) is not Project
            or str(project.project_id) != project_id
            or project.is_archived
        ):
            raise KnowledgeLifecycleConflictError(
                "Project is not active."
            )
        try:
            return AttachmentScope(kind="project", id=project_id)
        except ValueError:
            raise KnowledgeLifecycleValidationError(
                "Project identifier is invalid."
            ) from None

    def _snapshot_file(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> FileCatalogSnapshot:
        """Load one exact snapshot and sanitize every adapter failure."""

        try:
            snapshot = self._attachment_service.snapshot_file(scope, link_id)
        except Exception:
            raise KnowledgeLifecycleNotFoundError(
                "Project Source is unavailable."
            ) from None
        if (
            type(snapshot) is not FileCatalogSnapshot
            or snapshot.scope != scope
            or len(snapshot.ownerships) != 1
            or len(snapshot.originals) != 1
            or snapshot.ownerships[0].link_id != link_id
            or snapshot.ownerships[0].file_id
            != snapshot.originals[0].file_id
        ):
            raise KnowledgeLifecycleStorageError(
                "Project Source metadata is inconsistent."
            )
        return snapshot

    @classmethod
    def _validate_destination(cls, destination: object) -> Path:
        """Return an absolute non-redirected native destination path."""

        if not isinstance(destination, Path) or not destination.is_absolute():
            raise KnowledgeLifecycleValidationError(
                "Export destination must be an absolute Path."
            )
        if not destination.name or destination.name in (".", ".."):
            raise KnowledgeLifecycleValidationError(
                "Export destination is invalid."
            )
        parent = destination.parent
        cls._require_safe_directory_chain(parent)
        if not parent.is_dir():
            raise KnowledgeLifecycleValidationError(
                "Export destination directory does not exist."
            )
        if destination.exists() or destination.is_symlink():
            try:
                details = destination.lstat()
            except OSError:
                raise KnowledgeLifecycleStorageError(
                    "Export destination is unreadable."
                ) from None
            if (
                not stat_module.S_ISREG(details.st_mode)
                or stat_module.S_ISLNK(details.st_mode)
                or cls._is_reparse(details)
                or details.st_nlink != 1
            ):
                raise KnowledgeLifecycleValidationError(
                    "Export destination is not a safe regular file."
                )
        return destination

    @classmethod
    def _require_safe_directory_chain(cls, directory: Path) -> None:
        """Reject symlink or Windows reparse components in an existing parent."""

        if not directory.exists():
            raise KnowledgeLifecycleValidationError(
                "Export destination directory does not exist."
            )
        current = directory
        while True:
            try:
                details = current.lstat()
            except OSError:
                raise KnowledgeLifecycleStorageError(
                    "Export destination directory is unreadable."
                ) from None
            if (
                not stat_module.S_ISDIR(details.st_mode)
                or stat_module.S_ISLNK(details.st_mode)
                or cls._is_reparse(details)
            ):
                raise KnowledgeLifecycleValidationError(
                    "Export destination directory is redirected."
                )
            if current.parent == current:
                break
            current = current.parent

    @staticmethod
    def _is_reparse(details: os.stat_result) -> bool:
        """Return whether Windows marks a filesystem entry as redirected."""

        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(attributes & reparse_flag)

    @staticmethod
    def _publish_export(
        temporary_path: Path,
        target: Path,
        *,
        overwrite: bool,
    ) -> None:
        """Publish flushed bytes atomically with exact overwrite semantics."""

        if overwrite:
            os.replace(temporary_path, target)
        else:
            # Both paths share a parent, so a hard link is an atomic
            # create-if-absent publication on supported local filesystems.
            os.link(temporary_path, target)
            temporary_path.unlink()
        if sys.platform != "win32":
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
