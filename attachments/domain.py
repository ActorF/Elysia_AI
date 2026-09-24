"""Public, path-independent values for the attachment surface."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

AttachmentScopeKind = Literal["chat", "project"]
AttachmentStatus = Literal["ready"]
FileOrigin = Literal["local_import", "legacy_migration", "generated"]
FileOwnershipRole = Literal["chat_attachment", "project_source"]

MAX_ATTACHMENT_ID_LENGTH: Final = 128
MAX_SCOPE_ID_LENGTH: Final = 128
MAX_FILE_NAME_LENGTH: Final = 255
MAX_MEDIA_TYPE_LENGTH: Final = 255
MAX_SOURCE_PATH_LENGTH: Final = 32_767
MAX_JSON_SAFE_INTEGER: Final = 9_007_199_254_740_991
FILE_METADATA_SCHEMA_VERSION: Final[Literal[1]] = 1
MAX_DERIVATION_KIND_LENGTH: Final = 64
MAX_PRODUCER_VERSION_LENGTH: Final = 128

_ATTACHMENT_ID_PATTERN = re.compile(
    r"^attachment_[A-Za-z0-9_-]{1,117}$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FILE_ID_PATTERN = re.compile(r"^file_[0-9a-f]{64}$")
_DERIVATION_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PRODUCER_VERSION_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"
)
_SCOPE_ID_PATTERNS: Final = {
    "chat": re.compile(r"^chat_[A-Za-z0-9_-]{1,122}$"),
    "project": re.compile(r"^project_[A-Za-z0-9_-]{1,119}$"),
}
_WINDOWS_RESERVED_STEMS: Final = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})
_BIDI_CONTROL_CODE_POINTS: Final = frozenset({
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
})
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)


def validate_attachment_id(value: object) -> str:
    """Return one storage-safe opaque attachment identifier."""

    if (
        not isinstance(value, str)
        or len(value) > MAX_ATTACHMENT_ID_LENGTH
        or _ATTACHMENT_ID_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("attachment_id has an invalid opaque-ID format.")
    return value


def validate_sha256(value: object) -> str:
    """Return one canonical lowercase SHA-256 digest."""

    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("sha256 must be a lowercase SHA-256 digest.")
    return value


def validate_file_id(value: object) -> str:
    """Return one content-stable file identifier without resolving a path."""

    if not isinstance(value, str) or _FILE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("file_id must contain one lowercase SHA-256 digest.")
    return value


def file_id_from_sha256(value: object) -> str:
    """Derive the stable opaque file identifier for canonical content."""

    return f"file_{validate_sha256(value)}"


def validate_file_name(value: object) -> str:
    """Validate a cross-platform display basename without resolving a path.

    Names cross both renderer and operating-system boundaries, so reserved
    Windows stems and bidirectional controls are rejected even on other hosts.
    """

    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value != value.strip()
        or value.endswith(".")
        or len(value) > MAX_FILE_NAME_LENGTH
        or any(character in '<>:"/\\|?*' for character in value)
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS
        or any(
            ord(character) < 32
            or ord(character) == 127
            or ord(character) in _BIDI_CONTROL_CODE_POINTS
            for character in value
        )
    ):
        raise ValueError("file_name is not a safe display basename.")
    return value


def validate_media_type(value: object) -> str:
    """Validate one normalized media type inferred by trusted storage code."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_MEDIA_TYPE_LENGTH
        or _MEDIA_TYPE_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("media_type is invalid.")
    return value


@dataclass(frozen=True, slots=True)
class AttachmentScope:
    """Identify one isolated Chat or Project attachment namespace."""

    kind: AttachmentScopeKind
    id: str

    def __post_init__(self) -> None:
        if self.kind not in _SCOPE_ID_PATTERNS:
            raise ValueError("Attachment scope kind must be chat or project.")
        if (
            not isinstance(self.id, str)
            or len(self.id) > MAX_SCOPE_ID_LENGTH
            or _SCOPE_ID_PATTERNS[self.kind].fullmatch(self.id) is None
        ):
            raise ValueError("Attachment scope ID does not match its kind.")


@dataclass(frozen=True, slots=True)
class AttachmentItem:
    """Expose safe attachment metadata without a source or storage path."""

    attachment_id: str
    file_name: str
    media_type: str
    size_bytes: int
    status: AttachmentStatus = "ready"

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
            raise ValueError("size_bytes must be a positive safe integer.")
        if self.status != "ready":
            raise ValueError("Public attachment status must be ready.")


