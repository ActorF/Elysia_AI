"""Bridge one live Work owner to strict checkpoints without restoring capabilities.

The session serializes writes independently of the runner's state lock. A store
revision is a compare-and-swap fence, while workflow revision describes logical
progress. Recovery only reconstructs paused data; native providers and approval
tokens are reacquired by the owner manager.
"""

from datetime import datetime, timezone
from threading import RLock
from typing import Any

from agent.workflow_state import (
    WorkflowState, WorkflowStateError, make_checkpoint, replace_workflow_plan, workflow_from_plan,
)
from agent.workflow_store import WorkflowStore


class WorkflowSession:
    """Persist one exact canonical Chat/Project/Workspace owner's latest plan."""

    def __init__(self, store: WorkflowStore, chat_id: str, project_id: str | None, selected_workspace_key: str) -> None:
        """Load only the owner's bounded latest record, without running any work."""

        self._store = store
        self._chat_id = chat_id
        self._project_id = project_id
        self._workspace_key = selected_workspace_key
        self._record = store.load_latest(chat_id, project_id, selected_workspace_key)
        self._live: WorkflowState | None = None
        self._lock = RLock()

    def recovery(self) -> WorkflowState | None:
        """Return immutable recovery data; uncertain writes remain inspection-only."""

        with self._lock:
            return None if self._record is None else self._record.state

    def adopt_recovery(self) -> dict[str, Any]:
        """Detach a safe paused/terminal plan while explicitly rejecting uncertain writes.

        Clearing checkpoint provenance here is a data transition only. The
        manager must validate current authority before installing this session;
        the runner remains paused until the user explicitly resumes it.
        """

        with self._lock:
            state = self.recovery()
            if state is None:
                raise WorkflowStateError("state_conflict")
            value = state.to_data()
            if value["checkpoint"]["disposition"] != "revalidate":
                raise WorkflowStateError("recovery_required")
            value["checkpoint"] = None
            value["revision"] += 1
            self._live = WorkflowState(value)
            return self._live.to_stage9_plan()

    def save(self, plan: dict[str, Any], admitted_step_id: str | None) -> None:
        """Durably record a safe boundary before allowing a new native side effect.

        The runner supplies the actual admitted marker. Failed storage leaves
        the old record and live state intact so no successful persistence can
        be claimed or a newer owner overwritten by this callback.
        """

        with self._lock:
            public = {**plan, "busy": False}
            now = datetime.now(timezone.utc).isoformat()
            live = (
                workflow_from_plan(public, chat_id=self._chat_id, project_id=self._project_id,
                                   selected_workspace_key=self._workspace_key, created_at=now,
                                   admitted_step_id=admitted_step_id)
                if self._live is None else replace_workflow_plan(
                    self._live, public, admitted_step_id=admitted_step_id,
                )
            )
            saved = make_checkpoint(live, saved_at=now)
            if self._record is not None and self._record.state is not None:
                previous = self._record.state.to_data()
                value = saved.to_data()
                if previous["workflowId"] == value["workflowId"]:
                    value["checkpoint"]["parentId"] = previous["checkpoint"]["checkpointId"]
                    saved = WorkflowState(value)
            record = self._store.save(saved, expected_revision=None if self._record is None else self._record.revision)
            self._live = live
            self._record = record
