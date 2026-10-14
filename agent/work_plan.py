"""Run explicit Work plans sequentially with independent approval for each write.

Plans contain validated tool calls supplied by the user interface, never steps
inferred from conversation text. One worker owns a runner at a time; native
callbacks execute outside its state lock. Cancellation is cooperative and
shutdown invalidates publication, so a late local read cannot update a replaced
Chat owner. Approved writes retain their outcome while this owner remains live:
cancelling a plan cannot pretend an already-authorized mutation did not happen.
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
from work_tools.contracts import (
    TOOL_CATALOG, ToolCall, ToolResult, validate_plan_title, validate_tool_call, validate_tool_result,
)


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
    ) -> None:
        """Bind callbacks to one canonical Chat owner without starting a worker."""

        if not all(callable(callback) for callback in (read_call, preview_call, confirm_call)):
            raise TypeError("Work plan callbacks must be callable.")
        # The fixed graph admits only reads. Write previews and confirmations
        # retain their existing single-use authority outside graph replay.
        self._read_graph = WorkReadGraph(read_call)
        self._preview_call = preview_call
        self._confirm_call = confirm_call
        self._lock = RLock()
        self._plan: _Plan | None = None
        self._cancel = Event()
        self._pause_requested = False
        self._closed = False
        self._worker_active = False
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
            if self._worker_active:
                raise ValueError("Wait for the active Work step to finish.")
            self._plan = plan
            self._cancel = Event()
            self._pause_requested = False
            return snapshot

    def get(self) -> dict[str, Any] | None:
        """Return a detached snapshot, including a final frozen shutdown state."""

        with self._lock:
            return None if self._plan is None else _copy_json(_plan_data(self._plan))

    def is_busy(self) -> bool:
        """Report physical worker ownership while a cancelled or paused step drains."""

        with self._lock:
            return self._worker_active

    def control(self, plan_id: str, action: str) -> dict[str, Any]:
        """Start, pause, resume, cancel, or advance without holding locks during I/O.

        Pause stops between steps; cancelling a read discards its late result.
        Resume restores an existing approval without creating or authorizing a
        second write. The next action uses the same sequential admission rules.
        """

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
            return _copy_json(_plan_data(plan))

    def confirm(self, plan_id: str, approval_id: str, granted: bool) -> dict[str, Any]:
        """Consume only the current waiting step's exact approval token once.

        The trusted confirmation callback validates expiry and file identity.
        Its execution happens on the worker, so declining or approving cannot
        block pause/cancel controls behind filesystem work.
        """

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
            self._launch_locked(plan, confirmation=(step, approval_id, granted))
            return _copy_json(_plan_data(plan))

    def shutdown(self) -> bool:
        """Close admission and freeze snapshots immediately; return whether I/O drained.

        Native callbacks may finish later and their mutation service retains
        durable audit/recovery. This owner never publishes those late results
        after shutdown or waits for native I/O while holding its state lock.
        """

        with self._lock:
            self._closed = True
            if self._plan is not None and self._plan.state not in _TERMINAL:
                self._cancel_plan_locked(self._plan)
            return not self._worker_active

    def _require_open_locked(self) -> None:
        if self._closed:
            raise ValueError("The Work plan owner is closed.")

    def _require_plan_locked(self, plan_id: str) -> _Plan:
        if self._plan is None or type(plan_id) is not str or self._plan.plan_id != plan_id:
            raise ValueError("Work plan no longer belongs to this owner.")
        return self._plan

    def _cancel_plan_locked(self, plan: _Plan) -> None:
        self._cancel.set()
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
        confirmation: tuple[_Step, str, bool] | None = None,
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
        confirmation: tuple[_Step, str, bool] | None,
    ) -> None:
        """Execute one immutable step at a time and publish only to its live owner."""

        current: _Step | None = None
        try:
            while True:
                with self._lock:
                    if not self._owned_locked(plan, token) or cancel.is_set():
                        return
                    if confirmation is not None:
                        current = confirmation[0]
                        current.write_started = True
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
                if confirmation is not None:
                    _, approval_id, granted = confirmation
                    result = self._confirm_call(approval_id, granted)
                    confirmation = None
                elif current.call.tool_id in _READ_TOOLS:
                    result = self._read_graph.run(current.call, cancel.is_set)
                else:
                    approval = self._validate_approval(current.call, self._preview_call(current.call))
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
        except Exception:
            with self._lock:
                if current is not None and self._owned_locked(plan, token):
                    # A cancelled read can discard its late failure. An
                    # admitted write must settle visibly even when its callback
                    # raises, otherwise the step would stay "running" after
                    # physical capacity has already been released.
                    if not cancel.is_set() or current.write_started:
                        self._finish_step_locked(plan, current, ToolResult("failed", error_code="io_error"))
                        if cancel.is_set():
                            plan.state = "cancelled"
        finally:
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
