"""Public interface for Elysia Project domains and repositories."""

from chats.domain import ProjectId

from .domain import (
    MAX_PROJECT_INSTRUCTIONS_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
    MAX_WORKSPACE_PATH_LENGTH,
    PROJECT_SCHEMA_VERSION,
    Project,
    ProjectSettings,
    WorkspaceBinding,
    create_project,
    generate_project_id,
)
from .exceptions import (
    ChatProjectConflictError,
    ProjectAlreadyExistsError,
    ProjectArchivedError,
    ProjectChatBusyError,
    ProjectDataCorruptionError,
    ProjectHasChatsError,
    ProjectNotFoundError,
    ProjectRelationshipError,
    ProjectRelationshipRollbackError,
    ProjectRepositoryError,
    ProjectStorageError,
)
from .repository import JsonProjectRepository, ProjectRepository
from .serialization import PROJECT_STORE_SCHEMA_VERSION
from .service import (
    ProjectChatService,
    ProjectDeletionPolicy,
    validate_deletion_policy,
)

__all__ = [
    "PROJECT_SCHEMA_VERSION",
    "PROJECT_STORE_SCHEMA_VERSION",
    "MAX_PROJECT_INSTRUCTIONS_LENGTH",
    "MAX_PROJECT_NAME_LENGTH",
    "MAX_WORKSPACE_PATH_LENGTH",
    "ChatProjectConflictError",
    "JsonProjectRepository",
    "Project",
    "ProjectAlreadyExistsError",
    "ProjectArchivedError",
    "ProjectChatBusyError",
    "ProjectChatService",
    "ProjectDataCorruptionError",
    "ProjectDeletionPolicy",
    "ProjectHasChatsError",
    "ProjectId",
    "ProjectNotFoundError",
    "ProjectRelationshipError",
    "ProjectRelationshipRollbackError",
    "ProjectRepository",
    "ProjectRepositoryError",
    "ProjectSettings",
    "ProjectStorageError",
    "WorkspaceBinding",
    "create_project",
    "generate_project_id",
    "validate_deletion_policy",
]
