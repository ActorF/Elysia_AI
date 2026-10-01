"""Define stable, sanitized failures for the knowledge lifecycle boundary."""


class KnowledgeLifecycleError(Exception):
    """Base class for all public knowledge-lifecycle failures."""


class KnowledgeLifecycleValidationError(KnowledgeLifecycleError):
    """Report malformed operation input or an invalid lifecycle snapshot."""


class KnowledgeLifecycleNotFoundError(KnowledgeLifecycleError):
    """Report an operation identifier absent from the durable journal."""


class KnowledgeLifecycleConflictError(KnowledgeLifecycleError):
    """Report a concurrent revision or incompatible operation transition."""


class KnowledgeExportCancelledError(KnowledgeLifecycleError):
    """Report cancellation before an original-byte export is published."""


class KnowledgeLifecycleStorageError(KnowledgeLifecycleError):
    """Report a sanitized failure to read or replace the lifecycle journal."""


class KnowledgeLifecycleDataCorruptionError(KnowledgeLifecycleStorageError):
    """Report stored lifecycle JSON that violates its closed schema."""


class KnowledgeLifecycleRecoveryError(KnowledgeLifecycleError):
    """Report an operation that requires safe reconciliation before progress."""
