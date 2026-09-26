"""Define stable, path-free failures for trusted document processing."""


class DocumentError(Exception):
    """Base class for failures exposed by document processing boundaries."""


class DocumentValidationError(DocumentError):
    """Report an invalid request, domain value, or adapter result."""


class DocumentNotFoundError(DocumentError):
    """Report that an ownership link has no readable file in its scope."""


class DocumentUnsupportedFormatError(DocumentError):
    """Report a file type for which no trusted loader is registered."""


class DocumentUnsupportedFeatureError(DocumentError):
    """Report a required document capability this consumer cannot honor."""


class DocumentEmptyError(DocumentError):
    """Report a valid input that contains no loadable document content."""


class DocumentEncryptedError(DocumentError):
    """Report a document that requires credentials or decryption."""


class DocumentCorruptError(DocumentError):
    """Report malformed or internally inconsistent document bytes."""


class DocumentLimitError(DocumentError):
    """Base class for failures caused by an explicit resource budget."""


class DocumentTooLargeError(DocumentLimitError):
    """Report source bytes that exceed the admitted input-size budget."""


class DocumentContentLimitError(DocumentLimitError):
    """Report parsed structure or expanded content that exceeds its budget."""


class DocumentReadError(DocumentError):
    """Report failure to obtain the exact verified source bytes safely."""


class DocumentLoadFailedError(DocumentError):
    """Report an unexpected failure inside an otherwise selected loader."""


class DocumentProcessingFailedError(DocumentError):
    """Report an unexpected failure inside cleaning, chunking, or orchestration."""
