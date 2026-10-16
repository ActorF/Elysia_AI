"""Verify image GPU grant lifetime and fixed desktop broker startup ownership.

Controlled application and native-process seams exercise cancellation and
cleanup proofs without starting ComfyUI, loading weights, or using GPU.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Event, RLock, Thread
from types import SimpleNamespace
from typing import Any, cast

import pytest

import desktop_backend
import desktop_images
from desktop_images import DesktopImageCoordinator, _ImageTask
from desktop_protocol import AudioChannelError, ImageChannelError, ImageChannelWriter
from gpu_admission import GpuAdmissionCancelledError, GpuAdmissionError
from image_generation import (
    ImageGalleryItem, ImageGalleryStorageError, ImageGenerationApplicationService,
    ImageGenerationCancelOutcome, ImageGenerationCancelledError, ImageGenerationProgress,
    ImageGenerationStage, ImageGenerationUnavailableError,
)
from image_generation.managed_comfyui import ManagedComfyUiConfig, ManagedComfyUiRuntime
from image_generation.service import ImageGenerationCore


def _params() -> dict[str, Any]:
    return {
        "profileId": "reviewed", "sizePresetId": "square", "appearance": "adult fictional woman",
        "outfit": "dress", "pose": "standing", "scene": "garden", "style": "painting",
        "camera": "portrait", "lighting": "daylight", "negativePrompt": "blur", "seed": 1,
        "originalFictionalConfirmed": True,
    }


class _Grant:
    """Record whether a terminal operation released or quarantined its grant."""

    def __init__(self, events: list[str]) -> None:
        """Create an initially healthy physical execution grant."""

        self.events = events
        self.cancelled = False
        self.poisoned = False

    def poison(self) -> None:
        """Retain admission for Main's backend-exit proof instead of releasing."""

        self.poisoned = True
        self.events.append("poison")


class _Service:
    """Separate native finalization and Gallery commit from request dispatch."""

    def __init__(self, events: list[str], *, clean: bool = True, storage_error: bool = False) -> None:
        """Configure authoritative cleanup proof and an optional persistence failure."""

        self.events = events
        self.gpu_cleanup_complete = clean
        self.storage_error = storage_error
        self.calls = 0

    def generate(self, operation: str, spec: object, *, on_progress: Callable[[ImageGenerationProgress], None]) -> ImageGalleryItem:
        """Finalize the native owner before attempting the Gallery commit."""

        self.calls += 1
        self.events.extend(["native", "native-finalized", "gallery-commit"])
        if self.storage_error:
            raise ImageGalleryStorageError("private storage path")
        return ImageGalleryItem(
            artifact_id="gallery_" + "a" * 32, created_at=datetime.now(timezone.utc), width=1024,
            height=1024, byte_size=100, sha256="b" * 64, media_type="image/png", profile_id="reviewed", seed=1,
        )

    def cancel(self, operation: str) -> ImageGenerationCancelOutcome:
        """Report no application operation while the coordinator waits for GPU."""

        return ImageGenerationCancelOutcome.NOT_ACTIVE


