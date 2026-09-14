"""Load strict local GPT-SoVITS voice profiles without bundling their assets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import re
from typing import Final, Literal, TypeAlias, cast
import unicodedata

from .gpt_sovits import (
    GptSovitsPromptLanguage,
    GptSovitsVoice,
)
from .synthesis import (
    SYNTHESIS_MAX_AUDIO_BYTES,
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

VOICE_PROFILE_CATALOG_SCHEMA_VERSION: Final = 2
VOICE_PROFILE_CATALOG_MAX_BYTES: Final = 256 * 1024
VOICE_PROFILE_CATALOG_MAX_PROFILES: Final = 32
VOICE_PROFILE_MAX_REFERENCES: Final = 64
VOICE_PROFILE_ASSET_MAX_BYTES: Final = 8 * 1024 * 1024 * 1024

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
_ASSET_FIELDS: Final = frozenset({"path", "bytes", "sha256"})
_RIGHTS_STATUSES: Final = ("verified", "local-evaluation-only")
# The upstream v2 API rejects Ogg when ``streaming_mode`` is false. Profiles
# configure this deliberately non-streaming adapter, so reject that impossible
# combination while the engine-independent result contract can still validate
# Ogg Opus produced by a future streaming engine.
_AUDIO_FORMATS: Final = ("wav", "aac")
_PROMPT_LANGUAGES: Final = ("zh", "en")
_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_MAX_DISPLAY_NAME_CODE_POINTS: Final = 80
_MAX_RELATIVE_PATH_CODE_POINTS: Final = 512
_WINDOWS_RESERVED_PARTS: Final = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
    | {f"com{index}" for index in ("¹", "²", "³")}
    | {f"lpt{index}" for index in ("¹", "²", "³")}
)
_WINDOWS_FORBIDDEN_PATH_CHARACTERS: Final = frozenset('<>:"\\|?*')
_WINDOWS_MAX_COMPONENT_UTF16_UNITS: Final = 255
_SELECTION_PROVENANCE: Final = object()

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


def _require_lexical_asset_root(value: object) -> Path:
    """Return an absolute root without resolving away later reparse evidence."""

    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or ".." in value.parts
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return value


def _require_relative_asset_path(value: object) -> PurePosixPath:
    """Return one lexical portable path without touching local assets."""

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
    for part in raw_parts:
        try:
            utf16_units = len(part.encode("utf-16-le")) // 2
        except UnicodeEncodeError as error:
            raise VoiceProfileCatalogValidationError(
                _CATALOG_INVALID_MESSAGE
            ) from error
        if (
            unicodedata.normalize("NFC", part) != part
            or part.endswith((" ", "."))
            or any(
                character in _WINDOWS_FORBIDDEN_PATH_CHARACTERS
                for character in part
            )
            or utf16_units > _WINDOWS_MAX_COMPONENT_UTF16_UNITS
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_PARTS
        ):
            # Windows silently aliases trailing dots/spaces and device names;
            # accepting them would make a manifest identify a different file.
            raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return relative


def _windows_asset_key(asset: "LocalVoiceAssetDeclaration") -> str:
    """Return one case-insensitive NFC key for Windows alias detection."""

    return unicodedata.normalize(
        "NFC",
        asset.relative_path.as_posix(),
    ).casefold()


def _require_asset_size(value: object) -> int:
    """Return one positive bounded declared file length."""

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= VOICE_PROFILE_ASSET_MAX_BYTES
    ):
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return value


def _require_sha256(value: object) -> str:
    """Return one canonical lowercase SHA-256 declaration."""

    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
    return value


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
class LocalVoiceAssetDeclaration:
    """Declare one local asset candidate without claiming it was verified.

    Catalog loading validates this bounded manifest data and only checks that
    its lexical root is a directory; it never opens or hashes candidate files.
    The managed-runtime binding boundary must later reject links/reparse points
    and compare the opened file's length and SHA-256 before it may turn a
    declaration into an attested process lease.
    """

    asset_root: Path
    relative_path: PurePosixPath
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        """Reject direct construction that bypasses strict JSON helpers."""

        if not isinstance(self.relative_path, PurePosixPath):
            raise ValueError("Local voice relative_path must be a PurePosixPath.")
        try:
            _require_lexical_asset_root(self.asset_root)
            normalized = _require_relative_asset_path(
                self.relative_path.as_posix()
            )
            _require_asset_size(self.size_bytes)
            _require_sha256(self.sha256)
        except VoiceProfileCatalogValidationError as error:
            raise ValueError("Local voice asset declaration is invalid.") from error
        if normalized != self.relative_path:
            raise ValueError("Local voice relative_path must be canonical.")

    @property
    def candidate_path(self) -> Path:
        """Return the unverified absolute candidate below the declared root."""

        return self.asset_root.joinpath(*self.relative_path.parts)


@dataclass(frozen=True, slots=True)
class LocalVoiceReference:
    """Bind one logical emotion to exact reference audio and transcript data."""

    emotion: str
    audio: LocalVoiceAssetDeclaration
    prompt_text: str
    prompt_language: GptSovitsPromptLanguage

    def __post_init__(self) -> None:
        """Reject values that bypass the strict JSON parser."""

        _require_identifier(self.emotion)
        if not isinstance(self.audio, LocalVoiceAssetDeclaration):
            raise ValueError("Local voice audio must be an asset declaration.")
        if self.audio_path.suffix.casefold() != ".wav":
            raise ValueError("Local voice audio_path must end with .wav.")
        if not 12 <= self.audio.size_bytes <= SYNTHESIS_MAX_AUDIO_BYTES:
            raise ValueError("Local voice audio declared size is invalid.")
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

    @property
    def audio_path(self) -> Path:
        """Return the unverified reference candidate for the external adapter."""

        return self.audio.candidate_path


@dataclass(frozen=True, slots=True)
class LocalVoiceProfile:
    """Describe one model endpoint with one or more emotional references."""

    profile_id: str
    display_name: str
    base_url: str
    gpt_weights: LocalVoiceAssetDeclaration
    sovits_weights: LocalVoiceAssetDeclaration
    speed_factor: float
    audio_format: SynthesisAudioFormat
    rights_status: LocalVoiceRightsStatus
    references: tuple[LocalVoiceReference, ...]

    def __post_init__(self) -> None:
        """Enforce unique emotions and one neutral reference per profile."""

        _require_identifier(self.profile_id)
        _require_display_name(self.display_name)
        if not isinstance(self.gpt_weights, LocalVoiceAssetDeclaration):
            raise ValueError("Local voice gpt_weights must be an asset declaration.")
        if not isinstance(self.sovits_weights, LocalVoiceAssetDeclaration):
            raise ValueError(
                "Local voice sovits_weights must be an asset declaration."
            )
        if self.gpt_weights.size_bytes > VOICE_PROFILE_ASSET_MAX_BYTES:
            raise ValueError("Local voice GPT checkpoint is too large.")
        if self.sovits_weights.size_bytes > VOICE_PROFILE_ASSET_MAX_BYTES:
            raise ValueError("Local voice SoVITS checkpoint is too large.")
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

    @property
    def gpt_weights_path(self) -> Path:
        """Return the unverified GPT checkpoint candidate path."""

        return self.gpt_weights.candidate_path

    @property
    def sovits_weights_path(self) -> Path:
        """Return the unverified SoVITS checkpoint candidate path."""

        return self.sovits_weights.candidate_path


@dataclass(frozen=True, slots=True, repr=False)
class _LocalVoiceSelection:
    """Hold one policy-enabled but still unverified synthesis selection."""

    profile_id: str
    emotion: str
    base_url: str
    gpt_weights: LocalVoiceAssetDeclaration
    sovits_weights: LocalVoiceAssetDeclaration
    reference_audio: LocalVoiceAssetDeclaration
    prompt_text: str
    prompt_language: GptSovitsPromptLanguage
    speed_factor: float
    audio_format: SynthesisAudioFormat
    _provenance: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate direct construction through the external voice contract."""

        if self._provenance is not _SELECTION_PROVENANCE:
            raise ValueError("Local voice selection provenance is invalid.")
        _require_identifier(self.profile_id)
        _require_identifier(self.emotion)
        if not all(
            isinstance(asset, LocalVoiceAssetDeclaration)
            for asset in (
                self.gpt_weights,
                self.sovits_weights,
                self.reference_audio,
            )
        ):
            raise ValueError("Local voice selection assets are invalid.")
        roots = {
            asset.asset_root
            for asset in (
                self.gpt_weights,
                self.sovits_weights,
                self.reference_audio,
            )
        }
        if len(roots) != 1:
            raise ValueError("Local voice selection assets must share one root.")
        if not 12 <= self.reference_audio.size_bytes <= SYNTHESIS_MAX_AUDIO_BYTES:
            raise ValueError("Local voice reference declaration is invalid.")
        self.as_external_voice()

    def as_external_voice(self) -> GptSovitsVoice:
        """Build the permanently unverified loopback-adapter selection."""

        return GptSovitsVoice(
            base_url=self.base_url,
            gpt_weights_path=self.gpt_weights.candidate_path,
            sovits_weights_path=self.sovits_weights.candidate_path,
            reference_audio_path=self.reference_audio.candidate_path,
            prompt_text=self.prompt_text,
            prompt_language=self.prompt_language,
            speed_factor=self.speed_factor,
            audio_format=self.audio_format,
        )


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

        try:
            _require_lexical_asset_root(asset_root)
        except VoiceProfileCatalogValidationError as error:
            raise ValueError("asset_root must be a lexical absolute Path.") from error
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

        asset_aliases: dict[str, LocalVoiceAssetDeclaration] = {}
        reference_contexts: dict[
            str,
            tuple[LocalVoiceAssetDeclaration, str, GptSovitsPromptLanguage],
        ] = {}
        for profile in profiles:
            declarations = (
                profile.gpt_weights,
                profile.sovits_weights,
                *(reference.audio for reference in profile.references),
            )
            for declaration in declarations:
                if declaration.asset_root != asset_root:
                    raise ValueError(
                        "Local voice assets must share the catalog asset root."
                    )
                alias = _windows_asset_key(declaration)
                existing_asset = asset_aliases.setdefault(alias, declaration)
                if existing_asset != declaration:
                    raise ValueError(
                        "Local voice asset declarations contain an ambiguous alias."
                    )
            for reference in profile.references:
                alias = _windows_asset_key(reference.audio)
                context = (
                    reference.audio,
                    reference.prompt_text,
                    reference.prompt_language,
                )
                existing_context = reference_contexts.setdefault(alias, context)
                if existing_context != context:
                    raise ValueError(
                        "Local voice reference declarations conflict."
                    )

        # A GPT-SoVITS process has global model state. Sharing one endpoint is
        # safe only when profiles declare the same weights; different weights
        # must use separate service instances instead of mutable /set_* calls.
        endpoint_models: dict[
            str,
            tuple[LocalVoiceAssetDeclaration, LocalVoiceAssetDeclaration],
        ] = {}
        for profile in profiles:
            model_pair = (
                profile.gpt_weights,
                profile.sovits_weights,
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
        ):
            raise VoiceProfileCatalogValidationError(_CATALOG_INVALID_MESSAGE)
        _require_lexical_asset_root(asset_root)
        try:
            resolved_catalog = catalog_path.resolve(strict=True)
            if (
                not asset_root.is_dir()
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
                _profile_from_value(value, asset_root)
                for value in profile_values
            )
            return cls(
                asset_root,
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

        return self.resolve_selection(profile_id, emotion).as_external_voice()

    def resolve_selection(
        self,
        profile_id: str,
        emotion: str,
    ) -> _LocalVoiceSelection:
        """Return declared assets for later managed-runtime verification."""

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
        return _LocalVoiceSelection(
            profile_id=profile.profile_id,
            emotion=reference.emotion,
            base_url=profile.base_url,
            gpt_weights=profile.gpt_weights,
            sovits_weights=profile.sovits_weights,
            reference_audio=reference.audio,
            prompt_text=reference.prompt_text,
            prompt_language=reference.prompt_language,
            speed_factor=profile.speed_factor,
            audio_format=profile.audio_format,
            _provenance=_SELECTION_PROVENANCE,
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
            gpt_weights=_asset_declaration_from_value(
                profile["gpt_weights"],
                asset_root,
            ),
            sovits_weights=_asset_declaration_from_value(
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
            audio=_asset_declaration_from_value(
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


def _asset_declaration_from_value(
    value: object,
    asset_root: Path,
) -> LocalVoiceAssetDeclaration:
    """Parse one strict content declaration without reading its candidate."""

    asset = _require_exact_object(value, _ASSET_FIELDS)
    try:
        return LocalVoiceAssetDeclaration(
            asset_root=asset_root,
            relative_path=_require_relative_asset_path(asset["path"]),
            size_bytes=_require_asset_size(asset["bytes"]),
            sha256=_require_sha256(asset["sha256"]),
        )
    except (TypeError, ValueError) as error:
        raise VoiceProfileCatalogValidationError(
            _CATALOG_INVALID_MESSAGE
        ) from error
