"""Coordinate Project and Chat relationships without exposing paths."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Literal, cast

from chats import (
    ChatId,
    ChatRepository,
    ChatRepositoryError,
    ChatSession,
    ChatSessionMeta,
    ProjectId,
)

from .domain import Project, ProjectSettings, WorkspaceBinding
from .exceptions import (
    ChatProjectConflictError,
    ProjectArchivedError,
    ProjectChatBusyError,
    ProjectHasChatsError,
    ProjectRelationshipError,
    ProjectRelationshipRollbackError,
    ProjectRepositoryError,
)
from .repository import ProjectRepository

ProjectDeletionPolicy = Literal["restrict", "detach", "cascade"]
ChatBusyPredicate = Callable[[ChatId], bool]
Clock = Callable[[], datetime]


def _chat_is_never_busy(_chat_id: ChatId) -> bool:
    """Provide a backwards-compatible idle policy for offline callers."""

    return False


def _default_clock() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(timezone.utc)


class ProjectChatService:
    """Maintain one-to-many Project relationships across repositories."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        chat_repository: ChatRepository,
        *,
        is_chat_busy: ChatBusyPredicate = _chat_is_never_busy,
        clock: Clock = _default_clock,
    ) -> None:
        """Receive repositories and the process-local Chat busy predicate."""

        self._project_repository = project_repository
        self._chat_repository = chat_repository
        self._is_chat_busy = is_chat_busy
        self._clock = clock

    def create_project(
        self,
        *,
        name: str,
        custom_instructions: str | None = None,
    ) -> Project:
        """Create one active Project through the repository boundary."""

        return self._project_repository.create_project(
            name=name,
            settings=ProjectSettings(
                custom_instructions=custom_instructions,
            ),
        )

    def list_projects(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[Project, ...]:
        """List Project aggregates without exposing persistence paths."""

        return self._project_repository.list_projects(
            include_archived=include_archived,
        )

    def get_project(self, project_id: ProjectId) -> Project:
        """Load one Project through the repository boundary."""

        return self._project_repository.get_project(project_id)

    def rename_project(
        self,
        project_id: ProjectId,
        new_name: str,
    ) -> Project:
        """Rename an active Project when all linked Chats are idle."""

        self._require_active_project(project_id)
        self._require_project_chats_idle(project_id)
        return self._project_repository.rename_project(
            project_id,
            new_name,
        )

    def update_project(
        self,
        project_id: ProjectId,
        *,
        name: str,
        custom_instructions: str | None,
    ) -> Project:
        """Atomically replace UI-editable fields with one repository save."""

        project = self._require_active_project(project_id)
        updated_project = replace(
            project,
            name=name,
            settings=ProjectSettings(
                default_model_name=project.settings.default_model_name,
                custom_instructions=custom_instructions,
            ),
            updated_at=self._next_timestamp(project.updated_at),
        )
        if updated_project == project:
            return project

        self._require_project_chats_idle(project_id)
        self._project_repository.save_project(updated_project)
        return updated_project

    def update_custom_instructions(
        self,
        project_id: ProjectId,
        custom_instructions: str | None,
    ) -> Project:
        """Update instructions while preserving other Project settings."""

        project = self._require_active_project(project_id)
        updated_settings = ProjectSettings(
            default_model_name=project.settings.default_model_name,
            custom_instructions=custom_instructions,
        )
        self._require_project_chats_idle(project_id)
        return self._project_repository.update_settings(
            project_id,
            updated_settings,
        )

    def bind_workspace(
        self,
        project_id: ProjectId,
        root_path: str,
    ) -> Project:
        """Set or atomically replace an active Project's Workspace root."""

        self._require_active_project(project_id)
        workspace_binding = WorkspaceBinding(root_path=root_path)
        self._require_project_chats_idle(project_id)
        return self._project_repository.set_workspace_binding(
            project_id,
            workspace_binding,
        )

    def unbind_workspace(self, project_id: ProjectId) -> Project:
        """Remove an active Project's optional Workspace binding."""

        project = self._require_active_project(project_id)
        if project.workspace_binding is None:
            return project

        self._require_project_chats_idle(project_id)
        return self._project_repository.set_workspace_binding(
            project_id,
            None,
        )

    def archive_project(self, project_id: ProjectId) -> Project:
        """Archive a Project without moving or deleting any linked Chats."""

        project = self._project_repository.get_project(project_id)
        if project.is_archived:
            return project

        self._require_project_chats_idle(project_id)
        return self._project_repository.archive_project(project_id)

    def restore_project(self, project_id: ProjectId) -> Project:
        """Restore an archived Project without changing Chat relationships."""

        project = self._project_repository.get_project(project_id)
        if not project.is_archived:
            return project

        return self._project_repository.archive_project(
            project_id,
            archived=False,
        )

    def list_project_chats(
        self,
        project_id: ProjectId,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """Return only Chats belonging to the requested Project scope."""

        self._project_repository.get_project(project_id)
        return tuple(
            metadata
            for metadata in self._chat_repository.list_chats(
                include_archived=include_archived
            )
            if metadata.project_id == project_id
        )

    def attach_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Attach an unassigned idle Chat to one active Project."""

        self._require_active_project(project_id)
        chat = self._chat_repository.get_chat(chat_id)

        if chat.project_id == project_id:
            return chat

        if chat.project_id is not None:
            raise ChatProjectConflictError(
                f"Chat {chat_id} already belongs to "
                f"Project {chat.project_id}; use transfer_chat."
            )

        self._require_chat_idle(chat_id)
        updated_chat = replace(chat, project_id=project_id)
        self._chat_repository.save_chat(updated_chat)
        return updated_chat

    def detach_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Detach one idle Chat from the named active Project."""

        self._require_active_project(project_id)
        chat = self._chat_repository.get_chat(chat_id)

        if chat.project_id != project_id:
            raise ChatProjectConflictError(
                f"Chat {chat_id} does not belong to Project {project_id}."
            )

        self._require_chat_idle(chat_id)
        updated_chat = replace(chat, project_id=None)
        self._chat_repository.save_chat(updated_chat)
        return updated_chat

    def add_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Compatibility alias for :meth:`attach_chat`."""

        return self.attach_chat(project_id, chat_id)

    def remove_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Compatibility alias for :meth:`detach_chat`."""

        return self.detach_chat(project_id, chat_id)

    def transfer_chat(
        self,
        chat_id: ChatId,
        destination_project_id: ProjectId,
    ) -> ChatSession:
        """Move an assigned Chat directly to another active Project."""

        self._require_active_project(destination_project_id)
        chat = self._chat_repository.get_chat(chat_id)

        if chat.project_id is None:
            raise ChatProjectConflictError(
                f"Chat {chat_id} is not assigned; use add_chat."
            )

        if chat.project_id == destination_project_id:
            return chat

        self._require_active_project(chat.project_id)
        self._require_chat_idle(chat_id)
        updated_chat = replace(
            chat,
            project_id=destination_project_id,
        )
        self._chat_repository.save_chat(updated_chat)
        return updated_chat

    def move_chat(
        self,
        chat_id: ChatId,
        project_id: ProjectId | None,
    ) -> ChatSession:
        """Atomically attach, detach, transfer, or no-op from current state."""

        chat = self._chat_repository.get_chat(chat_id)
        if chat.project_id == project_id:
            return chat

        if project_id is not None:
            self._require_active_project(project_id)
        if chat.project_id is not None:
            self._require_active_project(chat.project_id)

        self._require_chat_idle(chat_id)
        updated_chat = replace(chat, project_id=project_id)
        self._chat_repository.save_chat(updated_chat)
        return updated_chat

    def delete_project(
        self,
        project_id: ProjectId,
        *,
        policy: ProjectDeletionPolicy,
    ) -> None:
        """Delete a Project using one explicit Chat handling policy."""

        if policy not in ("restrict", "detach", "cascade"):
            raise ValueError(
                "policy must be restrict, detach, or cascade."
            )

        self._project_repository.get_project(project_id)
        linked_chats = self._load_project_chats(project_id)

        if policy == "restrict":
            if linked_chats:
                raise ProjectHasChatsError(
                    f"Project {project_id} still owns "
                    f"{len(linked_chats)} Chat(s)."
                )

            self._project_repository.delete_project(project_id)
            return

        if policy == "detach":
            self._require_chats_idle(linked_chats)
            self._detach_then_delete(project_id, linked_chats)
            return

        self._require_chats_idle(linked_chats)
        self._cascade_delete(project_id, linked_chats)

    def _require_active_project(self, project_id: ProjectId) -> Project:
        """Return one Project only while its aggregate is mutable."""

        project = self._project_repository.get_project(project_id)
        if project.is_archived:
            raise ProjectArchivedError(
                f"Archived Project is read-only: {project_id}."
            )

        return project

    def _require_chat_idle(self, chat_id: ChatId) -> None:
        """Reject a Chat mutation while generation or summarization owns it."""

        if self._is_chat_busy(chat_id):
            raise ProjectChatBusyError(
                "Chat cannot change Project relationship while busy: "
                f"{chat_id}."
            )

    def _require_project_chats_idle(self, project_id: ProjectId) -> None:
        """Reject a Project mutation while any linked Chat is busy."""

        self._require_chats_idle(self._load_project_chats(project_id))

    def _require_chats_idle(
        self,
        chats: tuple[ChatSession, ...],
    ) -> None:
        """Apply the injected busy policy to complete Chat aggregates."""

        for chat in chats:
            if self._is_chat_busy(chat.chat_id):
                raise ProjectChatBusyError(
                    "Project cannot change while a linked Chat is busy: "
                    f"{chat.chat_id}."
                )

    def _load_project_chats(
        self,
        project_id: ProjectId,
    ) -> tuple[ChatSession, ...]:
        """Load complete linked Chats before performing destructive work."""

        metadata_entries = self.list_project_chats(
            project_id,
            include_archived=True,
        )
        return tuple(
            self._chat_repository.get_chat(metadata.chat_id)
            for metadata in metadata_entries
        )

    def _next_timestamp(self, previous: datetime) -> datetime:
        """Return a valid aware timestamp that never moves state backward."""

        current = self._clock()
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise ValueError(
                "Project service clock must return an aware datetime."
            )
        return max(current, previous)

    def _detach_then_delete(
        self,
        project_id: ProjectId,
        linked_chats: tuple[ChatSession, ...],
    ) -> None:
        """Detach Chats and restore them if Project deletion fails."""

        detached_originals: list[ChatSession] = []

        try:
            for chat in linked_chats:
                self._chat_repository.save_chat(
                    replace(chat, project_id=None)
                )
                detached_originals.append(chat)

            self._project_repository.delete_project(project_id)
        except (ChatRepositoryError, ProjectRepositoryError) as error:
            self._restore_updated_chats(detached_originals)
            raise ProjectRelationshipError(
                f"Could not detach Chats and delete Project {project_id}."
            ) from error

    def _cascade_delete(
        self,
        project_id: ProjectId,
        linked_chats: tuple[ChatSession, ...],
    ) -> None:
        """Delete Chats first and restore them if Project deletion fails."""

        deleted_chats: list[ChatSession] = []

        try:
            for chat in linked_chats:
                self._chat_repository.delete_chat(chat.chat_id)
                deleted_chats.append(chat)

            self._project_repository.delete_project(project_id)
        except (ChatRepositoryError, ProjectRepositoryError) as error:
            self._restore_deleted_chats(deleted_chats)
            raise ProjectRelationshipError(
                f"Could not cascade-delete Project {project_id}."
            ) from error

    def _restore_updated_chats(
        self,
        original_chats: list[ChatSession],
    ) -> None:
        """Restore relationship updates in reverse operation order."""

        for chat in reversed(original_chats):
            try:
                self._chat_repository.save_chat(chat)
            except ChatRepositoryError as rollback_error:
                raise ProjectRelationshipRollbackError(
                    "Could not roll back detached Chat relationships."
                ) from rollback_error

    def _restore_deleted_chats(
        self,
        deleted_chats: list[ChatSession],
    ) -> None:
        """Reinsert deleted complete Chats with their original stable IDs."""

        for chat in deleted_chats:
            try:
                self._chat_repository.restore_chat(chat)
            except ChatRepositoryError as rollback_error:
                raise ProjectRelationshipRollbackError(
                    "Could not roll back cascade-deleted Chats."
                ) from rollback_error


def validate_deletion_policy(value: str) -> ProjectDeletionPolicy:
    """Narrow an external string to one supported deletion policy."""

    if value not in ("restrict", "detach", "cascade"):
        raise ValueError("Unknown Project deletion policy.")

    return cast(ProjectDeletionPolicy, value)
