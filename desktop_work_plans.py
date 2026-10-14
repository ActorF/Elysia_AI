"""Bind explicit Work plans to canonical Chat and Workspace capabilities.

The manager binds in-memory plan lifetimes to strict private checkpoints. File
previews, single-use permission, and mutation recovery remain in WriteCoordinator.
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
from agent.workflow_session import WorkflowSession
from agent.workflow_state import workspace_key
from agent.workflow_store import WorkflowStore
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
    restored: bool = False
    use_project_knowledge: bool = False


class DesktopWorkPlans:
    """Manage per-Chat plans, their approval route, and all physically draining workers."""

    def __init__(
        self,
        chat_provider: Callable[[], ChatSession],
        boundary_provider: Callable[[str], WorkspaceBoundary],
        write_provider: Callable[[], WriteCoordinator],
        *,
        nodes_factory: Callable[[Callable[[], None], bool], WorkflowNodes] | None = None,
        workflow_store: WorkflowStore | Callable[[], WorkflowStore] | None = None,
    ) -> None:
        """Inject canonical providers without opening a filesystem or starting threads."""

        if not all(callable(provider) for provider in (chat_provider, boundary_provider, write_provider)):
            raise TypeError("Work plan providers must be callable.")
        self._chat_provider = chat_provider
        self._boundary_provider = boundary_provider
        self._write_provider = write_provider
        self._nodes_factory = nodes_factory
        self._workflow_store = workflow_store
        self._recovery_notices: dict[str, dict[str, Any]] = {}
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

        return self._create(chat, title, calls, use_project_knowledge=use_project_knowledge)

    def _create(self, chat: ChatSession, title: str, calls: list[ToolCall], *,
                use_project_knowledge: bool = False, reservation: object | None = None) -> dict[str, Any]:
        """Retain one creation CAS across a revised decision and fresh owner publication."""

        with self._lock:
            self._require_open_locked()
        if not isinstance(chat, ChatSession):
            raise ToolProtocolError()
        chat_id = str(chat.chat_id)
        project_id = None if chat.project_id is None else str(chat.project_id)
        self._require_chat(chat_id, project_id)
        boundary = self._boundary_provider(chat_id)
        boundary.metadata()
        owner = _Owner(chat_id, project_id, boundary.normalized_root, boundary.root_identity,
                       use_project_knowledge=use_project_knowledge)
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
        creation = object() if reservation is None else reservation
        with self._lock:
            self._require_open_locked()
            self._reap_locked()
            if (self._busy_locked() or (reservation is None and self._creation_token is not None)
                or (reservation is not None and self._creation_token is not creation)):
                raise ToolProtocolError("conflict")
            self._creation_token = creation
            old = self._owners.get(chat_id)
            if old is not None:
                self._retire_locked(old)
        # Revocation uses the writer's short-lived pending-permission mutex.
        # Keep providers outside the registry lock so Backend authority and
        # plan publication never acquire their locks in opposite orders.
        try:
            if old is not None and old.runner is not None:
                old.runner.flush_checkpoint()
            self._write_provider().invalidate(chat_id)
            self._require_chat(chat_id, project_id)
            fresh = self._boundary_provider(chat_id)
            fresh.metadata()
            if (fresh.normalized_root, fresh.root_identity) != (owner.root_path, owner.root_identity):
                raise ToolProtocolError("conflict")
            store = self._store()
            if store is not None:
                session = WorkflowSession(store, chat_id, project_id, workspace_key(owner.root_path, owner.root_identity))
                with self._lock:
                    self._require_open_locked()
                    if self._creation_token is not creation:
                        raise ToolProtocolError("conflict")
                runner.attach_checkpoint(session.save)
            with self._lock:
                self._require_open_locked()
                if self._busy_locked() or self._creation_token is not creation:
                    raise ToolProtocolError("conflict")
                self._owners[chat_id] = owner
                self._recovery_notices.pop(chat_id, None)
        finally:
            with self._lock:
                if self._creation_token is creation:
                    self._creation_token = None
        snapshot["busy"] = False
        return snapshot

    def _check_node_authority(self, owner: _Owner) -> None:
        """Revalidate the selected capability before and after advisory retrieval."""

        self._authority(owner)

    def _store(self) -> WorkflowStore | None:
        return self._workflow_store() if callable(self._workflow_store) else self._workflow_store

    def _recover(self, chat_id: str) -> _Owner | None:
        """Reserve recovery admission so a slow lookup cannot revoke a newer plan's token."""

        creation = object()
        with self._lock:
            self._require_open_locked()
            if chat_id in self._owners:
                return self._owners[chat_id]
            if self._busy_locked() or self._creation_token is not None:
                return None
            self._creation_token = creation
        try:
            return self._recover_reserved(chat_id, creation)
        finally:
            with self._lock:
                if self._creation_token is creation:
                    self._creation_token = None

    def _recover_reserved(self, chat_id: str, creation: object) -> _Owner | None:
        """Expose the latest safe record as paused data; never resume a native action.

        No recorded path is opened. The canonical current Chat's existing
        binding supplies a fresh root identity, and all native callbacks retain
        the ordinary per-action authority checks after explicit Resume.
        """

        store = self._store()
        if store is None:
            return None
        chat = self._chat_provider()
        project_id = None if chat.project_id is None else str(chat.project_id)
        self._require_chat(chat_id, project_id)
        try:
            boundary = self._boundary_provider(chat_id)
            boundary.metadata()
        except (ValueError, OSError, PermissionError):
            return None
        session = WorkflowSession(store, chat_id, project_id, workspace_key(boundary.normalized_root, boundary.root_identity))
        with self._lock:
            self._recovery_notices.pop(chat_id, None)
        recovery = session.recovery()
        if recovery is None:
            return None
        value = recovery.to_data()
        if value["checkpoint"]["disposition"] == "uncertain_write":
            with self._lock:
                self._recovery_notices[chat_id] = {"status": "uncertain_write", "planId": value["plan"]["planId"]}
            return None
        owner = _Owner(chat_id, project_id, boundary.normalized_root, boundary.root_identity, restored=True)
        runner = WorkPlanRunner(
            lambda call, cancel: self._read(owner, call, cancel),
            lambda call: self._preview(owner, call),
            lambda token, granted: self._confirm_write(owner, token, granted),
            workflow_nodes=None if self._nodes_factory is None else self._nodes_factory(
                lambda: self._check_node_authority(owner), False,
            ),
            checkpoint=session.save,
        )
        owner.runner = runner
        runner.restore(session.adopt_recovery())
        writer = self._write_provider()
        self._require_chat(chat_id, project_id)
        fresh = self._boundary_provider(chat_id)
        fresh.metadata()
        if (fresh.normalized_root, fresh.root_identity) != (owner.root_path, owner.root_identity):
            raise ToolProtocolError("conflict")
        with self._lock:
            self._require_open_locked()
            if self._busy_locked() or self._creation_token is not creation or chat_id in self._owners:
                return self._owners.get(chat_id)
            # This short pending-token operation performs no disk I/O. Keep
            # invalidation and publication in one registry turn so a stale
            # recovery lookup cannot revoke a newly created owner's preview.
            writer.invalidate(chat_id)
            self._owners[chat_id] = owner
            return owner

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """Return only the canonical current Chat's owned detached plan snapshot."""

        with self._lock:
            owner = self._owners.get(chat_id)
        if owner is None:
            owner = self._recover(chat_id)
        if owner is None:
            return None
        # A retired plan remains visible as cancelled while its callback drains.
        # Reading that snapshot grants no capability and needs no bound root.
        self._require_chat(owner.chat_id, owner.project_id)
        assert owner.runner is not None
        snapshot = owner.runner.get()
        if snapshot is not None and snapshot["state"] in {"waiting", "paused"} and owner.active and not owner.runner.is_busy():
            waiting = next((step for step in snapshot["steps"] if step["approval"] is not None), None)
            if waiting is not None and self._write_provider().approval_expired(owner.chat_id, owner.project_id,
                                                                              waiting["approval"]["approvalId"]):
                # The callback writes only the timeout audit, on the ordinary
                # worker. Polling remains responsive and cannot falsely release
                # capacity before that durable decision finishes.
                self._authority(owner)
                try:
                    snapshot = owner.runner.expire_approval(snapshot["planId"], waiting["approval"]["approvalId"])
                except ValueError:
                    # Another foreground decision may win between the detached
                    # sample and token claim; publish the winner's current state.
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
        owner.restored = False
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

    def revise(self, chat_id: str, plan_id: str, approval_id: str, replacement: ToolCall) -> dict[str, Any]:
        """Consume modify once, close the old plan, and publish only a fresh pending suffix.

        The reservation survives private audit/checkpoint I/O without holding the
        registry lock. Cancel, navigation, or shutdown can invalidate it before
        publication; no revised call runs until a separate explicit Start.
        """

        owner = self._owner(chat_id)
        boundary = self._authority(owner)
        creation = object()
        with self._lock:
            self._require_open_locked()
            if self._busy_locked() or self._creation_token is not None or approval_id not in owner.tokens:
                raise ToolProtocolError("conflict")
            assert owner.runner is not None
            title, calls = owner.runner.revision_calls(plan_id, approval_id, replacement)
            self._creation_token = creation
            self._retire_locked(owner)
        try:
            self._require_chat(owner.chat_id, owner.project_id)
            fresh = self._boundary_provider(chat_id)
            fresh.metadata()
            if (fresh.normalized_root, fresh.root_identity) != (boundary.normalized_root, boundary.root_identity):
                raise ToolProtocolError("conflict")
            outcome = self._write_provider().confirm(chat_id, owner.project_id, fresh, approval_id, False, decision="modify")
            if outcome.status != "denied" or outcome.error_code != "permission_denied":
                raise ToolProtocolError(outcome.error_code or "io_error")
            self._write_provider().invalidate(chat_id)
            with self._lock:
                self._require_open_locked()
                if self._creation_token is not creation:
                    raise ToolProtocolError("conflict")
            chat = self._require_chat(owner.chat_id, owner.project_id)
            return self._create(chat, title, calls, use_project_knowledge=owner.use_project_knowledge, reservation=creation)
        finally:
            with self._lock:
                if self._creation_token is creation:
                    self._creation_token = None

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

    def recovery_notice(self, chat_id: str) -> dict[str, Any] | None:
        """Report recovery and durability facts separately from native execution outcomes."""

        with self._lock:
            owner = self._owners.get(chat_id)
            notice = self._recovery_notices.get(chat_id)
        if owner is not None and owner.runner is not None:
            plan = owner.runner.get()
            if plan is not None:
                if owner.runner.checkpoint_failed():
                    return {"status": "storage_failed", "planId": plan["planId"]}
                if owner.restored and plan["state"] == "paused":
                    return {"status": "restored", "planId": plan["planId"]}
        return None if notice is None else dict(notice)

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
            self._flush_retired(owner)

    def forget_checkpoint(self, chat_id: str) -> None:
        """Tombstone a deleted Chat's private plan without deleting mutation recovery evidence."""

        store = self._store()
        if store is None:
            return
        # A deliberately unmatched key reads only the CAS head, never content
        # from the deleted owner's former directory or Project.
        head = store.load_latest(chat_id, None, "0" * 64)
        store.forget(chat_id, expected_revision=None if head is None else head.revision)
        with self._lock:
            self._recovery_notices.pop(chat_id, None)

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
        for owner in owners:
            self._flush_retired(owner)
        return not self.is_busy()

    @staticmethod
    def _flush_retired(owner: _Owner) -> None:
        """Persist an idle retirement without blocking cancellation behind active native I/O."""

        if owner.runner is not None and not owner.runner.is_busy():
            try:
                owner.runner.flush_checkpoint()
            except Exception:
                # The runner records this durability failure for UI reporting;
                # the older checkpoint still cannot auto-resume any operation.
                pass

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
        owner.runner.shutdown(persist=False)
        if owner.runner.is_busy() and all(existing is not owner for existing in self._retired):
            self._retired.append(owner)

    def _busy_locked(self) -> bool:
        return any(
            owner.runner is not None and owner.runner.is_busy()
            for owner in [*self._owners.values(), *self._retired]
        )

    def _reap_locked(self) -> None:
        self._retired = [owner for owner in self._retired if owner.runner is not None and owner.runner.is_busy()]
