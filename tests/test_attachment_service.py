"""Test secure local attachment staging, ownership, and recovery."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import attachments.store as attachment_store
from attachments import (
    AttachmentConflictError,
    AttachmentNotFoundError,
    AttachmentScope,
    AttachmentStorageError,
    AttachmentValidationError,
    JsonAttachmentStore,
)


def _chat_scope(name: str = "test") -> AttachmentScope:
    """Build a valid Chat-owned attachment scope with a stable test ID."""
    return AttachmentScope(kind="chat", id=f"chat_{name}")


def _project_scope(name: str = "test") -> AttachmentScope:
    """Build a valid Project-owned attachment scope with a stable test ID."""
    return AttachmentScope(kind="project", id=f"project_{name}")


def _store(tmp_path: Path, *, size: int = 1024, count: int = 10) -> JsonAttachmentStore:
    """Create an isolated store with deliberately small configurable limits."""
    return JsonAttachmentStore(
        tmp_path / "workspace" / "attachments",
        max_file_bytes=size,
        max_file_count=count,
    )


def _source(tmp_path: Path, name: str, content: bytes) -> Path:
    """Write and resolve an external source file for attachment staging."""
    source = tmp_path / "incoming" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    return source.resolve()


def _manifest_path(tmp_path: Path, scope: AttachmentScope) -> Path:
    """Return the canonical manifest path for an isolated attachment scope."""
    return (
        tmp_path
        / "workspace"
        / "attachments"
        / scope.kind
        / scope.id
        / "manifest.json"
    )


def _blob_files(tmp_path: Path, scope: AttachmentScope, folder: str) -> tuple[Path, ...]:
    """List persisted blob files in one scope lifecycle directory."""
    directory = _manifest_path(tmp_path, scope).parent / folder
    return tuple(directory.glob("*.blob")) if directory.exists() else ()


def _create_directory_redirect(link: Path, target: Path) -> None:
    """Create a junction or symlink used to exercise redirect-escape defenses."""
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
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
    """Remove directory redirect created by the fixture."""
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        link.rmdir()


def test_scope_requires_a_matching_storage_safe_id() -> None:
    """Verify that scope requires a matching storage safe ID."""
    assert AttachmentScope(kind="chat", id="chat_safe_123").id == "chat_safe_123"
    assert AttachmentScope(kind="project", id="project_safe-123").kind == "project"

    with pytest.raises(ValueError, match="does not match"):
        AttachmentScope(kind="chat", id="project_wrong")
    with pytest.raises(ValueError, match="does not match"):
        AttachmentScope(kind="project", id="../project_escape")


def test_stage_persists_only_safe_metadata_and_an_opaque_blob(tmp_path: Path) -> None:
    """Verify that stage persists only safe metadata and an opaque blob."""
    store = _store(tmp_path)
    scope = _chat_scope()
    source = _source(tmp_path, "private notes.TXT", b"local context")

    state = store.stage_files(scope, [source])

    assert state.scope == scope
    assert state.max_file_bytes == 1024
    assert state.max_file_count == 10
    assert len(state.attachments) == 1
    item = state.attachments[0]
    assert item.attachment_id.startswith("attachment_")
    assert item.file_name == "private notes.TXT"
    assert item.media_type == "text/plain"
    assert item.size_bytes == len(b"local context")
    assert item.status == "ready"

    manifest_text = _manifest_path(tmp_path, scope).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert str(source) not in manifest_text
    assert set(manifest) == {"schema_version", "scope", "items"}
    assert set(manifest["items"][0]) == {
        "attachment_id",
        "file_name",
        "media_type",
        "size_bytes",
        "sha256",
        "status",
    }
    blobs = _blob_files(tmp_path, scope, "drafts")
    assert [blob.name for blob in blobs] == [f"{item.attachment_id}.blob"]
    assert blobs[0].read_bytes() == b"local context"

    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_state(scope) == state


def test_redirected_workspace_root_is_rejected_before_external_write(
    tmp_path: Path,
) -> None:
    """Reject a redirected storage root before attacker-controlled external writes."""
    application_root = tmp_path / "application"
    outside = tmp_path / "outside"
    workspace = application_root / "workspace"
    _create_directory_redirect(workspace, outside)
    try:
        with pytest.raises(AttachmentStorageError, match="chain is unsafe"):
            JsonAttachmentStore(
                workspace / "attachments",
                max_file_bytes=1_024,
            )
        assert not (outside / "attachments").exists()
    finally:
        _remove_directory_redirect(workspace)


def test_hard_linked_process_lock_is_rejected_without_touching_target(
    tmp_path: Path,
) -> None:
    """Verify that hard linked process lock is rejected without touching target."""
    base_dir = tmp_path / "workspace" / "attachments"
    base_dir.mkdir(parents=True)
    victim = tmp_path / "outside-lock-target"
    victim.write_bytes(b"")
    try:
        os.link(victim, base_dir / ".backend.lock")
    except OSError:
        pytest.skip("This host does not allow test hard links.")

    with pytest.raises(AttachmentStorageError, match="lock is unsafe"):
        JsonAttachmentStore(base_dir, max_file_bytes=1_024)
    assert victim.read_bytes() == b""


def test_equal_content_is_deduplicated_within_one_scope(tmp_path: Path) -> None:
    """Verify that equal content is deduplicated within one scope."""
    store = _store(tmp_path)
    scope = _chat_scope()
    first = _source(tmp_path, "first.txt", b"same bytes")
    second = _source(tmp_path, "second.md", b"same bytes")

    state = store.stage_files(scope, [first, second])
    repeated = store.stage_files(scope, [second])

    assert len(state.attachments) == 1
    assert repeated == state
    assert len(_blob_files(tmp_path, scope, "drafts")) == 1


def test_smaller_restart_limit_keeps_existing_drafts_visible(
    tmp_path: Path,
) -> None:
    """Verify that smaller restart limit keeps existing drafts visible."""
    store = _store(tmp_path, size=1_024)
    scope = _chat_scope("smaller-limit")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "existing.txt", b"x" * 100)],
    ).attachments[0]
    store.close()

    restarted = _store(tmp_path, size=10)
    state = restarted.list_state(scope)
    assert state.attachments == (item,)
    assert state.max_file_bytes == 10
    with pytest.raises(AttachmentValidationError, match="size limit"):
        restarted.stage_files(
            scope,
            [_source(tmp_path, "new.txt", b"y" * 11)],
        )
    assert restarted.list_state(scope).attachments == (item,)


def test_deduplication_does_not_cross_scope_boundaries(tmp_path: Path) -> None:
    """Verify that deduplication does not cross scope boundaries."""
    store = _store(tmp_path)
    source = _source(tmp_path, "same.txt", b"same bytes")

    chat = store.stage_files(_chat_scope("one"), [source]).attachments[0]
    project = store.stage_files(_project_scope("one"), [source]).attachments[0]

    assert chat.attachment_id != project.attachment_id


def test_batch_failure_rolls_back_every_new_blob_and_manifest_change(
    tmp_path: Path,
) -> None:
    """Make multi-file staging atomic when any member violates store limits."""
    store = _store(tmp_path, size=8)
    scope = _chat_scope()
    valid = _source(tmp_path, "valid.txt", b"1234")
    oversized = _source(tmp_path, "large.txt", b"123456789")

    with pytest.raises(AttachmentValidationError, match="size limit"):
        store.stage_files(scope, [valid, oversized])

    assert store.list_state(scope).attachments == ()
    assert _blob_files(tmp_path, scope, "drafts") == ()


def test_manifest_replace_failure_rolls_back_a_staged_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove newly copied blobs when the authoritative manifest cannot commit."""
    store = _store(tmp_path)
    scope = _chat_scope()
    source = _source(tmp_path, "notes.txt", b"notes")
    real_replace = attachment_store.os.replace

    def fail_manifest_replace(
        source_path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        """Fail only the manifest replace after allowing blob moves."""
        if os.path.basename(os.fsdecode(destination)) == "manifest.json":
            raise OSError("simulated manifest failure")
        real_replace(source_path, destination)

    monkeypatch.setattr(attachment_store.os, "replace", fail_manifest_replace)

    with pytest.raises(AttachmentStorageError, match="manifest"):
        store.stage_files(scope, [source])

    assert _blob_files(tmp_path, scope, "drafts") == ()
    assert not _manifest_path(tmp_path, scope).exists()


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("program.exe", b"MZ", "type is not supported"),
        ("empty.txt", b"", "Empty files"),
    ],
)
def test_unsupported_and_empty_files_are_rejected(
    tmp_path: Path,
    name: str,
    content: bytes,
    message: str,
) -> None:
    """Verify that unsupported and empty files are rejected."""
    store = _store(tmp_path)
    with pytest.raises(AttachmentValidationError, match=message):
        store.stage_files(_chat_scope(), [_source(tmp_path, name, content)])


