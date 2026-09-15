"""Pin trusted Windows files as immutable, verified, read-only inputs.

Managed local-model workers must not trust a path merely because its bytes
matched a catalog once.  This module opens every path component without
following reparse points, keeps directory handles that deny rename/delete, and
keeps each leaf handle with read-only sharing.  The leaf is hashed through that
same handle and its stable file identity is checked before and after hashing.

Paths and digests can reveal private local-model details.  Public exceptions
and representations therefore use a small fixed vocabulary and never render a
path, digest, or native handle.  The module remains importable away from
Windows, where acquisition fails with a stable unavailable error.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import ntpath
import os
from pathlib import Path
import re
from threading import Condition, Lock, RLock
from typing import Any, Iterable, Tuple


_IS_WINDOWS = os.name == "nt"
_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_DEVICE = 0x00000040
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_BEGIN = 0
_FILE_TYPE_DISK = 0x0001
_DRIVE_FIXED = 3
_VOLUME_NAME_GUID = 0x00000001
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_WINDOWS_PATH_CODE_UNITS = 32_767
_CONCRETE_PATH_TYPE = type(Path("."))
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_FIXED_VOLUME_MAPPING_PATTERN = re.compile(
    r"\\Device\\HarddiskVolume[0-9]+\Z", re.IGNORECASE
)
_VOLUME_GUID_PATH_PATTERN = re.compile(
    r"\\\\\?\\Volume\{"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"\}\\.+\Z",
    re.IGNORECASE,
)
_CONSTRUCTION_PROVENANCE = object()
_GUARDED_VOLUME_PATH_ACCESS_SEAL = object()

_HANDLE = wintypes.HANDLE


class WindowsReadOnlyFileGuardError(RuntimeError):
    """Base class for sanitized trusted-file guard failures."""


class WindowsReadOnlyFileGuardUnavailableError(WindowsReadOnlyFileGuardError):
    """Report that the required Windows file primitives are unavailable."""


class WindowsReadOnlyFileGuardValidationError(WindowsReadOnlyFileGuardError):
    """Report a malformed or mismatched declaration without disclosing it."""


class WindowsReadOnlyFileGuardAcquireError(WindowsReadOnlyFileGuardError):
    """Report that native handles could not be acquired as one transaction."""


class WindowsReadOnlyFileGuardCloseError(WindowsReadOnlyFileGuardError):
    """Report that at least one owned native handle remains retryable."""


@dataclass(frozen=True, repr=False)
class GuardedFileDeclaration:
    """Declare one absolute regular file and its immutable byte identity.

    ``sha256`` must be exactly 64 lowercase hexadecimal characters.  Validation
    intentionally occurs at acquisition rather than construction so creating a
    catalog value remains side-effect free and unsupported platforms fail
    before any potentially sensitive declaration is inspected.
    """

    path: Path
    size_bytes: int
    sha256: str

    def __repr__(self) -> str:
        """Render a constant redacted form that cannot leak catalog secrets."""

        return f"{type(self).__name__}(redacted=True)"


class _FILETIME(ctypes.Structure):
    """Mirror the Win32 FILETIME embedded in handle information."""

    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    """Mirror identity, size, and attributes returned for a disk handle."""

    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class _FileSnapshot:
    """Freeze only native fields needed to detect identity or content races."""

    volume_serial: int
    file_index: int
    size_bytes: int
    attributes: int

    @property
    def identity(self) -> Tuple[int, int]:
        """Return a volume-qualified identity that also catches hard-link aliases."""

        return self.volume_serial, self.file_index


_kernel32: Any
if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        _HANDLE,
    ]
    _kernel32.CreateFileW.restype = _HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = [
        _HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileType.argtypes = [_HANDLE]
    _kernel32.GetFileType.restype = wintypes.DWORD
    _kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wintypes.UINT
    _kernel32.QueryDosDeviceW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    _kernel32.QueryDosDeviceW.restype = wintypes.DWORD
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        _HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.SetFilePointerEx.argtypes = [
        _HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        _HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [_HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:
    _kernel32 = None

# A failed CloseHandle normally means the caller still owns the handle.  Keep
# that ownership explicit across an acquisition exception instead of dropping
# the last Python reference and silently leaving an untracked lock behind.
_DEFERRED_ROLLBACK_LOCK = Lock()
_DEFERRED_ROLLBACK_HANDLES: set[int] = set()

# The deferred-ownership gate is meaningful only if another caller cannot pass
# it while an earlier transaction is still opening, verifying, or rolling back
# handles.  Serialize the complete acquisition transaction process-wide.
_ACQUISITION_TRANSACTION_LOCK = RLock()


def _require_windows() -> None:
    """Fail before inspecting declarations when Win32 locking is unavailable."""

    if not _IS_WINDOWS or _kernel32 is None:
        raise WindowsReadOnlyFileGuardUnavailableError(
            "The Windows read-only file guard is unavailable on this platform."
        )


def _handle_value(handle: object) -> int:
    """Convert a non-null ctypes handle to its pointer-sized integer value."""

    if isinstance(handle, int):
        return handle
    value = getattr(handle, "value", None)
    return 0 if value is None else int(value)


def _close_handle(handle: int) -> bool:
    """Release one owned native handle without exposing native diagnostics."""

    if handle == 0 or _kernel32 is None:
        return True
    return bool(_kernel32.CloseHandle(_HANDLE(handle)))


def _close_with_retry(handle: int) -> bool:
    """Retry one non-blocking native close before declaring ownership pending."""

    for _attempt in range(2):
        try:
            if _close_handle(handle):
                return True
        except BaseException:
            # A second attempt handles transient injected/native failures while
            # preserving the same owned value; CloseHandle does not partially
            # close a valid handle when it reports failure.
            pass
    return False


def _retain_deferred_handles(handles: Iterable[int]) -> None:
    """Record still-owned handles behind the process-wide acquisition gate."""

    with _DEFERRED_ROLLBACK_LOCK:
        _DEFERRED_ROLLBACK_HANDLES.update(handles)


def _adopt_new_handle(handle: int, ownership: list[int]) -> None:
    """Transfer a fresh native handle into the provisional ownership ledger.

    Even ``list.append`` can theoretically fail under memory pressure.  Keep a
    local owner until append succeeds; otherwise close immediately and retain a
    persistently unclosed handle in the deferred registry before propagating.
    """

    try:
        ownership.append(handle)
    except BaseException:
        if not _close_with_retry(handle):
            _retain_deferred_handles((handle,))
        raise


def _rollback_handles(handles: Iterable[int]) -> bool:
    """Release provisional handles and retain ownership of persistent failures."""

    failed: list[int] = []
    for handle in reversed(tuple(handles)):
        if not _close_with_retry(handle):
            failed.append(handle)
    if failed:
        _retain_deferred_handles(failed)
        return False
    return True


def _drain_deferred_rollback_handles() -> None:
    """Retry prior rollback ownership before permitting another acquisition.

    A persistent native close failure is safer as a fail-closed process-wide
    lock than as forgotten ownership.  Every later acquisition is an explicit
    retry point and remains unavailable until all prior handles are accounted
    for and released.
    """

    with _DEFERRED_ROLLBACK_LOCK:
        if not _DEFERRED_ROLLBACK_HANDLES:
            return
        remaining = {
            handle
            for handle in _DEFERRED_ROLLBACK_HANDLES
            if not _close_with_retry(handle)
        }
        _DEFERRED_ROLLBACK_HANDLES.clear()
        _DEFERRED_ROLLBACK_HANDLES.update(remaining)
        if remaining:
            raise WindowsReadOnlyFileGuardAcquireError(
                "A prior guarded file rollback is still pending."
            ) from None


def _validate_declarations(
    declarations: object,
) -> Tuple[GuardedFileDeclaration, ...]:
    """Freeze and validate a non-empty, unambiguous declaration tuple.

    Windows normalizes case, trailing dots/spaces, device prefixes, and stream
    syntax in ways that can make distinct-looking strings address one object.
    Rejecting those ambiguous spellings here keeps the later handle identity
    check small and makes every catalog path portable across Windows APIs.
    """

    if type(declarations) is not tuple or len(declarations) == 0:
        raise WindowsReadOnlyFileGuardValidationError(
            "Guard declarations must be a non-empty tuple."
        )
    trusted: list[GuardedFileDeclaration] = []
    lexical_keys: set[str] = set()
    for declaration in declarations:
        if type(declaration) is not GuardedFileDeclaration:
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file declaration is invalid."
            )
        # Read the caller-owned frozen dataclass exactly once.  ``frozen``
        # blocks normal assignment but cannot prevent object.__setattr__ from
        # another thread; only these local scalar snapshots cross the boundary.
        supplied_path = declaration.path
        supplied_size = declaration.size_bytes
        supplied_hash = declaration.sha256
        if type(supplied_path) is not _CONCRETE_PATH_TYPE:
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file declaration is invalid."
            )
        if type(supplied_size) is not int or type(supplied_hash) is not str:
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file declaration is invalid."
            )
        rendered = str(supplied_path)
        path = Path(rendered)
        if type(path) is not _CONCRETE_PATH_TYPE or str(path) != rendered:
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file declaration is invalid."
            )
        folded_prefix = rendered.casefold()
        if (
            not rendered
            or "\x00" in rendered
            or not ntpath.isabs(rendered)
            or not path.is_absolute()
            or ntpath.normpath(rendered) != rendered
            or folded_prefix.startswith("\\\\?\\")
            or folded_prefix.startswith("\\\\.\\")
            or folded_prefix.startswith("\\??\\")
        ):
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file path is not normalized and absolute."
            )
        drive, tail = ntpath.splitdrive(rendered)
        if (
            len(drive) != 2
            or drive[0] < "A"
            or drive[0] > "Z"
            or drive[1] != ":"
            or not tail.startswith("\\")
        ):
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file path must use a local drive letter."
            )
        components = [component for component in tail.split("\\") if component]
        if not components or any(
            component in {".", ".."}
            or component.endswith((" ", "."))
            or ":" in component
            for component in components
        ):
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file path uses an ambiguous Windows spelling."
            )
        if (
            supplied_size < 0
            or supplied_size > 0x7FFFFFFFFFFFFFFF
            or _SHA256_PATTERN.fullmatch(supplied_hash) is None
        ):
            raise WindowsReadOnlyFileGuardValidationError(
                "A guarded file declaration is invalid."
            )
        lexical_key = ntpath.normcase(rendered)
        if lexical_key in lexical_keys:
            raise WindowsReadOnlyFileGuardValidationError(
                "Guard declarations contain a Windows path alias."
            )
        lexical_keys.add(lexical_key)
        # Every later native and cryptographic decision consumes this private
        # exact-type copy, never a caller object or subclass-controlled value.
        trusted.append(
            GuardedFileDeclaration(
                path=path,
                size_bytes=supplied_size,
                sha256=supplied_hash,
            )
        )
    return tuple(trusted)


def _query_fixed_drive_mapping(drive: str) -> str:
    """Return one fixed-volume DOS mapping or reject remote/SUBST namespaces.

    A drive-letter spelling is safe for later worker reopen only when Windows
    maps it directly to a fixed ``HarddiskVolume`` device.  SUBST targets use a
    ``\\??\\`` mapping and network paths use a different device namespace, so
    both fail closed even if their eventual storage happens to be local.
    """

    assert _kernel32 is not None
    root = f"{drive}\\"
    if int(_kernel32.GetDriveTypeW(root)) != _DRIVE_FIXED:
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file path is not on a fixed local volume."
        )
    buffer = ctypes.create_unicode_buffer(_MAX_WINDOWS_PATH_CODE_UNITS)
    result = int(
        _kernel32.QueryDosDeviceW(
            drive,
            buffer,
            _MAX_WINDOWS_PATH_CODE_UNITS,
        )
    )
    if result == 0:
        raise WindowsReadOnlyFileGuardAcquireError(
            "The guarded drive mapping could not be verified."
        ) from None
    mapping = buffer.value
    if _FIXED_VOLUME_MAPPING_PATTERN.fullmatch(mapping) is None:
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file path uses an unsafe drive mapping."
        )
    return mapping


def _capture_drive_mappings(
    declarations: Tuple[GuardedFileDeclaration, ...],
) -> dict[str, str]:
    """Freeze every distinct drive mapping used by one acquisition."""

    mappings: dict[str, str] = {}
    for declaration in declarations:
        drive = ntpath.splitdrive(str(declaration.path))[0].upper()
        if drive not in mappings:
            mappings[drive] = _query_fixed_drive_mapping(drive)
    return mappings


def _verify_drive_mappings(mappings: dict[str, str]) -> None:
    """Reject a DOS-device namespace change during handle acquisition."""

    for drive, expected in mappings.items():
        if _query_fixed_drive_mapping(drive).casefold() != expected.casefold():
            raise WindowsReadOnlyFileGuardAcquireError(
                "A guarded drive mapping changed during acquisition."
            ) from None


def _path_directories(path: Path) -> Tuple[str, ...]:
    """Return the drive/share root through the leaf's parent in order."""

    parent = path.parent
    chain: list[str] = []
    current = parent
    while True:
        chain.append(str(current))
        next_parent = current.parent
        if next_parent == current:
            break
        current = next_parent
    chain.reverse()
    return tuple(chain)


