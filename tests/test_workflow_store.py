"""Verify private latest workflow checkpoints, durable CAS, owner filtering, and link refusal."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from agent.workflow_state import WorkflowState, WorkflowStateError, make_checkpoint, workflow_from_plan
from agent.workflow_store import WorkflowStore
import agent.workflow_store as store_module
from work_tools.contracts import ToolCall


_KEY = "a" * 64
_TIME = "2026-10-14T20:00:00+00:00"


def _live(*, chat: str = "chat_a", project: str | None = "project_a", key: str = _KEY) -> WorkflowState:
    return workflow_from_plan({
        "planId": "plan_" + "1" * 32, "title": "Read notes", "state": "pending", "busy": False,
        "steps": [{"stepId": "step_" + "2" * 32, "call": ToolCall("read_text", "notes.txt").to_data(),
                   "status": "pending", "result": None, "approval": None}],
    }, chat_id=chat, project_id=project, selected_workspace_key=key, created_at=_TIME)


def _checkpoint(**kwargs: Any) -> WorkflowState:
    return make_checkpoint(_live(**kwargs), saved_at=_TIME)


def _record_path(directory: Path) -> Path:
    return next(directory.glob("state_*.json"))


def test_latest_checkpoint_survives_restart_and_never_grants_a_live_permission(tmp_path: Path) -> None:
    """Persist a detached paused checkpoint and reconstruct only recovery data after restart."""

    directory = tmp_path / "private" / "workflows"
    store = WorkflowStore(directory)
    state = _checkpoint()
    saved = store.save(state, expected_revision=None)
    assert saved.revision == 1 and saved.state == state
    loaded = WorkflowStore(directory).load_latest("chat_a", "project_a", _KEY)
    assert loaded is not None and loaded.state is not None and loaded.revision == 1
    assert loaded.state.to_data() == state.to_data()
    assert loaded.state.to_data()["plan"]["state"] == "paused"
    assert loaded.state.to_data()["checkpoint"]["requiresRevalidation"] is True
    detached = loaded.state.to_data()
    detached["request"]["chatId"] = "chat_b"
    assert loaded.state.to_data()["request"]["chatId"] == "chat_a"
    assert not list(directory.glob("*.tmp"))


def test_scope_mismatch_exposes_only_cas_head_and_explicit_new_plan_can_replace_it(tmp_path: Path) -> None:
    """Hide old Project/Workspace content without losing the revision needed for intentional replacement."""

    store = WorkflowStore(tmp_path / "private")
    store.save(_checkpoint(), expected_revision=None)
    assert store.load_latest("chat_b", "project_a", _KEY) is None
    for project, key in ((None, _KEY), ("project_b", _KEY), ("project_a", "b" * 64)):
        hidden = store.load_latest("chat_a", project, key)
        assert hidden is not None and hidden.revision == 1 and hidden.state is None
    replacement = _checkpoint(project="project_b", key="b" * 64)
    assert store.save(replacement, expected_revision=1).revision == 2
    visible = store.load_latest("chat_a", "project_b", "b" * 64)
    assert visible is not None and visible.state is not None
    assert visible.state.to_data()["workflowId"] == replacement.to_data()["workflowId"]


@pytest.mark.parametrize("expected", [None, 0, True, 2, -1, "1"])
def test_stale_or_untyped_cas_cannot_replace_a_saved_checkpoint(tmp_path: Path, expected: Any) -> None:
    """Reject absent, stale, boolean, and malformed revisions before changing any durable bytes."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    store.save(_checkpoint(), expected_revision=None)
    before = _record_path(directory).read_bytes()
    with pytest.raises(WorkflowStateError, match="changed"):
        store.save(_checkpoint(), expected_revision=expected)
    assert _record_path(directory).read_bytes() == before


