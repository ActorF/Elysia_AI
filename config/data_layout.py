"""Define the upgrade-safe directory contract for private application data.

The application root contains executable code and replaceable model tooling,
while this layout owns files that must survive an application upgrade. The
desktop shell selects the root; Python derives every child path from that
trusted absolute value so individual services cannot drift to the install
directory or invent their own storage locations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProductionDataLayout:
    """Describe every stable private-data category below one movable root."""

    root: Path

    def __post_init__(self) -> None:
        """Require an absolute root before any service derives a child path."""

        if not isinstance(self.root, Path):
            raise TypeError("root must be a Path.")
        if not self.root.is_absolute():
            raise ValueError("root must be absolute.")

    @property
    def workspace(self) -> Path:
        """Return the durable application-state container."""

        return self.root / "workspace"

    @property
    def settings(self) -> Path:
        """Return the directory for revisioned user and device settings."""

        return self.workspace / "settings"

    @property
    def chats(self) -> Path:
        """Return the canonical Chat repository directory."""

        return self.workspace / "chats"

    @property
    def projects(self) -> Path:
        """Return the canonical Project repository directory."""

        return self.workspace / "projects"

    @property
    def memory(self) -> Path:
        """Return the profile and long-term Memory directory."""

        return self.workspace / "memory"

    @property
    def attachments(self) -> Path:
        """Return the private original-file and attachment directory."""

        return self.workspace / "attachments"

    @property
    def knowledge(self) -> Path:
        """Return the Source catalog, lifecycle journal, and index directory."""

        return self.workspace / "knowledge"

    @property
    def audio(self) -> Path:
        """Return the reserved transient-audio directory.

        Current microphone and synthesized speech bytes remain in memory. A
        dedicated location exists so future bounded spill files cannot land in
        durable Chat or Source storage and can be cleared as one category.
        """

        return self.root / "audio"

    @property
    def cache(self) -> Path:
        """Return the rebuildable application-cache directory."""

        return self.root / "cache"

    @property
    def logs(self) -> Path:
        """Return the local diagnostic-log directory."""

        return self.root / "logs"


__all__ = ["ProductionDataLayout"]
