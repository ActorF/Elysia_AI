"""Verify authenticated image routing, cancellation, and private preview ownership.

The bridge tests exercise the real application service and durable Gallery with
a controlled inference boundary. No ComfyUI installation or GPU is required.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
from pathlib import Path
import struct
from threading import Condition, Event, Thread
from time import monotonic
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4
import zlib

import pytest

import desktop_images
from config.desktop_settings import (
    DesktopSettingsRepository,
    EditableDesktopSettings,
)
from core import Brain
from desktop_backend import DesktopBackend
from desktop_protocol import (
    ImageChannelReader,
    ImageChannelWriter,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    parse_server_message,
)
from image_generation.application import ImageGenerationApplicationService
from image_generation.domain import ImageGenerationSpec, ImageIntent
from image_generation.exceptions import ImageGenerationCancelledError
from image_generation.gallery import (
    ImageGalleryItem,
    ImageGalleryStorageError,
    ImageGalleryStore,
)
from image_generation.png import PNG_SIGNATURE, canonicalize_png
from image_generation.service import (
    GeneratedImageResult,
    ImageGenerationCore,
    ImageGenerationProgress,
    ImageGenerationStage,
)
from voice import create_voice_settings_service


_TOKEN = "image-backend-session-token-00000001"
_ARTIFACT_ID = "gallery_" + "a" * 32
_METHODS = (
    "image.generate",
    "image.gallery.list",
    "image.gallery.get",
    "image.gallery.delete",
    "image.gallery.open",
)
_STAGES = {
    ImageGenerationStage.VALIDATING: 5,
    ImageGenerationStage.PROBING: 15,
    ImageGenerationStage.SUBMITTING: 25,
    ImageGenerationStage.SAMPLING: 35,
    ImageGenerationStage.VERIFYING: 90,
    ImageGenerationStage.COMPLETED: 100,
}


def _params(**changes: object) -> dict[str, object]:
    """Return the complete path-free generation DTO accepted by the protocol."""

    params: dict[str, object] = {
        "profileId": "anime-local",
        "sizePresetId": "square",
        "appearance": "adult original woman with violet hair",
        "outfit": "white dress",
        "pose": "standing",
        "scene": "quiet garden",
        "style": "watercolor",
        "camera": "portrait",
        "lighting": "soft daylight",
        "negativePrompt": "low contrast",
        "seed": 42,
        "originalFictionalConfirmed": True,
    }
    params.update(changes)
    return params


def _method_params(method: str) -> dict[str, object]:
    """Provide a valid minimal request for each image method."""

    if method == "image.generate":
        return _params()
    if method == "image.gallery.list":
        return {"limit": 10, "offset": 0}
    return {"artifactId": _ARTIFACT_ID}


def _request(request_id: str, method: str, params: dict[str, object]) -> str:
    """Serialize raw requests so invalid DTO cases reach the Backend parser."""

    return json.dumps({
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "type": "request",
        "id": request_id,
        "method": method,
        "params": params,
    })


def _handshake(backend: DesktopBackend) -> None:
    """Authenticate the bridge without invoking Brain initialization."""

    assert backend._handle_line(_request("handshake", "handshake", {
        "client": {"name": "elysia-electron", "version": "0.1.0"},
        "sessionToken": _TOKEN,
    }))


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk with a valid CRC for real Gallery verification."""

    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _result(sequence: int = 0) -> GeneratedImageResult:
    """Create distinct canonical PNGs and timestamps for pagination checks."""

    png = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress((b"\0" + b"\x11\x22\x33" * 2) * 2))
        + _chunk(b"IEND", b"")
    )
    return GeneratedImageResult(
        job_id=str(uuid4()),
        image=canonicalize_png(png, expected_width=2, expected_height=2),
        profile_id="anime-local",
        profile_digest="a" * 64,
        workflow_id="txt2img-v1",
        workflow_version=1,
        workflow_digest="b" * 64,
        seed=42,
        created_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
        + timedelta(seconds=sequence),
    )


class _Output(StringIO):
    """Expose condition-based waits for complete NDJSON responses."""

    def __init__(self) -> None:
        """Initialize a stream whose reads synchronize with worker writes."""

        super().__init__()
        self._condition = Condition()

    def write(self, text: str) -> int:
        """Publish one text segment and notify observers of complete frames."""

        with self._condition:
            count = super().write(text)
            self._condition.notify_all()
            return count

    def messages(self) -> list[dict[str, Any]]:
        """Read complete frames and validate them against the shared contract."""

        with self._condition:
            messages = [json.loads(line) for line in self.getvalue().splitlines()]
        for message in messages:
            parse_server_message(message)
        return messages

    def response(self, request_id: str) -> dict[str, Any]:
        """Wait at most two seconds for exactly one terminal request response."""

        deadline = monotonic() + 2.0
        with self._condition:
            while True:
                responses = [
                    item for item in self.messages()
                    if item.get("type") == "response"
                    and item.get("id") == request_id
                ]
                if responses:
                    assert len(responses) == 1
                    return responses[0]
                remaining = deadline - monotonic()
                assert remaining > 0, f"No terminal response for {request_id}"
                self._condition.wait(remaining)


