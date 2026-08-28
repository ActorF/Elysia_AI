"""Errors raised by project repositories and cross-aggregate operations."""


class ProjectRepositoryError(Exception):
    """Base error raised by project persistence and coordination."""


class ProjectNotFoundError(ProjectRepositoryError):
    """Raised when a requested project does not exist."""


class ProjectAlreadyExistsError(ProjectRepositoryError):
    """Raised when a stable Project ID already exists."""


class ProjectStorageError(ProjectRepositoryError):
    """Raised when project data cannot be read, written, or replaced."""


class ProjectDataCorruptionError(ProjectRepositoryError):
    """Raised when stored project JSON does not match its schema."""


class ProjectRelationshipError(ProjectRepositoryError):
    """Base error for operations spanning Projects and Chats."""


class ProjectArchivedError(ProjectRelationshipError):
    """Raised when assigning a Chat to an archived project."""


class ProjectHasChatsError(ProjectRelationshipError):
    """Raised when restrict deletion finds linked Chats."""


class ChatProjectConflictError(ProjectRelationshipError):
    """Raised when a Chat has an incompatible Project relationship."""


class ProjectChatBusyError(ProjectRelationshipError):
    """Raised when a linked Chat is busy during a Project mutation."""


class ProjectRelationshipRollbackError(ProjectRelationshipError):
    """Raised when cross-repository rollback cannot restore old data."""
