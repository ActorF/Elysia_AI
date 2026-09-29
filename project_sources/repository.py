"""Persist exact Project Source catalog snapshots with CAS revisions."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat as stat_module
import sys
from tempfile import mkstemp
from threading import Lock, RLock
import time
from typing import BinaryIO, Final, Iterator, Literal, Protocol, cast

from attachments import AttachmentScope
from documents import (
    MAX_RETRIEVAL_DOCUMENTS,
    DocumentError,
    DocumentSource,
    ExpectedDocumentGeneration,
    GroundedAnswerStyle,
)

from .domain import (
    PROJECT_SOURCE_GENERATION_SCHEMA_VERSION,
    PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
    PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
    ProjectSourceGeneration,
    ProjectSourceInstructions,
    ProjectSourceSnapshot,
)
from .exceptions import (
    ProjectSourceConflictError,
    ProjectSourceDataCorruptionError,
    ProjectSourceNotFoundError,
    ProjectSourceStorageError,
    ProjectSourceValidationError,
)


PROJECT_SOURCE_CATALOG_SCHEMA_VERSION: Final[Literal[2]] = 2
_LEGACY_PROJECT_SOURCE_CATALOG_SCHEMA_VERSION: Final[Literal[1]] = 1
_CATALOG_FILE_NAME: Final = "project_sources.json"
_CATALOG_LOCK_FILE_NAME: Final = ".project_sources.lock"
_MAX_CATALOG_BYTES: Final = 16 * 1024 * 1024
_MAX_CATALOG_ENTRIES: Final = 10_000
_FILE_LOCK_TIMEOUT_SECONDS: Final = 2.0
_PATH_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[Path, RLock] = {}


@dataclass(frozen=True, slots=True, init=False)
class CatalogEntrySnapshot:
    """Expose one atomic live-catalog or tombstone observation.

    Revision zero means the Project has never had a catalog entry.  A positive
    revision with no snapshot is a durable revocation tombstone, so lifecycle
    recovery can distinguish it from a never-published Project without racing
    separate revision and snapshot reads.
    """

    revision: int
    snapshot: ProjectSourceSnapshot | None
    instructions: ProjectSourceInstructions

    def __init__(
        self,
        revision: int,
        snapshot: ProjectSourceSnapshot | None,
        instructions: ProjectSourceInstructions | None = None,
    ) -> None:
        """Resolve retained policy from a live snapshot or explicit tombstone."""

        resolved = (
            snapshot.instructions
            if instructions is None and snapshot is not None
            else (
                ProjectSourceInstructions(
                    schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
                )
                if instructions is None
                else instructions
            )
        )
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "instructions", resolved)
        self.__post_init__()

    def __post_init__(self) -> None:
        """Require a non-negative revision coherent with any live snapshot."""

        if type(self.revision) is not int or self.revision < 0:
            raise ProjectSourceValidationError(
                "Project Source catalog entry revision is invalid."
            )
        if type(self.instructions) is not ProjectSourceInstructions:
            raise ProjectSourceValidationError(
                "Project Source catalog entry instructions are invalid."
            )
        if self.snapshot is not None and (
            type(self.snapshot) is not ProjectSourceSnapshot
            or self.revision == 0
            or self.snapshot.revision != self.revision
            or self.snapshot.instructions != self.instructions
        ):
            raise ProjectSourceValidationError(
                "Project Source catalog entry snapshot is inconsistent."
            )


def _thread_lock_for(path: Path) -> RLock:
    """Share one in-process lock across repository instances for a path."""

    normalized = Path(os.path.normcase(str(path.absolute())))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized, RLock())


def _stat_is_reparse(details: os.stat_result) -> bool:
    """Return whether Windows marked a filesystem entry as a reparse point."""

    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _require_safe_directory_chain(path: Path) -> None:
    """Reject symlink or reparse aliases anywhere in the catalog directory."""

    def _inspect_existing_components() -> None:
        """Validate every existing ancestor without creating through it."""

        for current in reversed((path, *path.parents)):
            try:
                details = current.lstat()
            except FileNotFoundError:
                continue
            if (
                stat_module.S_ISLNK(details.st_mode)
                or _stat_is_reparse(details)
                or not stat_module.S_ISDIR(details.st_mode)
            ):
                raise ProjectSourceStorageError(
                    "Project Source catalog directory is unsafe."
                )

    try:
        # Preflight prevents an already redirected ancestor from receiving
        # directories as a side effect; the second pass detects replacement
        # during creation before any catalog or lock file is opened.
        _inspect_existing_components()
        path.mkdir(parents=True, exist_ok=True)
        _inspect_existing_components()
    except ProjectSourceStorageError:
        raise
    except OSError:
        raise ProjectSourceStorageError(
            "Project Source catalog directory is unavailable."
        ) from None


def _try_lock_stream(stream: BinaryIO) -> bool:
    """Attempt one non-blocking cross-platform byte-range lock."""

    stream.seek(0)
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl  # type: ignore[import-not-found]

    try:
        fcntl.flock(  # type: ignore[attr-defined]
            stream.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )
    except OSError:
        return False
    return True


def _unlock_stream(stream: BinaryIO) -> None:
    """Release a lock previously acquired by ``_try_lock_stream``."""

    stream.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl  # type: ignore[import-not-found]

    fcntl.flock(  # type: ignore[attr-defined]
        stream.fileno(),
        fcntl.LOCK_UN,  # type: ignore[attr-defined]
    )


@contextmanager
def _catalog_file_lock(catalog_file: Path) -> Iterator[None]:
    """Serialize complete read/compare/write transactions across processes."""

    directory = catalog_file.parent
    lock_path = directory / _CATALOG_LOCK_FILE_NAME
    with _thread_lock_for(catalog_file):
        _require_safe_directory_chain(directory)
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            opened = os.fstat(descriptor)
            linked = lock_path.lstat()
            if (
                not stat_module.S_ISREG(opened.st_mode)
                or stat_module.S_ISLNK(linked.st_mode)
                or _stat_is_reparse(linked)
                or opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
                or opened.st_nlink != 1
            ):
                raise ProjectSourceStorageError(
                    "Project Source catalog lock is unsafe."
                )
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            with os.fdopen(descriptor, "r+b", closefd=True) as stream:
                descriptor = -1
                deadline = time.monotonic() + _FILE_LOCK_TIMEOUT_SECONDS
                while not _try_lock_stream(stream):
                    if time.monotonic() >= deadline:
                        raise ProjectSourceStorageError(
                            "Project Source catalog is busy."
                        )
                    time.sleep(0.025)
                try:
                    yield
                finally:
                    _unlock_stream(stream)
        except ProjectSourceStorageError:
            raise
        except OSError:
            raise ProjectSourceStorageError(
                "Project Source catalog lock is unavailable."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)


def _strict_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate keys before exact catalog-schema validation."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Project Source catalog key")
        result[key] = value
    return result


class ProjectSourceRepository(Protocol):
    """Persist Project Source snapshots without exposing storage paths."""

    def get_snapshot(self, scope: AttachmentScope) -> ProjectSourceSnapshot:
        """Return the explicitly published snapshot for one Project scope."""

        ...

    def read_entry(self, scope: AttachmentScope) -> CatalogEntrySnapshot:
        """Read one live snapshot or tombstone under a single repository lock."""

        ...

    def list_snapshots(self) -> tuple[ProjectSourceSnapshot, ...]:
        """Return every catalog snapshot in deterministic Project order."""

        ...

    def get_revision(self, scope: AttachmentScope) -> int:
        """Return the live or tombstone CAS revision, or zero if never used."""

        ...

    def save_snapshot(
        self,
        snapshot: ProjectSourceSnapshot,
        *,
        expected_revision: int,
    ) -> ProjectSourceSnapshot:
        """Publish exactly the next revision or fail the CAS operation."""

        ...

    def delete_snapshot(
        self,
        scope: AttachmentScope,
        *,
        expected_revision: int,
        instructions: ProjectSourceInstructions | None = None,
    ) -> int:
        """CAS-publish a tombstone while retaining non-authorizing policy."""

        ...


def _exact_object(value: object, fields: set[str], context: str) -> dict[str, object]:
    """Return one JSON object with an exact closed field set."""

    if type(value) is not dict or set(value) != fields:
        raise ProjectSourceDataCorruptionError(
            f"Stored {context} does not match its schema."
        )
    return cast(dict[str, object], value)


def _exact_string(value: object, context: str) -> str:
    """Return one exact JSON string or a sanitized corruption failure."""

    if type(value) is not str:
        raise ProjectSourceDataCorruptionError(
            f"Stored {context} is invalid."
        )
    return value


def _exact_integer(value: object, context: str) -> int:
    """Return one exact JSON integer while rejecting booleans."""

    if type(value) is not int:
        raise ProjectSourceDataCorruptionError(
            f"Stored {context} is invalid."
        )
    return value


def _timestamp_to_text(value: datetime) -> str:
    """Serialize one validated timestamp in canonical UTC Z form."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ProjectSourceValidationError(
            "Project Source timestamp must be timezone-aware."
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_from_value(value: object) -> datetime:
    """Parse one canonical UTC Z timestamp without accepting local time."""

    text = _exact_string(value, "Project Source timestamp")
    if not text.endswith("Z"):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source timestamp is invalid."
        )
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError:
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source timestamp is invalid."
        ) from None
    return parsed.astimezone(timezone.utc)


