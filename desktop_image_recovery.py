"""Quarantine explicitly confirmed crash-left image runs without deleting bytes.

Only ordinary UUIDv4 directories beneath the fixed private run root qualify.
Preview is read-only. Apply repeats its metadata snapshot under the Gallery's
existing cross-process lease, then performs individual no-overwrite directory
renames. A partial failure deliberately keeps completed moves and remaining
sources; a fresh preview can retry without guessing which copy is authoritative.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Iterator
from uuid import UUID

from image_generation.gallery import ImageGalleryStore, _LOCK_NAME, _OWNER_MARKER_BYTES, _OWNER_MARKER_NAME


_MAX_RUNS = 32
_MAX_ENTRIES = 100_000
_MAX_DEPTH = 64
_MAX_INTEGER = 9_007_199_254_740_991
_SNAPSHOT = re.compile(r"[0-9a-f]{64}\Z")
_FAILURE = "Image recovery could not be completed safely. All run directories remain preserved; review and retry."


class DesktopImageRecoveryError(RuntimeError):
    """Report a stable recovery refusal without paths, filenames, or native errors."""


@dataclass(frozen=True, slots=True)
class ImageRecoveryPreview:
    """Carry bounded counts and a private Main-only metadata comparison token."""

    snapshot: str
    run_count: int
    file_count: int
    byte_size: int

    def to_private_dict(self) -> dict[str, object]:
        """Return the closed worker receipt; the snapshot must never reach Renderer."""

        return {"kind": "image.recovery.preview", "snapshot": self.snapshot,
                "runCount": self.run_count, "fileCount": self.file_count, "byteSize": self.byte_size}


@dataclass(frozen=True, slots=True)
class _Run:
    name: str
    records: tuple[tuple[object, ...], ...]
    files: int
    bytes: int


@dataclass(frozen=True, slots=True)
class _Inventory:
    preview: ImageRecoveryPreview
    source: Path
    destination: Path
    runs: tuple[_Run, ...]


def _safe(metadata: os.stat_result, *, directory: bool) -> None:
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))):
        raise DesktopImageRecoveryError(_FAILURE)


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_nlink


def _directory(path: Path, *, missing: bool = False) -> os.stat_result | None:
    for component in (*reversed(path.parents), path):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            if missing:
                return None
            raise
        _safe(metadata, directory=True)
    return metadata


def _roots(data_root: Path) -> tuple[Path, Path, Path]:
    if (not isinstance(data_root, Path) or not data_root.is_absolute()
            or ".." in data_root.parts or "\x00" in str(data_root)):
        raise DesktopImageRecoveryError(_FAILURE)
    _directory(data_root, missing=True)
    workspace = data_root / "workspace"
    _directory(workspace, missing=True)
    return workspace / "image-runs", workspace / "image-recovery", workspace / "gallery"


def _uuid(name: str) -> None:
    try:
        parsed = UUID(name)
    except (ValueError, AttributeError):
        raise DesktopImageRecoveryError(_FAILURE) from None
    if parsed.version != 4 or str(parsed) != name:
        raise DesktopImageRecoveryError(_FAILURE)


def _children(path: Path, *, limit: int) -> tuple[Path, ...]:
    entries: list[Path] = []
    with os.scandir(path) as stream:
        for entry in stream:
            if len(entries) >= limit:
                raise DesktopImageRecoveryError(_FAILURE)
            entries.append(path / entry.name)
    return tuple(sorted(entries, key=lambda entry: entry.name))


def _tree(root: Path, *, budget: int) -> _Run:
    """Build deterministic metadata records with a bounded iterative DFS.

    No payload file is opened: metadata is enough for the confirmation CAS,
    and leaving hard-linked contents untouched preserves their external owners.
    """

    records: list[tuple[object, ...]] = []
    files = 0
    byte_size = 0
    pending = [(root, 0)]
    while pending:
        path, depth = pending.pop()
        if depth > _MAX_DEPTH or len(records) >= budget:
            raise DesktopImageRecoveryError(_FAILURE)
        metadata = path.lstat()
        directory = stat.S_ISDIR(metadata.st_mode)
        _safe(metadata, directory=directory)
        relative = path.relative_to(root).as_posix()
        if len(relative) > 4096:
            raise DesktopImageRecoveryError(_FAILURE)
        records.append((relative, "directory" if directory else "file", *_signature(metadata)))
        if directory:
            for child in reversed(_children(path, limit=budget - len(records) + 1)):
                pending.append((child, depth + 1))
            if len(pending) + len(records) > budget:
                raise DesktopImageRecoveryError(_FAILURE)
        else:
            # Hard links are preserved by renaming their containing directory;
            # unlike deletion, quarantine never follows or edits their contents.
            files += 1
            byte_size += metadata.st_size
            if not 0 <= byte_size <= _MAX_INTEGER:
                raise DesktopImageRecoveryError(_FAILURE)
    return _Run(root.name, tuple(records), files, byte_size)


def _inventory(data_root: Path) -> _Inventory:
    """Bind the confirmation to source metadata and existing quarantine names."""

    source, destination, _ = _roots(data_root)
    source_metadata = _directory(source, missing=True)
    destination_metadata = _directory(destination, missing=True)
    previous: list[tuple[object, ...]] = []
    if destination_metadata is not None:
        for item in _children(destination, limit=_MAX_ENTRIES):
            _uuid(item.name)
            metadata = item.lstat()
            _safe(metadata, directory=True)
            previous.append((item.name, *_signature(metadata)))
    runs: list[_Run] = []
    entries = 0
    if source_metadata is not None:
        for item in _children(source, limit=_MAX_RUNS):
            _uuid(item.name)
            _safe(item.lstat(), directory=True)
            if any(record[0] == item.name for record in previous):
                raise DesktopImageRecoveryError(_FAILURE)
            run = _tree(item, budget=_MAX_ENTRIES - entries)
            entries += len(run.records)
            runs.append(run)
    byte_size = sum(run.bytes for run in runs)
    if byte_size > _MAX_INTEGER:
        raise DesktopImageRecoveryError(_FAILURE)
    document = {"source": _signature(source_metadata) if source_metadata else None,
                "destination": _signature(destination_metadata) if destination_metadata else None,
                "previous": previous, "runs": [(run.name, run.records) for run in runs]}
    token = hashlib.sha256(json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
    preview = ImageRecoveryPreview(token, len(runs), sum(run.files for run in runs), byte_size)
    return _Inventory(preview, source, destination, tuple(runs))


def image_recovery_required(data_root: Path) -> bool:
    """Detect any fixed scratch-root residue without adopting or opening its children."""

    try:
        source, _, _ = _roots(data_root)
        if _directory(source, missing=True) is None:
            return False
        with os.scandir(source) as entries:
            return next(entries, None) is not None
    except (OSError, DesktopImageRecoveryError):
        raise DesktopImageRecoveryError(_FAILURE) from None


def preview_image_recovery(data_root: Path) -> ImageRecoveryPreview:
    """Read a bounded no-follow snapshot of qualifying UUID runs without mutation."""

    try:
        return _inventory(data_root).preview
    except (OSError, DesktopImageRecoveryError):
        raise DesktopImageRecoveryError(_FAILURE) from None


def _lease_identity(path: Path, descriptor: int) -> None:
    linked = ImageGalleryStore._require_safe_regular_file(path, maximum_bytes=1)
    opened = os.fstat(descriptor)
    if (linked.st_dev != opened.st_dev or linked.st_ino != opened.st_ino
            or opened.st_nlink != 1 or opened.st_size != 1 or linked.st_size != 1):
        raise DesktopImageRecoveryError(_FAILURE)


@contextmanager
def _gallery_lease(data_root: Path) -> Iterator[tuple[Path, int]]:
    _, _, gallery = _roots(data_root)
    _directory(gallery)
    marker = gallery / _OWNER_MARKER_NAME
    marker_info = ImageGalleryStore._require_safe_regular_file(marker, maximum_bytes=len(_OWNER_MARKER_BYTES))
    marker_descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if (_signature(os.fstat(marker_descriptor)) != _signature(marker_info)
                or os.read(marker_descriptor, len(_OWNER_MARKER_BYTES) + 1) != _OWNER_MARKER_BYTES
                or _signature(marker.lstat()) != _signature(marker_info)):
            raise DesktopImageRecoveryError(_FAILURE)
    finally:
        os.close(marker_descriptor)
    path = gallery / _LOCK_NAME
    # Reuse the exact Gallery lock inode and platform byte-lock helpers without
    # constructing a store, which would rebuild indexes or remove transactions.
    ImageGalleryStore._require_safe_regular_file(path, maximum_bytes=1)
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    locked = False
    try:
        _lease_identity(path, descriptor)
        ImageGalleryStore._lock_process_descriptor(descriptor)
        locked = True
        _lease_identity(path, descriptor)
        yield path, descriptor
    finally:
        try:
            if locked:
                ImageGalleryStore._unlock_process_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _bound_move(source_root: Path, destination_root: Path, source: Path, destination: Path) -> None:
    _directory(source_root)
    _directory(destination_root)
    _directory(source)
    if (source.parent != source_root or destination.parent != destination_root
            or source.name != destination.name
            or source.resolve(strict=True).parent != source_root.resolve(strict=True)
            or destination.resolve(strict=False).parent != destination_root.resolve(strict=True)):
        raise DesktopImageRecoveryError(_FAILURE)
    _uuid(source.name)
    try:
        destination.lstat()
    except FileNotFoundError:
        return
    raise DesktopImageRecoveryError(_FAILURE)


def _rename_exclusive(source: Path, destination: Path) -> None:
    # Plain POSIX rename may overwrite an empty foreign directory. Both native
    # branches atomically refuse any existing destination, including late races.
    if os.name == "nt":
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        if not move(str(source), str(destination), 0):
            raise DesktopImageRecoveryError(_FAILURE)
        return
    if sys.platform.startswith("linux"):
        rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        if rename is not None:
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
                return
    raise DesktopImageRecoveryError(_FAILURE)


def quarantine_image_recovery(data_root: Path, *, snapshot: str, trusted_confirmation: bool) -> int:
    """Move only the explicitly confirmed snapshot to the fixed quarantine root.

    Main must first stop and join its owned Backend. The existing Gallery lease
    excludes cooperating Backends; native no-overwrite renames preserve every
    directory on partial failure. No recursive removal or process killing occurs.
    """

    if trusted_confirmation is not True or type(snapshot) is not str or _SNAPSHOT.fullmatch(snapshot) is None:
        raise DesktopImageRecoveryError(_FAILURE)
    try:
        with _gallery_lease(data_root) as (lease_path, descriptor):
            inventory = _inventory(data_root)
            if inventory.preview.snapshot != snapshot:
                raise DesktopImageRecoveryError(_FAILURE)
            if not inventory.runs:
                return 0
            inventory.destination.mkdir(mode=0o700, exist_ok=True)
            _directory(inventory.destination)
            for run in inventory.runs:
                _bound_move(inventory.source, inventory.destination, inventory.source / run.name, inventory.destination / run.name)
            for run in inventory.runs:
                _lease_identity(lease_path, descriptor)
                source = inventory.source / run.name
                destination = inventory.destination / run.name
                _bound_move(inventory.source, inventory.destination, source, destination)
                if _tree(source, budget=_MAX_ENTRIES).records != run.records:
                    raise DesktopImageRecoveryError(_FAILURE)
                _rename_exclusive(source, destination)
                if _tree(destination, budget=_MAX_ENTRIES).records != run.records:
                    raise DesktopImageRecoveryError(_FAILURE)
            return len(inventory.runs)
    except Exception:
        raise DesktopImageRecoveryError(_FAILURE) from None
