"""Verify live job budgets through real graphs, approvals, audit, and checkpoint recovery.

Native effects use only generated text files. Event-controlled callbacks and an
injected monotonic clock make expiry races deterministic without real waits,
desktop interaction, model downloads, or access to important user data.
"""

from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import pytest

from agent.work_plan import WorkPlanRunner
from agent.workflow_jobs import WorkflowJobBudget
from agent.workflow_jobs import WorkflowJobError
from agent.workflow_nodes import WorkflowNodes
from agent.workflow_state import workspace_key
from agent.workflow_store import WorkflowStore
from chats import create_chat_session
from desktop_work_plans import DesktopWorkPlans
from desktop_work import DesktopWorkRuntime
from work_tools.contracts import ToolCall, ToolResult
from work_tools.job_wire import validate_work_job
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator
from work_tools.wire import validate_work_result


class _Clock:
    """Supply deterministic deadlines without changing the process-wide monotonic clock."""

    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 5
    while not predicate():
        assert monotonic() < deadline, "Synthetic budget worker did not drain."
        Event().wait(0.002)


def _snapshot(runner: WorkPlanRunner) -> dict[str, Any]:
    snapshot = runner.get()
    assert snapshot is not None
    return {**snapshot, "busy": runner.is_busy()}


def _metadata(call: ToolCall, _cancel: Callable[[], bool]) -> ToolResult:
    return ToolResult("succeeded", {"path": call.path, "name": call.path or "selected", "kind": "file", "sizeBytes": 1})


def _setup(tmp_path: Path) -> tuple[DesktopWorkPlans, Any, Path, WriteCoordinator, WorkflowStore, _Clock]:
    chat = create_chat_session(title="Synthetic budget", mode="work", model_name="test-model")
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "note.txt").write_text("old", encoding="utf-8")
    writer = WriteCoordinator(tmp_path / "private" / "work")
    store = WorkflowStore(tmp_path / "private" / "work" / "workflows")
    manager = DesktopWorkPlans(lambda: chat, lambda _chat: WorkspaceBoundary(selected), lambda: writer, workflow_store=store)
    return manager, chat, selected, writer, store, _Clock()


def _inject_budget(manager: DesktopWorkPlans, chat_id: str, clock: _Clock) -> WorkPlanRunner:
    runner = manager._owners[chat_id].runner
    assert runner is not None
    runner._job = WorkflowJobBudget(clock=clock)
    return runner


def test_blocked_brain_keeps_physical_ownership_until_timeout_is_settled_and_explicitly_retried() -> None:
    """A blocked graph provider cannot release resources or publish data just because its step deadline elapsed."""

    clock = _Clock()
    entered, release = Event(), Event()
    reads: list[str] = []
    audits: list[ToolResult] = []

    def _brain(_query: str, _memory: Any, _rag: Any, _cancel: Callable[[], bool]) -> str:
        entered.set()
        assert release.wait(5)
        return "Only advisory text."

    def _read(call: ToolCall, cancel: Callable[[], bool]) -> ToolResult:
        reads.append(call.path)
        return _metadata(call, cancel)

    runner = WorkPlanRunner(_read, lambda _call: {}, lambda _token, _granted: ToolResult("failed", error_code="io_error"),
                            workflow_nodes=WorkflowNodes(brain_call=_brain), job_budget=WorkflowJobBudget(clock=clock),
                            read_result=lambda _call, result: audits.append(result))
    plan = runner.create("Bound local analysis", [ToolCall("metadata", "note.txt")])
    runner.control(plan["planId"], "start")
    assert entered.wait(5)
    clock.value += 31
    try:
        job = runner.job_observation()
        assert job is not None and job["physicalBusy"] and job["stopReason"] == "timeout"
        assert validate_work_job(job, _snapshot(runner)) == job
        assert runner.is_busy() and reads == []
        with pytest.raises(ValueError):
            runner.retry(plan["planId"], plan["steps"][0]["stepId"])
    finally:
        release.set()
        _wait(lambda: not runner.is_busy())
    failed = _snapshot(runner)
    assert failed["state"] == "failed" and failed["steps"][0]["result"]["error"]["code"] == "timeout"
    assert [result.error_code for result in audits] == ["timeout"] and reads == []
    replacement = runner.retry(plan["planId"], plan["steps"][0]["stepId"])
    assert replacement["planId"] != plan["planId"] and replacement["state"] == "pending"
    assert replacement["steps"][0]["stepId"] == plan["steps"][0]["stepId"]
    observation = runner.job_observation()
    assert observation is not None and observation["totalRemainingSeconds"] == 269 and observation["retryableStepIds"] == []
    runner.control(replacement["planId"], "start")
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner)["state"] == "succeeded" and reads == ["note.txt"]
    assert [result.status for result in audits] == ["failed", "succeeded"]


