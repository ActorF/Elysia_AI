"""Safe local attachment metadata and storage services."""

from .domain import (
    MAX_FILE_NAME_LENGTH,
    MAX_JSON_SAFE_INTEGER,
    MAX_MEDIA_TYPE_LENGTH,
    MAX_SOURCE_PATH_LENGTH,
    AttachmentItem,
    AttachmentScope,
    AttachmentScopeKind,
    AttachmentState,
    AttachmentStatus,
)
from .exceptions import (
    AttachmentConflictError,
    AttachmentError,
    AttachmentNotFoundError,
    AttachmentStorageError,
    AttachmentValidationError,
)
from .store import (
    ALLOWED_ATTACHMENT_EXTENSIONS,
    DEFAULT_MAX_FILE_COUNT,
    JsonAttachmentStore,
)

__all__ = [
    "ALLOWED_ATTACHMENT_EXTENSIONS",
    "DEFAULT_MAX_FILE_COUNT",
    "MAX_FILE_NAME_LENGTH",
    "MAX_JSON_SAFE_INTEGER",
    "MAX_MEDIA_TYPE_LENGTH",
    "MAX_SOURCE_PATH_LENGTH",
    "AttachmentConflictError",
    "AttachmentError",
    "AttachmentItem",
    "AttachmentNotFoundError",
    "AttachmentScope",
    "AttachmentScopeKind",
    "AttachmentState",
    "AttachmentStatus",
    "AttachmentStorageError",
    "AttachmentValidationError",
    "JsonAttachmentStore",
]
