"""Define trusted model profiles for fixed local ComfyUI workflows.

Profiles are application-owned configuration rather than renderer or language-
model input.  They bind a logical ID to one checkpoint declaration, prompt
dialect, size allowlist, and conservative sampler defaults.  This prevents a
request from turning filenames or arbitrary ComfyUI nodes into an execution
channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
import unicodedata
from typing import Final

from .domain import validate_public_id
from .exceptions import (
    ImageGenerationProfileError,
    ImageGenerationValidationError,
)


MAX_PROFILE_DISPLAY_NAME_CODEPOINTS: Final = 80
MAX_CHECKPOINT_NAME_CODEPOINTS: Final = 240
MAX_MODEL_PROVENANCE_CODEPOINTS: Final = 512
MAX_IMAGE_EDGE_PIXELS: Final = 2_048
MAX_IMAGE_PIXELS: Final = 4_194_304
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_SUFFIX: Final = ".safetensors"
_SAMPLERS: Final = frozenset({
    "dpmpp_2m",
    "dpmpp_2m_sde",
    "dpmpp_3m_sde",
    "euler",
    "euler_ancestral",
    "heun",
    "lms",
})
_SCHEDULERS: Final = frozenset({
    "normal",
    "karras",
    "exponential",
    "sgm_uniform",
    "simple",
    "ddim_uniform",
    "beta",
})


class ModelArchitecture(StrEnum):
    """Identify the checkpoint architecture supported by a workflow compiler."""

    SDXL = "sdxl"


class PromptDialect(StrEnum):
    """Choose the reviewed prompt grammar expected by a model family."""

    SDXL_NATURAL = "sdxl-natural"
    PONY_TAGS = "pony-tags"
    ILLUSTRIOUS_TAGS = "illustrious-tags"


def _profile_text(value: object, field_name: str, maximum: int) -> str:
    """Validate one human-readable trusted profile field."""

    if type(value) is not str:
        raise ImageGenerationProfileError(f"{field_name} must be text.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in normalized
        )
    ):
        raise ImageGenerationProfileError(f"{field_name} is invalid.")
    return normalized


def _checkpoint_name(value: object, field_name: str = "checkpoint_name") -> str:
    """Return one relative safetensors asset key without traversal aliases."""

    if type(value) is not str:
        raise ImageGenerationProfileError(f"{field_name} must be text.")
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > MAX_CHECKPOINT_NAME_CODEPOINTS
        or not normalized.lower().endswith(_CHECKPOINT_SUFFIX)
        or ":" in normalized
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in normalized)
    ):
        raise ImageGenerationProfileError(f"{field_name} is invalid.")
    windows = PureWindowsPath(normalized)
    posix = PurePosixPath(normalized.replace("\\", "/"))
    # PurePath erases dot segments and duplicate separators; inspect the raw
    # segments as well so configuration cannot retain those filesystem aliases.
    if (
        windows.is_absolute()
        or posix.is_absolute()
        or any(part in {"", ".", ".."} for part in normalized.replace("\\", "/").split("/"))
    ):
        raise ImageGenerationProfileError(
            f"{field_name} must be a relative ComfyUI model key."
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ImageSizePreset:
    """Bind a logical size choice to one bounded latent canvas."""

    preset_id: str
    width: int
    height: int

    def __post_init__(self) -> None:
        """Reject dimensions that are incompatible with latent downsampling."""

        try:
            validate_public_id(self.preset_id, "preset_id")
        except ImageGenerationValidationError as error:
            raise ImageGenerationProfileError(str(error)) from error
        for value, field_name in ((self.width, "width"), (self.height, "height")):
            if (
                type(value) is not int
                or value < 512
                or value > MAX_IMAGE_EDGE_PIXELS
                or value % 64 != 0
            ):
                raise ImageGenerationProfileError(
                    f"{field_name} must be a 512-2048 multiple of 64."
                )
        if self.width * self.height > MAX_IMAGE_PIXELS:
            raise ImageGenerationProfileError(
                "The image size exceeds the first-version pixel budget."
            )


@dataclass(frozen=True, slots=True)
class ModelLoraProfile:
    """Pin one reviewed SDXL LoRA below the trusted models/loras directory.

    This configuration is application-owned.  Requests may select ``lora_id``
    and bounded strengths, but cannot supply this model key, hash, or provenance.
    Trigger words are deliberately user prompt text, never injected implicitly.
    """

    lora_id: str
    display_name: str
    architecture: ModelArchitecture
    lora_name: str
    lora_sha256: str
    lora_source: str
    lora_license: str

    def __post_init__(self) -> None:
        """Reject unsafe asset keys, missing provenance, and incompatible models."""

        try:
            validate_public_id(self.lora_id, "lora_id")
        except ImageGenerationValidationError as error:
            raise ImageGenerationProfileError(str(error)) from error
        object.__setattr__(self, "display_name", _profile_text(
            self.display_name, "display_name", MAX_PROFILE_DISPLAY_NAME_CODEPOINTS,
        ))
        if type(self.architecture) is not ModelArchitecture:
            raise ImageGenerationProfileError("LoRA architecture is unsupported.")
        object.__setattr__(self, "lora_name", _checkpoint_name(self.lora_name, "lora_name"))
        if type(self.lora_sha256) is not str or _SHA256_PATTERN.fullmatch(self.lora_sha256) is None:
            raise ImageGenerationProfileError("lora_sha256 must be a lowercase SHA-256 digest.")
        for field_name in ("lora_source", "lora_license"):
            object.__setattr__(self, field_name, _profile_text(
                getattr(self, field_name), field_name, MAX_MODEL_PROVENANCE_CODEPOINTS,
            ))


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Pin one reviewed SDXL-derived model and its execution defaults."""

    profile_id: str
    display_name: str
    architecture: ModelArchitecture
    prompt_dialect: PromptDialect
    checkpoint_name: str
    checkpoint_sha256: str
    checkpoint_source: str
    checkpoint_license: str
    size_presets: tuple[ImageSizePreset, ...]
    default_size_preset_id: str
    sampler_name: str = "dpmpp_2m"
    scheduler: str = "karras"
    steps: int = 28
    cfg_scale: float = 6.0
    loras: tuple[ModelLoraProfile, ...] = ()

    def __post_init__(self) -> None:
        """Validate compatibility, provenance, and deterministic defaults."""

        try:
            validate_public_id(self.profile_id, "profile_id")
            validate_public_id(
                self.default_size_preset_id,
                "default_size_preset_id",
            )
        except ImageGenerationValidationError as error:
            raise ImageGenerationProfileError(str(error)) from error
        object.__setattr__(
            self,
            "display_name",
            _profile_text(
                self.display_name,
                "display_name",
                MAX_PROFILE_DISPLAY_NAME_CODEPOINTS,
            ),
        )
        if type(self.architecture) is not ModelArchitecture:
            raise ImageGenerationProfileError("architecture is unsupported.")
        if type(self.prompt_dialect) is not PromptDialect:
            raise ImageGenerationProfileError("prompt_dialect is unsupported.")
        object.__setattr__(
            self,
            "checkpoint_name",
            _checkpoint_name(self.checkpoint_name),
        )
        if (
            type(self.checkpoint_sha256) is not str
            or _SHA256_PATTERN.fullmatch(self.checkpoint_sha256) is None
        ):
            raise ImageGenerationProfileError(
                "checkpoint_sha256 must be a lowercase SHA-256 digest."
            )
        for field_name in ("checkpoint_source", "checkpoint_license"):
            object.__setattr__(
                self,
                field_name,
                _profile_text(
                    getattr(self, field_name),
                    field_name,
                    MAX_MODEL_PROVENANCE_CODEPOINTS,
                ),
            )
        if (
            type(self.size_presets) is not tuple
            or not self.size_presets
            or not all(type(item) is ImageSizePreset for item in self.size_presets)
        ):
            raise ImageGenerationProfileError(
                "size_presets must contain reviewed ImageSizePreset values."
            )
        preset_ids = tuple(item.preset_id for item in self.size_presets)
        if len(set(preset_ids)) != len(preset_ids):
            raise ImageGenerationProfileError("size_presets contains duplicate IDs.")
        if self.default_size_preset_id not in preset_ids:
            raise ImageGenerationProfileError(
                "default_size_preset_id is not present in size_presets."
            )
        if type(self.sampler_name) is not str or self.sampler_name not in _SAMPLERS:
            raise ImageGenerationProfileError("sampler_name is unsupported.")
        if type(self.scheduler) is not str or self.scheduler not in _SCHEDULERS:
            raise ImageGenerationProfileError("scheduler is unsupported.")
        if type(self.steps) is not int or not 1 <= self.steps <= 80:
            raise ImageGenerationProfileError("steps must be from 1 through 80.")
        if (
            type(self.cfg_scale) is not float
            or not math.isfinite(self.cfg_scale)
            or not 1.0 <= self.cfg_scale <= 20.0
        ):
            raise ImageGenerationProfileError(
                "cfg_scale must be a finite float from 1.0 through 20.0."
            )
        if (
            type(self.loras) is not tuple
            or len(self.loras) > 64
            or not all(type(lora) is ModelLoraProfile for lora in self.loras)
            or any(lora.architecture is not self.architecture for lora in self.loras)
            or len({lora.lora_id for lora in self.loras}) != len(self.loras)
            or len({lora.lora_name.casefold().replace("\\", "/") for lora in self.loras}) != len(self.loras)
        ):
            raise ImageGenerationProfileError("loras must be unique compatible reviewed LoRA profiles.")

    def lora(self, lora_id: str) -> ModelLoraProfile:
        """Resolve one trusted logical LoRA ID without accepting an asset key."""

        try:
            validate_public_id(lora_id, "lora_id")
        except ImageGenerationValidationError as error:
            raise ImageGenerationProfileError(str(error)) from error
        for lora in self.loras:
            if lora.lora_id == lora_id:
                return lora
        raise ImageGenerationProfileError("The requested LoRA is not available for this profile.")

    def size_preset(self, preset_id: str) -> ImageSizePreset:
        """Resolve one allowlisted size or fail without falling back silently."""

        for preset in self.size_presets:
            if preset.preset_id == preset_id:
                return preset
        raise ImageGenerationProfileError(
            "The requested size preset is not available for this profile."
        )

    @property
    def profile_digest(self) -> str:
        """Return a stable digest for artifact and workflow provenance."""

        document = {
            "architecture": self.architecture.value,
            "cfg_scale": self.cfg_scale,
            "checkpoint_license": self.checkpoint_license,
            "checkpoint_name": self.checkpoint_name,
            "checkpoint_sha256": self.checkpoint_sha256,
            "checkpoint_source": self.checkpoint_source,
            "default_size_preset_id": self.default_size_preset_id,
            "display_name": self.display_name,
            "profile_id": self.profile_id,
            "prompt_dialect": self.prompt_dialect.value,
            "sampler_name": self.sampler_name,
            "scheduler": self.scheduler,
            "size_presets": [
                {
                    "height": preset.height,
                    "preset_id": preset.preset_id,
                    "width": preset.width,
                }
                for preset in self.size_presets
            ],
            "steps": self.steps,
        }
        # Omit the new configuration entirely for existing profiles so their
        # recorded provenance digests remain byte-for-byte compatible.  For
        # configured LoRAs the digest pins the allowlist and file hashes, while
        # the workflow digest identifies the selected key and strengths.
        if self.loras:
            document["loras"] = [
                {
                    "architecture": lora.architecture.value,
                    "display_name": lora.display_name,
                    "lora_id": lora.lora_id,
                    "lora_license": lora.lora_license,
                    "lora_name": lora.lora_name,
                    "lora_sha256": lora.lora_sha256,
                    "lora_source": lora.lora_source,
                }
                for lora in self.loras
            ]
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ModelProfileCatalog:
    """Own an immutable unique set of trusted model profiles."""

    def __init__(self, profiles: tuple[ModelProfile, ...]) -> None:
        """Copy and validate profiles before exposing logical lookup."""

        if (
            type(profiles) is not tuple
            or not profiles
            or not all(type(profile) is ModelProfile for profile in profiles)
        ):
            raise ImageGenerationProfileError(
                "profiles must contain at least one ModelProfile."
            )
        profile_ids = tuple(profile.profile_id for profile in profiles)
        if len(set(profile_ids)) != len(profile_ids):
            raise ImageGenerationProfileError("profiles contains duplicate IDs.")
        self._profiles = tuple(profiles)

    @property
    def profiles(self) -> tuple[ModelProfile, ...]:
        """Return the immutable profiles in configured display order."""

        return self._profiles

    def get(self, profile_id: str) -> ModelProfile:
        """Resolve one logical profile ID without accepting a model filename."""

        try:
            validate_public_id(profile_id, "profile_id")
        except ImageGenerationValidationError as error:
            raise ImageGenerationProfileError(str(error)) from error
        for profile in self._profiles:
            if profile.profile_id == profile_id:
                return profile
        raise ImageGenerationProfileError("The requested model profile is unknown.")


__all__ = [
    "ImageSizePreset",
    "MAX_IMAGE_EDGE_PIXELS",
    "MAX_IMAGE_PIXELS",
    "ModelArchitecture",
    "ModelLoraProfile",
    "ModelProfile",
    "ModelProfileCatalog",
    "PromptDialect",
]
