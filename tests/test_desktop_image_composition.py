"""Verify ready image-service composition, storage separation, and rollback.

Construction doubles record ownership transfers without launching ComfyUI,
loading checkpoint weights, or acquiring native resources.  Filesystem path
normalization remains real so rejected overlap cannot reach either owner.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import image_generation.composition as composition
from image_generation.application import ImageGenerationApplicationService
from image_generation.exceptions import ImageGenerationUnavailableError
from image_generation.managed_comfyui import ManagedComfyUiConfig
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelProfile,
    ModelProfileCatalog,
    PromptDialect,
)


class _Owner:
    """Record release attempts and optionally simulate a private close error."""

    def __init__(self, harness: _Harness, name: str) -> None:
        """Bind one provisional owner to the shared construction event log."""

        self._harness = harness
        self._name = name

    def close(self) -> None:
        """Record every attempt before raising any configured cleanup failure."""

        self._harness.events.append(f"close:{self._name}")
        if self._name in self._harness.close_failures:
            raise OSError("D:/private/image-state/secret.png")


class _Runtime(_Owner):
    """Allow only file-readiness checks during application composition."""

    def ensure_ready(self, profile: ModelProfile) -> None:
        """Record the exact reviewed profile and fail at a configured check."""

        event = f"ready:{profile.profile_id}"
        self._harness._event(event)
        self._harness.ready_profiles.append(profile)


class _Application:
    """Represent ownership transfer after the final constructor succeeds."""

    def __init__(self, harness: _Harness) -> None:
        """Retain collaborators as a real service would after construction."""

        self._harness = harness

    def close(self) -> None:
        """Release the runtime and Gallery through the completed service."""

        self._harness.runtime.close()
        self._harness.gallery.close()


class _Harness:
    """Replace construction seams while preserving trusted factory inputs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        """Create two reviewed profiles and capture every factory boundary."""

        first = ModelProfile(
            profile_id="first",
            display_name="First reviewed model",
            architecture=ModelArchitecture.SDXL,
            prompt_dialect=PromptDialect.SDXL_NATURAL,
            checkpoint_name="reviewed.safetensors",
            checkpoint_sha256="a" * 64,
            checkpoint_source="Reviewed local fixture",
            checkpoint_license="Fixture only",
            size_presets=(ImageSizePreset("square", 512, 512),),
            default_size_preset_id="square",
        )
        self.profiles = ModelProfileCatalog((
            first,
            replace(first, profile_id="second", display_name="Second reviewed model"),
        ))
        self.config = ManagedComfyUiConfig(
            runtime_root=root / "trusted-runtime",
            python_executable=root / "python" / "python.exe",
            models_root=root / "trusted-models",
            private_state_root=root / "private-runtime",
            python_sha256="b" * 64,
            comfyui_main_sha256="c" * 64,
        )
        self.data_root = root / "application-data"
        self.events: list[str] = []
        self.ready_profiles: list[ModelProfile] = []
        self.close_failures: set[str] = set()
        self.failure_at: str | None = None
        self.runtime = _Runtime(self, "runtime")
        self.gallery = _Owner(self, "gallery")
        self.core = object()
        self.application = _Application(self)
        monkeypatch.setattr(composition, "ManagedComfyUiRuntime", self._runtime)
        monkeypatch.setattr(composition, "ImageGalleryStore", self._gallery)
        monkeypatch.setattr(composition, "ImageGenerationCore", self._core)
        monkeypatch.setattr(
            composition, "ImageGenerationApplicationService", self._application,
        )

    def _event(self, event: str) -> None:
        """Expose a nested private error only at the configured boundary."""

        self.events.append(event)
        if self.failure_at == event:
            try:
                raise ValueError("D:/private/checkpoint.safetensors")
            except ValueError as error:
                raise RuntimeError("D:/private/prompt.json") from error

    def _runtime(self, config: ManagedComfyUiConfig) -> _Runtime:
        """Require the caller's trusted configuration without creating a child."""

        assert config is self.config
        self._event("runtime")
        return self.runtime

    def _gallery(self, root: Path) -> _Owner:
        """Require the fixed application-owned Gallery location."""

        assert root == self.data_root / "workspace" / "gallery"
        self._event("gallery")
        return self.gallery

    def _core(self, profiles: ModelProfileCatalog, runtime: _Runtime) -> object:
        """Reject substitution of an unreviewed catalog or unmanaged adapter."""

        assert profiles is self.profiles
        assert runtime is self.runtime
        self._event("core")
        return self.core

    def _application(self, core: object, gallery: _Owner) -> _Application:
        """Record the final service constructor and require the owned resources."""

        assert core is self.core
        assert gallery is self.gallery
        self._event("application")
        return self.application

    def _compose(self) -> ImageGenerationApplicationService:
        """Invoke the public factory with the fixture's trusted dependencies."""

        return composition.create_managed_image_application_service(
            self.profiles, self.config, self.data_root,
        )


