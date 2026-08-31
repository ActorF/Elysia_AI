"""Validate and persist the desktop application's public settings.

The environment-backed :class:`AppSettings` remains the bootstrap source.
This module stores only the small, non-sensitive subset users can edit from
the desktop UI.  Persisted data is schema checked and atomically replaced so
a failed write cannot damage the last known-good settings file.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkstemp
from threading import Lock, RLock
from typing import BinaryIO, Final, cast
from urllib.parse import urlsplit

from .settings import (
    DEFAULT_DATA_IMPORT_MAX_BYTES,
    DEFAULT_MEMORY_RETRIEVAL_LIMIT,
    DEFAULT_MODEL_NAME,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET,
    AppSettings,
)

DESKTOP_SETTINGS_SCHEMA_VERSION: Final = 1
MAX_MODEL_NAME_LENGTH: Final = 200
MAX_OLLAMA_HOST_LENGTH: Final = 2_048
MAX_MEMORY_SETTING: Final = 10_000_000
MAX_DATA_IMPORT_BYTES: Final = 2_147_483_647
MAX_JSON_SAFE_INTEGER: Final = 9_007_199_254_740_991
_FILE_LOCK_TIMEOUT_SECONDS: Final = 5.0

_DOCUMENT_FIELDS: Final = frozenset({
    "schema_version",
    "revision",
    "updated_at",
    "settings",
})
_SETTINGS_FIELDS: Final = frozenset({
    "model_name",
    "ollama_host",
    "short_term_memory_token_budget",
    "memory_retrieval_limit",
    "data_import_max_bytes",
})
_PATH_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[Path, RLock] = {}

Clock = Callable[[], datetime]
ReplaceFile = Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None]


class DesktopSettingsError(Exception):
    """Base error for desktop-settings operations."""


class DesktopSettingsValidationError(DesktopSettingsError):
    """Raised when public settings are malformed or unsafe."""


class DesktopSettingsConflictError(DesktopSettingsError):
    """Raised when a stale UI attempts to replace newer settings."""


class DesktopSettingsStorageError(DesktopSettingsError):
    """Raised when the settings file cannot be saved safely."""


def _thread_lock_for(path: Path) -> RLock:
    normalized = path.resolve()
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized, RLock())


def _try_lock_stream(stream: BinaryIO) -> bool:
    stream.seek(0)
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def desktop_settings_file_lock(path: Path) -> Iterator[None]:
    """Serialize settings CAS operations across threads and app processes."""

    normalized = Path(path).resolve()
    lock_path = normalized.with_name(f".{normalized.name}.lock")
    with _thread_lock_for(normalized):
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                deadline = time.monotonic() + _FILE_LOCK_TIMEOUT_SECONDS
                while not _try_lock_stream(stream):
                    if time.monotonic() >= deadline:
                        raise DesktopSettingsStorageError(
                            "Settings are busy in another Elysia process."
                        )
                    time.sleep(0.025)
                try:
                    yield
                finally:
                    _unlock_stream(stream)
        except DesktopSettingsStorageError:
            raise
        except OSError as error:
            raise DesktopSettingsStorageError(
                "Settings could not be locked for a safe update."
            ) from error


@dataclass(frozen=True, slots=True)
class EditableDesktopSettings:
    """The complete, deliberately non-sensitive desktop settings surface."""

    model_name: str
    ollama_host: str
    short_term_memory_token_budget: int
    memory_retrieval_limit: int
    data_import_max_bytes: int

    def __post_init__(self) -> None:
        """Normalize nothing implicitly and reject every invalid field."""

        validate_model_name(self.model_name)
        validate_ollama_host(self.ollama_host)
        _validate_positive_integer(
            self.short_term_memory_token_budget,
            "short_term_memory_token_budget",
            maximum=MAX_MEMORY_SETTING,
        )
        _validate_positive_integer(
            self.memory_retrieval_limit,
            "memory_retrieval_limit",
            maximum=MAX_MEMORY_SETTING,
        )
        _validate_positive_integer(
            self.data_import_max_bytes,
            "data_import_max_bytes",
            maximum=MAX_DATA_IMPORT_BYTES,
        )


@dataclass(frozen=True, slots=True)
class DesktopSettingsSnapshot:
    """Return one revisioned desired-settings snapshot and recovery warning."""

    revision: int
    updated_at: datetime | None
    values: EditableDesktopSettings
    warning: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_integer(
    value: object,
    field_name: str,
    *,
    maximum: int,
) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > maximum
    ):
        raise DesktopSettingsValidationError(
            f"{field_name} must be an integer between 1 and {maximum}."
        )


def validate_model_name(value: object) -> str:
    """Return a model name only when it is bounded, trimmed, and printable."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_MODEL_NAME_LENGTH
        or "\x00" in value
        or any(character in "\r\n" for character in value)
    ):
        raise DesktopSettingsValidationError(
            "model_name must be a non-empty trimmed single-line string."
        )
    return value


