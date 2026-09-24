"""Test versioned file metadata, ownership, migration, and safe lifecycle."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import attachments.store as attachment_store
from attachments import (
    AttachmentImportCancelledError,
    AttachmentNotFoundError,
    AttachmentScope,
    AttachmentStorageError,
    AttachmentValidationError,
    DerivedFileRelation,
    JsonAttachmentStore,
)

_NOW = datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc)


def _scope(kind: str, name: str) -> AttachmentScope:
    """Build one stable Chat or Project scope for a store test."""

    if kind == "chat":
        return AttachmentScope(kind="chat", id=f"chat_{name}")
    return AttachmentScope(kind="project", id=f"project_{name}")


def _base(tmp_path: Path) -> Path:
    """Return the isolated attachment root used by one test."""

    return tmp_path / "workspace" / "attachments"


def _store(tmp_path: Path, *, size: int = 4 * 1024 * 1024) -> JsonAttachmentStore:
    """Create a deterministic version-two attachment store."""

    return JsonAttachmentStore(
        _base(tmp_path),
        max_file_bytes=size,
        clock=lambda: _NOW,
    )


def _source(tmp_path: Path, name: str, data: bytes) -> Path:
    """Create an absolute external source path with controlled bytes."""

    path = tmp_path / "incoming" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path.resolve()


def _scope_root(tmp_path: Path, scope: AttachmentScope) -> Path:
    """Return the private filesystem root for one test-only scope."""

    return _base(tmp_path) / scope.kind / scope.id


def test_file_record_and_ownership_are_stable_path_free_and_verified(
    tmp_path: Path,
) -> None:
    """Persist complete metadata while keeping every native path private."""

    store = _store(tmp_path)
    scope = _scope("chat", "metadata")
    source = _source(tmp_path, "private notes.txt", b"private content")
    item = store.stage_files(scope, [source]).attachments[0]

    records = store.list_file_records(scope)
    ownerships = store.list_file_ownerships(scope)
    assert len(records) == len(ownerships) == 1
    record = records[0]
    ownership = ownerships[0]
    assert record.schema_version == 1
    assert record.file_id == f"file_{record.sha256}"
    assert record.sha256 == hashlib.sha256(b"private content").hexdigest()
    assert record.file_name == "private notes.txt"
    assert record.media_type == "text/plain"
    assert record.size_bytes == len(b"private content")
    assert record.origin == "local_import"
    assert record.imported_at == _NOW
    assert ownership.link_id == item.attachment_id
    assert ownership.file_id == record.file_id
    assert ownership.scope == scope
    assert ownership.role == "chat_attachment"
    assert ownership.imported_at == _NOW
    assert not any("path" in field for field in record.__dataclass_fields__)
    assert not any("path" in field for field in ownership.__dataclass_fields__)

    with store.open_verified_file(scope, record.file_id) as stream:
        assert stream.read() == b"private content"
    store.close()

    restarted = _store(tmp_path)
    assert restarted.list_file_records(scope) == records
    assert restarted.list_file_ownerships(scope) == ownerships
    manifest_text = (_scope_root(tmp_path, scope) / "manifest.json").read_text(
        encoding="utf-8"
    )
    assert str(source) not in manifest_text
    assert "source_path" not in manifest_text
    assert "storage_path" not in manifest_text


def test_legacy_v1_manifest_migrates_to_content_addressed_v2(
    tmp_path: Path,
) -> None:
    """Migrate existing Stage 6 data without changing IDs or losing bytes."""

    scope = _scope("chat", "legacy")
    scope_root = _scope_root(tmp_path, scope)
    drafts = scope_root / "drafts"
    drafts.mkdir(parents=True)
    data = b"legacy bytes"
    digest = hashlib.sha256(data).hexdigest()
    attachment_id = "attachment_legacy"
    (drafts / f"{attachment_id}.blob").write_bytes(data)
    manifest = scope_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {"kind": scope.kind, "id": scope.id},
                "items": [
                    {
                        "attachment_id": attachment_id,
                        "file_name": "legacy.txt",
                        "media_type": "text/plain",
                        "size_bytes": len(data),
                        "sha256": digest,
                        "status": "ready",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    legacy_time = datetime(2025, 5, 6, 7, 8, tzinfo=timezone.utc)
    os.utime(manifest, (legacy_time.timestamp(), legacy_time.timestamp()))

    store = _store(tmp_path)

    migrated = json.loads(manifest.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["derived"] == []
    assert migrated["items"][0]["attachment_id"] == attachment_id
    assert migrated["items"][0]["origin"] == "legacy_migration"
    assert migrated["items"][0]["file_id"] == f"file_{digest}"
    originals = tuple((scope_root / "originals").glob("*.blob"))
    assert len(originals) == 1
    assert originals[0].read_bytes() == data
    assert tuple(drafts.glob("*.blob")) == ()
    records = store.list_file_records(scope)
    store.close()
    assert _store(tmp_path).list_file_records(scope) == records


def test_legacy_migration_reverifies_bytes_after_the_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a legacy pathname swapped after its first integrity check."""

    scope = _scope("chat", "legacy-swap")
    scope_root = _scope_root(tmp_path, scope)
    drafts = scope_root / "drafts"
    drafts.mkdir(parents=True)
    trusted = b"trusted legacy bytes"
    digest = hashlib.sha256(trusted).hexdigest()
    attachment_id = "attachment_legacy_swap"
    legacy_blob = drafts / f"{attachment_id}.blob"
    legacy_blob.write_bytes(trusted)
    replacement = tmp_path / "malicious.txt"
    replacement.write_bytes(b"malicious replacement")
    manifest = scope_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {"kind": scope.kind, "id": scope.id},
                "items": [
                    {
                        "attachment_id": attachment_id,
                        "file_name": "legacy.txt",
                        "media_type": "text/plain",
                        "size_bytes": len(trusted),
                        "sha256": digest,
                        "status": "ready",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    expected_source = os.path.normcase(os.path.abspath(legacy_blob))
    real_replace = attachment_store.os.replace
    swapped = False

    def swap_before_legacy_move(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        """Substitute different bytes immediately before the legacy move."""

        nonlocal swapped
        source_path = os.path.normcase(os.path.abspath(os.fsdecode(source)))
        if not swapped and source_path == expected_source:
            real_replace(replacement, legacy_blob)
            swapped = True
        real_replace(source, destination)

    monkeypatch.setattr(
        attachment_store.os,
        "replace",
        swap_before_legacy_move,
    )

    with pytest.raises(AttachmentStorageError, match="integrity"):
        _store(tmp_path)

    assert swapped is True
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not (
        scope_root / "originals" / f"file_{digest}.blob"
    ).exists()


def test_legacy_duplicate_content_keeps_every_link_and_one_canonical_record(
    tmp_path: Path,
) -> None:
    """Migrate same-byte legacy drafts without losing names or claim recovery."""

    scope = _scope("chat", "legacy-duplicates")
    scope_root = _scope_root(tmp_path, scope)
    drafts = scope_root / "drafts"
    drafts.mkdir(parents=True)
    data = b"shared legacy bytes"
    digest = hashlib.sha256(data).hexdigest()
    identifiers = ("attachment_first", "attachment_second")
    for attachment_id in identifiers:
        (drafts / f"{attachment_id}.blob").write_bytes(data)
    (scope_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": {"kind": scope.kind, "id": scope.id},
                "items": [
                    {
                        "attachment_id": identifiers[0],
                        "file_name": "first.txt",
                        "media_type": "text/plain",
                        "size_bytes": len(data),
                        "sha256": digest,
                        "status": "claimed",
                    },
                    {
                        "attachment_id": identifiers[1],
                        "file_name": "second.md",
                        "media_type": "text/markdown",
                        "size_bytes": len(data),
                        "sha256": digest,
                        "status": "ready",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    store = _store(tmp_path)

    records = store.list_file_records(scope)
    ownerships = store.list_file_ownerships(scope)
    assert len(records) == 1
    assert records[0].file_name == "first.txt"
    assert [
        (link.link_id, link.file_name, link.media_type)
        for link in ownerships
    ] == [
        (identifiers[0], "first.txt", "text/plain"),
        (identifiers[1], "second.md", "text/markdown"),
    ]
    assert [item.attachment_id for item in store.list_state(scope).attachments] == [
        *identifiers
    ]
    assert len(tuple((scope_root / "originals").glob("*.blob"))) == 1


def test_future_manifest_version_fails_closed_without_touching_bytes(
    tmp_path: Path,
) -> None:
    """Reject unknown future schemas instead of guessing their semantics."""

    store = _store(tmp_path)
    scope = _scope("project", "future")
    scope_root = _scope_root(tmp_path, scope)
    scope_root.mkdir(parents=True)
    manifest = scope_root / "manifest.json"
    original = scope_root / "keep.bin"
    original.write_bytes(b"do not touch")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 999,
                "scope": {"kind": scope.kind, "id": scope.id},
                "items": [],
                "derived": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AttachmentStorageError, match="manifest"):
        store.list_state(scope)

    assert original.read_bytes() == b"do not touch"


def test_startup_preserves_unknown_tmp_before_rejecting_future_manifest(
    tmp_path: Path,
) -> None:
    """Never clean an unknown future temporary file by suffix alone."""

    scope = _scope("project", "future-temporary")
    scope_root = _scope_root(tmp_path, scope)
    originals = scope_root / "originals"
    originals.mkdir(parents=True)
    manifest = scope_root / "manifest.json"
    sentinel = originals / "future-format.tmp"
    sentinel.write_bytes(b"future bytes must remain")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 999,
                "scope": {"kind": scope.kind, "id": scope.id},
                "items": [],
                "derived": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AttachmentStorageError, match="unknown temporary"):
        _store(tmp_path)

    assert sentinel.read_bytes() == b"future bytes must remain"
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 999


@pytest.mark.parametrize("version_field", ["manifest", "record"])
def test_manifest_rejects_float_schema_versions(
    tmp_path: Path,
    version_field: str,
) -> None:
    """Require JSON integer tokens for top-level and record schemas."""

    store = _store(tmp_path)
    scope = _scope("project", f"float-{version_field}")
    store.stage_files(
        scope,
        [_source(tmp_path, f"{version_field}.txt", b"content")],
    )
    manifest = _scope_root(tmp_path, scope) / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if version_field == "manifest":
        document["schema_version"] = 2.0
    else:
        document["items"][0]["record_schema_version"] = 1.0
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AttachmentStorageError, match="manifest"):
        store.list_file_records(scope)


def test_manifest_replacement_between_validation_and_open_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a manifest inode swapped after its safe-path validation."""

    store = _store(tmp_path)
    scope = _scope("project", "manifest-swap")
    store.stage_files(
        scope,
        [_source(tmp_path, "source.txt", b"source")],
    )
    manifest = _scope_root(tmp_path, scope) / "manifest.json"
    replacement = tmp_path / "replacement-manifest.json"
    replacement.write_bytes(manifest.read_bytes())
    expected_path = os.path.normcase(os.path.abspath(manifest))
    real_open = attachment_store.os.open
    swapped = False

    def swap_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        """Replace only the validated manifest immediately before open."""

        nonlocal swapped
        opened_path = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        if not swapped and opened_path == expected_path:
            replacement.replace(manifest)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(attachment_store.os, "open", swap_before_open)

    with pytest.raises(AttachmentStorageError, match="manifest"):
        store.list_file_records(scope)

    assert swapped is True


def test_manifest_writer_enforces_the_same_bound_as_the_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an oversized update before publishing an unreadable manifest."""

    store = _store(tmp_path)
    scope = _scope("chat", "manifest-bound")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "source.txt", b"source")],
    ).attachments[0]
    manifest = _scope_root(tmp_path, scope) / "manifest.json"
    before = manifest.read_bytes()
    monkeypatch.setattr(
        attachment_store,
        "_MAX_MANIFEST_BYTES",
        len(before) + 1,
    )

    with pytest.raises(AttachmentStorageError, match="safe storage limit"):
        store.claim_chat(scope, [item.attachment_id])

    assert manifest.read_bytes() == before
    assert store.list_state(scope).attachments == (item,)
    assert not tuple(manifest.parent.glob(".manifest.json.*.tmp"))


