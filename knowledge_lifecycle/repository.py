"""Persist bounded knowledge-operation saga snapshots with revision CAS."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
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
from attachments.domain import MAX_JSON_SAFE_INTEGER
from documents import GroundedAnswerStyle, MAX_RETRIEVAL_DOCUMENTS
from project_sources.domain import (
    PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
    ProjectSourceInstructions,
)
from project_sources.exceptions import ProjectSourceValidationError

from .domain import (
    KNOWLEDGE_OPERATION_SCHEMA_VERSION,
    KnowledgeOperationId,
    KnowledgeOperationKind,
    KnowledgeOperationPhase,
    KnowledgeOperationSnapshot,
    KnowledgeOperationState,
    validate_knowledge_operation_id,
)
from .exceptions import (
    KnowledgeLifecycleConflictError,
    KnowledgeLifecycleDataCorruptionError,
    KnowledgeLifecycleNotFoundError,
    KnowledgeLifecycleStorageError,
    KnowledgeLifecycleValidationError,
)


KNOWLEDGE_OPERATION_CATALOG_SCHEMA_VERSION: Final[Literal[1]] = 1
MAX_KNOWLEDGE_OPERATIONS: Final = 2_048
MAX_KNOWLEDGE_CATALOG_BYTES: Final = 4 * 1024 * 1024

_CATALOG_FILE_NAME: Final = "knowledge_operations.json"
_CATALOG_LOCK_FILE_NAME: Final = ".knowledge_operations.lock"
_FILE_LOCK_TIMEOUT_SECONDS: Final = 2.0
_RECOVERABLE_STATES: Final = frozenset(
    {"running", "cancel_requested", "recovery_required"}
)
_PATH_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[Path, RLock] = {}


def _thread_lock_for(path: Path) -> RLock:
    """Share one in-process lock across repository instances for a path."""

    normalized = Path(os.path.normcase(str(path.absolute())))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized, RLock())


def _stat_is_reparse(details: os.stat_result) -> bool:
    """Return whether Windows marked an entry as a reparse point."""

    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _require_safe_directory_chain(path: Path) -> None:
    """Reject aliases and non-directories in the journal directory chain."""

    def _inspect_existing_components() -> None:
        """Validate every existing ancestor without following an alias."""

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
                raise KnowledgeLifecycleStorageError(
                    "Knowledge lifecycle journal directory is unsafe."
                )

    try:
        # Both passes are required: the first prevents creating through an
        # existing alias, while the second detects replacement during mkdir.
        _inspect_existing_components()
        path.mkdir(parents=True, exist_ok=True)
        _inspect_existing_components()
    except KnowledgeLifecycleStorageError:
        raise
    except OSError:
        raise KnowledgeLifecycleStorageError(
            "Knowledge lifecycle journal directory is unavailable."
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
def _journal_file_lock(catalog_file: Path) -> Iterator[None]:
    """Serialize complete journal read/compare/write transactions."""

    directory = catalog_file.parent
    lock_path = directory / _CATALOG_LOCK_FILE_NAME
    with _thread_lock_for(catalog_file):
        _require_safe_directory_chain(directory)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
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
                raise KnowledgeLifecycleStorageError(
                    "Knowledge lifecycle journal lock is unsafe."
                )
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            with os.fdopen(descriptor, "r+b", closefd=True) as stream:
                descriptor = -1
                deadline = time.monotonic() + _FILE_LOCK_TIMEOUT_SECONDS
                while not _try_lock_stream(stream):
                    if time.monotonic() >= deadline:
                        raise KnowledgeLifecycleStorageError(
                            "Knowledge lifecycle journal is busy."
                        )
                    time.sleep(0.025)
                try:
                    yield
                finally:
                    _unlock_stream(stream)
        except KnowledgeLifecycleStorageError:
            raise
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle journal lock is unavailable."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)


def _strict_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate keys before exact journal-schema validation."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate knowledge lifecycle journal key")
        result[key] = value
    return result


def _bounded_json_integer(value: str) -> int:
    """Reject oversized JSON integers before arbitrary-precision parsing."""

    if len(value) > 20:
        raise ValueError("knowledge lifecycle integer is oversized")
    return int(value)


def _reject_json_constant(_value: str) -> object:
    """Reject non-finite constants that are outside the JSON standard."""

    raise ValueError("knowledge lifecycle JSON constant is invalid")


class KnowledgeOperationRepository(Protocol):
    """Persist Project-only operation checkpoints without exposing paths."""

    def create_operation(
        self,
        snapshot: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Create a revision-one operation or report an identifier conflict."""

        ...

    def get_operation(
        self,
        operation_id: str,
    ) -> KnowledgeOperationSnapshot:
        """Return one exact operation or a stable not-found failure."""

        ...

    def list_operations(self) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Return all operations in deterministic creation order."""

        ...

    def list_recoverable(self) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Return operations that still require execution or reconciliation."""

        ...

    def save_operation(
        self,
        snapshot: KnowledgeOperationSnapshot,
        *,
        expected_revision: int,
    ) -> KnowledgeOperationSnapshot:
        """Publish exactly the next journal revision through CAS."""

        ...

    def request_cancel(self, operation_id: str) -> KnowledgeOperationSnapshot:
        """Atomically request cancellation unless the operation is terminal."""

        ...