class _BinaryOutput(BytesIO):
    """Retain bytes after close and optionally block a native-write surrogate."""

    def __init__(self, output: _Output, *, blocked: bool = False) -> None:
        """Initialize writer gates and metadata-before-bytes observations."""

        super().__init__()
        self.output = output
        self.entered = Event()
        self.release = Event()
        if not blocked:
            self.release.set()
        self.flushed = Event()
        self.closed_event = Event()
        self.captured = b""
        self.responses_before_write: list[str] = []

    def write(self, data: Any) -> int:
        """Record already-published metadata before allowing binary I/O."""

        if not self.entered.is_set():
            self.responses_before_write = [
                item["id"] for item in self.output.messages()
                if item.get("type") == "response" and item.get("ok") is True
            ]
            self.entered.set()
        assert self.release.wait(5.0)
        return super().write(data)

    def flush(self) -> None:
        """Mark the complete header and payload available to a test reader."""

        super().flush()
        self.flushed.set()

    def close(self) -> None:
        """Preserve bytes before the writer releases its owned stream."""

        if not self.closed:
            self.captured = self.getvalue()
        super().close()
        self.closed_event.set()


class _Core:
    """Model cancellable inference without touching ComfyUI or a GPU."""

    def __init__(self) -> None:
        """Initialize result, failure injection, cancellation, and close gates."""

        self.result = _result()
        self.specs: list[ImageGenerationSpec] = []
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.closed = Event()
        self.close_entered = Event()
        self.close_release = Event()
        self.close_release.set()
        self.error: Exception | None = None
        self.ignore_cancellation = False

    def generate(
        self,
        spec: ImageGenerationSpec,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[ImageGenerationProgress], None] | None = None,
    ) -> GeneratedImageResult:
        """Emit trusted stages and honor cancellation unless a test blocks it."""

        self.specs.append(spec)
        self.entered.set()
        for stage, percent in _STAGES.items():
            if on_progress is not None:
                on_progress(ImageGenerationProgress(stage, percent))
            if stage is ImageGenerationStage.SAMPLING:
                deadline = monotonic() + 5.0
                while not self.release.wait(0.005):
                    if not self.ignore_cancellation and cancel_requested is not None:
                        if cancel_requested():
                            raise ImageGenerationCancelledError("cancelled")
                    assert monotonic() < deadline
            if not self.ignore_cancellation and cancel_requested is not None:
                if cancel_requested():
                    raise ImageGenerationCancelledError("cancelled")
        if self.error is not None:
            raise self.error
        return self.result

    def close(self) -> None:
        """Record independently scheduled runtime release and optional blockage."""

        self.close_entered.set()
        assert self.close_release.wait(5.0)
        self.closed.set()


class _Gallery(ImageGalleryStore):
    """Keep durable Gallery semantics while allowing a paused commit boundary."""

    def __init__(self, root: Path) -> None:
        """Open a real private store and initialize commit/close observations."""

        super().__init__(root)
        self.create_entered = Event()
        self.create_release = Event()
        self.create_release.set()
        self.create_error: Exception | None = None
        self.closed_event = Event()

    def create(
        self,
        result: GeneratedImageResult,
        *,
        private_prompt: str | None = None,
    ) -> ImageGalleryItem:
        """Pause an admitted commit before performing the actual atomic write."""

        self.create_entered.set()
        assert self.create_release.wait(5.0)
        if self.create_error is not None:
            raise self.create_error
        return super().create(result, private_prompt=private_prompt)

    def close(self) -> None:
        """Release the real cross-process Gallery lease and record completion."""

        super().close()
        self.closed_event.set()


def _backend(
    tmp_path: Path,
    *,
    service: ImageGenerationApplicationService | None = None,
    writer: ImageChannelWriter | None = None,
    output: _Output | None = None,
) -> tuple[DesktopBackend, _Output]:
    """Build an isolated backend whose Brain must never be initialized."""

    def _unexpected_brain() -> Brain:
        """Reject accidental dependence on unrelated conversational readiness."""

        raise AssertionError("Image routing must not initialize Brain")

    output = _Output() if output is None else output
    backend = DesktopBackend(
        brain_factory=_unexpected_brain,
        model_loader=lambda: (),
        settings_validator=lambda: None,
        settings_repository=DesktopSettingsRepository(
            tmp_path / "global.json",
            EditableDesktopSettings(
                model_name="test-model",
                ollama_host="http://localhost:11434",
                short_term_memory_token_budget=2_048,
                memory_retrieval_limit=5,
                data_import_max_bytes=16 * 1024 * 1024,
            ),
        ),
        voice_settings_service=create_voice_settings_service(tmp_path),
        image_service=service,
        image_writer=writer,
        input_stream=StringIO(),
        output_stream=output,
        expected_session_token=_TOKEN,
    )
    return backend, output


