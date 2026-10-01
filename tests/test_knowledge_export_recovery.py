"""Verify durable, identity-pinned recovery of external export temporaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, cast

import pytest

from attachments import AttachmentScope
from chats import ProjectId
from knowledge_lifecycle import (
    KnowledgeExportService,
    KnowledgeLifecycleStorageError,
)


class _ImmediateLease:
    """Provide an uncontended mutation lease for export boundary tests."""

    @contextmanager
    def hold_mutation(self) -> Iterator[None]:
        """Enter and leave one deterministic no-op critical section."""

        yield


class _OriginalReader:
    """Yield one immutable original without a filesystem attachment store."""

    def __init__(self, payload: bytes) -> None:
        """Retain the exact verified bytes returned to export."""

        self._payload = payload

    def snapshot_file(
        self,
        _scope: AttachmentScope,
        _attachment_id: str,
    ) -> Any:
        """Reject unexpected metadata access outside the patched test seam."""

        raise AssertionError("Snapshot fixture was not installed.")

    @contextmanager
    def open_verified_file(
        self,
        _scope: AttachmentScope,
        _file_id: str,
    ) -> Iterator[BinaryIO]:
        """Yield a fresh binary reader over the verified fixture bytes."""

        with BytesIO(self._payload) as stream:
            yield stream


def _service(intent_directory: Path) -> KnowledgeExportService:
    """Construct only the recovery boundary with deliberately unused peers."""

    unused = cast(Any, object())
    return KnowledgeExportService(
        unused,
        unused,
        unused,
        intent_directory=intent_directory,
    )


def _temporary_path(parent: Path) -> Path:
    """Return one name matching the production export mkstemp contract."""

    return parent / ".document.txt.abcdef12.export.tmp"


def _write_intent(
    intent_directory: Path,
    temporary_path: Path,
    *,
    temporary_identity: tuple[int, int] | None = None,
    parent_identity: tuple[int, int] | None = None,
    identifier: str = "a" * 32,
) -> Path:
    """Write one canonical crash fixture from captured filesystem identity."""

    intent_directory.mkdir(parents=True, exist_ok=True)
    temporary_details = temporary_path.lstat()
    parent_details = temporary_path.parent.lstat()
    effective_temporary_identity = (
        (temporary_details.st_dev, temporary_details.st_ino)
        if temporary_identity is None
        else temporary_identity
    )
    effective_parent_identity = (
        (parent_details.st_dev, parent_details.st_ino)
        if parent_identity is None
        else parent_identity
    )
    payload = {
        "schema_version": 1,
        "target_name": "document.txt",
        "temporary_path": str(temporary_path),
        "parent_path": str(temporary_path.parent),
        "temporary_device": effective_temporary_identity[0],
        "temporary_inode": effective_temporary_identity[1],
        "parent_device": effective_parent_identity[0],
        "parent_inode": effective_parent_identity[1],
    }
    intent_path = intent_directory / f"export-{identifier}.json"
    intent_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return intent_path


def test_runtime_initialization_removes_identity_matched_crash_temp(
    tmp_path: Path,
) -> None:
    """Recover a legacy original-byte temp and atomically clear its intent."""

    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    temporary_path = _temporary_path(destination_parent)
    temporary_path.write_bytes(b"private original bytes")
    intent_directory = tmp_path / "private" / "export-intents"
    intent_path = _write_intent(intent_directory, temporary_path)

    service = _service(intent_directory)

    assert temporary_path.exists()
    assert intent_path.exists()
    service.recover_pending_exports()

    assert not temporary_path.exists()
    assert not intent_path.exists()
    assert tuple(intent_directory.iterdir()) == ()


def test_runtime_initialization_clears_intent_for_missing_published_temp(
    tmp_path: Path,
) -> None:
    """Treat a missing temp as published only under the pinned parent identity."""

    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    temporary_path = _temporary_path(destination_parent)
    temporary_path.write_bytes(b"already published")
    intent_directory = tmp_path / "private" / "export-intents"
    intent_path = _write_intent(intent_directory, temporary_path)
    temporary_path.unlink()

    service = _service(intent_directory)
    service.recover_pending_exports()

    assert not intent_path.exists()


def test_runtime_initialization_refuses_temporary_identity_mismatch(
    tmp_path: Path,
) -> None:
    """Fail closed without deleting a path not owned by the durable intent."""

    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    temporary_path = _temporary_path(destination_parent)
    temporary_path.write_bytes(b"replacement must survive")
    details = temporary_path.lstat()
    intent_directory = tmp_path / "private" / "export-intents"
    intent_path = _write_intent(
        intent_directory,
        temporary_path,
        temporary_identity=(details.st_dev, details.st_ino + 1),
    )

    with pytest.raises(
        KnowledgeLifecycleStorageError,
        match="temporary file identity changed",
    ):
        _service(intent_directory).recover_pending_exports()

    assert temporary_path.read_bytes() == b"replacement must survive"
    assert intent_path.exists()


def test_runtime_initialization_refuses_replaced_parent_path(
    tmp_path: Path,
) -> None:
    """Keep both paths untouched when the recorded destination was replaced."""

    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    temporary_path = _temporary_path(destination_parent)
    temporary_path.write_bytes(b"recorded original")
    intent_directory = tmp_path / "private" / "export-intents"
    intent_path = _write_intent(intent_directory, temporary_path)
    displaced_parent = tmp_path / "displaced-destination"
    os.replace(destination_parent, displaced_parent)
    destination_parent.mkdir()
    replacement_path = _temporary_path(destination_parent)
    replacement_path.write_bytes(b"unrelated replacement")

    with pytest.raises(
        KnowledgeLifecycleStorageError,
        match="parent identity changed",
    ):
        _service(intent_directory).recover_pending_exports()

    assert replacement_path.read_bytes() == b"unrelated replacement"
    assert _temporary_path(displaced_parent).read_bytes() == b"recorded original"
    assert intent_path.exists()


def test_runtime_initialization_preserves_corrupt_intent_and_external_path(
    tmp_path: Path,
) -> None:
    """Never infer deletion authority from malformed private state."""

    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    temporary_path = _temporary_path(destination_parent)
    temporary_path.write_bytes(b"unknown ownership")
    intent_directory = tmp_path / "private" / "export-intents"
    intent_directory.mkdir(parents=True)
    intent_path = intent_directory / f"export-{'b' * 32}.json"
    intent_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(
        KnowledgeLifecycleStorageError,
        match="intent is corrupted",
    ):
        _service(intent_directory).recover_pending_exports()

    assert temporary_path.read_bytes() == b"unknown ownership"
    assert intent_path.exists()


def test_recovery_continues_after_one_unreachable_intent(
    tmp_path: Path,
) -> None:
    """Clean later verified originals while retaining one unreachable record."""

    intent_directory = tmp_path / "private" / "export-intents"
    unavailable_parent = tmp_path / "unavailable"
    unavailable_parent.mkdir()
    unavailable_temporary = _temporary_path(unavailable_parent)
    unavailable_temporary.write_bytes(b"drive will disappear")
    unavailable_intent = _write_intent(
        intent_directory,
        unavailable_temporary,
        identifier="0" * 32,
    )
    unavailable_temporary.unlink()
    unavailable_parent.rmdir()

    available_parent = tmp_path / "available"
    available_parent.mkdir()
    available_temporary = _temporary_path(available_parent)
    available_temporary.write_bytes(b"sensitive later record")
    available_intent = _write_intent(
        intent_directory,
        available_temporary,
        identifier="f" * 32,
    )

    with pytest.raises(
        KnowledgeLifecycleStorageError,
        match="parent identity is unavailable",
    ):
        _service(intent_directory).recover_pending_exports()

    assert unavailable_intent.exists()
    assert not available_temporary.exists()
    assert not available_intent.exists()


def test_export_revalidates_identity_immediately_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the final guard observes a post-copy path swap."""

    payload = b"verified export payload"
    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    intent_directory = tmp_path / "private" / "export-intents"
    reader = _OriginalReader(payload)
    service = KnowledgeExportService(
        cast(Any, object()),
        reader,
        _ImmediateLease(),
        intent_directory=intent_directory,
    )
    scope = AttachmentScope("project", "project_export_guard")
    ownership = SimpleNamespace(
        role="project_source",
        file_id="file_export_guard",
        file_name="document.txt",
        media_type="text/plain",
    )
    original = SimpleNamespace(size_bytes=len(payload))
    snapshot = SimpleNamespace(
        ownerships=(ownership,),
        originals=(original,),
        snapshot_fingerprint="f" * 64,
    )
    monkeypatch.setattr(
        service,
        "_active_project_scope",
        lambda _project_id: scope,
    )
    monkeypatch.setattr(
        service,
        "_snapshot_file",
        lambda _scope, _link_id: snapshot,
    )
    original_guard = service._require_external_identity
    guard_calls = 0

    def _simulate_identity_swap(
        temporary_path: Path,
        parent_path: Path,
        temporary_identity: tuple[int, int],
        parent_identity: tuple[int, int],
    ) -> None:
        """Accept the pre-copy pin and reject the final publication pin."""

        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise KnowledgeLifecycleStorageError(
                "Recorded export parent identity changed."
            )
        original_guard(
            temporary_path,
            parent_path,
            temporary_identity,
            parent_identity,
        )

    monkeypatch.setattr(
        service,
        "_require_external_identity",
        _simulate_identity_swap,
    )
    destination = destination_parent / "document.txt"

    with pytest.raises(
        KnowledgeLifecycleStorageError,
        match="parent identity changed",
    ):
        service.export_original(
            ProjectId("project_export_guard"),
            "attachment_export_guard",
            destination,
        )

    assert guard_calls == 2
    assert not destination.exists()
    assert tuple(destination_parent.glob("*.export.tmp")) == ()
    assert tuple(intent_directory.iterdir()) == ()


