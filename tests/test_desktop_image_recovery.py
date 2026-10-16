"""Verify confirmed crash-run quarantine preserves bytes and excludes live Backends."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

import desktop_image_recovery as recovery
from desktop_image_settings import DesktopImageSettingsStore
from desktop_image_settings_worker import handle_request
from image_generation.gallery import ImageGalleryStore


def _fixture(tmp_path: Path, *, runs: int = 1) -> tuple[Path, list[Path]]:
    data = tmp_path / "data"
    source = data / "workspace" / "image-runs"
    source.mkdir(parents=True)
    with ImageGalleryStore(source.parent / "gallery"):
        pass
    paths: list[Path] = []
    for number in range(runs):
        path = source / f"00000000-0000-4000-8000-{number + 1:012d}"
        (path / "output").mkdir(parents=True)
        (path / "output" / "private.png").write_bytes(b"preserve every private byte\x00" + bytes([number]))
        (path / "extra.json").write_bytes(b'{"private":"metadata"}\n')
        paths.append(path)
    return data, paths


def _apply(data: Path, token: str) -> int:
    return recovery.quarantine_image_recovery(data, snapshot=token, trusted_confirmation=True)


def _gallery_bytes(data: Path) -> dict[str, bytes]:
    root = data / "workspace" / "gallery"
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


def test_preview_is_read_only_and_apply_preserves_the_complete_run_and_gallery(tmp_path: Path) -> None:
    """Confirmation changes only each run's parent directory, retaining file identities."""

    data, runs = _fixture(tmp_path)
    original = runs[0] / "output" / "private.png"
    identity = original.stat().st_ino
    payload = original.read_bytes()
    gallery = _gallery_bytes(data)
    preview = recovery.preview_image_recovery(data)
    assert preview.run_count == 1 and preview.file_count == 2
    assert preview.byte_size == sum(path.stat().st_size for path in runs[0].rglob("*") if path.is_file())
    assert len(preview.snapshot) == 64
    assert not (data / "workspace" / "image-recovery").exists()
    assert _apply(data, preview.snapshot) == 1
    quarantined = data / "workspace" / "image-recovery" / runs[0].name
    assert not runs[0].exists()
    assert (quarantined / "output" / "private.png").read_bytes() == payload
    assert (quarantined / "output" / "private.png").stat().st_ino == identity
    assert _gallery_bytes(data) == gallery
    assert not recovery.image_recovery_required(data)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        _apply(data, preview.snapshot)


def test_empty_preview_does_not_create_workspace_or_gallery(tmp_path: Path) -> None:
    """A read-only check cannot adopt a missing data root or manufacture ownership."""

    data = tmp_path / "absent-data"
    preview = recovery.preview_image_recovery(data)
    assert (preview.run_count, preview.file_count, preview.byte_size) == (0, 0, 0)
    assert not data.exists()


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_only_literal_native_confirmation_permits_apply(tmp_path: Path, confirmation: object) -> None:
    """Truthy values cannot substitute for the native-dialog decision."""

    data, runs = _fixture(tmp_path)
    preview = recovery.preview_image_recovery(data)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        recovery.quarantine_image_recovery(data, snapshot=preview.snapshot, trusted_confirmation=confirmation)  # type: ignore[arg-type]
    assert runs[0].is_dir()


def test_snapshot_change_and_foreign_quarantine_target_fail_without_moves(tmp_path: Path) -> None:
    """Confirmation names one metadata snapshot and never overwrites an existing target."""

    data, runs = _fixture(tmp_path)
    token = recovery.preview_image_recovery(data).snapshot
    (runs[0] / "extra.json").write_bytes(b"changed after preview")
    with pytest.raises(recovery.DesktopImageRecoveryError):
        _apply(data, token)
    token = recovery.preview_image_recovery(data).snapshot
    foreign = data / "workspace" / "image-recovery" / runs[0].name
    foreign.mkdir(parents=True)
    (foreign / "user.txt").write_bytes(b"foreign original")
    with pytest.raises(recovery.DesktopImageRecoveryError):
        _apply(data, token)
    assert runs[0].is_dir() and (foreign / "user.txt").read_bytes() == b"foreign original"


def test_native_rename_refuses_even_an_empty_late_foreign_destination(tmp_path: Path) -> None:
    """The final native operation has no replacement semantics on supported hosts."""

    source = tmp_path / "source"
    target = tmp_path / "foreign"
    source.mkdir()
    target.mkdir()
    source_inode, target_inode = source.stat().st_ino, target.stat().st_ino
    with pytest.raises(recovery.DesktopImageRecoveryError):
        recovery._rename_exclusive(source, target)
    assert source.stat().st_ino == source_inode and target.stat().st_ino == target_inode