def test_same_workflow_revision_cannot_move_backwards_and_tombstone_blocks_old_writers(tmp_path: Path) -> None:
    """Keep workflow observations monotonic and deletion durable even across process restart."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    first = _checkpoint()
    store.save(first, expected_revision=None)
    with pytest.raises(WorkflowStateError):
        store.save(first, expected_revision=1)
    second = make_checkpoint(first, saved_at=_TIME)
    assert store.save(second, expected_revision=1).revision == 2
    assert store.forget("chat_a", expected_revision=2).revision == 3
    restarted = WorkflowStore(directory)
    forgotten = restarted.load_latest("chat_a", "project_a", _KEY)
    assert forgotten is not None and forgotten.revision == 3 and forgotten.state is None
    with pytest.raises(WorkflowStateError):
        restarted.save(second, expected_revision=2)
    assert restarted.save(_checkpoint(), expected_revision=3).revision == 4


def test_two_instances_racing_the_same_cas_allow_only_one_writer(tmp_path: Path) -> None:
    """Serialize a complete revision check/publication across independent store objects."""

    directory = tmp_path / "private"
    first, second = WorkflowStore(directory), WorkflowStore(directory)
    first.save(_checkpoint(), expected_revision=None)

    def _write(store: WorkflowStore) -> str:
        try:
            store.save(_checkpoint(), expected_revision=1)
            return "saved"
        except WorkflowStateError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_write, (first, second)))
    assert sorted(results) == ["saved", "state_conflict"]
    head = first.load_latest("chat_a", "project_a", _KEY)
    assert head is not None and head.revision == 2


def test_native_lock_refuses_a_second_process_without_changing_private_records(tmp_path: Path) -> None:
    """A separate application process cannot enter a CAS transaction while its native lock is owned."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    store.save(_checkpoint(), expected_revision=None)
    before = _record_path(directory).read_bytes()
    script = (
        "from pathlib import Path; import sys; "
        "from agent.workflow_store import WorkflowStore; from agent.workflow_state import WorkflowStateError\n"
        "try:\n WorkflowStore(Path(sys.argv[1])).forget('chat_a',expected_revision=1)\n"
        "except WorkflowStateError:\n print('blocked')\n"
        "else:\n print('unexpected write')\n"
    )
    with store._locked():
        child = subprocess.run([sys.executable, "-c", script, str(directory)], capture_output=True,
                               text=True, timeout=10, check=True)
    assert child.stdout.strip() == "blocked" and child.stderr == ""
    assert _record_path(directory).read_bytes() == before


def test_unprepared_live_state_and_scope_pollution_never_reach_storage(tmp_path: Path) -> None:
    """Persist only checkpoint-prepared JSON and validate canonical owner/key inputs before accessing records."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    with pytest.raises(WorkflowStateError):
        store.save(_live(), expected_revision=None)
    for chat, project, key in (("chat_../outside", None, _KEY), ("chat_a", "project_" + "x" * 121, _KEY),
                               ("chat_a", None, "not a key")):
        with pytest.raises(WorkflowStateError):
            store.load_latest(chat, project, key)
    assert not list(directory.glob("state_*.json"))


@pytest.mark.parametrize("fault", ["duplicate", "extra", "version", "owner", "unprepared", "oversized"])
def test_malformed_or_cross_owner_private_records_fail_closed(tmp_path: Path, fault: str) -> None:
    """Reject ambiguous, unsupported, oversized, and forged envelopes using fixed diagnostics."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    store.save(_checkpoint(), expected_revision=None)
    path = _record_path(directory)
    value = json.loads(path.read_bytes())
    if fault == "duplicate":
        raw = path.read_bytes().replace(b'"version":1', b'"version":1,"version":1', 1)
    elif fault == "oversized":
        raw = b"x" * (store_module._MAX_RECORD_BYTES + 1)
    else:
        if fault == "extra":
            value["secret"] = "private corrupted payload"
        elif fault == "version":
            value["version"] = True
        elif fault == "owner":
            value["chatId"] = "chat_b"
        else:
            value["state"] = _live().to_data()
        raw = json.dumps(value).encode("utf-8")
    path.write_bytes(raw)
    with pytest.raises(WorkflowStateError) as error:
        store.load_latest("chat_a", "project_a", _KEY)
    assert "private corrupted payload" not in str(error.value) and str(directory) not in str(error.value)


