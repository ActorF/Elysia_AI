"""Validate and persist the desktop application's public settings.

The environment-backed :class:`AppSettings` remains the bootstrap source.
This module stores only the small, non-sensitive subset users can edit from
the desktop UI.  Persisted data is schema checked and atomically replaced so
a failed write cannot damage the last known-good settings file.
"""

from __future__ import annotations

import json
import os
import re
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
    DEFAULT_AUTO_READ_ALOUD,
    DEFAULT_AUTOMATIC_RELISTEN,
    DEFAULT_CAPTIONS_ENABLED,
    DEFAULT_DATA_IMPORT_MAX_BYTES,
    DEFAULT_MEMORY_RETRIEVAL_LIMIT,
    DEFAULT_MODEL_NAME,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET,
    DEFAULT_SPEECH_RATE_PERCENT,
    DEFAULT_SPEECH_VOLUME_PERCENT,
    DEFAULT_TRANSCRIPT_REVIEW_MODE,
    DEFAULT_TRANSCRIPTION_DEVICE,
    DEFAULT_TRANSCRIPTION_LANGUAGE,
    DEFAULT_TRANSCRIPTION_MODEL,
    DEFAULT_VOICE_PROFILE_ID,
    TRANSCRIPT_REVIEW_MODES,
    TRANSCRIPTION_DEVICES,
    TRANSCRIPTION_LANGUAGES,
    TRANSCRIPTION_MODELS,
    AppSettings,
    TranscriptionDevice,
    TranscriptionLanguage,
    TranscriptionModel,
    TranscriptReviewMode,
)

DESKTOP_SETTINGS_SCHEMA_VERSION: Final = 3
_LEGACY_DESKTOP_SETTINGS_SCHEMA_VERSION: Final = 1
_PREVIOUS_DESKTOP_SETTINGS_SCHEMA_VERSION: Final = 2
MAX_MODEL_NAME_LENGTH: Final = 200
MAX_OLLAMA_HOST_LENGTH: Final = 2_048
MAX_MEMORY_SETTING: Final = 10_000_000
MAX_DATA_IMPORT_BYTES: Final = 2_147_483_647
MIN_SPEECH_RATE_PERCENT: Final = 50
MAX_SPEECH_RATE_PERCENT: Final = 200
MIN_SPEECH_VOLUME_PERCENT: Final = 0
MAX_SPEECH_VOLUME_PERCENT: Final = 100
MAX_VOICE_PROFILE_ID_LENGTH: Final = 64
MAX_JSON_SAFE_INTEGER: Final = 9_007_199_254_740_991
_FILE_LOCK_TIMEOUT_SECONDS: Final = 5.0

_DOCUMENT_FIELDS: Final = frozenset({
    "schema_version",
    "revision",
    "updated_at",
    "settings",
})
_LEGACY_SETTINGS_FIELDS: Final = frozenset({
    "model_name",
    "ollama_host",
    "short_term_memory_token_budget",
    "memory_retrieval_limit",
    "data_import_max_bytes",
})
_VERSION_TWO_SETTINGS_FIELDS: Final = frozenset({
    *_LEGACY_SETTINGS_FIELDS,
    "transcription_model",
    "transcription_device",
    "transcription_language",
})
_SETTINGS_FIELDS: Final = frozenset({
    *_VERSION_TWO_SETTINGS_FIELDS,
    "auto_read_aloud",
    "speech_rate_percent",
    "speech_volume_percent",
    "voice_profile_id",
    "captions_enabled",
    "transcript_review_mode",
    "automatic_relisten",
})
_VOICE_PROFILE_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
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
    """Serialize settings CAS operations across threads and app processes.

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
    transcription_model: TranscriptionModel = DEFAULT_TRANSCRIPTION_MODEL
    transcription_device: TranscriptionDevice = DEFAULT_TRANSCRIPTION_DEVICE
    transcription_language: TranscriptionLanguage = (
        DEFAULT_TRANSCRIPTION_LANGUAGE
    )
    auto_read_aloud: bool = DEFAULT_AUTO_READ_ALOUD
    speech_rate_percent: int = DEFAULT_SPEECH_RATE_PERCENT
    speech_volume_percent: int = DEFAULT_SPEECH_VOLUME_PERCENT
    voice_profile_id: str = DEFAULT_VOICE_PROFILE_ID
    captions_enabled: bool = DEFAULT_CAPTIONS_ENABLED
    transcript_review_mode: TranscriptReviewMode = (
        DEFAULT_TRANSCRIPT_REVIEW_MODE
    )
    automatic_relisten: bool = DEFAULT_AUTOMATIC_RELISTEN

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
        validate_transcription_model(self.transcription_model)
        validate_transcription_device(self.transcription_device)
        validate_transcription_language(self.transcription_language)
        validate_boolean(self.auto_read_aloud, "auto_read_aloud")
        _validate_integer_range(
            self.speech_rate_percent,
            "speech_rate_percent",
            minimum=MIN_SPEECH_RATE_PERCENT,
            maximum=MAX_SPEECH_RATE_PERCENT,
        )
        _validate_integer_range(
            self.speech_volume_percent,
            "speech_volume_percent",
            minimum=MIN_SPEECH_VOLUME_PERCENT,
            maximum=MAX_SPEECH_VOLUME_PERCENT,
        )
        validate_voice_profile_id(self.voice_profile_id)
        validate_boolean(self.captions_enabled, "captions_enabled")
        validate_transcript_review_mode(self.transcript_review_mode)
        validate_boolean(self.automatic_relisten, "automatic_relisten")


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


def _validate_integer_range(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Return one real integer inside an inclusive settings range."""

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise DesktopSettingsValidationError(
            f"{field_name} must be an integer between {minimum} and {maximum}."
        )
    return value


