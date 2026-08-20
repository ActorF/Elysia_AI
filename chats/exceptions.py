"""Errors raised by chat persistence and repository operations."""


class ChatRepositoryError(Exception):
    """Base error raised by chat repository implementations."""


class ChatNotFoundError(ChatRepositoryError):
    """Raised when a requested chat does not exist."""


class ChatAlreadyExistsError(ChatRepositoryError):
    """Raised when a repository already contains the same stable chat ID."""


class ChatStorageError(ChatRepositoryError):
    """Raised when the repository cannot complete a filesystem operation."""


class ChatDataCorruptionError(ChatRepositoryError):
    """Raised when stored JSON cannot produce a valid chat domain object."""