def _source_to_data(source: DocumentSource) -> dict[str, object]:
    """Serialize one path-private authorized source."""

    return {
        "scope": {"kind": source.scope.kind, "id": source.scope.id},
        "link_id": source.link_id,
        "file_id": source.file_id,
        "file_name": source.file_name,
        "media_type": source.media_type,
        "size_bytes": source.size_bytes,
    }


def _source_from_value(value: object) -> DocumentSource:
    """Rebuild one exact path-private authorized source."""

    data = _exact_object(
        value,
        {"scope", "link_id", "file_id", "file_name", "media_type", "size_bytes"},
        "Project Source metadata",
    )
    raw_scope = _exact_object(data["scope"], {"kind", "id"}, "Project Source scope")
    try:
        return DocumentSource(
            scope=AttachmentScope(
                kind=cast(
                    Literal["chat", "project"],
                    _exact_string(raw_scope["kind"], "scope kind"),
                ),
                id=_exact_string(raw_scope["id"], "scope ID"),
            ),
            link_id=_exact_string(data["link_id"], "link ID"),
            file_id=_exact_string(data["file_id"], "file ID"),
            file_name=_exact_string(data["file_name"], "file name"),
            media_type=_exact_string(data["media_type"], "media type"),
            size_bytes=_exact_integer(data["size_bytes"], "file size"),
        )
    except (DocumentError, TypeError, ValueError):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source metadata is invalid."
        ) from None


