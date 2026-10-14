"""Verify single-use exact-action approval, durable before-images, safe audit, and conditional Undo."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from work_tools.contracts import ToolCall, ToolProtocolError
from work_tools.mutations import WorkspaceMutations
from work_tools.workspace import WorkspaceBoundary, WorkspaceBoundaryError
from work_tools.writes import WriteCoordinator
import work_tools.writes as writes_module


def _setup(tmp_path: Path) -> tuple[WriteCoordinator, WorkspaceBoundary, Path]:
    root = tmp_path / "selected"
    root.mkdir()
    private = tmp_path / "private" / "work"
    return WriteCoordinator(private), WorkspaceBoundary(root), private


def _confirm(coordinator: WriteCoordinator, boundary: WorkspaceBoundary, call: ToolCall) -> str:
    preview = coordinator.preview("chat_a", "project_a", boundary, call)
    result = coordinator.confirm("chat_a", "project_a", boundary, preview["approvalId"], True)
    assert result.status == "succeeded"
    assert result.data is not None
    return str(result.data["undoId"])


@pytest.mark.parametrize("tool_id", ["create", "edit", "move", "delete"])
def test_every_mutation_can_be_explicitly_approved_and_undone_across_restart(tmp_path: Path, tool_id: Any) -> None:
    """Undo persists its eligibility and restores the appropriate bytes or filename after restart."""

    coordinator, boundary, private = _setup(tmp_path)
    if tool_id != "create":
        (boundary.root / "note.txt").write_bytes(b"original private text")
    call = ToolCall(
        tool_id, "note.txt", content="changed" if tool_id in {"create", "edit"} else None,
        destination="moved.txt" if tool_id == "move" else None,
    )
    undo_id = _confirm(coordinator, boundary, call)
    restarted = WriteCoordinator(private)
    audit = restarted.audit("chat_a")
    assert audit[0]["undoId"] == undo_id
    assert audit[0]["result"] == "succeeded"
    result = restarted.undo("chat_a", "project_a", boundary, undo_id)
    assert result.status == "succeeded"
    if tool_id == "create":
        assert not (boundary.root / "note.txt").exists()
    else:
        assert (boundary.root / "note.txt").read_bytes() == b"original private text"
    assert not (boundary.root / "moved.txt").exists()
    assert WriteCoordinator(private).undo("chat_a", "project_a", boundary, undo_id).error_code == "undo_unavailable"
    assert restarted.audit("chat_a")[0]["result"] == "undone"


def test_preview_is_exact_and_does_not_write_selected_files(tmp_path: Path) -> None:
    """The visible action carries full parameters while its preview remains a separate bounded string."""

    coordinator, boundary, _ = _setup(tmp_path)
    original = boundary.root / "note.txt"
    original.write_bytes(b"before\n")
    call = ToolCall("edit", "note.txt", content="after\n")
    approval = coordinator.preview("chat_a", None, boundary, call)
    assert set(approval) == {"approvalId", "call", "risk", "preview", "expiresAt"}
    assert approval["call"] == call.to_data()
    assert "-before" in approval["preview"] and "+after" in approval["preview"]
    assert approval["risk"] == "medium"
    assert datetime.fromisoformat(approval["expiresAt"]).utcoffset() == timedelta(0)
    assert original.read_bytes() == b"before\n"
    assert coordinator.audit("chat_a") == []


def test_long_deletion_diff_has_honest_truncation_marker(tmp_path: Path) -> None:
    """A large one-line deletion stays within the preview cap without hiding that some changes are omitted."""

    coordinator, boundary, _ = _setup(tmp_path)
    (boundary.root / "long.txt").write_text("\u4f60" * 50_000, encoding="utf-8")
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("delete", "long.txt"))
    assert len(approval["preview"].encode("utf-8")) <= 32 * 1024
    assert "Preview truncated" in approval["preview"]


def test_decline_and_duplicate_approval_never_write_and_are_audited(tmp_path: Path) -> None:
    """Each token authorizes one decision; declining consumes it just as granting does."""

    coordinator, boundary, _ = _setup(tmp_path)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="private payload"))
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], False).error_code == "permission_denied"
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True).error_code == "permission_denied"
    assert not (boundary.root / "new.txt").exists()
    audit = coordinator.audit("chat_a")
    assert audit[0]["result"] == "denied"
    assert audit[0]["undoId"] is None
    assert "private payload" not in json.dumps(audit)


def test_cross_chat_and_project_approval_is_refused_and_consumed(tmp_path: Path) -> None:
    """A token cannot be replayed through a different Chat or Project context."""

    coordinator, boundary, _ = _setup(tmp_path)
    for wrong_chat, wrong_project in (("chat_b", "project_a"), ("chat_a", "project_b")):
        approval = coordinator.preview("chat_a", "project_a", boundary, ToolCall("create", "new.txt", content="new"))
        assert coordinator.confirm(wrong_chat, wrong_project, boundary, approval["approvalId"], True).status == "denied"
        assert coordinator.confirm("chat_a", "project_a", boundary, approval["approvalId"], True).status == "denied"
    assert not (boundary.root / "new.txt").exists()


def test_expiry_restart_and_invalidation_discard_approval_rights(tmp_path: Path) -> None:
    """Time expiry, restart, and an explicit binding change all require a fresh preview."""

    root = tmp_path / "selected"
    root.mkdir()
    private = tmp_path / "private"
    current = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    coordinator = WriteCoordinator(private, clock=lambda: current)
    boundary = WorkspaceBoundary(root)
    expired = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="new"))
    current += timedelta(minutes=5)
    assert coordinator.confirm("chat_a", None, boundary, expired["approvalId"], True).error_code == "conflict"
    restart = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="new"))
    assert WriteCoordinator(private).confirm("chat_a", None, boundary, restart["approvalId"], True).error_code == "permission_denied"
    invalidated = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="new"))
    coordinator.invalidate("chat_a")
    assert coordinator.confirm("chat_a", None, boundary, invalidated["approvalId"], True).error_code == "permission_denied"
    assert not (root / "new.txt").exists()


def test_changed_file_and_late_destination_require_new_preview(tmp_path: Path) -> None:
    """Stale text and newly occupied names cannot reuse an earlier exact-action approval."""

    coordinator, boundary, _ = _setup(tmp_path)
    target = boundary.root / "note.txt"
    target.write_bytes(b"before")
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("edit", "note.txt", content="after"))
    target.write_bytes(b"external edit")
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True).error_code == "conflict"
    assert target.read_bytes() == b"external edit"
    move = coordinator.preview("chat_a", None, boundary, ToolCall("move", "note.txt", destination="occupied.txt"))
    (boundary.root / "occupied.txt").write_bytes(b"external destination")
    assert coordinator.confirm("chat_a", None, boundary, move["approvalId"], True).error_code == "conflict"
    assert (boundary.root / "occupied.txt").read_bytes() == b"external destination"
    assert all(entry["result"] == "denied" for entry in coordinator.audit("chat_a"))


def test_root_replacement_does_not_transfer_approval_or_undo(tmp_path: Path) -> None:
    """A same-named replacement directory never inherits approval bound to the old filesystem identity."""

    coordinator, boundary, _ = _setup(tmp_path)
    undo_id = _confirm(coordinator, boundary, ToolCall("create", "old.txt", content="old"))
    approval = coordinator.preview("chat_a", "project_a", boundary, ToolCall("create", "new.txt", content="new"))
    boundary.root.rename(tmp_path / "original")
    boundary.root.mkdir()
    current = WorkspaceBoundary(boundary.root)
    assert coordinator.confirm("chat_a", "project_a", current, approval["approvalId"], True).error_code == "conflict"
    assert coordinator.undo("chat_a", "project_a", current, undo_id).error_code == "undo_unavailable"
    assert list(current.root.iterdir()) == []


def test_backup_and_intent_exist_before_any_workspace_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistence order keeps original bytes recoverable even when the mutation fails immediately."""

    coordinator, boundary, private = _setup(tmp_path)
    target = boundary.root / "note.txt"
    target.write_bytes(b"original private bytes")

    def inspect_before_replace(self: WorkspaceMutations, path: str, data: bytes, expected_sha256: str) -> str:
        """Observe the real synced transaction before simulating a failing native mutation."""

        transactions = list((private / "transactions").iterdir())
        assert len(transactions) == 1
        record = json.loads((transactions[0] / "manifest.json").read_bytes())
        assert record["result"] == "prepared"
        assert (transactions[0] / "before.bin").read_bytes() == b"original private bytes"
        target.write_bytes(b"partial write")
        raise WorkspaceBoundaryError("simulated interruption")

    monkeypatch.setattr(WorkspaceMutations, "replace", inspect_before_replace)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("edit", "note.txt", content="new"))
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True).status == "failed"
    assert target.read_bytes() == b"partial write"
    audit = WriteCoordinator(private).audit("chat_a")
    assert audit[0]["result"] == "failed" and audit[0]["recoverable"] is True
    assert audit[0]["undoId"] is None
    assert "original private bytes" not in json.dumps(audit)