def test_reimport_after_chat_commit_reuses_one_original_with_a_new_link(
    tmp_path: Path,
) -> None:
    """Deduplicate immutable bytes while preserving per-message ownership."""

    store = _store(tmp_path)
    scope = _scope("chat", "history-dedupe")
    first_source = _source(tmp_path, "first.txt", b"same bytes")
    second_source = _source(tmp_path, "renamed.md", b"same bytes")
    first = store.stage_files(scope, [first_source]).attachments[0]
    store.claim_chat(scope, [first.attachment_id])
    store.mark_committed(scope, [first.attachment_id])

    second = store.stage_files(
        scope,
        [second_source],
        [first.attachment_id],
    ).attachments[0]

    assert second.attachment_id != first.attachment_id
    assert second.file_name == "renamed.md"
    records = store.list_file_records(scope)
    ownerships = store.list_file_ownerships(scope)
    assert len(records) == 1
    assert len(ownerships) == 2
    assert records[0].file_name == "first.txt"
    assert records[0].media_type == "text/plain"
    assert {link.file_id for link in ownerships} == {records[0].file_id}
    assert len(tuple((_scope_root(tmp_path, scope) / "originals").glob("*.blob"))) == 1
    store.claim_chat(scope, [second.attachment_id])
    store.mark_committed(scope, [second.attachment_id])
    store.reconcile(scope, [second.attachment_id])
    assert store.list_file_records(scope) == records
    assert [
        (link.link_id, link.file_name, link.media_type)
        for link in store.list_file_ownerships(scope)
    ] == [(second.attachment_id, "renamed.md", "text/markdown")]
    assert len(tuple((_scope_root(tmp_path, scope) / "originals").glob("*.blob"))) == 1