def _generation_to_data(source: ProjectSourceGeneration) -> dict[str, object]:
    """Serialize one exact catalog generation."""

    return {
        "schema_version": source.schema_version,
        "source": _source_to_data(source.generation.source),
        "derivation_fingerprint": source.generation.derivation_fingerprint,
        "index_profile_fingerprint": source.index_profile_fingerprint,
        "published_at": _timestamp_to_text(source.published_at),
    }


def _generation_from_value(value: object) -> ProjectSourceGeneration:
    """Rebuild one validated exact catalog generation."""

    data = _exact_object(
        value,
        {
            "schema_version",
            "source",
            "derivation_fingerprint",
            "index_profile_fingerprint",
            "published_at",
        },
        "Project Source generation",
    )
    try:
        return ProjectSourceGeneration(
            schema_version=cast(
                Literal[1],
                _exact_integer(
                    data["schema_version"],
                    "generation schema version",
                ),
            ),
            generation=ExpectedDocumentGeneration(
                source=_source_from_value(data["source"]),
                derivation_fingerprint=_exact_string(
                    data["derivation_fingerprint"],
                    "derivation fingerprint",
                ),
            ),
            index_profile_fingerprint=_exact_string(
                data["index_profile_fingerprint"],
                "index profile fingerprint",
            ),
            published_at=_timestamp_from_value(data["published_at"]),
        )
    except ProjectSourceDataCorruptionError:
        raise
    except (DocumentError, ProjectSourceValidationError, TypeError, ValueError):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source generation is invalid."
        ) from None


def _instructions_to_data(
    instructions: ProjectSourceInstructions,
) -> dict[str, object]:
    """Serialize one bounded Project Source answer-instruction policy."""

    return {
        "schema_version": instructions.schema_version,
        "preferred_source_link_ids": list(
            instructions.preferred_source_link_ids
        ),
        "answer_style": instructions.answer_style,
    }