def validate_boolean(value: object, field_name: str) -> bool:
    """Return a settings Boolean without accepting integer lookalikes."""

    if not isinstance(value, bool):
        raise DesktopSettingsValidationError(
            f"{field_name} must be a Boolean."
        )
    return value


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


def validate_transcription_model(value: object) -> TranscriptionModel:
    """Return one offline model choice from the fixed local allowlist."""

    if not isinstance(value, str) or value not in TRANSCRIPTION_MODELS:
        raise DesktopSettingsValidationError(
            "transcription_model must be a supported local model."
        )
    return cast(TranscriptionModel, value)


def validate_transcription_device(value: object) -> TranscriptionDevice:
    """Return one supported local transcription device preference."""

    if not isinstance(value, str) or value not in TRANSCRIPTION_DEVICES:
        raise DesktopSettingsValidationError(
            "transcription_device must be auto, cuda, or cpu."
        )
    return cast(TranscriptionDevice, value)


def validate_transcription_language(value: object) -> TranscriptionLanguage:
    """Return one supported default transcription language hint."""

    if not isinstance(value, str) or value not in TRANSCRIPTION_LANGUAGES:
        raise DesktopSettingsValidationError(
            "transcription_language must be auto, zh, or en."
        )
    return cast(TranscriptionLanguage, value)


def validate_voice_profile_id(value: object) -> str:
    """Return one bounded logical voice ID without accepting paths or labels."""

    if (
        not isinstance(value, str)
        or len(value) > MAX_VOICE_PROFILE_ID_LENGTH
        or _VOICE_PROFILE_ID_PATTERN.fullmatch(value) is None
    ):
        raise DesktopSettingsValidationError(
            "voice_profile_id must be a bounded lowercase logical identifier."
        )
    return value