def test_factory_preflights_all_profiles_before_publishing_ownership(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Publish a service only after every reviewed profile and owner is ready."""

    harness = _Harness(monkeypatch, tmp_path)

    service = harness._compose()

    assert service is harness.application
    assert tuple(harness.ready_profiles) == harness.profiles.profiles
    assert harness.events == [
        "runtime", "ready:first", "ready:second", "gallery", "core", "application",
    ]
    service.close()
    assert harness.events[-2:] == ["close:runtime", "close:gallery"]


@pytest.mark.parametrize(
    ("failure_at", "released"),
    [
        ("runtime", []),
        ("ready:first", ["close:runtime"]),
        ("ready:second", ["close:runtime"]),
        ("gallery", ["close:runtime"]),
        ("core", ["close:gallery", "close:runtime"]),
        ("application", ["close:gallery", "close:runtime"]),
    ],
)
def test_failed_factory_releases_all_constructed_owners_and_detaches_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_at: str,
    released: list[str],
) -> None:
    """Never retain a private constructor exception or leak a completed owner."""

    harness = _Harness(monkeypatch, tmp_path)
    harness.failure_at = failure_at

    with pytest.raises(ImageGenerationUnavailableError) as captured:
        harness._compose()

    assert [event for event in harness.events if event.startswith("close:")] == released
    assert str(captured.value) == "The managed local image application is unavailable."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_gallery_rollback_error_still_releases_runtime_and_stays_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed Gallery close must not prevent releasing the independent runtime."""

    harness = _Harness(monkeypatch, tmp_path)
    harness.failure_at = "application"
    harness.close_failures = {"gallery", "runtime"}

    with pytest.raises(ImageGenerationUnavailableError) as captured:
        harness._compose()

    assert harness.events[-2:] == ["close:gallery", "close:runtime"]
    assert "private" not in str(captured.value)
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "private_location",
    ["data-root", "workspace", "gallery", "gallery-child", "traversal"],
)
def test_factory_rejects_runtime_gallery_overlap_before_allocating_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_location: str,
) -> None:
    """Protect persistent Gallery files from every direction of runtime overlap."""

    harness = _Harness(monkeypatch, tmp_path)
    workspace = harness.data_root / "workspace"
    gallery = workspace / "gallery"
    locations = {
        "data-root": harness.data_root,
        "workspace": workspace,
        "gallery": gallery,
        "gallery-child": gallery / "private",
        "traversal": workspace / "other" / ".." / "gallery",
    }
    harness.config = replace(
        harness.config, private_state_root=locations[private_location],
    )

    with pytest.raises(ImageGenerationUnavailableError) as captured:
        harness._compose()

    assert harness.events == []
    assert captured.value.__context__ is None
    assert not harness.data_root.exists()


def test_factory_allows_separate_runtime_under_application_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow a private runtime sibling while retaining the fixed Gallery root."""

    harness = _Harness(monkeypatch, tmp_path)
    harness.config = replace(
        harness.config,
        private_state_root=harness.data_root / "workspace" / "image-runtime",
    )

    assert harness._compose() is harness.application


def test_factory_requires_an_absolute_application_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Avoid relocating persistent Gallery storage when the process cwd changes."""

    harness = _Harness(monkeypatch, tmp_path)
    harness.data_root = Path("relative-data")

    with pytest.raises(ValueError, match="data_root must be absolute"):
        harness._compose()

    assert harness.events == []


@pytest.mark.parametrize("invalid_input", ["profiles", "config", "data-root"])
def test_factory_rejects_untrusted_input_types_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_input: str,
) -> None:
    """Retain the exact trusted catalog/config boundary and Path-only data root."""

    harness = _Harness(monkeypatch, tmp_path)
    profiles = harness.profiles
    config = harness.config
    data_root = harness.data_root
    if invalid_input == "profiles":
        profiles = cast(ModelProfileCatalog, object())
    elif invalid_input == "config":
        config = cast(ManagedComfyUiConfig, object())
    else:
        data_root = cast(Path, str(data_root))

    with pytest.raises(TypeError):
        composition.create_managed_image_application_service(profiles, config, data_root)

    assert harness.events == []
