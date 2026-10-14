"""Connect explicit desktop Work requests to per-Chat filesystem capabilities.

The coordinator receives canonical Chat objects from Brain. It keeps binding
state in the production data root, leaving message and personality storage
alone. Renderer text never enters this dispatcher as an executable instruction.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

from chats import ChatSession
from core.mode_policy import require_work_mode
from desktop_work_plans import DesktopWorkPlans
from work_tools.bindings import WorkspaceBindings
from work_tools.contracts import TOOL_CATALOG, ToolResult, descriptor_to_data, validate_tool_call, validate_tool_result
from work_tools.executor import WorkToolExecutor
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator


class DesktopWorkRuntime:
    """Route Work tools through canonical mode, pinned roots, and one-action permissions."""

    def __init__(self, directory: Path, chat_provider: Callable[[], ChatSession], *, protected_roots: tuple[Path, ...] = ()) -> None:
        """Use one private directory and the current canonical active-Chat provider."""

        self._bindings = WorkspaceBindings(directory)
        self._directory = directory
        self._writes: WriteCoordinator | None = None
        self._protected_roots = (directory.resolve(), *(path.resolve() for path in protected_roots))
        self._chat_provider = chat_provider
        self._lock = RLock()
        self._closed = False
        self._plans = DesktopWorkPlans(chat_provider, self.boundary, self._get_writes)

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Apply an explicit authenticated Work request or deny stale/Chat owners."""

        # NDJSON serializes foreground admission; repositories and worker
        # managers own their narrower locks. Never hold this runtime's singleton
        # mutex through native I/O, so cancellation can revoke tokens promptly.
        with self._lock:
            if self._closed:
                raise PermissionError("Work is shutting down.")
        chat = self._chat_provider()
        if str(chat.chat_id) != params["chatId"] or chat.is_archived:
            raise ValueError("Work request no longer belongs to the active Chat.")
        require_work_mode(chat.mode)
        chat_id = str(chat.chat_id)
        if method == "work.plan.create":
            plan = self._plans.create(chat, params["title"], [validate_tool_call(call) for call in params["calls"]])
            return {"kind": "work.plan", "plan": plan}
        if method == "work.plan.get":
            return {"kind": "work.plan", "plan": self._plans.get(chat_id)}
        if method == "work.plan.control":
            return {"kind": "work.plan", "plan": self._plans.control(chat_id, params["planId"], params["action"])}
        if method == "work.plan.confirm":
            return {"kind": "work.plan", "plan": self._plans.confirm(chat_id, params["planId"], params["approvalId"], params["granted"])}
        if method == "work.tool.confirm" and self._plans.owns_approval(chat_id, params["approvalId"]):
            return {"kind": "work.result", "result": ToolResult("denied", error_code="permission_denied").to_data()}
        if method in {"work.tool.run", "work.tool.preview", "work.tool.confirm"} and (
            self._plans.has_live_plan(chat_id) or self._plans.is_busy()
        ):
            raise PermissionError("Finish or cancel the Work plan before using another tool.")
        if method == "work.tools.list":
            return {"kind": "work.tools", "tools": [descriptor_to_data(item) for item in TOOL_CATALOG]}
        if method == "work.tool.run":
            call = validate_tool_call(params["call"])
            result = WorkToolExecutor(self.boundary(str(chat.chat_id))).run(call)
            return {"kind": "work.result", "result": validate_tool_result(call.tool_id, result)}
        if method == "work.tool.preview":
            call = validate_tool_call(params["call"])
            boundary = self.boundary(str(chat.chat_id))
            approval = self._get_writes().preview(str(chat.chat_id), None if chat.project_id is None else str(chat.project_id),
                                                  boundary, call)
            return {"kind": "work.approval", "approval": approval}
        if method == "work.tool.confirm":
            result = self._get_writes().confirm(str(chat.chat_id), None if chat.project_id is None else str(chat.project_id),
                                                self.boundary(str(chat.chat_id)), params["approvalId"], params["granted"])
            return {"kind": "work.result", "result": result.to_data()}
        if method == "work.workspace.bind":
            if self.is_busy():
                raise PermissionError("Wait for the active Work step before changing its Workspace.")
            self.invalidate(str(chat.chat_id))
            selected = params["workspacePath"]
            if selected is None:
                self._bindings.set(str(chat.chat_id), None)
            else:
                boundary = WorkspaceBoundary(Path(selected))
                self._require_unprotected_root(boundary.root)
                self._bindings.set(str(chat.chat_id), boundary.normalized_root,
                                   expected_identity=boundary.root_identity)
        elif method != "work.workspace.get":
            raise ValueError("Work method is unavailable.")
        binding = self._bindings.get(str(chat.chat_id))
        return {"kind": "work.workspace", "chatId": str(chat.chat_id),
                "workspacePath": None if binding is None else binding.root}

    def _get_writes(self) -> WriteCoordinator:
        """Open private recovery state for an explicit preview or Work plan."""

        with self._lock:
            if self._writes is None:
                self._writes = WriteCoordinator(self._directory)
            return self._writes

    def invalidate(self, chat_id: str) -> None:
        """Revoke transient approval rights after a scope, mode, or owner change."""

        self._plans.invalidate(chat_id)
        if self._writes is not None:
            self._writes.invalidate(chat_id)

    def is_busy(self) -> bool:
        """Report physical plan callbacks, including cancelled workers still draining."""

        return self._plans.is_busy()

    def shutdown(self) -> bool:
        """Close plan admission and cancel owners without holding the runtime mutex during I/O."""

        with self._lock:
            self._closed = True
        return self._plans.shutdown()

    def forget_binding(self, chat_id: str) -> None:
        """Revoke a deleted Chat's scope while retaining useful file recovery records."""

        self.invalidate(chat_id)
        self._bindings.set(chat_id, None)

    def boundary(self, chat_id: str) -> WorkspaceBoundary:
        """Reacquire the exact selected disk identity for one executable operation."""

        binding = self._bindings.get(chat_id)
        if binding is None:
            raise ValueError("Choose a Workspace before using Work tools.")
        boundary = WorkspaceBoundary(Path(binding.root), expected_root_identity=(binding.device, binding.inode))
        self._require_unprotected_root(boundary.root)
        return boundary

    def _require_unprotected_root(self, selected: Path) -> None:
        """Keep executable code and permission journals outside tool-writable scope.

        Otherwise a selected ancestor could edit the binding itself and turn a
        confirmed JSON edit into access to a second unselected directory.
        """

        for protected in self._protected_roots:
            if selected.is_relative_to(protected) or protected.is_relative_to(selected):
                raise PermissionError("Choose a Workspace separate from application code and private data.")
