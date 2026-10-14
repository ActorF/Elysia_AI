"""Exercise durable single-use approval decisions, expiry cleanup, and legacy audit compatibility."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pytest

import work_tools.writes as writes_module
from work_tools.audit_wire import validate_audit_page
from work_tools.contracts import ToolCall, ToolProtocolError
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator


def _setup(tmp_path: Path) -> tuple[WriteCoordinator, WorkspaceBoundary, Path]:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "note.txt").write_bytes(b"original private body")
    private = tmp_path / "private"
    return WriteCoordinator(private), WorkspaceBoundary(selected), private


def _approval(writer: WriteCoordinator, boundary: WorkspaceBoundary) -> dict[str, Any]:
    return writer.preview("chat_owner", "project_owner", boundary, ToolCall("edit", "note.txt", content="new private payload"))


@pytest.mark.parametrize("decision", ["approve", "reject", "modify", "timeout"])
def test_each_decision_is_single_use_durable_redacted_and_matches_actual_mutation(tmp_path: Path, decision: Any) -> None:
    """Only approval changes bytes; every other exact decision consumes its token and persists its fixed outcome."""

    writer, boundary, private = _setup(tmp_path)
    approval = _approval(writer, boundary)
    result = writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], decision == "approve", decision=decision)
    assert writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True).error_code == "permission_denied"
    assert (boundary.root / "note.txt").read_bytes() == (b"new private payload" if decision == "approve" else b"original private body")
    history = {"kind": "work.audit", **WriteCoordinator(private).audit_page("chat_owner")}
    assert validate_audit_page(history) == history
    assert len(history["entries"]) == 1
    entry = history["entries"][0]
    assert entry["decision"] == decision
    assert entry["result"] == result.status
    assert entry["recoverable"] is False
    assert entry["undoId"] is not None if decision == "approve" else entry["undoId"] is None
    encoded = json.dumps(history)
    assert "private payload" not in encoded and "private body" not in encoded
    assert approval["approvalId"] not in encoded and str(boundary.root) not in encoded


@pytest.mark.parametrize("decision,granted", [("approve", False), ("reject", True), ("modify", True),
    ("timeout", True), ("command", False), (True, False), ({"approved": True}, False)])
def test_invalid_or_inconsistent_decision_does_not_consume_valid_token(tmp_path: Path, decision: Any, granted: bool) -> None:
    """Malformed authenticated requests cannot turn contradictory fields into permission or silently revoke a valid preview."""

    writer, boundary, _ = _setup(tmp_path)
    approval = _approval(writer, boundary)
    result = writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], granted, decision=decision)
    assert result.error_code == "invalid_request" and writer.audit("chat_owner") == []
    assert writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], False, decision="modify").error_code == "permission_denied"
    assert writer.audit("chat_owner")[0]["decision"] == "modify"
    assert (boundary.root / "note.txt").read_bytes() == b"original private body"


@pytest.mark.parametrize("expiry", ["utc", "monotonic"])
def test_expired_late_approval_is_audited_as_timeout_without_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expiry: str) -> None:
    """Both clocks independently bound the token; a late approve becomes a truthful timeout outcome."""

    writer, boundary, private = _setup(tmp_path)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    tick = 0.0
    monkeypatch.setattr(writer, "_clock", lambda: now)
    monkeypatch.setattr(writes_module, "monotonic", lambda: tick)
    approval = _approval(writer, boundary)
    if expiry == "utc":
        now += timedelta(minutes=5)
    else:
        now -= timedelta(days=1)
        tick += 300
    result = writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True)
    assert result.status == "failed" and result.error_code == "timeout"
    entry = WriteCoordinator(private).audit("chat_owner")[0]
    assert entry["decision"] == "timeout" and entry["error"] == "timeout"
    assert (boundary.root / "note.txt").read_bytes() == b"original private body"


@pytest.mark.parametrize("cleanup", ["preview", "audit"])
def test_cleanup_records_expiry_before_old_token_disappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str) -> None:
    """A new preview or history read cannot silently discard an expired waiting decision."""

    writer, boundary, _ = _setup(tmp_path)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(writer, "_clock", lambda: now)
    approval = _approval(writer, boundary)
    now += timedelta(minutes=5)
    if cleanup == "preview":
        writer.preview("chat_other", None, boundary, ToolCall("create", "other.txt", content="new"))
    else:
        writer.audit("chat_owner")
    assert writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True).error_code == "permission_denied"
    history = writer.audit("chat_owner")
    assert len(history) == 1 and history[0]["decision"] == "timeout"
    assert writer.audit("chat_other") == []


def test_approval_is_persisted_before_mutation_and_actual_failure_retains_approved_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The prewrite journal links the actual admitted action to its human decision even when native mutation fails."""

    writer, boundary, private = _setup(tmp_path)
    approval = _approval(writer, boundary)
    seen: list[str] = []

    def _fail(selected: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        manifests = list((private / "transactions").glob("*/manifest.json"))
        assert len(manifests) == 1
        record = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert record["version"] == 2 and record["decision"] == "approve" and record["result"] == "prepared"
        seen.append("admitted")
        raise OSError("private native path diagnostic")

    monkeypatch.setattr(writer, "_apply", _fail)
    result = writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True)
    assert result.error_code == "io_error" and seen == ["admitted"]
    entry = WriteCoordinator(private).audit("chat_owner")[0]
    assert entry["decision"] == "approve" and entry["result"] == "failed" and entry["recoverable"]
    assert "diagnostic" not in json.dumps(entry)