def _ready(
    tmp_path: Path,
    *,
    blocked_pipe: bool = False,
) -> tuple[DesktopBackend, _Output, _BinaryOutput, _Core, _Gallery, ImageGenerationApplicationService]:
    """Compose and authenticate the real service with controlled external edges."""

    core = _Core()
    gallery = _Gallery(tmp_path / "gallery")
    service = ImageGenerationApplicationService(cast(ImageGenerationCore, core), gallery)
    output = _Output()
    stream = _BinaryOutput(output, blocked=blocked_pipe)
    backend, _ = _backend(
        tmp_path,
        service=service,
        writer=ImageChannelWriter(stream),
        output=output,
    )
    _handshake(backend)
    return backend, output, stream, core, gallery, service


def _success(output: _Output, request_id: str) -> dict[str, Any]:
    """Return one successfully validated bridge result."""

    response = output.response(request_id)
    assert response["ok"] is True, response
    return cast(dict[str, Any], response["result"])


def _error(output: _Output, request_id: str) -> dict[str, Any]:
    """Return one unsuccessful bridge result without losing error context."""

    response = output.response(request_id)
    assert response["ok"] is False, response
    return cast(dict[str, Any], response["error"])


def _shutdown(backend: DesktopBackend) -> None:
    """End directly driven sessions through the public shutdown route."""

    assert not backend._handle_line(_request("shutdown", "shutdown", {}))


def _record_thread(threads: list[Thread], *args: Any, **kwargs: Any) -> Thread:
    """Retain a real worker handle so tests can prove its finalizer completed."""

    thread = Thread(*args, **kwargs)
    threads.append(thread)
    return thread


@pytest.mark.parametrize("method", _METHODS)
def test_unconfigured_image_method_is_explicitly_unavailable(tmp_path: Path, method: str) -> None:
    """Close the accepted-contract gap even before Brain initialization."""

    backend, output = _backend(tmp_path)
    _handshake(backend)
    try:
        assert backend._handle_line(_request("image", method, _method_params(method)))
        assert _error(output, "image")["code"] == "image.unavailable"
        assert not set(_METHODS).intersection(_success(output, "handshake")["capabilities"])
    finally:
        _shutdown(backend)


@pytest.mark.parametrize("method", _METHODS)
def test_image_methods_require_authenticated_local_peer(tmp_path: Path, method: str) -> None:
    """Prevent unauthenticated clients from discovering or using image resources."""

    backend, output = _backend(tmp_path)
    try:
        assert backend._handle_line(_request("unauthenticated", method, _method_params(method)))
        assert _error(output, "unauthenticated")["code"] == "protocol.not_authenticated"
    finally:
        _shutdown(backend)


@pytest.mark.parametrize(("has_service", "has_writer"), [(False, False), (True, False), (False, True), (True, True)])
def test_image_capability_requires_complete_runtime_and_private_channel(
    tmp_path: Path,
    has_service: bool,
    has_writer: bool,
) -> None:
    """Advertise image methods only with both trusted owners installed."""

    core = _Core()
    gallery = _Gallery(tmp_path / "gallery")
    service = ImageGenerationApplicationService(cast(ImageGenerationCore, core), gallery)
    output = _Output()
    stream = _BinaryOutput(output)
    writer = ImageChannelWriter(stream)
    backend, _ = _backend(
        tmp_path,
        service=service if has_service else None,
        writer=writer if has_writer else None,
        output=output,
    )
    _handshake(backend)
    try:
        capabilities = set(_success(output, "handshake")["capabilities"])
        assert capabilities.intersection(_METHODS) == (set(_METHODS) if has_service and has_writer else set())
        assert backend._handle_line(_request("list", "image.gallery.list", {"limit": 1, "offset": 0}))
        if has_service and has_writer:
            assert _success(output, "list")["items"] == []
        else:
            assert _error(output, "list")["code"] == "image.unavailable"
    finally:
        _shutdown(backend)
        service.close()
        writer.close()


