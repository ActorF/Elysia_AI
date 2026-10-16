"""Coordinate cancellable generation with durable path-private Gallery access.

This application boundary combines the synchronous image-generation Core with
the durable Gallery.  One state transition linearizes cancellation against
Gallery publication: cancellation may win while inference is running, or the
operation may enter its non-cancellable commit phase, but callers can never be
told that a committed artifact was cancelled.  Core completion progress is
withheld until that durable commit succeeds.

Only opaque operation and artifact identifiers cross this surface.  Model
paths, Gallery paths, runtime paths, and the private positive prompt remain in
the trusted Backend composition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Condition, RLock, get_ident
from typing import TypeVar
from uuid import uuid4
import re

from .domain import ImageGenerationSpec
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
from .gallery import (
    ImageGalleryError,
    ImageGalleryItem,
    ImageGalleryLimitError,
    ImageGalleryNotFoundError,
    ImageGalleryStorageError,
    ImageGalleryStore,
    ImageGalleryValidationError,
)
from .png import CanonicalPng
from .service import (
    GeneratedImageResult,
    ImageGenerationCore,
    ImageGenerationProgress,
    ImageGenerationStage,
)


_OPERATION_ID_PATTERN = re.compile(r"^imageop_[0-9a-f]{32}$")
_MAX_RETIRED_OPERATION_IDS = 65_536
_T = TypeVar("_T")
_IMAGE_ERROR_MESSAGES: dict[type[ImageGenerationError], str] = {
    ImageGenerationError: "Local image generation failed.",
    ImageGenerationValidationError: "The image generation request is invalid.",
    ImageGenerationSafetyError: (
        "The image generation request is outside the supported subject boundary."
    ),
    ImageGenerationProfileError: "The selected image model profile is invalid.",
    ImageGenerationUnavailableError: "Local image generation is unavailable.",
    ImageGenerationProtocolError: "The local image runtime response is invalid.",
    ImageGenerationLimitError: "The image generation resource limit was exceeded.",
    ImageGenerationCancelledError: "Local image generation was cancelled.",
    ImageGenerationFailedError: "Local image generation failed.",
}
_GALLERY_ERROR_MESSAGES: dict[type[ImageGalleryError], str] = {
    ImageGalleryError: "The local image Gallery operation failed.",
    ImageGalleryStorageError: "The local image Gallery is unavailable.",
    ImageGalleryValidationError: "The image Gallery request is invalid.",
    ImageGalleryLimitError: "The image Gallery limit was exceeded.",
    ImageGalleryNotFoundError: "The Gallery artifact was not found.",
}


class ImageGenerationApplicationError(Exception):
    """Base class for stable application-layer image failures."""


class ImageGenerationApplicationValidationError(ImageGenerationApplicationError):
    """Report an invalid opaque operation identifier or call argument."""


class ImageGenerationApplicationBusyError(ImageGenerationApplicationError):
    """Report that the single managed generation runtime is already in use."""


class ImageGenerationApplicationClosedError(ImageGenerationApplicationError):
    """Report that application shutdown has begun or completed."""


class ImageGenerationApplicationLimitError(ImageGenerationApplicationError):
    """Report exhaustion of the bounded single-use operation-ID history."""


class ImageGenerationCancelOutcome(StrEnum):
    """Describe whether cancellation won the publication boundary."""

    REQUESTED = "requested"
    TOO_LATE = "too-late"
    NOT_ACTIVE = "not-active"


class _GenerationPhase(StrEnum):
    """Track the active request around its single publication boundary."""

    RUNNING = "running"
    CANCEL_REQUESTED = "cancel-requested"
    COMMITTING = "committing"


@dataclass(slots=True)
class _ActiveGeneration:
    """Hold mutable state for the one process-owned generation operation."""

    operation_id: str
    owner_thread_id: int
    phase: _GenerationPhase = _GenerationPhase.RUNNING


class ImageGenerationApplicationService:
    """Publish generated images atomically and expose a path-private Gallery.

    The managed Core owns one private runtime, so this service admits only one
    generation at a time.  Gallery metadata and verified-byte operations may
    run concurrently; shutdown waits for every admitted operation before it
    releases either owned collaborator.
    """

    def __init__(
        self,
        core: ImageGenerationCore,
        gallery: ImageGalleryStore,
    ) -> None:
        """Capture the Core and Gallery without starting native work."""

        if core is None:
            raise TypeError("core is required.")
        if gallery is None:
            raise TypeError("gallery is required.")
        if not callable(getattr(core, "generate", None)) or not callable(
            getattr(core, "close", None)
        ):
            raise TypeError("core does not implement the required contract.")
        gallery_methods = (
            "create",
            "list",
            "get",
            "open_verified",
            "delete",
            "close",
        )
        if not all(callable(getattr(gallery, name, None)) for name in gallery_methods):
            raise TypeError("gallery does not implement the required contract.")
        self._core = core
        self._gallery = gallery
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._active: _ActiveGeneration | None = None
        self._retired_operation_ids: set[str] = set()
        self._gallery_operations = 0
        self._closing = False
        self._closed = False
        self._teardown_in_progress = False
        self._teardown_attempt = 0
        self._teardown_results: dict[int, bool] = {}
        self._teardown_waiters: dict[int, int] = {}
        self._core_released = False
        self._gallery_released = False

    def __enter__(self) -> "ImageGenerationApplicationService":
        """Return this open application service for context-managed use."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Close the Core and Gallery when leaving a context."""

        self.close()

    @staticmethod
    def create_operation_id() -> str:
        """Mint one opaque identifier suitable for generation and cancellation."""

        return f"imageop_{uuid4().hex}"

    def generate(
        self,
        operation_id: str,
        spec: ImageGenerationSpec,
        *,
        on_progress: Callable[[ImageGenerationProgress], None] | None = None,
    ) -> ImageGalleryItem:
        """Generate and durably publish one image under a cancellable operation.

        ``cancel`` can win only before this method changes the active phase to
        ``COMMITTING``.  After that transition, cancellation reports
        ``TOO_LATE`` and this method returns the committed Gallery item (or a
        genuine Gallery failure), never a contradictory cancellation result.
        """

        safe_operation_id = self._validate_operation_id(operation_id)
        if type(spec) is not ImageGenerationSpec:
            raise TypeError("spec must be ImageGenerationSpec.")
        if on_progress is not None and not callable(on_progress):
            raise TypeError("on_progress must be callable.")
        operation = self._begin_generation(safe_operation_id)
        try:
            result = self._generate_core(
                operation,
                spec,
                on_progress,
            )

            self._begin_commit(operation)
            # The private manifest retains only normalized user-supplied
            # positive components.  System prompt anchors, model-dialect
            # prefixes, and the negative prompt remain compiler concerns and
            # are deliberately not represented as a reproducibility record.
            private_prompt = "\n".join(spec.prompt_components)
            item = self._create_gallery_item(result, private_prompt)

            # The Core's 100% event is intentionally suppressed until the
            # authoritative Gallery rename has committed.  Callback failure is
            # advisory and cannot reinterpret a successful durable commit.
            self._notify(
                on_progress,
                ImageGenerationProgress(
                    stage=ImageGenerationStage.COMPLETED,
                    percent=100,
                ),
            )
            return item
        finally:
            self._finish_generation(operation)

    def cancel(self, operation_id: str) -> ImageGenerationCancelOutcome:
        """Request cancellation or report that Gallery commit already won."""

        safe_operation_id = self._validate_operation_id(operation_id)
        with self._condition:
            operation = self._active
            if operation is None or operation.operation_id != safe_operation_id:
                return ImageGenerationCancelOutcome.NOT_ACTIVE
            if operation.phase is _GenerationPhase.COMMITTING:
                return ImageGenerationCancelOutcome.TOO_LATE
            operation.phase = _GenerationPhase.CANCEL_REQUESTED
            return ImageGenerationCancelOutcome.REQUESTED

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ImageGalleryItem, ...]:
        """Return one newest-first page of path-free Gallery metadata."""

        return self._call_gallery(
            lambda: self._gallery.list(limit=limit, offset=offset),
        )

    def get(self, artifact_id: str) -> ImageGalleryItem:
        """Return path-free metadata for one opaque Gallery artifact."""

        return self._call_gallery(lambda: self._gallery.get(artifact_id))

    def open(self, artifact_id: str) -> CanonicalPng:
        """Return canonical PNG bytes after Gallery integrity verification."""

        return self._call_gallery(
            lambda: self._gallery.open_verified(artifact_id),
        )

    def delete(self, artifact_id: str) -> None:
        """Delete one Gallery artifact selected only by its opaque identifier."""

        self._call_gallery(lambda: self._gallery.delete(artifact_id))

    def request_shutdown(self) -> bool:
        """Stop admission and cancel inference without waiting or releasing owners.

        Return whether an active Gallery commit already owns publication.  The
        admission gate and cancellation phase change under the commit lock, so
        a false result guarantees that running inference cannot start a later
        commit.  Shutdown coordinators can therefore distinguish a commit that
        must finish from cancellable inference before starting asynchronous
        resource teardown.  Calls are idempotent and never reopen admission.

        A generation callback cannot request shutdown of its own service.  This
        preserves the same reentrancy contract as ``close`` without allowing a
        rejected callback request to partially close the admission gate.
        """

        with self._condition:
            active = self._active
            if active is not None and active.owner_thread_id == get_ident():
                raise ImageGenerationApplicationBusyError(
                    "Image generation cannot close from its own callback."
                )
            if not self._closing:
                self._closing = True
                active = self._active
                if (
                    active is not None
                    and active.phase is not _GenerationPhase.COMMITTING
                ):
                    active.phase = _GenerationPhase.CANCEL_REQUESTED
            return (
                active is not None
                and active.phase is _GenerationPhase.COMMITTING
            )

    def close(self) -> None:
        """Cancel running inference, drain admitted work, and release resources.

        A progress callback executes on the generation thread.  Closing from
        that callback would wait on itself, so the call is rejected while the
        callback failure remains advisory to generation.
        """

        self.request_shutdown()
        with self._condition:
            # Every closer waits on the real drain predicate.  There is no
            # distinguished drain owner whose interruption could strand the
            # remaining callers behind a separate flag.
            while self._active is not None or self._gallery_operations:
                self._condition.wait()
            self._closed = True

        self._close_resources()

    def wait_for_commit(self) -> None:
        """Wait only for the currently committing operation to retire, if any.

        This durability barrier never stops admission or waits for inference,
        Gallery reads, or resource cleanup.  A shutdown coordinator must call
        ``request_shutdown`` first so a later generation cannot start another
        commit after this snapshot.  Retain the exact operation identity so
        unrelated work admitted by other callers cannot prolong this wait.
        A commit callback cannot wait for its own operation to retire.
        """

        with self._condition:
            operation = self._active
            if (
                operation is None
                or operation.phase is not _GenerationPhase.COMMITTING
            ):
                return
            if operation.owner_thread_id == get_ident():
                raise ImageGenerationApplicationBusyError(
                    "Image generation cannot wait for its own commit."
                )
            while self._active is operation:
                self._condition.wait()

    @staticmethod
    def _validate_operation_id(value: object) -> str:
        """Accept only an opaque operation ID and never interpret it as a path."""

        if type(value) is not str or _OPERATION_ID_PATTERN.fullmatch(value) is None:
            raise ImageGenerationApplicationValidationError(
                "Image generation operation ID is invalid."
            )
        return value

    def _begin_generation(self, operation_id: str) -> _ActiveGeneration:
        """Admit one generation while excluding shutdown and concurrent runs."""

        with self._condition:
            if self._closing or self._closed:
                raise ImageGenerationApplicationClosedError(
                    "Local image generation is closed."
                )
            if self._active is not None:
                raise ImageGenerationApplicationBusyError(
                    "Another local image generation operation is active."
                )
            if operation_id in self._retired_operation_ids:
                # IDs are single-use so a delayed cancellation from an old
                # renderer request can never target a later generation that
                # accidentally reused the same caller-controlled value.
                raise ImageGenerationApplicationValidationError(
                    "Image generation operation ID was already used."
                )
            if len(self._retired_operation_ids) >= _MAX_RETIRED_OPERATION_IDS:
                # Keeping tombstones for the process lifetime is what makes
                # the late-cancel guarantee exact.  Fail closed at a generous
                # fixed bound rather than silently forgetting old IDs.
                raise ImageGenerationApplicationLimitError(
                    "Image generation operation history is full."
                )
            operation = _ActiveGeneration(
                operation_id=operation_id,
                owner_thread_id=get_ident(),
            )
            self._active = operation
            return operation

    def _generate_core(
        self,
        operation: _ActiveGeneration,
        spec: ImageGenerationSpec,
        callback: Callable[[ImageGenerationProgress], None] | None,
    ) -> GeneratedImageResult:
        """Invoke the Core while discarding unreviewed exception object graphs."""

        failure: Exception | None = None
        try:
            result = self._core.generate(
                spec,
                cancel_requested=lambda: self._is_cancel_requested(operation),
                on_progress=lambda progress: self._forward_core_progress(
                    callback,
                    progress,
                ),
            )
        except ImageGenerationError as error:
            failure = self._detached_image_error(error)
        except Exception:
            # Do not raise the replacement inside this handler: ``from None``
            # suppresses display but still retains the original exception in
            # ``__context__``, where paths and runtime bodies remain readable.
            failure = ImageGenerationApplicationError(
                "Local image generation failed."
            )
        else:
            return result
        assert failure is not None
        raise failure

    def _create_gallery_item(
        self,
        result: GeneratedImageResult,
        private_prompt: str,
    ) -> ImageGalleryItem:
        """Commit one result while severing unexpected exception context."""

        failure: Exception | None = None
        try:
            item = self._gallery.create(result, private_prompt=private_prompt)
        except ImageGalleryError as error:
            failure = self._detached_gallery_error(error)
        except Exception:
            failure = ImageGenerationApplicationError(
                "The generated image could not be saved."
            )
        else:
            return item
        assert failure is not None
        raise failure

    @staticmethod
    def _detached_image_error(error: ImageGenerationError) -> Exception:
        """Preserve a reviewed error type without retaining its object graph.

        Lower layers intentionally expose stable exception classes, but a
        Python exception can still carry a private ``cause`` or ``context``.
        Exact-type reconstruction with application-owned text retains the UI
        mapping contract without trusting messages, args, tracebacks, paths,
        or nested exception objects supplied by a collaborator.
        """

        error_type = type(error)
        message = _IMAGE_ERROR_MESSAGES.get(error_type)
        if message is None:
            return ImageGenerationApplicationError(
                "Local image generation failed."
            )
        return error_type(message)

    @staticmethod
    def _detached_gallery_error(error: ImageGalleryError) -> Exception:
        """Preserve a known Gallery error type with a new path-free payload."""

        error_type = type(error)
        message = _GALLERY_ERROR_MESSAGES.get(error_type)
        if message is None:
            return ImageGenerationApplicationError(
                "The local image Gallery operation failed."
            )
        return error_type(message)

    def _close_resources(self) -> None:
        """Run or join one teardown attempt and retry only failed owners later."""

        owns_attempt = False
        joined_attempt_succeeded = False
        joined_attempt = False
        with self._condition:
            if self._core_released and self._gallery_released:
                return
            if self._teardown_in_progress:
                observed_attempt = self._teardown_attempt
                self._teardown_waiters[observed_attempt] = (
                    self._teardown_waiters.get(observed_attempt, 0) + 1
                )
                try:
                    while observed_attempt not in self._teardown_results:
                        self._condition.wait()
                    joined_attempt_succeeded = self._teardown_results[
                        observed_attempt
                    ]
                    joined_attempt = True
                finally:
                    remaining = self._teardown_waiters[observed_attempt] - 1
                    if remaining:
                        self._teardown_waiters[observed_attempt] = remaining
                    else:
                        del self._teardown_waiters[observed_attempt]
                        self._teardown_results.pop(observed_attempt, None)
            else:
                # Results without registered waiters belong to completed calls
                # and can be dropped before a distinct retry attempt begins.
                for attempt in tuple(self._teardown_results):
                    if self._teardown_waiters.get(attempt, 0) == 0:
                        del self._teardown_results[attempt]
                self._teardown_attempt += 1
                observed_attempt = self._teardown_attempt
                self._teardown_in_progress = True
                close_core = not self._core_released
                close_gallery = not self._gallery_released
                owns_attempt = True

        if joined_attempt and not joined_attempt_succeeded:
            raise ImageGenerationApplicationError(
                "Local image generation resources could not be closed."
            )
        if joined_attempt or not owns_attempt:
            return

        core_released = not close_core or self._close_one(self._core.close)
        gallery_released = not close_gallery or self._close_one(self._gallery.close)
        failed = not core_released or not gallery_released
        with self._condition:
            if core_released:
                self._core_released = True
            if gallery_released:
                self._gallery_released = True
            self._teardown_results[observed_attempt] = not failed
            self._teardown_in_progress = False
            self._condition.notify_all()
        if failed:
            raise ImageGenerationApplicationError(
                "Local image generation resources could not be closed."
            )

    @staticmethod
    def _close_one(close: Callable[[], None]) -> bool:
        """Return whether one collaborator proved release without retaining errors."""

        try:
            close()
        except Exception:
            return False
        return True

    def _begin_commit(self, operation: _ActiveGeneration) -> None:
        """Linearize cancellation against the irreversible Gallery create call."""

        with self._condition:
            if self._active is not operation:
                raise ImageGenerationApplicationError(
                    "Image generation state became inconsistent."
                )
            if operation.phase is _GenerationPhase.CANCEL_REQUESTED:
                raise ImageGenerationCancelledError(
                    "Local image generation was cancelled."
                )
            if operation.phase is not _GenerationPhase.RUNNING:
                raise ImageGenerationApplicationError(
                    "Image generation state became inconsistent."
                )
            operation.phase = _GenerationPhase.COMMITTING

    def _finish_generation(self, operation: _ActiveGeneration) -> None:
        """Release the active slot and wake shutdown on every terminal path."""

        with self._condition:
            if self._active is operation:
                self._retired_operation_ids.add(operation.operation_id)
                self._active = None
                self._condition.notify_all()

    def _is_cancel_requested(self, operation: _ActiveGeneration) -> bool:
        """Read the cooperative cancellation bit without exposing mutable state."""

        with self._condition:
            return operation.phase is _GenerationPhase.CANCEL_REQUESTED

    @staticmethod
    def _notify(
        callback: Callable[[ImageGenerationProgress], None] | None,
        progress: ImageGenerationProgress,
    ) -> None:
        """Treat renderer progress as advisory rather than control flow."""

        if callback is None:
            return
        try:
            callback(progress)
        except Exception:
            return

    def _forward_core_progress(
        self,
        callback: Callable[[ImageGenerationProgress], None] | None,
        progress: ImageGenerationProgress,
    ) -> None:
        """Suppress premature Core completion until the Gallery commit exists."""

        if (
            type(progress) is ImageGenerationProgress
            and progress.stage is ImageGenerationStage.COMPLETED
        ):
            return
        self._notify(callback, progress)

    def _begin_gallery_operation(self) -> None:
        """Acquire a shutdown-aware lease for one Gallery delegation."""

        with self._condition:
            if self._closing or self._closed:
                raise ImageGenerationApplicationClosedError(
                    "Local image generation is closed."
                )
            self._gallery_operations += 1

    def _finish_gallery_operation(self) -> None:
        """Release one Gallery lease and wake a waiting shutdown."""

        with self._condition:
            self._gallery_operations -= 1
            self._condition.notify_all()

    def _call_gallery(self, operation: Callable[[], _T]) -> _T:
        """Delegate one Gallery call while sanitizing unexpected exceptions."""

        self._begin_gallery_operation()
        try:
            failure: Exception | None = None
            try:
                result = operation()
            except ImageGalleryError as error:
                failure = self._detached_gallery_error(error)
            except Exception:
                failure = ImageGenerationApplicationError(
                    "The local image Gallery operation failed."
                )
            else:
                return result
            assert failure is not None
            raise failure
        finally:
            self._finish_gallery_operation()


__all__ = [
    "ImageGenerationApplicationBusyError",
    "ImageGenerationApplicationClosedError",
    "ImageGenerationApplicationError",
    "ImageGenerationApplicationLimitError",
    "ImageGenerationApplicationService",
    "ImageGenerationApplicationValidationError",
    "ImageGenerationCancelOutcome",
]
