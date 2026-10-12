"""Expose the stable local image-generation domain and trusted core surface.

The raw HTTP adapter remains available from ``image_generation.comfyui`` for
contract tests and a future private-runtime supervisor.  It is intentionally
not part of this stable package surface because it cannot satisfy the core's
managed-runtime ownership requirement by itself.
"""

from .comfyui import (
    ComfyOutputDescriptor,
    ComfyPromptHandle,
    ComfyUiConfig,
)
from .domain import ContentMode, ImageGenerationSpec, ImageIntent
from .exceptions import (
    ImageGenerationCancelledError,
    ImageGenerationError,
    ImageGenerationFailedError,
    ImageGenerationLimitError,
    ImageGenerationProfileError,
    ImageGenerationProtocolError,
    ImageGenerationSafetyError,
    ImageGenerationUnavailableError,
    ImageGenerationValidationError,
)
from .profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelProfile,
    ModelProfileCatalog,
    PromptDialect,
)
from .png import CanonicalPng, canonicalize_png
from .service import (
    GeneratedImageResult,
    ImageGenerationCore,
    ImageGenerationProgress,
    ImageGenerationStage,
)

__all__ = [
    "ContentMode",
    "CanonicalPng",
    "ComfyOutputDescriptor",
    "ComfyPromptHandle",
    "ComfyUiConfig",
    "ImageGenerationCancelledError",
    "ImageGenerationError",
    "ImageGenerationFailedError",
    "GeneratedImageResult",
    "ImageGenerationCore",
    "ImageGenerationProgress",
    "ImageGenerationStage",
    "ImageGenerationLimitError",
    "ImageGenerationProfileError",
    "ImageGenerationProtocolError",
    "ImageGenerationSafetyError",
    "ImageGenerationSpec",
    "ImageGenerationUnavailableError",
    "ImageGenerationValidationError",
    "ImageIntent",
    "ImageSizePreset",
    "ModelArchitecture",
    "ModelProfile",
    "ModelProfileCatalog",
    "PromptDialect",
    "canonicalize_png",
]