def validate_transcript_review_mode(value: object) -> TranscriptReviewMode:
    """Keep transcript submission behind the supported manual-review policy."""

    if not isinstance(value, str) or value not in TRANSCRIPT_REVIEW_MODES:
        raise DesktopSettingsValidationError(
            "transcript_review_mode must be manual."
        )
    return cast(TranscriptReviewMode, value)


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
        transcription_model=validate_transcription_model(
            settings.transcription_model
        ),
        transcription_device=validate_transcription_device(
            settings.transcription_device
        ),
        transcription_language=validate_transcription_language(
            settings.transcription_language
        ),
        auto_read_aloud=validate_boolean(
            settings.auto_read_aloud,
            "auto_read_aloud",
        ),
        speech_rate_percent=_validate_integer_range(
            settings.speech_rate_percent,
            "speech_rate_percent",
            minimum=MIN_SPEECH_RATE_PERCENT,
            maximum=MAX_SPEECH_RATE_PERCENT,
        ),
        speech_volume_percent=_validate_integer_range(
            settings.speech_volume_percent,
            "speech_volume_percent",
            minimum=MIN_SPEECH_VOLUME_PERCENT,
            maximum=MAX_SPEECH_VOLUME_PERCENT,
        ),
        voice_profile_id=validate_voice_profile_id(settings.voice_profile_id),
        captions_enabled=validate_boolean(
            settings.captions_enabled,
            "captions_enabled",
        ),
        transcript_review_mode=validate_transcript_review_mode(
            settings.transcript_review_mode
        ),
        automatic_relisten=validate_boolean(
            settings.automatic_relisten,
            "automatic_relisten",
        ),
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
    try:
        transcription_model = validate_transcription_model(
            settings.transcription_model
        )
    except DesktopSettingsValidationError:
        transcription_model = DEFAULT_TRANSCRIPTION_MODEL
    try:
        transcription_device = validate_transcription_device(
            settings.transcription_device
        )
    except DesktopSettingsValidationError:
        transcription_device = DEFAULT_TRANSCRIPTION_DEVICE
    try:
        transcription_language = validate_transcription_language(
            settings.transcription_language
        )
    except DesktopSettingsValidationError:
        transcription_language = DEFAULT_TRANSCRIPTION_LANGUAGE
    try:
        voice_profile_id = validate_voice_profile_id(settings.voice_profile_id)
    except DesktopSettingsValidationError:
        voice_profile_id = DEFAULT_VOICE_PROFILE_ID
    try:
        transcript_review_mode = validate_transcript_review_mode(
            settings.transcript_review_mode
        )
    except DesktopSettingsValidationError:
        transcript_review_mode = cast(
            TranscriptReviewMode,
            DEFAULT_TRANSCRIPT_REVIEW_MODE,
        )

    def positive_or_default(value: object, default: int, maximum: int) -> int:
        try:
            _validate_positive_integer(value, "value", maximum=maximum)
        except DesktopSettingsValidationError:
            return default
        return cast(int, value)

    def bounded_or_default(
        value: object,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        """Recover one malformed bounded bootstrap integer to its safe default."""

        try:
            return _validate_integer_range(
                value,
                "value",
                minimum=minimum,
                maximum=maximum,
            )
        except DesktopSettingsValidationError:
            return default

    def boolean_or_default(value: object, default: bool) -> bool:
        """Recover one malformed bootstrap Boolean to its safe default."""

        return value if isinstance(value, bool) else default

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
        transcription_model=transcription_model,
        transcription_device=transcription_device,
        transcription_language=transcription_language,
        auto_read_aloud=boolean_or_default(
            settings.auto_read_aloud,
            DEFAULT_AUTO_READ_ALOUD,
        ),
        speech_rate_percent=bounded_or_default(
            settings.speech_rate_percent,
            DEFAULT_SPEECH_RATE_PERCENT,
            MIN_SPEECH_RATE_PERCENT,
            MAX_SPEECH_RATE_PERCENT,
        ),
        speech_volume_percent=bounded_or_default(
            settings.speech_volume_percent,
            DEFAULT_SPEECH_VOLUME_PERCENT,
            MIN_SPEECH_VOLUME_PERCENT,
            MAX_SPEECH_VOLUME_PERCENT,
        ),
        voice_profile_id=voice_profile_id,
        captions_enabled=boolean_or_default(
            settings.captions_enabled,
            DEFAULT_CAPTIONS_ENABLED,
        ),
        transcript_review_mode=transcript_review_mode,
        automatic_relisten=boolean_or_default(
            settings.automatic_relisten,
            DEFAULT_AUTOMATIC_RELISTEN,
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
        transcription_model=values.transcription_model,
        transcription_device=values.transcription_device,
        transcription_language=values.transcription_language,
        auto_read_aloud=values.auto_read_aloud,
        speech_rate_percent=values.speech_rate_percent,
        speech_volume_percent=values.speech_volume_percent,
        voice_profile_id=values.voice_profile_id,
        captions_enabled=values.captions_enabled,
        transcript_review_mode=values.transcript_review_mode,
        automatic_relisten=values.automatic_relisten,
    )


def apply_live_editable_settings(
    base: AppSettings,
    values: EditableDesktopSettings,
) -> AppSettings:
    """Apply only preferences whose contracts do not require backend restart.

    Model, transcription, profile, and synthesis-rate changes keep their old
    active values until restart. Renderer/session policy and playback volume
    are safe to adopt between admitted Chat and transcription operations.
    """

    return replace(
        base,
        auto_read_aloud=values.auto_read_aloud,
        speech_volume_percent=values.speech_volume_percent,
        captions_enabled=values.captions_enabled,
        transcript_review_mode=values.transcript_review_mode,
        automatic_relisten=values.automatic_relisten,
    )


def changed_setting_names(
    desired: EditableDesktopSettings,
    active: EditableDesktopSettings,
) -> tuple[str, ...]:
    """Return stable UI names for differing restart-bound settings only.

    Live voice preferences are deliberately absent because the Backend merges
    them into its active snapshot immediately after a successful save.
    """

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
        (
            "transcriptionModel",
            desired.transcription_model,
            active.transcription_model,
        ),
        (
            "transcriptionDevice",
            desired.transcription_device,
            active.transcription_device,
        ),
        (
            "transcriptionLanguage",
            desired.transcription_language,
            active.transcription_language,
        ),
        (
            "speechRatePercent",
            desired.speech_rate_percent,
            active.speech_rate_percent,
        ),
        (
            "voiceProfileId",
            desired.voice_profile_id,
            active.voice_profile_id,
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
        """Return the local persisted-settings path for diagnostics and tests."""

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
        schema_version = value.get("schema_version")
        if schema_version not in {
            _LEGACY_DESKTOP_SETTINGS_SCHEMA_VERSION,
            _PREVIOUS_DESKTOP_SETTINGS_SCHEMA_VERSION,
            DESKTOP_SETTINGS_SCHEMA_VERSION,
        }:
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
        if schema_version == _LEGACY_DESKTOP_SETTINGS_SCHEMA_VERSION:
            expected_fields = _LEGACY_SETTINGS_FIELDS
        elif schema_version == _PREVIOUS_DESKTOP_SETTINGS_SCHEMA_VERSION:
            expected_fields = _VERSION_TWO_SETTINGS_FIELDS
        else:
            expected_fields = _SETTINGS_FIELDS
        if (
            not isinstance(raw_settings, dict)
            or set(raw_settings) != expected_fields
        ):
            raise DesktopSettingsValidationError(
                "Saved settings values have invalid fields."
            )
        # Older versions are expanded only in memory so their CAS revision and
        # bytes remain stable. The next real edit writes a complete v3 document.
        # Version 1 predates local transcription; both v1 and v2 predate the
        # portable voice-behavior preferences introduced here.
        transcription_model = (
            DEFAULT_TRANSCRIPTION_MODEL
            if schema_version == _LEGACY_DESKTOP_SETTINGS_SCHEMA_VERSION
            else raw_settings.get("transcription_model")
        )
        transcription_device = (
            DEFAULT_TRANSCRIPTION_DEVICE
            if schema_version == _LEGACY_DESKTOP_SETTINGS_SCHEMA_VERSION
            else raw_settings.get("transcription_device")
        )
        transcription_language = (
            DEFAULT_TRANSCRIPTION_LANGUAGE
            if schema_version == _LEGACY_DESKTOP_SETTINGS_SCHEMA_VERSION
            else raw_settings.get("transcription_language")
        )
        has_voice_preferences = schema_version == DESKTOP_SETTINGS_SCHEMA_VERSION
        auto_read_aloud = (
            raw_settings.get("auto_read_aloud")
            if has_voice_preferences
            else DEFAULT_AUTO_READ_ALOUD
        )
        speech_rate_percent = (
            raw_settings.get("speech_rate_percent")
            if has_voice_preferences
            else DEFAULT_SPEECH_RATE_PERCENT
        )
        speech_volume_percent = (
            raw_settings.get("speech_volume_percent")
            if has_voice_preferences
            else DEFAULT_SPEECH_VOLUME_PERCENT
        )
        voice_profile_id = (
            raw_settings.get("voice_profile_id")
            if has_voice_preferences
            else DEFAULT_VOICE_PROFILE_ID
        )
        captions_enabled = (
            raw_settings.get("captions_enabled")
            if has_voice_preferences
            else DEFAULT_CAPTIONS_ENABLED
        )
        transcript_review_mode = (
            raw_settings.get("transcript_review_mode")
            if has_voice_preferences
            else DEFAULT_TRANSCRIPT_REVIEW_MODE
        )
        automatic_relisten = (
            raw_settings.get("automatic_relisten")
            if has_voice_preferences
            else DEFAULT_AUTOMATIC_RELISTEN
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
            transcription_model=cast(
                TranscriptionModel,
                transcription_model,
            ),
            transcription_device=cast(
                TranscriptionDevice,
                transcription_device,
            ),
            transcription_language=cast(
                TranscriptionLanguage,
                transcription_language,
            ),
            auto_read_aloud=cast(bool, auto_read_aloud),
            speech_rate_percent=cast(int, speech_rate_percent),
            speech_volume_percent=cast(int, speech_volume_percent),
            voice_profile_id=cast(str, voice_profile_id),
            captions_enabled=cast(bool, captions_enabled),
            transcript_review_mode=cast(
                TranscriptReviewMode,
                transcript_review_mode,
            ),
            automatic_relisten=cast(bool, automatic_relisten),
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
                "transcription_model": snapshot.values.transcription_model,
                "transcription_device": snapshot.values.transcription_device,
                "transcription_language": (
                    snapshot.values.transcription_language
                ),
                "auto_read_aloud": snapshot.values.auto_read_aloud,
                "speech_rate_percent": snapshot.values.speech_rate_percent,
                "speech_volume_percent": (
                    snapshot.values.speech_volume_percent
                ),
                "voice_profile_id": snapshot.values.voice_profile_id,
                "captions_enabled": snapshot.values.captions_enabled,
                "transcript_review_mode": (
                    snapshot.values.transcript_review_mode
                ),
                "automatic_relisten": snapshot.values.automatic_relisten,
            },
        }

    def _atomic_write(self, value: dict[str, object]) -> None:
        """Flush a sibling temporary file before atomically replacing state."""

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
