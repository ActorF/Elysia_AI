"""Restrict Work filesystem access to one selected, identity-pinned directory.

An absolute path is configuration, not permission to follow links later. Every
operation opens the complete directory chain again and checks the selected
root's identity. Windows directory handles deny rename/delete while held;
POSIX operations use directory descriptors and O_NOFOLLOW. Leaf reads use the
same opened descriptor for validation and bytes, never a check-then-open path.
No shell command, network share, device namespace, or implicit write is exposed.
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator, Literal


_MAX_PATH_LENGTH = 4096
_MAX_READ_BYTES = 16 * 1024 * 1024
_MAX_DIRECTORY_ENTRIES = 10_000
_RESERVED_NAME = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\..*)?\Z",
    re.IGNORECASE,
)
_REPARSE_POINT = 0x400
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class WorkspaceBoundaryError(ValueError):
    """Report an unsafe, changed, unavailable, or excessive Work path."""


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """Return bounded metadata with a root-relative path and stable identity."""

    path: str
    name: str
    kind: Literal["file", "directory"]
    size_bytes: int
    identity: tuple[int, int]


class _WindowsFileInformation(ctypes.Structure):
    """Mirror the native attributes and identity of one already-open handle."""

    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("modified", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


_kernel32: Any = None
if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_WindowsFileInformation),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    _kernel32.GetFileType.restype = wintypes.DWORD
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetDriveTypeW.restype = wintypes.UINT
    _kernel32.QueryDosDeviceW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
    ]
    _kernel32.QueryDosDeviceW.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _validate_component(component: str) -> None:
    """Exclude spellings that Win32 silently redirects or normalizes."""

    if (
        not component or component in {".", ".."}
        or component != component.strip() or component.endswith(".")
        or any(ord(char) < 32 or char in '<>:"\\|?*' for char in component)
        or _RESERVED_NAME.fullmatch(component) is not None
    ):
        raise WorkspaceBoundaryError("Work path contains an unsafe component.")


def _relative_parts(value: str, *, allow_root: bool = False) -> tuple[str, ...]:
    """Accept only portable slash-separated relative paths, with no aliases."""

    if type(value) is not str or len(value) > _MAX_PATH_LENGTH:
        raise WorkspaceBoundaryError("Work path is invalid or too long.")
    if value == "" and allow_root:
        return ()
    if not value or ntpath.isabs(value) or value.startswith("/"):
        raise WorkspaceBoundaryError("Work path must be relative to its workspace.")
    parts = tuple(value.split("/"))
    if len(parts) > 128:
        raise WorkspaceBoundaryError("Work path has too many components.")
    for component in parts:
        _validate_component(component)
    return parts


def validate_relative_path(value: str, *, allow_root: bool = False) -> str:
    """Validate a portable root-relative tool argument without touching disk.

    Returning the unchanged spelling lets callers freeze exact approval
    parameters. Actual access must still pass through WorkspaceBoundary.
    """

    _relative_parts(value, allow_root=allow_root)
    return value


def _identity(details: os.stat_result) -> tuple[int, int]:
    """Use filesystem-qualified identity so a replaced root cannot inherit approval."""

    return details.st_dev, details.st_ino


def _require_safe(details: os.stat_result, *, directory: bool = False) -> None:
    """Reject redirection, special files, and hard-linked file aliases."""

    if getattr(details, "st_file_attributes", 0) & _REPARSE_POINT:
        raise WorkspaceBoundaryError("Work paths cannot traverse reparse points.")
    if directory:
        safe = stat.S_ISDIR(details.st_mode)
    else:
        safe = stat.S_ISREG(details.st_mode) or stat.S_ISDIR(details.st_mode)
    if not safe:
        raise WorkspaceBoundaryError("Work path is not a regular file or directory.")
    # An in-scope hard link can alias an out-of-scope private file. Reject it
    # rather than claim that lexical containment proves data isolation.
    if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
        raise WorkspaceBoundaryError("Work files cannot have hard-link aliases.")


def _windows_drive_mapping(path: Path) -> str:
    """Allow only a direct fixed local volume, excluding mapped and SUBST drives."""

    drive = path.drive.upper()
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
        raise WorkspaceBoundaryError("Workspace must use a local drive letter.")
    if _kernel32.GetDriveTypeW(drive + "\\") != 3:
        raise WorkspaceBoundaryError("Workspace must be on a fixed local drive.")
    buffer = ctypes.create_unicode_buffer(32768)
    if not _kernel32.QueryDosDeviceW(drive, buffer, len(buffer)):
        raise WorkspaceBoundaryError("Workspace drive could not be verified.")
    if re.fullmatch(r"\\Device\\HarddiskVolume[0-9]+", buffer.value, re.I) is None:
        raise WorkspaceBoundaryError("Workspace uses an unsafe drive mapping.")
    return buffer.value


def _windows_handle(path: Path, *, directory: bool, allow_alias: bool = False) -> int:
    """Open an exact non-reparse object while refusing rename/delete sharing."""

    handle = _kernel32.CreateFileW(
        # FILE_READ_ATTRIBUTES alone does not participate in Windows sharing
        # checks for directory rename. FILE_LIST_DIRECTORY makes deny-delete
        # effective, closing the parent-swap race before any leaf path is used.
        str(path), 0x81 if directory else 0x80000000,
        0x3 if directory else 0x1, None, 3,
        0x00200000 | (0x02000000 if directory else 0), None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        raise WorkspaceBoundaryError("Workspace object could not be opened safely.")
    owned = int(handle)
    try:
        info = _WindowsFileInformation()
        if not _kernel32.GetFileInformationByHandle(owned, ctypes.byref(info)):
            raise WorkspaceBoundaryError("Workspace object identity is unavailable.")
        if (
            _kernel32.GetFileType(owned) != 1 or info.attributes & _REPARSE_POINT
            or bool(info.attributes & 0x10) != directory
            or (not directory and info.links != 1)
        ):
            raise WorkspaceBoundaryError("Workspace object is redirected or unsafe.")
        buffer = ctypes.create_unicode_buffer(32768)
        length = _kernel32.GetFinalPathNameByHandleW(owned, buffer, len(buffer), 0)
        canonical = buffer.value
        if canonical.startswith("\\\\?\\"):
            canonical = canonical[4:]
        if (
            not length or length >= len(buffer)
            or (not allow_alias and ntpath.normcase(canonical.rstrip("\\"))
                != ntpath.normcase(str(path).rstrip("\\")))
        ):
            raise WorkspaceBoundaryError("Workspace object has a path alias.")
        return owned
    except BaseException:
        _windows_close(owned)
        raise


def _windows_close(handle: int) -> None:
    """Release one owned handle and surface a failure without disclosing paths."""

    if not _kernel32.CloseHandle(handle):
        raise WorkspaceBoundaryError("Workspace handle could not be released.")


@contextmanager
def _opened_directory(
    path: Path, *, parent_fd: int | None = None, allow_alias: bool = False,
) -> Iterator[int]:
    """Pin one directory without following a mutable path component."""

    if os.name == "nt":
        handle = _windows_handle(path, directory=True, allow_alias=allow_alias)
        try:
            yield handle
        finally:
            _windows_close(handle)
    else:
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
        if not directory_flag or not nofollow_flag:
            raise WorkspaceBoundaryError("Secure workspace directory access is unavailable.")
        descriptor = os.open(
            str(path) if parent_fd is None else path.name,
            os.O_RDONLY | directory_flag | nofollow_flag,
            dir_fd=parent_fd,
        )
        try:
            _require_safe(os.fstat(descriptor), directory=True)
            yield descriptor
        finally:
            os.close(descriptor)


class WorkspaceParent:
    """Expose a leaf capability only while its parent's directory chain is pinned.

    Callers must perform POSIX mutations using directory_fd and leaf_name.
    Windows callers may use path because each ancestor denies rename/delete.
    The context closes every capability on exit; retaining this value does not
    grant access to a later operation or authorize a write by itself.
    """

    def __init__(self, parent_path: Path, leaf_name: str, directory_fd: int | None) -> None:
        """Store an already-pinned parent for one validated leaf name."""

        self.parent_path = parent_path
        self.leaf_name = leaf_name
        self.directory_fd = directory_fd
        self.path = parent_path / leaf_name
        self._active = True

    def _require_active(self) -> None:
        if not self._active:
            raise WorkspaceBoundaryError("Workspace access context has ended.")

    def stat_leaf(self) -> os.stat_result | None:
        """Read non-following metadata, returning None only for an absent leaf."""

        self._require_active()
        try:
            details = os.stat(
                self.path if self.directory_fd is None else self.leaf_name,
                dir_fd=self.directory_fd, follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        _require_safe(details)
        return details

    @contextmanager
    def open_leaf(self) -> Iterator[int]:
        """Pin a regular leaf for bounded reads; reject links and concurrent replacement."""

        self._require_active()
        if os.name == "nt":
            import msvcrt

            handle = _windows_handle(self.path, directory=False)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                _windows_close(handle)
                raise
        else:
            nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
            nonblock_flag = getattr(os, "O_NONBLOCK", 0)
            if not nofollow_flag or not nonblock_flag:
                raise WorkspaceBoundaryError("Secure workspace file access is unavailable.")
            descriptor = os.open(
                self.leaf_name, os.O_RDONLY | nofollow_flag | nonblock_flag,
                dir_fd=self.directory_fd,
            )
        try:
            _require_safe(os.fstat(descriptor))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise WorkspaceBoundaryError("Work read requires a regular file.")
            yield descriptor
        finally:
            os.close(descriptor)


class WorkspaceBoundary:
    """Validate one selected root and recheck its identity on every filesystem operation."""

    def __init__(
        self, root: Path, *, expected_root_identity: tuple[int, int] | None = None,
    ) -> None:
        """Require an existing local directory, rejecting aliases and substituted roots."""

        if not isinstance(root, Path) or not root.is_absolute():
            raise WorkspaceBoundaryError("Workspace root must be an absolute path.")
        rendered = str(root)
        if (
            len(rendered) > _MAX_PATH_LENGTH or rendered.startswith(("//", "\\\\"))
            or root == Path(root.anchor)
        ):
            raise WorkspaceBoundaryError("Workspace root is too broad or invalid.")
        for component in root.parts[1:]:
            _validate_component(component)
        if expected_root_identity is not None and (
            type(expected_root_identity) is not tuple or len(expected_root_identity) != 2
            or any(type(value) is not int or value < 0 for value in expected_root_identity)
        ):
            raise WorkspaceBoundaryError("Expected workspace identity is invalid.")
        self._root = root
        self._root_identity: tuple[int, int] | None = None
        try:
            with self._directory(()) as (_, descriptor):
                if os.name == "nt":
                    details = root.stat(follow_symlinks=False)
                else:
                    details = os.fstat(descriptor)
                _require_safe(details, directory=True)
                self._root_identity = _identity(details)
                # The entire selected chain is now non-reparse and pinned.
                # Resolving here expands Windows 8.3 spellings safely, so a
                # short-name alias cannot bypass protected-root comparisons.
                canonical_root = root.resolve(strict=True)
                canonical_details = canonical_root.stat(follow_symlinks=False)
                _require_safe(canonical_details, directory=True)
                if _identity(canonical_details) != self._root_identity:
                    raise WorkspaceBoundaryError("Selected workspace root changed during normalization.")
            self._root = canonical_root
            if expected_root_identity is not None and self._root_identity != expected_root_identity:
                raise WorkspaceBoundaryError("Selected workspace root has changed.")
        except OSError:
            raise WorkspaceBoundaryError("Workspace root could not be opened safely.") from None

    @property
    def root(self) -> Path:
        """Return the selected absolute root; accessing it alone grants no filesystem capability."""

        return self._root

    @property
    def normalized_root(self) -> str:
        """Return the validated root spelling for the private binding repository."""

        return str(self._root)

    @property
    def root_identity(self) -> tuple[int, int]:
        """Return the stable selected-root identity to persist alongside its path."""

        assert self._root_identity is not None
        return self._root_identity

    @contextmanager
    def _directory(self, parts: tuple[str, ...]) -> Iterator[tuple[Path, int]]:
        """Open each ancestor before its child; never resolve through a link first."""

        mapping = _windows_drive_mapping(self.root) if os.name == "nt" else None
        current = Path(self.root.anchor)
        initial_selection = self._root_identity is None
        with ExitStack() as stack:
            descriptor = stack.enter_context(_opened_directory(current, allow_alias=True)) if initial_selection else stack.enter_context(_opened_directory(current))
            root_components = self.root.parts[1:]
            for index, component in enumerate((*root_components, *parts)):
                current = current / component
                if initial_selection:
                    descriptor = stack.enter_context(_opened_directory(
                        current, parent_fd=None if os.name == "nt" else descriptor,
                        allow_alias=True,
                    ))
                else:
                    descriptor = stack.enter_context(_opened_directory(
                        current, parent_fd=None if os.name == "nt" else descriptor,
                    ))
                if index == len(root_components) - 1 and self._root_identity is not None:
                    details = current.stat(follow_symlinks=False) if os.name == "nt" else os.fstat(descriptor)
                    if _identity(details) != self._root_identity:
                        raise WorkspaceBoundaryError("Selected workspace root has changed.")
            if mapping is not None and _windows_drive_mapping(self.root) != mapping:
                raise WorkspaceBoundaryError("Workspace drive mapping has changed.")
            yield current, descriptor

    @contextmanager
    def guarded_parent(self, relative_path: str) -> Iterator[WorkspaceParent]:
        """Pin ancestors for one relative leaf; an absent leaf is allowed for future creation."""

        parts = _relative_parts(relative_path)
        try:
            with self._directory(parts[:-1]) as (parent, descriptor):
                access = WorkspaceParent(parent, parts[-1], None if os.name == "nt" else descriptor)
                try:
                    access.stat_leaf()
                    yield access
                finally:
                    access._active = False
        except OSError:
            raise WorkspaceBoundaryError("Workspace path could not be accessed safely.") from None

    def read_bytes(self, relative_path: str, *, max_bytes: int = 262_144) -> bytes:
        """Read a bounded regular file through one pinned descriptor; reject changes during reading."""

        if type(max_bytes) is not int or not 0 < max_bytes <= _MAX_READ_BYTES:
            raise WorkspaceBoundaryError("Work read limit is invalid.")
        with self.guarded_parent(relative_path) as access, access.open_leaf() as descriptor:
            before = os.fstat(descriptor)
            if before.st_size > max_bytes:
                raise WorkspaceBoundaryError("Work file exceeds the read limit.")
            chunks: list[bytes] = []
            count = 0
            while count <= max_bytes:
                chunk = os.read(descriptor, min(65_536, max_bytes + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            after = os.fstat(descriptor)
            _require_safe(after)
            if (
                count > max_bytes or count != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                raise WorkspaceBoundaryError("Work file changed during its bounded read.")
            return b"".join(chunks)

    def metadata(self, relative_path: str = "") -> WorkspaceEntry:
        """Return metadata for an existing in-scope file or directory without opening its contents."""

        parts = _relative_parts(relative_path, allow_root=True)
        if not parts:
            try:
                with self._directory(()) as (path, descriptor):
                    root_details = path.stat(follow_symlinks=False) if os.name == "nt" else os.fstat(descriptor)
            except OSError:
                raise WorkspaceBoundaryError("Workspace root is unavailable.") from None
            return self._entry("", self.root.name, root_details)
        with self.guarded_parent(relative_path) as access:
            details = access.stat_leaf()
            if details is None:
                raise WorkspaceBoundaryError("Work path does not exist.")
            return self._entry(relative_path, access.leaf_name, details)

    def list_directory(
        self, relative_path: str = "", *, max_entries: int = 1000,
    ) -> tuple[WorkspaceEntry, ...]:
        """List a bounded directory without following children; unsafe entries are omitted."""

        if type(max_entries) is not int or not 0 < max_entries <= _MAX_DIRECTORY_ENTRIES:
            raise WorkspaceBoundaryError("Work directory limit is invalid.")
        parts = _relative_parts(relative_path, allow_root=True)
        try:
            with self._directory(parts) as (path, descriptor):
                entries: list[WorkspaceEntry] = []
                # scandir streams entries so the limit bounds allocation even
                # for a directory containing millions of unrelated files.
                with os.scandir(path if os.name == "nt" else descriptor) as scan:
                    for count, entry in enumerate(scan, start=1):
                        if count > max_entries:
                            raise WorkspaceBoundaryError("Work directory exceeds the entry limit.")
                        try:
                            _validate_component(entry.name)
                            # Windows DirEntry caches enumeration metadata,
                            # which reports a zero link count and no stable
                            # inode. Ask the pinned path for full metadata.
                            details = os.stat(entry.path, follow_symlinks=False) if os.name == "nt" else entry.stat(follow_symlinks=False)
                            _require_safe(details)
                        except (OSError, WorkspaceBoundaryError):
                            continue
                        entry_path = "/".join((*parts, entry.name))
                        entries.append(self._entry(entry_path, entry.name, details))
                return tuple(sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name)))
        except OSError:
            raise WorkspaceBoundaryError("Work directory could not be listed safely.") from None

    @staticmethod
    def _entry(path: str, name: str, details: os.stat_result) -> WorkspaceEntry:
        return WorkspaceEntry(
            path, name, "directory" if stat.S_ISDIR(details.st_mode) else "file",
            details.st_size if stat.S_ISREG(details.st_mode) else 0, _identity(details),
        )