def test_real_retry_drops_completed_write_and_persists_a_new_pending_suffix_with_fresh_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a failed read once without repeating an already completed mutation or authorizing its pending tail."""

    manager, chat, selected, writer, store, clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    calls = [ToolCall("edit", "note.txt", content="updated"), ToolCall("read_text", "note.txt"),
             ToolCall("create", "tail.txt", content="separate approval")]
    plan = manager.create(chat, "Write, read, then create", calls)
    runner = _inject_budget(manager, chat_id, clock)
    original_read = manager._read
    attempts = 0

    def _transient(owner: Any, call: ToolCall, cancel: Callable[[], bool]) -> ToolResult:
        nonlocal attempts
        attempts += 1
        return ToolResult("failed", error_code="io_error") if attempts == 1 else original_read(owner, call, cancel)

    monkeypatch.setattr(manager, "_read", _transient)
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None
    manager.confirm(chat_id, plan["planId"], waiting["steps"][0]["approval"]["approvalId"], True)
    _wait(lambda: not manager.is_busy())
    failed = manager.get(chat_id)
    assert failed is not None and failed["state"] == "failed" and failed["steps"][0]["status"] == "succeeded"
    boundary = WorkspaceBoundary(selected)
    key = workspace_key(boundary.normalized_root, boundary.root_identity)
    previous = store.load_latest(chat_id, None, key)
    assert previous is not None and previous.state is not None
    clock.value += 40
    replacement = manager.retry(chat_id, plan["planId"], plan["steps"][1]["stepId"])
    assert replacement["planId"] != plan["planId"] and replacement["state"] == "pending"
    assert [step["stepId"] for step in replacement["steps"]] == [step["stepId"] for step in plan["steps"][1:]]
    assert [step["call"] for step in replacement["steps"]] == [call.to_data() for call in calls[1:]]
    observation = runner.job_observation()
    assert observation is not None and observation["totalRemainingSeconds"] == 260
    current = store.load_latest(chat_id, None, key)
    assert current is not None and current.state is not None and current.revision > previous.revision
    assert current.state.to_data()["workflowId"] != previous.state.to_data()["workflowId"]
    assert previous.state.to_data()["plan"]["state"] == "failed"
    assert (selected / "note.txt").read_text("utf-8") == "updated" and not (selected / "tail.txt").exists()
    manager.control(chat_id, replacement["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None and waiting["state"] == "waiting" and waiting["steps"][0]["status"] == "succeeded"
    manager.confirm(chat_id, replacement["planId"], waiting["steps"][1]["approval"]["approvalId"], True)
    _wait(lambda: not manager.is_busy())
    assert (selected / "tail.txt").read_text("utf-8") == "separate approval" and attempts == 2
    history = writer.audit(chat_id)
    assert len([entry for entry in history if entry["toolId"] == "edit"]) == 1
    assert [entry["result"] for entry in history if entry["toolId"] == "read_text"] == ["failed", "succeeded"]


def test_integrated_read_retry_is_explicit_capped_and_cannot_reset_budget() -> None:
    """A fourth attempt is refused, and polling or creating the replacement never performs an automatic read."""

    clock = _Clock()
    reads: list[str] = []

    def _read(call: ToolCall, _cancel: Callable[[], bool]) -> ToolResult:
        reads.append(call.path)
        return ToolResult("failed", error_code="io_error")

    runner = WorkPlanRunner(_read, lambda _call: {}, lambda _token, _granted: ToolResult("failed", error_code="io_error"),
                            job_budget=WorkflowJobBudget(clock=clock))
    plan = runner.create("Explicit capped read", [ToolCall("read_text", "note.txt")])
    step_id = plan["steps"][0]["stepId"]
    for count in (1, 2, 3):
        runner.control(plan["planId"], "start")
        _wait(lambda: not runner.is_busy())
        assert len(reads) == count
        if count < 3:
            clock.value += 30
            plan = runner.retry(plan["planId"], step_id)
            assert len(reads) == count and plan["state"] == "pending"
    with pytest.raises(ValueError):
        runner.retry(plan["planId"], step_id)
    observed = runner.job_observation()
    assert observed is not None and observed["retryableStepIds"] == [] and observed["totalRemainingSeconds"] == 240


def test_failed_retry_checkpoint_cannot_admit_the_new_pending_plan() -> None:
    """A failed durable replacement leaves the retry nonexecuting even if the user tries Start afterward."""

    first_id: str | None = None
    reads: list[str] = []

    def _save(plan: dict[str, Any], _admitted: str | None) -> None:
        nonlocal first_id
        if first_id is None:
            first_id = plan["planId"]
        elif plan["planId"] != first_id:
            raise OSError("synthetic private disk failure")

    def _read(call: ToolCall, _cancel: Callable[[], bool]) -> ToolResult:
        reads.append(call.path)
        return ToolResult("failed", error_code="io_error")

    runner = WorkPlanRunner(_read, lambda _call: {}, lambda _token, _granted: ToolResult("failed", error_code="io_error"), checkpoint=_save)
    plan = runner.create("Retry must persist", [ToolCall("read_text", "note.txt")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    with pytest.raises(OSError):
        runner.retry(plan["planId"], plan["steps"][0]["stepId"])
    pending = _snapshot(runner)
    assert pending["state"] == "pending" and runner.checkpoint_failed()
    with pytest.raises(ValueError):
        runner.control(pending["planId"], "start")
    assert reads == ["note.txt"] and not runner.is_busy()


@pytest.mark.parametrize("paused", [False, True])
def test_total_budget_expires_waiting_approval_as_audited_timeout_even_when_its_native_ttl_is_valid(tmp_path: Path, paused: bool) -> None:
    """Job expiry consumes the waiting token as timeout without granting or silently discarding its decision."""

    manager, chat, selected, writer, _store, clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    plan = manager.create(chat, "Bounded waiting", [ToolCall("edit", "note.txt", content="never applied")])
    runner = _inject_budget(manager, chat_id, clock)
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None and waiting["state"] == "waiting"
    approval_id = waiting["steps"][0]["approval"]["approvalId"]
    assert not writer.approval_expired(chat_id, None, approval_id)
    if paused:
        assert manager.control(chat_id, plan["planId"], "pause")["state"] == "paused"
    clock.value += 300
    manager.get(chat_id)
    _wait(lambda: not manager.is_busy())
    failed = manager.get(chat_id)
    assert failed is not None and failed["state"] == "failed" and failed["steps"][0]["result"]["error"]["code"] == "timeout"
    assert writer.audit(chat_id)[0]["decision"] == "timeout"
    assert writer.confirm(chat_id, None, WorkspaceBoundary(selected), approval_id, True).error_code == "permission_denied"
    assert (selected / "note.txt").read_text("utf-8") == "old"
    job = runner.job_observation()
    assert job is not None and validate_work_job(job, {**failed, "busy": False}) == job


def test_slow_admission_checkpoint_times_out_before_native_write_and_records_no_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkpoint that uses the mutation admission budget must cause a timeout decision before bytes can change."""

    manager, chat, selected, writer, store, clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    plan = manager.create(chat, "Slow prewrite persistence", [ToolCall("edit", "note.txt", content="never applied")])
    _inject_budget(manager, chat_id, clock)
    original_save = store.save
    delayed = False

    def _save(state: Any, *, expected_revision: int | None) -> Any:
        nonlocal delayed
        if state.to_data()["checkpoint"]["disposition"] == "uncertain_write" and not delayed:
            delayed = True
            clock.value += 21
        return original_save(state, expected_revision=expected_revision)

    monkeypatch.setattr(store, "save", _save)
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None
    manager.confirm(chat_id, plan["planId"], waiting["steps"][0]["approval"]["approvalId"], True)
    _wait(lambda: not manager.is_busy())
    failed = manager.get(chat_id)
    assert failed is not None and failed["steps"][0]["result"]["error"]["code"] == "timeout"
    assert delayed and (selected / "note.txt").read_text("utf-8") == "old"
    assert writer.audit(chat_id)[0]["decision"] == "timeout" and not writer.audit(chat_id)[0]["recoverable"]


