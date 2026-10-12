"""Define stable, path-free failures for local image generation."""


class ImageGenerationError(Exception):
    """Base class for failures exposed by the image-generation boundary."""


class ImageGenerationValidationError(ImageGenerationError):
    """Report an invalid structured request or trusted configuration value."""


class ImageGenerationSafetyError(ImageGenerationValidationError):
    """Report a request outside the fictional-adult content boundary."""


class ImageGenerationProfileError(ImageGenerationValidationError):
    """Report an invalid or incompatible trusted model profile."""


class ImageGenerationUnavailableError(ImageGenerationError):
    """Report that the configured local generation runtime is unavailable."""


class ImageGenerationProtocolError(ImageGenerationError):
    """Report malformed or uncorrelated data from the local runtime."""


class ImageGenerationLimitError(ImageGenerationError):
    """Report bytes, pixels, time, or records beyond a fixed resource budget."""


class ImageGenerationCancelledError(ImageGenerationError):
    """Report cooperative cancellation before a generated image is published."""


class ImageGenerationFailedError(ImageGenerationError):
    """Report a sanitized local inference failure after admission succeeded."""


__all__ = [
    "ImageGenerationCancelledError",
    "ImageGenerationError",
    "ImageGenerationFailedError",
    "ImageGenerationLimitError",
    "ImageGenerationProfileError",
    "ImageGenerationProtocolError",
    "ImageGenerationSafetyError",
    "ImageGenerationUnavailableError",
    "ImageGenerationValidationError",
]
