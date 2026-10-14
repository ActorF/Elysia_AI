"""Test cancellable image publication and path-private Gallery delegation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
from threading import Event, Thread
from typing import cast
from uuid import uuid4

import pytest

from image_generation.application import (
    ImageGenerationApplicationBusyError,
    ImageGenerationApplicationClosedError,
    ImageGenerationApplicationError,
    ImageGenerationApplicationService,
    ImageGenerationApplicationValidationError,
    ImageGenerationCancelOutcome,
)
from image_generation.domain import ImageGenerationSpec
from image_generation.exceptions import (
    ImageGenerationCancelledError,
    ImageGenerationUnavailableError,
)
from image_generation.gallery import (
    ImageGalleryItem,
    ImageGalleryNotFoundError,
    ImageGalleryStore,
)
from image_generation.png import CanonicalPng, PNG_MEDIA_TYPE
from image_generation.service import (
    GeneratedImageResult,
    ImageGenerationCore,
    ImageGenerationProgress,
    ImageGenerationStage,
)


def _spec(**changes: object) -> ImageGenerationSpec:
    """Build one valid unified ordinary-or-explicit generation request."""

    values: dict[str, object] = {
        "profile_id": "anime-local",
        "size_preset_id": "square",
        "appearance": "long pink hair, blue eyes",
        "outfit": "white and violet dress",
        "scene": "quiet garden",
        "negative_prompt": "low contrast",
        "seed": 42,
        "original_fictional_confirmed": True,
    }
    values.update(changes)
    return ImageGenerationSpec(**values)  # type: ignore[arg-type]


def _image() -> CanonicalPng:
    """Return deterministic bytes sufficient for the focused application fake."""

    data = b"canonical-png-fixture"
    return CanonicalPng(
        data=data,
        width=512,
        height=512,
        sha256=hashlib.sha256(data).hexdigest(),
        media_type=PNG_MEDIA_TYPE,
    )


def _result() -> GeneratedImageResult:
    """Return one typed Core result with path-free provenance metadata."""

    return GeneratedImageResult(
        job_id=str(uuid4()),
        image=_image(),
        profile_id="anime-local",
        profile_digest="a" * 64,
        workflow_id="txt2img",
        workflow_version=1,
        workflow_digest="b" * 64,
        seed=42,
        created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )


def _item() -> ImageGalleryItem:
    """Return one renderer-safe Gallery record without a filesystem path."""

    image = _image()
    return ImageGalleryItem(
        artifact_id=f"gallery_{uuid4().hex}",
        created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        width=image.width,
        height=image.height,
        byte_size=len(image.data),
        sha256=image.sha256,
        media_type=image.media_type,
        profile_id="anime-local",
        seed=42,
    )


class _FakeCore:
    """Expose controllable inference boundaries without a native runtime."""

    def __init__(self) -> None:
        """Initialize deterministic result, gates, and call observations."""

        self.result = _result()
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.wait_for_release = False
        self.wait_until_cancelled = False
        self.ignore_cancellation = False
        self.error: Exception | None = None
        self.closed = False
        self.close_calls = 0
        self.close_entered = Event()
        self.close_release = Event()
        self.close_release.set()
        self.close_error: Exception | None = None
        self.calls = 0

    def generate(
        self,
        spec: ImageGenerationSpec,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[ImageGenerationProgress], None] | None = None,
    ) -> GeneratedImageResult:
        """Block at selected points and return one typed generated result."""

        assert spec.profile_id == "anime-local"
        self.calls += 1
        self.entered.set()
        if on_progress is not None:
            on_progress(
                ImageGenerationProgress(
                    stage=ImageGenerationStage.VALIDATING,
                    percent=5,
                )
            )
        if self.wait_until_cancelled:
            assert cancel_requested is not None
            while not cancel_requested():
                self.release.wait(0.01)
            raise ImageGenerationCancelledError(
                "Local image generation was cancelled."
            )
        if self.wait_for_release:
            assert self.release.wait(5)
        if self.error is not None:
            raise self.error
        if (
            not self.ignore_cancellation
            and cancel_requested is not None
            and cancel_requested()
        ):
            raise ImageGenerationCancelledError(
                "Local image generation was cancelled."
            )
        if on_progress is not None:
            on_progress(
                ImageGenerationProgress(
                    stage=ImageGenerationStage.COMPLETED,
                    percent=100,
                )
            )
        return self.result

    def close(self) -> None:
        """Record release or raise the configured teardown failure."""

        self.close_calls += 1
        self.close_entered.set()
        assert self.close_release.wait(5)
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class _FakeGallery:
    """Record opaque Gallery calls and optionally pause the commit."""

    def __init__(self) -> None:
        """Initialize one item, one image, and deterministic operation gates."""

        self.item = _item()
        self.image = _image()
        self.created: list[tuple[GeneratedImageResult, str | None]] = []
        self.deleted: list[str] = []
        self.opened: list[str] = []
        self.create_entered = Event()
        self.create_release = Event()
        self.create_release.set()
        self.list_entered = Event()
        self.list_release = Event()
        self.list_release.set()
        self.create_error: Exception | None = None
        self.list_error: Exception | None = None
        self.closed = False
        self.close_calls = 0
        self.close_entered = Event()
        self.close_release = Event()
        self.close_release.set()
        self.close_error: Exception | None = None

    def create(
        self,
        result: GeneratedImageResult,
        *,
        private_prompt: str | None = None,
    ) -> ImageGalleryItem:
        """Pause at the durable-commit surrogate and record private prompt text."""

        self.create_entered.set()
        assert self.create_release.wait(5)
        if self.create_error is not None:
            raise self.create_error
        self.created.append((result, private_prompt))
        return self.item

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ImageGalleryItem, ...]:
        """Return one page while recording bounded pagination arguments."""

        assert limit > 0
        assert offset >= 0
        self.list_entered.set()
        assert self.list_release.wait(5)
        if self.list_error is not None:
            raise self.list_error
        return (self.item,)

    def get(self, artifact_id: str) -> ImageGalleryItem:
        """Return the fixture item only for its opaque identifier."""

        if artifact_id != self.item.artifact_id:
            raise ImageGalleryNotFoundError("The Gallery artifact was not found.")
        return self.item

    def open_verified(self, artifact_id: str) -> CanonicalPng:
        """Return verified fixture bytes while recording the opaque selector."""

        self.opened.append(artifact_id)
        return self.image

    def delete(self, artifact_id: str) -> None:
        """Record one deletion selected without a filesystem path."""

        self.deleted.append(artifact_id)

    def close(self) -> None:
        """Record release or raise the configured teardown failure."""

        self.close_calls += 1
        self.close_entered.set()
        assert self.close_release.wait(5)
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


def _service(
    core: _FakeCore | None = None,
    gallery: _FakeGallery | None = None,
) -> tuple[ImageGenerationApplicationService, _FakeCore, _FakeGallery]:
    """Build an application service from focused structural test doubles."""

    resolved_core = core or _FakeCore()
    resolved_gallery = gallery or _FakeGallery()
    service = ImageGenerationApplicationService(
        cast(ImageGenerationCore, resolved_core),
        cast(ImageGalleryStore, resolved_gallery),
    )
    return service, resolved_core, resolved_gallery


def _run_in_thread(call: Callable[[], object]) -> tuple[Thread, list[object]]:
    """Run one call and capture either its result or raised exception."""

    outcome: list[object] = []

    def invoke() -> None:
        """Capture the worker result without losing assertion context."""

        try:
            outcome.append(call())
        except BaseException as error:
            outcome.append(error)

    thread = Thread(target=invoke)
    thread.start()
    return thread, outcome


def _exception_payloads(error: BaseException) -> tuple[str, ...]:
    """Collect arguments from an exception and every retained chain node."""

    payloads: list[str] = []
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        payloads.extend(str(argument) for argument in current.args)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(payloads)


def test_generate_commits_result_and_keeps_prompt_private() -> None:
    """Persist one Core result while returning only path-free Gallery metadata."""

    service, core, gallery = _service()
    operation_id = service.create_operation_id()

    item = service.generate(operation_id, _spec())

    assert item is gallery.item
    assert gallery.created == [
        (
            core.result,
            "long pink hair, blue eyes\nwhite and violet dress\nquiet garden",
        )
    ]
    assert not hasattr(item, "path")
    assert not hasattr(item, "private_prompt")
    assert "low contrast" not in gallery.created[0][1]  # type: ignore[operator]


def test_core_completion_is_withheld_until_gallery_commit() -> None:
    """Publish 100% progress only after the durable create boundary succeeds."""

    gallery = _FakeGallery()
    gallery.create_release.clear()
    service, _core, _gallery = _service(gallery=gallery)
    progress: list[ImageGenerationProgress] = []
    thread, outcome = _run_in_thread(
        lambda: service.generate(
            service.create_operation_id(),
            _spec(),
            on_progress=progress.append,
        )
    )

    assert gallery.create_entered.wait(2)
    assert [event.stage for event in progress] == [ImageGenerationStage.VALIDATING]
    gallery.create_release.set()
    thread.join(2)

    assert outcome == [gallery.item]
    assert [event.stage for event in progress] == [
        ImageGenerationStage.VALIDATING,
        ImageGenerationStage.COMPLETED,
    ]


def test_cancel_wins_before_commit_even_if_core_ignores_the_late_request() -> None:
    """Discard returned bytes when cancellation linearizes before publication."""

    core = _FakeCore()
    core.wait_for_release = True
    core.ignore_cancellation = True
    core.release.clear()
    service, _core, gallery = _service(core=core)
    operation_id = service.create_operation_id()
    thread, outcome = _run_in_thread(
        lambda: service.generate(operation_id, _spec())
    )

    assert core.entered.wait(2)
    assert service.cancel(operation_id) is ImageGenerationCancelOutcome.REQUESTED
    core.release.set()
    thread.join(2)

    assert len(outcome) == 1
    assert isinstance(outcome[0], ImageGenerationCancelledError)
    assert gallery.created == []


def test_commit_wins_before_cancel_and_returns_saved_item() -> None:
    """Report too-late cancellation once the Gallery create call owns outcome."""

    gallery = _FakeGallery()
    gallery.create_release.clear()
    service, _core, _gallery = _service(gallery=gallery)
    operation_id = service.create_operation_id()
    thread, outcome = _run_in_thread(
        lambda: service.generate(operation_id, _spec())
    )

    assert gallery.create_entered.wait(2)
    assert service.cancel(operation_id) is ImageGenerationCancelOutcome.TOO_LATE
    gallery.create_release.set()
    thread.join(2)

    assert outcome == [gallery.item]
    assert len(gallery.created) == 1


def test_cancel_is_idempotent_while_running_and_opaque_for_other_ids() -> None:
    """Keep repeated cancellation stable without revealing another operation."""

    core = _FakeCore()
    core.wait_for_release = True
    core.ignore_cancellation = True
    core.release.clear()
    service, _core, _gallery = _service(core=core)
    operation_id = service.create_operation_id()
    thread, _outcome = _run_in_thread(
        lambda: service.generate(operation_id, _spec())
    )
    assert core.entered.wait(2)

    assert service.cancel(operation_id) is ImageGenerationCancelOutcome.REQUESTED
    assert service.cancel(operation_id) is ImageGenerationCancelOutcome.REQUESTED
    assert (
        service.cancel(service.create_operation_id())
        is ImageGenerationCancelOutcome.NOT_ACTIVE
    )
    core.release.set()
    thread.join(2)


def test_operation_id_is_single_use_and_late_cancel_cannot_hit_next_job() -> None:
    """Retire completed IDs so delayed cancellation cannot target later work."""

    service, core, _gallery = _service()
    retired_id = service.create_operation_id()
    service.generate(retired_id, _spec())

    with pytest.raises(
        ImageGenerationApplicationValidationError,
        match="already used",
    ):
        service.generate(retired_id, _spec())

    next_id = service.create_operation_id()
    core.wait_for_release = True
    core.release.clear()
    core.entered.clear()
    thread, outcome = _run_in_thread(lambda: service.generate(next_id, _spec()))
    assert core.entered.wait(2)
    assert service.cancel(retired_id) is ImageGenerationCancelOutcome.NOT_ACTIVE
    core.release.set()
    thread.join(2)

    assert outcome and not isinstance(outcome[0], BaseException)


def test_only_one_generation_uses_the_managed_runtime() -> None:
    """Reject a concurrent request before it can reach the single Core."""

    core = _FakeCore()
    core.wait_for_release = True
    core.release.clear()
    service, _core, _gallery = _service(core=core)
    first_id = service.create_operation_id()
    thread, _outcome = _run_in_thread(
        lambda: service.generate(first_id, _spec())
    )
    assert core.entered.wait(2)

    with pytest.raises(ImageGenerationApplicationBusyError, match="active"):
        service.generate(service.create_operation_id(), _spec())

    assert core.calls == 1
    service.cancel(first_id)
    core.release.set()
    thread.join(2)


def test_callback_failure_cannot_undo_a_gallery_commit() -> None:
    """Treat both inference and committed progress callbacks as advisory."""

    service, _core, gallery = _service()

    item = service.generate(
        service.create_operation_id(),
        _spec(),
        on_progress=lambda _event: (_ for _ in ()).throw(
            RuntimeError(r"D:\private\callback.txt")
        ),
    )

    assert item is gallery.item
    assert len(gallery.created) == 1


def test_close_from_generation_callback_is_rejected_without_deadlock() -> None:
    """Prevent a callback from waiting on its own active generation thread."""

    service, core, gallery = _service()
    observed: list[Exception] = []

    def callback(_event: ImageGenerationProgress) -> None:
        """Capture the self-close failure and allow generation to continue."""

        try:
            service.close()
        except Exception as error:
            observed.append(error)

    result = service.generate(
        service.create_operation_id(),
        _spec(),
        on_progress=callback,
    )

    assert result is gallery.item
    assert observed
    assert all(
        isinstance(error, ImageGenerationApplicationBusyError)
        for error in observed
    )
    assert core.closed is False
    assert gallery.closed is False


def test_close_cancels_running_inference_and_waits_for_terminal_cleanup() -> None:
    """Drain cooperative inference before releasing Core and Gallery owners."""

    core = _FakeCore()
    core.wait_until_cancelled = True
    service, _core, gallery = _service(core=core)
    generation_thread, generation_outcome = _run_in_thread(
        lambda: service.generate(service.create_operation_id(), _spec())
    )
    assert core.entered.wait(2)

    close_thread, close_outcome = _run_in_thread(service.close)
    generation_thread.join(2)
    close_thread.join(2)

    assert len(generation_outcome) == 1
    assert isinstance(generation_outcome[0], ImageGenerationCancelledError)
    assert close_outcome == [None]
    assert core.closed is True
    assert gallery.closed is True


def test_close_waits_for_non_cancellable_gallery_commit() -> None:
    """Let an admitted commit finish instead of reporting false cancellation."""

    gallery = _FakeGallery()
    gallery.create_release.clear()
    service, core, _gallery = _service(gallery=gallery)
    operation_id = service.create_operation_id()
    generation_thread, generation_outcome = _run_in_thread(
        lambda: service.generate(operation_id, _spec())
    )
    assert gallery.create_entered.wait(2)

    close_thread, close_outcome = _run_in_thread(service.close)
    close_thread.join(0.05)
    assert close_thread.is_alive()
    assert service.cancel(operation_id) is ImageGenerationCancelOutcome.TOO_LATE
    gallery.create_release.set()
    generation_thread.join(2)
    close_thread.join(2)

    assert generation_outcome == [gallery.item]
    assert close_outcome == [None]
    assert core.closed is True
    assert gallery.closed is True


def test_close_waits_for_an_admitted_gallery_read() -> None:
    """Avoid closing storage beneath a concurrently admitted metadata read."""

    gallery = _FakeGallery()
    gallery.list_release.clear()
    service, core, _gallery = _service(gallery=gallery)
    list_thread, list_outcome = _run_in_thread(service.list)
    assert gallery.list_entered.wait(2)

    close_thread, close_outcome = _run_in_thread(service.close)
    close_thread.join(0.05)
    assert close_thread.is_alive()
    gallery.list_release.set()
    list_thread.join(2)
    close_thread.join(2)

    assert list_outcome == [(gallery.item,)]
    assert close_outcome == [None]
    assert core.closed is True
    assert gallery.closed is True


def test_close_is_idempotent_and_rejects_later_operations() -> None:
    """Release each owned collaborator once and keep the closed gate stable."""

    service, core, gallery = _service()

    service.close()
    service.close()

    assert core.closed is True
    assert gallery.closed is True
    assert core.close_calls == 1
    assert gallery.close_calls == 1
    with pytest.raises(ImageGenerationApplicationClosedError, match="closed"):
        service.list()
    with pytest.raises(ImageGenerationApplicationClosedError, match="closed"):
        service.generate(service.create_operation_id(), _spec())


def test_gallery_surface_delegates_only_opaque_ids_and_path_free_values() -> None:
    """Expose list/get/open/delete without accepting or returning storage paths."""

    service, _core, gallery = _service()
    artifact_id = gallery.item.artifact_id

    assert service.list(limit=1, offset=0) == (gallery.item,)
    assert service.get(artifact_id) is gallery.item
    assert service.open(artifact_id) is gallery.image
    service.delete(artifact_id)

    assert gallery.opened == [artifact_id]
    assert gallery.deleted == [artifact_id]
    assert not hasattr(service.get(artifact_id), "path")


def test_operation_ids_reject_paths_and_noncanonical_values() -> None:
    """Never let cancellation identifiers become filesystem selectors."""

    service, core, gallery = _service()

    with pytest.raises(ImageGenerationApplicationValidationError, match="invalid"):
        service.cancel(r"D:\private\operation")
    with pytest.raises(ImageGenerationApplicationValidationError, match="invalid"):
        service.generate("imageop_NOT_HEX", _spec())

    assert core.calls == 0
    assert gallery.created == []


def test_stable_lower_layer_failures_remain_typed() -> None:
    """Preserve reviewed error types while severing private exception chains."""

    core = _FakeCore()
    core_secret = r"D:\runtime\private\python.exe"
    core.error = ImageGenerationUnavailableError("Runtime is unavailable.")
    core.error.__cause__ = OSError(core_secret)
    core.error.__context__ = OSError(core_secret)
    service, _core, gallery = _service(core=core)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable") as core_error:
        service.generate(service.create_operation_id(), _spec())
    assert all(
        core_secret not in payload
        for payload in _exception_payloads(core_error.value)
    )
    assert core_error.value.__cause__ is None
    assert core_error.value.__context__ is None

    gallery_secret = r"D:\gallery\private\manifest.json"
    gallery.list_error = ImageGalleryNotFoundError(
        "The Gallery artifact was not found."
    )
    gallery.list_error.__cause__ = OSError(gallery_secret)
    gallery.list_error.__context__ = OSError(gallery_secret)
    with pytest.raises(ImageGalleryNotFoundError, match="not found") as gallery_error:
        service.list()
    assert all(
        gallery_secret not in payload
        for payload in _exception_payloads(gallery_error.value)
    )
    assert gallery_error.value.__cause__ is None
    assert gallery_error.value.__context__ is None


@pytest.mark.parametrize("failure_owner", ["core", "gallery"])
def test_unexpected_generation_and_commit_errors_are_sanitized(
    failure_owner: str,
) -> None:
    """Remove paths and collaborator details from unexpected generation errors."""

    core = _FakeCore()
    gallery = _FakeGallery()
    secret = (
        r"D:\models\private\secret.safetensors"
        if failure_owner == "core"
        else "long pink hair, blue eyes"
    )
    if failure_owner == "core":
        core.error = RuntimeError(secret)
    else:
        gallery.create_error = RuntimeError(secret)
    service, _core, _gallery = _service(core=core, gallery=gallery)

    with pytest.raises(ImageGenerationApplicationError) as captured:
        service.generate(service.create_operation_id(), _spec())

    assert all(secret not in payload for payload in _exception_payloads(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_unexpected_gallery_read_error_is_sanitized() -> None:
    """Do not expose repository paths from an unreviewed Gallery exception."""

    gallery = _FakeGallery()
    secret = r"D:\gallery\image_private\manifest.json"
    gallery.list_error = OSError(secret)
    service, _core, _gallery = _service(gallery=gallery)

    with pytest.raises(ImageGenerationApplicationError) as captured:
        service.list()

    assert all(secret not in payload for payload in _exception_payloads(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("failed_owner", ["core", "gallery"])
def test_close_retries_only_failed_resources_without_reopening_admission(
    failed_owner: str,
) -> None:
    """Retry uncertain teardown while keeping the application permanently closed."""

    core = _FakeCore()
    gallery = _FakeGallery()
    secret = r"D:\runtime\private"
    if failed_owner == "core":
        core.close_error = OSError(secret)
    else:
        gallery.close_error = OSError(secret)
    service, _core, _gallery = _service(core=core, gallery=gallery)

    with pytest.raises(ImageGenerationApplicationError) as captured:
        service.close()

    assert all(secret not in payload for payload in _exception_payloads(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert core.closed is (failed_owner != "core")
    assert gallery.closed is (failed_owner != "gallery")
    assert core.close_calls == 1
    assert gallery.close_calls == 1
    with pytest.raises(ImageGenerationApplicationClosedError, match="closed"):
        service.list()

    core.close_error = None
    gallery.close_error = None
    service.close()

    assert core.closed is True
    assert gallery.closed is True
    assert core.close_calls == (2 if failed_owner == "core" else 1)
    assert gallery.close_calls == (2 if failed_owner == "gallery" else 1)


def test_concurrent_closers_observe_one_consistent_failed_attempt() -> None:
    """Make concurrent shutdown callers share failure before a later retry."""

    core = _FakeCore()
    gallery = _FakeGallery()
    core.close_error = OSError(r"D:\runtime\private")
    core.close_release.clear()
    service, _core, _gallery = _service(core=core, gallery=gallery)

    first, first_outcome = _run_in_thread(service.close)
    assert core.close_entered.wait(2)
    second, second_outcome = _run_in_thread(service.close)
    second.join(0.05)
    assert second.is_alive()
    core.close_release.set()
    first.join(2)
    second.join(2)

    assert len(first_outcome) == 1
    assert len(second_outcome) == 1
    assert isinstance(first_outcome[0], ImageGenerationApplicationError)
    assert isinstance(second_outcome[0], ImageGenerationApplicationError)
    assert core.close_calls == 1
    assert gallery.close_calls == 1

    core.close_error = None
    service.close()
    assert core.close_calls == 2
    assert gallery.close_calls == 1


def test_application_surface_has_no_content_mode_switch() -> None:
    """Keep ordinary and explicit prompts on the same application path."""

    service, _core, gallery = _service()
    explicit = _spec(appearance="explicit nude fictional adult woman")

    item = service.generate(service.create_operation_id(), explicit)

    assert item is gallery.item
    assert len(gallery.created) == 1
    with pytest.raises(TypeError, match="unexpected keyword"):
        service.generate(  # type: ignore[call-arg]
            service.create_operation_id(),
            explicit,
            content_mode="adult",
        )