def _instructions_from_value(value: object) -> ProjectSourceInstructions:
    """Rebuild one exact non-authorizing instruction policy."""

    data = _exact_object(
        value,
        {"schema_version", "preferred_source_link_ids", "answer_style"},
        "Project Source instructions",
    )
    raw_preferred = data["preferred_source_link_ids"]
    if type(raw_preferred) is not list or len(raw_preferred) > (
        MAX_RETRIEVAL_DOCUMENTS
    ):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source instructions are invalid."
        )
    if not all(type(item) is str for item in raw_preferred):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source instructions are invalid."
        )
    try:
        return ProjectSourceInstructions(
            schema_version=cast(
                Literal[1],
                _exact_integer(
                    data["schema_version"],
                    "instructions schema version",
                ),
            ),
            preferred_source_link_ids=tuple(cast(list[str], raw_preferred)),
            answer_style=cast(
                GroundedAnswerStyle,
                _exact_string(data["answer_style"], "answer style"),
            ),
        )
    except (ProjectSourceValidationError, TypeError, ValueError):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source instructions are invalid."
        ) from None


def _snapshot_to_data(snapshot: ProjectSourceSnapshot) -> dict[str, object]:
    """Serialize one complete catalog snapshot."""

    return {
        "schema_version": snapshot.schema_version,
        "scope": {"kind": snapshot.scope.kind, "id": snapshot.scope.id},
        "revision": snapshot.revision,
        "source_catalog_fingerprint": snapshot.source_catalog_fingerprint,
        "sources": [_generation_to_data(source) for source in snapshot.sources],
        "instructions": _instructions_to_data(snapshot.instructions),
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
    }


def _snapshot_from_value(value: object) -> ProjectSourceSnapshot:
    """Rebuild one strict catalog snapshot from JSON-shaped data."""

    data = _exact_object(
        value,
        {
            "schema_version",
            "scope",
            "revision",
            "source_catalog_fingerprint",
            "sources",
            "instructions",
            "snapshot_fingerprint",
        },
        "Project Source snapshot",
    )
    raw_scope = _exact_object(data["scope"], {"kind", "id"}, "snapshot scope")
    raw_sources = data["sources"]
    if type(raw_sources) is not list:
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source snapshot sources are invalid."
        )
    if len(raw_sources) > MAX_RETRIEVAL_DOCUMENTS:
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source snapshot exceeds its source limit."
        )
    try:
        return ProjectSourceSnapshot(
            schema_version=cast(
                Literal[1],
                _exact_integer(data["schema_version"], "snapshot schema version"),
            ),
            scope=AttachmentScope(
                kind=cast(
                    Literal["chat", "project"],
                    _exact_string(raw_scope["kind"], "scope kind"),
                ),
                id=_exact_string(raw_scope["id"], "scope ID"),
            ),
            revision=_exact_integer(data["revision"], "snapshot revision"),
            source_catalog_fingerprint=_exact_string(
                data["source_catalog_fingerprint"],
                "source catalog fingerprint",
            ),
            sources=tuple(_generation_from_value(item) for item in raw_sources),
            instructions=_instructions_from_value(data["instructions"]),
            snapshot_fingerprint=_exact_string(
                data["snapshot_fingerprint"],
                "snapshot fingerprint",
            ),
        )
    except ProjectSourceDataCorruptionError:
        raise
    except (ProjectSourceValidationError, TypeError, ValueError):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source snapshot is invalid."
        ) from None


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    """Retain one monotonic live snapshot or policy-bearing tombstone."""

    scope: AttachmentScope
    revision: int
    snapshot: ProjectSourceSnapshot | None
    instructions: ProjectSourceInstructions

    def __post_init__(self) -> None:
        """Require an exact Project scope and a coherent live revision."""

        if type(self.scope) is not AttachmentScope or self.scope.kind != "project":
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog entry scope is invalid."
            )
        if type(self.revision) is not int or self.revision <= 0:
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog entry revision is invalid."
            )
        if type(self.instructions) is not ProjectSourceInstructions:
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog instructions are invalid."
            )
        if self.snapshot is not None and (
            type(self.snapshot) is not ProjectSourceSnapshot
            or self.snapshot.scope != self.scope
            or self.snapshot.revision != self.revision
            or self.snapshot.instructions != self.instructions
        ):
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog entry is inconsistent."
            )


def _entry_to_data(entry: _CatalogEntry) -> dict[str, object]:
    """Serialize one live entry or monotonic deletion tombstone."""

    return {
        "scope": {"kind": entry.scope.kind, "id": entry.scope.id},
        "revision": entry.revision,
        "snapshot": (
            None if entry.snapshot is None else _snapshot_to_data(entry.snapshot)
        ),
        "instructions": _instructions_to_data(entry.instructions),
    }