def test_decision_journal_failure_prevents_write_and_cannot_replay_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unavailable decision journal fails closed before any target mutation, and never invents a recorded success."""

    writer, boundary, _ = _setup(tmp_path)
    approval = _approval(writer, boundary)

    def _fail(record: dict[str, Any], backup: bytes | None) -> None:
        raise OSError("private storage unavailable")

    monkeypatch.setattr(writer, "_prepare", _fail)
    assert writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True).error_code == "io_error"
    assert writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True).error_code == "permission_denied"
    assert (boundary.root / "note.txt").read_bytes() == b"original private body"


def test_legacy_journal_has_unknown_decision_and_conditional_undo_remains_available(tmp_path: Path) -> None:
    """Version 1 logs remain loadable without inventing a historical approval decision."""

    writer, boundary, private = _setup(tmp_path)
    approval = _approval(writer, boundary)
    result = writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True)
    assert result.data is not None
    manifest = private / "transactions" / result.data["undoId"] / "manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["version"] = 1
    record.pop("decision")
    manifest.write_text(json.dumps(record), encoding="utf-8")
    restarted = WriteCoordinator(private)
    assert restarted.audit("chat_owner")[0]["decision"] is None
    assert restarted.undo("chat_owner", "project_owner", boundary, result.data["undoId"]).status == "succeeded"
    assert (boundary.root / "note.txt").read_bytes() == b"original private body"


@pytest.mark.parametrize("decision", ["command", "timeout", "modify"])
def test_tampered_decision_journal_cannot_claim_approved_success(tmp_path: Path, decision: str) -> None:
    """Strict version 2 history rejects unknown or nonmutating decisions attached to a successful write."""

    writer, boundary, private = _setup(tmp_path)
    approval = _approval(writer, boundary)
    result = writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True)
    assert result.data is not None
    manifest = private / "transactions" / result.data["undoId"] / "manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["decision"] = decision
    manifest.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ToolProtocolError):
        WriteCoordinator(private).audit("chat_owner")


def test_public_audit_accepts_legacy_missing_decision_but_rejects_forged_nonmutating_success(tmp_path: Path) -> None:
    """New audit disclosure remains backward compatible while decisions cannot smuggle write or Undo rights."""

    writer, boundary, _ = _setup(tmp_path)
    approval = _approval(writer, boundary)
    writer.confirm("chat_owner", "project_owner", boundary, approval["approvalId"], True)
    history = {"kind": "work.audit", **writer.audit_page("chat_owner")}
    legacy = deepcopy(history)
    legacy["entries"][0].pop("decision")
    assert validate_audit_page(legacy) == legacy
    for decision in ("modify", "timeout", {"approve": True}):
        forged = deepcopy(history)
        forged["entries"][0]["decision"] = decision
        with pytest.raises(ValueError):
            validate_audit_page(forged)
