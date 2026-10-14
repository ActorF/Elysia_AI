"""Verify selected-root containment, native handle pinning, and bounded Work reads."""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
from typing import Iterator

import pytest

import work_tools.workspace as workspace_module
from work_tools.workspace import (
    WorkspaceBoundary, WorkspaceBoundaryError, validate_relative_path,
)


@pytest.mark.parametrize("value", [
    "../secret.txt", "a/../secret.txt", "a/./b", "/etc/passwd", "C:/private.txt",
    "C:private.txt", "\\\\server\\share\\secret.txt", "\\\\?\\C:\\private.txt",
    "a\\b", "file.txt:private", "", "a//b", "a/", ".", "..", " file.txt",
    "file.txt ", "file.txt.", "file\x00.txt", "file\n.txt", "CON", "nul.txt",
    "LPT1", "com9.data", "COM\u00b9.txt", "AUX", "a/*", "a/?", "a/\"b",
])
def test_relative_paths_reject_windows_aliases_and_traversal(value: str) -> None:
    """Reject portable alias spellings before they can reach any native filesystem API."""

    with pytest.raises(WorkspaceBoundaryError):
        validate_relative_path(value)


def test_relative_path_validation_is_pure_and_preserves_approval_spelling() -> None:
    """Freeze exact safe parameters without normalizing a different approved target."""

    assert validate_relative_path("notes/\u4f60\u597d.txt") == "notes/\u4f60\u597d.txt"
    assert validate_relative_path("", allow_root=True) == ""
    with pytest.raises(WorkspaceBoundaryError):
        validate_relative_path("a/" + "/".join("b" for _ in range(128)))
    with pytest.raises(WorkspaceBoundaryError):
        validate_relative_path("a" * 4097)


def test_selected_root_requires_existing_absolute_directory(tmp_path: Path) -> None:
    """Refuse roots that have no narrow, selectable filesystem scope."""

    for root in (Path("relative"), Path(tmp_path.anchor), tmp_path / "missing"):
        with pytest.raises(WorkspaceBoundaryError):
            WorkspaceBoundary(root)
    file_path = tmp_path / "file.txt"
    file_path.write_text("file", encoding="utf-8")
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceBoundary(file_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows namespaces are platform-specific")
@pytest.mark.parametrize("root", [
    "\\\\server\\share\\folder", "\\\\?\\C:\\folder", "\\\\.\\C:\\folder",
])
def test_windows_root_rejects_network_and_device_namespaces(root: str) -> None:
    """A native picker selection never grants access to UNC or device namespaces."""

    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceBoundary(Path(root))


def test_read_metadata_and_listing_are_bounded_and_do_not_write(tmp_path: Path) -> None:
    """Exercise the real filesystem and verify reads create no journal or temporary files."""

    root = tmp_path / "selected"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "note.txt").write_bytes(b"hello")
    (root / "empty.txt").write_bytes(b"")
    before = {path.relative_to(root): path.stat().st_mtime_ns for path in root.rglob("*")}
    boundary = WorkspaceBoundary(root)
    assert boundary.normalized_root == str(root)
    assert boundary.root_identity == (root.stat().st_dev, root.stat().st_ino)
    assert boundary.read_bytes("nested/note.txt", max_bytes=5) == b"hello"
    assert boundary.read_bytes("empty.txt", max_bytes=1) == b""
    assert boundary.metadata().kind == "directory"
    metadata = boundary.metadata("nested/note.txt")
    assert (metadata.path, metadata.name, metadata.kind, metadata.size_bytes) == (
        "nested/note.txt", "note.txt", "file", 5,
    )
    assert [entry.path for entry in boundary.list_directory()] == ["empty.txt", "nested"]
    assert [entry.path for entry in boundary.list_directory("nested")] == ["nested/note.txt"]
    assert before == {path.relative_to(root): path.stat().st_mtime_ns for path in root.rglob("*")}