def test_common_code_file_is_stored_without_parsing_it(tmp_path: Path) -> None:
    """Verify that common code file is stored without parsing it."""
    store = _store(tmp_path)
    state = store.stage_files(
        _chat_scope(),
        [_source(tmp_path, "example.py", b"print('local only')\n")],
    )

    assert state.attachments[0].media_type == "text/x-python"


def test_directory_relative_path_and_internal_blob_are_rejected(tmp_path: Path) -> None:
    """Verify that directory relative path and internal blob are rejected."""
    store = _store(tmp_path)
    scope = _chat_scope()
    source = _source(tmp_path, "first.txt", b"content")
    state = store.stage_files(scope, [source])
    internal_blob = _blob_files(tmp_path, scope, "drafts")[0]

    with pytest.raises(AttachmentValidationError, match="path is invalid"):
        store.stage_files(scope, [Path("relative.txt")])
    with pytest.raises(AttachmentValidationError, match="path is invalid"):
        store.stage_files(scope, [Path(r"\\.\C:\device.txt")])
    with pytest.raises(AttachmentValidationError, match="regular local"):
        store.stage_files(scope, [source.parent])
    with pytest.raises(AttachmentValidationError, match="Internal application"):
        store.stage_files(scope, [internal_blob])

    assert store.list_state(scope) == state


