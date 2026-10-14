"""Test secure creation and exact cleanup of private image-runtime trees."""

from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
import uuid

import pytest

from image_generation import _private_run_directory as private_runs


def _create_directory_redirect(link: Path, target: Path) -> None:
    """Create a portable directory redirect for cleanup rejection tests."""

    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("This host does not allow test junctions.")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not allow test directory symlinks.")


def _remove_directory_redirect(link: Path) -> None:
    """Remove a test redirect itself without touching its external target."""

    if link.is_symlink():
        link.unlink()
    elif link.exists():
        link.rmdir()


def test_create_builds_one_uuid_direct_child_with_fixed_private_layout(
    tmp_path: Path,
) -> None:
    """Create the complete fixed layout as a canonical UUID direct child."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()

    directory = private_runs.create_private_run_directory(trusted_root)

    assert directory.root.parent == trusted_root
    assert str(uuid.UUID(directory.root.name)) == directory.root.name
    assert uuid.UUID(directory.root.name).version == 4
    assert {
        child.name for child in directory.root.iterdir()
    } == {"input", "output", "temp", "user", "cache", "frontend"}
    for child in (
        directory.input,
        directory.output,
        directory.temp,
        directory.user,
        directory.cache,
        directory.frontend,
    ):
        assert child.is_dir()
        assert child.parent == directory.root


def test_remove_deletes_only_the_selected_run_and_preserves_siblings(
    tmp_path: Path,
) -> None:
    """Recursively delete one run without changing adjacent trusted-root data."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    selected = private_runs.create_private_run_directory(trusted_root)
    other_run = private_runs.create_private_run_directory(trusted_root)
    unrelated = trusted_root / "keep.txt"
    unrelated.write_text("sibling", encoding="utf-8")
    nested = selected.output / "nested" / "deeper"
    nested.mkdir(parents=True)
    (nested / "result.bin").write_bytes(b"private output")
    (selected.input / "request.txt").write_text("prompt", encoding="utf-8")

    private_runs.remove_private_run_directory(selected)

    assert not selected.root.exists()
    assert other_run.root.is_dir()
    assert unrelated.read_text(encoding="utf-8") == "sibling"


def test_remove_rejects_a_noncanonical_layout_without_mutation(
    tmp_path: Path,
) -> None:
    """Do not let altered path fields widen cleanup beyond the issued layout."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    malformed = replace(directory, cache=outside)

    with pytest.raises(
        private_runs.PrivateRunDirectoryError,
        match="layout is invalid",
    ):
        private_runs.remove_private_run_directory(malformed)

    assert directory.root.is_dir()
    assert outside.is_dir()


def test_remove_rejects_a_redirected_trusted_root_without_deleting_target(
    tmp_path: Path,
) -> None:
    """Reject a trust boundary replaced by a redirect after run creation."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    relocated_root = tmp_path / "relocated"
    trusted_root.rename(relocated_root)
    _create_directory_redirect(trusted_root, relocated_root)

    try:
        with pytest.raises(private_runs.PrivateRunDirectoryError):
            private_runs.remove_private_run_directory(directory)
        assert (relocated_root / directory.root.name).is_dir()
    finally:
        _remove_directory_redirect(trusted_root)


def test_remove_rejects_a_redirected_run_target_without_deleting_target(
    tmp_path: Path,
) -> None:
    """Reject a UUID target replaced by a redirect and preserve its contents."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    relocated_run = trusted_root / "relocated-run"
    directory.root.rename(relocated_run)
    _create_directory_redirect(directory.root, relocated_run)

    try:
        with pytest.raises(private_runs.PrivateRunDirectoryError):
            private_runs.remove_private_run_directory(directory)
        assert (relocated_run / "output").is_dir()
    finally:
        _remove_directory_redirect(directory.root)


def test_remove_rejects_a_descendant_redirect_before_any_mutation(
    tmp_path: Path,
) -> None:
    """Preflight the full tree so one redirect cannot cause partial cleanup."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    retained = directory.input / "retained.txt"
    retained.write_text("keep", encoding="utf-8")
    external = tmp_path / "external"
    redirect = directory.output / "redirect"
    _create_directory_redirect(redirect, external)
    (external / "outside.txt").write_text("outside", encoding="utf-8")

    try:
        with pytest.raises(
            private_runs.PrivateRunDirectoryError,
            match="link or reparse point",
        ):
            private_runs.remove_private_run_directory(directory)
        assert retained.read_text(encoding="utf-8") == "keep"
        assert (external / "outside.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _remove_directory_redirect(redirect)


def test_remove_rejects_reparse_metadata_on_every_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the Windows reparse defense even on a POSIX test host."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    marked = directory.cache / "runtime.bin"
    marked.write_bytes(b"cache")
    original_is_reparse = private_runs._is_reparse

    def is_selected_reparse(info: os.stat_result) -> bool:
        """Mark the selected regular-file shape while preserving real checks."""

        return (
            stat.S_ISREG(info.st_mode) and info.st_size == len(b"cache")
        ) or original_is_reparse(info)

    monkeypatch.setattr(private_runs, "_is_reparse", is_selected_reparse)

    with pytest.raises(
        private_runs.PrivateRunDirectoryError,
        match="link or reparse point",
    ):
        private_runs.remove_private_run_directory(directory)

    assert marked.read_bytes() == b"cache"


def test_remove_rejects_an_unbounded_entry_inventory_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound hostile runtime output before collecting an unbounded path list."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    retained = directory.input / "retained.txt"
    retained.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(private_runs, "_MAX_TREE_ENTRIES", 6)

    with pytest.raises(
        private_runs.PrivateRunDirectoryError,
        match="cleanup bounds",
    ):
        private_runs.remove_private_run_directory(directory)

    assert retained.read_text(encoding="utf-8") == "keep"


def test_remove_rejects_excessive_tree_depth_before_mutation(
    tmp_path: Path,
) -> None:
    """Bound descendant walks created by a compromised local runtime."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    retained = directory.input / "retained.txt"
    retained.write_text("keep", encoding="utf-8")
    deepest = directory.output
    for _index in range(private_runs._MAX_TREE_DEPTH):
        deepest = deepest / "d"
        deepest.mkdir()

    with pytest.raises(
        private_runs.PrivateRunDirectoryError,
        match="cleanup bounds",
    ):
        private_runs.remove_private_run_directory(directory)

    assert retained.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-node regression")
def test_remove_rejects_a_special_node_before_any_mutation(tmp_path: Path) -> None:
    """Reject a FIFO because cleanup permits only regular files and directories."""

    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    directory = private_runs.create_private_run_directory(trusted_root)
    retained = directory.input / "retained.txt"
    retained.write_text("keep", encoding="utf-8")
    fifo = directory.temp / "runtime.fifo"
    os.mkfifo(fifo)

    with pytest.raises(
        private_runs.PrivateRunDirectoryError,
        match="special filesystem node",
    ):
        private_runs.remove_private_run_directory(directory)

    assert retained.read_text(encoding="utf-8") == "keep"
    assert fifo.exists()