def test_read_rejects_excessive_and_nonregular_inputs(tmp_path: Path) -> None:
    """Bounds remain enforced for bool parameters, directories, and absent leaves."""

    (tmp_path / "large.txt").write_bytes(b"12345")
    (tmp_path / "folder").mkdir()
    boundary = WorkspaceBoundary(tmp_path)
    for limit in (0, -1, True, 16 * 1024 * 1024 + 1):
        with pytest.raises(WorkspaceBoundaryError):
            boundary.read_bytes("large.txt", max_bytes=limit)
    for name in ("large.txt", "folder", "missing.txt"):
        with pytest.raises(WorkspaceBoundaryError):
            boundary.read_bytes(name, max_bytes=4)
    with pytest.raises(WorkspaceBoundaryError):
        boundary.metadata("missing.txt")


def test_directory_scan_counts_unsafe_entries_toward_limit(tmp_path: Path) -> None:
    """Skipping disallowed names must not permit an unbounded hostile directory scan."""

    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    boundary = WorkspaceBoundary(tmp_path)
    with pytest.raises(WorkspaceBoundaryError):
        boundary.list_directory(max_entries=1)
    for limit in (0, -1, True, 10_001):
        with pytest.raises(WorkspaceBoundaryError):
            boundary.list_directory(max_entries=limit)


def test_root_identity_survives_persistence_and_refuses_replacement(tmp_path: Path) -> None:
    """An approval for an old directory must not transfer to a new same-named directory."""

    root = tmp_path / "selected"
    root.mkdir()
    boundary = WorkspaceBoundary(root)
    original_identity = boundary.root_identity
    assert WorkspaceBoundary(root, expected_root_identity=original_identity).root_identity == original_identity
    root.rename(tmp_path / "original")
    root.mkdir()
    (root / "new.txt").write_bytes(b"unapproved")
    for operation in (boundary.metadata, boundary.list_directory):
        with pytest.raises(WorkspaceBoundaryError, match="has changed"):
            operation()
    with pytest.raises(WorkspaceBoundaryError, match="has changed"):
        boundary.read_bytes("new.txt")
    with pytest.raises(WorkspaceBoundaryError, match="has changed"):
        WorkspaceBoundary(root, expected_root_identity=original_identity)


def test_parent_capability_expires_and_missing_leaf_is_explicit(tmp_path: Path) -> None:
    """A closed context cannot be reused after the directory handles are released."""

    boundary = WorkspaceBoundary(tmp_path)
    with boundary.guarded_parent("new.txt") as access:
        assert access.stat_leaf() is None
        assert access.path == tmp_path / "new.txt"
    with pytest.raises(WorkspaceBoundaryError, match="has ended"):
        access.stat_leaf()
    with pytest.raises(WorkspaceBoundaryError, match="has ended"):
        with access.open_leaf():
            pass


def test_hardlinked_files_are_not_inherited_from_selected_scope(tmp_path: Path) -> None:
    """Lexical containment must not authorize an alias to an unrelated private file."""

    root = tmp_path / "selected"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_bytes(b"secret")
    os.link(outside, root / "alias.txt")
    boundary = WorkspaceBoundary(root)
    with pytest.raises(WorkspaceBoundaryError, match="hard-link"):
        boundary.read_bytes("alias.txt")
    with pytest.raises(WorkspaceBoundaryError, match="hard-link"):
        boundary.metadata("alias.txt")
    assert boundary.list_directory() == ()


def _make_symlink(source: Path, destination: Path, *, directory: bool) -> None:
    """Skip only a missing native symlink privilege; Windows junction coverage remains separate."""

    try:
        destination.symlink_to(source, target_is_directory=directory)
    except OSError:
        pytest.skip("This account cannot create native symbolic links.")


def test_symlink_roots_ancestors_and_leaves_are_refused(tmp_path: Path) -> None:
    """Neither root selection nor a later nested lookup follows a filesystem redirect."""

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    root = tmp_path / "selected"
    root.mkdir()
    _make_symlink(outside, root / "escape", directory=True)
    _make_symlink(outside / "secret.txt", root / "leaf.txt", directory=False)
    _make_symlink(root, tmp_path / "alias", directory=True)
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceBoundary(tmp_path / "alias")
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceBoundary(tmp_path / "alias" / "escape")
    boundary = WorkspaceBoundary(root)
    for path in ("escape/secret.txt", "leaf.txt"):
        with pytest.raises(WorkspaceBoundaryError):
            boundary.read_bytes(path)
    assert boundary.list_directory() == ()