def test_generation_translates_dto_progress_and_durable_receipt(tmp_path: Path) -> None:
    """Publish an artifact and exact trusted stages without initializing Brain."""

    backend, output, stream, core, gallery, _service = _ready(tmp_path)
    try:
        assert backend._handle_line(_request("generate", "image.generate", _params()))
        result = _success(output, "generate")
        assert result["kind"] == "image.generation"
        assert result["operationId"].startswith("imageop_")
        artifact = result["artifact"]
        assert gallery.get(artifact["artifactId"]).sha256 == artifact["sha256"]
        spec, = core.specs
        assert spec.intent is ImageIntent.NEW_CHARACTER
        assert spec.original_fictional_confirmed is True
        assert spec.profile_id == "anime-local"
        assert spec.size_preset_id == "square"
        assert spec.prompt_components == (
            "adult original woman with violet hair", "white dress", "standing",
            "quiet garden", "watercolor", "portrait", "soft daylight",
        )
        assert spec.negative_prompt == "low contrast"
        assert spec.seed == 42
        assert spec.lora is None
        progress = [item for item in output.messages() if item.get("type") == "progress" and item.get("requestId") == "generate"]
        assert [(item["message"], item["completed"]) for item in progress] == [(str(stage), percent) for stage, percent in _STAGES.items()]
        assert all(item["operation"] == "image.generate" and item["total"] == 100 for item in progress)
        assert core.result.image.data not in stream.getvalue()
        assert "adult original woman" not in output.getvalue()
        assert str(tmp_path) not in output.getvalue()
        assert not any("base64" in key.lower() or "path" in key.lower() for key in artifact)
    finally:
        _shutdown(backend)


@pytest.mark.parametrize("strength_model,strength_clip", [(0, 2), (0.75, 1.25)])
def test_generation_maps_optional_lora_without_changing_public_artifact(
    tmp_path: Path,
    strength_model: float,
    strength_clip: float,
) -> None:
    """Pass only typed selection to the application while retaining Gallery metadata."""

    backend, output, _stream, core, _gallery, _service = _ready(tmp_path)
    try:
        selection = {
            "loraId": "character_style",
            "strengthModel": strength_model,
            "strengthClip": strength_clip,
        }
        assert backend._handle_line(_request("generate-lora", "image.generate", _params(lora=selection)))
        artifact = _success(output, "generate-lora")["artifact"]
        spec, = core.specs
        assert spec.lora is not None
        assert spec.lora.lora_id == "character_style"
        assert spec.lora.strength_model == strength_model
        assert spec.lora.strength_clip == strength_clip
        assert "lora" not in artifact
    finally:
        _shutdown(backend)


