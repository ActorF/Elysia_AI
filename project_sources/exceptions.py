"""Define stable failures for Project Source authorization and catalogs."""


class ProjectSourceError(Exception):
    """Base class for Project Source application-boundary failures."""


class ProjectSourceValidationError(ProjectSourceError):
    """Report malformed Project Source domain or dependency data."""


class ProjectSourceAuthorizationError(ProjectSourceError):
    """Report a Chat that is not authorized for one Project corpus."""


class ProjectSourceConflictError(ProjectSourceError):
    """Report a concurrent authority or catalog revision change."""


class ProjectSourceStaleError(ProjectSourceError):
    """Report a catalog that no longer matches ownership or index policy."""


class ProjectSourceNotFoundError(ProjectSourceError):
    """Report an absent explicitly published Project Source catalog."""


class ProjectSourceStorageError(ProjectSourceError):
    """Report a sanitized Project Source persistence failure."""


class ProjectSourceDataCorruptionError(ProjectSourceStorageError):
    """Report stored Project Source JSON that violates its exact schema."""