def test_backup_failure_prevents_workspace_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed durable before-image never permits the later destructive operation."""

    coordinator, boundary, private = _setup(tmp_path)
    (boundary.root / "note.txt").write_bytes(b"original")
    original_create = WorkspaceMutations.create

    def fail_backup(self: WorkspaceMutations, path: str, data: bytes) -> str:
        """Reject only the private backup publication while retaining real native behavior elsewhere."""

        if path.endswith("before.bin"):
            raise WorkspaceBoundaryError("backup failed")
        return original_create(self, path, data)

    monkeypatch.setattr(WorkspaceMutations, "create", fail_backup)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("delete", "note.txt"))
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True).status == "failed"
    assert (boundary.root / "note.txt").read_bytes() == b"original"
    assert WriteCoordinator(private).audit("chat_a")[0]["recoverable"] is True


@pytest.mark.parametrize("tool_id", ["create", "edit", "move", "delete"])
def test_undo_never_overwrites_external_changes(tmp_path: Path, tool_id: Any) -> None:
    """Undo validates current bytes and name occupancy before consuming its durable right."""

    coordinator, boundary, _ = _setup(tmp_path)
    if tool_id != "create":
        (boundary.root / "note.txt").write_bytes(b"original")
    undo_id = _confirm(coordinator, boundary, ToolCall(
        tool_id, "note.txt", content="after" if tool_id in {"create", "edit"} else None,
        destination="moved.txt" if tool_id == "move" else None,
    ))
    target = boundary.root / ("moved.txt" if tool_id == "move" else "note.txt")
    target.write_bytes(b"external edit")
    assert coordinator.undo("chat_a", "project_a", boundary, undo_id).error_code == "undo_unavailable"
    assert target.read_bytes() == b"external edit"


def test_undo_is_chat_project_bound_and_audit_excludes_other_chats(tmp_path: Path) -> None:
    """Neither audit history nor an Undo capability crosses its persisted owner context."""

    coordinator, boundary, _ = _setup(tmp_path)
    undo_id = _confirm(coordinator, boundary, ToolCall("create", "new.txt", content="private new content"))
    assert coordinator.audit("chat_b") == []
    for chat_id, project_id in (("chat_b", "project_a"), ("chat_a", None), ("chat_a", "project_b")):
        assert coordinator.undo(chat_id, project_id, boundary, undo_id).error_code == "undo_unavailable"
    assert (boundary.root / "new.txt").read_bytes() == b"private new content"
    assert coordinator.undo("chat_a", "project_a", boundary, undo_id).status == "succeeded"


def test_undo_durable_consumption_precedes_mutation_and_exposes_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted Undo remains disclosed for recovery and cannot automatically execute again."""

    coordinator, boundary, private = _setup(tmp_path)
    undo_id = _confirm(coordinator, boundary, ToolCall("create", "new.txt", content="new"))

    def fail_remove(self: WorkspaceMutations, path: str, expected_sha256: str) -> str:
        """Inspect the consumed intent before simulating an interrupted Undo deletion."""

        record = json.loads((private / "transactions" / undo_id / "manifest.json").read_bytes())
        assert record["undoState"] == "executing"
        raise WorkspaceBoundaryError("simulated failure")

    monkeypatch.setattr(WorkspaceMutations, "remove", fail_remove)
    assert coordinator.undo("chat_a", "project_a", boundary, undo_id).status == "failed"
    assert WriteCoordinator(private).undo("chat_a", "project_a", boundary, undo_id).error_code == "undo_unavailable"
    assert coordinator.audit("chat_a")[0]["recoverable"] is True
    assert (boundary.root / "new.txt").read_bytes() == b"new"