def test_failed_atomic_publication_preserves_old_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse publication failure without damaging the previous checkpoint or claiming a new revision."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    store.save(_checkpoint(), expected_revision=None)
    before = _record_path(directory).read_bytes()
    if os.name == "nt":
        monkeypatch.setattr(store_module._kernel32, "MoveFileExW", lambda *_args: 0)
    else:
        def _fail(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("private rename failure")
        monkeypatch.setattr(store_module.os, "replace", _fail)
    with pytest.raises(WorkflowStateError):
        store.save(_checkpoint(), expected_revision=1)
    assert _record_path(directory).read_bytes() == before and not list(directory.glob("*.tmp"))


def test_replaced_root_and_hardlinked_record_do_not_restore_or_modify_external_files(tmp_path: Path) -> None:
    """Keep recovery inside its original non-link root and refuse aliases to another file."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    store.save(_checkpoint(), expected_revision=None)
    path = _record_path(directory)
    outside = tmp_path / "external.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    os.link(outside, path)
    with pytest.raises(WorkflowStateError):
        store.load_latest("chat_a", "project_a", _KEY)
    assert outside.read_bytes() == path.read_bytes()
    directory.rename(tmp_path / "retired")
    directory.mkdir()
    with pytest.raises(WorkflowStateError):
        store.forget("chat_a", expected_revision=1)
    assert not list(directory.iterdir())


def test_record_capacity_refuses_new_chat_but_allows_existing_head_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound retained latest records without deleting another Chat's recovery checkpoint."""

    store = WorkflowStore(tmp_path / "private")
    monkeypatch.setattr(store_module, "_MAX_RECORDS", 1)
    store.save(_checkpoint(), expected_revision=None)
    with pytest.raises(WorkflowStateError):
        store.save(_checkpoint(chat="chat_b"), expected_revision=None)
    assert store.save(_checkpoint(), expected_revision=1).revision == 2


def test_newer_cas_does_not_allow_same_workflow_to_change_its_canonical_owner(tmp_path: Path) -> None:
    """Require a fresh workflow identity for intentional Project/Workspace replacement, even with a current CAS."""

    directory = tmp_path / "private"
    store = WorkflowStore(directory)
    first = _checkpoint()
    store.save(first, expected_revision=None)
    changed = _checkpoint(project="project_b").to_data()
    changed.update(workflowId=first.to_data()["workflowId"], revision=2)
    before = _record_path(directory).read_bytes()
    with pytest.raises(WorkflowStateError):
        store.save(WorkflowState(changed), expected_revision=1)
    assert _record_path(directory).read_bytes() == before


def test_uncertain_write_stays_nonexecuting_and_preserves_manual_review_disposition(tmp_path: Path) -> None:
    """Store interrupted admitted writes as data requiring journal review, never as a replayable pending operation."""

    plan = {
        "planId": "plan_" + "1" * 32, "title": "Write notes", "state": "running", "busy": True,
        "steps": [{"stepId": "step_" + "2" * 32, "call": ToolCall("edit", "notes.txt", content="private text").to_data(),
                   "status": "running", "result": None, "approval": None}],
    }
    state = make_checkpoint(workflow_from_plan(plan, chat_id="chat_a", project_id=None,
                                              selected_workspace_key=_KEY, created_at=_TIME), saved_at=_TIME)
    directory = tmp_path / "private"
    WorkflowStore(directory).save(state, expected_revision=None)
    loaded = WorkflowStore(directory).load_latest("chat_a", None, _KEY)
    assert loaded is not None and loaded.state is not None
    value = loaded.state.to_data()
    assert value["checkpoint"]["disposition"] == "uncertain_write"
    assert value["plan"]["state"] == "paused" and value["plan"]["steps"][0]["status"] == "running"
    assert value["admittedStepId"] == value["plan"]["steps"][0]["stepId"]
    assert value["error"]["code"] == "recovery_required"
    assert value["plan"]["steps"][0]["approval"] is None
    assert not (tmp_path / "notes.txt").exists()


@pytest.mark.parametrize("target", ["directory", "record", "lock"])
def test_symlinked_private_roots_records_and_lock_files_are_refused(tmp_path: Path, target: str) -> None:
    """Reject redirected private storage before a checkpoint can be loaded or another file changed."""

    outside = tmp_path / "outside"
    outside.mkdir()
    directory = tmp_path / "private"
    if target == "directory":
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("The host does not permit directory symlinks.")
        with pytest.raises(WorkflowStateError):
            WorkflowStore(directory)
    else:
        store = WorkflowStore(directory)
        store.save(_checkpoint(), expected_revision=None)
        path = _record_path(directory) if target == "record" else directory / ".workflow-store.lock"
        original = path.read_bytes()
        external = outside / "unchanged.json"
        external.write_bytes(original)
        path.unlink()
        try:
            path.symlink_to(external)
        except OSError:
            pytest.skip("The host does not permit file symlinks.")
        with pytest.raises(WorkflowStateError):
            store.load_latest("chat_a", "project_a", _KEY)
        assert external.read_bytes() == original
