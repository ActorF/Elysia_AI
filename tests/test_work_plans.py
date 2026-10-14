"""Exercise explicit Work plans, single-worker races, approvals, and shutdown."""

from collections.abc import Callable
import json
from threading import Event
from time import monotonic
from typing import Any

import pytest

import agent.work_plan as plan_module
from agent.work_plan import WorkPlanRunner
from work_tools.contracts import TOOL_CATALOG, ToolCall, ToolResult


def _wait(predicate: Callable[[], bool]) -> None:
    """Wait briefly for a worker checkpoint after an event-controlled callback."""

    deadline = monotonic() + 3.0
    tick = Event()
    while not predicate():
        assert monotonic() < deadline, "Work plan checkpoint did not settle."
        tick.wait(0.002)


def _snapshot(runner: WorkPlanRunner) -> dict[str, Any]:
    snapshot = runner.get()
    assert snapshot is not None
    return snapshot


class _Callbacks:
    """Provide deterministic tools with events around potentially blocking calls."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.previews: list[str] = []
        self.confirms: list[tuple[str, bool]] = []
        self.approvals: dict[str, ToolCall] = {}
        self.read_entered = Event()
        self.read_release = Event()
        self.write_entered = Event()
        self.write_release = Event()
        self.block_read = False
        self.block_write = False
        self.read_error = False
        self.read_result: ToolResult | None = None

    def read(self, call: ToolCall, should_cancel: Callable[[], bool]) -> ToolResult:
        """Return safe metadata or an event-controlled failure/cancellation."""

        self.reads.append(call.path)
        self.read_entered.set()
        if self.block_read:
            assert self.read_release.wait(3.0)
        if should_cancel():
            return ToolResult("cancelled", error_code="cancelled")
        if self.read_error:
            raise RuntimeError("C:/private/secret raw native diagnostics")
        if self.read_result is not None:
            return self.read_result
        return ToolResult("succeeded", {"path": call.path, "name": call.path, "kind": "file", "sizeBytes": 1})

    def preview(self, call: ToolCall) -> dict[str, Any]:
        """Return a separate owner-bound approval for each exact write call."""

        self.previews.append(call.path)
        approval_id = f"approval_{len(self.previews):032x}"
        self.approvals[approval_id] = call
        descriptor = next(item for item in TOOL_CATALOG if item.tool_id == call.tool_id)
        return {
            "approvalId": approval_id, "call": call.to_data(), "risk": descriptor.risk,
            "preview": "--- before\n+++ after\n+safe text\n", "expiresAt": "2030-01-01T00:00:00+00:00",
        }

    def confirm(self, approval_id: str, granted: bool) -> ToolResult:
        """Record one explicit decision and finish an already-authorized mutation."""

        self.confirms.append((approval_id, granted))
        self.write_entered.set()
        if self.block_write:
            assert self.write_release.wait(3.0)
        if not granted:
            return ToolResult("denied", error_code="permission_denied")
        call = self.approvals[approval_id]
        data = {"path": call.path, "undoId": "undo_1"}
        if call.destination is not None:
            data["destination"] = call.destination
        return ToolResult("succeeded", data)

    def runner(self) -> WorkPlanRunner:
        """Create one runner bound to these deterministic owner callbacks."""

        return WorkPlanRunner(self.read, self.preview, self.confirm)


def test_explicit_plan_runs_sequentially_and_snapshots_are_detached() -> None:
    """Execute supplied calls once in order without inferring actions from title text."""

    callbacks = _Callbacks()
    runner = callbacks.runner()
    calls = [ToolCall("metadata", "first.txt"), ToolCall("metadata", "second.txt")]
    plan = runner.create("Delete everything is only a title", calls)
    assert set(plan) == {"planId", "title", "state", "steps"}
    assert set(plan["steps"][0]) == {"stepId", "call", "status", "result", "approval"}
    plan["steps"][0]["call"]["arguments"]["path"] = "changed.txt"
    calls.clear()
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    complete = _snapshot(runner)
    assert complete["state"] == "succeeded"
    assert [step["status"] for step in complete["steps"]] == ["succeeded", "succeeded"]
    assert callbacks.reads == ["first.txt", "second.txt"]
    assert not callbacks.previews and not callbacks.confirms
    complete["steps"][0]["result"]["data"]["path"] = "mutated.txt"
    assert _snapshot(runner)["steps"][0]["result"]["data"]["path"] == "first.txt"


@pytest.mark.parametrize("title,calls", [
    ("", [ToolCall("metadata", "")]), ("x" * 201, [ToolCall("metadata", "")]),
    ("bad\x00title", [ToolCall("metadata", "")]), ("bad\ud800title", [ToolCall("metadata", "")]),
    ("\ufeff", [ToolCall("metadata", "")]),
    ("Empty", []), ("Too many", [ToolCall("metadata", "")] * 17), ("Text inference", ["delete a file"]),
])
def test_plan_creation_rejects_unbounded_or_untyped_inputs(title: str, calls: Any) -> None:
    """Require a bounded title and validated calls before any worker admission."""

    runner = _Callbacks().runner()
    with pytest.raises(ValueError):
        runner.create(title, calls)
    assert runner.get() is None and not runner.is_busy()


def test_plan_title_matches_the_shared_protocol_without_silent_normalization() -> None:
    """Preserve display text while applying the same explicit blank set as Electron."""

    runner = _Callbacks().runner()
    title = "  Read notes  "
    assert runner.create(title, [ToolCall("metadata", "")])["title"] == title
    assert runner.create("\x1c", [ToolCall("metadata", "")])["title"] == "\x1c"


def test_pause_stops_after_current_step_and_resume_never_repeats_it() -> None:
    """Leave native reads outside the state lock and pause between exact calls."""

    callbacks = _Callbacks()
    callbacks.block_read = True
    runner = callbacks.runner()
    plan = runner.create("Two reads", [ToolCall("metadata", "first.txt"), ToolCall("metadata", "second.txt")])
    runner.control(plan["planId"], "start")
    assert callbacks.read_entered.wait(2.0)
    paused = runner.control(plan["planId"], "pause")
    assert paused["state"] == "paused" and runner.is_busy()
    with pytest.raises(ValueError, match="active Work step"):
        runner.create("Replacement", [ToolCall("metadata", "third.txt")])
    callbacks.read_release.set()
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner)["state"] == "paused"
    assert callbacks.reads == ["first.txt"]
    runner.control(plan["planId"], "resume")
    _wait(lambda: not runner.is_busy())
    assert callbacks.reads == ["first.txt", "second.txt"]
    assert _snapshot(runner)["state"] == "succeeded"


def test_resume_while_current_read_drains_does_not_launch_a_duplicate_worker() -> None:
    """Clear a pause request on the same worker without duplicating its active call."""

    callbacks = _Callbacks()
    callbacks.block_read = True
    runner = callbacks.runner()
    plan = runner.create("Reads", [ToolCall("metadata", "one.txt"), ToolCall("metadata", "two.txt")])
    runner.control(plan["planId"], "next")
    assert callbacks.read_entered.wait(2.0)
    with pytest.raises(ValueError):
        runner.control(plan["planId"], "start")
    runner.control(plan["planId"], "pause")
    runner.control(plan["planId"], "resume")
    callbacks.read_release.set()
    _wait(lambda: not runner.is_busy())
    assert callbacks.reads == ["one.txt", "two.txt"]


def test_cancel_discards_late_read_and_blocks_replacement_until_native_drain() -> None:
    """Keep physical capacity owned after logical cancellation without stale results."""

    callbacks = _Callbacks()
    callbacks.block_read = True
    runner = callbacks.runner()
    plan = runner.create("Cancel", [ToolCall("metadata", "one.txt"), ToolCall("metadata", "two.txt")])
    runner.control(plan["planId"], "start")
    assert callbacks.read_entered.wait(2.0)
    cancelled = runner.control(plan["planId"], "cancel")
    assert cancelled["state"] == "cancelled"
    assert all(step["status"] == "cancelled" for step in cancelled["steps"])
    with pytest.raises(ValueError):
        runner.create("Too early", [ToolCall("metadata", "new.txt")])
    callbacks.read_release.set()
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner) == cancelled
    replacement = runner.create("New owner plan", [ToolCall("metadata", "new.txt")])
    assert replacement["planId"] != plan["planId"]
    with pytest.raises(ValueError, match="no longer belongs"):
        runner.control(plan["planId"], "start")
    assert callbacks.reads == ["one.txt"]


def test_every_write_requires_its_own_exact_approval_and_then_verification_read() -> None:
    """Wait on two distinct write tokens instead of granting a whole plan permanently."""

    callbacks = _Callbacks()
    runner = callbacks.runner()
    plan = runner.create("Write and verify", [
        ToolCall("create", "one.txt", content="one"), ToolCall("create", "two.txt", content="two"),
        ToolCall("metadata", "two.txt"),
    ])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    waiting = _snapshot(runner)
    assert waiting["state"] == "waiting" and callbacks.confirms == []
    token1 = waiting["steps"][0]["approval"]["approvalId"]
    with pytest.raises(ValueError):
        runner.confirm(plan["planId"], "wrong_token", True)
    with pytest.raises(ValueError):
        runner.control(plan["planId"], "next")
    runner.confirm(plan["planId"], token1, True)
    with pytest.raises(ValueError):
        runner.confirm(plan["planId"], token1, True)
    _wait(lambda: not runner.is_busy())
    second = _snapshot(runner)
    assert second["state"] == "waiting"
    token2 = second["steps"][1]["approval"]["approvalId"]
    assert token1 != token2 and callbacks.confirms == [(token1, True)]
    runner.confirm(plan["planId"], token2, True)
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner)["state"] == "succeeded"
    assert callbacks.confirms == [(token1, True), (token2, True)]
    assert callbacks.reads == ["two.txt"]


def test_waiting_plan_pause_resume_preserves_approval_without_repreview() -> None:
    """Keep approval identity stable across pause while still requiring an explicit decision."""

    callbacks = _Callbacks()
    runner = callbacks.runner()
    plan = runner.create("Approval", [ToolCall("delete", "one.txt")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    token = _snapshot(runner)["steps"][0]["approval"]["approvalId"]
    runner.control(plan["planId"], "pause")
    with pytest.raises(ValueError):
        runner.confirm(plan["planId"], token, True)
    resumed = runner.control(plan["planId"], "resume")
    assert resumed["state"] == "waiting"
    assert resumed["steps"][0]["approval"]["approvalId"] == token
    assert callbacks.previews == ["one.txt"]
    runner.confirm(plan["planId"], token, False)
    _wait(lambda: not runner.is_busy())
    declined = _snapshot(runner)
    assert declined["state"] == "failed"
    assert declined["steps"][0]["result"]["error"]["code"] == "permission_denied"


def test_cancel_after_write_authorization_retains_truthful_result_and_stops_next_steps() -> None:
    """Allow an approved atomic write to settle while cancel prevents later execution."""

    callbacks = _Callbacks()
    callbacks.block_write = True
    runner = callbacks.runner()
    plan = runner.create("Authorized write", [ToolCall("create", "one.txt", content="one"), ToolCall("metadata", "one.txt")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    token = _snapshot(runner)["steps"][0]["approval"]["approvalId"]
    runner.confirm(plan["planId"], token, True)
    assert callbacks.write_entered.wait(2.0)
    during = runner.control(plan["planId"], "cancel")
    assert during["state"] == "cancelled" and runner.is_busy()
    assert during["steps"][0]["status"] == "running"
    callbacks.write_release.set()
    _wait(lambda: not runner.is_busy())
    complete = _snapshot(runner)
    assert complete["state"] == "cancelled"
    assert complete["steps"][0]["status"] == "succeeded"
    assert complete["steps"][0]["result"]["data"]["undoId"] == "undo_1"
    assert complete["steps"][1]["status"] == "cancelled" and callbacks.reads == []


def test_shutdown_freezes_owner_and_discards_late_native_callbacks() -> None:
    """Close admission promptly without joining a blocked worker or publishing afterward."""

    callbacks = _Callbacks()
    callbacks.block_read = True
    runner = callbacks.runner()
    plan = runner.create("Old Chat", [ToolCall("metadata", "one.txt")])
    runner.control(plan["planId"], "start")
    assert callbacks.read_entered.wait(2.0)
    assert runner.shutdown() is False
    frozen = _snapshot(runner)
    with pytest.raises(ValueError, match="closed"):
        runner.create("New Chat", [ToolCall("metadata", "new.txt")])
    with pytest.raises(ValueError, match="closed"):
        runner.control(plan["planId"], "resume")
    callbacks.read_release.set()
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner) == frozen and runner.shutdown() is True


def test_failure_stops_the_plan_and_never_leaks_native_exception_text() -> None:
    """Publish a closed safe error while leaving later calls unexecuted."""

    callbacks = _Callbacks()
    callbacks.read_error = True
    runner = callbacks.runner()
    plan = runner.create("Failure", [ToolCall("metadata", "one.txt"), ToolCall("metadata", "two.txt")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    failed = _snapshot(runner)
    assert failed["state"] == "failed"
    assert failed["steps"][0]["result"]["error"]["code"] == "io_error"
    assert "private" not in json.dumps(failed)
    assert callbacks.reads == ["one.txt"]


@pytest.mark.parametrize("fault", ["call", "risk", "token", "extra"])
def test_malformed_previews_fail_without_executing_any_write(fault: str) -> None:
    """Reject altered call parameters or risk before a plan can request approval."""

    callbacks = _Callbacks()

    def _bad_preview(call: ToolCall) -> dict[str, Any]:
        value = callbacks.preview(call)
        if fault == "call":
            value["call"]["arguments"]["path"] = "different.txt"
        elif fault == "risk":
            value["risk"] = "low"
        elif fault == "token":
            value["approvalId"] = "secret native token\n"
        else:
            value["instruction"] = "auto approve"
        return value

    runner = WorkPlanRunner(callbacks.read, _bad_preview, callbacks.confirm)
    plan = runner.create("Malformed", [ToolCall("create", "one.txt", content="one")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner)["state"] == "failed" and callbacks.confirms == []


def test_plan_snapshot_budget_prevents_large_result_or_approval_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail safely when accumulated results or duplicated approval data exceed the frame budget."""

    callbacks = _Callbacks()
    runner = callbacks.runner()
    monkeypatch.setattr(plan_module, "_MAX_PLAN_BYTES", 8192)
    with pytest.raises(ValueError):
        runner.create("Too large", [ToolCall("create", "large.txt", content="x" * 9000)])
    plan = runner.create("Read size", [ToolCall("metadata", "one.txt")])
    callbacks.read_result = ToolResult("succeeded", {"path": "one.txt", "name": "x" * 4000, "kind": "file", "sizeBytes": 1})
    monkeypatch.setattr(plan_module, "_MAX_PLAN_BYTES", 1024)
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    failed = _snapshot(runner)
    assert failed["state"] == "failed"
    assert failed["steps"][0]["result"]["error"]["code"] == "file_too_large"