def _normalized_path_from_handle(handle: int, volume_name: int) -> str:
    """Return one normalized Win32 spelling selected by its volume namespace."""

    assert _kernel32 is not None
    capacity = 512
    while capacity <= _MAX_WINDOWS_PATH_CODE_UNITS:
        buffer = ctypes.create_unicode_buffer(capacity)
        result = int(
            _kernel32.GetFinalPathNameByHandleW(
                _HANDLE(handle), buffer, capacity, volume_name
            )
        )
        if result == 0:
            raise WindowsReadOnlyFileGuardAcquireError(
                "The guarded file namespace could not be verified."
            ) from None
        if result < capacity:
            return buffer.value
        capacity = result + 1
    raise WindowsReadOnlyFileGuardValidationError(
        "A guarded file path exceeds the Windows namespace limit."
    )


def _canonical_path_from_handle(handle: int) -> str:
    """Return the normalized DOS spelling Windows assigns to one open handle."""

    return _normalized_path_from_handle(handle, 0)


def _volume_guid_path_from_handle(handle: int) -> str:
    """Return and validate a stable volume-GUID spelling for one leaf handle.

    Unlike a drive letter, the GUID namespace is not redirected by DOS-device
    mapping changes.  Managed workers can therefore reopen the exact guarded
    volume even if the original drive letter experiences a later ABA remap.
    """

    path = _normalized_path_from_handle(handle, _VOLUME_NAME_GUID)
    if _VOLUME_GUID_PATH_PATTERN.fullmatch(path) is None:
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file has no unambiguous volume GUID path."
        )
    return path


