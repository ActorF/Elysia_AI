"""Create and remove isolated image-runtime directories under a trusted root.

Each run owns one UUID-named direct child of a caller-selected trusted root.
Cleanup first inventories the complete run tree without following redirects,
then removes only the inventoried regular files and directories.  The separate
validation phase prevents an unsafe descendant from causing partial cleanup,
while repeated checks during removal reduce the risk of deleting through a
path that changed after inventory.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Final
import uuid


_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0400
_RUN_CHILD_NAMES: Final = (
    "input",
    "output",
    "temp",
    "user",
    "cache",
    "frontend",
)
_UUID_CREATION_ATTEMPTS: Final = 16
_MAX_TREE_DEPTH: Final = 64
_MAX_TREE_ENTRIES: Final = 100_000


class PrivateRunDirectoryError(RuntimeError):
    """Report that a private run directory could not be handled safely."""


@dataclass(frozen=True, slots=True)
class PrivateRunDirectory:
    """Describe the fixed filesystem layout owned by one private image run."""

    root: Path
    input: Path
    output: Path
    temp: Path
    user: Path
    cache: Path
    frontend: Path


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    """Return a lexical absolute path without resolving filesystem redirects."""

    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as error:
        raise PrivateRunDirectoryError(
            "Private run directory path is invalid."
        ) from error


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows junctions and other reparse-backed redirects."""

    return bool(
        getattr(info, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _safe_lstat(path: Path, *, label: str) -> os.stat_result:
    """Read one node without following it and collapse path-bearing errors."""

    try:
        return os.lstat(path)
    except (OSError, ValueError) as error:
        raise PrivateRunDirectoryError(
            f"{label} is unavailable or unsafe."
        ) from error


def _require_directory(path: Path, *, label: str) -> None:
    """Require an ordinary directory that is not a link or reparse point."""

    info = _safe_lstat(path, label=label)
    if (
        stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
        or not stat.S_ISDIR(info.st_mode)
    ):
        raise PrivateRunDirectoryError(f"{label} is unavailable or unsafe.")


def _require_regular_file(path: Path, *, label: str) -> None:
    """Require an ordinary file that is not a link or reparse point."""

    info = _safe_lstat(path, label=label)
    if (
        stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
        or not stat.S_ISREG(info.st_mode)
    ):
        raise PrivateRunDirectoryError(f"{label} is unavailable or unsafe.")


def _layout(root: Path) -> PrivateRunDirectory:
    """Build the fixed run layout without touching the filesystem."""

    return PrivateRunDirectory(
        root=root,
        input=root / "input",
        output=root / "output",
        temp=root / "temp",
        user=root / "user",
        cache=root / "cache",
        frontend=root / "frontend",
    )


def _require_canonical_layout(directory: PrivateRunDirectory) -> Path:
    """Validate that an object names one canonical UUID direct-child layout."""

    if type(directory) is not PrivateRunDirectory:
        raise PrivateRunDirectoryError("Private run directory layout is invalid.")
    try:
        root = _absolute_path(directory.root)
        if directory.root != root or directory != _layout(root):
            raise PrivateRunDirectoryError(
                "Private run directory layout is invalid."
            )
        run_id = uuid.UUID(root.name)
    except (AttributeError, TypeError, ValueError) as error:
        raise PrivateRunDirectoryError(
            "Private run directory layout is invalid."
        ) from error
    if run_id.version != 4 or str(run_id) != root.name:
        raise PrivateRunDirectoryError("Private run directory layout is invalid.")
    return root


def _require_directory_chain(trusted_root: Path, directory: Path) -> None:
    """Recheck every run-owned directory before a path-based deletion.

    This repeated lexical walk catches a parent that became a symlink or
    junction after the initial inventory.  It is intentionally rooted at the
    already selected trust boundary, so cleanup can never walk through ``..``
    or inspect unrelated siblings.
    """

    _require_directory(trusted_root, label="Private run trusted root")
    try:
        relative = directory.relative_to(trusted_root)
    except ValueError as error:
        raise PrivateRunDirectoryError(
            "Private run directory layout is invalid."
        ) from error
    current = trusted_root
    for part in relative.parts:
        current = current / part
        _require_directory(current, label="Private run directory")


def _inventory_tree(root: Path) -> tuple[list[Path], list[Path]]:
    """Inventory regular files and directories without following redirects.

    No deletion occurs during this pass.  Refusing the whole operation before
    mutation is important because callers use cleanup success as proof that no
    private runtime state remains.
    """

    pending = [root]
    files: list[Path] = []
    directories: list[Path] = []
    while pending:
        current = pending.pop()
        _require_directory(current, label="Private run directory")
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as error:
                        raise PrivateRunDirectoryError(
                            "Private run directory could not be inspected safely."
                        ) from error
                    candidate = current / entry.name
                    if (
                        len(candidate.relative_to(root).parts) > _MAX_TREE_DEPTH
                        or len(files) + len(directories) >= _MAX_TREE_ENTRIES
                    ):
                        # Scan entries incrementally: materializing a hostile
                        # directory first would defeat the memory bound.
                        raise PrivateRunDirectoryError(
                            "Private run directory exceeds its cleanup bounds."
                        )
                    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                        raise PrivateRunDirectoryError(
                            "Private run directory contains a link or reparse point."
                        )
                    if stat.S_ISDIR(info.st_mode):
                        directories.append(candidate)
                        pending.append(candidate)
                    elif stat.S_ISREG(info.st_mode):
                        files.append(candidate)
                    else:
                        raise PrivateRunDirectoryError(
                            "Private run directory contains a special filesystem node."
                        )
        except PrivateRunDirectoryError:
            raise
        except OSError as error:
            raise PrivateRunDirectoryError(
                "Private run directory could not be inspected safely."
            ) from error
    return files, directories


def create_private_run_directory(
    trusted_root: str | os.PathLike[str],
) -> PrivateRunDirectory:
    """Create and return one private UUID run layout below ``trusted_root``.

    The trust root must already exist as an ordinary directory.  Every newly
    created directory requests owner-only POSIX permissions; Windows applies
    the caller's directory ACL instead.  UUID collisions are retried without
    adopting or modifying the pre-existing entry.
    """

    root = _absolute_path(trusted_root)
    _require_directory(root, label="Private run trusted root")
    run_root: Path | None = None
    for _attempt in range(_UUID_CREATION_ATTEMPTS):
        candidate = root / str(uuid.uuid4())
        try:
            os.mkdir(candidate, mode=0o700)
        except FileExistsError:
            continue
        except OSError as error:
            raise PrivateRunDirectoryError(
                "Private run directory could not be created."
            ) from error
        run_root = candidate
        break
    if run_root is None:
        raise PrivateRunDirectoryError(
            "Private run directory could not allocate a unique identity."
        )

    directory = _layout(run_root)
    try:
        for child_name in _RUN_CHILD_NAMES:
            os.mkdir(run_root / child_name, mode=0o700)
    except OSError as error:
        try:
            remove_private_run_directory(directory)
        except PrivateRunDirectoryError as cleanup_error:
            raise PrivateRunDirectoryError(
                "Private run directory creation failed and cleanup was unsafe."
            ) from cleanup_error
        raise PrivateRunDirectoryError(
            "Private run directory could not be created."
        ) from error
    return directory


def remove_private_run_directory(directory: PrivateRunDirectory) -> None:
    """Safely remove exactly one UUID run tree while preserving its siblings.

    The trusted parent, target, and every descendant must remain ordinary
    filesystem nodes.  A complete no-links/no-special-files inventory precedes
    mutation, and each path is checked again immediately before removal.
    """

    root = _require_canonical_layout(directory)
    trusted_root = root.parent
    _require_directory(trusted_root, label="Private run trusted root")
    _require_directory_chain(trusted_root, root)
    files, directories = _inventory_tree(root)

    try:
        for candidate in files:
            _require_directory_chain(trusted_root, candidate.parent)
            _require_regular_file(candidate, label="Private run file")
            candidate.unlink()
        for candidate in sorted(
            directories,
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            _require_directory_chain(trusted_root, candidate)
            candidate.rmdir()
        _require_directory_chain(trusted_root, root)
        root.rmdir()
    except OSError as error:
        raise PrivateRunDirectoryError(
            "Private run directory could not be removed safely."
        ) from error