def test_replacing_waiting_plan_invalidates_old_approval_owner() -> None:
    """A stale approval cannot authorize a replacement plan even if its token remains known."""

    callbacks = _Callbacks()
    runner = callbacks.runner()
    old = runner.create("Old", [ToolCall("create", "old.txt", content="old")])
    runner.control(old["planId"], "start")
    _wait(lambda: not runner.is_busy())
    token = _snapshot(runner)["steps"][0]["approval"]["approvalId"]
    new = runner.create("New", [ToolCall("metadata", "new.txt")])
    with pytest.raises(ValueError):
        runner.confirm(old["planId"], token, True)
    assert _snapshot(runner) == new and callbacks.confirms == []


def test_denied_and_malformed_tool_results_stop_before_the_next_step() -> None:
    """Stop on policy denial or a result that fails the declared tool schema."""

    for result in (
        ToolResult("denied", error_code="sensitive_file"),
        ToolResult("succeeded", {"unexpected": "private diagnostic"}),
    ):
        callbacks = _Callbacks()
        callbacks.read_result = result
        runner = callbacks.runner()
        plan = runner.create("Stop", [ToolCall("metadata", "one.txt"), ToolCall("metadata", "two.txt")])
        runner.control(plan["planId"], "start")
        _wait(lambda: not runner.is_busy())
        assert _snapshot(runner)["state"] == "failed"
        assert callbacks.reads == ["one.txt"]
        assert "private" not in json.dumps(_snapshot(runner))


