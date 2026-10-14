"""Verify bounded, recoverable, path-free generated-image Gallery storage."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import traceback
from typing import Any
from uuid import uuid4
import zlib

import pytest

import image_generation.gallery as gallery_module
from image_generation.gallery import (
    ImageGalleryLimitError,
    ImageGalleryNotFoundError,
    ImageGalleryStorageError,
    ImageGalleryStore,
    ImageGalleryValidationError,
    MAX_PRIVATE_PROMPT_CODEPOINTS,
)
from image_generation.png import PNG_SIGNATURE, canonicalize_png
from image_generation.service import GeneratedImageResult


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """Encode one test PNG chunk with its required CRC."""

    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", checksum)
    )


def _png(width: int = 2, height: int = 2, *, value: int = 17) -> bytes:
    """Return a minimal non-interlaced RGB PNG accepted by the canonicalizer."""

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(
        b"\0" + bytes((value, value + 1, value + 2)) * width
        for _ in range(height)
    )
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _result(
    *,
    created_at: datetime | None = None,
    value: int = 17,
) -> GeneratedImageResult:
    """Build one realistic verified generation result for Gallery tests."""

    image = canonicalize_png(_png(value=value), expected_width=2, expected_height=2)
    return GeneratedImageResult(
        job_id=str(uuid4()),
        image=image,
        profile_id="anime-local",
        profile_digest="a" * 64,
        workflow_id="txt2img-v1",
        workflow_version=1,
        workflow_digest="b" * 64,
        seed=42,
        created_at=created_at or datetime(2026, 10, 14, tzinfo=timezone.utc),
    )


def _artifact_directory(root: Path) -> Path:
    """Return the sole live artifact directory from white-box storage state."""

    matches = tuple(root.glob("image_*"))
    assert len(matches) == 1
    return matches[0]


def _manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    """Load the sole private manifest for controlled tampering assertions."""

    path = _artifact_directory(root) / "manifest.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_create_list_get_open_and_delete_keep_public_contract_path_free(
    tmp_path: Path,
) -> None:
    """Round-trip one image while confining prompt and paths to private storage."""

    root = tmp_path / "workspace" / "gallery"
    prompt = "private full prompt: blue hair"
    with ImageGalleryStore(root) as store:
        item = store.create(_result(), private_prompt=prompt)

        assert item.artifact_id.startswith("gallery_")
        assert not hasattr(item, "path")
        assert not hasattr(item, "prompt")
        assert store.list() == (item,)
        assert store.get(item.artifact_id) == item
        opened = store.open_verified(item.artifact_id)
        assert opened.sha256 == item.sha256
        assert opened.data == (_artifact_directory(root) / "image.png").read_bytes()

        index_text = (root / "index.json").read_text(encoding="utf-8")
        assert prompt not in index_text
        _, manifest = _manifest(root)
        assert manifest["private_prompt"] == prompt
        assert set(path.name for path in _artifact_directory(root).iterdir()) == {
            "image.png",
            "manifest.json",
        }

        store.delete(item.artifact_id)
        assert store.list() == ()
        with pytest.raises(ImageGalleryNotFoundError, match="not found"):
            store.get(item.artifact_id)

    assert not tuple(root.glob("image_*"))
    assert not tuple(root.glob(".deleting_*"))


def test_list_is_newest_first_and_pagination_is_bounded(tmp_path: Path) -> None:
    """Keep deterministic chronology while rejecting unbounded page requests."""

    root = tmp_path / "gallery"
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with ImageGalleryStore(root) as store:
        first = store.create(_result(created_at=base, value=10))
        second = store.create(
            _result(created_at=base + timedelta(seconds=1), value=20)
        )
        third = store.create(
            _result(created_at=base + timedelta(seconds=2), value=30)
        )

        assert store.list() == (third, second, first)
        assert store.list(limit=1, offset=1) == (second,)
        assert store.list(limit=2, offset=10) == ()
        for invalid in (0, -1, 257, True):
            with pytest.raises(ImageGalleryValidationError, match="limit"):
                store.list(limit=invalid)  # type: ignore[arg-type]


def test_list_get_and_create_do_not_reread_prior_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open from manifests once and never reread prior PNG bodies for metadata."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        first = store.create(_result(value=10))

    original_read = ImageGalleryStore._read_bounded_file

    def reject_image_body_read(
        store: ImageGalleryStore,
        path: Path,
        *,
        maximum_bytes: int,
    ) -> bytes:
        """Fail if reopen or a metadata-only operation reads a prior PNG."""

        if path.name == "image.png":
            raise AssertionError("prior PNG body was reread")
        return original_read(
            store,
            path,
            maximum_bytes=maximum_bytes,
        )

    monkeypatch.setattr(
        ImageGalleryStore,
        "_read_bounded_file",
        reject_image_body_read,
    )
    with ImageGalleryStore(root) as store:
        assert store.list() == (first,)
        assert store.get(first.artifact_id) == first
        second = store.create(_result(value=20))
        assert {item.artifact_id for item in store.list()} == {
            first.artifact_id,
            second.artifact_id,
        }


def test_restart_rebuilds_a_missing_or_stale_index_from_manifests(
    tmp_path: Path,
) -> None:
    """Treat the cache as disposable while artifact directories remain canonical."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        item = store.create(_result())
    (root / "index.json").unlink()

    with ImageGalleryStore(root) as reopened:
        assert reopened.get(item.artifact_id) == item
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    index["items"] = []
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")

    with ImageGalleryStore(root) as reopened:
        assert reopened.list() == (item,)
    rebuilt = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert rebuilt["items"][0]["artifact_id"] == item.artifact_id