def _entry_from_value(value: object, *, schema_version: int) -> _CatalogEntry:
    """Rebuild one exact entry, migrating legacy policy-less tombstones."""

    data = _exact_object(
        value,
        (
            {"scope", "revision", "snapshot"}
            if schema_version == _LEGACY_PROJECT_SOURCE_CATALOG_SCHEMA_VERSION
            else {"scope", "revision", "snapshot", "instructions"}
        ),
        "Project Source catalog entry",
    )
    raw_scope = _exact_object(data["scope"], {"kind", "id"}, "entry scope")
    try:
        scope = AttachmentScope(
            kind=cast(
                Literal["chat", "project"],
                _exact_string(raw_scope["kind"], "entry scope kind"),
            ),
            id=_exact_string(raw_scope["id"], "entry scope ID"),
        )
        snapshot = (
            None
            if data["snapshot"] is None
            else _snapshot_from_value(data["snapshot"])
        )
        instructions = (
            snapshot.instructions
            if snapshot is not None
            else (
                ProjectSourceInstructions(
                    schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
                )
                if schema_version
                == _LEGACY_PROJECT_SOURCE_CATALOG_SCHEMA_VERSION
                else _instructions_from_value(data["instructions"])
            )
        )
        return _CatalogEntry(
            scope=scope,
            revision=_exact_integer(data["revision"], "entry revision"),
            snapshot=snapshot,
            instructions=instructions,
        )
    except ProjectSourceDataCorruptionError:
        raise
    except (TypeError, ValueError):
        raise ProjectSourceDataCorruptionError(
            "Stored Project Source catalog entry is invalid."
        ) from None


