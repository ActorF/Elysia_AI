"""Compose the trusted image core with its private ComfyUI supervisor.

Keeping construction in one application-owned function prevents Desktop or
future agent code from accidentally wiring the raw loopback adapter directly
into the generation core.
"""

from __future__ import annotations

from .managed_comfyui import ManagedComfyUiConfig, ManagedComfyUiRuntime
from .profiles import ModelProfileCatalog
from .service import ImageGenerationCore


def create_managed_image_generation_core(
    profiles: ModelProfileCatalog,
    config: ManagedComfyUiConfig,
) -> ImageGenerationCore:
    """Create one core that exclusively owns a private ComfyUI supervisor."""

    if type(profiles) is not ModelProfileCatalog:
        raise TypeError("profiles must be ModelProfileCatalog.")
    if type(config) is not ManagedComfyUiConfig:
        raise TypeError("config must be ManagedComfyUiConfig.")
    runtime = ManagedComfyUiRuntime(config)
    return ImageGenerationCore(profiles, runtime)


__all__ = ["create_managed_image_generation_core"]