def _coordinator(service: _Service) -> tuple[DesktopImageCoordinator, _ImageTask, list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    coordinator = DesktopImageCoordinator(cast(Any, service), ImageChannelWriter(BytesIO()), cast(Any, output.append))
    task = _ImageTask("generate", ImageGenerationApplicationService.create_operation_id())
    coordinator._generation = task
    return coordinator, task, output


def _install_grant(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    @contextmanager
    def _lease(owner: str, *, cancel_requested: Callable[[], bool]) -> Iterator[_Grant]:
        assert owner == "image-generation"
        events.append("grant")
        grant = _Grant(events)
        try:
            yield grant
        finally:
            events.append("retained" if grant.poisoned else "release")

    monkeypatch.setattr(desktop_images, "using_desktop_gpu", lambda: True)
    monkeypatch.setattr(desktop_images, "gpu_lease", _lease)


def test_image_generation_retains_gpu_through_native_cleanup_and_gallery_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the complete application operation can release global image admission."""

    events: list[str] = []
    _install_grant(monkeypatch, events)
    coordinator, task, output = _coordinator(_Service(events))
    coordinator._generate(task, coordinator._spec(_params()))
    assert events == ["grant", "native", "native-finalized", "gallery-commit", "release"]
    assert output[-1]["ok"] is True


def test_image_storage_failure_releases_proven_native_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary Gallery failure cannot be mistaken for a surviving GPU process."""

    events: list[str] = []
    _install_grant(monkeypatch, events)
    coordinator, task, output = _coordinator(_Service(events, storage_error=True))
    coordinator._generate(task, coordinator._spec(_params()))
    assert events[-1] == "release"
    assert output[-1]["error"]["code"] == "image.gallery.storage_failed"


def test_unproven_image_cleanup_keeps_grant_poisoned_until_backend_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal application result alone never authorizes another heavy task."""

    events: list[str] = []
    _install_grant(monkeypatch, events)
    coordinator, task, output = _coordinator(_Service(events, clean=False))
    coordinator._generate(task, coordinator._spec(_params()))
    assert events[-2:] == ["poison", "retained"]
    assert "release" not in events
    assert output[-1]["error"]["code"] == "image.unavailable"


def test_image_cancel_during_gpu_wait_starts_no_runtime_and_returns_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancellation before application admission persists until the broker wait exits."""

    waiting = Event()
    events: list[str] = []

    @contextmanager
    def _waiting(owner: str, *, cancel_requested: Callable[[], bool]) -> Iterator[object]:
        waiting.set()
        while not cancel_requested():
            Event().wait(0.005)
        raise GpuAdmissionCancelledError("cancelled")
        yield

    monkeypatch.setattr(desktop_images, "using_desktop_gpu", lambda: True)
    monkeypatch.setattr(desktop_images, "gpu_lease", _waiting)
    service = _Service(events)
    coordinator, task, output = _coordinator(service)
    thread = Thread(target=coordinator._generate, args=(task, coordinator._spec(_params())))
    thread.start()
    assert waiting.wait(1.0)
    assert coordinator.cancel(task.request_id)
    thread.join(1.0)
    assert not thread.is_alive()
    assert service.calls == 0
    assert output[-1]["error"]["code"] == "image.cancelled"


def test_missing_cleanup_proof_is_not_accepted_as_truthy_metadata() -> None:
    """Both forwarding layers require literal native proof and hide failed accessors."""

    core = object.__new__(ImageGenerationCore)
    service = object.__new__(ImageGenerationApplicationService)
    service._core = core
    for value in [False, 1, "true", None]:
        core._client = cast(Any, SimpleNamespace(gpu_cleanup_complete=value))
        assert service.gpu_cleanup_complete is False
    core._client = cast(Any, SimpleNamespace(gpu_cleanup_complete=True))
    assert service.gpu_cleanup_complete is True


def test_managed_image_runtime_proof_distinguishes_idle_and_unknown_rollback(tmp_path: Path) -> None:
    """A poisoned startup may have no active handle while still retaining native owners."""

    config = ManagedComfyUiConfig(
        runtime_root=tmp_path / "runtime", python_executable=tmp_path / "python.exe",
        models_root=tmp_path / "models", private_state_root=tmp_path / "private",
        python_sha256="a" * 64, comfyui_main_sha256="b" * 64,
    )
    runtime = ManagedComfyUiRuntime(config)
    assert runtime.gpu_cleanup_complete is True
    with runtime._lock:
        runtime._state = "poisoned"
    assert runtime.gpu_cleanup_complete is False
    with pytest.raises(ImageGenerationUnavailableError):
        runtime.close()
    assert runtime.gpu_cleanup_complete is False


def _bootstrap(monkeypatch: pytest.MonkeyPatch, events: list[str], *, promised: bool = True,
               failed: bool = False, run_error: bool = False) -> None:
    if promised:
        monkeypatch.setenv("ELYSIA_GPU_BROKER", "1")
    else:
        monkeypatch.delenv("ELYSIA_GPU_BROKER", raising=False)
    monkeypatch.setattr(desktop_backend, "_configure_protocol_streams", lambda *_: None)

    def _gpu() -> Any:
        events.append("fd5")
        if failed:
            raise GpuAdmissionError("private native handle failure")
        return SimpleNamespace(close=lambda: events.append("gpu-close"))

    def _install(client: object) -> None:
        assert client is not None
        events.append("install")

    def _audio() -> Any:
        events.append("fd3")
        raise AudioChannelError("unavailable")

    def _image() -> Any:
        events.append("fd4")
        raise ImageChannelError("unavailable")

    def _backend(**kwargs: object) -> Any:
        events.append("backend")

        def _run() -> None:
            events.append("run")
            if run_error:
                raise RuntimeError("controlled backend failure")

        return SimpleNamespace(run=_run)

    monkeypatch.setattr(desktop_backend, "GpuAdmissionClient", SimpleNamespace(from_inherited_fd5=_gpu))
    monkeypatch.setattr(desktop_backend, "configure_gpu_client", _install)
    monkeypatch.setattr(desktop_backend.AudioChannelWriter, "from_inherited_fd3", _audio)
    monkeypatch.setattr(desktop_backend.ImageChannelWriter, "from_inherited_fd4", _image)
    monkeypatch.setattr(desktop_backend, "DesktopBackend", _backend)


def test_backend_installs_gpu_client_before_services_and_closes_it_after_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model constructors cannot run before fd5 supplies Desktop GPU authority."""

    events: list[str] = []
    _bootstrap(monkeypatch, events)
    desktop_backend.main()
    assert events == ["fd5", "install", "fd3", "fd4", "backend", "run", "gpu-close"]


def test_backend_refuses_promised_but_missing_gpu_channel_before_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """An Electron launch never falls back to unconstrained GPU work on fd5 failure."""

    events: list[str] = []
    _bootstrap(monkeypatch, events, failed=True)
    desktop_backend.main()
    assert events == ["fd5"]


def test_non_electron_backend_preserves_library_gpu_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The explicit Main launch flag keeps ordinary console and test startup compatible."""

    events: list[str] = []
    _bootstrap(monkeypatch, events, promised=False)
    desktop_backend.main()
    assert events == ["fd3", "fd4", "backend", "run"]


def test_backend_exception_still_closes_gpu_client_without_synthetic_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure cleanup closes ownership transport without claiming physical job completion."""

    events: list[str] = []
    _bootstrap(monkeypatch, events, run_error=True)
    with pytest.raises(RuntimeError, match="controlled backend failure"):
        desktop_backend.main()
    assert events[-1] == "gpu-close"
    assert events.count("install") == 1