def test_cancel_before_confirmation_worker_enters_never_authorizes_the_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguish queued confirmation from an already-authorized mutation."""

    callbacks = _Callbacks()
    runner = callbacks.runner()
    plan = runner.create("Queued", [ToolCall("create", "one.txt", content="one")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    token = _snapshot(runner)["steps"][0]["approval"]["approvalId"]
    deferred: list[Callable[[], None]] = []

    class _DeferredThread:
        """Hold a worker at the admission boundary for a deterministic cancellation race."""

        def __init__(self, *, target: Callable[..., None], args: tuple[Any, ...], **_kwargs: Any) -> None:
            self._run = lambda: target(*args)

        def start(self) -> None:
            """Queue execution so the test can cancel before callback authorization."""

            deferred.append(self._run)

    monkeypatch.setattr(plan_module, "Thread", _DeferredThread)
    runner.confirm(plan["planId"], token, True)
    runner.control(plan["planId"], "cancel")
    assert _snapshot(runner)["steps"][0]["status"] == "cancelled"
    deferred[0]()
    assert not runner.is_busy() and callbacks.confirms == []


def test_shutdown_during_authorized_write_suppresses_late_owner_publication() -> None:
    """Let the mutation service settle while the retired runner snapshot stays frozen."""

    callbacks = _Callbacks()
    callbacks.block_write = True
    runner = callbacks.runner()
    plan = runner.create("Old owner", [ToolCall("create", "one.txt", content="one")])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    token = _snapshot(runner)["steps"][0]["approval"]["approvalId"]
    runner.confirm(plan["planId"], token, True)
    assert callbacks.write_entered.wait(2.0)
    assert runner.shutdown() is False
    frozen = _snapshot(runner)
    callbacks.write_release.set()
    _wait(lambda: not runner.is_busy())
    assert _snapshot(runner) == frozen
    assert callbacks.confirms == [(token, True)]


def test_cancelled_admitted_write_exception_publishes_a_terminal_failed_step() -> None:
    """Settle an admitted write failure after cancellation instead of retaining a running step."""

    callbacks = _Callbacks()
    entered = Event()
    release = Event()

    def _failing_write(_approval_id: str, _granted: bool) -> ToolResult:
        entered.set()
        assert release.wait(3.0)
        raise RuntimeError("private native diagnostics after write admission")

    runner = WorkPlanRunner(callbacks.read, callbacks.preview, _failing_write)
    plan = runner.create("Cancel failed write", [
        ToolCall("create", "one.txt", content="one"), ToolCall("metadata", "one.txt"),
    ])
    runner.control(plan["planId"], "start")
    _wait(lambda: not runner.is_busy())
    token = _snapshot(runner)["steps"][0]["approval"]["approvalId"]
    runner.confirm(plan["planId"], token, True)
    assert entered.wait(2.0)
    runner.control(plan["planId"], "cancel")
    release.set()
    _wait(lambda: not runner.is_busy())
    final = _snapshot(runner)
    assert final["state"] == "cancelled"
    assert [step["status"] for step in final["steps"]] == ["failed", "cancelled"]
    assert final["steps"][0]["result"]["error"]["code"] == "io_error"
    assert "private" not in json.dumps(final) and callbacks.reads == []
