"""Bridge trusted image services to Desktop JSON and private PNG streams.

Generation and Gallery I/O run outside the request reader. The application
service remains the only authority for cancellation versus durable commit;
this coordinator owns request correlation, bounded admission, and the output
boundary at shutdown. Trusted configuration is supplied by the composition
root, never by an image request or a language model.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import timezone
import logging
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any

from gpu_admission import GpuAdmissionCancelledError, GpuAdmissionError, gpu_lease, using_desktop_gpu

from desktop_protocol import (
    ServerMessage,
    build_error_response,
    build_progress,
    build_success_response,
)
from desktop_protocol.image_channel import (
    ImageChannelError,
    ImageChannelWriter,
    PreparedImageFrame,
)
from image_generation import (
    ImageGalleryItem,
    ImageGalleryLimitError,
    ImageGalleryNotFoundError,
    ImageGalleryStorageError,
    ImageGalleryValidationError,
    ImageGenerationApplicationBusyError,
    ImageGenerationApplicationClosedError,
    ImageGenerationApplicationLimitError,
    ImageGenerationApplicationService,
    ImageGenerationApplicationValidationError,
    ImageGenerationCancelOutcome,
    ImageGenerationCancelledError,
    ImageGenerationLimitError,
    ImageGenerationProfileError,
    ImageGenerationProgress,
    ImageGenerationProtocolError,
    ImageGenerationSafetyError,
    ImageGenerationSpec,
    ImageGenerationStage,
    ImageGenerationUnavailableError,
    ImageGenerationValidationError,
    ImageIntent,
)


logger = logging.getLogger(__name__)
IMAGE_CAPABILITIES = (
    "image.generate",
    "image.gallery.list",
    "image.gallery.get",
    "image.gallery.delete",
    "image.gallery.open",
)
IMAGE_SHUTDOWN_TIMEOUT_SECONDS = 2.0
_PROGRESS_CHECKPOINTS = {
    ImageGenerationStage.VALIDATING: 5,
    ImageGenerationStage.PROBING: 15,
    ImageGenerationStage.SUBMITTING: 25,
    ImageGenerationStage.SAMPLING: 35,
    ImageGenerationStage.VERIFYING: 90,
    ImageGenerationStage.COMPLETED: 100,
}
_ERRORS: dict[type[Exception], tuple[str, str, bool]] = {
    ImageGenerationApplicationBusyError: (
        "image.busy", "Another local image operation is active.", True,
    ),
    ImageGenerationApplicationClosedError: (
        "image.closed", "Local image generation is closing.", False,
    ),
    ImageGenerationApplicationLimitError: (
        "image.limit_exceeded", "The image operation limit was exceeded.", False,
    ),
    ImageGenerationApplicationValidationError: (
        "image.invalid", "The image request is invalid.", False,
    ),
    ImageGenerationCancelledError: (
        "image.cancelled", "Local image generation was cancelled.", False,
    ),
    ImageGenerationSafetyError: (
        "image.subject_unsupported", "The image subject is unsupported.", False,
    ),
    ImageGenerationValidationError: (
        "image.invalid", "The image request is invalid.", False,
    ),
    ImageGenerationProfileError: (
        "image.profile_invalid", "The image model profile is unavailable.", False,
    ),
    ImageGenerationUnavailableError: (
        "image.unavailable", "The trusted local image runtime is unavailable.", True,
    ),
    ImageGenerationProtocolError: (
        "image.runtime_invalid", "The local image runtime response is invalid.", False,
    ),
    ImageGenerationLimitError: (
        "image.limit_exceeded", "The image resource limit was exceeded.", False,
    ),
    ImageGalleryNotFoundError: (
        "image.gallery.not_found", "The Gallery artifact was not found.", False,
    ),
    ImageGalleryStorageError: (
        "image.gallery.storage_failed", "The local image Gallery is unavailable.", True,
    ),
    ImageGalleryValidationError: (
        "image.gallery.invalid", "The image Gallery request is invalid.", False,
    ),
    ImageGalleryLimitError: (
        "image.gallery.limit_exceeded", "The image Gallery limit was exceeded.", False,
    ),
}


@dataclass(slots=True)
class _ImageTask:
    """Keep early cancel intent until the application admits the operation."""

    request_id: str
    operation_id: str
    cancel_requested: Event = field(default_factory=Event)
    admitted: bool = False


class DesktopImageCoordinator:
    """Own injected, preflighted image services and their private preview writer.

    A supplied service must come from the trusted application composition root.
    Both service and writer are required before capabilities are advertised.
    Missing configuration is an ordinary unavailable state, with no fallback
    to a shared ComfyUI installation or the speech pipe.
    """

    def __init__(
        self,
        service: ImageGenerationApplicationService | None,
        writer: ImageChannelWriter | None,
        emit: Callable[[ServerMessage], None],
    ) -> None:
        """Take teardown ownership without starting native work or reading files."""

        self._service = service
        self._writer = writer
        self._emit = emit
        self._lock = RLock()
        self._closing = False
        self._preview_failed = False
        self._generation: _ImageTask | None = None
        self._gallery_request: str | None = None
        self._service_released = service is None
        self._writer_released = writer is None
        self._service_closer: Thread | None = None
        self._writer_closer: Thread | None = None

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Advertise image methods only while both trusted owners are available."""

        with self._lock:
            return IMAGE_CAPABILITIES if self._available() else ()

    def dispatch(self, request_id: str, method: str, params: dict[str, Any]) -> None:
        """Admit one parsed image request or emit one explicit, sanitized refusal."""

        try:
            with self._lock:
                if not self._available():
                    raise ImageGenerationUnavailableError()
                if method == "image.generate":
                    if self._generation is not None:
                        raise ImageGenerationApplicationBusyError()
                    spec = self._spec(params)
                    task = _ImageTask(
                        request_id,
                        ImageGenerationApplicationService.create_operation_id(),
                    )
                    self._generation = task
                    try:
                        worker = Thread(
                            target=self._generate,
                            args=(task, spec),
                            name="elysia-desktop-image-generate",
                            daemon=True,
                        )
                        worker.start()
                    except Exception:
                        self._generation = None
                        raise
                elif method in IMAGE_CAPABILITIES:
                    # At most one verified PNG may be retained by Gallery work.
                    # A blocked pipe therefore cannot create an unbounded queue
                    # of 32 MiB buffers while stdin continues accepting requests.
                    if self._gallery_request is not None:
                        raise ImageGenerationApplicationBusyError()
                    self._gallery_request = request_id
                    try:
                        worker = Thread(
                            target=self._gallery,
                            args=(request_id, method, params),
                            name="elysia-desktop-image-gallery",
                            daemon=True,
                        )
                        worker.start()
                    except Exception:
                        self._gallery_request = None
                        raise
                else:
                    raise ImageGenerationApplicationValidationError()
        except Exception as error:
            self._error(request_id, error)

    def cancel(self, target_request_id: str) -> bool:
        """Cancel the matching generation only if publication has not won."""

        with self._lock:
            task = self._generation
            service = self._service
            if (
                self._closing or task is None or service is None
                or task.request_id != target_request_id
            ):
                return False
            outcome = service.cancel(task.operation_id)
            if outcome is ImageGenerationCancelOutcome.REQUESTED:
                task.cancel_requested.set()
                return True
            if outcome is ImageGenerationCancelOutcome.NOT_ACTIVE and not task.admitted:
                # Thread.start precedes application admission. Persist intent
                # here and reassert it in the first Core progress callback,
                # which executes after the service has installed its operation.
                task.cancel_requested.set()
                return True
            return False

    def close(self, timeout: float | None = None) -> bool:
        """Suppress output and drain teardown, preserving an already winning commit.

        The service drains its workers before releasing Core/Runtime/Gallery.
        An already admitted Gallery commit drains without the inference wait
        limit, because acknowledging shutdown mid-commit could lose a saved
        result. All other waiting shares one bound; a false return means some
        resource owners are still draining or need a later close retry.
        A pipe write can wait for Main to destroy its read end, so writer close
        has an independent daemon and cannot prevent runtime cleanup or block
        the request reader forever. Later calls retry failed teardown owners.
        """

        wait_seconds = IMAGE_SHUTDOWN_TIMEOUT_SECONDS if timeout is None else timeout
        deadline = monotonic() + max(0.0, wait_seconds)
        with self._lock:
            self._closing = True
            if self._generation is not None:
                self._generation.cancel_requested.set()
            # Stop service admission synchronously, so a later worker cannot
            # enter COMMITTING after this snapshot. A commit that already won
            # must drain before Backend acknowledges shutdown, just like Chat.
            committing = self._service is not None and self._service.request_shutdown()
            for resource in ("service", "writer"):
                released = getattr(self, f"_{resource}_released")
                closer = getattr(self, f"_{resource}_closer")
                if not released and (closer is None or not closer.is_alive()):
                    try:
                        closer = Thread(
                            target=self._close_resource,
                            args=(resource,),
                            name=f"elysia-image-close-{resource}",
                            daemon=True,
                        )
                        setattr(self, f"_{resource}_closer", closer)
                        closer.start()
                    except Exception:
                        setattr(self, f"_{resource}_closer", None)
                        logger.error("Desktop image teardown could not start.")
            closers = (self._service_closer, self._writer_closer)
        if committing:
            assert self._service is not None
            # Wait only for publication, not unrelated Gallery reads or native
            # resource teardown that happens later in service.close(). No
            # coordinator lock may span this wait: final progress needs it.
            self._service.wait_for_commit()
        for closer in closers:
            if closer is not None:
                closer.join(max(0.0, deadline - monotonic()))
        with self._lock:
            return self._service_released and self._writer_released

    def _available(self) -> bool:
        """Read the admission predicate while the lifecycle lock is held."""

        return (
            not self._closing and not self._preview_failed
            and self._service is not None and self._writer is not None
        )

    @staticmethod
    def _spec(params: dict[str, Any]) -> ImageGenerationSpec:
        """Translate the closed DTO while fixing intent inside the Backend."""

        return ImageGenerationSpec(
            profile_id=params["profileId"],
            size_preset_id=params["sizePresetId"],
            appearance=params["appearance"],
            outfit=params["outfit"],
            pose=params["pose"],
            scene=params["scene"],
            style=params["style"],
            camera=params["camera"],
            lighting=params["lighting"],
            negative_prompt=params["negativePrompt"],
            seed=params["seed"],
            original_fictional_confirmed=params["originalFictionalConfirmed"],
            intent=ImageIntent.NEW_CHARACTER,
        )

    def _generate(self, task: _ImageTask, spec: ImageGenerationSpec) -> None:
        """Run one application-owned operation and retire admission before output."""

        try:
            with self._lock:
                if self._closing or task.cancel_requested.is_set():
                    raise ImageGenerationCancelledError()
            service = self._service
            assert service is not None
            desktop_gpu = using_desktop_gpu()
            try:
                with (gpu_lease("image-generation", cancel_requested=task.cancel_requested.is_set)
                      if desktop_gpu else nullcontext()) as grant:
                    if task.cancel_requested.is_set() or (grant is not None and grant.cancelled):
                        raise ImageGenerationCancelledError()
                    try:
                        # The application call includes native finalization and
                        # durable Gallery publication. Neither completion nor a
                        # cancelled response can prematurely free this grant.
                        item = service.generate(
                            task.operation_id,
                            spec,
                            on_progress=lambda progress: self._progress(task, progress),
                        )
                    finally:
                        if desktop_gpu and not service.gpu_cleanup_complete:
                            assert grant is not None
                            grant.poison()
                            raise ImageGenerationUnavailableError()
            except GpuAdmissionCancelledError:
                raise ImageGenerationCancelledError() from None
            except GpuAdmissionError:
                raise ImageGenerationUnavailableError() from None
            response: ServerMessage = build_success_response(task.request_id, {
                "kind": "image.generation",
                "operationId": task.operation_id,
                "artifact": self._artifact(item),
            })
        except Exception as error:
            response = self._error_message(task.request_id, error)
        with self._lock:
            if self._generation is task:
                self._generation = None
            self._publish(response)

    def _progress(self, task: _ImageTask, progress: ImageGenerationProgress) -> None:
        """Reassert early cancellation and map reviewed stages to fixed wire pairs."""

        with self._lock:
            task.admitted = True
            if task.cancel_requested.is_set() or self._closing:
                assert self._service is not None
                self._service.cancel(task.operation_id)
            if self._closing:
                return
            # Ignore arbitrary percent values from collaborators; the protocol
            # defines coarse checkpoints and only the service emits completion
            # after Gallery commit. No runtime diagnostic becomes wire text.
            if type(progress) is ImageGenerationProgress:
                completed = _PROGRESS_CHECKPOINTS[progress.stage]
                self._publish(build_progress(
                    task.request_id, "image.generate", completed,
                    total=100, message=progress.stage.value,
                ))

    @staticmethod
    def _artifact(item: ImageGalleryItem) -> dict[str, Any]:
        """Project only the protocol allowlist from a private Gallery record."""

        return {
            "artifactId": item.artifact_id,
            "createdAt": item.created_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "width": item.width,
            "height": item.height,
            "byteSize": item.byte_size,
            "sha256": item.sha256,
            "mediaType": item.media_type,
            "profileId": item.profile_id,
            "seed": item.seed,
        }

    def _gallery(self, request_id: str, method: str, params: dict[str, Any]) -> None:
        """Perform bounded Gallery I/O, publishing metadata before binary output."""

        frame: PreparedImageFrame | None = None
        published = False
        writing = False
        try:
            service, writer = self._service, self._writer
            assert service is not None and writer is not None
            if method == "image.gallery.list":
                limit, offset = params["limit"], params["offset"]
                items = service.list(limit=limit + 1, offset=offset)
                result: dict[str, Any] = {
                    "kind": method,
                    "items": [self._artifact(item) for item in items[:limit]],
                    "limit": limit, "offset": offset, "hasMore": len(items) > limit,
                }
            elif method == "image.gallery.get":
                result = {"kind": method, "item": self._artifact(service.get(params["artifactId"]))}
            elif method == "image.gallery.delete":
                service.delete(params["artifactId"])
                result = {"kind": method, "artifactId": params["artifactId"], "deleted": True}
            else:
                png = service.open(params["artifactId"])
                frame = writer.prepare_png(png.data, width=png.width, height=png.height)
                result = {
                    "kind": method, "artifactId": params["artifactId"],
                    **frame.metadata.as_dict(),
                }
            response = build_success_response(request_id, result)
            with self._lock:
                if frame is None:
                    self._gallery_request = None
                published = self._publish(response)
                if frame is not None and published:
                    writing = True
            if writing and frame is not None:
                # No lifecycle/output lock may be held across a potentially
                # blocked native write: cancellation and shutdown use stdin.
                writer.write_frame(frame)
        except Exception as error:
            if isinstance(error, ImageChannelError):
                with self._lock:
                    self._preview_failed = True
            if frame is not None and not writing:
                self._discard_frame(frame)
                frame = None
            if writing:
                with self._lock:
                    self._preview_failed = True
                logger.error("Desktop image preview delivery failed.")
            elif not published:
                with self._lock:
                    if self._gallery_request == request_id:
                        self._gallery_request = None
                self._error(request_id, error)
        finally:
            if frame is not None and not writing:
                self._discard_frame(frame)
            with self._lock:
                if self._gallery_request == request_id:
                    self._gallery_request = None

    def _discard_frame(self, frame: PreparedImageFrame) -> None:
        """Discard an unpublished frame, allowing shutdown to have won first."""

        try:
            assert self._writer is not None
            self._writer.discard_frame(frame)
        except ImageChannelError:
            # Shutdown may have already discarded this exact pending frame.
            # It cannot be reused or delivered after the output gate closes.
            pass

    def _publish(self, message: ServerMessage) -> bool:
        """Emit under the lifecycle gate or suppress a frame after shutdown."""

        with self._lock:
            if self._closing:
                return False
            self._emit(message)
            return True

    def _error(self, request_id: str, error: Exception) -> None:
        """Emit one failure without retaining or logging private diagnostics."""

        self._publish(self._error_message(request_id, error))

    @staticmethod
    def _error_message(request_id: str, error: Exception) -> ServerMessage:
        """Map exact reviewed error types to application-owned public strings."""

        code, message, retryable = _ERRORS.get(type(error), (
            "image.failed", "The local image operation failed.", True,
        ))
        if isinstance(error, ImageChannelError):
            code, message, retryable = (
                "image.preview_unavailable", "The private image preview channel is unavailable.", False,
            )
        return build_error_response(request_id, code, message, retryable=retryable)

    def _close_resource(self, resource: str) -> None:
        """Close one owner independently and retain failed owners for retry."""

        owner = self._service if resource == "service" else self._writer
        try:
            assert owner is not None
            owner.close()
        except Exception:
            logger.error("Desktop image resource shutdown failed.")
            return
        with self._lock:
            setattr(self, f"_{resource}_released", True)
