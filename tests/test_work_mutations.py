"""Verify conditional Work writes and native handle-addressed move/delete ownership."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from typing import Iterator

import pytest

import work_tools.mutations as mutation_module
from work_tools.mutations import WorkspaceMutations
from work_tools.workspace import WorkspaceBoundary, WorkspaceBoundaryError, WorkspaceParent


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_create_replace_move_and_remove_verify_actual_files(tmp_path: Path) -> None:
    """Run the complete native file lifecycle, including shorter in-place replacement."""

    (tmp_path / "folder").mkdir()
    boundary = WorkspaceBoundary(tmp_path)
    mutations = WorkspaceMutations(boundary)
    assert mutations.create("note.txt", b"original longer content") == _digest(b"original longer content")
    original_identity = boundary.metadata("note.txt").identity
    assert mutations.replace("note.txt", b"new", _digest(b"original longer content")) == _digest(b"new")
    assert boundary.read_bytes("note.txt") == b"new"
    assert boundary.metadata("note.txt").identity == original_identity
    assert mutations.move("note.txt", "folder/moved.txt", _digest(b"new")) == _digest(b"new")
    assert not (tmp_path / "note.txt").exists()
    assert boundary.read_bytes("folder/moved.txt") == b"new"
    assert mutations.remove("folder/moved.txt", _digest(b"new")) == _digest(b"new")
    assert not (tmp_path / "folder" / "moved.txt").exists()


def test_empty_payload_is_valid_for_creation_and_replacement(tmp_path: Path) -> None:
    """A zero-byte file is still a complete verified mutation, including truncation to empty."""

    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    assert mutations.create("empty.txt", b"") == _digest(b"")
    assert mutations.replace("empty.txt", b"content", _digest(b"")) == _digest(b"content")
    assert mutations.replace("empty.txt", b"", _digest(b"content")) == _digest(b"")
    assert (tmp_path / "empty.txt").read_bytes() == b""


@pytest.mark.parametrize("operation", ["replace", "move", "remove"])
def test_stale_preview_refuses_every_mutation_without_changing_bytes(tmp_path: Path, operation: str) -> None:
    """An approval for old content cannot authorize a changed target's mutation."""

    target = tmp_path / "target.txt"
    target.write_bytes(b"changed after preview")
    before = target.stat()
    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    with pytest.raises(WorkspaceBoundaryError, match="changed after preview"):
        if operation == "replace":
            mutations.replace("target.txt", b"new", _digest(b"old content"))
        elif operation == "move":
            mutations.move("target.txt", "destination.txt", _digest(b"old content"))
        else:
            mutations.remove("target.txt", _digest(b"old content"))
    assert target.read_bytes() == b"changed after preview"
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert not (tmp_path / "destination.txt").exists()


def test_create_and_move_never_overwrite_existing_destination(tmp_path: Path) -> None:
    """Destination collision is a refusal for both previewed and newly created names."""

    (tmp_path / "source.txt").write_bytes(b"source")
    (tmp_path / "destination.txt").write_bytes(b"private destination")
    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    with pytest.raises(WorkspaceBoundaryError, match="already exists"):
        mutations.create("destination.txt", b"replacement")
    with pytest.raises(WorkspaceBoundaryError, match="already exists"):
        mutations.move("source.txt", "destination.txt", _digest(b"source"))
    assert (tmp_path / "destination.txt").read_bytes() == b"private destination"
    assert (tmp_path / "source.txt").read_bytes() == b"source"


def test_invalid_arguments_are_refused_before_creating_files(tmp_path: Path) -> None:
    """Malformed digests, nonbytes payloads, and excessive bytes never reach a write handle."""

    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    for data in (bytearray(b"mutable"), b"x" * (16 * 1024 * 1024 + 1)):
        with pytest.raises(WorkspaceBoundaryError):
            mutations.create("new.txt", data)  # type: ignore[arg-type]
    for expected in ("", "A" * 64, "0" * 63):
        with pytest.raises(WorkspaceBoundaryError):
            mutations.replace("new.txt", b"new", expected)
    with pytest.raises(WorkspaceBoundaryError):
        mutations.create("../outside.txt", b"outside")
    assert list(tmp_path.iterdir()) == []


def test_directory_recursion_and_hardlink_mutations_are_unavailable(tmp_path: Path) -> None:
    """Only single regular files are eligible; aliases and directory deletion remain outside this API."""

    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "file.txt").write_bytes(b"keep")
    source = tmp_path / "source.txt"
    source.write_bytes(b"source")
    os.link(source, tmp_path / "alias.txt")
    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    for name in ("folder", "alias.txt"):
        with pytest.raises(WorkspaceBoundaryError):
            mutations.remove(name, _digest(b"source"))
        with pytest.raises(WorkspaceBoundaryError):
            mutations.replace(name, b"new", _digest(b"source"))
    assert (folder / "file.txt").read_bytes() == b"keep"
    assert source.read_bytes() == b"source"


