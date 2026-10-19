"""Compile structured image requests into model-specific prompt dialects."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .domain import (
    ImageGenerationSpec,
    ImageIntent,
    MAX_COMPILED_PROMPT_CODEPOINTS,
)
from .exceptions import ImageGenerationLimitError
from .profiles import ModelProfile, PromptDialect


_ADULT_SUBJECT = "original fictional adult character, clearly adult"
_ELYSIA_SUBJECT = (
    "Elysia, fictional adult woman, age 28, clearly adult, "
    "long pink hair, violet eyes, elf ears, 1girl, solo, one person, single subject"
)
# Character adapters can inherit training sheets with repeated views. Keep this
# composition guidance specific to the fixed identity so legacy prompts retain
# their exact wording; it reduces that bias without claiming output detection.
_ELYSIA_NEGATIVE = (
    "2girls, multiple girls, multiple people, multiple characters, twins, duplicate person, split screen, "
    "multiple views, multi panel, character sheet, turnaround, collage"
)
# Illustrious character LoRAs often overlearn face crops. Detect an explicit
# whole-body request in either supported UI language and translate that intent
# into the concrete composition tags the model was trained to follow. The
# user's original text is still preserved, and other compositions are not
# forced into a wide shot.
_FULL_BODY_REQUEST_PATTERN = re.compile(
    r"(?:\bfull[-\s]*(?:body|length)\b|\bhead[-\s]*to[-\s]*toe\b|"
    r"\bentire\s+body\b|全身(?:照|像|构图|構圖)?|从头到脚|從頭到腳)",
    flags=re.IGNORECASE,
)
_NEGATED_FULL_BODY_REQUEST_PATTERN = re.compile(
    r"(?:\b(?:avoid|without|no|not|never|don't|dont)\b.{0,24}"
    r"\b(?:full[-\s]*(?:body|length)|head[-\s]*to[-\s]*toe|entire\s+body)\b|"
    r"(?:不要|不用|避免|别|別|非).{0,12}"
    r"(?:全身(?:照|像|构图|構圖)?|从头到脚|從頭到腳))",
    flags=re.IGNORECASE,
)
_CLOSE_UP_REQUEST_PATTERN = re.compile(
    r"(?:\b(?:close[-\s]*up|headshot|face[-\s]+portrait)\b|"
    r"(?:脸部|臉部|面部)(?:大)?特写|(?:脸部|臉部|面部)(?:大)?特寫|"
    r"头像|頭像|大头照|大頭照)",
    flags=re.IGNORECASE,
)
_FULL_BODY_POSITIVE = (
    "full body, head-to-toe, entire body visible, feet visible, wide shot"
)
_FULL_BODY_NEGATIVE = (
    "close-up, extreme close-up, portrait crop, cropped body, cropped torso, "
    "cropped legs, cropped feet, out of frame"
)
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

    composition_source = unicodedata.normalize(
        "NFKC", "\n".join((spec.pose, spec.scene, spec.camera)),
    ).casefold()
    requests_full_body = (
        spec.intent is ImageIntent.ELYSIA
        and profile.prompt_dialect is PromptDialect.ILLUSTRIOUS_TAGS
        and _FULL_BODY_REQUEST_PATTERN.search(composition_source) is not None
        and _NEGATED_FULL_BODY_REQUEST_PATTERN.search(composition_source) is None
        and _CLOSE_UP_REQUEST_PATTERN.search(composition_source) is None
    )
    positive_parts = [
        _DIALECT_POSITIVE_PREFIXES[profile.prompt_dialect],
        _ELYSIA_SUBJECT if spec.intent is ImageIntent.ELYSIA else _ADULT_SUBJECT,
    ]
    if requests_full_body:
        positive_parts.append(_FULL_BODY_POSITIVE)
    positive_parts.extend(spec.prompt_components)
    positive = ", ".join(positive_parts)
    negative_parts = [
        _DIALECT_NEGATIVE_PREFIXES[profile.prompt_dialect],
        _COMMON_NEGATIVE,
    ]
    if spec.intent is ImageIntent.ELYSIA:
        negative_parts.append(_ELYSIA_NEGATIVE)
    if requests_full_body:
        negative_parts.append(_FULL_BODY_NEGATIVE)
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
