"""Run explicit Work plans sequentially with independent approval for each write.

Plans contain validated tool calls supplied by the user interface, never steps
inferred from conversation text. One worker owns a runner at a time; native
callbacks execute outside its state lock. Cancellation is cooperative and
shutdown invalidates publication, so a late local read cannot update a replaced
Chat owner. Approved writes retain their outcome while this owner remains live:
cancelling a plan cannot pretend an already-authorized mutation did not happen.
Monotonic budgets cover graph advice and native preparation. Explicit safe-read
retries create a new pending suffix while retaining the original job's time and
attempt limits; completed writes and terminal checkpoint observations are never
rewritten or repeated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
from threading import Event, RLock, Thread
from typing import Any, Literal
from uuid import uuid4

from agent.work_graph import WorkReadGraph
from agent.workflow_jobs import JobPhase, WorkflowAttempt, WorkflowJobBudget, WorkflowJobError
from agent.workflow_nodes import WorkflowNodes
from work_tools.contracts import (
    TOOL_CATALOG, ToolCall, ToolResult, validate_plan_title, validate_tool_call, validate_tool_result,
)
from work_tools.wire import validate_work_result


PlanState = Literal["pending", "running", "paused", "waiting", "succeeded", "failed", "cancelled"]
StepState = Literal["pending", "running", "waiting", "succeeded", "failed", "cancelled"]
ReadCall = Callable[[ToolCall, Callable[[], bool]], ToolResult]
PreviewCall = Callable[[ToolCall], dict[str, Any]]
ConfirmCall = Callable[[str, bool], ToolResult]
_MAX_STEPS = 16
_MAX_PLAN_BYTES = 768 * 1024
_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})
_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(slots=True)
class _Step:
    """Keep canonical step data separate from mutable UI snapshots."""

    step_id: str
    call: ToolCall
    status: StepState = "pending"
    result: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    write_started: bool = False


@dataclass(slots=True)
class _Plan:
    """Identify one owned plan whose callbacks cannot publish to a replacement."""

    plan_id: str
    title: str
    steps: list[_Step]
    state: PlanState = "pending"


def _plan_data(plan: _Plan) -> dict[str, Any]:
    return {
        "planId": plan.plan_id, "title": plan.title, "state": plan.state,
        "steps": [
            {
                "stepId": step.step_id, "call": step.call.to_data(),
                "status": step.status, "result": step.result, "approval": step.approval,
            }
            for step in plan.steps
        ],
    }


def _copy_json(value: dict[str, Any], *, reserve_bytes: int = 0) -> dict[str, Any]:
    """Detach snapshots and bound their encoded size before any publication."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_PLAN_BYTES - reserve_bytes:
            raise ValueError("Work plan exceeds the snapshot limit.")
        copied: dict[str, Any] = json.loads(encoded)
        return copied
    except (TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError("Work plan contains invalid data.") from None


class WorkPlanRunner:
    """Own one bounded explicit plan and at most one sequential background worker."""

    def __init__(
        self,
        read_call: ReadCall,
        preview_call: PreviewCall,
        confirm_call: ConfirmCall,
        *,
        workflow_nodes: WorkflowNodes | None = None,
        checkpoint: Callable[[dict[str, Any], str | None], None] | None = None,
        job_budget: WorkflowJobBudget | None = None,
        read_result: Callable[[ToolCall, ToolResult], None] | None = None,
        timeout_call: Callable[[str], ToolResult] | None = None,
        budget_confirm_call: Callable[[str, bool, Callable[[], bool]], ToolResult] | None = None,
    ) -> None:
        """Bind one canonical owner and live limits without starting callbacks.

        read_result receives the verified budget-settled outcome for private
        audit before publication. timeout_call consumes a waiting decision with
        no mutation. budget_confirm_call carries the same preparation deadline
        into native backup so entering that service cannot reset its allowance.
        Legacy two-argument confirmation callbacks remain supported.
        """

        if not all(callable(callback) for callback in (read_call, preview_call, confirm_call)):
            raise TypeError("Work plan callbacks must be callable.")
        # The fixed graph admits only reads. Write previews and confirmations
        # retain their existing single-use authority outside graph replay.
        self._read_graph = WorkReadGraph(read_call, nodes=workflow_nodes)
        self._workflow_nodes = workflow_nodes
        self._checkpoint_callback = checkpoint
        self._checkpoint_lock = RLock()
        self._checkpoint_failed = False
        self._preview_call = preview_call
        self._confirm_call = confirm_call
        self._budget_confirm_call = budget_confirm_call
        self._timeout_call = timeout_call if timeout_call is not None else lambda token: confirm_call(token, False)
        self._read_result = read_result
        self._job = WorkflowJobBudget() if job_budget is None else job_budget
        self._retry_steps: set[str] = set()
        self._lock = RLock()
        self._plan: _Plan | None = None
        self._cancel = Event()
        self._pause_requested = False
        self._closed = False
        self._worker_active = False
        self._preparing_retry = False
        self._worker_token: object | None = None

    def create(self, title: str, calls: list[ToolCall]) -> dict[str, Any]:
        """Replace an idle plan with at most sixteen exact validated tool calls.

        A draining native callback retains physical ownership even after its
        plan is cancelled. Replacing it is denied until that callback returns,
        preventing two plans from executing concurrently under one Chat.
        """

        checked_title = validate_plan_title(title)
        if type(calls) is not list or not 1 <= len(calls) <= _MAX_STEPS:
            raise ValueError("Work plan requires one to sixteen steps.")
        checked: list[ToolCall] = []
        for call in calls:
            if type(call) is not ToolCall:
                raise ValueError("Work plan step is invalid.")
            checked.append(validate_tool_call(call.to_data()))
        plan = _Plan(
            "plan_" + uuid4().hex, checked_title,
            [_Step("step_" + uuid4().hex, call) for call in checked],
        )
        # Reserve space for sixteen small cancellation outcomes so a later
        # stop can always produce a valid snapshot at the same byte limit.
        snapshot = _copy_json(_plan_data(plan), reserve_bytes=4096)
        with self._lock:
            self._require_open_locked()
            if self._worker_active or self._preparing_retry:
                raise ValueError("Wait for the active Work step to finish.")
            if self._plan is not None:
                self._job = self._job.fresh()
            self._plan = plan
            self._cancel = Event()
            self._pause_requested = False
            self._retry_steps.clear()
        self._checkpoint()
        return snapshot

    def restore(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Install validated paused or terminal data without creating a worker or permission.

        The manager already checked the persisted owner and selected root. Only
        settled and pending steps are accepted here; interrupted writes cannot
        be replayed by manufacturing an idle runner around a running step.
        """

        validate_work_result({"kind": "work.plan", "plan": snapshot})
        if (snapshot["busy"] or snapshot["state"] not in {"paused", *_TERMINAL}
            or any(step["status"] in {"running", "waiting"} or step["approval"] is not None for step in snapshot["steps"])):
            raise ValueError("Work recovery requires safe paused data.")
        with self._lock:
            self._require_open_locked()
            if self._plan is not None or self._worker_active:
                raise ValueError("Work recovery requires an empty owner.")
            self._plan = _Plan(snapshot["planId"], snapshot["title"], [
                _Step(item["stepId"], validate_tool_call(item["call"]), item["status"],
                      None if item["result"] is None else _copy_json(item["result"]))
                for item in snapshot["steps"]
            ], snapshot["state"])
            self._pause_requested = snapshot["state"] == "paused"
            return _copy_json(_plan_data(self._plan))

    def get(self) -> dict[str, Any] | None:
        """Return a detached snapshot, including a final frozen shutdown state."""

        with self._lock:
            return None if self._plan is None else _copy_json(_plan_data(self._plan))

    def job_observation(self, plan_snapshot: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Pair live limits with a supplied detached plan, filtering hints against that exact response snapshot."""

        with self._lock:
            if self._plan is None:
                return None
            plan = _plan_data(self._plan) if plan_snapshot is None else plan_snapshot
            if plan["planId"] != self._plan.plan_id:
                return None
            snapshot = self._job.snapshot()
            # A worker can settle after the plan is detached but before this
            # budget sample. Hints must describe the response's failed reads,
            # rather than advertise a retry for its older running/null step.
            failed = {step["stepId"] for step in plan["steps"] if step["status"] == "failed"
                      and step["call"]["toolId"] in _READ_TOOLS and step["result"] is not None
                      and step["result"]["status"] == "failed" and step["result"]["error"] is not None
                      and step["result"]["error"]["code"] in {"timeout", "io_error"}}
            snapshot["retryableStepIds"] = [step_id for step_id in snapshot["retryableStepIds"] if step_id in failed]
            return {"planId": self._plan.plan_id, **snapshot}

    def retry(self, plan_id: str, step_id: str) -> dict[str, Any]:
        """Create a pending replacement suffix for one explicitly retried drained transient read.

        The old settled snapshot is unchanged. Remaining calls and step IDs are
        exact, while a new plan/workflow identity avoids rewriting a terminal
        checkpoint. The same live budget retains the total deadline and retry
        count. No callback runs until a separate Start action succeeds.
        """

        with self._lock:
            self._require_open_locked()
            plan = self._require_plan_locked(plan_id)
            if plan.state != "failed" or self._worker_active or self._preparing_retry or self._checkpoint_failed or not self._job.can_retry(step_id):
                raise ValueError("This Work read cannot be retried.")
            index = next((index for index, step in enumerate(plan.steps) if step.step_id == step_id and step.status == "failed"), None)
            if index is None or any(step.status != "pending" for step in plan.steps[index + 1:]):
                raise ValueError("Work retry cannot replay settled steps.")
            self._plan = _Plan("plan_" + uuid4().hex, plan.title,
                               [_Step(step.step_id, step.call) for step in plan.steps[index:]])
            self._cancel = Event()
            self._pause_requested = False
            self._retry_steps = {step_id}
            snapshot = _copy_json(_plan_data(self._plan))
            self._preparing_retry = True
        try:
            self._checkpoint()
        finally:
            with self._lock:
                changed = self._plan is not None and _plan_data(self._plan) != snapshot
            try:
                if changed:
                    try:
                        self._checkpoint()
                    except Exception:
                        # Logical cancellation remains responsive while the
                        # retry CAS drains; failed durability prevents admission.
                        pass
            finally:
                with self._lock:
                    self._preparing_retry = False
        with self._lock:
            current = self._require_plan_locked(snapshot["planId"])
            if self._closed or current.state != "pending":
                # Cancel/Pause can win while private CAS I/O is outside this
                # mutex. A successful retry response promises a pending plan;
                # reject a superseded publication rather than revive old UI.
                raise WorkflowJobError("conflict")
            return _copy_json(_plan_data(current))

    def expire_budget(self) -> None:
        """Observe an idle expired job without a timer, auditing a waiting timeout on the normal worker."""

        changed = False
        with self._lock:
            plan = self._plan
            if (self._closed or plan is None or plan.state in _TERMINAL or self._worker_active
                or self._job.snapshot()["stopReason"] != "timeout"):
                return
            waiting = next((step for step in plan.steps if step.status == "waiting" and step.approval is not None), None)
            if waiting is not None:
                approval_id = waiting.approval["approvalId"]  # type: ignore[index]
                waiting.approval = None
                waiting.status = "running"
                plan.state = "running"
                self._launch_locked(plan, confirmation=(waiting, approval_id, False, True))
                return
            pending = next((step for step in plan.steps if step.status == "pending"), None)
            if pending is not None:
                self._finish_step_locked(plan, pending, ToolResult("failed", error_code="timeout"))
                changed = True
        if changed:
            self._checkpoint()

    def is_busy(self) -> bool:
        """Report physical worker ownership while a cancelled or paused step drains."""

        with self._lock:
            return self._worker_active or self._preparing_retry

    def observation(self) -> dict[str, Any] | None:
        """Expose bounded advisory progress without adding permissions to the plan DTO."""

        return None if self._workflow_nodes is None else self._workflow_nodes.observation()

    def checkpoint_failed(self) -> bool:
        """Report failed durability separately from the truthful native step outcome."""

        with self._lock:
            return self._checkpoint_failed

    def attach_checkpoint(self, callback: Callable[[dict[str, Any], str | None], None]) -> None:
        """Attach durability once to an idle newly admitted owner and save its current plan."""

        with self._lock:
            self._require_open_locked()
            if not callable(callback) or self._checkpoint_callback is not None or self._worker_active:
                raise ValueError("Work checkpoint ownership is already established.")
            self._checkpoint_callback = callback
        self._checkpoint()

    def flush_checkpoint(self) -> None:
        """Persist final detached state outside the manager's registry lock."""

        self._checkpoint()

    def _checkpoint(self) -> None:
        """Save a freshly sampled boundary without retaining the state lock during disk I/O.

        A separate mutex serializes snapshots, including foreground cancellation
        and worker settlement. Cancellation changes logical state immediately;
        a physically active worker persists that latest state before releasing
        its lease. An admitted write is recorded before the callback can mutate.
        """

        if self._checkpoint_callback is None:
            return
        with self._checkpoint_lock:
            with self._lock:
                if self._plan is None:
                    return
                snapshot = _copy_json(_plan_data(self._plan))
                admitted = next((step.step_id for step in self._plan.steps
                                 if step.status == "running" and step.write_started), None)
            try:
                self._checkpoint_callback(snapshot, admitted)
            except Exception:
                with self._lock:
                    self._checkpoint_failed = True
                raise
            with self._lock:
                self._checkpoint_failed = False

    def control(self, plan_id: str, action: str) -> dict[str, Any]:
        """Start, pause, resume, cancel, or advance without holding locks during I/O.

        Pause stops between steps; cancelling a read discards its late result.
        Resume restores an existing approval without creating or authorizing a
        second write. The next action uses the same sequential admission rules.
        """

        self.expire_budget()
        with self._lock:
            self._require_open_locked()
            plan = self._require_plan_locked(plan_id)
            if type(action) is not str or action not in {"start", "pause", "resume", "cancel", "next"}:
                raise ValueError("Work plan control is invalid.")
            if action == "cancel":
                if plan.state not in _TERMINAL:
                    self._cancel_plan_locked(plan)
            elif action == "pause":
                if plan.state not in {"pending", "running", "waiting", "paused"}:
                    raise ValueError("This Work plan cannot be paused.")
                self._pause_requested = True
                plan.state = "paused"
            else:
                if self._checkpoint_failed or self._preparing_retry:
                    raise ValueError("Work checkpoint storage must recover before admission.")
                allowed = {"pending"} if action == "start" else {"pending", "paused"}
                if plan.state not in allowed:
                    raise ValueError("This Work plan cannot be started or resumed.")
                self._pause_requested = False
                if any(step.status == "waiting" for step in plan.steps):
                    plan.state = "waiting"
                else:
                    plan.state = "running"
                    if not self._worker_active:
                        self._launch_locked(plan)
            snapshot = _copy_json(_plan_data(plan))
            checkpoint_now = not self._worker_active and not self._preparing_retry
        if checkpoint_now:
            self._checkpoint()
        return snapshot

    def confirm(self, plan_id: str, approval_id: str, granted: bool) -> dict[str, Any]:
        """Consume only the current waiting step's exact approval token once.

        The trusted confirmation callback validates expiry and file identity.
        Its execution happens on the worker, so declining or approving cannot
        block pause/cancel controls behind filesystem work.
        """

        self.expire_budget()
        with self._lock:
            self._require_open_locked()
            plan = self._require_plan_locked(plan_id)
            if type(granted) is not bool or type(approval_id) is not str or plan.state != "waiting":
                raise ValueError("Work plan approval is not current.")
            step = next((item for item in plan.steps if item.status == "waiting"), None)
            if step is None or step.approval is None or step.approval["approvalId"] != approval_id:
                raise ValueError("Work plan approval is not current.")
            if self._worker_active:
                raise ValueError("Wait for the active Work step to finish.")
            step.status = "running"
            step.approval = None
            plan.state = "running"
            self._launch_locked(plan, confirmation=(step, approval_id, granted, False))
            return _copy_json(_plan_data(plan))

    def revision_calls(self, plan_id: str, approval_id: str, replacement: ToolCall) -> tuple[str, list[ToolCall]]:
        """Copy only the changed waiting step and pending suffix for a new explicit plan.

        This does not consume permission, start a worker, or alter the old plan.
        The manager must atomically close this owner before auditing modify and
        publishing a fresh pending plan; completed steps must never be replayed.
        """

        if type(replacement) is not ToolCall:
            raise ValueError("Work revision requires a typed call.")
        checked = validate_tool_call(replacement.to_data())
        with self._lock:
            self._require_open_locked()
            plan = self._require_plan_locked(plan_id)
            if plan.state != "waiting" or self._worker_active:
                raise ValueError("Work revision requires an idle waiting plan.")
            index = next((index for index, step in enumerate(plan.steps) if step.status == "waiting"), None)
            if index is None:
                raise ValueError("Work revision has no waiting step.")
            step = plan.steps[index]
            if step.approval is None or step.approval["approvalId"] != approval_id or checked.tool_id != step.call.tool_id:
                raise ValueError("Work revision does not match its current tool approval.")
            if any(item.status != "pending" for item in plan.steps[index + 1:]):
                raise ValueError("Work revision cannot replay settled steps.")
            calls = [checked, *(validate_tool_call(item.call.to_data()) for item in plan.steps[index + 1:])]
            _copy_json({"title": plan.title, "calls": [call.to_data() for call in calls]}, reserve_bytes=4096)
            return plan.title, calls

    def expire_approval(self, plan_id: str, approval_id: str) -> dict[str, Any]:
        """Settle an expired waiting or paused approval using a nonmutating worker decision.

        The manager first checks the writer's own UTC and monotonic clocks. This
        helper never grants permission; pausing cannot extend the original lease.
        """

        with self._lock:
            self._require_open_locked()
            plan = self._require_plan_locked(plan_id)
            if plan.state not in {"waiting", "paused"} or self._worker_active:
                raise ValueError("Work approval cannot expire in this state.")
            step = next((step for step in plan.steps if step.status == "waiting"), None)
            if step is None or step.approval is None or step.approval["approvalId"] != approval_id:
                raise ValueError("Work approval is no longer current.")
            step.approval = None
            step.status = "running"
            plan.state = "running"
            self._launch_locked(plan, confirmation=(step, approval_id, False, True))
            return _copy_json(_plan_data(plan))

    def shutdown(self, *, persist: bool = True) -> bool:
        """Close admission and freeze snapshots immediately; return whether I/O drained.

        Native callbacks may finish later and their mutation service retains
        durable audit/recovery. This owner never publishes those late results
        after shutdown or waits for native I/O while holding its state lock.
        """

        with self._lock:
            self._closed = True
            self._job.shutdown()
            if self._plan is not None and self._plan.state not in _TERMINAL:
                self._cancel_plan_locked(self._plan)
            drained = not self._worker_active and not self._preparing_retry
        if drained and persist:
            try:
                self._checkpoint()
            except Exception:
                # An unavailable private disk must not block shutdown. The
                # previous checkpoint still requires explicit revalidation.
                pass
        return drained

    def _require_open_locked(self) -> None:
        if self._closed:
            raise ValueError("The Work plan owner is closed.")

    def _require_plan_locked(self, plan_id: str) -> _Plan:
        if self._plan is None or type(plan_id) is not str or self._plan.plan_id != plan_id:
            raise ValueError("Work plan no longer belongs to this owner.")
        return self._plan

    def _cancel_plan_locked(self, plan: _Plan) -> None:
        self._cancel.set()
        self._job.cancel()
        plan.state = "cancelled"
        for step in plan.steps:
            if step.status in {"pending", "waiting"} or (step.status == "running" and not step.write_started):
                step.status = "cancelled"
                step.approval = None
                step.result = ToolResult("cancelled", error_code="cancelled").to_data()

    def _launch_locked(
        self,
        plan: _Plan,
        *,
        confirmation: tuple[_Step, str, bool, bool] | None = None,
    ) -> None:
        token = object()
        self._worker_active = True
        self._worker_token = token
        try:
            Thread(
                target=self._run_worker, args=(plan, token, self._cancel, confirmation),
                name="elysia-work-plan-" + plan.plan_id, daemon=True,
            ).start()
        except Exception:
            self._worker_active = False
            self._worker_token = None
            plan.state = "failed"
            step = next((item for item in plan.steps if item.status in {"pending", "running"}), None)
            if step is not None:
                self._finish_step_locked(plan, step, ToolResult("failed", error_code="io_error"))

    def _owned_locked(self, plan: _Plan, token: object) -> bool:
        return not self._closed and self._plan is plan and self._worker_token is token

    def _run_worker(
        self,
        plan: _Plan,
        token: object,
        cancel: Event,
        confirmation: tuple[_Step, str, bool, bool] | None,
    ) -> None:
        """Execute one immutable step at a time and publish only to its live owner."""

        current: _Step | None = None
        attempt: WorkflowAttempt | None = None
        native_started = False
        read_audited = False
        try:
            while True:
                native_started = False
                read_audited = False
                with self._lock:
                    if not self._owned_locked(plan, token) or cancel.is_set():
                        return
                    if confirmation is not None:
                        current = confirmation[0]
                        current.write_started = confirmation[2] and not confirmation[3]
                    else:
                        current = next((step for step in plan.steps if step.status == "pending"), None)
                        if current is None:
                            plan.state = "succeeded"
                            return
                        if self._pause_requested:
                            plan.state = "paused"
                            return
                        current.status = "running"
                        plan.state = "running"
                timeout_decision = confirmation is not None and confirmation[3]
                if not timeout_decision:
                    phase: JobPhase = "mutation" if confirmation is not None else "read" if current.call.tool_id in _READ_TOOLS else "preview"
                    attempt = self._job.begin_step(current.step_id, current.call,
                                                   retry=current.step_id in self._retry_steps, phase=phase)
                    self._retry_steps.discard(current.step_id)
                self._checkpoint()
                with self._lock:
                    if not self._owned_locked(plan, token) or cancel.is_set():
                        if self._owned_locked(plan, token) and confirmation is not None:
                            # Cancellation won before the native confirmation
                            # callback. No effect occurred despite write-ahead
                            # admission, so this process can settle it exactly.
                            current.write_started = False
                            self._cancel_plan_locked(plan)
                        return
                if confirmation is not None:
                    _, approval_id, granted, expired = confirmation
                    if expired or attempt is not None and attempt.should_stop():
                        current.write_started = False
                        self._checkpoint()
                        result = self._timeout_call(approval_id)
                        if result.error_code != "io_error":
                            result = ToolResult("failed", error_code="timeout")
                    else:
                        native_started = granted
                        if self._budget_confirm_call is not None:
                            assert attempt is not None
                            result = self._budget_confirm_call(approval_id, granted, attempt.should_stop)
                        else:
                            result = self._confirm_call(approval_id, granted)
                    confirmation = None
                elif current.call.tool_id in _READ_TOOLS:
                    assert attempt is not None
                    lease = attempt
                    result = self._read_graph.run(current.call, lambda: cancel.is_set() or lease.should_stop(), query=plan.title)
                else:
                    assert attempt is not None
                    lease = attempt
                    route = self._read_graph.prepare(current.call, lambda: cancel.is_set() or lease.should_stop(), query=plan.title)
                    if route.status != "denied" or route.error_code != "permission_required":
                        result = self._job.finish(attempt, route)
                        attempt = None
                        with self._lock:
                            if self._owned_locked(plan, token) and not cancel.is_set():
                                outcome = result if result.status in {"failed", "denied", "cancelled"} else ToolResult("failed", error_code="io_error")
                                self._finish_step_locked(plan, current, outcome)
                        return
                    approval = self._validate_approval(current.call, self._preview_call(current.call))
                    preview_lease = attempt
                    attempt = None
                    try:
                        self._job.finish_preview(preview_lease)
                    except WorkflowJobError as error:
                        # The returned preview never became visible. Consume
                        # its native token as a nonmutating timeout rather than
                        # retaining a hidden permission after budget expiry.
                        if error.code == "timeout":
                            self._timeout_call(approval["approvalId"])
                        raise
                    with self._lock:
                        if not self._owned_locked(plan, token) or cancel.is_set():
                            return
                        current.approval = approval
                        current.status = "waiting"
                        try:
                            _copy_json(_plan_data(plan), reserve_bytes=4096)
                        except ValueError:
                            current.approval = None
                            self._finish_step_locked(plan, current, ToolResult("failed", error_code="file_too_large"))
                            return
                        plan.state = "paused" if self._pause_requested else "waiting"
                    return
                if attempt is not None:
                    result = self._job.finish(attempt, result, admitted_write=native_started)
                    attempt = None
                if current.call.tool_id in _READ_TOOLS:
                    read_audited = True
                    if self._read_result is not None:
                        try:
                            self._read_result(current.call, result)
                        except Exception:
                            result = ToolResult("failed", error_code="io_error")
                with self._lock:
                    if not self._owned_locked(plan, token):
                        return
                    if cancel.is_set() and current.call.tool_id in _READ_TOOLS:
                        return
                    keep_running = self._finish_step_locked(plan, current, result)
                    if cancel.is_set():
                        plan.state = "cancelled"
                        return
                    if not keep_running:
                        return
        except Exception as error:
            result = ToolResult("failed", error_code=error.code if isinstance(error, WorkflowJobError) else "io_error")
            if attempt is not None:
                result = self._job.finish(attempt, result, admitted_write=native_started)
                attempt = None
            if current is not None and current.call.tool_id in _READ_TOOLS and not read_audited and self._read_result is not None:
                try:
                    self._read_result(current.call, result)
                except Exception:
                    result = ToolResult("failed", error_code="io_error")
            with self._lock:
                if current is not None and self._owned_locked(plan, token):
                    # A cancelled read can discard its late failure. An
                    # admitted write must settle visibly even when its callback
                    # raises, otherwise the step would stay "running" after
                    # physical capacity has already been released.
                    if not cancel.is_set() or current.write_started:
                        self._finish_step_locked(plan, current, result)
                        if cancel.is_set():
                            plan.state = "cancelled"
        finally:
            if attempt is not None:
                # Early cancellation between checkpoint and callback still
                # owns a lease. Only this physically returning worker releases
                # it; shutdown never clears the native operation itself.
                self._job.finish(attempt, ToolResult("cancelled", error_code="cancelled"), admitted_write=native_started)
            try:
                self._checkpoint()
            except Exception:
                # Keep actual native outcomes visible. The saved admission
                # record remains uncertain and the UI reports failed durability.
                pass
            with self._lock:
                if self._worker_token is token:
                    self._worker_active = False
                    self._worker_token = None

    def _finish_step_locked(self, plan: _Plan, step: _Step, result: ToolResult) -> bool:
        if type(result) is not ToolResult:
            result = ToolResult("failed", error_code="io_error")
        step.result = validate_tool_result(step.call.tool_id, result)
        step.approval = None
        try:
            _copy_json(_plan_data(plan), reserve_bytes=4096)
        except ValueError:
            result = ToolResult("failed", error_code="file_too_large")
            step.result = result.to_data()
        if result.status in {"succeeded", "partial"}:
            step.status = "succeeded"
            return True
        if result.status == "cancelled":
            step.status = "cancelled"
            self._cancel_plan_locked(plan)
        else:
            step.status = "failed"
            plan.state = "failed"
        return False

    def _validate_approval(self, call: ToolCall, value: dict[str, Any]) -> dict[str, Any]:
        """Freeze the exact call before displaying an approval; text cannot grant it."""

        if type(value) is not dict or set(value) != {"approvalId", "call", "risk", "preview", "expiresAt"}:
            raise ValueError("Work approval is invalid.")
        token = value["approvalId"]
        if type(token) is not str or re.fullmatch(r"approval_[0-9a-f]{32}", token) is None:
            raise ValueError("Work approval is invalid.")
        if validate_tool_call(value["call"]) != call:
            raise ValueError("Work approval call changed.")
        descriptor = next(item for item in TOOL_CATALOG if item.tool_id == call.tool_id)
        if value["risk"] != descriptor.risk:
            raise ValueError("Work approval risk changed.")
        if type(value["preview"]) is not str or type(value["expiresAt"]) is not str:
            raise ValueError("Work approval is invalid.")
        if (
            len(value["preview"].encode("utf-8")) > 32 * 1024
            or not 1 <= len(value["expiresAt"]) <= 128
            or "\x00" in value["preview"] or "\x00" in value["expiresAt"]
        ):
            raise ValueError("Work approval is invalid.")
        expiry = value["expiresAt"]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", expiry) is None:
            raise ValueError("Work approval expiry is invalid.")
        try:
            if datetime.fromisoformat(expiry).utcoffset() != timedelta(0):
                raise ValueError("Work approval expiry is invalid.")
        except ValueError:
            raise ValueError("Work approval expiry is invalid.") from None
        return _copy_json(value)