@pytest.mark.skipif(os.name != "nt", reason="Native junction protection requires Windows")
def test_windows_junction_redirects_are_refused(tmp_path: Path) -> None:
    """Reject directory junctions even where symlink creation requires unavailable privilege."""

    root = tmp_path / "selected"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "escape"), str(outside)],
        capture_output=True, check=False,
    )
    assert result.returncode == 0
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceBoundary(root / "escape")
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceBoundary(root).read_bytes("escape/secret.txt")


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes prevent directory renames")
def test_windows_parent_and_leaf_handles_block_external_mutation(tmp_path: Path) -> None:
    """The guard owns the exact ancestors and file until the read capability is released."""

    root = tmp_path / "selected"
    root.mkdir()
    (root / "folder").mkdir()
    (root / "folder" / "file.txt").write_bytes(b"approved")
    boundary = WorkspaceBoundary(root)
    with boundary.guarded_parent("folder/file.txt") as access:
        with pytest.raises(OSError):
            root.rename(tmp_path / "moved")
        with pytest.raises(OSError):
            (root / "folder").rename(root / "other")
        with access.open_leaf() as descriptor:
            assert os.read(descriptor, 8) == b"approved"
            with pytest.raises(OSError):
                (root / "folder" / "file.txt").write_bytes(b"changed")
            with pytest.raises(OSError):
                (root / "folder" / "file.txt").unlink()
    assert boundary.read_bytes("folder/file.txt") == b"approved"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor addressing has a different native contract")
def test_posix_parent_swap_does_not_redirect_read_to_outside(tmp_path: Path) -> None:
    """A swapped lexical parent cannot redirect a descriptor-relative leaf lookup."""

    root = tmp_path / "selected"
    root.mkdir()
    folder = root / "folder"
    folder.mkdir()
    (folder / "file.txt").write_bytes(b"approved")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_bytes(b"private")
    boundary = WorkspaceBoundary(root)
    with boundary.guarded_parent("folder/file.txt") as access:
        folder.rename(root / "original")
        folder.symlink_to(outside, target_is_directory=True)
        with access.open_leaf() as descriptor:
            assert os.read(descriptor, 8) == b"approved"
    with pytest.raises(WorkspaceBoundaryError):
        boundary.read_bytes("folder/file.txt")


def test_growth_during_read_is_rejected_without_partial_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject adapter races even if the file initially fits the requested byte bound."""

    (tmp_path / "file.txt").write_bytes(b"small")
    original_read = os.read
    calls = 0

    def growing_read(descriptor: int, count: int) -> bytes:
        """Simulate an externally changing input without requiring unsafe concurrent writes."""

        nonlocal calls
        calls += 1
        return b"untrusted" if calls == 1 else original_read(descriptor, count)

    monkeypatch.setattr(workspace_module.os, "read", growing_read)
    with pytest.raises(WorkspaceBoundaryError, match="changed"):
        WorkspaceBoundary(tmp_path).read_bytes("file.txt", max_bytes=5)


def test_failed_leaf_open_closes_parent_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acquisition rollback closes directory guards even when a later leaf fails."""

    boundary = WorkspaceBoundary(tmp_path)
    opened = 0
    closed = 0
    original_directory = workspace_module._opened_directory

    @contextmanager
    def tracked_directory(path: Path, *, parent_fd: int | None = None) -> Iterator[int]:
        """Observe real guard ownership without replacing native security behavior."""

        nonlocal opened, closed
        with original_directory(path, parent_fd=parent_fd) as descriptor:
            opened += 1
            try:
                yield descriptor
            finally:
                closed += 1

    monkeypatch.setattr(workspace_module, "_opened_directory", tracked_directory)
    with pytest.raises(WorkspaceBoundaryError):
        boundary.read_bytes("absent.txt")
    assert opened > 0 and opened == closed
