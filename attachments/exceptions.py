"""Stable, path-free errors raised by attachment storage operations."""


class AttachmentError(Exception):
    """Base error for the local attachment surface."""


class AttachmentValidationError(AttachmentError):
    """Raised when an attachment request is malformed or unsafe."""


class AttachmentNotFoundError(AttachmentError):
    """Raised when a draft attachment does not exist in its scope."""


class AttachmentConflictError(AttachmentError):
    """Raised when an attachment cannot change in its current state."""


class AttachmentStorageError(AttachmentError):
    """Raised when local attachment data cannot be stored safely."""
