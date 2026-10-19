"""Define the path-free structured contract for local image generation.

Requests support one original fictional adult or the fixed adult fictional
Elysia identity. Existing-person likenesses, reference images, edits,
arbitrary model paths, and arbitrary ComfyUI graphs are not part of this
contract; later features must add explicit trusted fields rather than smuggle
those capabilities through prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
import unicodedata
from typing import Final

from .exceptions import (
    ImageGenerationSafetyError,
    ImageGenerationValidationError,
)


MAX_PROMPT_COMPONENT_CODEPOINTS: Final = 1_024
MAX_COMPILED_PROMPT_CODEPOINTS: Final = 6_000
MAX_JSON_SAFE_SEED: Final = 9_007_199_254_740_991
MAX_SELECTED_LORAS: Final = 4
_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CONTROL_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})
_MINOR_TERM_PATTERN: Final = re.compile(
    r"(?:"
    r"\b(?:infant|infants|child|children|kid|kids|minor|"
    r"underage|teen|teenage|teenager|adolescent|juvenile|preteen|toddler|"
    r"preschooler|schoolgirl|schoolboy|middle[-\s]?(?:schooler|school\s+student)|"
    r"high[-\s]?(?:schooler|school\s+student)|elementary[-\s]?student|"
    r"loli|lolita|shota)\b"
    r"|\bnewborns?\b"
    r"|\bbab(?:y|ies)\b(?![-\s]+(?:blue|pink)\b)"
    r"|\byoung[-\s]+looking\b"
    r"|\b(?:young|little)\s+(?:girl|boy|person|character)\b"
    r"|幼女|幼童|儿童|兒童|未成年|小学生|小學生|初中生|國中生|国中生|"
    r"高中生|中学生|中學生|婴儿|嬰兒|宝宝|寶寶|青少年|少年|少女|"
    r"女童|男童|新生儿|新生兒|婴幼儿|嬰幼兒"
    r")",
    flags=re.IGNORECASE,
)
_EXPLICIT_NUMERIC_AGE_PATTERN: Final = re.compile(
    r"(?<!\d)(\d{1,3})(?!\d)[-\s]*(?:years?[-\s]*old|y\s*/?\s*o\b|"
    r"周岁|週歲|岁|歲)",
    flags=re.IGNORECASE,
)
_AGE_PREFIX_PATTERN: Final = re.compile(
    r"\b(?:age|aged)\s*[:=-]?\s*(\d{1,3})\b",
    flags=re.IGNORECASE,
)
_MINOR_AGE_WORDS: Final = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    "twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
)
_EXPLICIT_WORD_AGE_PATTERN: Final = re.compile(
    rf"(?:\b(?:{_MINOR_AGE_WORDS})[-\s]+years?[-\s]+old\b|"
    rf"\b(?:age|aged)\s+(?:{_MINOR_AGE_WORDS})\b)",
    flags=re.IGNORECASE,
)
_CHINESE_NUMERAL_CHARS: Final = "零〇一二两兩三四五六七八九十百"
_EXPLICIT_CHINESE_AGE_PATTERN: Final = re.compile(
    rf"(?<![{_CHINESE_NUMERAL_CHARS}])([{_CHINESE_NUMERAL_CHARS}]+)[岁歲]"
)
_CHINESE_DIGITS: Final = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_REAL_PERSON_REFERENCE_PATTERN: Final = re.compile(
    r"(?:"
    r"\b(?:real[-\s]+person|real[-\s]+human|celebrity|public\s+figure|"
    r"deepfake|likeness\s+of|looks?\s+like\s+(?:a\s+)?real)\b"
    r"|真人|现实人物|現實人物|真实人物|真實人物|名人|明星|公众人物|"
    r"公眾人物|真人长相|真人長相|照着真人|照著真人"
    r")",
    flags=re.IGNORECASE,
)
_PROPER_NAME_PATTERN: Final = re.compile(
    r"\b[A-Z][a-z]{1,30}(?:[-'][A-Z]?[a-z]{1,30})?"
    r"(?:\s+[A-Z][a-z]{1,30}(?:[-'][A-Z]?[a-z]{1,30})?){1,3}\b"
)
_MULTI_SUBJECT_PATTERN: Final = re.compile(
    r"(?:"
    r"\b(?:couple|duo|twins|(?:group|pair)\s+of\s+(?:people|persons|"
    r"characters|women|men|girls|boys)|multiple\s+(?:people|persons|"
    r"characters|women|men|girls|boys)|two\s+(?:people|persons|characters|"
    r"women|men|girls|boys)|(?:two|three|four|five|six|seven|eight|nine)\s+"
    r"(?:people|persons|characters|women|men|girls|boys)|[2-9]\s*"
    r"(?:girls|boys|women|men)|one\s+(?:woman|man|girl|boy)\s+and\s+one\s+"
    r"(?:woman|man|girl|boy))\b"
    r"|\b(?:a\s+)?(?:woman|man|girl|boy|person|character)\s+"
    r"(?:beside|alongside|next\s+to|with|and)\s+"
    r"(?:(?:a|one|another|the)\s+)?"
    r"(?:woman|man|girl|boy|person|character)\b"
    r"|多人|两人|兩人|双人|雙人|情侣|情侶|(?:一对|一對)(?:人|角色|男女)|"
    r"双胞胎|雙胞胎"
    r")",
    flags=re.IGNORECASE,
)
_EXISTING_CHARACTER_REFERENCE_PATTERN: Final = re.compile(
    r"(?:"
    r"\b(?:existing\s+character|canon\s+character|fanart\s+of|"
    r"cosplay\s+as|character\s+from|elysia)\b"
    r"|现有角色|現有角色|已有角色|原作角色|同人角色|"
    r"爱莉希雅|愛莉希雅"
    r")",
    flags=re.IGNORECASE,
)
_ELYSIA_IDENTITY_PATTERN: Final = re.compile(
    r"\belysia\b|爱莉希雅|愛莉希雅", flags=re.IGNORECASE,
)
_RESERVED_MODEL_REFERENCE_PATTERN: Final = re.compile(
    r"\bembedding\s*:",
    flags=re.IGNORECASE,
)
_NEGATIVE_SAFETY_ANCHOR_PATTERN: Final = re.compile(
    r"(?:"
    r"\b(?:adults?|grown[-\s]?ups?|mature|fully[-\s]+grown|"
    r"fictional|original\s+character|solo|single\s+subject|"
    r"one\s+(?:person|character|subject))\b"
    r"|成年(?:人|角色)?|虚构|虛構|原创角色|原創角色|单人|單人"
    r")",
    flags=re.IGNORECASE,
)


def _contains_explicit_minor_age(value: str) -> bool:
    """Return whether text explicitly identifies a subject below age 18.

    This deliberately recognizes only unambiguous numeric and common written
    age forms.  It complements, rather than pretends to replace, the closed
    adult affirmation and conservative age-ambiguous term check.
    """

    numeric_ages = (
        *(int(match.group(1), 10) for match in _EXPLICIT_NUMERIC_AGE_PATTERN.finditer(value)),
        *(int(match.group(1), 10) for match in _AGE_PREFIX_PATTERN.finditer(value)),
    )
    return (
        any(age < 18 for age in numeric_ages)
        or _EXPLICIT_WORD_AGE_PATTERN.search(value) is not None
        or any(
            _parse_chinese_age(match.group(1)) < 18
            for match in _EXPLICIT_CHINESE_AGE_PATTERN.finditer(value)
        )
    )


def _parse_chinese_age(value: str) -> int:
    """Parse a bounded Chinese age numeral without suffix-substring matches.

    Ages containing ``百`` are necessarily outside the minor range, so only
    the common zero-through-ninety-nine forms need exact arithmetic here.
    Invalid/non-canonical forms return an adult-range sentinel and remain
    subject to the separate lexical tripwires.
    """

    if "百" in value:
        return 100
    if "十" in value:
        if value.count("十") != 1:
            return 100
        tens_text, ones_text = value.split("十", 1)
        tens = 1 if not tens_text else _CHINESE_DIGITS.get(tens_text)
        ones = 0 if not ones_text else _CHINESE_DIGITS.get(ones_text)
        if tens is None or ones is None:
            return 100
        return tens * 10 + ones
    if not value or any(character not in _CHINESE_DIGITS for character in value):
        return 100
    try:
        return int("".join(str(_CHINESE_DIGITS[character]) for character in value))
    except ValueError:
        return 100


class ImageIntent(StrEnum):
    """Identify the reviewed generation workflow requested by the user."""

    NEW_CHARACTER = "new-character"
    ELYSIA = "elysia-character"


def validate_public_id(value: object, field_name: str) -> str:
    """Return one bounded logical ID without accepting paths or display text."""

    if (
        type(value) is not str
        or _ID_PATTERN.fullmatch(value) is None
    ):
        raise ImageGenerationValidationError(
            f"{field_name} must be a lowercase logical identifier."
        )
    return value


def validate_prompt_component(
    value: object,
    field_name: str,
    *,
    required: bool = False,
) -> str:
    """Return normalized prompt text under one transport-safe size ceiling.

    Format controls and surrogate code points are rejected because prompts are
    logged, serialized, and displayed across Python, Electron, and ComfyUI.
    Ordinary punctuation and line breaks are allowed after trimming so users
    can still write natural multi-clause descriptions.
    """

    if type(value) is not str:
        raise ImageGenerationValidationError(f"{field_name} must be text.")
    normalized = unicodedata.normalize("NFC", value).strip()
    if required and not normalized:
        raise ImageGenerationValidationError(f"{field_name} is required.")
    if len(normalized) > MAX_PROMPT_COMPONENT_CODEPOINTS:
        raise ImageGenerationValidationError(
            f"{field_name} exceeds its prompt-component limit."
        )
    if any(
        unicodedata.category(character) in _CONTROL_CATEGORIES
        and character not in "\n\t"
        for character in normalized
    ):
        raise ImageGenerationValidationError(
            f"{field_name} contains unsupported control characters."
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ImageLoraSelection:
    """Select one reviewed LoRA by logical ID and bounded model/CLIP strengths."""

    lora_id: str
    strength_model: float = 1.0
    strength_clip: float = 1.0

    def __post_init__(self) -> None:
        """Reject paths, booleans, non-finite numbers, and strengths outside 0–2."""

        validate_public_id(self.lora_id, "lora_id")
        for field_name in ("strength_model", "strength_clip"):
            value = getattr(self, field_name)
            if type(value) not in {int, float} or not 0.0 <= value <= 2.0 or not math.isfinite(value):
                raise ImageGenerationValidationError(f"{field_name} must be a finite number from 0 through 2.")
            object.__setattr__(self, field_name, float(value))


@dataclass(frozen=True, slots=True)
class ImageGenerationSpec:
    """Describe one reviewed single-image request without runtime internals.

    Explicit and non-explicit descriptions share this one request contract.
    Original requests affirm an original fictional subject; the dedicated
    Elysia intent instead affirms the fixed fictional adult identity. The compiler
    always anchors the subject as clearly adult. Conservative contradiction
    checks reject obvious minor, real-person, existing-character, and
    multi-subject requests; they are deliberately bounded lexical tripwires,
    not a complete natural-language identity or age classifier.
    """

    profile_id: str
    size_preset_id: str
    appearance: str
    outfit: str = ""
    pose: str = ""
    scene: str = ""
    style: str = ""
    camera: str = ""
    lighting: str = ""
    negative_prompt: str = ""
    seed: int = 0
    original_fictional_confirmed: bool = False
    intent: ImageIntent = ImageIntent.NEW_CHARACTER
    lora: ImageLoraSelection | None = None
    loras: tuple[ImageLoraSelection, ...] = ()
    fictional_adult_confirmed: bool = False

    def __post_init__(self) -> None:
        """Reject ambiguous content, unsafe text, and unreviewed capabilities."""

        validate_public_id(self.profile_id, "profile_id")
        validate_public_id(self.size_preset_id, "size_preset_id")
        if self.lora is not None and type(self.lora) is not ImageLoraSelection:
            raise ImageGenerationValidationError("lora must be one reviewed LoRA selection.")
        if (
            type(self.loras) is not tuple
            or len(self.loras) > MAX_SELECTED_LORAS
            or any(type(selection) is not ImageLoraSelection for selection in self.loras)
            or len({selection.lora_id for selection in self.loras}) != len(self.loras)
            or (self.lora is not None and self.loras)
        ):
            raise ImageGenerationValidationError(
                "loras must contain at most four distinct ordered selections without the legacy lora alias."
            )
        if type(self.intent) is not ImageIntent:
            raise ImageGenerationValidationError("intent must be a reviewed image intent.")
        for field_name in (
            "appearance",
            "outfit",
            "pose",
            "scene",
            "style",
            "camera",
            "lighting",
            "negative_prompt",
        ):
            normalized = validate_prompt_component(
                getattr(self, field_name),
                field_name,
                required=field_name == "appearance" and self.intent is ImageIntent.NEW_CHARACTER,
            )
            object.__setattr__(self, field_name, normalized)
        if type(self.seed) is not int or not 0 <= self.seed <= MAX_JSON_SAFE_SEED:
            raise ImageGenerationValidationError(
                "seed must be a non-negative JSON-safe integer."
            )
        if type(self.original_fictional_confirmed) is not bool:
            raise ImageGenerationValidationError(
                "original_fictional_confirmed must be a boolean."
            )
        if type(self.fictional_adult_confirmed) is not bool:
            raise ImageGenerationValidationError("fictional_adult_confirmed must be a boolean.")
        if self.intent is ImageIntent.NEW_CHARACTER and not self.original_fictional_confirmed:
            raise ImageGenerationSafetyError(
                "Image generation requires confirmation of an original fictional subject."
            )
        if self.intent is ImageIntent.ELYSIA and self.fictional_adult_confirmed is not True:
            raise ImageGenerationSafetyError(
                "Elysia generation requires confirmation of the fixed fictional adult subject."
            )
        combined = "\n".join(
            getattr(self, field_name)
            for field_name in (
                "appearance",
                "outfit",
                "pose",
                "scene",
                "style",
                "camera",
                "lighting",
            )
        )
        # Compatibility normalization is used only for policy matching, not
        # for the prompt itself.  It closes obvious full-width and casefolding
        # aliases without changing the user's preserved NFC description.
        policy_source = unicodedata.normalize("NFKC", combined)
        policy_text = policy_source.casefold()
        all_prompt_source = unicodedata.normalize(
            "NFKC",
            "\n".join((combined, self.negative_prompt)),
        )
        if _RESERVED_MODEL_REFERENCE_PATTERN.search(all_prompt_source) is not None:
            # CLIPTextEncode treats embedding:name as a local model lookup.
            # Free-form text may not escape the profile's reviewed asset set.
            raise ImageGenerationSafetyError(
                "Image prompts cannot select local embedding assets."
            )
        if (
            self.negative_prompt
            and (
                _NEGATIVE_SAFETY_ANCHOR_PATTERN.search(
                    unicodedata.normalize("NFKC", self.negative_prompt).casefold()
                ) is not None
                or (
                    self.intent is ImageIntent.ELYSIA
                    and _ELYSIA_IDENTITY_PATTERN.search(
                        unicodedata.normalize("NFKC", self.negative_prompt).casefold()
                    ) is not None
                )
            )
        ):
            # A user negative is appended after the system negative.  Forbid
            # terms that would negate the adult/original/single-subject anchors
            # instead of pretending positive text always wins model guidance.
            raise ImageGenerationSafetyError(
                "The negative prompt contradicts a required subject boundary."
            )
        # Every accepted request compiles to a clearly adult fictional
        # character. Reject contradictions before prompt compilation so the
        # fixed subject anchor is never treated as a repair for unsafe input.
        if (
            _MINOR_TERM_PATTERN.search(policy_text) is not None
            or _contains_explicit_minor_age(policy_text)
        ):
            raise ImageGenerationSafetyError(
                "Image generation cannot include minor or age-ambiguous subjects."
            )
        if _REAL_PERSON_REFERENCE_PATTERN.search(policy_text) is not None:
            raise ImageGenerationSafetyError(
                "Image generation cannot include a real-person likeness."
            )
        if _PROPER_NAME_PATTERN.search(
            unicodedata.normalize("NFKC", self.appearance)
        ) is not None:
            raise ImageGenerationSafetyError(
                "The original-character appearance cannot contain a proper name."
            )
        if _MULTI_SUBJECT_PATTERN.search(policy_text) is not None:
            raise ImageGenerationSafetyError(
                "This image workflow supports exactly one subject."
            )
        # Only the dedicated identity is exempted. Removing that exact name
        # leaves generic existing-character instructions subject to the legacy
        # boundary, rather than opening a free-form character selector.
        identity_checked_text = (
            _ELYSIA_IDENTITY_PATTERN.sub("", policy_text)
            if self.intent is ImageIntent.ELYSIA else policy_text
        )
        if _EXISTING_CHARACTER_REFERENCE_PATTERN.search(identity_checked_text) is not None:
            raise ImageGenerationSafetyError(
                "This image workflow supports only a new original character."
                if self.intent is ImageIntent.NEW_CHARACTER
                else "This image workflow does not accept another existing character."
            )

    @property
    def selected_loras(self) -> tuple[ImageLoraSelection, ...]:
        """Return the ordered effective selections while retaining old single callers."""

        return (self.lora,) if self.lora is not None else self.loras

    @property
    def prompt_components(self) -> tuple[str, ...]:
        """Return non-empty positive components in deterministic UI order."""

        return tuple(
            component
            for component in (
                self.appearance,
                self.outfit,
                self.pose,
                self.scene,
                self.style,
                self.camera,
                self.lighting,
            )
            if component
        )


__all__ = [
    "ImageGenerationSpec",
    "ImageIntent",
    "ImageLoraSelection",
    "MAX_COMPILED_PROMPT_CODEPOINTS",
    "MAX_JSON_SAFE_SEED",
    "MAX_SELECTED_LORAS",
    "MAX_PROMPT_COMPONENT_CODEPOINTS",
    "validate_prompt_component",
    "validate_public_id",
]