def test_corrupt_index_is_preserved_before_rebuild(tmp_path: Path) -> None:
    """Retain bounded forensic evidence without blocking authoritative recovery."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        item = store.create(_result())
    (root / "index.json").write_bytes(b'{"items": [broken')

    with ImageGalleryStore(root) as reopened:
        assert reopened.get(item.artifact_id) == item

    backups = tuple(root.glob(".index.corrupt_*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b'{"items": [broken'
    assert json.loads((root / "index.json").read_text(encoding="utf-8"))[
        "items"
    ]


def test_index_rebuild_failure_does_not_block_authoritative_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open verified artifacts even when the disposable index cannot be written."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        item = store.create(_result())
    (root / "index.json").unlink()

    def fail_index_write(_store: ImageGalleryStore) -> None:
        """Simulate a cache-only write denial during reopen."""

        raise PermissionError(str(root / "index.json"))

    monkeypatch.setattr(
        ImageGalleryStore,
        "_write_index_required",
        fail_index_write,
    )
    with ImageGalleryStore(root) as reopened:
        assert reopened.list() == (item,)
        assert reopened.open_verified(item.artifact_id).sha256 == item.sha256


def test_boolean_schema_versions_are_never_accepted_as_integer_one(
    tmp_path: Path,
) -> None:
    """Reject Python's bool-as-int alias in authoritative and cache schemas."""

    manifest_root = tmp_path / "manifest"
    with ImageGalleryStore(manifest_root) as store:
        store.create(_result())
    manifest_path, manifest = _manifest(manifest_root)
    manifest["schema_version"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ImageGalleryStorageError, match="version"):
        ImageGalleryStore(manifest_root)

    index_root = tmp_path / "index"
    with ImageGalleryStore(index_root) as store:
        item = store.create(_result())
    index_path = index_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["schema_version"] = True
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with ImageGalleryStore(index_root) as reopened:
        assert reopened.get(item.artifact_id) == item
    backups = tuple(index_root.glob(".index.corrupt_*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))[
        "schema_version"
    ] is True


def test_startup_discards_closed_staging_and_committed_tombstones(
    tmp_path: Path,
) -> None:
    """Converge interrupted create and delete transactions by exact grammar."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        store.create(_result())
    artifact = _artifact_directory(root)
    tombstone = root / artifact.name.replace("image_", ".deleting_", 1)
    os.replace(artifact, tombstone)
    staging = root / f".staging_{uuid4().hex}"
    staging.mkdir()
    (staging / "image.png").write_bytes(_png())

    with ImageGalleryStore(root) as recovered:
        assert recovered.list() == ()

    assert not tombstone.exists()
    assert not staging.exists()


@pytest.mark.parametrize(
    "remaining_names",
    [(), ("image.png",), ("manifest.json",), ("image.png", "manifest.json")],
)
def test_startup_converges_partially_purged_tombstones(
    tmp_path: Path,
    remaining_names: tuple[str, ...],
) -> None:
    """Finish deletion after crashes between either canonical child unlink."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        store.create(_result())
    artifact = _artifact_directory(root)
    tombstone = root / artifact.name.replace("image_", ".deleting_", 1)
    os.replace(artifact, tombstone)
    for child_name in ("image.png", "manifest.json"):
        if child_name not in remaining_names:
            (tombstone / child_name).unlink()

    with ImageGalleryStore(root) as recovered:
        assert recovered.list() == ()

    assert not tombstone.exists()


def test_unowned_wide_root_is_never_modified_before_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspect the complete bounded inventory before creating marker or lock."""

    root = tmp_path / "not-a-gallery"
    root.mkdir()
    sentinels = tuple(root / f"user-{index}.txt" for index in range(3))
    for sentinel in sentinels:
        sentinel.write_text(sentinel.name, encoding="utf-8")
    monkeypatch.setattr(gallery_module, "MAX_ROOT_ENTRIES", 2)

    with pytest.raises(ImageGalleryLimitError, match="too many"):
        ImageGalleryStore(root)

    assert {entry.name for entry in root.iterdir()} == {
        sentinel.name for sentinel in sentinels
    }
    assert not (root / ".gallery.owner").exists()
    assert not (root / ".gallery.lock").exists()


@pytest.mark.parametrize(
    "name",
    [".staging_bad", ".deleting_bad", ".unknown", "notes.txt", "image_bad"],
)
def test_unknown_root_entries_fail_closed_without_deletion(
    tmp_path: Path,
    name: str,
) -> None:
    """Never reinterpret unknown or malformed names as recoverable transactions."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root):
        pass
    candidate = root / name
    if "." in name and not name.startswith("image_"):
        candidate.write_text("keep", encoding="utf-8")
    else:
        candidate.mkdir()

    with pytest.raises(ImageGalleryStorageError, match="unknown"):
        ImageGalleryStore(root)

    assert candidate.exists()


def test_malformed_closed_staging_fails_without_traversing_unknown_children(
    tmp_path: Path,
) -> None:
    """Purge only known flat transaction contents and preserve suspicious data."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root):
        pass
    staging = root / f".staging_{uuid4().hex}"
    staging.mkdir()
    suspicious = staging / "unexpected.bin"
    suspicious.write_bytes(b"keep")

    with pytest.raises(ImageGalleryStorageError, match="unknown"):
        ImageGalleryStore(root)

    assert suspicious.read_bytes() == b"keep"


def test_selected_open_detects_png_tampering_without_blocking_metadata_reads(
    tmp_path: Path,
) -> None:
    """Defer PNG body verification to selected open while metadata stays usable."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        item = store.create(_result())
    image = _artifact_directory(root) / "image.png"
    data = bytearray(image.read_bytes())
    data[-1] ^= 1
    image.write_bytes(data)

    with ImageGalleryStore(root) as store:
        assert store.list() == (item,)
        assert store.get(item.artifact_id) == item
        with pytest.raises(ImageGalleryStorageError, match="seal|bytes"):
            store.open_verified(item.artifact_id)


def test_strict_manifest_rejects_unknown_fields_and_duplicate_members(
    tmp_path: Path,
) -> None:
    """Prevent schema smuggling through extensions or duplicate JSON keys."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        store.create(_result())
    path, manifest = _manifest(root)
    manifest["unexpected"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ImageGalleryStorageError, match="manifest"):
        ImageGalleryStore(root)

    del manifest["unexpected"]
    encoded = json.dumps(manifest, separators=(",", ":"))
    path.write_text(encoded[:-1] + ',"schema_version":1}', encoding="utf-8")
    with pytest.raises(ImageGalleryStorageError, match="manifest"):
        ImageGalleryStore(root)


def test_redirected_or_reparse_marked_entries_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise reparse defense independently of host symlink privileges."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        store.create(_result())
    image = _artifact_directory(root) / "image.png"
    selected_inode = image.stat().st_ino
    original = gallery_module._is_reparse

    def selected_reparse(info: os.stat_result) -> bool:
        """Mark only the committed image as redirected for this test."""

        return info.st_ino == selected_inode or original(info)

    monkeypatch.setattr(gallery_module, "_is_reparse", selected_reparse)
    with pytest.raises(ImageGalleryStorageError, match="unsafe"):
        ImageGalleryStore(root)


def test_hard_linked_image_is_rejected_when_supported(tmp_path: Path) -> None:
    """Reject a second filesystem name that could mutate committed bytes."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        store.create(_result())
    image = _artifact_directory(root) / "image.png"
    alias = tmp_path / "alias.png"
    try:
        os.link(image, alias)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem.")

    with pytest.raises(ImageGalleryStorageError, match="unsafe"):
        ImageGalleryStore(root)


def test_item_and_byte_capacity_are_enforced_before_commit(tmp_path: Path) -> None:
    """Bound both directory cardinality and aggregate canonical image bytes."""

    item_root = tmp_path / "items"
    with ImageGalleryStore(item_root, max_items=1) as store:
        store.create(_result(value=10))
        with pytest.raises(ImageGalleryLimitError, match="full"):
            store.create(_result(value=20))
        assert len(store.list()) == 1

    byte_root = tmp_path / "bytes"
    image_size = len(_result().image.data)
    with ImageGalleryStore(
        byte_root,
        max_total_image_bytes=image_size,
    ) as store:
        store.create(_result(value=10))
        with pytest.raises(ImageGalleryLimitError, match="byte"):
            store.create(_result(value=20))


def test_concurrent_creates_cannot_race_past_item_capacity(tmp_path: Path) -> None:
    """Use the in-process lock to make capacity admission and commit indivisible."""

    with ImageGalleryStore(tmp_path / "gallery", max_items=1) as store:
        def attempt(value: int) -> str:
            """Return the outcome of one concurrently submitted create."""

            try:
                store.create(_result(value=value))
            except ImageGalleryLimitError:
                return "full"
            return "created"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(attempt, (10, 20)))

        assert sorted(outcomes) == ["created", "full"]
        assert len(store.list()) == 1