def test_symlink_source_is_rejected_without_leaking_its_path(tmp_path: Path) -> None:
    """Block symlink sources and keep both link and target paths out of errors."""
    target = _source(tmp_path, "target.txt", b"content")
    link = tmp_path / "incoming" / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("This host does not allow test symlinks.")

    with pytest.raises(AttachmentValidationError, match="Linked or redirected") as error:
        _store(tmp_path).stage_files(_chat_scope(), [link.absolute()])

    assert str(link) not in str(error.value)
    assert str(target) not in str(error.value)


def test_source_change_during_copy_rolls_back_the_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detect a source mutation during secure copy and remove its partial blob."""
    store = _store(tmp_path)
    scope = _chat_scope()
    source = _source(tmp_path, "changing.txt", b"content")
    source_details = source.stat()
    source_identity = (source_details.st_dev, source_details.st_ino)
    real_fstat = attachment_store.os.fstat
    source_checks = 0

    def changing_fstat(descriptor: int) -> object:
        """Make the second source stat appear modified during secure copy."""
        nonlocal source_checks
        details = real_fstat(descriptor)
        if (details.st_dev, details.st_ino) != source_identity:
            return details
        source_checks += 1
        if source_checks == 2:
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_size=details.st_size,
                st_mtime_ns=details.st_mtime_ns + 1,
                st_dev=details.st_dev,
                st_ino=details.st_ino,
                st_file_attributes=getattr(details, "st_file_attributes", 0),
            )
        return details

    monkeypatch.setattr(attachment_store.os, "fstat", changing_fstat)

    with pytest.raises(AttachmentValidationError, match="changed"):
        store.stage_files(scope, [source])

    drafts = _manifest_path(tmp_path, scope).parent / "drafts"
    assert not drafts.exists() or tuple(drafts.iterdir()) == ()


def test_remove_is_scope_isolated_and_rejects_claimed_items(tmp_path: Path) -> None:
    """Verify that remove is scope isolated and rejects claimed items."""
    store = _store(tmp_path)
    first_scope = _chat_scope("first")
    second_scope = _chat_scope("second")
    item = store.stage_files(
        first_scope,
        [_source(tmp_path, "notes.txt", b"content")],
    ).attachments[0]

    with pytest.raises(AttachmentNotFoundError, match="this scope"):
        store.remove(second_scope, item.attachment_id)

    claimed = store.claim_chat(first_scope, [item.attachment_id])
    assert claimed == (item,)
    assert store.list_state(first_scope).attachments == ()
    with pytest.raises(AttachmentConflictError, match="already in use"):
        store.remove(first_scope, item.attachment_id)

    # A Backend restart releases a claim that never reached Chat commit.
    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_state(first_scope).attachments == (item,)
    assert restarted.remove(first_scope, item.attachment_id).attachments == ()
    assert _blob_files(tmp_path, first_scope, "drafts") == ()


def test_live_store_lock_prevents_a_second_backend_releasing_claims(
    tmp_path: Path,
) -> None:
    """Verify that live store lock prevents a second backend releasing claims."""
    store = _store(tmp_path)
    scope = _chat_scope("exclusive")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "exclusive.txt", b"claimed")],
    ).attachments[0]
    store.claim_chat(scope, [item.attachment_id])

    with pytest.raises(AttachmentStorageError, match="already in use"):
        _store(tmp_path)
    assert store.list_state(scope).attachments == ()

    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_state(scope).attachments == (item,)


def test_claim_is_all_or_none_and_requires_a_chat_scope(tmp_path: Path) -> None:
    """Verify that claim is all or none and requires a chat scope."""
    store = _store(tmp_path)
    chat_scope = _chat_scope()
    project_scope = _project_scope()
    chat_item = store.stage_files(
        chat_scope,
        [_source(tmp_path, "chat.txt", b"chat")],
    ).attachments[0]
    project_item = store.stage_files(
        project_scope,
        [_source(tmp_path, "project.txt", b"project")],
    ).attachments[0]

    with pytest.raises(AttachmentValidationError, match="Only Chat"):
        store.claim_chat(project_scope, [project_item.attachment_id])
    with pytest.raises(AttachmentNotFoundError):
        store.claim_chat(chat_scope, [chat_item.attachment_id, "attachment_missing"])

    assert store.remove(chat_scope, chat_item.attachment_id).attachments == ()


def test_chat_commit_removes_draft_state_but_keeps_referenced_blob(
    tmp_path: Path,
) -> None:
    """Verify that chat commit removes draft state but keeps referenced blob."""
    store = _store(tmp_path)
    scope = _chat_scope()
    item = store.stage_files(
        scope,
        [_source(tmp_path, "context.pdf", b"%PDF-test")],
    ).attachments[0]

    store.claim_chat(scope, [item.attachment_id])
    committed_state = store.mark_committed(scope, [item.attachment_id])

    assert committed_state.attachments == ()
    assert _blob_files(tmp_path, scope, "drafts") == ()
    committed = _blob_files(tmp_path, scope, "committed")
    assert [path.name for path in committed] == [f"{item.attachment_id}.blob"]

    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_state(scope, [item.attachment_id]).attachments == ()
    assert len(_blob_files(tmp_path, scope, "committed")) == 1
    restarted.reconcile(scope, ())
    assert _blob_files(tmp_path, scope, "committed") == ()


def test_chat_commit_rolls_blob_back_when_manifest_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a moved blob to draft state if its committed manifest write fails."""
    store = _store(tmp_path)
    scope = _chat_scope()
    item = store.stage_files(
        scope,
        [_source(tmp_path, "context.txt", b"context")],
    ).attachments[0]
    store.claim_chat(scope, [item.attachment_id])
    real_replace = attachment_store.os.replace

    def fail_manifest_replace(
        source_path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        """Fail only the manifest replace during the commit transition."""
        if os.path.basename(os.fsdecode(destination)) == "manifest.json":
            raise OSError("simulated manifest failure")
        real_replace(source_path, destination)

    monkeypatch.setattr(attachment_store.os, "replace", fail_manifest_replace)

    with pytest.raises(AttachmentStorageError, match="manifest"):
        store.mark_committed(scope, [item.attachment_id])

    assert [path.name for path in _blob_files(tmp_path, scope, "drafts")] == [
        f"{item.attachment_id}.blob"
    ]
    assert _blob_files(tmp_path, scope, "committed") == ()


def test_interrupted_chat_commit_is_repaired_from_canonical_references(
    tmp_path: Path,
) -> None:
    """Reconcile interrupted attachment state from persisted Chat references."""
    store = _store(tmp_path)
    scope = _chat_scope()
    item = store.stage_files(
        scope,
        [_source(tmp_path, "context.md", b"context")],
    ).attachments[0]
    draft = _blob_files(tmp_path, scope, "drafts")[0]
    committed_dir = _manifest_path(tmp_path, scope).parent / "committed"
    committed_dir.mkdir(exist_ok=True)
    os.replace(draft, committed_dir / draft.name)

    state = store.reconcile(scope, [item.attachment_id])

    assert state.attachments == ()
    stored_items = json.loads(
        _manifest_path(tmp_path, scope).read_text(encoding="utf-8")
    )["items"]
    assert len(stored_items) == 1
    assert stored_items[0]["attachment_id"] == item.attachment_id
    assert stored_items[0]["status"] == "committed"
    assert len(_blob_files(tmp_path, scope, "committed")) == 1


def test_reconcile_verifies_committed_blob_integrity(tmp_path: Path) -> None:
    """Verify that reconcile verifies committed blob integrity."""
    store = _store(tmp_path)
    scope = _chat_scope("committed-integrity")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "integrity.txt", b"expected")],
    ).attachments[0]
    store.claim_chat(scope, [item.attachment_id])
    store.mark_committed(scope, [item.attachment_id])
    committed = _blob_files(tmp_path, scope, "committed")[0]
    committed.write_bytes(b"tampered")

    with pytest.raises(AttachmentStorageError, match="integrity"):
        store.reconcile(scope, [item.attachment_id])


