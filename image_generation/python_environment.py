"""Review and explicitly prepare a trusted embedded Python installation.

Only the interpreter, ComfyUI entry point, and checkpoint have runtime file
guards. Dependency roots in ``._pth`` remain part of the operator-reviewed
installation. Preparing them never accepts an image request or a node graph.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from uuid import uuid4


_MAX_BYTES = 65_536
_MAX_LINES = 128
_PTH_NAME = re.compile(r"python(?:3[0-9]{1,2})?\._pth\Z", re.IGNORECASE)
_PREPARATION_MESSAGE = (
    "The embedded Python environment needs reviewed absolute dependency roots. "
    "Run scripts/prepare_image_python.py with the trusted installation paths "
    "and --apply before checking image readiness again."
)


class PythonEnvironmentError(RuntimeError):
    """Report a path-free failure to review or prepare the trusted installation."""


class PythonEnvironmentPreparationRequiredError(PythonEnvironmentError):
    """Report a supported embedded installation that still needs explicit setup."""


@dataclass(frozen=True, slots=True)
class PythonEnvironmentReview:
    """Describe reviewed dependency policy without exposing installation paths."""

    mode: str
    requires_preparation: bool
    before_sha256: str | None
    after_sha256: str | None
    path_entry_count: int
    adds_site_packages: bool


@dataclass(frozen=True, slots=True)
class PythonEnvironmentPreparation:
    """Report a setup transaction and the adjacent immutable original backup."""

    review: PythonEnvironmentReview
    applied: bool
    backup_name: str | None


@dataclass(frozen=True, slots=True)
class _Plan:
    review: PythonEnvironmentReview
    path: Path | None = None
    original: bytes = b""
    prepared: bytes = b""
    identity: tuple[int, int, int, int] | None = None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _checked_path(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise PythonEnvironmentError("Trusted Python installation paths must be absolute.")
    normalized = Path(os.path.normpath(str(value)))
    for item in (*reversed(normalized.parents), normalized):
        metadata = item.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise PythonEnvironmentError("The Python installation contains a redirected path.")
    return normalized


def _read_regular(path: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    _checked_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= _MAX_BYTES:
        raise PythonEnvironmentError("The Python path configuration must be a bounded private regular file.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if _identity(before) != _identity(opened) or opened.st_nlink != 1:
            raise PythonEnvironmentError("The Python path configuration changed during review.")
        data = bytearray()
        while len(data) <= _MAX_BYTES:
            chunk = os.read(descriptor, min(8192, _MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_BYTES or _identity(opened) != _identity(os.fstat(descriptor)):
            raise PythonEnvironmentError("The Python path configuration changed during review.")
        if _identity(opened) != _identity(path.lstat()):
            raise PythonEnvironmentError("The Python path configuration changed during review.")
        return bytes(data), _identity(opened)
    finally:
        os.close(descriptor)


def _dependency_path(entry: str, python_root: Path, runtime_root: Path) -> Path:
    # Dependency DLLs need ordinary absolute paths on supported CPython/Windows
    # builds. Device/UNC spellings and drive-relative paths must not silently
    # introduce another policy while converting the reviewed relative entries.
    if entry.startswith(("\\", "//")) or ":" in entry[2:] or re.match(r"^[A-Za-z]:(?:$|[^\\/])", entry):
        raise PythonEnvironmentError("The Python dependency declaration uses an unsupported path.")
    parts = entry.replace("\\", "/").split("/")
    if any(part.endswith((" ", ".")) and part not in {".", ".."} for part in parts):
        raise PythonEnvironmentError("The Python dependency declaration uses a path alias.")
    if any(character in entry for character in '\x00";*?|<>'):
        raise PythonEnvironmentError("The Python dependency declaration is invalid.")
    candidate = Path(entry.replace("\\", os.sep))
    if not candidate.is_absolute():
        candidate = python_root / candidate
    candidate = Path(os.path.normpath(str(candidate)))
    within_python = candidate == python_root or python_root in candidate.parents
    approved_comfy = candidate == runtime_root and runtime_root.parent == python_root.parent
    if not within_python and not approved_comfy:
        raise PythonEnvironmentError("Python dependencies must stay inside the reviewed installation roots.")
    _checked_path(candidate)
    if not candidate.is_dir() and not candidate.is_file():
        raise PythonEnvironmentError("A declared Python dependency root is unavailable.")
    return candidate


def _plan(python_executable: Path, runtime_root: Path) -> _Plan:
    try:
        executable = _checked_path(python_executable)
        runtime = _checked_path(runtime_root)
        if not executable.is_file() or not runtime.is_dir():
            raise PythonEnvironmentError("The trusted Python installation is unavailable.")
        python_root = executable.parent
        if python_root.name.casefold() == "scripts":
            try:
                (python_root.parent / "pyvenv.cfg").lstat()
            except FileNotFoundError:
                pass
            else:
                # Even a dangling redirector configuration is incompatible;
                # following links here could incorrectly classify it as full Python.
                raise PythonEnvironmentError(
                    "Windows virtual-environment redirectors are unsupported for image generation. "
                    "Choose an actual Python interpreter or a reviewed portable installation."
                )
        candidates: list[Path] = []
        with os.scandir(python_root) as entries:
            for count, entry in enumerate(entries):
                if count >= 4096:
                    raise PythonEnvironmentError("The Python installation directory exceeds the review limit.")
                if entry.name.casefold().startswith("python") and entry.name.casefold().endswith("._pth"):
                    if _PTH_NAME.fullmatch(entry.name) is None:
                        raise PythonEnvironmentError("The embedded Python path configuration is unsupported.")
                    candidates.append(python_root / entry.name)
        if not candidates:
            return _Plan(PythonEnvironmentReview("standard", False, None, None, 0, False))
        if len(candidates) != 1:
            raise PythonEnvironmentError("The embedded Python path configuration is ambiguous.")
        path = candidates[0]
        original, identity = _read_regular(path)
        text = original.decode("utf-8-sig")
        lines = text.splitlines(keepends=True)
        if len(lines) > _MAX_LINES or any(ord(character) < 32 and character not in "\t\r\n" for character in text):
            raise PythonEnvironmentError("The Python path configuration exceeds the supported text format.")
        newline = "\r\n" if "\r\n" in text else "\n"
        output: list[str] = []
        roots: set[Path] = set()
        site_index: int | None = None
        changed = False
        for line in lines:
            body = line.rstrip("\r\n")
            if len(body) > 4096:
                raise PythonEnvironmentError("The Python dependency declaration is too long.")
            if not body.strip() or body.lstrip().startswith("#"):
                output.append(line)
                continue
            if body == "import site":
                if site_index is not None:
                    raise PythonEnvironmentError("The Python path configuration repeats site initialization.")
                site_index = len(output)
                output.append(line)
                continue
            if body != body.strip() or body.startswith("import"):
                raise PythonEnvironmentError("Only the exact import site directive is supported.")
            target = _dependency_path(body, python_root, runtime)
            if target in roots:
                raise PythonEnvironmentError("The Python path configuration repeats a dependency root.")
            roots.add(target)
            absolute = str(target)
            changed = changed or body != absolute
            output.append(absolute + line[len(body):])
        if not roots:
            raise PythonEnvironmentError("The Python path configuration declares no dependency roots.")
        site_packages = python_root / "Lib" / "site-packages"
        adds_site = site_packages.is_dir() and site_packages not in roots
        if adds_site:
            _checked_path(site_packages)
            insertion = site_index if site_index is not None else len(output)
            if insertion > 0 and not output[insertion - 1].endswith(("\r", "\n")):
                output[insertion - 1] += newline
            output.insert(insertion, str(site_packages) + newline)
            roots.add(site_packages)
            changed = True
        prepared_text = "".join(output)
        # Preserve a UTF-8 BOM and every comment; preparation changes only
        # approved dependency declarations and explicit site-packages admission.
        prepared = (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + prepared_text.encode("utf-8")
        if len(prepared) > _MAX_BYTES:
            raise PythonEnvironmentError("The prepared Python path configuration exceeds its byte limit.")
        review = PythonEnvironmentReview("embedded", changed, _digest(original), _digest(prepared), len(roots), adds_site)
        return _Plan(review, path, original, prepared, identity)
    except PythonEnvironmentError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise PythonEnvironmentError("The trusted Python environment could not be reviewed safely.") from None


def check_python_environment(python_executable: Path, runtime_root: Path) -> PythonEnvironmentReview:
    """Validate static launch compatibility without loading Python, models, or GPU."""

    review = _plan(python_executable, runtime_root).review
    if review.requires_preparation:
        raise PythonEnvironmentPreparationRequiredError(_PREPARATION_MESSAGE)
    return review


def _remove_owned(path: Path, identity: tuple[int, int, int, int] | None) -> bool:
    if identity is None:
        return False
    try:
        if _identity(path.lstat()) != identity:
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _write_exclusive(path: Path, data: bytes) -> tuple[int, int, int, int]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    succeeded = False
    identity: tuple[int, int, int, int] | None = None
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(descriptor)
        identity = _identity(os.fstat(descriptor))
        succeeded = True
        return identity
    finally:
        if identity is None:
            try:
                identity = _identity(os.fstat(descriptor))
            except OSError:
                pass
        os.close(descriptor)
        if not succeeded:
            # An interrupted write owns only this newly created file identity;
            # a replacement at the same name must survive failed staging.
            _remove_owned(path, identity)


def _apply(plan: _Plan) -> str:
    assert plan.path is not None
    path = plan.path
    lock = path.with_name(path.name + ".elysia-prepare.lock")
    backup = path.with_name(path.name + ".elysia-backup-" + _digest(plan.original) + ".bak")
    temporary = path.with_name("." + path.name + ".elysia-" + uuid4().hex + ".tmp")
    rollback = temporary.with_name(temporary.name + ".rollback")
    lock_identity: tuple[int, int, int, int] | None = None
    temporary_identity: tuple[int, int, int, int] | None = None
    rollback_identity: tuple[int, int, int, int] | None = None
    published_identity: tuple[int, int, int, int] | None = None
    try:
        # O_EXCL serializes cooperating setup tools. Immediate byte/identity
        # comparison also refuses ordinary concurrent editor changes; this is
        # setup, not a claim to lock the complete dependency installation.
        lock_identity = _write_exclusive(lock, b"Elysia trusted Python preparation\n")
        current, identity = _read_regular(path)
        if current != plan.original or identity != plan.identity:
            raise PythonEnvironmentError("The Python path configuration changed before preparation.")
        if backup.exists():
            if _read_regular(backup)[0] != plan.original:
                raise PythonEnvironmentError("The original Python configuration backup conflicts.")
        else:
            _write_exclusive(backup, plan.original)
        temporary_identity = _write_exclusive(temporary, plan.prepared)
        published_identity = temporary_identity
        if _read_regular(path) != (plan.original, plan.identity):
            raise PythonEnvironmentError("The Python path configuration changed before publication.")
        if _read_regular(temporary) != (plan.prepared, temporary_identity):
            raise PythonEnvironmentError("The staged Python path configuration changed before publication.")
        os.replace(temporary, path)
        if _read_regular(path) != (plan.prepared, published_identity):
            raise PythonEnvironmentError("The prepared Python path configuration changed after publication.")
        return backup.name
    except (OSError, PythonEnvironmentError):
        # A replace adapter can fail after publishing. Restore only the exact
        # new file we own, never a later editor's replacement, and retain the
        # immutable original backup even when rollback itself cannot complete.
        if published_identity is not None:
            try:
                if _read_regular(path) == (plan.prepared, published_identity):
                    rollback_identity = _write_exclusive(rollback, plan.original)
                    if (_read_regular(path) == (plan.prepared, published_identity)
                            and _read_regular(rollback) == (plan.original, rollback_identity)):
                        os.replace(rollback, path)
            except (OSError, PythonEnvironmentError):
                raise PythonEnvironmentError("Python preparation failed; its original backup is retained for recovery.") from None
        raise PythonEnvironmentError("Python preparation failed safely; review the installation and its original backup.") from None
    finally:
        # Names alone do not confer ownership: an editor may have replaced a
        # staged file or lock, and O_EXCL failure never acquired that name.
        _remove_owned(temporary, temporary_identity)
        _remove_owned(rollback, rollback_identity)
        if lock_identity is not None and not _remove_owned(lock, lock_identity):
            raise PythonEnvironmentError("Python preparation retained its setup lock; review the original backup before retrying.") from None


def prepare_python_environment(python_executable: Path, runtime_root: Path, *,
                               apply: bool = False) -> PythonEnvironmentPreparation:
    """Preview or explicitly apply only approved absolute dependency declarations.

    Applying preserves an adjacent digest-named original backup, publishes with
    atomic replacement, and conditionally rolls back publication failure. The
    setup lock coordinates cooperating operators; filesystem APIs cannot make
    the final identity check and replacement one conditional transaction against
    unrelated editors. Run only while installation editors and workers are stopped.
    """

    if type(apply) is not bool:
        raise PythonEnvironmentError("Python preparation requires an explicit boolean apply choice.")
    plan = _plan(python_executable, runtime_root)
    if not apply or not plan.review.requires_preparation:
        return PythonEnvironmentPreparation(plan.review, False, None)
    return PythonEnvironmentPreparation(plan.review, True, _apply(plan))
