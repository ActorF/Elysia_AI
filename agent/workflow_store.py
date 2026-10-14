"""Persist one capability-free recovery checkpoint per Chat in private storage.

Storage revisions are independent of workflow revisions: a new plan can replace
an older plan without letting a stale writer reuse its generation. Native locks
serialize cooperating application processes, while pinned ancestors reject root
replacement and links. Verified temporary bytes are flushed before an atomic
same-directory publication; an interrupted save preserves the previous record.
No selected Workspace files or live approval tokens are written by this store.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from work_tools.mutations import WorkspaceMutations, _mutation_descriptor
from work_tools.workspace import WorkspaceBoundary, WorkspaceBoundaryError, _kernel32

from .workflow_state import MAX_WORKFLOW_STATE_BYTES, WorkflowState, WorkflowStateError


_MAX_RECORDS = 1000
_MAX_RECORD_BYTES = MAX_WORKFLOW_STATE_BYTES + 1024
_MAX_REVISION = 2**53 - 1
_LOCK_NAME = ".workflow-store.lock"
_LOCKS_GUARD = RLock()
_LOCKS: dict[tuple[str, tuple[int, int]], Any] = {}

if os.name == "nt":
    _kernel32.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    _kernel32.MoveFileExW.restype = ctypes.c_int


def _owner(chat_id: str, project_id: str | None = None) -> None:
    if type(chat_id) is not str or re.fullmatch(r"chat_[A-Za-z0-9_-]{1,123}", chat_id) is None:
        raise WorkflowStateError()
    if project_id is not None and (type(project_id) is not str or re.fullmatch(r"project_[A-Za-z0-9_-]{1,120}", project_id) is None):
        raise WorkflowStateError()


def _filename(chat_id: str) -> str:
    return "state_" + hashlib.sha256(chat_id.encode("utf-8")).hexdigest() + ".json"


@dataclass(frozen=True, slots=True)
class StoredWorkflow:
    """Return a durable per-Chat CAS revision and an optional owner-matched checkpoint.

    A tombstone or a different Project/Workspace has state=None but retains its
    revision, permitting an explicit new plan to replace it using the same CAS.
    """

    revision: int
    state: WorkflowState | None


class WorkflowStore:
    """Own bounded private latest checkpoints without reconstructing execution permissions."""

    def __init__(self, private_directory: Path) -> None:
        """Create a trusted local private directory through pinned, non-link ancestors."""

        if not isinstance(private_directory, Path) or not private_directory.is_absolute():
            raise WorkflowStateError()
        missing: list[Path] = []
        parent = private_directory
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        try:
            boundary = WorkspaceBoundary(parent)
            for directory in reversed(missing):
                with boundary.guarded_parent(directory.name) as access:
                    try:
                        os.mkdir(access.path if access.directory_fd is None else access.leaf_name, 0o700, dir_fd=access.directory_fd)
                    except FileExistsError:
                        # Another store may have initialized the same fixed
                        # private directory; the new boundary validates it.
                        pass
                boundary = WorkspaceBoundary(directory)
            self._storage = WorkspaceBoundary(private_directory)
            lock_key = (os.path.normcase(self._storage.normalized_root), self._storage.root_identity)
            with _LOCKS_GUARD:
                self._lock = _LOCKS.setdefault(lock_key, RLock())
            with self._lock:
                with self._storage.guarded_parent(_LOCK_NAME) as access:
                    if access.stat_leaf() is None:
                        try:
                            WorkspaceMutations(self._storage).create(_LOCK_NAME, b"")
                        except WorkspaceBoundaryError:
                            if access.stat_leaf() is None:
                                raise
                if self._storage.metadata(_LOCK_NAME).kind != "file":
                    raise WorkflowStateError("io_error")
        except (WorkspaceBoundaryError, OSError):
            raise WorkflowStateError("io_error") from None

    def load_latest(self, chat_id: str, project_id: str | None, selected_workspace_key: str) -> StoredWorkflow | None:
        """Load only matching canonical scope; return the CAS head without content for mismatched scope."""

        _owner(chat_id, project_id)
        if type(selected_workspace_key) is not str or re.fullmatch(r"[0-9a-f]{64}", selected_workspace_key) is None:
            raise WorkflowStateError()
        with self._locked():
            record = self._load(chat_id)
            if record is None or record.state is None:
                return record
            request = record.state.to_data()["request"]
            if request["projectId"] != project_id or request["workspaceKey"] != selected_workspace_key:
                return StoredWorkflow(record.revision, None)
            return record

    def save(self, state: WorkflowState, *, expected_revision: int | None) -> StoredWorkflow:
        """CAS-save a scrubbed checkpoint, permitting an explicit new plan to replace the Chat's previous plan."""

        if type(state) is not WorkflowState:
            raise WorkflowStateError()
        value = state.to_data()
        if value["checkpoint"] is None or any(step["approval"] is not None for step in value["plan"]["steps"]):
            raise WorkflowStateError()
        chat_id = value["request"]["chatId"]
        _owner(chat_id, value["request"]["projectId"])
        with self._locked():
            previous = self._load(chat_id)
            revision = self._next_revision(previous, expected_revision)
            if previous is not None and previous.state is not None:
                old = previous.state.to_data()
                if old["workflowId"] == value["workflowId"]:
                    # Replacing a plan uses a fresh workflow identity. Within
                    # one workflow, even a newer storage CAS cannot substitute
                    # the captured owner, ordered calls, or step identities.
                    if (value["revision"] <= old["revision"] or value["request"] != old["request"]
                        or value["plan"]["planId"] != old["plan"]["planId"]
                        or [step["stepId"] for step in value["plan"]["steps"]]
                        != [step["stepId"] for step in old["plan"]["steps"]]):
                        raise WorkflowStateError("state_conflict")
            result = StoredWorkflow(revision, state)
            self._publish(chat_id, result, new_record=previous is None)
            return result

    def forget(self, chat_id: str, *, expected_revision: int | None) -> StoredWorkflow:
        """Persist a CAS tombstone for deletion, preventing earlier writers from resurrecting the task."""

        _owner(chat_id)
        with self._locked():
            previous = self._load(chat_id)
            result = StoredWorkflow(self._next_revision(previous, expected_revision), None)
            self._publish(chat_id, result, new_record=previous is None)
            return result

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Pin the root and exclusively own the fixed native lock across the complete CAS transaction."""

        with self._lock:
            try:
                with self._storage.guarded_parent(_LOCK_NAME) as access, _mutation_descriptor(access):
                    if access.stat_leaf() is None:
                        raise WorkflowStateError("io_error")
                    yield
            except WorkspaceBoundaryError:
                raise WorkflowStateError("state_conflict") from None
            except OSError:
                raise WorkflowStateError("io_error") from None

    @staticmethod
    def _next_revision(previous: StoredWorkflow | None, expected: int | None) -> int:
        if expected is not None and (type(expected) is not int or not 1 <= expected < _MAX_REVISION):
            raise WorkflowStateError("state_conflict")
        actual = None if previous is None else previous.revision
        if actual != expected or actual == _MAX_REVISION:
            raise WorkflowStateError("state_conflict")
        return 1 if actual is None else actual + 1

    def _load(self, chat_id: str) -> StoredWorkflow | None:
        name = _filename(chat_id)
        with self._storage.guarded_parent(name) as access:
            if access.stat_leaf() is None:
                return None
        raw = self._storage.read_bytes(name, max_bytes=_MAX_RECORD_BYTES)

        def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise WorkflowStateError()
                result[key] = value
            return result

        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
            if (type(value) is not dict or set(value) != {"version", "chatId", "revision", "state"}
                or type(value["version"]) is not int or value["version"] != 1 or value["chatId"] != chat_id
                or type(value["revision"]) is not int or not 1 <= value["revision"] <= _MAX_REVISION):
                raise WorkflowStateError()
            state = None if value["state"] is None else WorkflowState(value["state"])
            if state is not None:
                data = state.to_data()
                if data["request"]["chatId"] != chat_id or data["checkpoint"] is None:
                    raise WorkflowStateError()
                _owner(chat_id, data["request"]["projectId"])
            return StoredWorkflow(value["revision"], state)
        except (UnicodeError, ValueError, TypeError, KeyError, RecursionError):
            raise WorkflowStateError() from None

    def _publish(self, chat_id: str, result: StoredWorkflow, *, new_record: bool) -> None:
        """Flush verified private bytes, publish atomically, and flush the rename's durable metadata."""

        entries = self._storage.list_directory(max_entries=_MAX_RECORDS + 1)
        if new_record and len(entries) >= _MAX_RECORDS + 1:
            raise WorkflowStateError("io_error")
        encoded = json.dumps({
            "version": 1, "chatId": chat_id, "revision": result.revision,
            "state": None if result.state is None else result.state.to_data(),
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_RECORD_BYTES:
            raise WorkflowStateError()
        temporary = ".checkpoint_" + uuid4().hex + ".tmp"
        digest = hashlib.sha256(encoded).hexdigest()
        mutations = WorkspaceMutations(self._storage)
        with self._storage.guarded_parent(_filename(chat_id)) as access:
            try:
                mutations.create(temporary, encoded)
                if os.name == "nt":
                    # WRITE_THROUGH complements the already flushed bytes by
                    # waiting for the same-volume rename metadata to commit.
                    if not _kernel32.MoveFileExW(str(access.parent_path / temporary), str(access.path), 0x1 | 0x8):
                        raise WorkflowStateError("io_error")
                else:
                    os.replace(temporary, access.leaf_name, src_dir_fd=access.directory_fd, dst_dir_fd=access.directory_fd)
                    assert access.directory_fd is not None
                    os.fsync(access.directory_fd)
                if self._storage.read_bytes(_filename(chat_id), max_bytes=_MAX_RECORD_BYTES) != encoded:
                    raise WorkflowStateError("io_error")
            finally:
                try:
                    mutations.remove(temporary, digest)
                except (WorkspaceBoundaryError, OSError):
                    # Success removes the temporary name. On failure only its
                    # exact verified bytes are eligible for cleanup; unknown
                    # crash evidence is never deleted recursively.
                    pass
