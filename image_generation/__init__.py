"""Expose stable local image application, runtime, and Gallery surfaces.

The raw HTTP adapter remains available from ``image_generation.comfyui`` for
contract tests and the managed private-runtime implementation.  It is
intentionally not part of this stable package surface because it cannot
satisfy the core's managed-runtime ownership requirement by itself.  Gallery
consumers receive opaque identifiers and path-free records; its on-disk schema
and implementation limits remain private to ``image_generation.gallery``.
"""

from .application import (
    ImageGenerationApplicationBusyError,
    ImageGenerationApplicationClosedError,
    ImageGenerationApplicationError,
    ImageGenerationApplicationLimitError,
    ImageGenerationApplicationService,
    ImageGenerationApplicationValidationError,
    ImageGenerationCancelOutcome,
)
from .comfyui import (
    ComfyOutputDescriptor,
    ComfyPromptHandle,
    ComfyUiConfig,
)
from .composition import (
    create_managed_image_application_service,
    create_managed_image_generation_core,
)
from .domain import ImageGenerationSpec, ImageIntent, ImageLoraSelection, MAX_SELECTED_LORAS
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
    ModelLoraProfile,
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
from .gallery import (
    ImageGalleryError,
    ImageGalleryItem,
    ImageGalleryLimitError,
    ImageGalleryNotFoundError,
    ImageGalleryStorageError,
    ImageGalleryStore,
    ImageGalleryValidationError,
)

__all__ = [
    "CanonicalPng",
    "ComfyOutputDescriptor",
    "ComfyPromptHandle",
    "ComfyUiConfig",
    "create_managed_image_application_service",
    "create_managed_image_generation_core",
    "ImageGenerationApplicationBusyError",
    "ImageGenerationApplicationClosedError",
    "ImageGenerationApplicationError",
    "ImageGenerationApplicationLimitError",
    "ImageGenerationApplicationService",
    "ImageGenerationApplicationValidationError",
    "ImageGenerationCancelOutcome",
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
    "ImageGalleryError",
    "ImageGalleryItem",
    "ImageGalleryLimitError",
    "ImageGalleryNotFoundError",
    "ImageGalleryStorageError",
    "ImageGalleryStore",
    "ImageGalleryValidationError",
    "ImageIntent",
    "ImageLoraSelection",
    "MAX_SELECTED_LORAS",
    "ImageSizePreset",
    "ModelArchitecture",
    "ModelLoraProfile",
    "ModelProfile",
    "ModelProfileCatalog",
    "ManagedComfyUiConfig",
    "PromptDialect",
    "canonicalize_png",
]
