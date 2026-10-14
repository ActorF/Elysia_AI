"""Compile structured image requests into model-specific prompt dialects."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import (
    ImageGenerationSpec,
    MAX_COMPILED_PROMPT_CODEPOINTS,
)
from .exceptions import ImageGenerationLimitError
from .profiles import ModelProfile, PromptDialect


_ADULT_SUBJECT = "original fictional adult character, clearly adult"
_COMMON_NEGATIVE = (
    "child, children, minor, underage, teenager, teen, loli, shota, "
    "young-looking subject, bad anatomy, malformed hands, extra fingers, "
    "missing fingers, fused fingers, extra limbs, duplicate body, distorted face, "
    "text, watermark, logo"
)
_DIALECT_POSITIVE_PREFIXES = {
    PromptDialect.SDXL_NATURAL: "high quality anime illustration",
    PromptDialect.PONY_TAGS: (
        "score_9, score_8_up, score_7_up, source_anime"
    ),
    PromptDialect.ILLUSTRIOUS_TAGS: (
        "masterpiece, best quality, amazing quality, anime illustration"
    ),
}
_DIALECT_NEGATIVE_PREFIXES = {
    PromptDialect.SDXL_NATURAL: "low quality, low resolution",
    PromptDialect.PONY_TAGS: "score_4, score_3, score_2, score_1, source_pony",
    PromptDialect.ILLUSTRIOUS_TAGS: "worst quality, low quality, normal quality",
}


@dataclass(frozen=True, slots=True)
class CompiledPrompts:
    """Carry final positive and negative text without runtime node details."""

    positive: str
    negative: str


def compile_prompts(
    spec: ImageGenerationSpec,
    profile: ModelProfile,
) -> CompiledPrompts:
    """Compile one spec using the exact grammar declared by its profile.

    Model families are not prompt-compatible merely because they share SDXL
    tensor shapes.  The profile therefore selects quality prefixes while the
    structured subject boundary and anatomy negatives remain invariant.
    """

    if type(spec) is not ImageGenerationSpec:
        raise TypeError("spec must be ImageGenerationSpec.")
    if type(profile) is not ModelProfile:
        raise TypeError("profile must be ModelProfile.")
    if spec.profile_id != profile.profile_id:
        raise ValueError("The image spec and model profile IDs do not match.")

    positive = ", ".join((
        _DIALECT_POSITIVE_PREFIXES[profile.prompt_dialect],
        _ADULT_SUBJECT,
        *spec.prompt_components,
    ))
    negative_parts = [
        _DIALECT_NEGATIVE_PREFIXES[profile.prompt_dialect],
        _COMMON_NEGATIVE,
    ]
    if spec.negative_prompt:
        negative_parts.append(spec.negative_prompt)
    negative = ", ".join(negative_parts)
    if (
        len(positive) > MAX_COMPILED_PROMPT_CODEPOINTS
        or len(negative) > MAX_COMPILED_PROMPT_CODEPOINTS
    ):
        raise ImageGenerationLimitError(
            "The compiled image prompt exceeds its fixed size budget."
        )
    return CompiledPrompts(positive=positive, negative=negative)


__all__ = ["CompiledPrompts", "compile_prompts"]
