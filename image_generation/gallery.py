"""Persist verified generated PNGs in a private, recoverable local Gallery.

The Gallery treats its index as a rebuildable cache and each artifact
directory as the source of truth.  Creation and deletion commit with sibling
directory renames, while a process-wide file lease and an in-process ``RLock``
exclude concurrent writers.  Startup validates the complete authoritative
inventory once; metadata reads use that immutable snapshot, while
``open_verified`` revalidates the selected canonical PNG before returning
bytes, while deletion revalidates only the selected strict metadata and safe
filesystem structure before its authoritative rename.

This is an internal persistence boundary.  Callers must pass results obtained
through the trusted Backend composition; structural checks here do not
cryptographically attest generation provenance or bind the optional private
prompt back to a model execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import RLock
from typing import Final, List
import unicodedata
from uuid import UUID, uuid4

from .png import (
    CanonicalPng,
    MAX_GENERATED_PNG_BYTES,
    PNG_MEDIA_TYPE,
    canonicalize_png,
)
from .profiles import MAX_IMAGE_EDGE_PIXELS, MAX_IMAGE_PIXELS
from .service import GeneratedImageResult


GALLERY_SCHEMA_VERSION: Final = 1
MAX_GALLERY_ITEMS: Final = 1_024
MAX_GALLERY_TOTAL_IMAGE_BYTES: Final = 4 * 1024 * 1024 * 1024
MAX_GALLERY_MANIFEST_BYTES: Final = 64 * 1024
MAX_GALLERY_INDEX_BYTES: Final = 2 * 1024 * 1024
MAX_PRIVATE_PROMPT_CODEPOINTS: Final = 6_000
MAX_LIST_LIMIT: Final = 256
MAX_CORRUPT_INDEX_BACKUPS: Final = 8
MAX_ROOT_ENTRIES: Final = MAX_GALLERY_ITEMS + 64

_LOCK_NAME: Final = ".gallery.lock"
_OWNER_MARKER_NAME: Final = ".gallery.owner"
_OWNER_MARKER_BYTES: Final = b"elysia-image-gallery-v1\n"
_INDEX_NAME: Final = "index.json"
_IMAGE_NAME: Final = "image.png"
_MANIFEST_NAME: Final = "manifest.json"
_ARTIFACT_ID_PATTERN: Final = re.compile(r"^gallery_[0-9a-f]{32}$")
_ARTIFACT_DIRECTORY_PATTERN: Final = re.compile(r"^image_([0-9a-f]{32})$")
_STAGING_PATTERN: Final = re.compile(r"^\.staging_([0-9a-f]{32})$")
_TOMBSTONE_PATTERN: Final = re.compile(r"^\.deleting_([0-9a-f]{32})$")
_INDEX_TEMP_PATTERN: Final = re.compile(r"^\.index_([0-9a-f]{32})\.tmp$")
_CORRUPT_INDEX_PATTERN: Final = re.compile(
    r"^\.index\.corrupt_([0-9a-f]{32})\.json$"
)
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CONTROL_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})
_MAX_JSON_SAFE_INTEGER: Final = 9_007_199_254_740_991


class ImageGalleryError(Exception):
    """Base class for stable path-free Gallery failures."""


class ImageGalleryStorageError(ImageGalleryError):
    """Report unavailable, unsafe, corrupt, or inconsistent Gallery storage."""


class ImageGalleryValidationError(ImageGalleryError):
    """Report an invalid artifact ID, result snapshot, or API argument."""


class ImageGalleryLimitError(ImageGalleryError):
    """Report an artifact-count, byte, JSON, or pagination limit breach."""


class ImageGalleryNotFoundError(ImageGalleryError):
    """Report that an opaque Gallery artifact does not exist."""


@dataclass(frozen=True, slots=True)
class ImageGalleryItem:
    """Describe one Gallery artifact without exposing paths or private prompts."""

    artifact_id: str
    created_at: datetime
    width: int
    height: int
    byte_size: int
    sha256: str
    media_type: str
    profile_id: str
    seed: int


@dataclass(frozen=True, slots=True)
class _StoredArtifact:
    """Bind one validated public snapshot to its private storage identity."""

    item: ImageGalleryItem
    storage_id: str
    job_id: str
    profile_digest: str
    workflow_id: str
    workflow_version: int
    workflow_digest: str
    private_prompt: str | None


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows junctions and other reparse-backed redirects."""

    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _windows_volume_identity(path: Path) -> tuple[int, str]:
    """Return the Win32 drive type and filesystem for the containing volume."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_volume_path = kernel32.GetVolumePathNameW
    get_volume_path.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_volume_path.restype = wintypes.BOOL
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [wintypes.LPCWSTR]
    get_drive_type.restype = wintypes.UINT
    get_volume_information = kernel32.GetVolumeInformationW
    get_volume_information.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_volume_information.restype = wintypes.BOOL

    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    volume_path = ctypes.create_unicode_buffer(261)
    if not get_volume_path(str(existing), volume_path, len(volume_path)):
        raise OSError(ctypes.get_last_error(), "volume path unavailable")
    filesystem = ctypes.create_unicode_buffer(64)
    if not get_volume_information(
        volume_path.value,
        None,
        0,
        None,
        None,
        None,
        filesystem,
        len(filesystem),
    ):
        raise OSError(ctypes.get_last_error(), "volume information unavailable")
    return int(get_drive_type(volume_path.value)), filesystem.value


def _require_supported_local_volume(path: Path) -> None:
    """Require fixed local NTFS/ReFS before creating private transaction files.

    Windows directory renames, byte-range locking, reparse metadata, and hard
    link accounting are the durability and containment primitives used by this
    store.  Network, removable, and FAT-like filesystems cannot provide the
    same reviewed contract and are rejected before the Gallery root is changed.
    """

    if os.name != "nt":
        return
    if str(path).startswith("\\\\"):
        raise ImageGalleryStorageError(
            "Gallery storage requires a fixed local NTFS or ReFS volume."
        )
    try:
        drive_type, filesystem = _windows_volume_identity(path)
    except (OSError, ValueError):
        raise ImageGalleryStorageError(
            "Gallery storage volume is unavailable."
        ) from None
    if drive_type != 3 or filesystem.casefold() not in {"ntfs", "refs"}:
        raise ImageGalleryStorageError(
            "Gallery storage requires a fixed local NTFS or ReFS volume."
        )


def _format_timestamp(value: datetime) -> str:
    """Serialize one aware instant in a single canonical UTC representation."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ImageGalleryValidationError(
            "Generated image time must include a timezone."
        )
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    """Parse only the canonical timestamp emitted by this store version."""

    if type(value) is not str or len(value) != 27 or not value.endswith("Z"):
        raise ImageGalleryStorageError("Gallery manifest is invalid.")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise ImageGalleryStorageError("Gallery manifest is invalid.") from None
    if _format_timestamp(parsed) != value:
        raise ImageGalleryStorageError("Gallery manifest is invalid.")
    return parsed