@pytest.mark.parametrize("name", ["unknown-state", ".foreign", "00000000-0000-1000-8000-000000000001"])
def test_unknown_or_non_v4_run_names_are_never_adopted(tmp_path: Path, name: str) -> None:
    """The scratch location alone does not authorize moving unrelated user data."""

    data, _ = _fixture(tmp_path, runs=0)
    unknown = data / "workspace" / "image-runs" / name
    unknown.mkdir()
    (unknown / "user.txt").write_bytes(b"unknown private user data")
    assert recovery.image_recovery_required(data)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        recovery.preview_image_recovery(data)
    assert (unknown / "user.txt").read_bytes() == b"unknown private user data"
    assert not (data / "workspace" / "image-recovery").exists()


def test_hard_linked_regular_content_is_preserved_without_opening_or_unlinking_it(tmp_path: Path) -> None:
    """A containing-directory rename preserves both hard-link references and bytes."""

    data, runs = _fixture(tmp_path)
    external = tmp_path / "external-user-file"
    external.write_bytes(b"outside hard-link reference")
    os.link(external, runs[0] / "linked.dat")
    preview = recovery.preview_image_recovery(data)
    _apply(data, preview.snapshot)
    linked = data / "workspace" / "image-recovery" / runs[0].name / "linked.dat"
    assert external.read_bytes() == linked.read_bytes() == b"outside hard-link reference"
    assert external.stat().st_ino == linked.stat().st_ino


def test_junction_or_symlink_tree_is_rejected_without_following_it(tmp_path: Path) -> None:
    """Directory redirects cannot turn a fixed run into an arbitrary path move."""

    data, runs = _fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user.txt").write_bytes(b"outside unchanged")
    link = runs[0] / "redirect"
    if os.name == "nt":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, timeout=10)
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        recovery.preview_image_recovery(data)
    assert (outside / "user.txt").read_bytes() == b"outside unchanged"


def test_gallery_lease_blocks_apply_while_preview_remains_read_only(tmp_path: Path) -> None:
    """An existing Backend's native byte lock prevents quarantine of its active runs."""

    data, runs = _fixture(tmp_path)
    with ImageGalleryStore(data / "workspace" / "gallery"):
        preview = recovery.preview_image_recovery(data)
        with pytest.raises(recovery.DesktopImageRecoveryError):
            _apply(data, preview.snapshot)
        assert runs[0].is_dir()
    assert _apply(data, preview.snapshot) == 1