def test_corrupted_backup_and_injected_audit_content_fail_closed(tmp_path: Path) -> None:
    """A forged journal cannot leak arbitrary content or authorize restoring unverified private bytes."""

    coordinator, boundary, private = _setup(tmp_path)
    (boundary.root / "note.txt").write_bytes(b"original")
    undo_id = _confirm(coordinator, boundary, ToolCall("edit", "note.txt", content="after"))
    directory = private / "transactions" / undo_id
    (directory / "before.bin").write_bytes(b"untrusted replacement")
    assert coordinator.undo("chat_a", "project_a", boundary, undo_id).error_code == "undo_unavailable"
    assert (boundary.root / "note.txt").read_bytes() == b"after"
    manifest = directory / "manifest.json"
    record = json.loads(manifest.read_bytes())
    record["parameters"]["content"] = "private injected bytes"
    manifest.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ToolProtocolError):
        coordinator.audit("chat_a")
    assert coordinator.undo("chat_a", "project_a", boundary, undo_id).error_code == "undo_unavailable"


def test_sensitive_root_and_binary_or_secret_targets_have_no_approval(tmp_path: Path) -> None:
    """A user-selected private directory cannot bypass the component-based sensitive-file policy."""

    coordinator, boundary, _ = _setup(tmp_path)
    for path in (".env", "credentials.json", "picture.png"):
        with pytest.raises(ToolProtocolError):
            coordinator.preview("chat_a", None, boundary, ToolCall("create", path, content="new"))
    secret_root = tmp_path / ".ssh"
    secret_root.mkdir()
    with pytest.raises(ToolProtocolError, match="restricted"):
        coordinator.preview("chat_a", None, WorkspaceBoundary(secret_root), ToolCall("create", "note.txt", content="new"))
    (boundary.root / "binary.txt").write_bytes(b"\x00binary")
    with pytest.raises(ToolProtocolError):
        coordinator.preview("chat_a", None, boundary, ToolCall("delete", "binary.txt"))
    assert coordinator.audit("chat_a") == []


