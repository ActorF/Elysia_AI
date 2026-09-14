"""Adapt logical speech requests to a loopback GPT-SoVITS ``/tts`` API."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import math
from pathlib import Path
from threading import Lock
from typing import Any, Final, Literal, Protocol, TypeAlias, cast
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout,
)

from .synthesis import (
    SYNTHESIS_MAX_AUDIO_BYTES,
    SYNTHESIS_MAX_SPEED_FACTOR,
    SYNTHESIS_MAX_TEXT_CODE_POINTS,
    SYNTHESIS_MIN_SPEED_FACTOR,
    SynthesisAudioFormat,
    SynthesisFailedError,
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
    SynthesisValidationError,
)


GptSovitsPromptLanguage: TypeAlias = Literal["zh", "en"]

_PROMPT_LANGUAGES: Final = ("zh", "en")
_AUDIO_FORMATS: Final = ("wav", "ogg", "aac")
_AUDIO_MEDIA_TYPES: Final = {
    "wav": frozenset(
        {"audio/wav", "audio/wave", "audio/x-wav", "audio/vnd.wave"}
    ),
    "ogg": frozenset({"audio/ogg", "audio/x-ogg"}),
    "aac": frozenset({"audio/aac", "audio/aacp", "audio/x-aac"}),
}
_AUDIO_EXTENSION: Final = {
    "wav": ".wav",
    "ogg": ".ogg",
    "aac": ".aac",
}
_SERVICE_UNAVAILABLE_MESSAGE: Final = (
    "Local speech synthesis service is not running or reachable."
)
_SERVICE_REQUEST_MESSAGE: Final = "Local speech synthesis request failed."
_SERVICE_REJECTED_MESSAGE: Final = (
    "Local speech synthesis service rejected the request."
)
_INVALID_AUDIO_MESSAGE: Final = (
    "Local speech synthesis service returned invalid audio."
)
_ASSETS_UNAVAILABLE_MESSAGE: Final = (
    "Configured local speech synthesis assets are unavailable."
)
_GIT_LFS_POINTER_PREFIX: Final = (
    b"version https://git-lfs.github.com/spec/v1"
)


def _is_strict_integer(value: object) -> bool:
    """Reject booleans where deterministic configuration needs an integer."""

    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_loopback_base_url(value: object) -> str:
    """Return a canonical loopback HTTP origin or reject possible SSRF input."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("GPT-SoVITS base_url must be a non-empty string.")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "http":
        raise ValueError("GPT-SoVITS base_url must use loopback HTTP.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("GPT-SoVITS base_url must not contain credentials.")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("GPT-SoVITS base_url must be an origin without a path.")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("GPT-SoVITS base_url must include a loopback host.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("GPT-SoVITS base_url has an invalid port.") from error
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("GPT-SoVITS base_url has an invalid port.")

    normalized_host = hostname.lower()
    if normalized_host != "localhost":
        try:
            address = ip_address(normalized_host)
        except ValueError as error:
            raise ValueError(
                "GPT-SoVITS base_url must use a loopback host."
            ) from error
        if not address.is_loopback:
            raise ValueError(
                "GPT-SoVITS base_url must use a loopback host."
            )

    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit(("http", netloc, "", "", ""))


def _contains_prompt_text(value: str) -> bool:
    """Return whether an exact reference transcript contains visible text."""

    return any(
        not (character.isspace() or character == "\ufeff")
        for character in value
    )


@dataclass(frozen=True, slots=True)
class GptSovitsConfig:
    """Configure bounded local HTTP access and the permitted asset root."""

    asset_root: Path
    request_timeout_seconds: float = 120.0
    deterministic_seed: int = 42
    max_response_bytes: int = SYNTHESIS_MAX_AUDIO_BYTES

    def __post_init__(self) -> None:
        """Reject ambiguous roots, excessive deadlines, and random seeds."""

        if (
            not isinstance(self.asset_root, Path)
            or not self.asset_root.is_absolute()
        ):
            raise ValueError("GPT-SoVITS asset_root must be an absolute Path.")
        if (
            not isinstance(self.request_timeout_seconds, float)
            or not math.isfinite(self.request_timeout_seconds)
            or not 0.1 <= self.request_timeout_seconds <= 300.0
        ):
            raise ValueError(
                "GPT-SoVITS request_timeout_seconds must be a finite float "
                "between 0.1 and 300.0."
            )
        if (
            not _is_strict_integer(self.deterministic_seed)
            or not 0 <= self.deterministic_seed <= 2_147_483_647
        ):
            raise ValueError(
                "GPT-SoVITS deterministic_seed must be between 0 and "
                "2147483647."
            )
        if (
            not _is_strict_integer(self.max_response_bytes)
            or not 64 <= self.max_response_bytes <= SYNTHESIS_MAX_AUDIO_BYTES
        ):
            raise ValueError(
                "GPT-SoVITS max_response_bytes must be between 64 and "
                f"{SYNTHESIS_MAX_AUDIO_BYTES}."
            )


@dataclass(frozen=True, slots=True)
class GptSovitsVoice:
    """Describe one resolved service endpoint and exact reference voice.

    Weight paths identify the model expected in that profile's independently
    configured GPT-SoVITS service. They are verified as local assets but are not
    sent to mutable ``/set_*`` endpoints; avoiding global model switches keeps
    concurrent profiles from changing each other's voice during synthesis.
    """

    base_url: str
    gpt_weights_path: Path
    sovits_weights_path: Path
    reference_audio_path: Path
    prompt_text: str
    prompt_language: GptSovitsPromptLanguage
    speed_factor: float = 1.0
    audio_format: SynthesisAudioFormat = "wav"

    def __post_init__(self) -> None:
        """Reject unsafe endpoints, paths, prompt metadata, and media choices."""

        object.__setattr__(
            self,
            "base_url",
            _normalize_loopback_base_url(self.base_url),
        )
        path_rules = (
            (self.gpt_weights_path, ".ckpt", "gpt_weights_path"),
            (self.sovits_weights_path, ".pth", "sovits_weights_path"),
            (self.reference_audio_path, ".wav", "reference_audio_path"),
        )
        for path, suffix, label in path_rules:
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError(f"GPT-SoVITS {label} must be an absolute Path.")
            if path.suffix.casefold() != suffix:
                raise ValueError(f"GPT-SoVITS {label} must end with {suffix}.")
        if (
            not isinstance(self.prompt_text, str)
            or len(self.prompt_text) > SYNTHESIS_MAX_TEXT_CODE_POINTS
            or not _contains_prompt_text(self.prompt_text)
        ):
            raise ValueError(
                "GPT-SoVITS prompt_text must be non-blank and within the "
                "text limit."
            )
        if (
            not isinstance(self.prompt_language, str)
            or self.prompt_language not in _PROMPT_LANGUAGES
        ):
            raise ValueError("GPT-SoVITS prompt_language must be zh or en.")
        if (
            not isinstance(self.speed_factor, float)
            or not math.isfinite(self.speed_factor)
            or not SYNTHESIS_MIN_SPEED_FACTOR
            <= self.speed_factor
            <= SYNTHESIS_MAX_SPEED_FACTOR
        ):
            raise ValueError(
                "GPT-SoVITS speed_factor must be a finite float between "
                "0.5 and 2.0."
            )
        if (
            not isinstance(self.audio_format, str)
            or self.audio_format not in _AUDIO_FORMATS
        ):
            raise ValueError("GPT-SoVITS audio_format must be wav, ogg, or aac.")


class GptSovitsVoiceResolver(Protocol):
    """Resolve logical profile and emotion identifiers to trusted local data."""

    def resolve(self, profile_id: str, emotion: str) -> GptSovitsVoice:
        """Return the configured endpoint and reference for one voice choice."""
        ...


class GptSovitsSynthesizer:
    """Synthesize one request through a non-streaming loopback ``/tts`` call."""

    def __init__(
        self,
        config: GptSovitsConfig,
        voice_resolver: GptSovitsVoiceResolver,
    ) -> None:
        """Bind safe transport settings and a logical local voice resolver."""

        if not isinstance(config, GptSovitsConfig):
            raise TypeError("config must be a GptSovitsConfig.")
        if not callable(getattr(voice_resolver, "resolve", None)):
            raise TypeError("voice_resolver must provide resolve().")
        self._config = config
        self._voice_resolver = voice_resolver
        # GPT-SoVITS inference is expensive and upstream service concurrency is
        # not an application contract. Serializing calls avoids non-deterministic
        # GPU pressure until the later sentence queue owns scheduling explicitly.
        self._request_lock = Lock()

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Resolve one trusted voice and return bounded encoded speech."""

        if not isinstance(request, SynthesisRequest):
            raise SynthesisFailedError("Local speech synthesis request is invalid.")
        voice = self._voice_resolver.resolve(
            request.profile_id,
            request.emotion,
        )
        if not isinstance(voice, GptSovitsVoice):
            raise SynthesisFailedError(
                "Local speech synthesis voice configuration is invalid."
            )
        reference_path = self._verify_local_assets(voice)
        payload: dict[str, object] = {
            "text": request.text,
            "text_lang": request.language,
            "ref_audio_path": str(reference_path),
            "prompt_text": voice.prompt_text,
            "prompt_lang": voice.prompt_language,
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": voice.speed_factor,
            "seed": self._config.deterministic_seed,
            "media_type": voice.audio_format,
            "streaming_mode": False,
            "parallel_infer": False,
        }

        with self._request_lock:
            audio = self._post_tts(
                f"{voice.base_url}/tts",
                payload,
                voice.audio_format,
            )
        try:
            return SynthesisResult(
                audio=audio,
                audio_format=voice.audio_format,
                speed_factor=voice.speed_factor,
            )
        except SynthesisValidationError as error:
            # Domain validation details are useful in tests but must not expose
            # arbitrary native/service response data to later UI boundaries.
            raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE) from error

    def _verify_local_assets(self, voice: GptSovitsVoice) -> Path:
        """Resolve symlinks and keep every configured asset under one root."""

        try:
            asset_root = self._config.asset_root.resolve(strict=True)
            if not asset_root.is_dir():
                raise OSError("asset root is not a directory")
            resolved_paths = tuple(
                path.resolve(strict=True)
                for path in (
                    voice.gpt_weights_path,
                    voice.sovits_weights_path,
                    voice.reference_audio_path,
                )
            )
        except (OSError, RuntimeError) as error:
            raise SynthesisUnavailableError(
                _ASSETS_UNAVAILABLE_MESSAGE
            ) from error
        if any(
            not path.is_file() or not path.is_relative_to(asset_root)
            for path in resolved_paths
        ):
            raise SynthesisUnavailableError(_ASSETS_UNAVAILABLE_MESSAGE)
        try:
            prefixes: list[bytes] = []
            for path in resolved_paths:
                with path.open("rb") as asset_file:
                    prefix = asset_file.read(128)
                if not prefix or prefix.startswith(_GIT_LFS_POINTER_PREFIX):
                    raise OSError("asset is empty or an unresolved LFS pointer")
                prefixes.append(prefix)
            if (
                prefixes[2][:4] != b"RIFF"
                or prefixes[2][8:12] != b"WAVE"
            ):
                raise OSError("reference audio is not a RIFF WAVE file")
        except OSError as error:
            raise SynthesisUnavailableError(
                _ASSETS_UNAVAILABLE_MESSAGE
            ) from error
        return resolved_paths[2]

    def _post_tts(
        self,
        url: str,
        payload: dict[str, object],
        audio_format: SynthesisAudioFormat,
    ) -> bytes:
        """Perform one non-redirecting request and read a bounded audio body."""

        try:
            with requests.Session() as session:
                # Local synthesis must not inherit proxy variables: doing so can
                # leak reference paths/text and make loopback availability depend
                # on an unrelated proxy configuration.
                session.trust_env = False
                response = session.post(
                    url,
                    json=cast(Any, payload),
                    timeout=self._config.request_timeout_seconds,
                    allow_redirects=False,
                    stream=True,
                )
                try:
                    if response.status_code != 200:
                        raise SynthesisFailedError(_SERVICE_REJECTED_MESSAGE)
                    content_encoding = response.headers.get(
                        "Content-Encoding",
                        "identity",
                    ).strip().lower()
                    if content_encoding not in ("", "identity"):
                        raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                    content_type = response.headers.get(
                        "Content-Type",
                        "",
                    ).split(";", 1)[0].strip().lower()
                    if content_type not in _AUDIO_MEDIA_TYPES[audio_format]:
                        raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError as error:
                            raise SynthesisFailedError(
                                _INVALID_AUDIO_MESSAGE
                            ) from error
                        if (
                            declared_length <= 0
                            or declared_length > self._config.max_response_bytes
                        ):
                            raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)

                    chunks: list[bytes] = []
                    total_bytes = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > self._config.max_response_bytes:
                            raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                        chunks.append(chunk)
                    return b"".join(chunks)
                finally:
                    response.close()
        except (RequestsConnectionError, Timeout) as error:
            raise SynthesisUnavailableError(
                _SERVICE_UNAVAILABLE_MESSAGE
            ) from error
        except SynthesisFailedError:
            raise
        except RequestException as error:
            raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE) from error