def _exact_object(
    value: object,
    fields: set[str],
    context: str,
) -> dict[str, object]:
    """Return one JSON object with an exact closed field set."""

    if type(value) is not dict or set(value) != fields:
        raise KnowledgeLifecycleDataCorruptionError(
            f"Stored {context} does not match its schema."
        )
    return cast(dict[str, object], value)


def _exact_string(value: object, context: str) -> str:
    """Return one exact JSON string or a sanitized corruption failure."""

    if type(value) is not str:
        raise KnowledgeLifecycleDataCorruptionError(
            f"Stored {context} is invalid."
        )
    return value


def _exact_optional_string(value: object, context: str) -> str | None:
    """Return one JSON string or null from an optional field."""

    if value is None:
        return None
    return _exact_string(value, context)


def _exact_integer(value: object, context: str) -> int:
    """Return one exact JSON integer while rejecting booleans."""

    if type(value) is not int:
        raise KnowledgeLifecycleDataCorruptionError(
            f"Stored {context} is invalid."
        )
    return value


def _exact_boolean(value: object, context: str) -> bool:
    """Return one exact JSON boolean without accepting integer aliases."""

    if type(value) is not bool:
        raise KnowledgeLifecycleDataCorruptionError(
            f"Stored {context} is invalid."
        )
    return value


