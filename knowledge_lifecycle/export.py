"""Export verified Project Source originals to a trusted native destination.

Export is deliberately separate from the mutating lifecycle journal.  It
copies only the user-owned original bytes, never vectors, prompts, internal
paths, or catalog identifiers, and it revalidates ownership before making the
destination visible.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import os
from pathlib import Path
import stat as stat_module
import sys
from tempfile import mkstemp
from typing import BinaryIO, Final, Protocol

from attachments import AttachmentScope, FileCatalogSnapshot
from chats import ProjectId
from projects import Project
from projects.exceptions import ProjectNotFoundError

from .exceptions import (
    KnowledgeLifecycleConflictError,
    KnowledgeLifecycleNotFoundError,
    KnowledgeLifecycleStorageError,
    KnowledgeLifecycleValidationError,
)


_COPY_BUFFER_BYTES: Final = 1024 * 1024


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
    ) -> None:
        """Compose canonical Project, original-byte, and mutation boundaries."""

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

    def export_original(
        self,
        project_id: ProjectId,
        link_id: str,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> KnowledgeExportResult:
        """Atomically export one verified original chosen by the native UI.

        The destination must be absolute and its parent must already exist.
        A no-overwrite export uses a hard-link publication inside that parent,
        which is atomic and cannot replace a file created by another process.
        """

        if not isinstance(overwrite, bool):
            raise KnowledgeLifecycleValidationError(
                "overwrite must be a boolean."
            )
        target = self._validate_destination(destination)
        scope = self._active_project_scope(project_id)
        temporary_path: Path | None = None
        try:
            with self._mutation_lease.hold_mutation():
                before = self._snapshot_file(scope, link_id)
                ownership = before.ownerships[0]
                original = before.originals[0]
                if ownership.role != "project_source":
                    raise KnowledgeLifecycleValidationError(
                        "Export requires a Project Source ownership."
                    )
                descriptor, temporary_name = mkstemp(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".export.tmp",
                )
                temporary_path = Path(temporary_name)
                bytes_written = 0
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        with self._attachment_service.open_verified_file(
                            scope,
                            ownership.file_id,
                        ) as source:
                            while True:
                                block = source.read(_COPY_BUFFER_BYTES)
                                if not block:
                                    break
                                bytes_written += len(block)
                                if bytes_written > original.size_bytes:
                                    raise KnowledgeLifecycleStorageError(
                                        "Verified export exceeded its declared size."
                                    )
                                output.write(block)
                        if bytes_written != original.size_bytes:
                            raise KnowledgeLifecycleStorageError(
                                "Verified export did not match its declared size."
                            )
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    if descriptor != -1:
                        os.close(descriptor)

                # A deletion or replacement must not race between the verified
                # read and publication.  The shared lease is the primary guard;
                # the same-read fingerprint check also detects a hostile or
                # incorrectly composed adapter.
                after = self._snapshot_file(scope, link_id)
                if after.snapshot_fingerprint != before.snapshot_fingerprint:
                    raise KnowledgeLifecycleConflictError(
                        "Project Source changed during export."
                    )
                self._publish_export(
                    temporary_path,
                    target,
                    overwrite=overwrite,
                )
                temporary_path = None
                return KnowledgeExportResult(
                    file_name=ownership.file_name,
                    media_type=ownership.media_type,
                    bytes_written=bytes_written,
                )
        except (
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
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

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
