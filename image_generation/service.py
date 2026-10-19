"""Orchestrate one synchronous trusted local image-generation operation.

This core owns validation, profile resolution, fixed workflow compilation,
runtime correlation, and output verification.  It intentionally does not own
durable Gallery storage, Desktop events, queueing, or the process-wide GPU
lease; those lifecycle concerns are added by the next integration layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from .comfyui import ComfyOutputDescriptor, ComfyPromptHandle
from .domain import ImageGenerationSpec
from .exceptions import (
    ImageGenerationCancelledError,
    ImageGenerationProtocolError,
    ImageGenerationUnavailableError,
)
from .png import CanonicalPng, canonicalize_png
from .profiles import ModelLoraProfile, ModelProfile, ModelProfileCatalog
from .workflow import CompiledWorkflow, compile_txt2img_workflow


class ImageGenerationStage(StrEnum):
    """Describe monotonic high-level progress without forwarding runtime nodes."""

    VALIDATING = "validating"
    PROBING = "probing"
    SUBMITTING = "submitting"
    SAMPLING = "sampling"
    VERIFYING = "verifying"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ImageGenerationProgress:
    """Expose a stable stage and coarse monotonic completion percentage."""

    stage: ImageGenerationStage
    percent: int

    def __post_init__(self) -> None:
        """Keep progress bounded and free of arbitrary runtime messages."""

        if type(self.stage) is not ImageGenerationStage:
            raise ValueError("stage must be ImageGenerationStage.")
        if type(self.percent) is not int or not 0 <= self.percent <= 100:
            raise ValueError("percent must be from 0 through 100.")


@dataclass(frozen=True, slots=True)
class GeneratedImageResult:
    """Return verified bytes and reproducibility metadata to trusted storage."""

    job_id: str
    image: CanonicalPng
    profile_id: str
    profile_digest: str
    workflow_id: str
    workflow_version: int
    workflow_digest: str
    seed: int
    created_at: datetime


class _ManagedRuntimeCapability:
    """Mark sessions issued by the reviewed private-runtime supervisor."""


_MANAGED_RUNTIME_CAPABILITY = _ManagedRuntimeCapability()


class ComfyUiClient(Protocol):
    """Describe one managed private runtime session consumed by the core."""

    @property
    def is_managed_private_runtime(self) -> bool:
        """Confirm that runtime artifacts and lifecycle are privately owned."""

    @property
    def managed_runtime_capability(self) -> object:
        """Return the supervisor-issued in-process admission capability."""

    def ensure_ready(self, profile: ModelProfile, *, lora: ModelLoraProfile | None = None) -> None:
        """Verify the checkpoint and optional exact reviewed LoRA before submission."""

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Mint the correlated handle before submission can create native work."""

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> object | None:
        """Submit one graph and return only the contractually required null."""

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Wait for exactly one output descriptor or a stable failure."""

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Fetch and canonicalize the selected runtime output."""

    def finalize(self, handle: ComfyPromptHandle) -> object | None:
        """Erase private state and return only the contractually required null."""

    def close(self) -> None:
        """Permanently stop and release the managed runtime owner."""


