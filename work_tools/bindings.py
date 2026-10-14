"""Persist explicit Chat workspace selections independently of conversation data.

Binding is a user capability, not a filesystem path inferred from messages or
inherited silently from a Project. A recorded directory identity invalidates
the capability when the selected root is replaced while the application is
closed. Execution still acquires the native WorkspaceBoundary on every call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from threading import RLock

from chats.storage import atomic_write_json, read_json_object


@dataclass(frozen=True, slots=True)
class WorkBinding:
    """Keep one canonical directory selection and its durable disk identity."""

    root: str
    device: int
    inode: int

    def validate_identity(self) -> None:
        """Reject a missing or replaced directory before acquiring its boundary."""

        state = Path(self.root).stat(follow_symlinks=False)
        if (state.st_dev, state.st_ino) != (self.device, self.inode):
            raise ValueError("The Workspace changed; choose the folder again.")


class WorkspaceBindings:
    """Atomically save explicit per-Chat bindings with no conversation schema change."""

    def __init__(self, directory: Path) -> None:
        """Select a private data directory; construction does not write files."""

        self._directory = directory
        self._lock = RLock()

    def _path(self, chat_id: str) -> Path:
        if not isinstance(chat_id, str) or re.fullmatch(r"chat_[A-Za-z0-9_-]{1,123}", chat_id) is None:
            raise ValueError("A valid Chat ID is required.")
        return self._directory / "bindings" / f"{chat_id}.json"

    def get(self, chat_id: str) -> WorkBinding | None:
        """Load a strict binding or fail closed on corrupt persisted state."""

        with self._lock:
            try:
                data = read_json_object(self._path(chat_id))
            except FileNotFoundError:
                return None
            if set(data) != {"version", "root", "device", "inode"} or data["version"] != 1:
                raise ValueError("The stored Workspace binding is invalid.")
            root, device, inode = data["root"], data["device"], data["inode"]
            if not isinstance(root, str) or not Path(root).is_absolute():
                raise ValueError("The stored Workspace binding is invalid.")
            if type(device) is not int or type(inode) is not int:
                raise ValueError("The stored Workspace identity is invalid.")
            return WorkBinding(root, device, inode)

    def set(self, chat_id: str, normalized_root: str | None, *, expected_identity: tuple[int, int] | None = None) -> WorkBinding | None:
        """Save a boundary-validated native selection or revoke the Chat capability.

        The caller must construct WorkspaceBoundary first. Recording an identity
        is not sufficient to authorize arbitrary renderer-provided paths.
        """

        with self._lock:
            destination = self._path(chat_id)
            if normalized_root is None:
                destination.unlink(missing_ok=True)
                return None
            root = Path(normalized_root)
            if not root.is_absolute() or not root.is_dir() or root.is_symlink():
                raise ValueError("Choose an existing local Workspace directory.")
            state = root.stat(follow_symlinks=False)
            if expected_identity is not None and (state.st_dev, state.st_ino) != expected_identity:
                raise ValueError("The selected Workspace changed before it was saved.")
            binding = WorkBinding(str(root), state.st_dev, state.st_ino)
            atomic_write_json(destination, {"version": 1, "root": binding.root,
                                          "device": binding.device, "inode": binding.inode})
            return binding
