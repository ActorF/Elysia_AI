"""Persist local audio-device preferences with strict recovery semantics."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkstemp
from threading import Lock, RLock
from typing import BinaryIO, Final, cast

from .domain import AudioDevicePreferences, VoiceSettingsSnapshot
from .exceptions import (
    VoiceSettingsConflictError,
    VoiceSettingsStorageError,
    VoiceSettingsValidationError,
)


VOICE_SETTINGS_SCHEMA_VERSION: Final = 1
MAX_JSON_SAFE_INTEGER: Final = 9_007_199_254_740_991
_FILE_LOCK_TIMEOUT_SECONDS: Final = 5.0
_DOCUMENT_FIELDS: Final = frozenset({
    "schema_version",
    "revision",
    "updated_at",
    "preferences",
})
_PREFERENCE_FIELDS: Final = frozenset({
    "input_device_id",
    "output_device_id",
})
_PATH_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[Path, RLock] = {}

Clock = Callable[[], datetime]
ReplaceFile = Callable[
    [
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ],
    None,
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
def voice_settings_file_lock(path: Path) -> Iterator[None]:
    """Serialize preference CAS operations across threads and app processes.

    A shared per-path ``RLock`` coordinates repository instances in this
    process, while an OS lock on a stable sidecar file protects the complete
    read/compare/write transaction from other Elysia processes. The sidecar
    contains one byte because Windows cannot lock an empty byte range.
    """

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
                        raise VoiceSettingsStorageError(
                            "Voice settings are busy in another Elysia process."
                        )
                    time.sleep(0.025)
                try:
                    yield
                finally:
                    _unlock_stream(stream)
        except VoiceSettingsStorageError:
            raise
        except OSError as error:
            raise VoiceSettingsStorageError(
                "Voice settings could not be locked for a safe update."
            ) from error


def _validate_revision(value: object, *, persisted: bool = False) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < (1 if persisted else 0)
        or value > MAX_JSON_SAFE_INTEGER
    ):
        raise VoiceSettingsValidationError(
            "Voice settings revision is outside the supported range."
        )
    return value


def _reject_json_constant(_value: str) -> object:
    raise VoiceSettingsValidationError(
        "Voice settings contain an invalid JSON constant."
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise VoiceSettingsValidationError(
                "Voice settings contain duplicate fields."
            )
        value[key] = item
    return value


class JsonVoiceSettingsRepository:
    """Read and atomically replace one strict revisioned preference document."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock = _utc_now,
        replace_file: ReplaceFile = os.replace,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._replace_file = replace_file
        self._lock = RLock()

    @property
    def path(self) -> Path:
        """Return the private local settings path for diagnostics and tests."""

        return self._path

    def load(self) -> VoiceSettingsSnapshot:
        """Load saved preferences or quarantine malformed local data."""

        with self._lock, voice_settings_file_lock(self._path):
            return self._load_locked()

    def save(
        self,
        values: AudioDevicePreferences,
        *,
        expected_revision: int,
    ) -> VoiceSettingsSnapshot:
        """Atomically replace preferences when the caller has latest revision."""

        if not isinstance(values, AudioDevicePreferences):
            raise VoiceSettingsValidationError(
                "Voice settings values are invalid."
            )
        revision = _validate_revision(expected_revision)
        with self._lock, voice_settings_file_lock(self._path):
            current = self._load_locked()
            if current.revision != revision:
                raise VoiceSettingsConflictError(
                    "Voice settings changed elsewhere. Reload before saving."
                )
            if current.values == values:
                return current
            if current.revision == MAX_JSON_SAFE_INTEGER:
                raise VoiceSettingsStorageError(
                    "Voice settings revision limit was reached; values were "
                    "not changed."
                )
            saved = VoiceSettingsSnapshot(
                revision=current.revision + 1,
                updated_at=self._aware_now(),
                values=values,
            )
            self._atomic_write(self.document_from_snapshot(saved))
            return saved

    def _load_locked(self) -> VoiceSettingsSnapshot:
        """Read and recover while the caller owns both repository locks."""

        if not self._path.exists():
            return VoiceSettingsSnapshot(
                0,
                None,
                AudioDevicePreferences(),
            )
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as error:
            raise VoiceSettingsStorageError(
                "Voice settings could not be read safely."
            ) from error
        except UnicodeError:
            return self._recover_invalid_locked()
        try:
            value = json.loads(
                raw,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_strict_object,
            )
            return self.snapshot_from_document(value)
        except (
            ValueError,
            RecursionError,
            VoiceSettingsValidationError,
        ):
            return self._recover_invalid_locked()

    def _recover_invalid_locked(self) -> VoiceSettingsSnapshot:
        self._quarantine_corrupt_file()
        return VoiceSettingsSnapshot(
            0,
            None,
            AudioDevicePreferences(),
            "Saved Voice device preferences were invalid and "
            "system defaults were restored.",
        )

    @staticmethod
    def snapshot_from_document(value: object) -> VoiceSettingsSnapshot:
        """Validate one persisted document at a recovery boundary."""

        if not isinstance(value, dict) or set(value) != _DOCUMENT_FIELDS:
            raise VoiceSettingsValidationError(
                "Voice settings document has invalid fields."
            )
        if value.get("schema_version") != VOICE_SETTINGS_SCHEMA_VERSION:
            raise VoiceSettingsValidationError(
                "Voice settings schema version is unsupported."
            )
        revision = _validate_revision(value.get("revision"), persisted=True)
        raw_updated_at = value.get("updated_at")
        if not isinstance(raw_updated_at, str):
            raise VoiceSettingsValidationError(
                "Voice settings timestamp is invalid."
            )
        try:
            updated_at = datetime.fromisoformat(raw_updated_at)
        except ValueError as error:
            raise VoiceSettingsValidationError(
                "Voice settings timestamp is invalid."
            ) from error
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise VoiceSettingsValidationError(
                "Voice settings timestamp is invalid."
            )
        raw_preferences = value.get("preferences")
        if (
            not isinstance(raw_preferences, dict)
            or set(raw_preferences) != _PREFERENCE_FIELDS
        ):
            raise VoiceSettingsValidationError(
                "Voice settings preferences have invalid fields."
            )
        preferences = AudioDevicePreferences(
            input_device_id=cast(
                str | None,
                raw_preferences.get("input_device_id"),
            ),
            output_device_id=cast(
                str | None,
                raw_preferences.get("output_device_id"),
            ),
        )
        return VoiceSettingsSnapshot(revision, updated_at, preferences)

    @staticmethod
    def document_from_snapshot(
        snapshot: VoiceSettingsSnapshot,
    ) -> dict[str, object]:
        """Serialize one validated non-default snapshot for atomic storage."""

        if snapshot.updated_at is None:
            raise VoiceSettingsValidationError(
                "Persisted Voice settings require a timestamp."
            )
        _validate_revision(snapshot.revision, persisted=True)
        return {
            "schema_version": VOICE_SETTINGS_SCHEMA_VERSION,
            "revision": snapshot.revision,
            "updated_at": snapshot.updated_at.isoformat(),
            "preferences": {
                "input_device_id": snapshot.values.input_device_id,
                "output_device_id": snapshot.values.output_device_id,
            },
        }

    def _atomic_write(self, value: dict[str, object]) -> None:
        """Flush a sibling temporary file before atomically replacing state."""

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=self._path.parent,
            )
        except OSError as error:
            raise VoiceSettingsStorageError(
                "Voice settings could not be saved; previous values remain "
                "active."
            ) from error
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as stream:
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
            raise VoiceSettingsStorageError(
                "Voice settings could not be saved; previous values remain "
                "active."
            ) from error

    def _quarantine_corrupt_file(self) -> None:
        timestamp = self._aware_now().strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self._path.with_name(
            f"{self._path.stem}.corrupt-{timestamp}{self._path.suffix}"
        )
        try:
            self._replace_file(self._path, quarantine)
        except OSError as error:
            raise VoiceSettingsStorageError(
                "Invalid Voice settings could not be quarantined safely."
            ) from error

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise VoiceSettingsStorageError(
                "Voice settings clock must return a timezone-aware timestamp."
            )
        return value.astimezone(timezone.utc)