@dataclass(frozen=True, slots=True)
class AttachmentState:
    """Carry renderer-safe drafts plus limits for newly selected files."""

    scope: AttachmentScope
    attachments: tuple[AttachmentItem, ...]
    max_file_bytes: int
    max_file_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, AttachmentScope):
            raise ValueError("scope must be AttachmentScope.")
        if (
            not isinstance(self.attachments, tuple)
            or not all(
                isinstance(item, AttachmentItem)
                for item in self.attachments
            )
        ):
            raise ValueError("attachments must be AttachmentItem values.")
        attachment_ids = [item.attachment_id for item in self.attachments]
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("Attachment state contains duplicate IDs.")
        for value, field_name in (
            (self.max_file_bytes, "max_file_bytes"),
            (self.max_file_count, "max_file_count"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > MAX_JSON_SAFE_INTEGER
            ):
                raise ValueError(f"{field_name} must be a positive safe integer.")
        if len(self.attachments) > self.max_file_count:
            raise ValueError("Attachment state exceeds its file-count limit.")


@dataclass(frozen=True, slots=True)
class OriginalFileMetadata:
    """Describe one immutable original without retaining its local path.

    The identifier is content-derived so repeated imports can share one
    original while separate ownership records preserve their Chat or Project
    boundaries. ``origin`` records how Elysia obtained the bytes, never where
    those bytes lived on the user's machine.
    """

    schema_version: Literal[1]
    file_id: str
    sha256: str
    file_name: str
    media_type: str
    size_bytes: int
    origin: FileOrigin
    imported_at: datetime

    def __post_init__(self) -> None:
        """Reject ambiguous, path-bearing, or non-canonical file metadata."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != FILE_METADATA_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported file metadata schema version.")
        digest = validate_sha256(self.sha256)
        file_id = validate_file_id(self.file_id)
        if file_id != file_id_from_sha256(digest):
            raise ValueError("file_id must be derived from sha256.")
        validate_file_name(self.file_name)
        validate_media_type(self.media_type)
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes <= 0
            or self.size_bytes > MAX_JSON_SAFE_INTEGER
        ):
            raise ValueError("size_bytes must be a positive safe integer.")
        if self.origin not in (
            "local_import",
            "legacy_migration",
            "generated",
        ):
            raise ValueError("origin is not a supported file origin.")
        _validate_utc_timestamp(self.imported_at, "imported_at")


@dataclass(frozen=True, slots=True)
class FileOwnership:
    """Bind one original to its exact Chat Attachment or Project Source."""

    schema_version: Literal[1]
    link_id: str
    file_id: str
    file_name: str
    media_type: str
    scope: AttachmentScope
    role: FileOwnershipRole
    imported_at: datetime

    def __post_init__(self) -> None:
        """Enforce a single unambiguous ownership role for each scope kind."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != FILE_METADATA_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported file ownership schema version.")
        validate_attachment_id(self.link_id)
        validate_file_id(self.file_id)
        validate_file_name(self.file_name)
        validate_media_type(self.media_type)
        if not isinstance(self.scope, AttachmentScope):
            raise ValueError("scope must be AttachmentScope.")
        expected_role: FileOwnershipRole = (
            "chat_attachment"
            if self.scope.kind == "chat"
            else "project_source"
        )
        if self.role != expected_role:
            raise ValueError("File ownership role does not match its scope.")
        _validate_utc_timestamp(self.imported_at, "imported_at")


@dataclass(frozen=True, slots=True)
class DerivedFileRelation:
    """Relate generated bytes to one original without exposing either path."""

    schema_version: Literal[1]
    original_file_id: str
    derived_file_id: str
    scope: AttachmentScope
    derivation_kind: str
    producer_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        """Reject self-relations and unstable derivation identifiers."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != FILE_METADATA_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported derived relation schema version.")
        original_id = validate_file_id(self.original_file_id)
        derived_id = validate_file_id(self.derived_file_id)
        if original_id == derived_id:
            raise ValueError("A derived file must differ from its original.")
        if not isinstance(self.scope, AttachmentScope):
            raise ValueError("scope must be AttachmentScope.")
        if (
            not isinstance(self.derivation_kind, str)
            or len(self.derivation_kind) > MAX_DERIVATION_KIND_LENGTH
            or _DERIVATION_KIND_PATTERN.fullmatch(self.derivation_kind) is None
        ):
            raise ValueError("derivation_kind must be a stable lowercase identifier.")
        if (
            not isinstance(self.producer_version, str)
            or len(self.producer_version) > MAX_PRODUCER_VERSION_LENGTH
            or _PRODUCER_VERSION_PATTERN.fullmatch(self.producer_version) is None
        ):
            raise ValueError("producer_version must be a stable non-empty identifier.")
        _validate_utc_timestamp(self.created_at, "created_at")


def _validate_utc_timestamp(value: object, field_name: str) -> datetime:
    """Require an aware zero-offset timestamp for deterministic persistence."""

    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be a UTC-aware datetime.")
    return value