@pytest.mark.parametrize("changes", [
    {"originalFictionalConfirmed": False},
    {"originalFictionalConfirmed": 1},
    {"originalFictionalConfirmed": "true"},
    {"intent": "new-character"},
    {"contentMode": "adult"},
    {"adultMode": True},
    {"nsfw": True},
    {"workflow": {}},
    {"profileId": r"D:\private\model.safetensors"},
    {"lora": None},
    {"lora": {"loraId": "character_style", "strengthModel": 1}},
    {"lora": {"loraId": "../private.safetensors", "strengthModel": 1, "strengthClip": 1}},
    {"lora": {"loraId": "character_style", "strengthModel": 1, "strengthClip": 3}},
])
def test_generation_rejects_nonliteral_confirmation_and_unreviewed_fields(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    """Keep the accepted DTO independent of mode toggles and native paths."""

    backend, output, _stream, core, _gallery, _service = _ready(tmp_path)
    try:
        assert backend._handle_line(_request("invalid", "image.generate", _params(**changes)))
        assert _error(output, "invalid")["code"] in {
            "protocol.invalid_params", "protocol.invalid_message",
        }
        assert core.specs == []
    finally:
        _shutdown(backend)


@pytest.mark.parametrize("changes", [
    {"appearance": "17 years old, violet hair"},
    {"appearance": "real person with violet hair"},
    {"appearance": "two women with violet hair"},
    {"appearance": "existing character with violet hair"},
    {"negativePrompt": "embedding:private-model"},
])
def test_backend_applies_subject_and_asset_boundaries_before_inference(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    """Apply the structured domain contract before acquiring native resources."""

    backend, output, _stream, core, gallery, _service = _ready(tmp_path)
    try:
        assert backend._handle_line(_request("unsupported", "image.generate", _params(**changes)))
        assert _error(output, "unsupported")["code"] == "image.subject_unsupported"
        assert core.specs == []
        assert gallery.list() == ()
    finally:
        _shutdown(backend)


def test_generation_admission_cancellation_and_retry_remain_independent_of_gallery(tmp_path: Path) -> None:
    """Cancel a busy runtime request while metadata reads remain responsive."""

    backend, output, _stream, core, gallery, _service = _ready(tmp_path)
    core.release.clear()
    try:
        assert backend._handle_line(_request("first", "image.generate", _params()))
        assert core.entered.wait(1.0)
        assert backend._handle_line(_request("busy", "image.generate", _params()))
        assert _error(output, "busy")["code"] == "image.busy"
        assert backend._handle_line(_request("list", "image.gallery.list", {"limit": 10, "offset": 0}))
        assert _success(output, "list")["items"] == []
        assert backend._handle_line(_request("cancel", "request.cancel", {"requestId": "first"}))
        assert _success(output, "cancel") == {"stopped": True}
        assert _error(output, "first")["code"] == "image.cancelled"
        assert gallery.list() == ()
        core.release.set()
        assert backend._handle_line(_request("retry", "image.generate", _params()))
        assert _success(output, "retry")["artifact"]["artifactId"].startswith("gallery_")
    finally:
        core.release.set()
        _shutdown(backend)


def test_late_cancel_for_previous_request_cannot_cancel_next_generation(tmp_path: Path) -> None:
    """Correlate cancellation with the active request rather than runtime reuse."""

    backend, output, _stream, core, _gallery, _service = _ready(tmp_path)
    try:
        assert backend._handle_line(_request("first", "image.generate", _params()))
        _success(output, "first")
        core.entered.clear()
        core.release.clear()
        assert backend._handle_line(_request("next", "image.generate", _params()))
        assert core.entered.wait(1.0)
        assert backend._handle_line(_request("stale-cancel", "request.cancel", {"requestId": "first"}))
        assert _error(output, "stale-cancel")["code"] == "request.not_cancellable"
        core.release.set()
        assert _success(output, "next")["kind"] == "image.generation"
    finally:
        core.release.set()
        _shutdown(backend)


def test_cancellation_before_application_registration_is_reapplied(tmp_path: Path) -> None:
    """Persist immediate cancel intent until the real service owns its ID."""

    backend, output, _stream, core, gallery, service = _ready(tmp_path)
    original_generate = service.generate
    entered = Event()
    release = Event()

    def _delayed_generate(*args: Any, **kwargs: Any) -> ImageGalleryItem:
        """Hold the narrow worker-start interval before service admission."""

        entered.set()
        assert release.wait(5.0)
        return original_generate(*args, **kwargs)

    try:
        with patch.object(service, "generate", side_effect=_delayed_generate):
            assert backend._handle_line(_request("early", "image.generate", _params()))
            assert entered.wait(1.0)
            assert backend._handle_line(_request("cancel-early", "request.cancel", {"requestId": "early"}))
            assert _success(output, "cancel-early") == {"stopped": True}
            release.set()
            assert _error(output, "early")["code"] == "image.cancelled"
        assert gallery.list() == ()
        assert len(core.specs) <= 1
    finally:
        release.set()
        _shutdown(backend)


def test_commit_wins_cancellation_and_withholds_completion_until_durable(tmp_path: Path) -> None:
    """Reject cancellation after Gallery admission without publishing early 100%."""

    backend, output, _stream, _core, gallery, _service = _ready(tmp_path)
    gallery.create_release.clear()
    try:
        assert backend._handle_line(_request("commit", "image.generate", _params()))
        assert gallery.create_entered.wait(1.0)
        assert not any(item.get("requestId") == "commit" and item.get("completed") == 100 for item in output.messages())
        assert backend._handle_line(_request("too-late", "request.cancel", {"requestId": "commit"}))
        assert _error(output, "too-late")["code"] == "request.not_cancellable"
        gallery.create_release.set()
        artifact = _success(output, "commit")["artifact"]
        assert gallery.get(artifact["artifactId"])
        assert backend._handle_line(_request("finished", "request.cancel", {"requestId": "commit"}))
        assert _error(output, "finished")["code"] == "request.not_cancellable"
    finally:
        gallery.create_release.set()
        _shutdown(backend)


def test_gallery_pagination_get_open_and_delete_are_path_private(tmp_path: Path) -> None:
    """Round-trip metadata and binary previews through their distinct channels."""

    backend, output, stream, _core, gallery, _service = _ready(tmp_path)
    items = [gallery.create(_result(sequence)) for sequence in range(3)]
    try:
        assert backend._handle_line(_request("page", "image.gallery.list", {"limit": 1, "offset": 1}))
        page = _success(output, "page")
        assert page["limit"] == 1 and page["offset"] == 1 and page["hasMore"] is True
        assert [item["artifactId"] for item in page["items"]] == [items[1].artifact_id]
        artifact_id = items[1].artifact_id
        assert backend._handle_line(_request("get", "image.gallery.get", {"artifactId": artifact_id}))
        assert _success(output, "get")["item"] == page["items"][0]
        assert backend._handle_line(_request("open", "image.gallery.open", {"artifactId": artifact_id}))
        preview = _success(output, "open")
        assert stream.flushed.wait(1.0)
        assert "open" in stream.responses_before_write
        read = ImageChannelReader().feed(stream.getvalue())
        assert read.frame is not None
        assert read.frame.png_bytes == gallery.open_verified(artifact_id).data
        assert read.frame.metadata.as_dict() == {key: preview[key] for key in read.frame.metadata.as_dict()}
        assert "base64" not in output.getvalue().lower()
        assert str(tmp_path) not in output.getvalue()
        assert backend._handle_line(_request("delete", "image.gallery.delete", {"artifactId": artifact_id}))
        assert _success(output, "delete") == {"kind": "image.gallery.delete", "artifactId": artifact_id, "deleted": True}
        assert backend._handle_line(_request("missing", "image.gallery.get", {"artifactId": artifact_id}))
        assert _error(output, "missing")["code"] == "image.gallery.not_found"
    finally:
        _shutdown(backend)


@pytest.mark.parametrize("method", ["image.gallery.get", "image.gallery.open", "image.gallery.delete"])
def test_missing_gallery_artifacts_fail_without_native_paths(tmp_path: Path, method: str) -> None:
    """Give all opaque lookup routes a typed, sanitized not-found response."""

    backend, output, stream, _core, _gallery, _service = _ready(tmp_path)
    try:
        assert backend._handle_line(_request("missing", method, {"artifactId": _ARTIFACT_ID}))
        assert _error(output, "missing")["code"] == "image.gallery.not_found"
        assert not stream.entered.is_set()
        assert str(tmp_path) not in output.getvalue()
    finally:
        _shutdown(backend)


def test_preview_revalidates_persisted_png_before_publishing_token(tmp_path: Path) -> None:
    """Reject corrupted storage before issuing a usable preview capability."""

    backend, output, stream, _core, gallery, _service = _ready(tmp_path)
    item = gallery.create(_result())
    image_path, = tuple((tmp_path / "gallery").glob("image_*/image.png"))
    image_path.write_bytes(b"corrupt persisted PNG")
    try:
        assert backend._handle_line(_request("corrupt", "image.gallery.open", {"artifactId": item.artifact_id}))
        assert _error(output, "corrupt")["code"] == "image.gallery.storage_failed"
        assert not stream.entered.is_set()
        assert "previewToken" not in output.getvalue()
        assert str(image_path) not in output.getvalue()
    finally:
        _shutdown(backend)


def test_repeated_preview_opens_issue_distinct_tokens_and_frames(tmp_path: Path) -> None:
    """Reserve a fresh one-use token every time the same artifact is reopened."""

    backend, output, stream, _core, gallery, _service = _ready(tmp_path)
    item = gallery.create(_result())
    workers: list[Thread] = []
    results: list[dict[str, Any]] = []
    try:
        with patch.object(desktop_images, "Thread", side_effect=lambda *args, **kwargs: _record_thread(workers, *args, **kwargs)):
            for request_id in ("open-first", "open-again"):
                assert backend._handle_line(_request(request_id, "image.gallery.open", {"artifactId": item.artifact_id}))
                results.append(_success(output, request_id))
                workers[-1].join(1.0)
                assert not workers[-1].is_alive()
        assert results[0]["previewToken"] != results[1]["previewToken"]
        assert results[0]["sha256"] == results[1]["sha256"] == item.sha256
        reader = ImageChannelReader()
        first = reader.feed(stream.getvalue())
        assert first.frame is not None
        second = reader.feed(stream.getvalue()[first.consumed_bytes:])
        assert second.frame is not None
        assert first.consumed_bytes + second.consumed_bytes == len(stream.getvalue())
        assert [first.frame.metadata.preview_token, second.frame.metadata.preview_token] == [result["previewToken"] for result in results]
        reader.finish()
    finally:
        _shutdown(backend)


def test_preview_write_failure_has_one_response_and_disables_channel(tmp_path: Path) -> None:
    """Withdraw a poisoned pipe without emitting a second response after metadata."""

    backend, output, stream, _core, gallery, _service = _ready(tmp_path)
    item = gallery.create(_result())
    workers: list[Thread] = []
    try:
        with patch.object(desktop_images, "Thread", side_effect=lambda *args, **kwargs: _record_thread(workers, *args, **kwargs)):
            with patch.object(stream, "write", side_effect=OSError(r"D:\private\pipe.log secret-token")):
                assert backend._handle_line(_request("open-failed", "image.gallery.open", {"artifactId": item.artifact_id}))
                assert _success(output, "open-failed")["kind"] == "image.gallery.open"
                workers[-1].join(1.0)
                assert not workers[-1].is_alive()
        assert stream.closed_event.is_set()
        assert backend._images.capabilities == ()
        assert "secret-token" not in output.getvalue()
        assert backend._handle_line(_request("after-pipe-failure", "image.gallery.list", {"limit": 1, "offset": 0}))
        assert _error(output, "after-pipe-failure")["code"] == "image.unavailable"
        assert len([message for message in output.messages() if message.get("id") == "open-failed"]) == 1
    finally:
        _shutdown(backend)


@pytest.mark.parametrize(("failure", "expected_code"), [
    ("core", "image.failed"),
    ("storage", "image.gallery.storage_failed"),
])
def test_generation_failure_is_sanitized_and_releases_admission(
    tmp_path: Path,
    failure: str,
    expected_code: str,
) -> None:
    """Hide private diagnostics and let a later request reuse healthy owners."""

    backend, output, _stream, core, gallery, _service = _ready(tmp_path)
    private_detail = r"D:\private\checkpoint.safetensors bearer-secret-42"
    if failure == "core":
        core.error = RuntimeError(private_detail)
    else:
        gallery.create_error = ImageGalleryStorageError(private_detail)
    try:
        assert backend._handle_line(_request("failure", "image.generate", _params()))
        assert _error(output, "failure")["code"] == expected_code
        assert private_detail not in output.getvalue()
        assert "bearer-secret-42" not in output.getvalue()
        assert not any(item.get("requestId") == "failure" and item.get("completed") == 100 for item in output.messages())
        core.error = None
        gallery.create_error = None
        assert backend._handle_line(_request("retry", "image.generate", _params()))
        assert _success(output, "retry")["kind"] == "image.generation"
    finally:
        _shutdown(backend)


@pytest.mark.parametrize("method", ["image.generate", "image.gallery.list"])
@pytest.mark.parametrize("phase", ["construct", "start"])
def test_worker_creation_failure_releases_admission_for_retry(
    tmp_path: Path,
    method: str,
    phase: str,
) -> None:
    """Recover admission when resource exhaustion prevents daemon creation."""

    backend, output, _stream, _core, _gallery, _service = _ready(tmp_path)
    private_detail = r"D:\private\thread-start.log"
    try:
        with patch.object(desktop_images, "Thread") as thread_type:
            if phase == "construct":
                thread_type.side_effect = RuntimeError(private_detail)
            else:
                thread_type.return_value.start.side_effect = RuntimeError(private_detail)
            assert backend._handle_line(_request("start-failed", method, _method_params(method)))
            assert _error(output, "start-failed")["code"] == "image.failed"
        assert private_detail not in output.getvalue()
        assert backend._handle_line(_request("retry", method, _method_params(method)))
        assert _success(output, "retry")["kind"] in {"image.generation", "image.gallery.list"}
    finally:
        _shutdown(backend)


def test_shutdown_retries_owner_whose_closer_could_not_be_constructed(tmp_path: Path) -> None:
    """Release the independent owner and retain failed teardown for retry."""

    backend, _output, stream, core, gallery, _service = _ready(tmp_path)
    constructed = 0

    def _construct_closer(*args: Any, **kwargs: Any) -> Thread:
        """Fail the service closer once while permitting the writer closer."""

        nonlocal constructed
        constructed += 1
        if constructed == 1:
            raise RuntimeError(r"D:\private\thread-capacity.log")
        return Thread(*args, **kwargs)

    try:
        with patch.object(desktop_images, "Thread", side_effect=_construct_closer):
            assert backend._images.close(timeout=0.05) is False
        assert stream.closed_event.wait(1.0)
        assert not core.close_entered.is_set()
        assert backend._images.close(timeout=1.0) is True
        assert core.closed.is_set()
        assert gallery.closed_event.is_set()
    finally:
        _shutdown(backend)


def test_unusable_preview_writer_withdraws_image_capability(tmp_path: Path) -> None:
    """Stop advertising image routes after the owned channel rejects prepare."""

    backend, output, _stream, _core, gallery, _service = _ready(tmp_path)
    item = gallery.create(_result())
    writer = backend._images._writer
    assert writer is not None
    writer.close()
    try:
        assert backend._handle_line(_request("closed-preview", "image.gallery.open", {"artifactId": item.artifact_id}))
        assert _error(output, "closed-preview")["code"] == "image.preview_unavailable"
        assert backend._images.capabilities == ()
        assert backend._handle_line(_request("unavailable", "image.generate", _params()))
        assert _error(output, "unavailable")["code"] == "image.unavailable"
    finally:
        _shutdown(backend)


def test_blocked_preview_writer_keeps_gallery_bounded_and_shutdown_responsive(tmp_path: Path) -> None:
    """Bound shutdown while a private pipe blocks, closing the service separately."""

    backend, output, stream, core, gallery, _service = _ready(tmp_path, blocked_pipe=True)
    item = gallery.create(_result())
    try:
        assert backend._handle_line(_request("open", "image.gallery.open", {"artifactId": item.artifact_id}))
        assert _success(output, "open")["artifactId"] == item.artifact_id
        assert stream.entered.wait(1.0)
        assert backend._handle_line(_request("busy-gallery", "image.gallery.list", {"limit": 1, "offset": 0}))
        assert _error(output, "busy-gallery")["code"] == "image.busy"
        started = monotonic()
        _shutdown(backend)
        assert monotonic() - started < 3.0
        assert _success(output, "shutdown") == {"stopped": True}
        assert core.closed.wait(1.0)
        assert gallery.closed_event.wait(1.0)
        assert not stream.closed_event.is_set()
        terminal_output = output.getvalue()
    finally:
        stream.release.set()
    assert stream.closed_event.wait(1.0)
    assert output.getvalue() == terminal_output


def test_shutdown_waits_for_an_admitted_gallery_commit(tmp_path: Path) -> None:
    """Finish a publication that won cancellation before process teardown."""

    backend, output, stream, core, gallery, _service = _ready(tmp_path)
    gallery.create_release.clear()
    stopped = Event()
    shutdown_errors: list[BaseException] = []

    def _stop_backend() -> None:
        """Capture shutdown failures while the commit remains deliberately paused."""

        try:
            _shutdown(backend)
        except BaseException as error:
            shutdown_errors.append(error)
        finally:
            stopped.set()

    assert backend._handle_line(_request("committing", "image.generate", _params()))
    assert gallery.create_entered.wait(1.0)
    shutdown_thread = Thread(target=_stop_backend, daemon=True)
    try:
        with patch.object(desktop_images, "IMAGE_SHUTDOWN_TIMEOUT_SECONDS", 0.01):
            shutdown_thread.start()
            assert not stopped.wait(0.1)
            assert not core.closed.is_set()
            assert stream.closed_event.wait(1.0)
            gallery.create_release.set()
            assert stopped.wait(1.0)
        assert shutdown_errors == []
        assert _success(output, "shutdown") == {"stopped": True}
        assert not any(item.get("id") == "committing" or item.get("requestId") == "committing" and item.get("completed") == 100 for item in output.messages())
        assert core.closed.wait(1.0)
        assert gallery.closed_event.wait(1.0)
        with ImageGalleryStore(tmp_path / "gallery") as reopened:
            assert len(reopened.list()) == 1
    finally:
        gallery.create_release.set()
        shutdown_thread.join(1.0)


def test_shutdown_bounds_runtime_teardown_after_winning_commit_finishes(tmp_path: Path) -> None:
    """Wait for durable publication while bounding subsequent native cleanup."""

    backend, output, stream, core, gallery, _service = _ready(tmp_path)
    gallery.create_release.clear()
    core.close_release.clear()
    stopped = Event()
    shutdown_errors: list[BaseException] = []

    def _stop_backend() -> None:
        """Capture shutdown completion independently of blocked runtime cleanup."""

        try:
            _shutdown(backend)
        except BaseException as error:
            shutdown_errors.append(error)
        finally:
            stopped.set()

    assert backend._handle_line(_request("committing", "image.generate", _params()))
    assert gallery.create_entered.wait(1.0)
    shutdown_thread = Thread(target=_stop_backend, daemon=True)
    try:
        with patch.object(desktop_images, "IMAGE_SHUTDOWN_TIMEOUT_SECONDS", 0.05):
            shutdown_thread.start()
            assert not stopped.wait(0.1)
            assert not core.close_entered.is_set()
            gallery.create_release.set()
            assert core.close_entered.wait(1.0)
            assert stopped.wait(0.5)
        assert shutdown_errors == []
        assert _success(output, "shutdown") == {"stopped": True}
        assert not core.closed.is_set()
        assert not gallery.closed_event.is_set()
        assert stream.closed_event.wait(1.0)
        terminal_output = output.getvalue()
    finally:
        gallery.create_release.set()
        core.close_release.set()
        shutdown_thread.join(1.0)
    assert core.closed.wait(1.0)
    assert gallery.closed_event.wait(1.0)
    assert output.getvalue() == terminal_output
    with ImageGalleryStore(tmp_path / "gallery") as reopened:
        assert len(reopened.list()) == 1


@pytest.mark.parametrize("termination", ["shutdown", "eof"])
def test_shutdown_suppresses_late_generation_and_releases_owners_after_drain(
    tmp_path: Path,
    termination: str,
) -> None:
    """Return within a fixed bound even when native inference ignores cancel."""

    backend, output, stream, core, gallery, _service = _ready(tmp_path)
    core.ignore_cancellation = True
    core.release.clear()
    try:
        assert backend._handle_line(_request("late", "image.generate", _params()))
        assert core.entered.wait(1.0)
        started = monotonic()
        if termination == "shutdown":
            _shutdown(backend)
            assert _success(output, "shutdown") == {"stopped": True}
        else:
            backend.run()
        assert monotonic() - started < 3.0
        assert stream.closed_event.wait(1.0)
        assert not core.closed.is_set()
        terminal_output = output.getvalue()
    finally:
        core.release.set()
    assert core.closed.wait(1.0)
    assert gallery.closed_event.wait(1.0)
    assert output.getvalue() == terminal_output
