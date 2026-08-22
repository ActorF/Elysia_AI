"""Define validated Global, Project, and Chat memory scopes."""

import re
from dataclasses import dataclass
from typing import Final, Literal, cast

from chats import ChatId, ProjectId

MemoryScope = Literal["global", "project", "chat"]

_PROJECT_ID_PATTERN: Final = re.compile(
    r"^project_[A-Za-z0-9_-]+$"
)
_CHAT_ID_PATTERN: Final = re.compile(
    r"^chat_[A-Za-z0-9_-]+$"
)


@dataclass(frozen=True, slots=True)
class MemoryScopeRef:
    """Identify exactly one readable or writable memory scope."""

    scope: MemoryScope
    scope_id: str | None

    def __post_init__(self) -> None:
        """Require IDs only for Project and Chat scopes."""

        if self.scope not in ("global", "project", "chat"):
            raise ValueError(
                "scope must be global, project, or chat."
            )

        if self.scope == "global":
            if self.scope_id is not None:
                raise ValueError(
                    "Global memory cannot have a scope_id."
                )
            return

        if not isinstance(self.scope_id, str):
            raise ValueError(
                f"{self.scope} memory requires a scope_id."
            )

        pattern = (
            _PROJECT_ID_PATTERN
            if self.scope == "project"
            else _CHAT_ID_PATTERN
        )
        if pattern.fullmatch(self.scope_id) is None:
            raise ValueError(
                f"{self.scope} memory requires a valid "
                f"{self.scope}_ ID."
            )


@dataclass(frozen=True, slots=True)
class MemoryScopeContext:
    """Describe the scopes one active Chat is allowed to read."""

    chat_id: ChatId
    project_id: ProjectId | None

    def __post_init__(self) -> None:
        """Validate the Chat and optional Project identifiers."""

        MemoryScopeRef(
            scope="chat",
            scope_id=str(self.chat_id),
        )

        if self.project_id is not None:
            MemoryScopeRef(
                scope="project",
                scope_id=str(self.project_id),
            )

    def readable_scopes(self) -> tuple[MemoryScopeRef, ...]:
        """Return allowed scopes from most specific to broadest."""

        scopes = [
            MemoryScopeRef(
                scope="chat",
                scope_id=str(self.chat_id),
            )
        ]

        if self.project_id is not None:
            scopes.append(
                MemoryScopeRef(
                    scope="project",
                    scope_id=str(self.project_id),
                )
            )

        scopes.append(
            MemoryScopeRef(
                scope="global",
                scope_id=None,
            )
        )
        return tuple(scopes)


def validate_memory_scope(
    scope: str,
    scope_id: str | None,
) -> MemoryScopeRef:
    """Validate external scope text and narrow it for typed callers."""

    if scope not in ("global", "project", "chat"):
        raise ValueError(
            "scope must be global, project, or chat."
        )

    return MemoryScopeRef(
        scope=cast(MemoryScope, scope),
        scope_id=scope_id,
    )