def validate_ollama_host(value: object) -> str:
    """Return one safe HTTP(S) Ollama origin without credentials or a path."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_OLLAMA_HOST_LENGTH
        or "\x00" in value
        or any(character.isspace() for character in value)
    ):
        raise DesktopSettingsValidationError(
            "ollama_host must be a valid HTTP or HTTPS origin."
        )

    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError as error:
        raise DesktopSettingsValidationError(
            "ollama_host must be a valid HTTP or HTTPS origin."
        ) from error

    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed_port is not None and not 1 <= parsed_port <= 65_535
    ):
        raise DesktopSettingsValidationError(
            "ollama_host must be a valid HTTP or HTTPS origin."
        )
    return value.removesuffix("/")


def editable_from_app_settings(
    settings: AppSettings,
) -> EditableDesktopSettings:
    """Select the public editable subset from one bootstrap snapshot."""

    return EditableDesktopSettings(
        model_name=settings.model_name.strip(),
        ollama_host=validate_ollama_host(settings.ollama_host.strip()),
        short_term_memory_token_budget=(
            settings.short_term_memory_token_budget
        ),
        memory_retrieval_limit=settings.memory_retrieval_limit,
        data_import_max_bytes=settings.data_import_max_bytes,
    )


def desktop_defaults_from_app_settings(
    settings: AppSettings,
) -> EditableDesktopSettings:
    """Return safe desktop defaults even when public ``.env`` values are bad."""

    try:
        model_name = validate_model_name(settings.model_name.strip())
    except DesktopSettingsValidationError:
        model_name = DEFAULT_MODEL_NAME
    try:
        ollama_host = validate_ollama_host(settings.ollama_host.strip())
    except DesktopSettingsValidationError:
        ollama_host = DEFAULT_OLLAMA_HOST

    def positive_or_default(value: object, default: int, maximum: int) -> int:
        try:
            _validate_positive_integer(value, "value", maximum=maximum)
        except DesktopSettingsValidationError:
            return default
        return cast(int, value)

    return EditableDesktopSettings(
        model_name=model_name,
        ollama_host=ollama_host,
        short_term_memory_token_budget=positive_or_default(
            settings.short_term_memory_token_budget,
            DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET,
            MAX_MEMORY_SETTING,
        ),
        memory_retrieval_limit=positive_or_default(
            settings.memory_retrieval_limit,
            DEFAULT_MEMORY_RETRIEVAL_LIMIT,
            MAX_MEMORY_SETTING,
        ),
        data_import_max_bytes=positive_or_default(
            settings.data_import_max_bytes,
            DEFAULT_DATA_IMPORT_MAX_BYTES,
            MAX_DATA_IMPORT_BYTES,
        ),
    )


def apply_editable_settings(
    base: AppSettings,
    values: EditableDesktopSettings,
    *,
    model_override: str | None = None,
) -> AppSettings:
    """Build the immutable runtime snapshot with an optional session override."""

    model_name = values.model_name
    if model_override is not None:
        model_name = validate_model_name(model_override)
    return replace(
        base,
        model_name=model_name,
        ollama_host=values.ollama_host,
        short_term_memory_token_budget=(
            values.short_term_memory_token_budget
        ),
        memory_retrieval_limit=values.memory_retrieval_limit,
        data_import_max_bytes=values.data_import_max_bytes,
    )


def changed_setting_names(
    desired: EditableDesktopSettings,
    active: EditableDesktopSettings,
) -> tuple[str, ...]:
    """Return stable camel-case UI field names whose runtime values differ."""

    fields = (
        ("modelName", desired.model_name, active.model_name),
        ("ollamaHost", desired.ollama_host, active.ollama_host),
        (
            "shortTermMemoryTokenBudget",
            desired.short_term_memory_token_budget,
            active.short_term_memory_token_budget,
        ),
        (
            "memoryRetrievalLimit",
            desired.memory_retrieval_limit,
            active.memory_retrieval_limit,
        ),
        (
            "dataImportMaxBytes",
            desired.data_import_max_bytes,
            active.data_import_max_bytes,
        ),
    )
    return tuple(name for name, wanted, current in fields if wanted != current)


class DesktopSettingsRepository:
    """Read and atomically replace one strict revisioned JSON document."""

    def __init__(
        self,
        path: Path,
        defaults: EditableDesktopSettings,
        *,
        clock: Clock = _utc_now,
        replace_file: ReplaceFile = os.replace,
    ) -> None:
        self._path = Path(path)
        self._defaults = defaults
        self._clock = clock
        self._replace_file = replace_file
        self._lock = RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> DesktopSettingsSnapshot:
        """Load the persisted settings or recover to bootstrap defaults."""

        with self._lock:
            if not self._path.exists():
                return DesktopSettingsSnapshot(0, None, self._defaults)
            try:
                raw = self._path.read_text(encoding="utf-8")
                value = json.loads(raw)
                return self._snapshot_from_value(value)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                DesktopSettingsValidationError,
            ):
                self._quarantine_corrupt_file()
                return DesktopSettingsSnapshot(
                    0,
                    None,
                    self._defaults,
                    "Saved settings were invalid and bootstrap defaults were restored.",
                )

    def save(
        self,
        values: EditableDesktopSettings,
        *,
        expected_revision: int,
    ) -> DesktopSettingsSnapshot:
        """Atomically replace settings when the caller has the latest revision."""

        _validate_positive_or_zero_revision(expected_revision)
        with self._lock, desktop_settings_file_lock(self._path):
            current = self.load()
            if current.revision != expected_revision:
                raise DesktopSettingsConflictError(
                    "Settings changed elsewhere. Reload them before saving."
                )
            if current.values == values:
                return current
            if current.revision == MAX_JSON_SAFE_INTEGER:
                raise DesktopSettingsStorageError(
                    "Settings revision limit was reached; values were not changed."
                )
            updated_at = self._aware_now()
            next_snapshot = DesktopSettingsSnapshot(
                revision=current.revision + 1,
                updated_at=updated_at,
                values=values,
            )
            document = self._value_from_snapshot(next_snapshot)
            self._atomic_write(document)
            return next_snapshot

    @staticmethod
    def _snapshot_from_value(value: object) -> DesktopSettingsSnapshot:
        if not isinstance(value, dict) or set(value) != _DOCUMENT_FIELDS:
            raise DesktopSettingsValidationError(
                "Saved settings document has invalid fields."
            )
        if value.get("schema_version") != DESKTOP_SETTINGS_SCHEMA_VERSION:
            raise DesktopSettingsValidationError(
                "Saved settings schema version is unsupported."
            )
        revision = value.get("revision")
        _validate_positive_or_zero_revision(revision)
        if cast(int, revision) == 0:
            raise DesktopSettingsValidationError(
                "Persisted settings revision must be greater than zero."
            )
        raw_updated_at = value.get("updated_at")
        if not isinstance(raw_updated_at, str):
            raise DesktopSettingsValidationError(
                "Saved settings timestamp is invalid."
            )
        try:
            updated_at = datetime.fromisoformat(raw_updated_at)
        except ValueError as error:
            raise DesktopSettingsValidationError(
                "Saved settings timestamp is invalid."
            ) from error
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise DesktopSettingsValidationError(
                "Saved settings timestamp is invalid."
            )
        raw_settings = value.get("settings")
        if not isinstance(raw_settings, dict) or set(raw_settings) != _SETTINGS_FIELDS:
            raise DesktopSettingsValidationError(
                "Saved settings values have invalid fields."
            )
        values = EditableDesktopSettings(
            model_name=cast(str, raw_settings.get("model_name")),
            ollama_host=cast(str, raw_settings.get("ollama_host")),
            short_term_memory_token_budget=cast(
                int,
                raw_settings.get("short_term_memory_token_budget"),
            ),
            memory_retrieval_limit=cast(
                int,
                raw_settings.get("memory_retrieval_limit"),
            ),
            data_import_max_bytes=cast(
                int,
                raw_settings.get("data_import_max_bytes"),
            ),
        )
        return DesktopSettingsSnapshot(
            cast(int, revision),
            updated_at,
            values,
        )

    @staticmethod
    def _value_from_snapshot(snapshot: DesktopSettingsSnapshot) -> dict[str, object]:
        assert snapshot.updated_at is not None
        return {
            "schema_version": DESKTOP_SETTINGS_SCHEMA_VERSION,
            "revision": snapshot.revision,
            "updated_at": snapshot.updated_at.isoformat(),
            "settings": {
                "model_name": snapshot.values.model_name,
                "ollama_host": snapshot.values.ollama_host,
                "short_term_memory_token_budget": (
                    snapshot.values.short_term_memory_token_budget
                ),
                "memory_retrieval_limit": snapshot.values.memory_retrieval_limit,
                "data_import_max_bytes": snapshot.values.data_import_max_bytes,
            },
        }

    def _atomic_write(self, value: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    value,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._replace_file(temporary_path, self._path)
        except (OSError, TypeError, ValueError) as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise DesktopSettingsStorageError(
                "Settings could not be saved; the previous values remain active."
            ) from error

    def _quarantine_corrupt_file(self) -> None:
        timestamp = self._aware_now().strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self._path.with_name(
            f"{self._path.stem}.corrupt-{timestamp}{self._path.suffix}"
        )
        try:
            self._replace_file(self._path, quarantine)
        except OSError:
            # Startup must remain recoverable even if quarantine itself fails.
            pass

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DesktopSettingsStorageError(
                "Settings clock must return a timezone-aware timestamp."
            )
        return value.astimezone(timezone.utc)


def _validate_positive_or_zero_revision(value: object) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_JSON_SAFE_INTEGER
    ):
        raise DesktopSettingsValidationError(
            "revision must be a non-negative JSON-safe integer."
        )


def create_desktop_settings_repository(
    base: AppSettings,
) -> DesktopSettingsRepository:
    """Create the production repository under ignored local workspace data."""

    return DesktopSettingsRepository(
        base.base_dir / "workspace" / "settings" / "global.json",
        desktop_defaults_from_app_settings(base),
    )


def desktop_settings_snapshot_from_document(
    value: object,
) -> DesktopSettingsSnapshot:
    """Parse one persisted document at a recovery or portability boundary."""

    return DesktopSettingsRepository._snapshot_from_value(value)


def validate_desktop_settings_document(value: object) -> None:
    """Validate one persisted document for import/export boundaries."""

    desktop_settings_snapshot_from_document(value)
