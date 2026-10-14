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
from work_tools.bindings import WorkspaceBindings
from work_tools.workspace import WorkspaceBoundary


class DesktopWorkRuntime:
    """Own Chat workspace selections and check canonical mode on every request."""

    def __init__(self, directory: Path, chat_provider: Callable[[], ChatSession]) -> None:
        """Use one private directory and the current canonical active-Chat provider."""

        self._bindings = WorkspaceBindings(directory)
        self._chat_provider = chat_provider
        self._lock = RLock()

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Apply an explicit authenticated Work request or deny stale/Chat owners."""

        with self._lock:
            chat = self._chat_provider()
            if str(chat.chat_id) != params["chatId"] or chat.is_archived:
                raise ValueError("Work request no longer belongs to the active Chat.")
            require_work_mode(chat.mode)
            if method == "work.workspace.bind":
                selected = params["workspacePath"]
                if selected is None:
                    self._bindings.set(str(chat.chat_id), None)
                else:
                    boundary = WorkspaceBoundary(Path(selected))
                    self._bindings.set(str(chat.chat_id), boundary.normalized_root,
                                       expected_identity=boundary.root_identity)
            elif method != "work.workspace.get":
                raise ValueError("Work method is unavailable.")
            binding = self._bindings.get(str(chat.chat_id))
            return {"kind": "work.workspace", "chatId": str(chat.chat_id),
                    "workspacePath": None if binding is None else binding.root}

    def boundary(self, chat_id: str) -> WorkspaceBoundary:
        """Reacquire the exact selected disk identity for one executable operation."""

        binding = self._bindings.get(chat_id)
        if binding is None:
            raise ValueError("Choose a Workspace before using Work tools.")
        return WorkspaceBoundary(Path(binding.root), expected_root_identity=(binding.device, binding.inode))