def test_creation_race_uses_exclusive_native_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An intervening file creation cannot turn a new-file approval into an overwrite."""

    original_descriptor = mutation_module._mutation_descriptor

    @contextmanager
    def collision_descriptor(access: WorkspaceParent, *, create: bool = False, delete_access: bool = False) -> Iterator[int]:
        """Insert a destination only after the guard's absence check, then keep real native semantics."""

        if create:
            access.path.write_bytes(b"intervening file")
        with original_descriptor(access, create=create, delete_access=delete_access) as descriptor:
            yield descriptor

    monkeypatch.setattr(mutation_module, "_mutation_descriptor", collision_descriptor)
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceMutations(WorkspaceBoundary(tmp_path)).create("new.txt", b"approved")
    assert (tmp_path / "new.txt").read_bytes() == b"intervening file"


def test_partial_system_writes_are_completed_and_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The write contract survives short native writes without assuming one call writes all bytes."""

    original_write = os.write

    def short_write(descriptor: int, data: bytes | memoryview) -> int:
        """Limit each real write to force multiple descriptor-pinned iterations."""

        return original_write(descriptor, data[:2])

    monkeypatch.setattr(mutation_module.os, "write", short_write)
    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    assert mutations.create("new.txt", b"long payload") == _digest(b"long payload")
    assert (tmp_path / "new.txt").read_bytes() == b"long payload"


def test_write_failure_surfaces_partial_state_for_durable_service_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The primitive reports failure and releases ownership; a journaled backup must restore interrupted bytes."""

    target = tmp_path / "target.txt"
    target.write_bytes(b"original")
    original_write = os.write
    calls = 0

    def failing_write(descriptor: int, data: bytes | memoryview) -> int:
        """Fail after one real partial write to represent disk exhaustion during execution."""

        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, data[:2])
        raise OSError("simulated disk failure")

    monkeypatch.setattr(mutation_module.os, "write", failing_write)
    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    with pytest.raises(WorkspaceBoundaryError):
        mutations.replace("target.txt", b"replacement", _digest(b"original"))
    assert target.read_bytes() == b"reiginal"
    # Failure releases the native handle so the service can subsequently use
    # its durable backup. The primitive never falsely reports rolled-back data.
    target.write_bytes(b"original")
    assert target.read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle rename/delete is platform-specific")
def test_windows_move_and_delete_keep_exclusive_source_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A same-user concurrent source swap cannot redirect the approved native move or deletion."""

    source = tmp_path / "source.txt"
    source.write_bytes(b"approved")
    original_rename = mutation_module._windows_rename
    original_remove = mutation_module._windows_remove

    def rename_owned(descriptor: int, destination: WorkspaceParent) -> None:
        """Try replacing the source while its approved handle is owned exclusively."""

        with pytest.raises(OSError):
            source.rename(tmp_path / "unapproved.txt")
        original_rename(descriptor, destination)

    def remove_owned(descriptor: int) -> None:
        """Try deleting the source name before the owned-handle disposition request."""

        with pytest.raises(OSError):
            (tmp_path / "moved.txt").unlink()
        original_remove(descriptor)

    monkeypatch.setattr(mutation_module, "_windows_rename", rename_owned)
    monkeypatch.setattr(mutation_module, "_windows_remove", remove_owned)
    mutations = WorkspaceMutations(WorkspaceBoundary(tmp_path))
    mutations.move("source.txt", "moved.txt", _digest(b"approved"))
    assert (tmp_path / "moved.txt").read_bytes() == b"approved"
    mutations.remove("moved.txt", _digest(b"approved"))
    assert not (tmp_path / "moved.txt").exists()
    assert not (tmp_path / "unapproved.txt").exists()


def test_move_race_does_not_replace_late_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Native rename and POSIX link both refuse a destination created after preflight."""

    (tmp_path / "source.txt").write_bytes(b"source")
    original_verify = mutation_module._verify_expected

    def create_destination_after_digest(descriptor: int, expected_sha256: str) -> None:
        """Interleave an unrelated destination creation immediately before the native mutation."""

        original_verify(descriptor, expected_sha256)
        (tmp_path / "late.txt").write_bytes(b"late private content")

    monkeypatch.setattr(mutation_module, "_verify_expected", create_destination_after_digest)
    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceMutations(WorkspaceBoundary(tmp_path)).move("source.txt", "late.txt", _digest(b"source"))
    assert (tmp_path / "late.txt").read_bytes() == b"late private content"
    assert (tmp_path / "source.txt").read_bytes() == b"source"
