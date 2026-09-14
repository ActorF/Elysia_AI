"""Load strict local GPT-SoVITS voice profiles without bundling their assets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Final, Literal, TypeAlias, cast

from .gpt_sovits import (
    GptSovitsPromptLanguage,
    GptSovitsVoice,
)
from .synthesis import (
    SYNTHESIS_MAX_IDENTIFIER_LENGTH,
    SYNTHESIS_MAX_SPEED_FACTOR,
    SYNTHESIS_MAX_TEXT_CODE_POINTS,
    SYNTHESIS_MIN_SPEED_FACTOR,
    SynthesisAudioFormat,
    SynthesisUnavailableError,
    _contains_spoken_text,
)


LocalVoiceRightsStatus: TypeAlias = Literal[
    "verified",
    "local-evaluation-only",
]

VOICE_PROFILE_CATALOG_SCHEMA_VERSION: Final = 1
VOICE_PROFILE_CATALOG_MAX_BYTES: Final = 256 * 1024
VOICE_PROFILE_CATALOG_MAX_PROFILES: Final = 32
VOICE_PROFILE_MAX_REFERENCES: Final = 64

_PROFILE_FIELDS: Final = frozenset(
    {
        "profile_id",
        "display_name",
        "base_url",
        "gpt_weights",
        "sovits_weights",
        "speed_factor",
        "audio_format",
        "rights_status",
        "references",
    }
)
_REFERENCE_FIELDS: Final = frozenset(
    {"emotion", "audio", "prompt_text", "prompt_language"}
)
_DOCUMENT_FIELDS: Final = frozenset(
    {"schema_version", "default_profile_id", "profiles"}
)
_RIGHTS_STATUSES: Final = ("verified", "local-evaluation-only")
# The upstream v2 API rejects Ogg when ``streaming_mode`` is false. Profiles
# configure this deliberately non-streaming adapter, so reject that impossible
# combination while the engine-independent result contract can still validate
# Ogg Opus produced by a future streaming engine.
_AUDIO_FORMATS: Final = ("wav", "aac")
_PROMPT_LANGUAGES: Final = ("zh", "en")
_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MAX_DISPLAY_NAME_CODE_POINTS: Final = 80
_MAX_RELATIVE_PATH_CODE_POINTS: Final = 512

_CATALOG_UNAVAILABLE_MESSAGE: Final = (
    "Local voice profile catalog is not installed or readable."
)
_CATALOG_INVALID_MESSAGE: Final = "Local voice profile catalog is invalid."
_PROFILE_UNAVAILABLE_MESSAGE: Final = (
    "Selected local voice profile or emotion is unavailable."
)
_PROFILE_DISABLED_MESSAGE: Final = (
    "Selected local voice profile is disabled by its usage policy."
)


class VoiceProfileCatalogError(Exception):
    """Base class for stable local voice catalog failures."""


class VoiceProfileCatalogUnavailableError(VoiceProfileCatalogError):
    """Report that the configured local catalog cannot be read safely."""


class VoiceProfileCatalogValidationError(VoiceProfileCatalogError):
    """Report malformed or ambiguous local profile configuration."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys instead of silently keeping the last value."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    """Reject non-standard NaN and Infinity constants in local JSON."""

    raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)


def _require_exact_object(
    value: object,
    fields: frozenset[str],
) -> dict[str, object]:
    """Require one object with exactly the documented field set."""

    if not isinstance(value, dict) or set(value) != fields:
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    if not all(isinstance(key, str) for key in value):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return cast(dict[str, object], value)