def _verify_canonical_handle_path(handle: int, path: str) -> None:
    """Reject case, short-name, or other alias spellings for an open object.

    ``GetFinalPathNameByHandleW`` asks the filesystem for its normalized DOS
    spelling after the handle is open.  Exact comparison deliberately includes
    component case: a catalog must name the canonical object the same way the
    later worker will reopen it, not merely a case-insensitive alias.
    """

    expected = f"\\\\?\\{path}"
    if _canonical_path_from_handle(handle) != expected:
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file path uses a non-canonical Windows alias."
        )


def _open_directory(path: str, ownership: list[int]) -> int:
    """Open, register, and validate one non-reparse directory handle.

    Ownership enters the transaction ledger immediately after CreateFileW and
    before snapshot, type, or canonical-name checks.  Any later failure is thus
    handled by the outer atomic rollback, including a failing CloseHandle.
    """

    assert _kernel32 is not None
    handle = _kernel32.CreateFileW(
        path,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    value = _handle_value(handle)
    if value == 0 or value == _INVALID_HANDLE_VALUE:
        raise WindowsReadOnlyFileGuardAcquireError(
            "The guarded file set could not be acquired."
        ) from None
    _adopt_new_handle(value, ownership)
    snapshot = _snapshot(value)
    if (
        snapshot.attributes & _FILE_ATTRIBUTE_DIRECTORY == 0
        or snapshot.attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or _file_type(value) != _FILE_TYPE_DISK
    ):
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file path contains an unsafe directory."
        )
    _verify_canonical_handle_path(value, path)
    return value