def test_clock_rollback_cannot_extend_approval_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A visible UTC clock correction cannot preserve a token after its five-minute runtime lease."""

    coordinator, boundary, _ = _setup(tmp_path)
    runtime = 10.0
    monkeypatch.setattr(writes_module, "monotonic", lambda: runtime)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="new"))
    runtime += 300
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True).error_code == "conflict"
    assert not (boundary.root / "new.txt").exists()


def test_returned_approval_cannot_mutate_frozen_action_and_control_content_is_denied(tmp_path: Path) -> None:
    """Changing a renderer-owned preview dict cannot alter stored approval parameters or create binary text."""

    coordinator, boundary, _ = _setup(tmp_path)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="approved"))
    approval["call"]["arguments"]["content"] = "unapproved replacement"
    assert coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True).status == "succeeded"
    assert (boundary.root / "new.txt").read_bytes() == b"approved"
    with pytest.raises(ToolProtocolError):
        coordinator.preview("chat_a", None, boundary, ToolCall("create", "control.txt", content="\x07bell"))
    assert not (boundary.root / "control.txt").exists()


def _seed_audit_records(private: Path, boundary: WorkspaceBoundary, *, count: int, long_paths: bool) -> None:
    """Seed valid crash-recovery records at the persistent boundary, without touching selected workspace files."""

    for index in range(count):
        undo_id = f"undo_{index:032x}"
        directory = private / "transactions" / undo_id
        directory.mkdir()
        source = "/".join(["\u4f60" * 120] * 30 + [f"source{index}.txt"]) if long_paths else f"source{index}.txt"
        destination = "/".join(["\u597d" * 120] * 30 + [f"destination{index}.txt"]) if long_paths else f"destination{index}.txt"
        record = {
            "version": 1, "undoId": undo_id,
            "timestamp": (datetime(2026, 10, 14, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat(),
            "chatId": "chat_a", "projectId": None, "rootPath": boundary.normalized_root,
            "rootIdentity": list(boundary.root_identity), "toolId": "move", "path": source,
            "destination": destination, "beforeHash": "0" * 64, "afterHash": "0" * 64,
            "parameters": {"path": source, "destination": destination}, "result": "prepared",
            "error": None, "undoState": "unavailable", "recoverable": True,
        }
        (directory / "manifest.json").write_bytes(json.dumps(record, ensure_ascii=False).encode("utf-8"))


def test_audit_page_bounds_latest_count_and_discloses_prepared_recovery(tmp_path: Path) -> None:
    """The UI receives the newest canonical history and knows that older entries remain undisplayed."""

    coordinator, boundary, private = _setup(tmp_path)
    _seed_audit_records(private, boundary, count=101, long_paths=False)
    page = coordinator.audit_page("chat_a")
    assert page["truncated"] is True and len(page["entries"]) == 100
    assert page["entries"][0]["parameters"]["path"] == "source100.txt"
    assert page["entries"][-1]["parameters"]["path"] == "source1.txt"
    assert all(entry["result"] == "failed" and entry["recoverable"] and entry["undoId"] is None for entry in page["entries"])
    assert len(coordinator.audit("chat_a")) == 101
    assert coordinator.audit_page("chat_b") == {"entries": [], "truncated": False}


def test_audit_page_bounds_unicode_encoded_bytes_without_shortening_targets(tmp_path: Path) -> None:
    """UTF-8 bytes bound the frame even when allowed Unicode path summaries are much larger than character counts."""

    coordinator, boundary, private = _setup(tmp_path)
    _seed_audit_records(private, boundary, count=100, long_paths=True)
    page = coordinator.audit_page("chat_a")
    assert page["truncated"] is True and 0 < len(page["entries"]) < 100
    assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= 512 * 1024
    assert page["entries"][0]["parameters"]["path"].endswith("source99.txt")
    assert all(entry["parameters"]["path"].count("\u4f60") == 3600 for entry in page["entries"])


def test_new_same_chat_preview_supersedes_old_token_and_keeps_other_chat_approval(tmp_path: Path) -> None:
    """Only the action currently displayed for a Chat can be confirmed; another Chat retains its own right."""

    coordinator, boundary, _ = _setup(tmp_path)
    old = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="old parameters"))
    other = coordinator.preview("chat_b", None, boundary, ToolCall("create", "other.txt", content="other Chat"))
    replacement = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="modified parameters"))
    assert coordinator.confirm("chat_a", None, boundary, old["approvalId"], True).error_code == "permission_denied"
    assert coordinator.confirm("chat_a", None, boundary, replacement["approvalId"], True).status == "succeeded"
    assert coordinator.confirm("chat_b", None, boundary, other["approvalId"], True).status == "succeeded"
    assert (boundary.root / "new.txt").read_bytes() == b"modified parameters"
    assert (boundary.root / "other.txt").read_bytes() == b"other Chat"


def test_invalid_replacement_preview_preserves_current_valid_approval(tmp_path: Path) -> None:
    """Validation failure never discards the still-visible action before a new preview can be shown."""

    coordinator, boundary, _ = _setup(tmp_path)
    old = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="valid action"))
    with pytest.raises(ToolProtocolError):
        coordinator.preview("chat_a", None, boundary, ToolCall("create", "credentials.txt", content="restricted"))
    assert coordinator.confirm("chat_a", None, boundary, old["approvalId"], True).status == "succeeded"
    assert (boundary.root / "new.txt").read_bytes() == b"valid action"


@pytest.mark.parametrize("delay_at", ["preflight", "backup"])
def test_admission_deadline_refuses_delayed_preflight_or_backup_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delay_at: str,
) -> None:
    """A timed-out preparation creates no workspace side effect and records a truthful terminal timeout."""

    coordinator, boundary, private = _setup(tmp_path)
    target = boundary.root / "note.txt"
    target.write_bytes(b"original")
    runtime = 0.0
    monkeypatch.setattr(writes_module, "monotonic", lambda: runtime)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("edit", "note.txt", content="after"))
    original_preflight = coordinator._check_pending
    original_prepare = coordinator._prepare
    apply_calls: list[str] = []

    def delayed_preflight(selected: WorkspaceBoundary, pending: Any) -> None:
        """Advance a controllable clock after a real target preflight has completed."""

        nonlocal runtime
        original_preflight(selected, pending)
        if delay_at == "preflight":
            runtime += 21

    def delayed_backup(record: dict[str, Any], backup: bytes | None) -> None:
        """Advance the same clock only after before-image and intent are actually persisted."""

        nonlocal runtime
        original_prepare(record, backup)
        if delay_at == "backup":
            runtime += 21

    def forbidden_apply(selected: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        """Observe any forbidden mutation admission after the preparation budget expires."""

        apply_calls.append(call.path)

    monkeypatch.setattr(coordinator, "_check_pending", delayed_preflight)
    monkeypatch.setattr(coordinator, "_prepare", delayed_backup)
    monkeypatch.setattr(coordinator, "_apply", forbidden_apply)
    result = coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True)
    assert result.status == "failed" and result.error_code == "timeout"
    assert apply_calls == [] and target.read_bytes() == b"original"
    entry = coordinator.audit("chat_a")[0]
    assert entry["result"] == "failed" and entry["error"] == "timeout" and entry["recoverable"] is False
    assert entry["undoId"] is None
    if delay_at == "backup":
        backups = list((private / "transactions").glob("*/before.bin"))
        assert len(backups) == 1 and backups[0].read_bytes() == b"original"


def test_admitted_write_finishes_truthfully_after_preparation_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An admitted, verified mutation stays succeeded when its non-preemptible native operation runs long."""

    coordinator, boundary, _ = _setup(tmp_path)
    runtime = 0.0
    monkeypatch.setattr(writes_module, "monotonic", lambda: runtime)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="verified"))
    original_apply = coordinator._apply

    def slow_admitted_apply(selected: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        """Complete the real file mutation before simulating a long native write/flush duration."""

        nonlocal runtime
        original_apply(selected, call, before)
        runtime += 100

    monkeypatch.setattr(coordinator, "_apply", slow_admitted_apply)
    result = coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True)
    assert result.status == "succeeded" and result.error_code is None
    assert (boundary.root / "new.txt").read_bytes() == b"verified"
    entry = coordinator.audit("chat_a")[0]
    assert entry["result"] == "succeeded" and entry["error"] is None and entry["undoId"] is not None