def _require_identifier(value: object) -> str:
    """Return one bounded lowercase logical identifier."""

    if (
        not isinstance(value, str)
        or len(value) > SYNTHESIS_MAX_IDENTIFIER_LENGTH
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return value


def _require_display_name(value: object) -> str:
    """Return one bounded visible profile name without changing its spelling."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > _MAX_DISPLAY_NAME_CODE_POINTS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return value


def _require_relative_asset_path(value: object, asset_root: Path) -> Path:
    """Resolve a portable relative path under the configured asset root."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_RELATIVE_PATH_CODE_POINTS
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    raw_parts = value.split("/")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not raw_parts
        or any(part in ("", ".", "..") for part in raw_parts)
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return asset_root.joinpath(*relative.parts)


def _require_speed_factor(value: object) -> float:
    """Return one finite supported speed, accepting ordinary JSON numbers."""

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not SYNTHESIS_MIN_SPEED_FACTOR
        <= float(value)
        <= SYNTHESIS_MAX_SPEED_FACTOR
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return float(value)


def _require_string_choice(
    value: object,
    allowed: tuple[str, ...],
) -> str:
    """Return a string only when it belongs to a closed vocabulary."""

    if not isinstance(value, str) or value not in allowed:
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return value


@dataclass(frozen=True, slots=True)
class LocalVoiceReference:
    """Bind one logical emotion to exact reference audio and transcript data."""

    emotion: str
    audio_path: Path
    prompt_text: str
    prompt_language: GptSovitsPromptLanguage

    def __post_init__(self) -> None:
        """Reject values that bypass the strict JSON parser."""

        _require_identifier(self.emotion)
        if not isinstance(self.audio_path, Path) or not self.audio_path.is_absolute():
            raise ValueError("Local voice audio_path must be an absolute Path.")
        if self.audio_path.suffix.casefold() != ".wav":
            raise ValueError("Local voice audio_path must end with .wav.")
        if (
            not isinstance(self.prompt_text, str)
            or not _contains_spoken_text(self.prompt_text)
            or len(self.prompt_text) > SYNTHESIS_MAX_TEXT_CODE_POINTS
        ):
            raise ValueError("Local voice prompt_text is invalid.")
        if (
            not isinstance(self.prompt_language, str)
            or self.prompt_language not in _PROMPT_LANGUAGES
        ):
            raise ValueError("Local voice prompt_language must be zh or en.")


@dataclass(frozen=True, slots=True)
class LocalVoiceProfile:
    """Describe one model endpoint with one or more emotional references."""

    profile_id: str
    display_name: str
    base_url: str
    gpt_weights_path: Path
    sovits_weights_path: Path
    speed_factor: float
    audio_format: SynthesisAudioFormat
    rights_status: LocalVoiceRightsStatus
    references: tuple[LocalVoiceReference, ...]

    def __post_init__(self) -> None:
        """Enforce unique emotions and one neutral reference per profile."""

        _require_identifier(self.profile_id)
        _require_display_name(self.display_name)
        if self.rights_status not in _RIGHTS_STATUSES:
            raise ValueError("Local voice rights_status is invalid.")
        if (
            not isinstance(self.references, tuple)
            or not 1 <= len(self.references) <= VOICE_PROFILE_MAX_REFERENCES
            or not all(
                isinstance(reference, LocalVoiceReference)
                for reference in self.references
            )
        ):
            raise ValueError("Local voice references are invalid.")
        emotions = tuple(reference.emotion for reference in self.references)
        if len(set(emotions)) != len(emotions) or "neutral" not in emotions:
            raise ValueError(
                "Local voice references must be unique and include neutral."
            )

        # Constructing one adapter value per reference centralizes endpoint,
        # suffix, exact-prompt, speed, and output-format validation.
        normalized_base_url: str | None = None
        for reference in self.references:
            voice = GptSovitsVoice(
                base_url=self.base_url,
                gpt_weights_path=self.gpt_weights_path,
                sovits_weights_path=self.sovits_weights_path,
                reference_audio_path=reference.audio_path,
                prompt_text=reference.prompt_text,
                prompt_language=reference.prompt_language,
                speed_factor=self.speed_factor,
                audio_format=self.audio_format,
            )
            normalized_base_url = voice.base_url
        if normalized_base_url is not None:
            object.__setattr__(self, "base_url", normalized_base_url)

    @property
    def emotions(self) -> tuple[str, ...]:
        """Return configured emotion identifiers in stable catalog order."""

        return tuple(reference.emotion for reference in self.references)


@dataclass(frozen=True, slots=True)
class LocalVoiceProfileSummary:
    """Expose profile choices without filesystem paths, prompts, or endpoints."""

    profile_id: str
    display_name: str
    emotions: tuple[str, ...]
    rights_status: LocalVoiceRightsStatus
    is_default: bool


class JsonVoiceProfileCatalog:
    """Resolve strict local JSON profiles for the GPT-SoVITS adapter."""

    def __init__(
        self,
        asset_root: Path,
        profiles: tuple[LocalVoiceProfile, ...],
        *,
        default_profile_id: str,
        allow_local_evaluation: bool = False,
    ) -> None:
        """Validate profile uniqueness and endpoint-to-model isolation."""

        if not isinstance(asset_root, Path) or not asset_root.is_absolute():
            raise ValueError("asset_root must be an absolute Path.")
        if (
            not isinstance(profiles, tuple)
            or not 1 <= len(profiles) <= VOICE_PROFILE_CATALOG_MAX_PROFILES
            or not all(isinstance(profile, LocalVoiceProfile) for profile in profiles)
        ):
            raise ValueError("profiles must be a bounded LocalVoiceProfile tuple.")
        if not isinstance(allow_local_evaluation, bool):
            raise TypeError("allow_local_evaluation must be a Boolean.")
        _require_identifier(default_profile_id)
        profile_ids = tuple(profile.profile_id for profile in profiles)
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("Local voice profile identifiers must be unique.")
        if default_profile_id not in profile_ids:
            raise ValueError("default_profile_id must identify a configured profile.")

        # A GPT-SoVITS process has global model state. Sharing one endpoint is
        # safe only when profiles declare the same weights; different weights
        # must use separate service instances instead of mutable /set_* calls.
        endpoint_models: dict[str, tuple[Path, Path]] = {}
        for profile in profiles:
            model_pair = (
                profile.gpt_weights_path,
                profile.sovits_weights_path,
            )
            existing = endpoint_models.setdefault(profile.base_url, model_pair)
            if existing != model_pair:
                raise ValueError(
                    "One local voice endpoint cannot declare different models."
                )

        self._asset_root = asset_root
        self._profiles = profiles
        self._by_id = {profile.profile_id: profile for profile in profiles}
        self._default_profile_id = default_profile_id
        self._allow_local_evaluation = allow_local_evaluation

    @classmethod
    def load(
        cls,
        catalog_path: Path,
        asset_root: Path,
        *,
        allow_local_evaluation: bool = False,
    ) -> "JsonVoiceProfileCatalog":
        """Read one bounded strict catalog with a separate local asset root."""

        if (
            not isinstance(catalog_path, Path)
            or not catalog_path.is_absolute()
            or not isinstance(asset_root, Path)
            or not asset_root.is_absolute()
        ):
            raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
        try:
            resolved_root = asset_root.resolve(strict=True)
            resolved_catalog = catalog_path.resolve(strict=True)
            if (
                not resolved_root.is_dir()
                or not resolved_catalog.is_file()
                or resolved_catalog.stat().st_size > VOICE_PROFILE_CATALOG_MAX_BYTES
            ):
                raise OSError("catalog or asset root is unavailable")
            raw = resolved_catalog.read_bytes()
        except (OSError, RuntimeError) as error:
            raise VoiceProfileCatalogUnavailableError(
                _CATALOG_UNAVAILABLE_MESSAGE
            ) from error

        try:
            decoded = raw.decode("utf-8-sig")
            document: object = json.loads(
                decoded,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_strict_object,
            )
            values = _require_exact_object(document, _DOCUMENT_FIELDS)
            schema_version = values["schema_version"]
            default_profile_id = _require_identifier(
                values["default_profile_id"]
            )
            profile_values = values["profiles"]
            if (
                not isinstance(schema_version, int)
                or isinstance(schema_version, bool)
                or schema_version != VOICE_PROFILE_CATALOG_SCHEMA_VERSION
                or not isinstance(profile_values, list)
                or not 1 <= len(profile_values) <= VOICE_PROFILE_CATALOG_MAX_PROFILES
            ):
                raise VoiceProfileCatalogValidationError(
                    _CATALOG_INVALID_MESSAGE
                )
            profiles = tuple(
                _profile_from_value(value, resolved_root)
                for value in profile_values
            )
            return cls(
                resolved_root,
                profiles,
                default_profile_id=default_profile_id,
                allow_local_evaluation=allow_local_evaluation,
            )
        except VoiceProfileCatalogValidationError:
            raise
        except (UnicodeError, ValueError, TypeError, RecursionError) as error:
            raise VoiceProfileCatalogValidationError(
                _CATALOG_INVALID_MESSAGE
            ) from error

    def resolve(self, profile_id: str, emotion: str) -> GptSovitsVoice:
        """Resolve one enabled logical choice without exposing catalog details."""

        resolved_profile_id = (
            self._default_profile_id if profile_id == "default" else profile_id
        )
        profile = self._by_id.get(resolved_profile_id)
        if profile is None:
            raise SynthesisUnavailableError(_PROFILE_UNAVAILABLE_MESSAGE)
        if (
            profile.rights_status == "local-evaluation-only"
            and not self._allow_local_evaluation
        ):
            raise SynthesisUnavailableError(_PROFILE_DISABLED_MESSAGE)
        reference = next(
            (
                candidate
                for candidate in profile.references
                if candidate.emotion == emotion
            ),
            None,
        )
        if reference is None:
            raise SynthesisUnavailableError(_PROFILE_UNAVAILABLE_MESSAGE)
        return GptSovitsVoice(
            base_url=profile.base_url,
            gpt_weights_path=profile.gpt_weights_path,
            sovits_weights_path=profile.sovits_weights_path,
            reference_audio_path=reference.audio_path,
            prompt_text=reference.prompt_text,
            prompt_language=reference.prompt_language,
            speed_factor=profile.speed_factor,
            audio_format=profile.audio_format,
        )

    def summaries(self) -> tuple[LocalVoiceProfileSummary, ...]:
        """Return safe profile metadata suitable for later settings surfaces."""

        return tuple(
            LocalVoiceProfileSummary(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                emotions=profile.emotions,
                rights_status=profile.rights_status,
                is_default=profile.profile_id == self._default_profile_id,
            )
            for profile in self._profiles
        )


def _profile_from_value(value: object, asset_root: Path) -> LocalVoiceProfile:
    """Convert one strict JSON object into an immutable local voice profile."""

    profile = _require_exact_object(value, _PROFILE_FIELDS)
    references_value = profile["references"]
    if (
        not isinstance(references_value, list)
        or not 1 <= len(references_value) <= VOICE_PROFILE_MAX_REFERENCES
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    references = tuple(
        _reference_from_value(reference, asset_root)
        for reference in references_value
    )
    try:
        return LocalVoiceProfile(
            profile_id=_require_identifier(profile["profile_id"]),
            display_name=_require_display_name(profile["display_name"]),
            base_url=cast(str, profile["base_url"]),
            gpt_weights_path=_require_relative_asset_path(
                profile["gpt_weights"],
                asset_root,
            ),
            sovits_weights_path=_require_relative_asset_path(
                profile["sovits_weights"],
                asset_root,
            ),
            speed_factor=_require_speed_factor(profile["speed_factor"]),
            audio_format=cast(
                SynthesisAudioFormat,
                _require_string_choice(profile["audio_format"], _AUDIO_FORMATS),
            ),
            rights_status=cast(
                LocalVoiceRightsStatus,
                _require_string_choice(
                    profile["rights_status"],
                    _RIGHTS_STATUSES,
                ),
            ),
            references=references,
        )
    except (TypeError, ValueError) as error:
        raise VoiceProfileCatalogValidationError(
            _CATALOG_INVALID_MESSAGE
        ) from error


def _reference_from_value(
    value: object,
    asset_root: Path,
) -> LocalVoiceReference:
    """Convert one strict JSON object into an exact emotional reference."""

    reference = _require_exact_object(value, _REFERENCE_FIELDS)
    prompt_text = reference["prompt_text"]
    if (
        not isinstance(prompt_text, str)
        or not _contains_spoken_text(prompt_text)
        or len(prompt_text) > SYNTHESIS_MAX_TEXT_CODE_POINTS
        or "\x00" in prompt_text
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    try:
        return LocalVoiceReference(
            emotion=_require_identifier(reference["emotion"]),
            audio_path=_require_relative_asset_path(
                reference["audio"],
                asset_root,
            ),
            prompt_text=prompt_text,
            prompt_language=cast(
                GptSovitsPromptLanguage,
                _require_string_choice(
                    reference["prompt_language"],
                    _PROMPT_LANGUAGES,
                ),
            ),
        )
    except (TypeError, ValueError) as error:
        raise VoiceProfileCatalogValidationError(
            _CATALOG_INVALID_MESSAGE
        ) from error