def test_late_preview_drains_and_consumes_hidden_approval_as_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A preview returned after thirty seconds releases its real callback and cannot retain an undisclosed executable token."""

    manager, chat, selected, writer, _store, clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    plan = manager.create(chat, "Bound preview", [ToolCall("edit", "note.txt", content="never applied")])
    runner = _inject_budget(manager, chat_id, clock)
    actual_preview = writer.preview
    approvals: list[str] = []

    def _preview(*args: Any, **kwargs: Any) -> dict[str, Any]:
        approval = actual_preview(*args, **kwargs)
        approvals.append(approval["approvalId"])
        clock.value += 31
        return approval

    monkeypatch.setattr(writer, "preview", _preview)
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    failed = manager.get(chat_id)
    assert failed is not None and failed["state"] == "failed" and failed["steps"][0]["approval"] is None
    assert failed["steps"][0]["result"]["error"]["code"] == "timeout"
    assert not runner._job.is_busy() and writer.audit(chat_id)[0]["decision"] == "timeout"
    assert writer.confirm(chat_id, None, WorkspaceBoundary(selected), approvals[0], True).error_code == "permission_denied"
    assert (selected / "note.txt").read_text("utf-8") == "old"


def test_admitted_real_write_settles_success_after_cancel_and_total_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native write admitted before cancellation keeps its actual successful bytes, result, and audit."""

    manager, chat, selected, writer, _store, clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    entered, release = Event(), Event()
    actual_apply = writer._apply

    def _apply(*args: Any, **kwargs: Any) -> None:
        entered.set()
        assert release.wait(5)
        actual_apply(*args, **kwargs)

    monkeypatch.setattr(writer, "_apply", _apply)
    plan = manager.create(chat, "Truthful native result", [ToolCall("edit", "note.txt", content="actually written")])
    runner = _inject_budget(manager, chat_id, clock)
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None
    manager.confirm(chat_id, plan["planId"], waiting["steps"][0]["approval"]["approvalId"], True)
    assert entered.wait(5)
    try:
        clock.value += 400
        manager.control(chat_id, plan["planId"], "cancel")
        assert manager.is_busy() and runner._job.is_busy()
    finally:
        release.set()
        _wait(lambda: not manager.is_busy())
    final = manager.get(chat_id)
    assert final is not None and final["state"] == "cancelled" and final["steps"][0]["result"]["status"] == "succeeded"
    assert (selected / "note.txt").read_text("utf-8") == "actually written"
    assert writer.audit(chat_id)[0]["result"] == "succeeded"
    assert not runner._job.is_busy()