def _reject_json_constant(_value: str) -> object:
    """Reject NaN and infinities, which are not part of interoperable JSON."""

    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON members before a later value can hide tampering."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _decode_json(data: bytes, *, limit: int, label: str) -> object:
    """Decode bounded strict UTF-8 JSON without leaking stored values in errors."""

    if not data or len(data) > limit:
        raise ImageGalleryStorageError(f"Gallery {label} exceeds its size limit.")
    try:
        text = data.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ImageGalleryStorageError(f"Gallery {label} is invalid.") from None


def _encode_json(value: object, *, limit: int, label: str) -> bytes:
    """Encode deterministic bounded JSON for an atomic store commit."""

    try:
        encoded = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ImageGalleryStorageError(f"Gallery {label} is invalid.") from None
    if len(encoded) > limit:
        raise ImageGalleryLimitError(f"Gallery {label} exceeds its size limit.")
    return encoded


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
    """Return a JSON object only when its schema contains exactly known keys."""

    if type(value) is not dict or frozenset(value) != expected:
        raise ImageGalleryStorageError(f"Gallery {label} is invalid.")
    return value


def _require_text(
    value: object,
    pattern: re.Pattern[str],
    *,
    label: str,
) -> str:
    """Validate one persisted bounded identifier against its closed grammar."""

    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ImageGalleryStorageError(f"Gallery {label} is invalid.")
    return value