def _open_leaf(path: str, ownership: list[int]) -> int:
    """Open, register, and validate one read-only regular-file handle.

    Registering before native inspection ensures snapshot/type/canonical-path
    failures transfer to rollback rather than losing the only owner reference.
    """

    assert _kernel32 is not None
    handle = _kernel32.CreateFileW(
        path,
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    value = _handle_value(handle)
    if value == 0 or value == _INVALID_HANDLE_VALUE:
        raise WindowsReadOnlyFileGuardAcquireError(
            "The guarded file set could not be acquired."
        ) from None
    _adopt_new_handle(value, ownership)
    snapshot = _snapshot(value)
    if (
        snapshot.attributes
        & (
            _FILE_ATTRIBUTE_DIRECTORY
            | _FILE_ATTRIBUTE_DEVICE
            | _FILE_ATTRIBUTE_REPARSE_POINT
        )
        or _file_type(value) != _FILE_TYPE_DISK
    ):
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded path is not a regular non-reparse file."
        )
    _verify_canonical_handle_path(value, path)
    return value


def _snapshot(handle: int) -> _FileSnapshot:
    """Read stable identity, size, and attributes from an already-owned handle."""

    assert _kernel32 is not None
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32.GetFileInformationByHandle(
        _HANDLE(handle), ctypes.byref(information)
    ):
        raise WindowsReadOnlyFileGuardAcquireError(
            "The guarded file set could not be verified."
        ) from None
    return _FileSnapshot(
        volume_serial=int(information.dwVolumeSerialNumber),
        file_index=(int(information.nFileIndexHigh) << 32)
        | int(information.nFileIndexLow),
        size_bytes=(int(information.nFileSizeHigh) << 32)
        | int(information.nFileSizeLow),
        attributes=int(information.dwFileAttributes),
    )