def test_checkpoint_and_writer_backup_share_one_mutation_admission_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nineteen checkpoint seconds plus two backup seconds cannot reset the deadline and admit a thirty-eight-second preparation."""

    manager, chat, selected, writer, store, clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    plan = manager.create(chat, "One prewrite deadline", [ToolCall("edit", "note.txt", content="never applied")])
    _inject_budget(manager, chat_id, clock)
    actual_save, actual_prepare = store.save, writer._prepare
    checkpoint_delayed = False
    effects: list[str] = []

    def _save(state: Any, *, expected_revision: int | None) -> Any:
        nonlocal checkpoint_delayed
        if state.to_data()["checkpoint"]["disposition"] == "uncertain_write" and not checkpoint_delayed:
            checkpoint_delayed = True
            clock.value += 19
        return actual_save(state, expected_revision=expected_revision)

    def _prepare(record: dict[str, Any], backup: bytes | None) -> None:
        actual_prepare(record, backup)
        if record["result"] == "prepared":
            clock.value += 2

    def _apply(*_args: Any, **_kwargs: Any) -> None:
        effects.append("unapproved late effect")

    monkeypatch.setattr(store, "save", _save)
    monkeypatch.setattr(writer, "_prepare", _prepare)
    monkeypatch.setattr(writer, "_apply", _apply)
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    waiting = manager.get(chat_id)
    assert waiting is not None
    manager.confirm(chat_id, plan["planId"], waiting["steps"][0]["approval"]["approvalId"], True)
    _wait(lambda: not manager.is_busy())
    final = manager.get(chat_id)
    assert final is not None and final["steps"][0]["result"]["error"]["code"] == "timeout"
    assert checkpoint_delayed and effects == [] and (selected / "note.txt").read_text("utf-8") == "old"
    entry = writer.audit(chat_id)[0]
    assert entry["decision"] == "approve" and entry["error"] == "timeout" and not entry["recoverable"]


def test_retry_checkpoint_blocks_start_but_cancel_and_shutdown_stay_responsive() -> None:
    """Retain private CAS capacity through its real return while foreground controls change logical state promptly."""

    from concurrent.futures import ThreadPoolExecutor

    first_id: str | None = None
    entered, release = Event(), Event()
    records: list[dict[str, Any]] = []

    def _save(plan: dict[str, Any], _admitted: str | None) -> None:
        nonlocal first_id
        if first_id is None:
            first_id = plan["planId"]
        elif plan["planId"] != first_id and plan["state"] == "pending":
            entered.set()
            assert release.wait(5)
        records.append(plan)

    runner = WorkPlanRunner(lambda _call, _cancel: ToolResult("failed", error_code="io_error"), lambda _call: {},
                            lambda _token, _granted: ToolResult("failed", error_code="io_error"), checkpoint=_save)
    plan = runner.create("Block retry CAS", [ToolCall("read_text", "note.txt")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    with ThreadPoolExecutor(max_workers=1) as pool:
        saving = pool.submit(runner.retry, plan["planId"], plan["steps"][0]["stepId"])
        assert entered.wait(5)
        try:
            replacement = _snapshot(runner)
            assert replacement["state"] == "pending" and runner.is_busy()
            with pytest.raises(ValueError):
                runner.control(replacement["planId"], "start")
            started = monotonic()
            assert runner.control(replacement["planId"], "cancel")["state"] == "cancelled"
            assert runner.shutdown() is False
            assert monotonic() - started < 1 and runner.is_busy() and not saving.done()
        finally:
            release.set()
        with pytest.raises(WorkflowJobError) as error:
            saving.result(timeout=5)
        assert error.value.code == "conflict"
    assert not runner.is_busy() and records[-1]["state"] == "cancelled"


def test_a_separately_created_plan_gets_fresh_limits_without_reusing_a_cancelled_lease() -> None:
    """An explicit new task can run after cancellation while an explicit retry would retain its original budget."""

    clock = _Clock()
    runner = WorkPlanRunner(_metadata, lambda _call: {}, lambda _token, _granted: ToolResult("failed", error_code="io_error"),
                            job_budget=WorkflowJobBudget(total_timeout_seconds=5, max_read_retries=1, clock=clock))
    old = runner.create("First explicit task", [ToolCall("metadata", "note.txt")])
    runner.control(old["planId"], "cancel")
    clock.value += 100
    new = runner.create("Second explicit task", [ToolCall("metadata", "note.txt")])
    runner.control(new["planId"], "start")
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner)["state"] == "succeeded"
    observation = runner.job_observation()
    assert observation is not None and observation["totalTimeoutSeconds"] == 5 and observation["maxReadRetries"] == 1


def test_paused_pending_tail_expires_without_preparing_or_executing_its_write() -> None:
    """A pause between completed read and pending write counts toward total time but never auto-admits that write."""

    clock = _Clock()
    runners: list[WorkPlanRunner] = []
    callbacks: list[str] = []

    def _read(call: ToolCall, cancel: Callable[[], bool]) -> ToolResult:
        callbacks.append("read")
        current = _snapshot(runners[0])
        runners[0].control(current["planId"], "pause")
        return _metadata(call, cancel)

    def _preview(_call: ToolCall) -> dict[str, Any]:
        callbacks.append("unexpected preview")
        return {}

    runner = WorkPlanRunner(_read, _preview, lambda _token, _granted: ToolResult("failed", error_code="io_error"),
                            job_budget=WorkflowJobBudget(clock=clock))
    runners.append(runner)
    plan = runner.create("Pause counts", [ToolCall("metadata", "note.txt"), ToolCall("create", "tail.txt", content="never applied")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner)["state"] == "paused"
    clock.value += 300
    runner.expire_budget()
    final = _snapshot(runner)
    assert final["state"] == "failed" and final["steps"][0]["status"] == "succeeded"
    assert final["steps"][1]["result"]["error"]["code"] == "timeout" and callbacks == ["read"]


def test_runtime_job_hints_correlate_to_the_detached_plan_when_worker_settles_between_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly failed read cannot advertise retry against the older running/null plan already sampled for its response."""

    chat = create_chat_session(title="Publication race", mode="work", model_name="test-model")
    chat_id = str(chat.chat_id)
    selected = tmp_path / "selected"
    selected.mkdir()
    runtime = DesktopWorkRuntime(tmp_path / "private" / "work", lambda: chat)
    runtime.dispatch("work.workspace.bind", {"chatId": chat_id, "workspacePath": str(selected)})
    entered, release = Event(), Event()

    def _read(_owner: Any, _call: ToolCall, _cancel: Callable[[], bool]) -> ToolResult:
        entered.set()
        assert release.wait(5)
        return ToolResult("failed", error_code="io_error")

    monkeypatch.setattr(runtime._plans, "_read", _read)
    created = runtime.dispatch("work.plan.create", {"chatId": chat_id, "title": "Read once", "calls": [ToolCall("metadata", "").to_data()]})
    plan_id = created["plan"]["planId"]
    runtime.dispatch("work.plan.control", {"chatId": chat_id, "planId": plan_id, "action": "start"})
    assert entered.wait(5)
    old = runtime._plans.get(chat_id)
    assert old is not None and old["steps"][0]["status"] == "running" and old["steps"][0]["result"] is None
    original_observation = runtime._plans.job_observation

    def _observe(*args: Any, **kwargs: Any) -> Any:
        release.set()
        _wait(lambda: not runtime.is_busy())
        return original_observation(*args, **kwargs)

    monkeypatch.setattr(runtime._plans, "job_observation", _observe)
    try:
        response = runtime._plan_result(chat_id, old)
        assert response["plan"]["steps"][0]["status"] == "running"
        assert response["job"]["retryableStepIds"] == []
        assert validate_work_result(response) == response
        latest = runtime.dispatch("work.plan.get", {"chatId": chat_id})
        assert latest["plan"]["state"] == "failed"
        assert latest["job"]["retryableStepIds"] == [old["steps"][0]["stepId"]]
        assert validate_work_result(latest) == latest
    finally:
        release.set()
        _wait(lambda: not runtime.is_busy())


