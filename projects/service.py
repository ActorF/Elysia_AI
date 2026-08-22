"""Coordinate Project and Chat relationships without exposing paths."""

from dataclasses import replace
from typing import Literal, cast

from chats import (
    ChatId,
    ChatRepository,
    ChatRepositoryError,
    ChatSession,
    ChatSessionMeta,
    ProjectId,
)

from .domain import Project
from .exceptions import (
    ChatProjectConflictError,
    ProjectArchivedError,
    ProjectHasChatsError,
    ProjectRelationshipError,
    ProjectRelationshipRollbackError,
    ProjectRepositoryError,
)
from .repository import ProjectRepository

ProjectDeletionPolicy = Literal["restrict", "detach", "cascade"]


class ProjectChatService:
    """Maintain one-to-many Project relationships across repositories."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        chat_repository: ChatRepository,
    ) -> None:
        """Receive repositories without learning their storage paths."""

        self._project_repository = project_repository
        self._chat_repository = chat_repository

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

    def add_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Add an unassigned Chat to one active Project."""

        self._require_active_project(project_id)
        chat = self._chat_repository.get_chat(chat_id)

        if chat.project_id == project_id:
            return chat

        if chat.project_id is not None:
            raise ChatProjectConflictError(
                f"Chat {chat_id} already belongs to "
                f"Project {chat.project_id}; use transfer_chat."
            )

        updated_chat = replace(chat, project_id=project_id)
        self._chat_repository.save_chat(updated_chat)
        return updated_chat

    def remove_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Move one Chat from the named Project to no Project."""

        self._project_repository.get_project(project_id)
        chat = self._chat_repository.get_chat(chat_id)

        if chat.project_id != project_id:
            raise ChatProjectConflictError(
                f"Chat {chat_id} does not belong to Project {project_id}."
            )

        updated_chat = replace(chat, project_id=None)
        self._chat_repository.save_chat(updated_chat)
        return updated_chat

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

        updated_chat = replace(
            chat,
            project_id=destination_project_id,
        )
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
            self._detach_then_delete(project_id, linked_chats)
            return

        self._cascade_delete(project_id, linked_chats)

    def _require_active_project(self, project_id: ProjectId) -> Project:
        """Return one Project only when it can accept Chats."""

        project = self._project_repository.get_project(project_id)
        if project.is_archived:
            raise ProjectArchivedError(
                f"Archived Project cannot accept Chats: {project_id}."
            )

        return project

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