def test_project_items_remain_available_when_marked_committed(tmp_path: Path) -> None:
    """Verify that project items remain available when marked committed."""
    store = _store(tmp_path)
    scope = _project_scope()
    item = store.stage_files(
        scope,
        [_source(tmp_path, "source.docx", b"document")],
    ).attachments[0]

    assert store.mark_committed(scope, [item.attachment_id]).attachments == (item,)
    store.close()
    assert _store(tmp_path).list_state(scope).attachments == (item,)


def test_staging_claimed_content_is_a_safe_deduplicated_noop(
    tmp_path: Path,
) -> None:
    """Verify that staging claimed content is a safe deduplicated noop."""
    store = _store(tmp_path)
    scope = _chat_scope("claimed-dedupe")
    source = _source(tmp_path, "same.txt", b"same bytes")
    item = store.stage_files(scope, [source]).attachments[0]
    store.claim_chat(scope, [item.attachment_id])

    assert store.stage_files(scope, [source]).attachments == ()
    manifest_items = json.loads(
        _manifest_path(tmp_path, scope).read_text(encoding="utf-8")
    )["items"]
    assert len(manifest_items) == 1
    assert manifest_items[0]["attachment_id"] == item.attachment_id
    assert manifest_items[0]["status"] == "claimed"

    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_state(scope).attachments == (item,)