def _file_type(handle: int) -> int:
    """Return a sanitized native type value for an already-owned handle."""

    assert _kernel32 is not None
    return int(_kernel32.GetFileType(_HANDLE(handle)))


def _hash_handle(handle: int) -> str:
    """Hash bytes through the locked leaf handle without reopening its path."""

    assert _kernel32 is not None
    new_position = ctypes.c_longlong()
    if not _kernel32.SetFilePointerEx(
        _HANDLE(handle),
        ctypes.c_longlong(0),
        ctypes.byref(new_position),
        _FILE_BEGIN,
    ):
        raise WindowsReadOnlyFileGuardAcquireError(
            "The guarded file set could not be verified."
        ) from None
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(_HASH_CHUNK_BYTES)
    while True:
        bytes_read = wintypes.DWORD()
        if not _kernel32.ReadFile(
            _HANDLE(handle),
            buffer,
            _HASH_CHUNK_BYTES,
            ctypes.byref(bytes_read),
            None,
        ):
            raise WindowsReadOnlyFileGuardAcquireError(
                "The guarded file set could not be verified."
            ) from None
        count = int(bytes_read.value)
        if count == 0:
            return digest.hexdigest()
        digest.update(buffer.raw[:count])


def _verify_leaf(
    handle: int,
    declaration: GuardedFileDeclaration,
) -> _FileSnapshot:
    """Match one declaration and reject any identity/metadata change while hashing."""

    before = _snapshot(handle)
    if before.size_bytes != declaration.size_bytes:
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file declaration does not match its file."
        )
    observed_digest = _hash_handle(handle)
    after = _snapshot(handle)
    if before != after or observed_digest != declaration.sha256:
        raise WindowsReadOnlyFileGuardValidationError(
            "A guarded file declaration does not match its file."
        )
    return after