def test_published_export_succeeds_when_private_intent_clear_is_deferred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report committed bytes once and let restart clear a retained intent."""

    payload = b"durably published export"
    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    intent_directory = tmp_path / "private" / "export-intents"
    service = KnowledgeExportService(
        cast(Any, object()),
        _OriginalReader(payload),
        _ImmediateLease(),
        intent_directory=intent_directory,
    )
    scope = AttachmentScope("project", "project_deferred_clear")
    snapshot = SimpleNamespace(
        ownerships=(
            SimpleNamespace(
                role="project_source",
                file_id="file_deferred_clear",
                file_name="document.txt",
                media_type="text/plain",
            ),
        ),
        originals=(SimpleNamespace(size_bytes=len(payload)),),
        snapshot_fingerprint="e" * 64,
    )
    monkeypatch.setattr(
        service,
        "_active_project_scope",
        lambda _project_id: scope,
    )
    monkeypatch.setattr(
        service,
        "_snapshot_file",
        lambda _scope, _link_id: snapshot,
    )

    def _defer_intent_clear(_intent: object) -> None:
        """Model private cleanup failure after irreversible publication."""

        raise KnowledgeLifecycleStorageError(
            "Export cleanup intent record could not be cleared."
        )

    monkeypatch.setattr(
        service,
        "_clear_export_intent",
        _defer_intent_clear,
    )
    destination = destination_parent / "document.txt"

    result = service.export_original(
        ProjectId("project_deferred_clear"),
        "attachment_deferred_clear",
        destination,
    )

    assert result.bytes_written == len(payload)
    assert destination.read_bytes() == payload
    assert len(tuple(intent_directory.iterdir())) == 1
    _service(intent_directory).recover_pending_exports()
    assert tuple(intent_directory.iterdir()) == ()
