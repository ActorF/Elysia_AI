"""Compose the trusted image core with its private ComfyUI supervisor.

Keeping construction in one application-owned function prevents Desktop or
future agent code from accidentally wiring the raw loopback adapter directly
into the generation core.
"""

from __future__ import annotations

from pathlib import Path

from .application import ImageGenerationApplicationService
from .exceptions import ImageGenerationUnavailableError
from .gallery import ImageGalleryStore
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


def create_managed_image_application_service(
    profiles: ModelProfileCatalog,
    config: ManagedComfyUiConfig,
    data_root: Path,
) -> ImageGenerationApplicationService:
    """Open a ready managed image service with its application-owned Gallery.

    Every configured profile must pass file/hash readiness before this factory
    returns.  Readiness never launches ComfyUI or loads a checkpoint onto the
    GPU; per-job runtime ownership remains with the generation core.  The
    returned service exclusively owns the runtime and Gallery and must be
    closed during Backend shutdown.  Construction failures release provisional
    owners and expose only a detached, path-free unavailable error.
    """

    if type(profiles) is not ModelProfileCatalog:
        raise TypeError("profiles must be ModelProfileCatalog.")
    if type(config) is not ManagedComfyUiConfig:
        raise TypeError("config must be ManagedComfyUiConfig.")
    if not isinstance(data_root, Path):
        raise TypeError("data_root must be a Path.")
    if not data_root.is_absolute():
        raise ValueError("data_root must be absolute.")

    runtime: ManagedComfyUiRuntime | None = None
    gallery: ImageGalleryStore | None = None
    service: ImageGenerationApplicationService | None = None
    try:
        gallery_root = data_root / "workspace" / "gallery"
        # Runtime rollback recursively removes private job directories.  Keep
        # persistent Gallery storage disjoint in both directions, including
        # lexical traversal and existing filesystem aliases, before allocating
        # either owner.  Each owner separately rejects unsafe path components.
        private_root = config.private_state_root.resolve()
        persistent_root = gallery_root.resolve()
        if (
            private_root.is_relative_to(persistent_root)
            or persistent_root.is_relative_to(private_root)
        ):
            raise ImageGenerationUnavailableError(
                "Private image runtime storage must be separate from the Gallery."
            )
        runtime = ManagedComfyUiRuntime(config)
        for profile in profiles.profiles:
            runtime.ensure_ready(profile)
        gallery = ImageGalleryStore(gallery_root)
        core = ImageGenerationCore(profiles, runtime)
        service = ImageGenerationApplicationService(core, gallery)
    except Exception:
        # Re-raise outside the handler so an unexpected collaborator's error
        # cannot remain attached through __context__, even with ``from None``.
        pass
    finally:
        if service is None:
            # Ownership transfers only after the whole service exists.  Close
            # independent owners separately so one rollback failure cannot
            # strand the Gallery lease or the private runtime owner.
            if gallery is not None:
                try:
                    gallery.close()
                except Exception:
                    pass
            if runtime is not None:
                try:
                    runtime.close()
                except Exception:
                    pass
    if service is None:
        raise ImageGenerationUnavailableError(
            "The managed local image application is unavailable."
        )
    return service


__all__ = [
    "create_managed_image_application_service",
    "create_managed_image_generation_core",
]