def _require_integer(
    value: object,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    """Validate a persisted integer without accepting booleans as numbers."""

    if type(value) is not int or not minimum <= value <= maximum:
        raise ImageGalleryStorageError(f"Gallery {label} is invalid.")
    return value


def _validated_private_prompt(value: object, *, persisted: bool) -> str | None:
    """Normalize optional private prompt text under a fixed storage budget."""

    error_type: type[ImageGalleryError]
    error_type = ImageGalleryStorageError if persisted else ImageGalleryValidationError
    if value is None:
        return None
    if type(value) is not str:
        raise error_type("Private Gallery prompt is invalid.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if len(normalized) > MAX_PRIVATE_PROMPT_CODEPOINTS:
        raise ImageGalleryLimitError("Private Gallery prompt exceeds its limit.")
    if any(
        unicodedata.category(character) in _CONTROL_CATEGORIES
        and character not in "\n\t"
        for character in normalized
    ):
        raise error_type("Private Gallery prompt is invalid.")
    return normalized or None


def _manifest_document(artifact: _StoredArtifact) -> dict[str, object]:
    """Build the complete private source-of-truth manifest for one artifact."""

    item = artifact.item
    return {
        "artifact_id": item.artifact_id,
        "created_at": _format_timestamp(item.created_at),
        "generation": {
            "job_id": artifact.job_id,
            "profile_digest": artifact.profile_digest,
            "profile_id": item.profile_id,
            "seed": item.seed,
            "workflow_digest": artifact.workflow_digest,
            "workflow_id": artifact.workflow_id,
            "workflow_version": artifact.workflow_version,
        },
        "image": {
            "byte_size": item.byte_size,
            "file_name": _IMAGE_NAME,
            "height": item.height,
            "media_type": item.media_type,
            "sha256": item.sha256,
            "width": item.width,
        },
        "private_prompt": artifact.private_prompt,
        "schema_version": GALLERY_SCHEMA_VERSION,
        "storage_id": artifact.storage_id,
    }


def _parse_manifest(value: object, *, storage_id: str) -> _StoredArtifact:
    """Parse one exact manifest and bind it to the containing directory name."""

    document = _require_exact_keys(
        value,
        frozenset({
            "artifact_id",
            "created_at",
            "generation",
            "image",
            "private_prompt",
            "schema_version",
            "storage_id",
        }),
        label="manifest",
    )
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != GALLERY_SCHEMA_VERSION
    ):
        raise ImageGalleryStorageError("Gallery manifest version is unsupported.")
    manifest_storage_id = _require_text(
        document["storage_id"], re.compile(r"^[0-9a-f]{32}$"), label="manifest"
    )
    if manifest_storage_id != storage_id:
        raise ImageGalleryStorageError("Gallery manifest is invalid.")
    artifact_id = _require_text(
        document["artifact_id"], _ARTIFACT_ID_PATTERN, label="manifest"
    )
    created_at = _parse_timestamp(document["created_at"])
    image = _require_exact_keys(
        document["image"],
        frozenset({
            "byte_size",
            "file_name",
            "height",
            "media_type",
            "sha256",
            "width",
        }),
        label="manifest",
    )
    if image["file_name"] != _IMAGE_NAME or image["media_type"] != PNG_MEDIA_TYPE:
        raise ImageGalleryStorageError("Gallery manifest is invalid.")
    width = _require_integer(
        image["width"],
        minimum=1,
        maximum=MAX_IMAGE_EDGE_PIXELS,
        label="manifest",
    )
    height = _require_integer(
        image["height"],
        minimum=1,
        maximum=MAX_IMAGE_EDGE_PIXELS,
        label="manifest",
    )
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageGalleryStorageError("Gallery manifest is invalid.")
    byte_size = _require_integer(
        image["byte_size"],
        minimum=1,
        maximum=MAX_GENERATED_PNG_BYTES,
        label="manifest",
    )
    image_digest = _require_text(
        image["sha256"], _SHA256_PATTERN, label="manifest"
    )
    generation = _require_exact_keys(
        document["generation"],
        frozenset({
            "job_id",
            "profile_digest",
            "profile_id",
            "seed",
            "workflow_digest",
            "workflow_id",
            "workflow_version",
        }),
        label="manifest",
    )
    try:
        job_id = str(UUID(str(generation["job_id"])))
    except (ValueError, AttributeError):
        raise ImageGalleryStorageError("Gallery manifest is invalid.") from None
    if generation["job_id"] != job_id:
        raise ImageGalleryStorageError("Gallery manifest is invalid.")
    profile_id = _require_text(
        generation["profile_id"], _LOGICAL_ID_PATTERN, label="manifest"
    )
    profile_digest = _require_text(
        generation["profile_digest"], _SHA256_PATTERN, label="manifest"
    )
    workflow_id = _require_text(
        generation["workflow_id"], _LOGICAL_ID_PATTERN, label="manifest"
    )
    workflow_version = _require_integer(
        generation["workflow_version"],
        minimum=1,
        maximum=_MAX_JSON_SAFE_INTEGER,
        label="manifest",
    )
    workflow_digest = _require_text(
        generation["workflow_digest"], _SHA256_PATTERN, label="manifest"
    )
    seed = _require_integer(
        generation["seed"],
        minimum=0,
        maximum=_MAX_JSON_SAFE_INTEGER,
        label="manifest",
    )
    private_prompt = _validated_private_prompt(
        document["private_prompt"], persisted=True
    )
    return _StoredArtifact(
        item=ImageGalleryItem(
            artifact_id=artifact_id,
            created_at=created_at,
            width=width,
            height=height,
            byte_size=byte_size,
            sha256=image_digest,
            media_type=PNG_MEDIA_TYPE,
            profile_id=profile_id,
            seed=seed,
        ),
        storage_id=storage_id,
        job_id=job_id,
        profile_digest=profile_digest,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        workflow_digest=workflow_digest,
        private_prompt=private_prompt,
    )


class ImageGalleryStore:
    """Persist trusted Backend results in one bounded, single-writer Gallery."""

    def __init__(
        self,
        root: Path,
        *,
        max_items: int = MAX_GALLERY_ITEMS,
        max_total_image_bytes: int = MAX_GALLERY_TOTAL_IMAGE_BYTES,
    ) -> None:
        """Open, lock, recover, and verify the Gallery without exposing paths."""

        if not isinstance(root, Path):
            raise TypeError("root must be a Path.")
        if not root.is_absolute():
            raise ValueError("root must be absolute.")
        if type(max_items) is not int or not 1 <= max_items <= MAX_GALLERY_ITEMS:
            raise ValueError("max_items is outside the supported range.")
        if (
            type(max_total_image_bytes) is not int
            or not 1 <= max_total_image_bytes <= MAX_GALLERY_TOTAL_IMAGE_BYTES
        ):
            raise ValueError(
                "max_total_image_bytes is outside the supported range."
            )
        self._root = root
        self._max_items = max_items
        self._max_total_image_bytes = max_total_image_bytes
        self._lock = RLock()
        self._process_lock_fd: int | None = None
        self._items: dict[str, _StoredArtifact] = {}
        with self._lock:
            self._prepare_root()
            self._validate_startup_inventory()
            self._acquire_process_lock()
            try:
                # The first inventory pass prevents a non-Gallery directory
                # from being changed merely by opening it.  The second pass,
                # after exclusion, closes the check-to-lock race before any
                # recovery entry is removed.
                self._validate_startup_inventory()
                self._recover_startup_entries()
                self._refresh(rebuild_index=True)
            except BaseException:
                self._release_process_lock()
                raise

    def __enter__(self) -> "ImageGalleryStore":
        """Return this open Gallery owner for context-managed use."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Release the cross-process Gallery lease when leaving a context."""

        self.close()

    def close(self) -> None:
        """Idempotently release the cross-process Gallery lease."""

        with self._lock:
            self._release_process_lock()

    def create(
        self,
        result: GeneratedImageResult,
        *,
        private_prompt: str | None = None,
    ) -> ImageGalleryItem:
        """Logically commit one trusted Core result and return path-free metadata."""

        with self._lock:
            self._require_open()
            prompt = _validated_private_prompt(private_prompt, persisted=False)
            stored, png = self._snapshot_result(result, prompt)
            if len(self._items) >= self._max_items:
                raise ImageGalleryLimitError("The local Gallery is full.")
            current_bytes = sum(item.item.byte_size for item in self._items.values())
            if current_bytes + len(png.data) > self._max_total_image_bytes:
                raise ImageGalleryLimitError("The local Gallery byte limit is full.")

            operation_id = uuid4().hex
            staging = self._root / f".staging_{operation_id}"
            destination = self._root / f"image_{stored.storage_id}"
            committed = False
            try:
                staging.mkdir(mode=0o700)
                self._require_safe_directory(staging)
                self._write_new_file(staging / _IMAGE_NAME, png.data)
                manifest = _encode_json(
                    _manifest_document(stored),
                    limit=MAX_GALLERY_MANIFEST_BYTES,
                    label="manifest",
                )
                self._write_new_file(staging / _MANIFEST_NAME, manifest)
                self._sync_directory(staging)
                if destination.exists() or destination.is_symlink():
                    raise ImageGalleryStorageError(
                        "Gallery artifact identity collided."
                    )
                os.replace(staging, destination)
                committed = True
            except ImageGalleryError:
                if not committed:
                    self._discard_partial_staging(staging)
                raise
            except OSError:
                if not committed:
                    self._discard_partial_staging(staging)
                raise ImageGalleryStorageError(
                    "Gallery artifact could not be saved."
                ) from None

            # The directory rename is the authoritative commit.  The index is
            # deliberately repaired after it so cache or directory-metadata
            # maintenance cannot turn a committed artifact into an ambiguous
            # failure that a caller might duplicate by retrying.
            self._items[stored.item.artifact_id] = stored
            self._sync_root_best_effort()
            self._write_index_best_effort()
            return stored.item

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ImageGalleryItem, ...]:
        """Return a bounded newest-first page of path-free Gallery metadata."""

        if type(limit) is not int or not 1 <= limit <= MAX_LIST_LIMIT:
            raise ImageGalleryValidationError("Gallery list limit is invalid.")
        if type(offset) is not int or not 0 <= offset <= _MAX_JSON_SAFE_INTEGER:
            raise ImageGalleryValidationError("Gallery list offset is invalid.")
        with self._lock:
            self._require_open()
            ordered = sorted(
                (artifact.item for artifact in self._items.values()),
                key=lambda item: (item.created_at, item.artifact_id),
                reverse=True,
            )
            return tuple(ordered[offset:offset + limit])

    def get(self, artifact_id: str) -> ImageGalleryItem:
        """Return metadata for one opaque ID or a path-free not-found failure."""

        normalized = self._validate_artifact_id(artifact_id)
        with self._lock:
            self._require_open()
            try:
                return self._items[normalized].item
            except KeyError:
                raise ImageGalleryNotFoundError(
                    "The Gallery artifact was not found."
                ) from None

    def open_verified(self, artifact_id: str) -> CanonicalPng:
        """Return canonical PNG bytes after rechecking file and manifest seals."""

        normalized = self._validate_artifact_id(artifact_id)
        with self._lock:
            self._require_open()
            try:
                artifact = self._items[normalized]
            except KeyError:
                raise ImageGalleryNotFoundError(
                    "The Gallery artifact was not found."
                ) from None
            loaded, png = self._load_artifact(artifact.storage_id)
            if loaded != artifact:
                raise ImageGalleryStorageError(
                    "Gallery artifact changed after startup."
                )
            return png

    def delete(self, artifact_id: str) -> None:
        """Commit one deletion by rename and defer non-authoritative cleanup."""

        normalized = self._validate_artifact_id(artifact_id)
        with self._lock:
            self._require_open()
            try:
                artifact = self._items[normalized]
            except KeyError:
                raise ImageGalleryNotFoundError(
                    "The Gallery artifact was not found."
                ) from None
            loaded = self._load_artifact_metadata(artifact.storage_id)
            if loaded != artifact:
                raise ImageGalleryStorageError(
                    "Gallery artifact changed after startup."
                )
            source = self._root / f"image_{artifact.storage_id}"
            tombstone = self._root / f".deleting_{artifact.storage_id}"
            try:
                if tombstone.exists() or tombstone.is_symlink():
                    raise ImageGalleryStorageError(
                        "Gallery deletion state is inconsistent."
                    )
                os.replace(source, tombstone)
            except ImageGalleryError:
                raise
            except OSError:
                raise ImageGalleryStorageError(
                    "Gallery artifact could not be deleted."
                ) from None
            # The rename is the deletion commit.  Everything below is
            # recoverable maintenance and therefore cannot report deletion as
            # failed after the artifact has already become unreachable.
            del self._items[normalized]
            self._sync_root_best_effort()
            self._write_index_best_effort()
            self._purge_tombstone_best_effort(tombstone)

    @staticmethod
    def _validate_artifact_id(value: object) -> str:
        """Accept only opaque IDs and never interpret caller text as a path."""

        if type(value) is not str or _ARTIFACT_ID_PATTERN.fullmatch(value) is None:
            raise ImageGalleryValidationError("Gallery artifact ID is invalid.")
        return value

    def _snapshot_result(
        self,
        result: GeneratedImageResult,
        private_prompt: str | None,
    ) -> tuple[_StoredArtifact, CanonicalPng]:
        """Validate the shape and bytes of one trusted internal Core result."""

        if type(result) is not GeneratedImageResult:
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            )
        if type(result.image) is not CanonicalPng:
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            )
        try:
            canonical = canonicalize_png(
                result.image.data,
                expected_width=result.image.width,
                expected_height=result.image.height,
            )
        except Exception:
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            ) from None
        if canonical != result.image:
            raise ImageGalleryValidationError(
                "Generated image result is inconsistent."
            )
        try:
            job_id = str(UUID(result.job_id))
        except (ValueError, AttributeError):
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            ) from None
        if result.job_id != job_id:
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            )
        if (
            type(result.profile_id) is not str
            or _LOGICAL_ID_PATTERN.fullmatch(result.profile_id) is None
            or type(result.workflow_id) is not str
            or _LOGICAL_ID_PATTERN.fullmatch(result.workflow_id) is None
            or type(result.profile_digest) is not str
            or _SHA256_PATTERN.fullmatch(result.profile_digest) is None
            or type(result.workflow_digest) is not str
            or _SHA256_PATTERN.fullmatch(result.workflow_digest) is None
            or type(result.workflow_version) is not int
            or not 1 <= result.workflow_version <= _MAX_JSON_SAFE_INTEGER
            or type(result.seed) is not int
            or not 0 <= result.seed <= _MAX_JSON_SAFE_INTEGER
        ):
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            )
        if not isinstance(result.created_at, datetime):
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            )
        try:
            created_at_text = _format_timestamp(result.created_at)
            created_at = _parse_timestamp(created_at_text)
        except ImageGalleryStorageError:
            raise ImageGalleryValidationError(
                "Generated image result is invalid."
            ) from None
        artifact_id = f"gallery_{uuid4().hex}"
        storage_id = uuid4().hex
        return (
            _StoredArtifact(
                item=ImageGalleryItem(
                    artifact_id=artifact_id,
                    created_at=created_at,
                    width=canonical.width,
                    height=canonical.height,
                    byte_size=len(canonical.data),
                    sha256=canonical.sha256,
                    media_type=canonical.media_type,
                    profile_id=result.profile_id,
                    seed=result.seed,
                ),
                storage_id=storage_id,
                job_id=job_id,
                profile_digest=result.profile_digest,
                workflow_id=result.workflow_id,
                workflow_version=result.workflow_version,
                workflow_digest=result.workflow_digest,
                private_prompt=private_prompt,
            ),
            canonical,
        )

    def _prepare_root(self) -> None:
        """Create and mark only an empty, redirect-free Gallery root."""

        _require_supported_local_volume(self._root)
        try:
            self._require_safe_directory_chain(self._root.parent)
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._require_safe_directory_chain(self._root)
        except ImageGalleryError:
            raise
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery storage is unavailable."
            ) from None
        entries = self._inventory_root_entries()
        names = {entry.name for entry in entries}
        if not entries:
            try:
                self._write_new_file(
                    self._root / _OWNER_MARKER_NAME,
                    _OWNER_MARKER_BYTES,
                )
                self._sync_directory(self._root)
            except ImageGalleryError:
                raise
            except OSError:
                raise ImageGalleryStorageError(
                    "Gallery ownership marker could not be created."
                ) from None
            return
        if _OWNER_MARKER_NAME not in names:
            # A nonempty unmarked directory may belong to another feature or
            # user.  Refuse it before creating even the disposable lock file.
            raise ImageGalleryStorageError(
                "Gallery storage is not an owned Gallery directory."
            )

    def _inventory_root_entries(self) -> List[Path]:
        """Collect one bounded root snapshot without changing any entry."""

        entries: List[Path] = []
        try:
            with os.scandir(self._root) as iterator:
                for position, raw_entry in enumerate(iterator, start=1):
                    if position > MAX_ROOT_ENTRIES:
                        raise ImageGalleryLimitError(
                            "Gallery contains too many filesystem entries."
                        )
                    entries.append(Path(raw_entry.path))
        except ImageGalleryError:
            raise
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery storage could not be inspected."
            ) from None
        return entries

    def _require_owner_marker(self, path: Path) -> None:
        """Verify the exact private-store ownership marker and its identity."""

        data = self._read_bounded_file(
            path,
            maximum_bytes=len(_OWNER_MARKER_BYTES),
        )
        if data != _OWNER_MARKER_BYTES:
            raise ImageGalleryStorageError(
                "Gallery ownership marker is invalid."
            )

    def _validate_startup_inventory(self) -> None:
        """Validate every startup entry without mutating the directory.

        This method intentionally runs both before and after process-lock
        acquisition.  Recovery starts only after the second complete pass, so
        an unknown sibling or malformed transaction can never be partially
        deleted merely because it appeared late in directory iteration.
        """

        backups = 0
        saw_marker = False
        for entry in self._inventory_root_entries():
            name = entry.name
            if name == _OWNER_MARKER_NAME:
                self._require_owner_marker(entry)
                saw_marker = True
                continue
            if name == _LOCK_NAME:
                self._require_safe_regular_file(entry, maximum_bytes=1)
                continue
            if name == _INDEX_NAME:
                self._require_safe_regular_file(
                    entry, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                )
                continue
            if _CORRUPT_INDEX_PATTERN.fullmatch(name) is not None:
                self._require_safe_regular_file(
                    entry, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                )
                backups += 1
                if backups > MAX_CORRUPT_INDEX_BACKUPS:
                    raise ImageGalleryLimitError(
                        "Gallery contains too many corrupt-index backups."
                    )
                continue
            artifact_match = _ARTIFACT_DIRECTORY_PATTERN.fullmatch(name)
            if artifact_match is not None:
                self._require_safe_directory(entry)
                self._validated_artifact_children(entry, allow_partial=False)
                continue
            if _STAGING_PATTERN.fullmatch(name) is not None:
                self._require_safe_directory(entry)
                self._validated_artifact_children(entry, allow_partial=True)
                continue
            if _TOMBSTONE_PATTERN.fullmatch(name) is not None:
                self._require_safe_directory(entry)
                self._validated_artifact_children(entry, allow_partial=True)
                continue
            if _INDEX_TEMP_PATTERN.fullmatch(name) is not None:
                self._require_safe_regular_file(
                    entry, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                )
                continue
            raise ImageGalleryStorageError(
                "Gallery contains an unknown filesystem entry."
            )
        if not saw_marker:
            raise ImageGalleryStorageError(
                "Gallery ownership marker is missing."
            )

    def _refresh(self, *, rebuild_index: bool) -> None:
        """Load authoritative manifests once and opportunistically repair index."""

        self._require_open()
        discovered, corrupt_backups = self._scan_root()
        index_state = self._read_index_state(discovered)
        self._items = discovered
        if rebuild_index and index_state != "current":
            if index_state == "corrupt":
                try:
                    self._preserve_corrupt_index(corrupt_backups)
                except (ImageGalleryError, OSError):
                    # Never overwrite a corrupt cache unless its backup rename
                    # completed.  Artifacts remain readable from the already
                    # validated in-memory authoritative snapshot.
                    return
            try:
                self._write_index_required()
            except (ImageGalleryError, OSError):
                # Index durability is never an admission condition for an
                # otherwise fully verified Gallery.
                return

    def _scan_root(self) -> tuple[dict[str, _StoredArtifact], List[Path]]:
        """Validate every root entry and load each authoritative artifact."""

        self._require_safe_directory(self._root)
        discovered: dict[str, _StoredArtifact] = {}
        corrupt_backups: List[Path] = []
        total_bytes = 0
        try:
            with os.scandir(self._root) as iterator:
                for position, raw_entry in enumerate(iterator, start=1):
                    if position > MAX_ROOT_ENTRIES:
                        raise ImageGalleryLimitError(
                            "Gallery contains too many filesystem entries."
                        )
                    entry = Path(raw_entry.path)
                    name = raw_entry.name
                    if name == _OWNER_MARKER_NAME:
                        self._require_owner_marker(entry)
                        continue
                    if name == _LOCK_NAME:
                        self._require_safe_regular_file(entry, maximum_bytes=1)
                        continue
                    if name == _INDEX_NAME:
                        self._require_safe_regular_file(
                            entry, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                        )
                        continue
                    if _CORRUPT_INDEX_PATTERN.fullmatch(name) is not None:
                        self._require_safe_regular_file(
                            entry, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                        )
                        corrupt_backups.append(entry)
                        continue
                    artifact_match = _ARTIFACT_DIRECTORY_PATTERN.fullmatch(name)
                    if artifact_match is not None:
                        self._require_safe_directory(entry)
                        artifact = self._load_artifact_metadata(
                            artifact_match.group(1)
                        )
                        if artifact.item.artifact_id in discovered:
                            raise ImageGalleryStorageError(
                                "Gallery contains a duplicate artifact identity."
                            )
                        discovered[artifact.item.artifact_id] = artifact
                        total_bytes += artifact.item.byte_size
                        if len(discovered) > self._max_items:
                            raise ImageGalleryLimitError(
                                "Gallery contains too many artifacts."
                            )
                        if total_bytes > self._max_total_image_bytes:
                            raise ImageGalleryLimitError(
                                "Gallery exceeds its total byte limit."
                            )
                        continue
                    # Startup recovery removes these names. Encountering one
                    # during an ordinary operation means another actor changed
                    # the locked root, so fail closed instead of guessing.
                    if (
                        _STAGING_PATTERN.fullmatch(name) is not None
                        or _TOMBSTONE_PATTERN.fullmatch(name) is not None
                        or _INDEX_TEMP_PATTERN.fullmatch(name) is not None
                    ):
                        raise ImageGalleryStorageError(
                            "Gallery contains unrecovered transaction state."
                        )
                    raise ImageGalleryStorageError(
                        "Gallery contains an unknown filesystem entry."
                    )
        except ImageGalleryError:
            raise
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery storage could not be inspected."
            ) from None
        if len(corrupt_backups) > MAX_CORRUPT_INDEX_BACKUPS:
            raise ImageGalleryLimitError(
                "Gallery contains too many corrupt-index backups."
            )
        return discovered, corrupt_backups

    def _load_artifact_metadata(self, storage_id: str) -> _StoredArtifact:
        """Load strict metadata and file identity without reading PNG contents."""

        root = self._root / f"image_{storage_id}"
        self._require_safe_directory(root)
        entries = self._validated_artifact_children(root, allow_partial=False)
        manifest_bytes = self._read_bounded_file(
            entries[_MANIFEST_NAME], maximum_bytes=MAX_GALLERY_MANIFEST_BYTES
        )
        artifact = _parse_manifest(
            _decode_json(
                manifest_bytes,
                limit=MAX_GALLERY_MANIFEST_BYTES,
                label="manifest",
            ),
            storage_id=storage_id,
        )
        image_info = self._require_safe_regular_file(
            entries[_IMAGE_NAME], maximum_bytes=MAX_GENERATED_PNG_BYTES
        )
        if image_info.st_size != artifact.item.byte_size:
            raise ImageGalleryStorageError("Gallery image seal is invalid.")
        return artifact

    def _load_artifact(
        self,
        storage_id: str,
    ) -> tuple[_StoredArtifact, CanonicalPng]:
        """Read one selected PNG and verify every manifest byte-level seal."""

        artifact = self._load_artifact_metadata(storage_id)
        image_path = self._root / f"image_{storage_id}" / _IMAGE_NAME
        image_bytes = self._read_bounded_file(
            image_path, maximum_bytes=MAX_GENERATED_PNG_BYTES
        )
        if len(image_bytes) != artifact.item.byte_size:
            raise ImageGalleryStorageError("Gallery image seal is invalid.")
        if not hashlib.sha256(image_bytes).hexdigest() == artifact.item.sha256:
            raise ImageGalleryStorageError("Gallery image seal is invalid.")
        try:
            png = canonicalize_png(
                image_bytes,
                expected_width=artifact.item.width,
                expected_height=artifact.item.height,
            )
        except Exception:
            raise ImageGalleryStorageError(
                "Gallery image bytes are invalid."
            ) from None
        if (
            png.data != image_bytes
            or png.sha256 != artifact.item.sha256
            or png.media_type != artifact.item.media_type
        ):
            raise ImageGalleryStorageError("Gallery image seal is invalid.")
        return artifact, png

    def _validated_artifact_children(
        self,
        root: Path,
        *,
        allow_partial: bool,
    ) -> dict[str, Path]:
        """Accept only regular, singly-linked canonical files in an artifact."""

        expected = {_IMAGE_NAME, _MANIFEST_NAME}
        found: dict[str, Path] = {}
        try:
            with os.scandir(root) as iterator:
                for position, raw_entry in enumerate(iterator, start=1):
                    if position > len(expected):
                        raise ImageGalleryStorageError(
                            "Gallery artifact contains unknown entries."
                        )
                    if raw_entry.name not in expected:
                        raise ImageGalleryStorageError(
                            "Gallery artifact contains unknown entries."
                        )
                    child = Path(raw_entry.path)
                    maximum = (
                        MAX_GENERATED_PNG_BYTES
                        if raw_entry.name == _IMAGE_NAME
                        else MAX_GALLERY_MANIFEST_BYTES
                    )
                    self._require_safe_regular_file(child, maximum_bytes=maximum)
                    found[raw_entry.name] = child
        except ImageGalleryError:
            raise
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery artifact could not be inspected."
            ) from None
        if not allow_partial and set(found) != expected:
            raise ImageGalleryStorageError("Gallery artifact is incomplete.")
        return found

    def _recover_startup_entries(self) -> None:
        """Resolve only transaction names from this exact store grammar.

        Staging directories never committed and are discarded.  Deletion
        tombstones already crossed the authoritative rename boundary and are
        purged.  Unknown hidden names, malformed transaction contents, and
        redirected entries remain untouched and make startup fail closed.
        """

        self._require_open()
        try:
            with os.scandir(self._root) as iterator:
                entries = []
                for position, raw_entry in enumerate(iterator, start=1):
                    if position > MAX_ROOT_ENTRIES:
                        raise ImageGalleryLimitError(
                            "Gallery contains too many filesystem entries."
                        )
                    entries.append(Path(raw_entry.path))
            for entry in entries:
                name = entry.name
                if _STAGING_PATTERN.fullmatch(name) is not None:
                    self._purge_artifact_directory(entry, allow_partial=True)
                    continue
                if _TOMBSTONE_PATTERN.fullmatch(name) is not None:
                    # A crash may occur between either child unlink.  The
                    # directory rename already committed deletion, so every
                    # safe canonical-file subset converges to absence.
                    self._purge_artifact_directory(entry, allow_partial=True)
                    continue
                if _INDEX_TEMP_PATTERN.fullmatch(name) is not None:
                    self._require_safe_regular_file(
                        entry, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                    )
                    entry.unlink()
        except ImageGalleryError:
            raise
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery transaction recovery failed."
            ) from None
        self._sync_root_best_effort()

    def _purge_artifact_directory(
        self,
        root: Path,
        *,
        allow_partial: bool,
    ) -> None:
        """Delete one validated flat transaction directory without traversal."""

        self._require_safe_directory(root)
        children = self._validated_artifact_children(
            root, allow_partial=allow_partial
        )
        try:
            for child in children.values():
                child.unlink()
            root.rmdir()
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery transaction cleanup failed."
            ) from None

    def _purge_tombstone_best_effort(self, root: Path) -> None:
        """Defer safe tombstone garbage collection without undoing deletion."""

        try:
            self._purge_artifact_directory(root, allow_partial=True)
        except (ImageGalleryError, OSError):
            return
        self._sync_root_best_effort()

    def _sync_root_best_effort(self) -> None:
        """Attempt durability maintenance after an authoritative rename commit."""

        try:
            self._sync_directory(self._root)
        except (ImageGalleryError, OSError):
            return

    def _discard_partial_staging(self, root: Path) -> None:
        """Best-effort rollback only for the exact staging directory just minted."""

        try:
            root_info = root.lstat()
        except FileNotFoundError:
            return
        except OSError:
            return
        if (
            stat.S_ISLNK(root_info.st_mode)
            or _is_reparse(root_info)
            or not stat.S_ISDIR(root_info.st_mode)
            or _STAGING_PATTERN.fullmatch(root.name) is None
        ):
            return
        try:
            self._purge_artifact_directory(root, allow_partial=True)
        except ImageGalleryError:
            # The caller still receives the original failure.  A residue with
            # the closed staging grammar is safely recoverable on next open.
            return

    def _read_index_state(
        self,
        discovered: dict[str, _StoredArtifact],
    ) -> str:
        """Classify the disposable index as missing, current, stale, or corrupt."""

        path = self._root / _INDEX_NAME
        try:
            path.lstat()
        except FileNotFoundError:
            return "missing"
        except OSError:
            raise ImageGalleryStorageError("Gallery index is unavailable.") from None
        self._require_safe_regular_file(path, maximum_bytes=MAX_GALLERY_INDEX_BYTES)
        try:
            document = _decode_json(
                self._read_bounded_file(
                    path, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                ),
                limit=MAX_GALLERY_INDEX_BYTES,
                label="index",
            )
            indexed = self._parse_index(document)
        except ImageGalleryStorageError:
            return "corrupt"
        expected = {
            artifact.item.artifact_id: (
                artifact.storage_id,
                artifact.item.sha256,
                _format_timestamp(artifact.item.created_at),
            )
            for artifact in discovered.values()
        }
        return "current" if indexed == expected else "stale"

    @staticmethod
    def _parse_index(
        value: object,
    ) -> dict[str, tuple[str, str, str]]:
        """Parse the strict rebuildable index without trusting it for reads."""

        document = _require_exact_keys(
            value,
            frozenset({"items", "schema_version"}),
            label="index",
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != GALLERY_SCHEMA_VERSION
        ):
            raise ImageGalleryStorageError("Gallery index version is unsupported.")
        raw_items = document["items"]
        if type(raw_items) is not list or len(raw_items) > MAX_GALLERY_ITEMS:
            raise ImageGalleryStorageError("Gallery index is invalid.")
        parsed: dict[str, tuple[str, str, str]] = {}
        storage_pattern = re.compile(r"^[0-9a-f]{32}$")
        for raw_item in raw_items:
            item = _require_exact_keys(
                raw_item,
                frozenset({
                    "artifact_id",
                    "created_at",
                    "image_sha256",
                    "storage_id",
                }),
                label="index",
            )
            artifact_id = _require_text(
                item["artifact_id"], _ARTIFACT_ID_PATTERN, label="index"
            )
            if artifact_id in parsed:
                raise ImageGalleryStorageError("Gallery index is invalid.")
            storage_id = _require_text(
                item["storage_id"], storage_pattern, label="index"
            )
            digest = _require_text(
                item["image_sha256"], _SHA256_PATTERN, label="index"
            )
            created_at = _parse_timestamp(item["created_at"])
            parsed[artifact_id] = (
                storage_id,
                digest,
                _format_timestamp(created_at),
            )
        return parsed

    def _index_document(self) -> dict[str, object]:
        """Build a prompt-free path-free cache from authoritative snapshots."""

        ordered = sorted(
            self._items.values(),
            key=lambda artifact: (
                artifact.item.created_at,
                artifact.item.artifact_id,
            ),
            reverse=True,
        )
        return {
            "items": [
                {
                    "artifact_id": artifact.item.artifact_id,
                    "created_at": _format_timestamp(artifact.item.created_at),
                    "image_sha256": artifact.item.sha256,
                    "storage_id": artifact.storage_id,
                }
                for artifact in ordered
            ],
            "schema_version": GALLERY_SCHEMA_VERSION,
        }

    def _write_index_required(self) -> None:
        """Atomically replace the rebuildable index under its fixed byte budget."""

        data = _encode_json(
            self._index_document(),
            limit=MAX_GALLERY_INDEX_BYTES,
            label="index",
        )
        temporary = self._root / f".index_{uuid4().hex}.tmp"
        try:
            self._write_new_file(temporary, data)
            os.replace(temporary, self._root / _INDEX_NAME)
            self._sync_directory(self._root)
        except ImageGalleryError:
            self._unlink_safe_temporary(temporary)
            raise
        except OSError:
            self._unlink_safe_temporary(temporary)
            raise ImageGalleryStorageError(
                "Gallery index could not be rebuilt."
            ) from None

    def _write_index_best_effort(self) -> None:
        """Keep a committed artifact authoritative if its cache refresh fails."""

        try:
            self._write_index_required()
        except (ImageGalleryError, OSError):
            # The artifact directory is the transaction record.  Removing a
            # stale index forces a deterministic rebuild on the next operation.
            index = self._root / _INDEX_NAME
            try:
                if index.exists() or index.is_symlink():
                    self._require_safe_regular_file(
                        index, maximum_bytes=MAX_GALLERY_INDEX_BYTES
                    )
                    index.unlink()
            except (OSError, ImageGalleryError):
                pass

    def _preserve_corrupt_index(self, backups: List[Path]) -> None:
        """Rename an unreadable index to a bounded forensic backup."""

        index = self._root / _INDEX_NAME
        if len(backups) >= MAX_CORRUPT_INDEX_BACKUPS:
            try:
                oldest = min(backups, key=lambda path: path.stat().st_mtime_ns)
            except OSError:
                raise ImageGalleryStorageError(
                    "Gallery corrupt-index backups are unavailable."
                ) from None
            self._require_safe_regular_file(
                oldest, maximum_bytes=MAX_GALLERY_INDEX_BYTES
            )
            try:
                oldest.unlink()
            except OSError:
                raise ImageGalleryStorageError(
                    "Gallery corrupt-index backup could not be rotated."
                ) from None
        backup = self._root / f".index.corrupt_{uuid4().hex}.json"
        try:
            os.replace(index, backup)
            self._sync_directory(self._root)
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery corrupt index could not be preserved."
            ) from None

    @staticmethod
    def _write_new_file(path: Path, data: bytes) -> None:
        """Create, flush, and close one new singly-owned regular file."""

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(path, flags, 0o600)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _is_reparse(opened)
                or opened.st_nlink != 1
            ):
                raise ImageGalleryStorageError(
                    "Gallery file destination is unsafe."
                )
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short Gallery write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _read_bounded_file(self, path: Path, *, maximum_bytes: int) -> bytes:
        """Read a pinned ordinary file and reject identity changes during I/O."""

        before = self._require_safe_regular_file(
            path, maximum_bytes=maximum_bytes
        )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or not stat.S_ISREG(opened.st_mode)
                or _is_reparse(opened)
                or opened.st_nlink != 1
                or opened.st_size > maximum_bytes
            ):
                raise ImageGalleryStorageError("Gallery file is unsafe.")
            chunks: list[bytes] = []
            remaining = maximum_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1_048_576, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
            linked_after = path.lstat()
        except ImageGalleryError:
            raise
        except OSError:
            raise ImageGalleryStorageError("Gallery file is unavailable.") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            len(data) > maximum_bytes
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != len(data)
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or linked_after.st_dev != opened.st_dev
            or linked_after.st_ino != opened.st_ino
            or stat.S_ISLNK(linked_after.st_mode)
            or _is_reparse(linked_after)
        ):
            raise ImageGalleryStorageError("Gallery file changed while reading.")
        return data

    @staticmethod
    def _require_safe_regular_file(
        path: Path,
        *,
        maximum_bytes: int,
    ) -> os.stat_result:
        """Require a bounded singly-linked regular file without redirection."""

        try:
            info = path.lstat()
        except OSError:
            raise ImageGalleryStorageError("Gallery file is unavailable.") from None
        if (
            stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not 0 <= info.st_size <= maximum_bytes
        ):
            raise ImageGalleryStorageError("Gallery file is unsafe.")
        return info

    @staticmethod
    def _require_safe_directory(path: Path) -> None:
        """Require one ordinary directory rather than a filesystem redirect."""

        try:
            info = path.lstat()
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery directory is unavailable."
            ) from None
        if (
            stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
            or not stat.S_ISDIR(info.st_mode)
        ):
            raise ImageGalleryStorageError("Gallery directory is unsafe.")

    @classmethod
    def _require_safe_directory_chain(cls, path: Path) -> None:
        """Reject every existing redirected or non-directory path component."""

        candidates = (path, *path.parents)
        for candidate in reversed(candidates):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise ImageGalleryStorageError(
                    "Gallery directory is unavailable."
                ) from None
            if (
                stat.S_ISLNK(info.st_mode)
                or _is_reparse(info)
                or not stat.S_ISDIR(info.st_mode)
            ):
                raise ImageGalleryStorageError("Gallery directory is unsafe.")

    def _acquire_process_lock(self) -> None:
        """Acquire and identity-pin a nonblocking cross-process writer lease."""

        path = self._root / _LOCK_NAME
        descriptor = -1
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            if path.exists() or path.is_symlink():
                self._require_safe_regular_file(path, maximum_bytes=1)
            descriptor = os.open(path, flags, 0o600)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _is_reparse(opened)
                or opened.st_nlink != 1
            ):
                raise ImageGalleryStorageError("Gallery lock is unsafe.")
            linked = self._require_safe_regular_file(path, maximum_bytes=1)
            if linked.st_dev != opened.st_dev or linked.st_ino != opened.st_ino:
                raise ImageGalleryStorageError("Gallery lock changed while opening.")
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            elif opened.st_size != 1:
                raise ImageGalleryStorageError("Gallery lock is unsafe.")
            os.lseek(descriptor, 0, os.SEEK_SET)
            self._lock_process_descriptor(descriptor)
        except ImageGalleryError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise ImageGalleryStorageError(
                "Gallery is already open in another Backend."
            ) from None
        self._process_lock_fd = descriptor

    def _require_open(self) -> None:
        """Verify that the live lease descriptor still names the lock path."""

        descriptor = self._process_lock_fd
        if descriptor is None:
            raise ImageGalleryStorageError("Gallery storage is not open.")
        try:
            opened = os.fstat(descriptor)
            linked = self._require_safe_regular_file(
                self._root / _LOCK_NAME, maximum_bytes=1
            )
        except OSError:
            raise ImageGalleryStorageError("Gallery lock is unavailable.") from None
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or opened.st_nlink != 1
            or linked.st_dev != opened.st_dev
            or linked.st_ino != opened.st_ino
        ):
            raise ImageGalleryStorageError("Gallery lock changed while in use.")

    @staticmethod
    def _lock_process_descriptor(descriptor: int) -> None:
        """Lock the sentinel byte with the platform's native file-lock API."""

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
        """Release the platform-native sentinel-byte lease."""

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

    def _release_process_lock(self) -> None:
        """Release and close the lease descriptor without deleting its inode."""

        descriptor = self._process_lock_fd
        if descriptor is None:
            return
        self._process_lock_fd = None
        release_failed = False
        try:
            self._unlock_process_descriptor(descriptor)
        except OSError:
            release_failed = True
        try:
            os.close(descriptor)
        except OSError:
            release_failed = True
        if release_failed:
            # Removing the descriptor from live state before teardown prevents
            # later operations from relying on a lease with uncertain status.
            raise ImageGalleryStorageError(
                "Gallery lock could not be released."
            ) from None

    @staticmethod
    def _sync_directory(path: Path) -> None:
        """Flush directory metadata where the platform exposes directory fsync."""

        if os.name == "nt":
            return
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise ImageGalleryStorageError(
                "Gallery directory metadata could not be synchronized."
            ) from None

    def _unlink_safe_temporary(self, path: Path) -> None:
        """Best-effort remove only an exact index temporary file."""

        if _INDEX_TEMP_PATTERN.fullmatch(path.name) is None:
            return
        try:
            self._require_safe_regular_file(
                path, maximum_bytes=MAX_GALLERY_INDEX_BYTES
            )
            path.unlink()
        except (OSError, ImageGalleryError):
            return


__all__ = [
    "GALLERY_SCHEMA_VERSION",
    "ImageGalleryError",
    "ImageGalleryItem",
    "ImageGalleryLimitError",
    "ImageGalleryNotFoundError",
    "ImageGalleryStorageError",
    "ImageGalleryStore",
    "ImageGalleryValidationError",
    "MAX_GALLERY_INDEX_BYTES",
    "MAX_GALLERY_ITEMS",
    "MAX_GALLERY_MANIFEST_BYTES",
    "MAX_GALLERY_TOTAL_IMAGE_BYTES",
    "MAX_LIST_LIMIT",
    "MAX_PRIVATE_PROMPT_CODEPOINTS",
]
