"""Bind explicit Work plans to canonical Chat and Workspace capabilities.

The manager owns only in-memory plan lifetimes and token routing. File previews,
single-use permission, durable audit, and recovery remain in WriteCoordinator.
Providers and filesystem callbacks run outside the registry lock so the Backend
can cancel a plan or inspect draining capacity without waiting behind native I/O.
Every operation rechecks the captured Chat, Project, mode, and selected root.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from agent.work_plan import WorkPlanRunner
from agent.workflow_nodes import WorkflowNodes
from chats import ChatSession
from work_tools.contracts import ToolCall, ToolProtocolError, ToolResult
from work_tools.executor import WorkToolExecutor
from work_tools.workspace import WorkspaceBoundary, WorkspaceBoundaryError
from work_tools.writes import WriteCoordinator


_LIVE_STATES = frozenset({"pending", "running", "paused", "waiting"})


@dataclass(slots=True)
class _Owner:
    """Freeze a plan's authority independently of later Chat or binding changes."""

    chat_id: str
    project_id: str | None
    root_path: str
    root_identity: tuple[int, int]
    runner: WorkPlanRunner | None = None
    active: bool = True
    tokens: set[str] = field(default_factory=set)


class DesktopWorkPlans:
    """Manage per-Chat plans, their approval route, and all physically draining workers."""

    def __init__(
        self,
        chat_provider: Callable[[], ChatSession],
        boundary_provider: Callable[[str], WorkspaceBoundary],
        write_provider: Callable[[], WriteCoordinator],
        *,
        nodes_factory: Callable[[Callable[[], None], bool], WorkflowNodes] | None = None,
    ) -> None:
        """Inject canonical providers without opening a filesystem or starting threads."""

        if not all(callable(provider) for provider in (chat_provider, boundary_provider, write_provider)):
            raise TypeError("Work plan providers must be callable.")
        self._chat_provider = chat_provider
        self._boundary_provider = boundary_provider
        self._write_provider = write_provider
        self._nodes_factory = nodes_factory
        self._lock = RLock()
        self._owners: dict[str, _Owner] = {}
        self._retired: list[_Owner] = []
        self._closed = False
        self._creation_token: object | None = None

    def create(self, chat: ChatSession, title: str, calls: list[ToolCall], *, use_project_knowledge: bool = False) -> dict[str, Any]:
        """Capture the current selected scope and replace an idle plan safely.

        Old waiting approvals are revoked before a replacement is admitted.
        A cancelled native callback still owns capacity, so no new owner can
        overlap it until the retired runner has physically drained.
        """

        with self._lock:
            self._require_open_locked()
        if not isinstance(chat, ChatSession):
            raise ToolProtocolError()
        chat_id = str(chat.chat_id)
        project_id = None if chat.project_id is None else str(chat.project_id)
        self._require_chat(chat_id, project_id)
        boundary = self._boundary_provider(chat_id)
        boundary.metadata()
        owner = _Owner(chat_id, project_id, boundary.normalized_root, boundary.root_identity)
        runner = WorkPlanRunner(
            lambda call, cancel: self._read(owner, call, cancel),
            lambda call: self._preview(owner, call),
            lambda token, granted: self._confirm_write(owner, token, granted),
            workflow_nodes=None if self._nodes_factory is None else self._nodes_factory(
                lambda: self._check_node_authority(owner), use_project_knowledge,
            ),
        )
        owner.runner = runner
        snapshot = runner.create(title, calls)
        creation = object()
        with self._lock:
            self._require_open_locked()
            self._reap_locked()
            if self._busy_locked() or self._creation_token is not None:
                raise ToolProtocolError("conflict")
            self._creation_token = creation
            old = self._owners.get(chat_id)
            if old is not None:
                self._retire_locked(old)
        # Revocation uses the writer's short-lived pending-permission mutex.
        # Keep providers outside the registry lock so Backend authority and
        # plan publication never acquire their locks in opposite orders.
        try:
            self._write_provider().invalidate(chat_id)
            self._require_chat(chat_id, project_id)
            fresh = self._boundary_provider(chat_id)
            fresh.metadata()
            if (fresh.normalized_root, fresh.root_identity) != (owner.root_path, owner.root_identity):
                raise ToolProtocolError("conflict")
            with self._lock:
                self._require_open_locked()
                if self._busy_locked() or self._creation_token is not creation:
                    raise ToolProtocolError("conflict")
                self._owners[chat_id] = owner
        finally:
            with self._lock:
                if self._creation_token is creation:
                    self._creation_token = None
        snapshot["busy"] = False
        return snapshot

    def _check_node_authority(self, owner: _Owner) -> None:
        """Revalidate the selected capability before and after advisory retrieval."""

        self._authority(owner)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """Return only the canonical current Chat's owned detached plan snapshot."""

        with self._lock:
            owner = self._owners.get(chat_id)
        if owner is None:
            return None
        # A retired plan remains visible as cancelled while its callback drains.
        # Reading that snapshot grants no capability and needs no bound root.
        self._require_chat(owner.chat_id, owner.project_id)
        assert owner.runner is not None
        snapshot = owner.runner.get()
        if snapshot is not None:
            snapshot["busy"] = owner.runner.is_busy()
        return snapshot

    def control(self, chat_id: str, plan_id: str, action: str) -> dict[str, Any]:
        """Control one owned plan without locking around native worker callbacks."""

        owner = self._owner(chat_id)
        self._require_chat(owner.chat_id, owner.project_id)
        assert owner.runner is not None
        with self._lock:
            if action in {"start", "resume", "next"} and self._busy_locked() and not owner.runner.is_busy():
                raise ToolProtocolError("conflict")
        snapshot = owner.runner.control(plan_id, action)
        if action == "cancel":
            self._write_provider().invalidate(chat_id)
        snapshot["busy"] = owner.runner.is_busy()
        return snapshot

    def confirm(self, chat_id: str, plan_id: str, approval_id: str, granted: bool) -> dict[str, Any]:
        """Admit a decision only through the plan that owns its exact approval token."""

        owner = self._owner(chat_id)
        self._authority(owner)
        with self._lock:
            if approval_id not in owner.tokens:
                raise ToolProtocolError("permission_denied")
            if self._busy_locked():
                raise ToolProtocolError("conflict")
        assert owner.runner is not None
        snapshot = owner.runner.confirm(plan_id, approval_id, granted)
        snapshot["busy"] = owner.runner.is_busy()
        return snapshot

    def is_busy(self) -> bool:
        """Report current and retired physical capacity without invoking providers."""

        with self._lock:
            self._reap_locked()
            return self._busy_locked()

    def observation(self, chat_id: str) -> dict[str, Any] | None:
        """Return advisory progress only while its captured canonical owner remains valid."""

        with self._lock:
            owner = self._owners.get(chat_id)
        if owner is None or not owner.active or owner.runner is None:
            return None
        try:
            return owner.runner.observation()
        except (ValueError, OSError, PermissionError):
            return None

    def has_live_plan(self, chat_id: str) -> bool:
        """Report pending, running, waiting, or paused state for Backend admission gates."""

        with self._lock:
            owner = self._owners.get(chat_id)
            if owner is None or not owner.active or owner.runner is None:
                return False
            plan = owner.runner.get()
            return plan is not None and plan["state"] in _LIVE_STATES

    def owns_approval(self, chat_id: str, token: str) -> bool:
        """Identify plan tokens so a generic confirmation route cannot bypass plan state."""

        with self._lock:
            return any(
                owner.chat_id == chat_id and token in owner.tokens
                for owner in [*self._owners.values(), *self._retired]
            )

    def invalidate(self, chat_id: str) -> None:
        """Retire a Chat owner and revoke its pending write permissions outside the lock."""

        with self._lock:
            self._creation_token = None
            owner = self._owners.get(chat_id)
            if owner is not None:
                self._retire_locked(owner)
        if owner is not None:
            self._write_provider().invalidate(chat_id)

    def shutdown(self) -> bool:
        """Close every owner promptly and return whether all native callbacks drained."""

        with self._lock:
            self._closed = True
            self._creation_token = None
            owners = list(self._owners.values())
            for owner in owners:
                self._retire_locked(owner)
            chat_ids = {owner.chat_id for owner in [*owners, *self._retired]}
        writer = self._write_provider() if chat_ids else None
        if writer is not None:
            for chat_id in chat_ids:
                writer.invalidate(chat_id)
        return not self.is_busy()

    def _require_open_locked(self) -> None:
        if self._closed:
            raise ToolProtocolError("conflict")

    def _owner(self, chat_id: str) -> _Owner:
        with self._lock:
            self._require_open_locked()
            owner = self._owners.get(chat_id)
            if owner is None or not owner.active:
                raise ToolProtocolError("conflict")
            return owner

    def _require_chat(self, chat_id: str, project_id: str | None) -> ChatSession:
        """Check canonical active ownership before accessing a captured capability."""

        chat = self._chat_provider()
        if (
            str(chat.chat_id) != chat_id or chat.is_archived
            or (None if chat.project_id is None else str(chat.project_id)) != project_id
        ):
            raise ToolProtocolError("conflict")
        if chat.mode != "work":
            raise ToolProtocolError("work_mode_required")
        return chat

    def _authority(self, owner: _Owner) -> WorkspaceBoundary:
        """Reacquire the exact live owner and root with no registry-to-Backend lock edge."""

        with self._lock:
            if self._closed or not owner.active or self._owners.get(owner.chat_id) is not owner:
                raise ToolProtocolError("conflict")
        self._require_chat(owner.chat_id, owner.project_id)
        boundary = self._boundary_provider(owner.chat_id)
        boundary.metadata()
        if (boundary.normalized_root, boundary.root_identity) != (owner.root_path, owner.root_identity):
            raise ToolProtocolError("conflict")
        with self._lock:
            if self._closed or not owner.active or self._owners.get(owner.chat_id) is not owner:
                raise ToolProtocolError("conflict")
        return boundary

    def _read(self, owner: _Owner, call: ToolCall, cancel: Callable[[], bool]) -> ToolResult:
        try:
            boundary = self._authority(owner)
            result = WorkToolExecutor(boundary).run(call, should_cancel=cancel)
            self._authority(owner)
        except ToolProtocolError as error:
            result = ToolResult("denied", error_code=error.code)
        except (WorkspaceBoundaryError, ValueError, PermissionError):
            result = ToolResult("denied", error_code="conflict")
        except Exception:
            result = ToolResult("failed", error_code="io_error")
        if cancel():
            result = ToolResult("cancelled", error_code="cancelled")
        try:
            # A plan can retire while its read drains. The durable event still
            # belongs to the captured user action; only relative parameters and
            # the fixed outcome are retained, never the late result body.
            self._write_provider().record_read(owner.chat_id, owner.project_id, call, result)
        except Exception:
            return ToolResult("failed", error_code="io_error")
        return result

    def _preview(self, owner: _Owner, call: ToolCall) -> dict[str, Any]:
        writer = self._write_provider()
        try:
            boundary = self._authority(owner)
            approval = writer.preview(owner.chat_id, owner.project_id, boundary, call)
            self._authority(owner)
            with self._lock:
                if not owner.active or self._closed or self._owners.get(owner.chat_id) is not owner:
                    raise ToolProtocolError("conflict")
                assert owner.runner is not None
                plan = owner.runner.get()
                if plan is None or plan["state"] == "cancelled":
                    raise ToolProtocolError("cancelled")
                owner.tokens.add(approval["approvalId"])
            return approval
        except Exception:
            # Invalidation can win while preview reads a file. Revoke a token
            # created after that win as well, keeping retired approvals inert.
            writer.invalidate(owner.chat_id)
            raise

    def _confirm_write(self, owner: _Owner, token: str, granted: bool) -> ToolResult:
        try:
            boundary = self._authority(owner)
            with self._lock:
                if token not in owner.tokens:
                    return ToolResult("denied", error_code="permission_denied")
            return self._write_provider().confirm(owner.chat_id, owner.project_id, boundary, token, granted)
        except ToolProtocolError as error:
            return ToolResult("denied", error_code=error.code)
        except (WorkspaceBoundaryError, ValueError, PermissionError):
            return ToolResult("denied", error_code="conflict")
        except Exception:
            return ToolResult("failed", error_code="io_error")

    def _retire_locked(self, owner: _Owner) -> None:
        owner.active = False
        assert owner.runner is not None
        owner.runner.shutdown()
        if owner.runner.is_busy() and all(existing is not owner for existing in self._retired):
            self._retired.append(owner)

    def _busy_locked(self) -> bool:
        return any(
            owner.runner is not None and owner.runner.is_busy()
            for owner in [*self._owners.values(), *self._retired]
        )

    def _reap_locked(self) -> None:
        self._retired = [owner for owner in self._retired if owner.runner is not None and owner.runner.is_busy()]