def _timestamp_to_text(value: datetime) -> str:
    """Serialize one validated timestamp in canonical UTC Z form."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise KnowledgeLifecycleValidationError(
            "Knowledge lifecycle timestamp must be timezone-aware."
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_from_value(value: object) -> datetime:
    """Parse one canonical UTC Z timestamp without accepting local time."""

    text = _exact_string(value, "knowledge lifecycle timestamp")
    if not text.endswith("Z"):
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge lifecycle timestamp is invalid."
        )
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError:
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge lifecycle timestamp is invalid."
        ) from None
    if parsed.isoformat().replace("+00:00", "Z") != text:
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge lifecycle timestamp is not canonical."
        )
    return parsed.astimezone(timezone.utc)


def _instructions_to_data(
    instructions: ProjectSourceInstructions,
) -> dict[str, object]:
    """Serialize one bounded non-authorizing instruction policy."""

    return {
        "schema_version": instructions.schema_version,
        "preferred_source_link_ids": list(
            instructions.preferred_source_link_ids
        ),
        "answer_style": instructions.answer_style,
    }


def _instructions_from_value(value: object) -> ProjectSourceInstructions:
    """Rebuild one exact Project Source instruction policy."""

    data = _exact_object(
        value,
        {"schema_version", "preferred_source_link_ids", "answer_style"},
        "knowledge operation instructions",
    )
    raw_preferred = data["preferred_source_link_ids"]
    if type(raw_preferred) is not list or len(raw_preferred) > (
        MAX_RETRIEVAL_DOCUMENTS
    ):
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge operation instructions are invalid."
        )
    if not all(type(item) is str for item in raw_preferred):
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge operation instructions are invalid."
        )
    try:
        return ProjectSourceInstructions(
            schema_version=cast(
                Literal[1],
                _exact_integer(
                    data["schema_version"],
                    "instruction schema version",
                ),
            ),
            preferred_source_link_ids=tuple(cast(list[str], raw_preferred)),
            answer_style=cast(
                GroundedAnswerStyle,
                _exact_string(data["answer_style"], "answer style"),
            ),
        )
    except (ProjectSourceValidationError, TypeError, ValueError):
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge operation instructions are invalid."
        ) from None


def _operation_to_data(
    snapshot: KnowledgeOperationSnapshot,
) -> dict[str, object]:
    """Serialize one path-private lifecycle checkpoint."""

    return {
        "schema_version": snapshot.schema_version,
        "journal_revision": snapshot.journal_revision,
        "operation_id": snapshot.operation_id,
        "scope": {"kind": snapshot.scope.kind, "id": snapshot.scope.id},
        "kind": snapshot.kind,
        "state": snapshot.state,
        "phase": snapshot.phase,
        "progress_percent": snapshot.progress_percent,
        "target_link_id": snapshot.target_link_id,
        "staged_link_id": snapshot.staged_link_id,
        "source_catalog_fingerprint": snapshot.source_catalog_fingerprint,
        "catalog_snapshot_fingerprint": (
            snapshot.catalog_snapshot_fingerprint
        ),
        "started_from_revoked_catalog": snapshot.started_from_revoked_catalog,
        "catalog_revision": snapshot.catalog_revision,
        "index_profile_fingerprint": snapshot.index_profile_fingerprint,
        "instructions": _instructions_to_data(snapshot.instructions),
        "attempt": snapshot.attempt,
        "created_at": _timestamp_to_text(snapshot.created_at),
        "updated_at": _timestamp_to_text(snapshot.updated_at),
        "error_code": snapshot.error_code,
    }


def _operation_from_value(value: object) -> KnowledgeOperationSnapshot:
    """Rebuild one validated operation from strict JSON-shaped data."""

    fields = {
        "schema_version",
        "journal_revision",
        "operation_id",
        "scope",
        "kind",
        "state",
        "phase",
        "progress_percent",
        "target_link_id",
        "staged_link_id",
        "source_catalog_fingerprint",
        "catalog_snapshot_fingerprint",
        "started_from_revoked_catalog",
        "catalog_revision",
        "index_profile_fingerprint",
        "instructions",
        "attempt",
        "created_at",
        "updated_at",
        "error_code",
    }
    data = _exact_object(value, fields, "knowledge operation")
    raw_scope = _exact_object(
        data["scope"],
        {"kind", "id"},
        "knowledge operation scope",
    )
    try:
        return KnowledgeOperationSnapshot(
            schema_version=cast(
                Literal[1],
                _exact_integer(data["schema_version"], "operation schema version"),
            ),
            journal_revision=_exact_integer(
                data["journal_revision"],
                "operation journal revision",
            ),
            operation_id=KnowledgeOperationId(
                _exact_string(data["operation_id"], "operation ID")
            ),
            scope=AttachmentScope(
                kind=cast(
                    Literal["chat", "project"],
                    _exact_string(raw_scope["kind"], "scope kind"),
                ),
                id=_exact_string(raw_scope["id"], "scope ID"),
            ),
            kind=cast(
                KnowledgeOperationKind,
                _exact_string(data["kind"], "operation kind"),
            ),
            state=cast(
                KnowledgeOperationState,
                _exact_string(data["state"], "operation state"),
            ),
            phase=cast(
                KnowledgeOperationPhase,
                _exact_string(data["phase"], "operation phase"),
            ),
            progress_percent=_exact_integer(
                data["progress_percent"],
                "operation progress",
            ),
            target_link_id=_exact_optional_string(
                data["target_link_id"],
                "target link ID",
            ),
            staged_link_id=_exact_optional_string(
                data["staged_link_id"],
                "staged link ID",
            ),
            source_catalog_fingerprint=_exact_optional_string(
                data["source_catalog_fingerprint"],
                "source catalog fingerprint",
            ),
            catalog_snapshot_fingerprint=_exact_optional_string(
                data["catalog_snapshot_fingerprint"],
                "catalog snapshot fingerprint",
            ),
            started_from_revoked_catalog=_exact_boolean(
                data["started_from_revoked_catalog"],
                "revoked-catalog origin marker",
            ),
            catalog_revision=_exact_integer(
                data["catalog_revision"],
                "catalog revision",
            ),
            index_profile_fingerprint=_exact_string(
                data["index_profile_fingerprint"],
                "index profile fingerprint",
            ),
            instructions=_instructions_from_value(data["instructions"]),
            attempt=_exact_integer(data["attempt"], "operation attempt"),
            created_at=_timestamp_from_value(data["created_at"]),
            updated_at=_timestamp_from_value(data["updated_at"]),
            error_code=_exact_optional_string(
                data["error_code"],
                "operation error code",
            ),
        )
    except KnowledgeLifecycleDataCorruptionError:
        raise
    except (KnowledgeLifecycleValidationError, TypeError, ValueError):
        raise KnowledgeLifecycleDataCorruptionError(
            "Stored knowledge operation is invalid."
        ) from None


def _operation_semantics_issue(
    snapshot: KnowledgeOperationSnapshot,
) -> str | None:
    """Return why one standalone checkpoint violates closed saga semantics."""

    terminal = snapshot.state in {"succeeded", "cancelled", "failed"}
    if terminal != (snapshot.phase == "completed"):
        return "terminal state and phase are inconsistent"
    if snapshot.state == "succeeded" and snapshot.progress_percent != 100:
        return "successful operation does not report full progress"
    if snapshot.progress_percent == 100 and snapshot.state != "succeeded":
        return "only a successful operation may report full progress"
    if snapshot.state in {"succeeded", "cancelled"} and (
        snapshot.error_code is not None
    ):
        return "clean terminal operation carries an error code"
    if snapshot.state == "failed" and snapshot.error_code is None:
        return "failed operation has no error code"

    target_required = snapshot.kind in {"replace", "reindex", "delete"}
    if target_required != (snapshot.target_link_id is not None):
        return "operation target does not match its kind"
    if snapshot.kind not in {"add", "replace"} and (
        snapshot.staged_link_id is not None
    ):
        return "operation kind cannot own a staged source"
    if snapshot.started_from_revoked_catalog and (
        snapshot.catalog_revision <= 0
        or snapshot.catalog_snapshot_fingerprint is not None
    ):
        return "revoked-catalog origin marker is inconsistent"
    return None


def _validate_new_operation(snapshot: KnowledgeOperationSnapshot) -> None:
    """Require the only legal checkpoint shape before any side effect."""

    issue = _operation_semantics_issue(snapshot)
    if issue is not None:
        raise KnowledgeLifecycleConflictError(
            "New knowledge operation state is inconsistent."
        )
    if (
        snapshot.state != "running"
        or snapshot.phase != "preparing"
        or snapshot.progress_percent != 0
        or snapshot.attempt != 1
        or snapshot.staged_link_id is not None
        or snapshot.error_code is not None
    ):
        raise KnowledgeLifecycleConflictError(
            "A new knowledge operation must begin at its initial checkpoint."
        )


def _validate_checkpoint_update(
    current: KnowledgeOperationSnapshot,
    replacement: KnowledgeOperationSnapshot,
) -> None:
    """Reject checkpoint rewrites that would change the original intent."""

    if _operation_semantics_issue(replacement) is not None:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation checkpoint state is inconsistent."
        )
    immutable_values = (
        current.operation_id == replacement.operation_id,
        current.scope == replacement.scope,
        current.kind == replacement.kind,
        current.target_link_id == replacement.target_link_id,
        (
            current.staged_link_id == replacement.staged_link_id
            or (
                current.staged_link_id is None
                and replacement.staged_link_id is not None
            )
        ),
        current.index_profile_fingerprint
        == replacement.index_profile_fingerprint,
        current.instructions == replacement.instructions,
        current.started_from_revoked_catalog
        == replacement.started_from_revoked_catalog,
        current.created_at == replacement.created_at,
    )
    if not all(immutable_values):
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation intent cannot change after creation."
        )
    if current.state in {"succeeded", "cancelled", "failed"}:
        raise KnowledgeLifecycleConflictError(
            "A terminal knowledge operation cannot be rewritten."
        )
    allowed_states = {
        "running": {
            "running",
            "cancel_requested",
            "recovery_required",
            "succeeded",
            "cancelled",
            "failed",
        },
        "cancel_requested": {
            "cancel_requested",
            "recovery_required",
            "succeeded",
            "cancelled",
            "failed",
        },
        "recovery_required": {
            "recovery_required",
            "cancel_requested",
            "succeeded",
            "cancelled",
            "failed",
        },
    }
    if replacement.state not in allowed_states[current.state]:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation state transition is invalid."
        )
    phase_order = {
        "preparing": 0,
        "importing": 1,
        "indexing": 2,
        "revoking": 3,
        "cleaning": 4,
        "publishing": 5,
        "completed": 6,
    }
    if phase_order[replacement.phase] < phase_order[current.phase]:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation phase cannot move backwards."
        )
    terminal = replacement.state in {"succeeded", "cancelled", "failed"}
    if terminal != (replacement.phase == "completed"):
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation terminal state and phase are inconsistent."
        )
    if replacement.state == "succeeded" and (
        replacement.progress_percent != 100
    ):
        raise KnowledgeLifecycleConflictError(
            "A successful knowledge operation must report full progress."
        )
    if replacement.progress_percent == 100 and replacement.state != "succeeded":
        raise KnowledgeLifecycleConflictError(
            "Only a successful knowledge operation can report full progress."
        )
    if replacement.updated_at < current.updated_at:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation timestamp cannot move backwards."
        )
    if replacement.progress_percent < current.progress_percent:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation progress cannot move backwards."
        )
    if replacement.attempt < current.attempt:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation attempt cannot move backwards."
        )
    if replacement.catalog_revision < current.catalog_revision:
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation catalog revision cannot move backwards."
        )
    if (
        current.source_catalog_fingerprint is not None
        and replacement.source_catalog_fingerprint
        != current.source_catalog_fingerprint
    ):
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation source fingerprint cannot change."
        )
    if (
        current.catalog_snapshot_fingerprint is not None
        and replacement.catalog_snapshot_fingerprint
        != current.catalog_snapshot_fingerprint
    ):
        raise KnowledgeLifecycleConflictError(
            "Knowledge operation catalog fingerprint cannot change."
        )


class JsonKnowledgeOperationRepository:
    """Store all bounded operation checkpoints in one atomic JSON journal."""

    def __init__(self, storage_directory: Path) -> None:
        """Configure a path-private journal guarded across processes."""

        self._storage_directory = Path(storage_directory).absolute()
        self._catalog_file = self._storage_directory / _CATALOG_FILE_NAME

    def create_operation(
        self,
        snapshot: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Create a revision-one operation or report an identifier conflict."""

        canonical = self._snapshot_copy(snapshot)
        if canonical.journal_revision != 1:
            raise KnowledgeLifecycleConflictError(
                "A new knowledge operation must start at journal revision one."
            )
        _validate_new_operation(canonical)
        with _journal_file_lock(self._catalog_file):
            operations = list(self._load_operations())
            if any(
                operation.operation_id == canonical.operation_id
                for operation in operations
            ):
                raise KnowledgeLifecycleConflictError(
                    "Knowledge operation identifier already exists."
                )
            if any(
                operation.scope == canonical.scope
                and operation.state in _RECOVERABLE_STATES
                for operation in operations
            ):
                raise KnowledgeLifecycleConflictError(
                    "Project already has a recoverable knowledge operation."
                )
            if len(operations) >= MAX_KNOWLEDGE_OPERATIONS:
                terminal_position = next(
                    (
                        index
                        for index, operation in enumerate(operations)
                        if operation.state
                        in {"succeeded", "cancelled", "failed"}
                    ),
                    None,
                )
                if terminal_position is None:
                    raise KnowledgeLifecycleStorageError(
                        "Knowledge lifecycle journal reached its active-operation limit."
                    )
                # The journal is operational recovery state rather than an
                # unbounded audit log. Evict only the oldest terminal entry;
                # nonterminal work is never discarded to admit a new command.
                del operations[terminal_position]
            operations.append(canonical)
            self._write_operations(tuple(operations))
            return canonical

    def get_operation(
        self,
        operation_id: str,
    ) -> KnowledgeOperationSnapshot:
        """Return one exact operation or a stable not-found failure."""

        canonical_id = validate_knowledge_operation_id(operation_id)
        with _journal_file_lock(self._catalog_file):
            for operation in self._load_operations():
                if operation.operation_id == canonical_id:
                    return operation
        raise KnowledgeLifecycleNotFoundError(
            "Knowledge operation does not exist."
        )

    def list_operations(self) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Return all operations in deterministic creation order."""

        with _journal_file_lock(self._catalog_file):
            return self._load_operations()

    def list_recoverable(self) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Return work still requiring execution or safe reconciliation."""

        with _journal_file_lock(self._catalog_file):
            return tuple(
                operation
                for operation in self._load_operations()
                if operation.state in _RECOVERABLE_STATES
            )

    def save_operation(
        self,
        snapshot: KnowledgeOperationSnapshot,
        *,
        expected_revision: int,
    ) -> KnowledgeOperationSnapshot:
        """Publish exactly the next journal revision through CAS."""

        canonical = self._snapshot_copy(snapshot)
        if (
            type(expected_revision) is not int
            or not 1 <= expected_revision < MAX_JSON_SAFE_INTEGER
        ):
            raise KnowledgeLifecycleValidationError(
                "expected_revision must permit one next JSON-safe revision."
            )
        if canonical.journal_revision != expected_revision + 1:
            raise KnowledgeLifecycleConflictError(
                "Knowledge operation checkpoint is not the next revision."
            )
        with _journal_file_lock(self._catalog_file):
            operations = list(self._load_operations())
            position = next(
                (
                    index
                    for index, operation in enumerate(operations)
                    if operation.operation_id == canonical.operation_id
                ),
                None,
            )
            if position is None:
                raise KnowledgeLifecycleNotFoundError(
                    "Knowledge operation does not exist."
                )
            current = operations[position]
            if current.journal_revision != expected_revision:
                raise KnowledgeLifecycleConflictError(
                    "Knowledge operation journal revision changed."
                )
            _validate_checkpoint_update(current, canonical)
            operations[position] = canonical
            self._write_operations(tuple(operations))
            return canonical

    def request_cancel(self, operation_id: str) -> KnowledgeOperationSnapshot:
        """Atomically request cancellation unless the operation is terminal."""

        canonical_id = validate_knowledge_operation_id(operation_id)
        with _journal_file_lock(self._catalog_file):
            operations = list(self._load_operations())
            position = next(
                (
                    index
                    for index, operation in enumerate(operations)
                    if operation.operation_id == canonical_id
                ),
                None,
            )
            if position is None:
                raise KnowledgeLifecycleNotFoundError(
                    "Knowledge operation does not exist."
                )
            current = operations[position]
            if current.state in {"cancel_requested", "cancelled"}:
                return current
            if current.state in {"succeeded", "failed"}:
                raise KnowledgeLifecycleConflictError(
                    "A terminal knowledge operation cannot be cancelled."
                )
            if current.journal_revision >= MAX_JSON_SAFE_INTEGER:
                raise KnowledgeLifecycleConflictError(
                    "Knowledge operation journal revision is exhausted."
                )
            now = datetime.now(timezone.utc)
            replacement = replace(
                current,
                journal_revision=current.journal_revision + 1,
                state="cancel_requested",
                updated_at=max(current.updated_at, now),
            )
            operations[position] = replacement
            self._write_operations(tuple(operations))
            return replacement

    @staticmethod
    def _snapshot_copy(snapshot: object) -> KnowledgeOperationSnapshot:
        """Detach one public snapshot through strict serialization values."""

        if type(snapshot) is not KnowledgeOperationSnapshot:
            raise KnowledgeLifecycleValidationError(
                "Knowledge operation snapshot is invalid."
            )
        try:
            return _operation_from_value(_operation_to_data(snapshot))
        except (MemoryError, RecursionError):
            raise KnowledgeLifecycleValidationError(
                "Knowledge operation snapshot exceeds a safe resource limit."
            ) from None
        except (
            AttributeError,
            KnowledgeLifecycleDataCorruptionError,
            KnowledgeLifecycleValidationError,
            ProjectSourceValidationError,
            TypeError,
            ValueError,
        ):
            raise KnowledgeLifecycleValidationError(
                "Knowledge operation snapshot is invalid."
            ) from None

    def _read_catalog_bytes(self) -> bytes | None:
        """Read at most four MiB through one pinned regular descriptor."""

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
                raise KnowledgeLifecycleStorageError(
                    "Knowledge lifecycle journal storage is unsafe."
                )
            if opened.st_size > MAX_KNOWLEDGE_CATALOG_BYTES:
                raise KnowledgeLifecycleDataCorruptionError(
                    "Stored knowledge lifecycle journal exceeds its size limit."
                )
            chunks: list[bytes] = []
            remaining = MAX_KNOWLEDGE_CATALOG_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_KNOWLEDGE_CATALOG_BYTES:
                raise KnowledgeLifecycleDataCorruptionError(
                    "Stored knowledge lifecycle journal exceeds its size limit."
                )
            opened_after = os.fstat(descriptor)
            linked_after = self._catalog_file.lstat()
            if (
                opened.st_dev != opened_after.st_dev
                or opened.st_ino != opened_after.st_ino
                or opened.st_size != opened_after.st_size
                or opened.st_mtime_ns != opened_after.st_mtime_ns
                or opened.st_dev != linked_after.st_dev
                or opened.st_ino != linked_after.st_ino
                or stat_module.S_ISLNK(linked_after.st_mode)
                or _stat_is_reparse(linked_after)
                or opened_after.st_nlink != 1
            ):
                raise KnowledgeLifecycleStorageError(
                    "Knowledge lifecycle journal changed during its read."
                )
            return payload
        except (
            KnowledgeLifecycleDataCorruptionError,
            KnowledgeLifecycleStorageError,
        ):
            raise
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle journal is unreadable."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)

    def _load_operations(self) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Load and validate the complete bounded operation catalog."""

        payload = self._read_catalog_bytes()
        if payload is None:
            return ()
        try:
            raw = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_int=_bounded_json_integer,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle journal is unreadable."
            ) from None
        except (MemoryError, RecursionError):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle journal exceeds a safe parsing limit."
            ) from None
        root = _exact_object(
            raw,
            {"schema_version", "operations"},
            "knowledge lifecycle journal",
        )
        if _exact_integer(root["schema_version"], "journal schema version") != (
            KNOWLEDGE_OPERATION_CATALOG_SCHEMA_VERSION
        ):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle journal version is unsupported."
            )
        raw_operations = root["operations"]
        if type(raw_operations) is not list or len(raw_operations) > (
            MAX_KNOWLEDGE_OPERATIONS
        ):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle operation list is invalid."
            )
        operations = tuple(
            _operation_from_value(value) for value in raw_operations
        )
        if any(
            _operation_semantics_issue(operation) is not None
            for operation in operations
        ):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle operation state is inconsistent."
            )
        operation_ids = tuple(
            operation.operation_id for operation in operations
        )
        if len(set(operation_ids)) != len(operation_ids):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle journal has duplicate operations."
            )
        recoverable_scopes = tuple(
            operation.scope
            for operation in operations
            if operation.state in _RECOVERABLE_STATES
        )
        if len(set(recoverable_scopes)) != len(recoverable_scopes):
            raise KnowledgeLifecycleDataCorruptionError(
                "Stored knowledge lifecycle journal has conflicting active work."
            )
        # Array order is the durable creation sequence. Wall clocks may repeat
        # or move backward, and opaque random IDs must never decide which
        # terminal operation is oldest or which policy intent is newest.
        return operations

    def _write_operations(
        self,
        operations: tuple[KnowledgeOperationSnapshot, ...],
    ) -> None:
        """Atomically replace the complete bounded journal document."""

        if len(operations) > MAX_KNOWLEDGE_OPERATIONS:
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle journal exceeds its operation limit."
            )
        document: Mapping[str, object] = {
            "schema_version": KNOWLEDGE_OPERATION_CATALOG_SCHEMA_VERSION,
            "operations": [
                _operation_to_data(operation) for operation in operations
            ],
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
                "Knowledge lifecycle journal cannot be serialized safely."
            ) from None
        if len(payload) > MAX_KNOWLEDGE_CATALOG_BYTES:
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle journal exceeds its storage limit."
            )
        _require_safe_directory_chain(self._storage_directory)
        try:
            descriptor, temporary_name = mkstemp(
                dir=self._storage_directory,
                prefix=f".{_CATALOG_FILE_NAME}.",
                suffix=".tmp",
            )
        except OSError:
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle journal storage is unavailable."
            ) from None
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._catalog_file)
            # POSIX needs the directory entry flushed after rename. Windows
            # cannot open directories this way; the replacement file itself
            # has already been durably flushed before ``os.replace``.
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
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle journal could not be replaced safely."
            ) from None
        finally:
            if descriptor != -1:
                os.close(descriptor)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