def test_another_backend_process_gallery_lease_excludes_recovery(tmp_path: Path) -> None:
    """The existing sentinel lease excludes an actual separately owned Python process."""

    data, runs = _fixture(tmp_path)
    source = (
        "from pathlib import Path\n"
        "from image_generation.gallery import ImageGalleryStore\n"
        "import sys\n"
        "with ImageGalleryStore(Path(sys.argv[1])):\n"
        "    print('owned', flush=True)\n"
        "    sys.stdin.buffer.read(1)\n"
    )
    child = subprocess.Popen([sys.executable, "-u", "-c", source, str(data / "workspace" / "gallery")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=Path(__file__).resolve().parents[1])
    try:
        assert child.stdout is not None
        assert child.stdout.readline().rstrip(b"\r\n") == b"owned"
        preview = recovery.preview_image_recovery(data)
        with pytest.raises(recovery.DesktopImageRecoveryError):
            _apply(data, preview.snapshot)
        assert runs[0].exists()
        output, error = child.communicate(b"q", timeout=10)
        assert child.returncode == 0 and output == b"" and error == b""
        assert _apply(data, preview.snapshot) == 1
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_partial_native_failure_preserves_every_directory_and_allows_a_new_preview_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completed moves stay quarantined and unmoved sources retain authoritative bytes."""

    data, runs = _fixture(tmp_path, runs=2)
    preview = recovery.preview_image_recovery(data)
    rename = recovery._rename_exclusive
    calls = 0

    def _fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private native failure")
        rename(source, destination)

    monkeypatch.setattr(recovery, "_rename_exclusive", _fail_second)
    with pytest.raises(recovery.DesktopImageRecoveryError) as error:
        _apply(data, preview.snapshot)
    assert "private native" not in str(error.value)
    quarantine = data / "workspace" / "image-recovery"
    assert (quarantine / runs[0].name / "extra.json").read_bytes() == b'{"private":"metadata"}\n'
    assert (runs[1] / "extra.json").read_bytes() == b'{"private":"metadata"}\n'
    next_preview = recovery.preview_image_recovery(data)
    assert next_preview.run_count == 1 and next_preview.snapshot != preview.snapshot
    monkeypatch.setattr(recovery, "_rename_exclusive", rename)
    assert _apply(data, next_preview.snapshot) == 1
    assert len(list(quarantine.iterdir())) == 2


def test_ambiguous_failure_after_rename_does_not_move_or_delete_anything_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native error after success cannot cause an unsafe compensating overwrite."""

    data, runs = _fixture(tmp_path)
    token = recovery.preview_image_recovery(data).snapshot
    rename = recovery._rename_exclusive

    def _fail_after_move(source: Path, destination: Path) -> None:
        rename(source, destination)
        raise OSError("uncertain completion")

    monkeypatch.setattr(recovery, "_rename_exclusive", _fail_after_move)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        _apply(data, token)
    assert not runs[0].exists()
    assert (data / "workspace" / "image-recovery" / runs[0].name / "extra.json").is_file()
    assert recovery.preview_image_recovery(data).run_count == 0


def test_missing_owned_gallery_and_bounded_inventory_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovery requires actual Gallery ownership and bounded ordinary source inventory."""

    data, runs = _fixture(tmp_path, runs=2)
    preview = recovery.preview_image_recovery(data)
    marker = data / "workspace" / "gallery" / ".gallery.owner"
    marker.unlink()
    with pytest.raises(recovery.DesktopImageRecoveryError):
        _apply(data, preview.snapshot)
    assert all(path.exists() for path in runs)
    monkeypatch.setattr(recovery, "_MAX_RUNS", 1)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        recovery.preview_image_recovery(data)


@pytest.mark.parametrize(("policy", "limit"), [("_MAX_ENTRIES", 3), ("_MAX_DEPTH", 0), ("_MAX_INTEGER", 1)])
def test_tree_and_size_budgets_refuse_without_any_partial_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, limit: int,
) -> None:
    """Deep, broad, or excessive metadata never expands the bounded recovery scan."""

    data, runs = _fixture(tmp_path)
    monkeypatch.setattr(recovery, policy, limit)
    with pytest.raises(recovery.DesktopImageRecoveryError):
        recovery.preview_image_recovery(data)
    assert runs[0].is_dir() and not (data / "workspace" / "image-recovery").exists()


def test_worker_protocol_is_closed_and_never_accepts_source_or_destination_paths(tmp_path: Path) -> None:
    """Only Main's snapshot and literal confirmation may select the fixed recovery operation."""

    data, runs = _fixture(tmp_path)
    preview = handle_request({"op": "recovery.preview"}, data)
    assert preview["kind"] == "image.recovery.preview"
    assert str(data) not in str(preview) and runs[0].name not in str(preview)
    for request in [
        {"op": "recovery.preview", "path": str(tmp_path)},
        {"op": "recovery.apply", "snapshot": preview["snapshot"], "trustedConfirmation": 1},
        {"op": "recovery.apply", "snapshot": preview["snapshot"], "trustedConfirmation": True, "destination": str(tmp_path)},
    ]:
        with pytest.raises(ValueError):
            handle_request(request, data)
    assert DesktopImageSettingsStore(data).status().state == "recovery_required"
    result = handle_request({"op": "recovery.apply", "snapshot": preview["snapshot"], "trustedConfirmation": True}, data)
    assert result["kind"] == "image.recovery.result" and result["quarantinedCount"] == 1
    assert "snapshot" not in result
    assert DesktopImageSettingsStore(data).status().state == "unconfigured"


def test_private_worker_subprocess_returns_a_bounded_preview_without_loading_a_model(tmp_path: Path) -> None:
    """The actual private JSON transport exposes counts without filesystem names."""

    data, _ = _fixture(tmp_path)
    result = subprocess.run([sys.executable, "desktop_image_settings_worker.py", "--data-root", str(data)],
                            input=b'{"op":"recovery.preview"}\n', capture_output=True, timeout=20,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0 and result.stderr == b""
    assert b'"kind":"image.recovery.preview"' in result.stdout
    assert str(data).encode() not in result.stdout and len(result.stdout) < 512
