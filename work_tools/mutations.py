"""Apply bounded, conditional file mutations through the selected Workspace guard.

Windows mutations own an exclusive leaf handle: content replacement writes that
same file, and rename/delete target that native handle rather than rechecking a
mutable source pathname. In-place replacement deliberately avoids an unsafe
check-then-rename overwrite. The execution service must durably save original
bytes before admission, because a crash or disk failure can interrupt a write.

POSIX uses no-follow directory-relative descriptors plus advisory locking and
identity checks. The local filesystem owner must cooperate with that locking;
portable POSIX APIs cannot prevent another process of the same user renaming an
open inode. Moves create a non-overwriting hard link before removing the source,
so interruption can leave two names for the approved inode, never an overwritten
destination. The execution journal owns recovery of that partial transition.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import os
import re
import stat
from typing import Any, cast, Iterator

from .workspace import (
    WorkspaceBoundary, WorkspaceBoundaryError, WorkspaceParent,
    _identity, _kernel32, _require_safe, _WindowsFileInformation, _windows_close,
)


_MAX_MUTATION_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _RenameInformation(ctypes.Structure):
    """Provide the aligned Win32 FILE_RENAME_INFO header before its UTF-16 name."""

    _fields_ = [
        ("replace", wintypes.BOOLEAN),
        ("root", wintypes.HANDLE),
        ("name_length", wintypes.DWORD),
        ("name", wintypes.WCHAR * 1),
    ]


class _DispositionInformation(ctypes.Structure):
    """Mirror the one-byte delete-on-close request for the exact owned leaf."""

    _fields_ = [("delete", wintypes.BOOLEAN)]


if os.name == "nt":
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL


def _validate_data(data: bytes) -> str:
    """Freeze a bytes-only bounded payload before any mutable capability is opened."""

    if type(data) is not bytes or len(data) > _MAX_MUTATION_BYTES:
        raise WorkspaceBoundaryError("Work write payload is invalid or too large.")
    return hashlib.sha256(data).hexdigest()


def _validate_hash(expected_sha256: str) -> None:
    if type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None:
        raise WorkspaceBoundaryError("Work expected file digest is invalid.")


def _same_named_leaf(access: WorkspaceParent, descriptor: int, *, allow_two_links: bool = False) -> None:
    """Refuse a renamed/replaced inode before any path-addressed POSIX mutation."""

    actual = os.stat(
        access.path if access.directory_fd is None else access.leaf_name,
        dir_fd=access.directory_fd, follow_symlinks=False,
    )
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(actual.st_mode) or _identity(actual) != _identity(opened):
        raise WorkspaceBoundaryError("Work target changed before mutation.")
    if allow_two_links:
        if actual.st_nlink != 2:
            raise WorkspaceBoundaryError("Work move acquired an unexpected file alias.")
    else:
        _require_safe(actual)


def _windows_mutation_handle(access: WorkspaceParent, *, create: bool, delete_access: bool) -> int:
    """Own a local regular file exclusively; CREATE_NEW refuses every existing destination."""

    requested_access = 0x80000000 | (0x10000 if delete_access else 0x40000000)
    handle = _kernel32.CreateFileW(
        str(access.path), requested_access, 0, None, 1 if create else 3,
        0x00200000, None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        raise WorkspaceBoundaryError("Work target could not be opened exclusively.")
    owned = int(handle)
    try:
        info = _WindowsFileInformation()
        if not _kernel32.GetFileInformationByHandle(owned, ctypes.byref(info)):
            raise WorkspaceBoundaryError("Work target identity is unavailable.")
        if _kernel32.GetFileType(owned) != 1 or info.attributes & (0x400 | 0x10) or info.links != 1:
            raise WorkspaceBoundaryError("Work target is redirected or has file aliases.")
        # The shared directory guard rejects drive aliases and pins every
        # ancestor; the leaf handle additionally rejects a short-name alias.
        buffer = ctypes.create_unicode_buffer(32768)
        length = _kernel32.GetFinalPathNameByHandleW(owned, buffer, len(buffer), 0)
        canonical = buffer.value.removeprefix("\\\\?\\")
        if not length or length >= len(buffer) or os.path.normcase(canonical) != os.path.normcase(str(access.path)):
            raise WorkspaceBoundaryError("Work target uses an unapproved path alias.")
        return owned
    except BaseException:
        _windows_close(owned)
        raise


@contextmanager
def _mutation_descriptor(
    access: WorkspaceParent, *, create: bool = False, delete_access: bool = False,
) -> Iterator[int]:
    """Hold exclusive native ownership or a cooperative POSIX lock until the mutation settles."""

    if os.name == "nt":
        import msvcrt

        handle = _windows_mutation_handle(access, create=create, delete_access=delete_access)
        try:
            descriptor = msvcrt.open_osfhandle(handle, (os.O_RDONLY if delete_access else os.O_RDWR) | os.O_BINARY)
        except BaseException:
            _windows_close(handle)
            raise
    else:
        import fcntl

        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_NONBLOCK", 0):
            raise WorkspaceBoundaryError("Secure Work mutation is unavailable.")
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(access.leaf_name, flags, 0o600, dir_fd=access.directory_fd)
        try:
            # Windows-targeted type checks intentionally lack fcntl members;
            # this branch runs only on POSIX, where advisory locking exists.
            native_lock = cast(Any, fcntl)
            native_lock.flock(descriptor, native_lock.LOCK_EX | native_lock.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
    try:
        details = os.fstat(descriptor)
        _require_safe(details)
        if not stat.S_ISREG(details.st_mode):
            raise WorkspaceBoundaryError("Work mutation requires a regular file.")
        _same_named_leaf(access, descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def _digest(descriptor: int) -> str:
    """Hash a bounded pinned inode, rejecting growth or metadata change during verification."""

    before = os.fstat(descriptor)
    if before.st_size > _MAX_MUTATION_BYTES:
        raise WorkspaceBoundaryError("Work file exceeds the mutation limit.")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    count = 0
    while count <= _MAX_MUTATION_BYTES:
        data = os.read(descriptor, min(65_536, _MAX_MUTATION_BYTES + 1 - count))
        if not data:
            break
        digest.update(data)
        count += len(data)
    after = os.fstat(descriptor)
    if (
        count != before.st_size or count > _MAX_MUTATION_BYTES
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise WorkspaceBoundaryError("Work file changed while verifying its digest.")
    return digest.hexdigest()


def _verify_expected(descriptor: int, expected_sha256: str) -> None:
    if _digest(descriptor) != expected_sha256:
        raise WorkspaceBoundaryError("Work target content changed after preview.")


def _write_verified(descriptor: int, data: bytes, expected_sha256: str) -> str:
    """Write, truncate, sync, and read back the same inode; the service owns crash recovery."""

    os.lseek(descriptor, 0, os.SEEK_SET)
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise WorkspaceBoundaryError("Work write could not make progress.")
        remaining = remaining[written:]
    os.ftruncate(descriptor, len(data))
    os.fsync(descriptor)
    result = _digest(descriptor)
    if result != expected_sha256:
        raise WorkspaceBoundaryError("Work write did not pass readback verification.")
    return result


def _windows_rename(descriptor: int, destination: WorkspaceParent) -> None:
    """Rename the approved handle, asking the kernel to refuse an existing destination."""

    import msvcrt

    encoded = str(destination.path).encode("utf-16-le")
    name_offset = _RenameInformation.name.offset
    # The Win32 wrapper may inspect the name as a terminated string while
    # constructing its NT request, despite the explicit byte-count field.
    # Reserve a UTF-16 terminator so it cannot read beyond the owned buffer.
    information = ctypes.create_string_buffer(name_offset + len(encoded) + 2)
    header = _RenameInformation.from_buffer(information)
    header.replace = False
    header.root = None
    header.name_length = len(encoded)
    ctypes.memmove(ctypes.addressof(information) + name_offset, encoded, len(encoded))
    if not _kernel32.SetFileInformationByHandle(
        msvcrt.get_osfhandle(descriptor), 3, information, len(information),
    ):
        raise WorkspaceBoundaryError("Work file could not be moved without overwriting a destination.")


def _windows_remove(descriptor: int) -> None:
    """Delete on handle close so a pathname swap cannot redirect the deletion."""

    import msvcrt

    information = _DispositionInformation(True)
    if not _kernel32.SetFileInformationByHandle(
        msvcrt.get_osfhandle(descriptor), 4, ctypes.byref(information), ctypes.sizeof(information),
    ):
        raise WorkspaceBoundaryError("Work file could not be removed safely.")


class WorkspaceMutations:
    """Apply explicit bounded file operations; callers own approval, durable backup, and audit."""

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        """Bind mutations to a previously selected root whose identity is rechecked per operation."""

        if not isinstance(boundary, WorkspaceBoundary):
            raise WorkspaceBoundaryError("Work mutation requires a selected workspace boundary.")
        self._boundary = boundary

    def create(self, path: str, data: bytes) -> str:
        """Create one new file exclusively and return its verified digest; never overwrite an existing leaf."""

        digest = _validate_data(data)
        with self._boundary.guarded_parent(path) as access:
            if access.stat_leaf() is not None:
                raise WorkspaceBoundaryError("Work creation destination already exists.")
            with _mutation_descriptor(access, create=True) as descriptor:
                return _write_verified(descriptor, data, digest)

    def replace(self, path: str, data: bytes, expected_sha256: str) -> str:
        """Replace bytes in the exclusively pinned inode only if its digest matches the approved preview.

        A service backup is required before this call: interrupted in-place
        writes can leave partial bytes, which must be reported and recovered.
        """

        digest = _validate_data(data)
        _validate_hash(expected_sha256)
        with self._boundary.guarded_parent(path) as access, _mutation_descriptor(access) as descriptor:
            _verify_expected(descriptor, expected_sha256)
            _same_named_leaf(access, descriptor)
            result = _write_verified(descriptor, data, digest)
            _same_named_leaf(access, descriptor)
            return result

    def move(self, source: str, destination: str, expected_sha256: str) -> str:
        """Move one approved regular file to an absent in-scope name without overwriting a destination."""

        _validate_hash(expected_sha256)
        if source == destination:
            raise WorkspaceBoundaryError("Work move requires two different file names.")
        with self._boundary.guarded_parent(source) as source_access:
            with self._boundary.guarded_parent(destination) as destination_access:
                if destination_access.stat_leaf() is not None:
                    raise WorkspaceBoundaryError("Work move destination already exists.")
                with _mutation_descriptor(source_access, delete_access=True) as descriptor:
                    _verify_expected(descriptor, expected_sha256)
                    if os.name == "nt":
                        _windows_rename(descriptor, destination_access)
                    else:
                        _same_named_leaf(source_access, descriptor)
                        os.link(
                            source_access.leaf_name, destination_access.leaf_name,
                            src_dir_fd=source_access.directory_fd,
                            dst_dir_fd=destination_access.directory_fd, follow_symlinks=False,
                        )
                        # This deliberate intermediate state is journaled by
                        # the service. Never remove either name after an inode
                        # mismatch; a later recovery must inspect both names.
                        _same_named_leaf(destination_access, descriptor, allow_two_links=True)
                        _same_named_leaf(source_access, descriptor, allow_two_links=True)
                        os.unlink(source_access.leaf_name, dir_fd=source_access.directory_fd)
                        os.fsync(descriptor)
                    return expected_sha256

    def remove(self, path: str, expected_sha256: str) -> str:
        """Remove only the exact approved regular file; recursion and directory deletion are unavailable."""

        _validate_hash(expected_sha256)
        with self._boundary.guarded_parent(path) as access:
            with _mutation_descriptor(access, delete_access=True) as descriptor:
                _verify_expected(descriptor, expected_sha256)
                if os.name == "nt":
                    _windows_remove(descriptor)
                else:
                    _same_named_leaf(access, descriptor)
                    os.unlink(access.leaf_name, dir_fd=access.directory_fd)
                return expected_sha256
