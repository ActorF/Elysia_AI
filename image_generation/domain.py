"""Define the path-free structured contract for local image generation.

The first production slice deliberately supports one newly created fictional
character per request.  Existing-person likenesses, reference images, edits,
arbitrary model paths, and arbitrary ComfyUI graphs are not part of this
contract; later features must add explicit trusted fields rather than smuggle
those capabilities through prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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
_EXPLICIT_CONTENT_TERM_PATTERN: Final = re.compile(
    r"(?:"
    r"\b(?:nsfw|r-?18|xxx|nude|nudity|naked|topless|bottomless|porn|"
    r"pornographic|hentai|ecchi|lewd|"
    r"erotic|sex|sexual|intercourse|masturbat(?:e|ing|ion)|penetration|"
    r"fellatio|cunnilingus|blowjob|handjob|orgasm|semen|genitals?|penis|"
    r"vagina|vulva|pussy|cock|anus|nipples?|(?:bare|exposed)\s+breasts?|"
    r"no\s+clothes|explicit\s+(?:adult\s+)?(?:image|scene|content)|uncensored|"
    r"bdsm|bondage)\b"
    r"|裸体|裸體|全裸|性交|性爱|性愛|色情|色图|色圖|成人向|口交|肛交|"
    r"自慰|高潮|精液|阴茎|陰莖|阴道|陰道|乳头|乳頭|露点|露點|做爱|做愛|"
    r"裸胸|上空"
    r")",
    flags=re.IGNORECASE,
)
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


class ContentMode(StrEnum):
    """Select the general or explicitly opted-in adult content boundary."""

    GENERAL = "general"
    ADULT = "adult"


class ImageIntent(StrEnum):
    """Identify the reviewed generation workflow requested by the user."""

    NEW_CHARACTER = "new-character"


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
class ImageGenerationSpec:
    """Describe one reviewed single-image request without runtime internals.

    Adult content does not require a numeric fictional age.  It does require
    the caller to select adult mode and affirm that every depicted subject is
    fictional and adult.  The affirmation is an authorization boundary, while
    the conservative term check catches obvious contradictory requests; it is
    not represented as a complete natural-language content classifier.
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
    content_mode: ContentMode = ContentMode.GENERAL
    adult_confirmed: bool = False
    intent: ImageIntent = ImageIntent.NEW_CHARACTER

    def __post_init__(self) -> None:
        """Reject ambiguous content, unsafe text, and unreviewed capabilities."""

        validate_public_id(self.profile_id, "profile_id")
        validate_public_id(self.size_preset_id, "size_preset_id")
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
                required=field_name == "appearance",
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
        if not self.original_fictional_confirmed:
            raise ImageGenerationSafetyError(
                "Image generation requires confirmation of an original fictional subject."
            )
        if type(self.content_mode) is not ContentMode:
            raise ImageGenerationValidationError(
                "content_mode must be a supported closed value."
            )
        if type(self.adult_confirmed) is not bool:
            raise ImageGenerationValidationError(
                "adult_confirmed must be a boolean."
            )
        if type(self.intent) is not ImageIntent or self.intent is not ImageIntent.NEW_CHARACTER:
            raise ImageGenerationValidationError(
                "Only new-character generation is supported in this version."
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
            and _NEGATIVE_SAFETY_ANCHOR_PATTERN.search(
                unicodedata.normalize("NFKC", self.negative_prompt).casefold()
            )
            is not None
        ):
            # A user negative is appended after the system negative.  Forbid
            # terms that would negate the adult/original/single-subject anchors
            # instead of pretending positive text always wins model guidance.
            raise ImageGenerationSafetyError(
                "The negative prompt contradicts a required subject boundary."
            )
        # Both modes compile to a clearly adult original character.  Rejecting
        # contradictions here prevents callers from using general mode as a
        # route around the adult-only subject boundary.
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
        if _EXISTING_CHARACTER_REFERENCE_PATTERN.search(policy_text) is not None:
            raise ImageGenerationSafetyError(
                "This image workflow supports only a new original character."
            )
        if self.content_mode is ContentMode.ADULT:
            if not self.adult_confirmed:
                raise ImageGenerationSafetyError(
                    "Adult mode requires confirmation of fictional adult subjects."
                )
        elif self.adult_confirmed:
            raise ImageGenerationValidationError(
                "adult_confirmed is only valid when adult mode is selected."
            )
        elif _EXPLICIT_CONTENT_TERM_PATTERN.search(policy_text) is not None:
            raise ImageGenerationSafetyError(
                "Explicit sexual content requires the confirmed adult mode."
            )

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
    "ContentMode",
    "ImageGenerationSpec",
    "ImageIntent",
    "MAX_COMPILED_PROMPT_CODEPOINTS",
    "MAX_JSON_SAFE_SEED",
    "MAX_PROMPT_COMPONENT_CODEPOINTS",
    "validate_prompt_component",
    "validate_public_id",
]