def test_reconcile_removes_temporary_and_orphan_files(tmp_path: Path) -> None:
    """Verify that reconcile removes temporary and orphan files."""
    store = _store(tmp_path)
    scope = _chat_scope()
    scope_root = _manifest_path(tmp_path, scope).parent
    drafts = scope_root / "drafts"
    committed = scope_root / "committed"
    drafts.mkdir(parents=True)
    committed.mkdir()
    (drafts / ".upload-crash.tmp").write_bytes(b"partial")
    (drafts / "attachment_orphan.blob").write_bytes(b"orphan")
    (committed / "attachment_orphan2.blob").write_bytes(b"orphan")

    assert store.reconcile(scope, ()).attachments == ()
    assert tuple(drafts.iterdir()) == ()
    assert tuple(committed.iterdir()) == ()


def test_corrupt_blob_is_reported_with_a_stable_path_free_error(tmp_path: Path) -> None:
    """Verify that corrupt blob is reported with a stable path free error."""
    store = _store(tmp_path)
    scope = _chat_scope()
    store.stage_files(
        scope,
        [_source(tmp_path, "notes.txt", b"expected")],
    )
    blob = _blob_files(tmp_path, scope, "drafts")[0]
    blob.write_bytes(b"changed")

    with pytest.raises(AttachmentStorageError, match="integrity") as error:
        store.list_state(scope)

    assert str(blob) not in str(error.value)