def test_private_prompt_and_opaque_id_validation_are_bounded(tmp_path: Path) -> None:
    """Reject oversized private text and any caller-controlled path syntax."""

    with ImageGalleryStore(tmp_path / "gallery") as store:
        with pytest.raises(ImageGalleryLimitError, match="prompt"):
            store.create(
                _result(),
                private_prompt="x" * (MAX_PRIVATE_PROMPT_CODEPOINTS + 1),
            )
        for invalid in ("../image.png", "image_" + "a" * 32, "", None):
            with pytest.raises(ImageGalleryValidationError, match="ID"):
                store.get(invalid)  # type: ignore[arg-type]


def test_second_store_cannot_acquire_the_live_process_lock(tmp_path: Path) -> None:
    """Exclude another Backend even though both instances share one process."""

    root = tmp_path / "gallery"
    first = ImageGalleryStore(root)
    try:
        with pytest.raises(ImageGalleryStorageError, match="another Backend"):
            ImageGalleryStore(root)
    finally:
        first.close()
    with ImageGalleryStore(root):
        pass


def test_separate_process_cannot_acquire_the_live_gallery_lease(
    tmp_path: Path,
) -> None:
    """Prove the writer lease excludes another Backend process, not only a thread."""

    root = tmp_path / "gallery"
    child = """
from pathlib import Path
import sys
from image_generation.gallery import ImageGalleryStorageError, ImageGalleryStore

try:
    ImageGalleryStore(Path(sys.argv[1]))
except ImageGalleryStorageError:
    print("locked")
else:
    print("acquired")
    raise SystemExit(2)
"""
    with ImageGalleryStore(root):
        completed = subprocess.run(
            [sys.executable, "-c", child, str(root)],
            cwd=Path.cwd(),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "locked"


def test_closed_store_refuses_operations(tmp_path: Path) -> None:
    """Make lease release terminal for that store object."""

    store = ImageGalleryStore(tmp_path / "gallery")
    store.close()
    store.close()

    with pytest.raises(ImageGalleryStorageError, match="not open"):
        store.list()


def test_create_rejects_malformed_generated_result_metadata(tmp_path: Path) -> None:
    """Validate trusted Backend result structure instead of trusting annotations."""

    with ImageGalleryStore(tmp_path / "gallery") as store:
        with pytest.raises(ImageGalleryValidationError, match="invalid"):
            store.create(replace(_result(), profile_digest="not-a-digest"))
        with pytest.raises(ImageGalleryValidationError, match="invalid"):
            store.create(replace(_result(), job_id="../job"))
        with pytest.raises(ImageGalleryValidationError, match="timezone"):
            store.create(
                replace(_result(), created_at=datetime(2026, 1, 1))
            )


def test_create_rollback_removes_only_its_closed_staging_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid publishing a partial directory when the authoritative rename fails."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        original_replace = gallery_module.os.replace

        def fail_artifact_replace(source: Any, destination: Any) -> None:
            """Fail only the staging-to-artifact commit boundary."""

            if Path(str(source)).name.startswith(".staging_"):
                raise OSError("injected")
            original_replace(source, destination)

        monkeypatch.setattr(gallery_module.os, "replace", fail_artifact_replace)
        with pytest.raises(ImageGalleryStorageError, match="saved"):
            store.create(_result())

        assert store.list() == ()
        assert not tuple(root.glob(".staging_*"))


def test_create_remains_successful_when_post_commit_root_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never invite a duplicate retry after staging-to-image rename committed."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        sibling = store.create(_result(value=10))
        original_sync = store._sync_directory

        def fail_root_sync(path: Path) -> None:
            """Fail only maintenance after the artifact directory rename."""

            if path == root:
                raise PermissionError(str(root))
            original_sync(path)

        with monkeypatch.context() as context:
            context.setattr(store, "_sync_directory", fail_root_sync)
            committed = store.create(_result(value=20))
            assert {item.artifact_id for item in store.list()} == {
                sibling.artifact_id,
                committed.artifact_id,
            }
            assert store.open_verified(committed.artifact_id).sha256 == (
                committed.sha256
            )

    with ImageGalleryStore(root) as reopened:
        assert {item.artifact_id for item in reopened.list()} == {
            sibling.artifact_id,
            committed.artifact_id,
        }


def test_delete_commit_survives_sync_and_tombstone_gc_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return logical success after rename and defer recoverable maintenance."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        deleted = store.create(_result(value=10))
        sibling = store.create(_result(value=20))
        original_sync = store._sync_directory
        original_purge = store._purge_artifact_directory

        def fail_root_sync(path: Path) -> None:
            """Fail only root durability maintenance after deletion commit."""

            if path == root:
                raise PermissionError(str(root))
            original_sync(path)

        def fail_tombstone_gc(path: Path, *, allow_partial: bool) -> None:
            """Leave the committed tombstone for startup recovery."""

            if path.name.startswith(".deleting_"):
                raise PermissionError(str(path))
            original_purge(path, allow_partial=allow_partial)

        with monkeypatch.context() as context:
            context.setattr(store, "_sync_directory", fail_root_sync)
            context.setattr(store, "_purge_artifact_directory", fail_tombstone_gc)
            store.delete(deleted.artifact_id)
            assert store.list() == (sibling,)
            assert store.open_verified(sibling.artifact_id).sha256 == sibling.sha256
            with pytest.raises(ImageGalleryNotFoundError, match="not found"):
                store.get(deleted.artifact_id)
            assert len(tuple(root.glob(".deleting_*"))) == 1

    with ImageGalleryStore(root) as reopened:
        assert reopened.list() == (sibling,)
        assert reopened.open_verified(sibling.artifact_id).sha256 == sibling.sha256
    assert not tuple(root.glob(".deleting_*"))


def test_error_messages_do_not_echo_private_paths_ids_or_prompts(
    tmp_path: Path,
) -> None:
    """Keep sensitive values out of messages, exception chains, and tracebacks."""

    root = tmp_path / "private-secret-gallery"
    secret_prompt = "secret-prompt-value"
    with ImageGalleryStore(root) as store:
        item = store.create(_result(), private_prompt=secret_prompt)
        os.replace(_artifact_directory(root), tmp_path / "moved-private-artifact")
        with pytest.raises(ImageGalleryStorageError) as captured:
            store.open_verified(item.artifact_id)

    rendered = "".join(
        traceback.format_exception(
            captured.type,
            captured.value,
            captured.tb,
        )
    )
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert str(root) not in rendered
    assert item.artifact_id not in rendered
    assert secret_prompt not in rendered


@pytest.mark.parametrize(
    ("drive_type", "filesystem"),
    [(2, "NTFS"), (3, "FAT32"), (4, "NTFS"), (3, "exFAT")],
)
def test_windows_unsupported_gallery_volumes_fail_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drive_type: int,
    filesystem: str,
) -> None:
    """Reject removable, remote, and FAT-like roots before creating storage."""

    root = tmp_path / "unsupported-gallery"
    monkeypatch.setattr(gallery_module.os, "name", "nt")
    monkeypatch.setattr(
        gallery_module,
        "_windows_volume_identity",
        lambda _path: (drive_type, filesystem),
    )

    with pytest.raises(ImageGalleryStorageError, match="fixed local"):
        ImageGalleryStore(root)

    assert not root.exists()


