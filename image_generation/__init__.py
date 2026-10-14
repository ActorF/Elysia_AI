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
from .composition import create_managed_image_generation_core
from .domain import ImageGenerationSpec, ImageIntent
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
from .managed_comfyui import ManagedComfyUiConfig
from .png import CanonicalPng, canonicalize_png
from .service import (
    GeneratedImageResult,
    ImageGenerationCore,
    ImageGenerationProgress,
    ImageGenerationStage,
)

__all__ = [
    "CanonicalPng",
    "ComfyOutputDescriptor",
    "ComfyPromptHandle",
    "ComfyUiConfig",
    "create_managed_image_generation_core",
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
    "ManagedComfyUiConfig",
    "PromptDialect",
    "canonicalize_png",
]