class WindowsReadOnlyFileGuardSet:
    """Own directory and leaf handles that pin a verified read-only file set.

    Instances are created atomically by :meth:`acquire`.  ``close`` has one
    concurrent owner; other callers wait for that attempt, so ``closed`` never
    becomes true before every native handle has actually been released.  A
    rare close failure retains exactly the failed handles for an explicit retry.
    """

    __slots__ = (
        "_condition",
        "_file_count",
        "_handles",
        "_state",
        "_volume_paths",
    )

    def __init__(
        self,
        handles: Tuple[int, ...],
        *,
        file_count: int,
        volume_paths: Tuple[str, ...] = (),
        _provenance: object = None,
    ) -> None:
        """Adopt handles only when called by the private acquire transaction.

        The provenance token prevents ordinary callers from forging a trusted
        guard or tricking its lifecycle into closing an unrelated native handle.
        """

        if _provenance is not _CONSTRUCTION_PROVENANCE:
            raise WindowsReadOnlyFileGuardValidationError(
                "Guard instances must be created by acquire()."
            ) from None

        self._condition = Condition(RLock())
        self._handles = handles
        self._file_count = file_count
        self._state = "active"
        self._volume_paths = volume_paths

    @classmethod
    def acquire(
        cls,
        declarations: tuple[GuardedFileDeclaration, ...],
    ) -> "WindowsReadOnlyFileGuardSet":
        """Atomically pin and verify all declared files or release every handle.

        Directories are opened from each drive/share root downward before its
        leaf.  Directory handles deny delete sharing; leaf handles allow only
        other readers.  File identity additionally rejects case, short-name,
        hard-link, and other aliases within one declaration set.
        """

        _require_windows()
        if cls is not WindowsReadOnlyFileGuardSet:
            # A subclass can replace __init__ and falsely accept ownership
            # without storing the verified handles.  This exact-class factory
            # rule closes that otherwise silent transfer/leak path.
            raise WindowsReadOnlyFileGuardValidationError(
                "Guard acquisition does not permit subclasses."
            ) from None

        with _ACQUISITION_TRANSACTION_LOCK:
            _drain_deferred_rollback_handles()
            frozen = _validate_declarations(declarations)
            drive_mappings = _capture_drive_mappings(frozen)
            handles: list[int] = []
            opened_directory_paths: set[str] = set()
            file_identities: set[Tuple[int, int]] = set()
            volume_path_keys: set[str] = set()
            volume_paths: list[str] = []
            try:
                for declaration in frozen:
                    for directory in _path_directories(declaration.path):
                        lexical_key = ntpath.normcase(directory)
                        if lexical_key in opened_directory_paths:
                            continue
                        _open_directory(directory, handles)
                        # Only exactly identical lexical paths are reused.
                        # File IDs can be unavailable or collide on unusual
                        # filesystems, so they never justify dropping a
                        # differently spelled directory guard.
                        opened_directory_paths.add(lexical_key)

                    leaf_handle = _open_leaf(str(declaration.path), handles)
                    leaf_snapshot = _verify_leaf(leaf_handle, declaration)
                    if leaf_snapshot.identity in file_identities:
                        raise WindowsReadOnlyFileGuardValidationError(
                            "Guard declarations contain a Windows file alias."
                        )
                    file_identities.add(leaf_snapshot.identity)
                    volume_path = _volume_guid_path_from_handle(leaf_handle)
                    volume_path_key = volume_path.casefold()
                    if volume_path_key in volume_path_keys:
                        raise WindowsReadOnlyFileGuardValidationError(
                            "Guard declarations contain an ambiguous volume path."
                        )
                    volume_path_keys.add(volume_path_key)
                    volume_paths.append(volume_path)
                _verify_drive_mappings(drive_mappings)
                guard = WindowsReadOnlyFileGuardSet(
                    tuple(handles),
                    file_count=len(frozen),
                    volume_paths=tuple(volume_paths),
                    _provenance=_CONSTRUCTION_PROVENANCE,
                )
                handles.clear()
                return guard
            except WindowsReadOnlyFileGuardError:
                if not _rollback_handles(handles):
                    raise WindowsReadOnlyFileGuardAcquireError(
                        "The guarded file rollback remains pending."
                    ) from None
                raise
            except BaseException:
                if not _rollback_handles(handles):
                    raise WindowsReadOnlyFileGuardAcquireError(
                        "The guarded file rollback remains pending."
                    ) from None
                raise WindowsReadOnlyFileGuardAcquireError(
                    "The guarded file acquisition transaction failed."
                ) from None

    @property
    def closed(self) -> bool:
        """Return true only after all owned native handles have been released."""

        with self._condition:
            return self._state == "closed"

    def __repr__(self) -> str:
        """Render lifecycle and cardinality without paths, hashes, or handles."""

        with self._condition:
            return (
                f"{type(self).__name__}(state={self._state!r}, "
                f"file_count={self._file_count})"
            )

    def __enter__(self) -> "WindowsReadOnlyFileGuardSet":
        """Return this owned guard for deterministic context management."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Release every lock when leaving a context, including exceptional exits."""

        self.close()

    def __del__(self) -> None:
        """Release or defer owned handles if explicit ownership was abandoned."""

        try:
            # Start inside the same transaction lock used by acquisition; no
            # new caller can pass the deferred gate between a failed destructor
            # close and publication of its still-owned handles.
            with _ACQUISITION_TRANSACTION_LOCK:
                try:
                    self.close()
                    return
                except BaseException:
                    with self._condition:
                        handles = self._handles
                        self._handles = ()
                        self._volume_paths = ()
                        self._state = "closed"
                        self._condition.notify_all()
                    _rollback_handles(handles)
        except BaseException:
            # Partially initialized objects and interpreter shutdown can make
            # synchronization unavailable.  The OS closes process handles at
            # final shutdown; normal runtime failures use the branch above.
            pass

    def close(self) -> None:
        """Release all handles exactly once and safely join concurrent callers.

        A caller that observes another close waits for its published result.
        Failed native closes leave only their exact handles owned and raise a
        stable error; a later call can retry without double-closing successes.
        """

        with self._condition:
            while self._state == "closing":
                self._condition.wait()
            if self._state == "closed":
                return
            self._state = "closing"
            handles = self._handles

        failed: list[int] = []
        for handle in reversed(handles):
            try:
                if not _close_handle(handle):
                    failed.append(handle)
            except BaseException:
                failed.append(handle)
        failed.reverse()

        with self._condition:
            self._handles = tuple(failed)
            # A partial close no longer protects every stable path.  Keep the
            # surviving handles retryable but use a distinct state so the
            # private accessor cannot mistake incomplete protection for active.
            self._state = "close-failed" if failed else "closed"
            if not failed:
                self._volume_paths = ()
            self._condition.notify_all()
        if failed:
            raise WindowsReadOnlyFileGuardCloseError(
                "The guarded file set could not be closed safely."
            ) from None


def _get_guarded_volume_paths(
    guard: object,
    *,
    access_seal: object,
) -> Tuple[str, ...]:
    """Return stable leaf paths only to the sealed managed-runtime boundary.

    The exact-type and private-seal checks prevent ordinary callers and
    subclasses from turning a public guard into a path-disclosure oracle.  The
    state check also forbids using a tuple after its protective handles begin
    closing.  No public property exposes these private deployment paths.
    """

    if (
        access_seal is not _GUARDED_VOLUME_PATH_ACCESS_SEAL
        or type(guard) is not WindowsReadOnlyFileGuardSet
    ):
        raise WindowsReadOnlyFileGuardValidationError(
            "Guarded volume paths are unavailable."
        ) from None
    with guard._condition:
        if guard._state != "active" or len(guard._volume_paths) != guard._file_count:
            raise WindowsReadOnlyFileGuardValidationError(
                "Guarded volume paths are unavailable."
            ) from None
        return guard._volume_paths