def test_remove_crash_before_manifest_commit_preserves_live_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep live bytes when the process stops at the metadata commit."""

    store = _store(tmp_path)
    scope = _scope("project", "remove-crash")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "source.txt", b"source")],
    ).attachments[0]
    file_id = store.list_file_records(scope)[0].file_id
    original = _scope_root(tmp_path, scope) / "originals" / f"{file_id}.blob"
    real_replace = attachment_store.os.replace

    def stop_at_manifest_commit(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        """Simulate process termination at the durable manifest replace."""

        if os.path.basename(os.fsdecode(destination)) == "manifest.json":
            raise SystemExit("simulated crash")
        real_replace(source, destination)

    monkeypatch.setattr(attachment_store.os, "replace", stop_at_manifest_commit)

    with pytest.raises(SystemExit, match="simulated crash"):
        store.remove(scope, item.attachment_id)

    assert original.read_bytes() == b"source"
    stored = json.loads(
        (_scope_root(tmp_path, scope) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert [entry["attachment_id"] for entry in stored["items"]] == [
        item.attachment_id
    ]


def test_reconcile_crash_before_manifest_commit_preserves_live_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit orphan metadata removal before reclaiming its content bytes."""

    store = _store(tmp_path)
    scope = _scope("chat", "reconcile-crash")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "source.txt", b"source")],
    ).attachments[0]
    store.claim_chat(scope, [item.attachment_id])
    store.mark_committed(scope, [item.attachment_id])
    file_id = store.list_file_records(scope)[0].file_id
    original = _scope_root(tmp_path, scope) / "originals" / f"{file_id}.blob"
    real_replace = attachment_store.os.replace

    def stop_at_manifest_commit(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        """Simulate process termination before the orphan-GC boundary."""

        if os.path.basename(os.fsdecode(destination)) == "manifest.json":
            raise SystemExit("simulated crash")
        real_replace(source, destination)

    monkeypatch.setattr(attachment_store.os, "replace", stop_at_manifest_commit)

    with pytest.raises(SystemExit, match="simulated crash"):
        store.reconcile(scope, ())

    assert original.read_bytes() == b"source"
    stored = json.loads(
        (_scope_root(tmp_path, scope) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert [entry["attachment_id"] for entry in stored["items"]] == [
        item.attachment_id
    ]
def test_derived_relation_persists_and_cascades_with_last_owner_link(
    tmp_path: Path,
) -> None:
    """Persist an explicit Original-to-Derived edge and remove it on cascade."""

    store = _store(tmp_path)
    scope = _scope("project", "derived")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "source.md", b"source")],
    ).attachments[0]
    original = store.list_file_records(scope)[0]
    derived_digest = hashlib.sha256(b"derived preview").hexdigest()
    relation = DerivedFileRelation(
        schema_version=1,
        original_file_id=original.file_id,
        derived_file_id=f"file_{derived_digest}",
        scope=scope,
        derivation_kind="preview",
        producer_version="loader-1.0",
        created_at=_NOW,
    )

    store.register_derived_relation(scope, relation)
    store.register_derived_relation(scope, relation)
    assert store.list_derived_relations(scope) == (relation,)
    store.close()
    restarted = _store(tmp_path)
    assert restarted.list_derived_relations(scope) == (relation,)

    restarted.remove(scope, item.attachment_id)

    assert restarted.list_derived_relations(scope) == ()
    assert restarted.list_file_records(scope) == ()


