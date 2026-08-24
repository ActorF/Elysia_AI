"""Errors raised by Stage 5 import, export, and recovery operations."""


class DataPortabilityError(Exception):
    """Base error for user-data portability operations."""


class ExportValidationError(DataPortabilityError):
    """Raised when an export destination is unsafe or unusable."""


class ImportValidationError(DataPortabilityError):
    """Raised when an import bundle is unsafe, corrupt, or unsupported."""


class ImportConflictError(DataPortabilityError):
    """Raised when imported stable IDs already exist in the target store."""


class ImportRollbackError(DataPortabilityError):
    """Raised when a failed import cannot be fully rolled back."""