@pytest.mark.parametrize("filesystem", ["NTFS", "refs", "ReFS"])
def test_windows_fixed_local_supported_gallery_volumes_are_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filesystem: str,
) -> None:
    """Admit case-insensitive NTFS/ReFS identities used by fixed local roots."""

    monkeypatch.setattr(gallery_module.os, "name", "nt")
    monkeypatch.setattr(
        gallery_module,
        "_windows_volume_identity",
        lambda _path: (3, filesystem),
    )

    gallery_module._require_supported_local_volume(tmp_path / "gallery")


def test_layout_root_must_be_absolute_and_limits_must_be_typed() -> None:
    """Reject ambiguous storage roots and boolean or out-of-range capacities."""

    with pytest.raises(ValueError, match="absolute"):
        ImageGalleryStore(Path("relative/gallery"))
    with pytest.raises(TypeError, match="Path"):
        ImageGalleryStore("C:/gallery")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_items"):
        ImageGalleryStore(Path.cwd() / "gallery", max_items=True)
    with pytest.raises(ValueError, match="max_total"):
        ImageGalleryStore(
            Path.cwd() / "gallery",
            max_total_image_bytes=0,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-file fixture")
def test_special_file_in_artifact_is_rejected(tmp_path: Path) -> None:
    """Reject named pipes rather than blocking while attempting to read them."""

    root = tmp_path / "gallery"
    with ImageGalleryStore(root) as store:
        store.create(_result())
    image = _artifact_directory(root) / "image.png"
    image.unlink()
    getattr(os, "mkfifo")(image)
    assert stat.S_ISFIFO(image.lstat().st_mode)

    with pytest.raises(ImageGalleryStorageError, match="unsafe"):
        ImageGalleryStore(root)