def test_import_cancellation_rolls_back_metadata_originals_and_temps(
    tmp_path: Path,
) -> None:
    """Let cancellation win before commit without leaving partial content."""

    store = _store(tmp_path)
    scope = _scope("project", "cancel")
    source = _source(tmp_path, "large.txt", b"x" * (2 * 1024 * 1024))
    polls = 0

    def cancel_during_copy() -> bool:
        """Request cancellation after the first copy-loop admission check."""

        nonlocal polls
        polls += 1
        return polls >= 3

    with pytest.raises(AttachmentImportCancelledError, match="cancelled"):
        store.stage_files(
            scope,
            [source],
            cancel_requested=cancel_during_copy,
        )

    scope_root = _scope_root(tmp_path, scope)
    assert store.list_state(scope).attachments == ()
    assert not (scope_root / "manifest.json").exists()
    originals = scope_root / "originals"
    assert not originals.exists() or tuple(originals.iterdir()) == ()


def test_multi_scope_owner_delete_rolls_back_or_commits_as_one_unit(
    tmp_path: Path,
) -> None:
    """Protect Project and linked Chat scopes with one deletion boundary."""

    store = _store(tmp_path)
    project_scope = _scope("project", "delete")
    chat_scope = _scope("chat", "delete")
    source = _source(tmp_path, "notes.txt", b"notes")
    project_state = store.stage_files(project_scope, [source])
    chat_state = store.stage_files(chat_scope, [source])

    def fail_owner_transaction() -> None:
        """Simulate canonical Project deletion rolling back all owners."""

        raise RuntimeError("owner transaction failed")

    with pytest.raises(RuntimeError, match="owner transaction failed"):
        store.delete_scopes_with(
            (project_scope, chat_scope),
            fail_owner_transaction,
        )
    assert store.list_state(project_scope) == project_state
    assert store.list_state(chat_scope) == chat_state

    committed = False

    def commit_owner_transaction() -> None:
        """Record that canonical owner deletion crossed its commit boundary."""

        nonlocal committed
        committed = True

    store.delete_scopes_with(
        (project_scope, chat_scope),
        commit_owner_transaction,
    )
    assert committed is True
    assert not _scope_root(tmp_path, project_scope).exists()
    assert not _scope_root(tmp_path, chat_scope).exists()