class JsonProjectSourceRepository:
    """Store all Project Source snapshots in one atomic strict JSON catalog."""

    def __init__(self, storage_directory: Path) -> None:
        """Configure a path-private catalog guarded across processes."""

        self._storage_directory = Path(storage_directory).absolute()
        self._catalog_file = self._storage_directory / _CATALOG_FILE_NAME

    def get_snapshot(self, scope: AttachmentScope) -> ProjectSourceSnapshot:
        """Return one exact Project snapshot or a stable not-found failure."""

        entry = self.read_entry(scope)
        if entry.snapshot is not None:
            return entry.snapshot
        raise ProjectSourceNotFoundError(
            "Project Source catalog has not been published."
        )

    def read_entry(self, scope: AttachmentScope) -> CatalogEntrySnapshot:
        """Read one coherent live snapshot, tombstone, or absent entry."""

        canonical_scope = self._require_project_scope(scope)
        with _catalog_file_lock(self._catalog_file):
            for entry in self._load_entries():
                if entry.scope == canonical_scope:
                    return CatalogEntrySnapshot(
                        revision=entry.revision,
                        snapshot=(
                            None
                            if entry.snapshot is None
                            else self._snapshot_copy(entry.snapshot)
                        ),
                        instructions=self._instructions_copy(entry.instructions),
                    )
        return CatalogEntrySnapshot(revision=0, snapshot=None)

    def list_snapshots(self) -> tuple[ProjectSourceSnapshot, ...]:
        """Return every validated snapshot sorted by stable Project ID."""

        with _catalog_file_lock(self._catalog_file):
            return tuple(
                entry.snapshot
                for entry in self._load_entries()
                if entry.snapshot is not None
            )

    def get_revision(self, scope: AttachmentScope) -> int:
        """Return a live/tombstone revision so recreation cannot suffer ABA."""

        return self.read_entry(scope).revision

    def save_snapshot(
        self,
        snapshot: ProjectSourceSnapshot,
        *,
        expected_revision: int,
    ) -> ProjectSourceSnapshot:
        """Publish exactly one next revision through compare-and-swap."""

        canonical = self._snapshot_copy(snapshot)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ProjectSourceValidationError(
                "expected_revision must be a non-negative integer."
            )
        if canonical.revision != expected_revision + 1:
            raise ProjectSourceConflictError(
                "Project Source snapshot is not the next revision."
            )
        with _catalog_file_lock(self._catalog_file):
            entries = list(self._load_entries())
            position = next(
                (
                    index
                    for index, existing in enumerate(entries)
                    if existing.scope == canonical.scope
                ),
                None,
            )
            current_revision = 0 if position is None else entries[position].revision
            if current_revision != expected_revision:
                raise ProjectSourceConflictError(
                    "Project Source catalog revision changed."
                )
            replacement = _CatalogEntry(
                scope=canonical.scope,
                revision=canonical.revision,
                snapshot=canonical,
                instructions=canonical.instructions,
            )
            if position is None:
                entries.append(replacement)
            else:
                entries[position] = replacement
            self._write_entries(tuple(entries))
            return canonical

    def delete_snapshot(
        self,
        scope: AttachmentScope,
        *,
        expected_revision: int,
        instructions: ProjectSourceInstructions | None = None,
    ) -> int:
        """CAS-publish a policy-bearing tombstone, including from absence."""

        canonical_scope = self._require_project_scope(scope)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ProjectSourceValidationError(
                "expected_revision must be a non-negative integer."
            )
        canonical_instructions = (
            None
            if instructions is None
            else self._instructions_copy(instructions)
        )
        with _catalog_file_lock(self._catalog_file):
            entries = list(self._load_entries())
            position = next(
                (
                    index
                    for index, existing in enumerate(entries)
                    if existing.scope == canonical_scope
                ),
                None,
            )
            current_revision = 0 if position is None else entries[position].revision
            if current_revision != expected_revision:
                raise ProjectSourceConflictError(
                    "Project Source catalog revision changed."
                )
            retained_instructions = (
                canonical_instructions
                if canonical_instructions is not None
                else (
                    ProjectSourceInstructions(
                        schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
                    )
                    if position is None
                    else entries[position].instructions
                )
            )
            tombstone_revision = expected_revision + 1
            replacement = _CatalogEntry(
                scope=canonical_scope,
                revision=tombstone_revision,
                snapshot=None,
                instructions=retained_instructions,
            )
            if position is None:
                entries.append(replacement)
            else:
                entries[position] = replacement
            self._write_entries(tuple(entries))
            return tombstone_revision

    @staticmethod
    def _require_project_scope(scope: object) -> AttachmentScope:
        """Rebuild one exact Project scope before catalog access."""

        if type(scope) is not AttachmentScope:
            raise ProjectSourceValidationError(
                "Project Source catalog scope is invalid."
            )
        try:
            canonical = AttachmentScope(kind=scope.kind, id=scope.id)
        except (TypeError, ValueError) as error:
            raise ProjectSourceValidationError(
                "Project Source catalog scope is invalid."
            ) from error
        if canonical.kind != "project":
            raise ProjectSourceValidationError(
                "Project Source catalog requires a Project scope."
            )
        return canonical

    @staticmethod
    def _snapshot_copy(snapshot: object) -> ProjectSourceSnapshot:
        """Detach one public snapshot through strict serialization values."""

        if type(snapshot) is not ProjectSourceSnapshot:
            raise ProjectSourceValidationError(
                "Project Source snapshot is invalid."
            )
        try:
            return _snapshot_from_value(_snapshot_to_data(snapshot))
        except (MemoryError, RecursionError):
            raise ProjectSourceValidationError(
                "Project Source snapshot exceeds a safe resource limit."
            ) from None
        except (
            AttributeError,
            DocumentError,
            ProjectSourceDataCorruptionError,
            ProjectSourceValidationError,
            TypeError,
            ValueError,
        ):
            raise ProjectSourceValidationError(
                "Project Source snapshot is invalid."
            ) from None

    @staticmethod
    def _instructions_copy(
        instructions: object,
    ) -> ProjectSourceInstructions:
        """Detach one public instruction policy through strict JSON values."""

        if type(instructions) is not ProjectSourceInstructions:
            raise ProjectSourceValidationError(
                "Project Source instructions are invalid."
            )
        try:
            return _instructions_from_value(_instructions_to_data(instructions))
        except (MemoryError, RecursionError):
            raise ProjectSourceValidationError(
                "Project Source instructions exceed a safe resource limit."
            ) from None
        except (
            ProjectSourceDataCorruptionError,
            ProjectSourceValidationError,
            TypeError,
            ValueError,
        ):
            raise ProjectSourceValidationError(
                "Project Source instructions are invalid."
            ) from None

    def _read_catalog_bytes(self) -> bytes | None:
        """Read at most the bounded regular file through a pinned descriptor."""

        if not self._catalog_file.exists() and not self._catalog_file.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(self._catalog_file, flags)
            opened = os.fstat(descriptor)
            linked = self._catalog_file.lstat()
            if (
                not stat_module.S_ISREG(opened.st_mode)
                or stat_module.S_ISLNK(linked.st_mode)
                or _stat_is_reparse(linked)
                or opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
                or opened.st_nlink != 1
            ):
                raise ProjectSourceStorageError(
                    "Project Source catalog storage is unsafe."
                )
            if opened.st_size > _MAX_CATALOG_BYTES:
                raise ProjectSourceDataCorruptionError(
                    "Stored Project Source catalog exceeds its size limit."
                )
            chunks: list[bytes] = []
            remaining = _MAX_CATALOG_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > _MAX_CATALOG_BYTES:
                raise ProjectSourceDataCorruptionError(
                    "Stored Project Source catalog exceeds its size limit."
                )
            linked_after = self._catalog_file.lstat()
            if (
                opened.st_dev != linked_after.st_dev
                or opened.st_ino != linked_after.st_ino
                or stat_module.S_ISLNK(linked_after.st_mode)
                or _stat_is_reparse(linked_after)
            ):
                raise ProjectSourceStorageError(
                    "Project Source catalog changed during its read."
                )
            return payload
        except (ProjectSourceDataCorruptionError, ProjectSourceStorageError):
            raise
        except OSError:
            raise ProjectSourceStorageError(
                "Project Source catalog is unreadable."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)

    def _load_entries(self) -> tuple[_CatalogEntry, ...]:
        """Load the bounded live/tombstone catalog from one pinned read."""

        payload = self._read_catalog_bytes()
        if payload is None:
            return ()
        try:
            raw = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog is unreadable."
            ) from None
        except (MemoryError, RecursionError):
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog exceeds a safe parsing limit."
            ) from None
        root = _exact_object(
            raw,
            {"schema_version", "entries"},
            "Project Source catalog",
        )
        schema_version = _exact_integer(
            root["schema_version"], "catalog schema version"
        )
        if schema_version not in {
            _LEGACY_PROJECT_SOURCE_CATALOG_SCHEMA_VERSION,
            PROJECT_SOURCE_CATALOG_SCHEMA_VERSION,
        }:
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog version is unsupported."
            )
        raw_entries = root["entries"]
        if type(raw_entries) is not list or len(raw_entries) > (
            _MAX_CATALOG_ENTRIES
        ):
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog entries are invalid."
            )
        entries = tuple(
            _entry_from_value(item, schema_version=schema_version)
            for item in raw_entries
        )
        scope_ids = tuple(entry.scope.id for entry in entries)
        if len(set(scope_ids)) != len(scope_ids):
            raise ProjectSourceDataCorruptionError(
                "Stored Project Source catalog contains duplicate Projects."
            )
        return tuple(sorted(entries, key=lambda entry: entry.scope.id))

    def _write_entries(
        self,
        entries: tuple[_CatalogEntry, ...],
    ) -> None:
        """Atomically replace the monotonic bounded catalog document."""

        if len(entries) > _MAX_CATALOG_ENTRIES:
            raise ProjectSourceStorageError(
                "Project Source catalog exceeds its entry limit."
            )
        ordered = tuple(sorted(entries, key=lambda entry: entry.scope.id))
        document: Mapping[str, object] = {
            "schema_version": PROJECT_SOURCE_CATALOG_SCHEMA_VERSION,
            "entries": [_entry_to_data(entry) for entry in ordered],
        }
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(payload) > _MAX_CATALOG_BYTES:
            raise ProjectSourceStorageError(
                "Project Source catalog exceeds its storage limit."
            )
        _require_safe_directory_chain(self._storage_directory)
        try:
            descriptor, temporary_name = mkstemp(
                dir=self._storage_directory,
                prefix=f".{_CATALOG_FILE_NAME}.",
                suffix=".tmp",
            )
        except OSError:
            raise ProjectSourceStorageError(
                "Project Source catalog storage is unavailable."
            ) from None
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._catalog_file)
            # POSIX needs a directory sync to make the rename durable. Windows
            # does not permit opening directories this way, while ReplaceFile
            # durability is already covered by the flushed replacement file.
            if sys.platform != "win32":
                directory_descriptor = os.open(
                    self._storage_directory,
                    os.O_RDONLY,
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except OSError:
            raise ProjectSourceStorageError(
                "Project Source catalog could not be replaced safely."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