class ImageGenerationCore:
    """Generate one verified image through an injected trusted profile catalog."""

    def __init__(
        self,
        profiles: ModelProfileCatalog,
        client: ComfyUiClient,
    ) -> None:
        """Capture collaborators without probing the runtime or loading models."""

        if type(profiles) is not ModelProfileCatalog:
            raise TypeError("profiles must be ModelProfileCatalog.")
        # The identity capability prevents an ordinary raw adapter from opting
        # in accidentally with a boolean.  It is an in-process composition
        # token, not a sandbox against malicious Python code.
        if (
            getattr(client, "is_managed_private_runtime", None) is not True
            or getattr(client, "managed_runtime_capability", None)
            is not _MANAGED_RUNTIME_CAPABILITY
        ):
            raise ImageGenerationUnavailableError(
                "A managed private image runtime is required."
            )
        required_methods = (
            "ensure_ready",
            "prepare",
            "submit_prepared",
            "wait_for_output",
            "fetch_png",
            "finalize",
            "close",
        )
        if not all(callable(getattr(client, name, None)) for name in required_methods):
            raise TypeError("client does not implement the ComfyUiClient contract.")
        # SaveImage and /history persist sensitive prompts and images inside a
        # normal shared ComfyUI process.  Only a supervisor that owns the child
        # process and its isolated directories may cross this composition root.
        self._profiles = profiles
        self._client = client

    def close(self) -> None:
        """Permanently close the injected managed runtime owner."""

        self._client.close()

    @property
    def gpu_cleanup_complete(self) -> bool:
        """Forward authoritative native quiescence, failing closed for unknown owners."""

        try:
            return getattr(self._client, "gpu_cleanup_complete", False) is True
        except BaseException:
            return False

    def __enter__(self) -> "ImageGenerationCore":
        """Return this synchronous generation composition root."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Close the managed runtime when leaving a context."""

        self.close()

    @staticmethod
    def _cancelled(cancel_requested: Callable[[], bool] | None) -> bool:
        """Read cooperative cancellation only at explicit commit boundaries."""

        return cancel_requested is not None and cancel_requested()

    @staticmethod
    def _notify(
        callback: Callable[[ImageGenerationProgress], None] | None,
        stage: ImageGenerationStage,
        percent: int,
    ) -> None:
        """Publish optional progress without granting callbacks control flow."""

        if callback is None:
            return
        try:
            callback(ImageGenerationProgress(stage=stage, percent=percent))
        except Exception:
            # Progress is advisory.  A UI callback failure must neither publish
            # an unverified image nor strand a completed native inference.
            return

    def generate(
        self,
        spec: ImageGenerationSpec,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[ImageGenerationProgress], None] | None = None,
    ) -> GeneratedImageResult:
        """Synchronously produce one verified image without persisting it.

        The injected session owns its ComfyUI child process and isolated
        runtime directories.  That ownership is required so cancellation and
        cleanup cannot leak prompts or images into a shared ComfyUI instance.
        """

        if type(spec) is not ImageGenerationSpec:
            raise TypeError("spec must be ImageGenerationSpec.")
        if cancel_requested is not None and not callable(cancel_requested):
            raise TypeError("cancel_requested must be callable.")
        if on_progress is not None and not callable(on_progress):
            raise TypeError("on_progress must be callable.")

        self._notify(on_progress, ImageGenerationStage.VALIDATING, 5)
        profile = self._profiles.get(spec.profile_id)
        job_id = str(uuid4())
        workflow = compile_txt2img_workflow(spec, profile, job_id=job_id)
        if self._cancelled(cancel_requested):
            raise ImageGenerationCancelledError(
                "Local image generation was cancelled."
            )

        self._notify(on_progress, ImageGenerationStage.PROBING, 15)
        if spec.lora is None:
            self._client.ensure_ready(profile)
        else:
            self._client.ensure_ready(profile, lora=profile.lora(spec.lora.lora_id))
        if self._cancelled(cancel_requested):
            raise ImageGenerationCancelledError(
                "Local image generation was cancelled."
            )

        self._notify(on_progress, ImageGenerationStage.SUBMITTING, 25)
        prepared = self._client.prepare(workflow)
        if type(prepared) is not ComfyPromptHandle:
            raise ImageGenerationProtocolError(
                "Managed image runtime returned an invalid prompt handle."
            )
        handle = prepared
        try:
            if handle.output_filename_prefix != workflow.output_filename_prefix:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned an invalid prompt handle."
                )
            if self._cancelled(cancel_requested):
                raise ImageGenerationCancelledError(
                    "Local image generation was cancelled."
                )
            submitted = self._client.submit_prepared(handle, workflow)
            if submitted is not None:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned an invalid submission result."
                )
            self._notify(on_progress, ImageGenerationStage.SAMPLING, 35)
            descriptor = self._client.wait_for_output(
                handle,
                workflow.expected_output_node_id,
                cancel_requested=cancel_requested,
            )
            if type(descriptor) is not ComfyOutputDescriptor:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned an invalid output descriptor."
                )
            if self._cancelled(cancel_requested):
                raise ImageGenerationCancelledError(
                    "Local image generation was cancelled."
                )

            self._notify(on_progress, ImageGenerationStage.VERIFYING, 90)
            candidate = self._client.fetch_png(
                descriptor,
                expected_width=workflow.expected_width,
                expected_height=workflow.expected_height,
            )
            if type(candidate) is not CanonicalPng:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned an invalid canonical image."
                )
            # Re-parse at the composition root so a buggy wrapper cannot forge
            # dimensions, digest, media type, or the absence of PNG metadata.
            image = canonicalize_png(
                candidate.data,
                expected_width=workflow.expected_width,
                expected_height=workflow.expected_height,
            )
            if candidate != image:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned inconsistent image metadata."
                )
            # The verified bytes become publishable only after this last check.
            # A cancellation that wins here discards the bytes instead of creating
            # an artifact the user explicitly cancelled.
            if self._cancelled(cancel_requested):
                raise ImageGenerationCancelledError(
                    "Local image generation was cancelled."
                )
            result = GeneratedImageResult(
                job_id=job_id,
                image=image,
                profile_id=profile.profile_id,
                profile_digest=profile.profile_digest,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                workflow_digest=workflow.workflow_digest,
                seed=spec.seed,
                created_at=datetime.now(timezone.utc),
            )
        finally:
            # Publication remains inside this scope: returning is impossible
            # until the supervisor proves terminal state and removes ComfyUI's
            # persisted output/history for this correlated handle.
            finalized = self._client.finalize(handle)
            if finalized is not None:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned an invalid cleanup result."
                )
        self._notify(on_progress, ImageGenerationStage.COMPLETED, 100)
        return result


__all__ = [
    "ComfyUiClient",
    "GeneratedImageResult",
    "ImageGenerationCore",
    "ImageGenerationProgress",
    "ImageGenerationStage",
]