def test_verified_open_requires_the_exact_owner_scope_and_integrity(
    tmp_path: Path,
) -> None:
    """Prevent an opaque File ID from becoming a cross-scope read capability."""

    store = _store(tmp_path)
    first_scope = _scope("chat", "authorized")
    other_scope = _scope("chat", "other")
    store.stage_files(
        first_scope,
        [_source(tmp_path, "secret.txt", b"secret")],
    )
    file_id = store.list_file_records(first_scope)[0].file_id

    with pytest.raises(AttachmentNotFoundError, match="owner scope"):
        with store.open_verified_file(other_scope, file_id):
            pass

    original = next(
        (_scope_root(tmp_path, first_scope) / "originals").glob("*.blob")
    )
    original.write_bytes(b"tampered")
    with pytest.raises(AttachmentStorageError, match="integrity"):
        with store.open_verified_file(first_scope, file_id):
            pass


def test_verified_open_uses_a_stable_snapshot_after_integrity_check(
    tmp_path: Path,
) -> None:
    """Prevent post-verification disk mutation from changing loader bytes."""

    store = _store(tmp_path)
    scope = _scope("project", "stable-read")
    store.stage_files(
        scope,
        [_source(tmp_path, "source.txt", b"trusted")],
    )
    file_id = store.list_file_records(scope)[0].file_id
    original = _scope_root(tmp_path, scope) / "originals" / f"{file_id}.blob"

    with store.open_verified_file(scope, file_id) as stream:
        original.write_bytes(b"changed")
        assert stream.read() == b"trusted"