def test_approval_invalidation_does_not_wait_for_admitted_native_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation promptly revokes pending rights while an already-admitted write finishes truthfully."""

    coordinator, boundary, _ = _setup(tmp_path)
    approval = coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="verified"))
    entered = Event()
    release = Event()
    revoked = Event()
    outcomes: list[Any] = []
    original_apply = coordinator._apply

    def blocked_apply(selected: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        """Hold native admission with an event instead of sleeping or replacing filesystem verification."""

        entered.set()
        assert release.wait(3)
        original_apply(selected, call, before)

    def revoke() -> None:
        """Signal when the short pending-right revocation has completed."""

        coordinator.invalidate("chat_a")
        revoked.set()

    monkeypatch.setattr(coordinator, "_apply", blocked_apply)
    worker = Thread(target=lambda: outcomes.append(coordinator.confirm("chat_a", None, boundary, approval["approvalId"], True)))
    revoker = Thread(target=revoke)
    worker.start()
    assert entered.wait(2)
    revoker.start()
    try:
        assert revoked.wait(2), "Revocation waited behind admitted native I/O."
        assert not (boundary.root / "new.txt").exists()
    finally:
        release.set()
        worker.join(3)
        revoker.join(3)
    assert not worker.is_alive() and not revoker.is_alive()
    assert outcomes[0].status == "succeeded"
    assert (boundary.root / "new.txt").read_bytes() == b"verified"


def test_invalidation_wins_late_preview_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A preview draining after cancellation cannot create a fresh hidden approval for its retired scope."""

    coordinator, boundary, _ = _setup(tmp_path)
    entered = Event()
    release = Event()
    outcomes: list[Any] = []

    def delayed_clock() -> datetime:
        """Hold a completed native preview immediately before its pending-right publication."""

        entered.set()
        assert release.wait(3)
        return datetime.now(timezone.utc)

    def prepare() -> None:
        """Capture either a visible approval or a safe rejection from the late preview worker."""

        try:
            outcomes.append(coordinator.preview("chat_a", None, boundary, ToolCall("create", "new.txt", content="retired")))
        except ToolProtocolError as error:
            outcomes.append(error)

    monkeypatch.setattr(coordinator, "_clock", delayed_clock)
    worker = Thread(target=prepare)
    worker.start()
    assert entered.wait(2)
    coordinator.invalidate("chat_a")
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert isinstance(outcomes[0], ToolProtocolError) and outcomes[0].code == "permission_denied"
    assert not (boundary.root / "new.txt").exists()