def test_manager_retry_cannot_publish_pending_after_cancel_wins_its_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the manager's reservation alive during Cancel but reject the retry response once its actual plan is cancelled."""

    from concurrent.futures import ThreadPoolExecutor

    manager, chat, selected, _writer, store, _clock = _setup(tmp_path)
    chat_id = str(chat.chat_id)
    monkeypatch.setattr(manager, "_read", lambda _owner, _call, _cancel: ToolResult("failed", error_code="io_error"))
    plan = manager.create(chat, "Cancel retry publication", [ToolCall("read_text", "note.txt")])
    manager.control(chat_id, plan["planId"], "start")
    _wait(lambda: not manager.is_busy())
    actual_save = store.save
    entered, release = Event(), Event()

    def _save(state: Any, *, expected_revision: int | None) -> Any:
        value = state.to_data()
        if value["plan"]["planId"] != plan["planId"] and value["plan"]["steps"][0]["status"] == "pending":
            entered.set()
            assert release.wait(5)
        return actual_save(state, expected_revision=expected_revision)

    monkeypatch.setattr(store, "save", _save)
    with ThreadPoolExecutor(max_workers=1) as pool:
        saving = pool.submit(manager.retry, chat_id, plan["planId"], plan["steps"][0]["stepId"])
        assert entered.wait(5)
        try:
            replacement = manager.get(chat_id)
            assert replacement is not None and replacement["state"] == "pending"
            manager.control(chat_id, replacement["planId"], "cancel")
            assert manager._creation_token is not None and manager._owners[chat_id].active
            assert manager.is_busy()
        finally:
            release.set()
        with pytest.raises(WorkflowJobError) as error:
            saving.result(timeout=5)
        assert error.value.code == "conflict"
    final = manager.get(chat_id)
    assert final is not None and final["state"] == "cancelled" and not manager.is_busy()
    assert manager._creation_token is None
    boundary = WorkspaceBoundary(selected)
    recovered = store.load_latest(chat_id, None, workspace_key(boundary.normalized_root, boundary.root_identity))
    assert recovered is not None and recovered.state is not None and recovered.state.to_data()["plan"]["state"] == "cancelled"