def test_lower_import_limit_does_not_hide_an_existing_verified_file(
    tmp_path: Path,
) -> None:
    """Treat the configurable limit as an admission rule for new imports."""

    scope = _scope("project", "lower-limit")
    store = _store(tmp_path, size=1_024)
    store.stage_files(
        scope,
        [_source(tmp_path, "source.txt", b"existing content")],
    )
    file_id = store.list_file_records(scope)[0].file_id
    store.close()

    reopened = _store(tmp_path, size=1)
    with reopened.open_verified_file(scope, file_id) as stream:
        assert stream.read() == b"existing content"


def test_reading_an_absent_scope_does_not_create_a_namespace(
    tmp_path: Path,
) -> None:
    """Keep a renderer state read from producing empty storage garbage."""

    store = _store(tmp_path)
    scope = _scope("project", "absent")

    assert store.list_state(scope).attachments == ()
    assert store.list_file_records(scope) == ()
    assert not _scope_root(tmp_path, scope).exists()


def test_register_derived_relation_rejects_cross_scope_parent(
    tmp_path: Path,
) -> None:
    """Reject a relation that could bypass owner-scope authorization."""

    store = _store(tmp_path)
    first_scope = _scope("project", "first")
    other_scope = _scope("project", "second")
    store.stage_files(
        first_scope,
        [_source(tmp_path, "source.txt", b"source")],
    )
    original = store.list_file_records(first_scope)[0]
    relation = DerivedFileRelation(
        schema_version=1,
        original_file_id=original.file_id,
        derived_file_id=(
            f"file_{hashlib.sha256(b'derived').hexdigest()}"
        ),
        scope=other_scope,
        derivation_kind="preview",
        producer_version="loader-1",
        created_at=_NOW,
    )

    with pytest.raises(AttachmentValidationError, match="owner scope"):
        store.register_derived_relation(first_scope, relation)