def test_manifest_rejects_unknown_fields_and_duplicate_json_keys(tmp_path: Path) -> None:
    """Verify that manifest rejects unknown fields and duplicate JSON keys."""
    scope = _chat_scope()
    store = _store(tmp_path)
    manifest = _manifest_path(tmp_path, scope)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"scope":{"kind":"chat","id":"chat_test"},'
        '"items":[],"unknown":true}',
        encoding="utf-8",
    )

    with pytest.raises(AttachmentStorageError, match="manifest"):
        store.list_state(scope)


def test_file_count_limit_is_enforced_without_changing_existing_state(
    tmp_path: Path,
) -> None:
    """Verify that file count limit is enforced without changing existing state."""
    store = _store(tmp_path, count=1)
    scope = _chat_scope()
    first = store.stage_files(
        scope,
        [_source(tmp_path, "first.txt", b"first")],
    )

    with pytest.raises(AttachmentValidationError, match="file limit"):
        store.stage_files(
            scope,
            [_source(tmp_path, "second.txt", b"second")],
        )

    assert store.list_state(scope) == first


def test_delete_scope_removes_only_the_exact_requested_scope(tmp_path: Path) -> None:
    """Verify that delete scope removes only the exact requested scope."""
    store = _store(tmp_path)
    first_scope = _chat_scope("first")
    second_scope = _chat_scope("second")
    source = _source(tmp_path, "notes.txt", b"notes")
    store.stage_files(first_scope, [source])
    second = store.stage_files(second_scope, [source])

    store.delete_scope(first_scope)
    store.delete_scope(first_scope)

    assert not _manifest_path(tmp_path, first_scope).parent.exists()
    assert store.list_state(second_scope) == second


def test_owner_delete_failure_restores_hidden_attachment_scope(
    tmp_path: Path,
) -> None:
    """Restore a hidden scope when deleting its canonical owner fails atomically."""
    store = _store(tmp_path)
    scope = _chat_scope("rollback")
    source = _source(tmp_path, "notes.txt", b"notes")
    original = store.stage_files(scope, [source])

    def fail_owner_delete() -> None:
        """Simulate owner deletion failing after its attachment scope is hidden."""
        raise RuntimeError("owner stayed canonical")

    with pytest.raises(RuntimeError, match="owner stayed canonical"):
        store.delete_scope_with(scope, fail_owner_delete)

    assert store.list_state(scope) == original


def test_startup_restores_ambiguous_delete_then_reconciles_owner(
    tmp_path: Path,
) -> None:
    """Verify that startup restores ambiguous delete then reconciles owner."""
    store = _store(tmp_path)
    scope = _chat_scope("crash-delete")
    original = store.stage_files(
        scope,
        [_source(tmp_path, "notes.txt", b"notes")],
    )
    scope_root = _manifest_path(tmp_path, scope).parent
    pending = scope_root.with_name(
        f".{scope.id}.delete-{'a' * 32}.pending"
    )
    os.replace(scope_root, pending)

    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_state(scope) == original
    restarted.reconcile_owners(chat_ids=(scope.id,), project_ids=())
    assert restarted.list_state(scope) == original

    scope_root = _manifest_path(tmp_path, scope).parent
    pending = scope_root.with_name(
        f".{scope.id}.delete-{'b' * 32}.pending"
    )
    os.replace(scope_root, pending)
    restarted.close()
    owner_deleted = _store(tmp_path)
    owner_deleted.reconcile_owners(chat_ids=(), project_ids=())
    assert not scope_root.exists()
