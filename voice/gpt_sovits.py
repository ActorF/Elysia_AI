"""Adapt logical speech requests to a loopback GPT-SoVITS ``/tts`` API."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import json
import math
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Final, Literal, Protocol, TypeAlias, cast
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout,
)
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError
from urllib3.util import Timeout as Urllib3Timeout

from .synthesis import (
    SYNTHESIS_MAX_AUDIO_BYTES,
    SYNTHESIS_MAX_SPEED_FACTOR,
    SYNTHESIS_MAX_TEXT_CODE_POINTS,
    SYNTHESIS_MIN_SPEED_FACTOR,
    SynthesisAudioFormat,
    SynthesisError,
    SynthesisFailedError,
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
    SynthesisValidationError,
    _contains_spoken_text,
)


GptSovitsPromptLanguage: TypeAlias = Literal["zh", "en"]
GptSovitsStatusState: TypeAlias = Literal[
    "unavailable",
    "available",
    "ready",
]
GptSovitsStatusReason: TypeAlias = Literal[
    "catalog_missing",
    "catalog_invalid",
    "selection_unavailable",
    "assets_unavailable",
    "service_unreachable",
    "invalid_service",
    "service_binding_unverified",
]

_PROMPT_LANGUAGES: Final = ("zh", "en")
_AUDIO_FORMATS: Final = ("wav", "aac")
_AUDIO_MEDIA_TYPES: Final = {
    "wav": frozenset(
        {"audio/wav", "audio/wave", "audio/x-wav", "audio/vnd.wave"}
    ),
    "aac": frozenset({"audio/aac", "audio/aacp", "audio/x-aac"}),
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
_OPENAPI_MAX_BYTES: Final = 512 * 1024
_STATUS_STATES: Final = ("unavailable", "available", "ready")
_UNAVAILABLE_STATUS_REASONS: Final = (
    "catalog_missing",
    "catalog_invalid",
    "selection_unavailable",
    "assets_unavailable",
    "service_unreachable",
    "invalid_service",
)


class _WallClockDeadlineExceeded(Exception):
    """Mark a local HTTP operation that exhausted its total time budget."""


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
    if normalized_host == "localhost":
        # Resolve the sole accepted hostname before I/O so DNS cannot sit
        # outside the adapter's bounded socket lifecycle. IPv6 callers can
        # still configure the explicit ``[::1]`` origin.
        normalized_host = "127.0.0.1"
    else:
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


def _require_deadline(deadline: float) -> None:
    """Stop work once a request's total monotonic time budget is exhausted."""

    if monotonic() >= deadline:
        raise _WallClockDeadlineExceeded


def _total_http_timeout(total_seconds: float) -> Urllib3Timeout:
    """Share one ordinary budget across connection and response headers.

    Requests normally interprets a float as two independent inactivity limits:
    one for connecting and another for reading.  Passing urllib3's explicit
    total timeout prevents those phases from each consuming the full budget.
    Once compatible identity headers arrive, streamed reads are additionally
    shortened to the monotonic remaining deadline. Requests cannot interrupt a
    peer that drip-feeds header lines, so this is not described as a universal
    cancellable HTTP deadline; unsupported transfer shapes fail closed below.
    Requests 2.34 accepts this object even though its bundled typing currently
    describes only floats and tuples, so call sites narrow the cast locally.
    """

    return Urllib3Timeout(
        total=total_seconds,
        connect=total_seconds,
        read=total_seconds,
    )


def _require_content_length(
    value: str | None,
    *,
    max_bytes: int,
    invalid_message: str,
) -> int:
    """Parse one bounded canonical decimal length without huge-int work.

    Python deliberately rejects extremely long decimal-to-integer conversions.
    Capping digits before conversion keeps a malicious local header inside the
    adapter's typed, sanitized failure boundary and avoids needless big integers.
    """

    if (
        value is None
        or not value.isascii()
        or not value.isdigit()
        or len(value) > len(str(max_bytes))
    ):
        raise SynthesisFailedError(invalid_message)
    try:
        declared_bytes = int(value)
    except ValueError as error:
        raise SynthesisFailedError(invalid_message) from error
    if not 1 <= declared_bytes <= max_bytes:
        raise SynthesisFailedError(invalid_message)
    return declared_bytes


def _set_remaining_socket_timeout(
    response: requests.Response,
    deadline: float,
    invalid_message: str,
) -> None:
    """Limit the next raw read to the request's remaining wall-clock budget.

    Requests has no public total-body deadline. The project pins Requests and
    urllib3, so this narrow adapter may reach their active response socket while
    keeping that dependency private to this module. Failing closed if the
    expected socket shape changes is safer than silently reverting to a per-read
    timeout that a slow peer can renew indefinitely.
    """

    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _WallClockDeadlineExceeded
    raw = cast(Any, response.raw)
    connection = getattr(raw, "_connection", None)
    active_socket = getattr(connection, "sock", None)
    if active_socket is None:
        http_response = getattr(raw, "_fp", None)
        buffered_reader = getattr(http_response, "fp", None)
        socket_io = getattr(buffered_reader, "raw", None)
        active_socket = getattr(socket_io, "_sock", None)
    if active_socket is None:
        raise SynthesisFailedError(invalid_message)
    try:
        active_socket.settimeout(remaining)
    except (OSError, ValueError) as error:
        raise SynthesisFailedError(invalid_message) from error


def _read_bounded_body(
    response: requests.Response,
    *,
    deadline: float,
    max_bytes: int,
    invalid_message: str,
) -> bytes:
    """Read one identity body without allowing slow chunks to reset its budget.

    Requests' ordinary timeout is an inactivity timeout, so a peer can evade it
    by continuously dripping bytes. ``urllib3`` exposes ``read1`` through the
    streamed response; unlike a fill-the-buffer read, it returns the currently
    available bytes and lets the monotonic deadline be checked between arrivals.
    Any transport exception is translated here because raw-response exceptions
    are not guaranteed to inherit from Requests' public exception hierarchy.
    """

    chunks: list[bytes] = []
    total_bytes = 0
    raw = cast(Any, response.raw)
    read_one = getattr(raw, "read1", None)
    if not callable(read_one):
        raise SynthesisFailedError(invalid_message)
    while True:
        _require_deadline(deadline)
        if bool(getattr(raw, "closed", False)):
            break
        _set_remaining_socket_timeout(response, deadline, invalid_message)
        try:
            chunk = read_one(64 * 1024, decode_content=False)
        except (TimeoutError, Urllib3ReadTimeoutError) as error:
            raise _WallClockDeadlineExceeded from error
        except Exception as error:
            if monotonic() >= deadline:
                raise _WallClockDeadlineExceeded from error
            raise SynthesisFailedError(invalid_message) from error
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise SynthesisFailedError(invalid_message)
        chunks.append(chunk)
    _require_deadline(deadline)
    return b"".join(chunks)


@dataclass(frozen=True, slots=True)
class GptSovitsConfig:
    """Configure bounded local HTTP access and the permitted asset root."""

    asset_root: Path
    request_timeout_seconds: float = 120.0
    probe_timeout_seconds: float = 1.0
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
            not isinstance(self.probe_timeout_seconds, float)
            or not math.isfinite(self.probe_timeout_seconds)
            or not 0.1 <= self.probe_timeout_seconds <= 10.0
        ):
            raise ValueError(
                "GPT-SoVITS probe_timeout_seconds must be a finite float "
                "between 0.1 and 10.0."
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
            or "\x00" in self.prompt_text
            or not _contains_spoken_text(self.prompt_text)
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
            # GPT-SoVITS v2 rejects Ogg when streaming_mode is false. This
            # adapter is deliberately non-streaming, so accepting Ogg here would
            # defer a deterministic configuration error to an expensive call.
            raise ValueError(
                "GPT-SoVITS audio_format must be wav or aac in non-streaming mode."
            )


@dataclass(frozen=True, slots=True)
class GptSovitsStatus:
    """Expose sanitized adapter readiness without paths or service details.

    The upstream API cannot attest which weights its process loaded. A valid
    OpenAPI surface is therefore only ``available`` with
    ``service_binding_unverified``; ``ready`` is reserved for a future trusted
    launcher or attestation boundary that proves the endpoint/model binding.
    """

    state: GptSovitsStatusState
    reason: GptSovitsStatusReason | None

    def __post_init__(self) -> None:
        """Enforce strict state/reason combinations for later protocol use."""

        if self.state not in _STATUS_STATES:
            raise ValueError("GPT-SoVITS status state is invalid.")
        if self.state == "unavailable" and self.reason not in (
            _UNAVAILABLE_STATUS_REASONS
        ):
            raise ValueError("Unavailable GPT-SoVITS status needs a failure reason.")
        if self.state == "available" and self.reason != (
            "service_binding_unverified"
        ):
            raise ValueError(
                "Available GPT-SoVITS status must identify unverified binding."
            )
        if self.state == "ready" and self.reason is not None:
            raise ValueError("Ready GPT-SoVITS status must not include a reason.")


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

    def get_status(
        self,
        *,
        profile_id: str = "default",
        emotion: str = "neutral",
    ) -> GptSovitsStatus:
        """Probe assets and the API shape without performing synthesis."""

        try:
            voice = self._voice_resolver.resolve(profile_id, emotion)
            if not isinstance(voice, GptSovitsVoice):
                raise SynthesisUnavailableError("invalid voice resolver result")
        except (SynthesisError, TypeError, ValueError):
            return GptSovitsStatus("unavailable", "selection_unavailable")
        try:
            self._verify_local_assets(voice)
        except SynthesisUnavailableError:
            return GptSovitsStatus("unavailable", "assets_unavailable")
        try:
            self._probe_openapi(f"{voice.base_url}/openapi.json")
        except SynthesisUnavailableError:
            return GptSovitsStatus("unavailable", "service_unreachable")
        except SynthesisFailedError:
            return GptSovitsStatus("unavailable", "invalid_service")
        return GptSovitsStatus("available", "service_binding_unverified")

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
        """Read one length-declared identity response under a body deadline."""

        deadline = monotonic() + self._config.request_timeout_seconds
        try:
            with requests.Session() as session:
                # Local synthesis must not inherit proxy variables: doing so can
                # leak reference paths/text and make loopback availability depend
                # on an unrelated proxy configuration.
                session.trust_env = False
                response = session.post(
                    url,
                    json=cast(Any, payload),
                    timeout=cast(
                        Any,
                        _total_http_timeout(
                            self._config.request_timeout_seconds
                        ),
                    ),
                    allow_redirects=False,
                    stream=True,
                )
                try:
                    _require_deadline(deadline)
                    if response.status_code != 200:
                        raise SynthesisFailedError(_SERVICE_REJECTED_MESSAGE)
                    content_encoding = response.headers.get(
                        "Content-Encoding",
                        "identity",
                    ).strip().lower()
                    if content_encoding not in ("", "identity"):
                        raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                    if response.headers.get("Transfer-Encoding") is not None:
                        raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                    content_type = response.headers.get(
                        "Content-Type",
                        "",
                    ).split(";", 1)[0].strip().lower()
                    if content_type not in _AUDIO_MEDIA_TYPES[audio_format]:
                        raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                    content_length = response.headers.get("Content-Length")
                    declared_length = _require_content_length(
                        content_length,
                        max_bytes=self._config.max_response_bytes,
                        invalid_message=_INVALID_AUDIO_MESSAGE,
                    )

                    body = _read_bounded_body(
                        response,
                        deadline=deadline,
                        max_bytes=self._config.max_response_bytes,
                        invalid_message=_INVALID_AUDIO_MESSAGE,
                    )
                    if len(body) != declared_length:
                        raise SynthesisFailedError(_INVALID_AUDIO_MESSAGE)
                    return body
                finally:
                    response.close()
        except (
            RequestsConnectionError,
            Timeout,
            _WallClockDeadlineExceeded,
        ) as error:
            raise SynthesisUnavailableError(
                _SERVICE_UNAVAILABLE_MESSAGE
            ) from error
        except SynthesisFailedError:
            raise
        except RequestException as error:
            raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE) from error

    def _probe_openapi(self, url: str) -> None:
        """Validate one length-declared local OpenAPI response safely."""

        deadline = monotonic() + self._config.probe_timeout_seconds
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    url,
                    timeout=cast(
                        Any,
                        _total_http_timeout(
                            self._config.probe_timeout_seconds
                        ),
                    ),
                    allow_redirects=False,
                    stream=True,
                )
                try:
                    _require_deadline(deadline)
                    if response.status_code != 200:
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    content_type = response.headers.get(
                        "Content-Type",
                        "",
                    ).split(";", 1)[0].strip().lower()
                    if content_type not in (
                        "application/json",
                        "application/openapi+json",
                    ):
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    content_encoding = response.headers.get(
                        "Content-Encoding",
                        "identity",
                    ).strip().lower()
                    if content_encoding not in ("", "identity"):
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    if response.headers.get("Transfer-Encoding") is not None:
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    declared = response.headers.get("Content-Length")
                    declared_bytes = _require_content_length(
                        declared,
                        max_bytes=_OPENAPI_MAX_BYTES,
                        invalid_message=_SERVICE_REQUEST_MESSAGE,
                    )
                    body = _read_bounded_body(
                        response,
                        deadline=deadline,
                        max_bytes=_OPENAPI_MAX_BYTES,
                        invalid_message=_SERVICE_REQUEST_MESSAGE,
                    )
                    if len(body) != declared_bytes:
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    document: object = json.loads(body)
                    _require_deadline(deadline)
                    if not isinstance(document, dict):
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    paths = document.get("paths")
                    if not isinstance(paths, dict):
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                    tts_path = paths.get("/tts")
                    if not isinstance(tts_path, dict) or not isinstance(
                        tts_path.get("post"),
                        dict,
                    ):
                        raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE)
                except (UnicodeError, ValueError, RecursionError) as error:
                    raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE) from error
                finally:
                    response.close()
        except (
            RequestsConnectionError,
            Timeout,
            _WallClockDeadlineExceeded,
        ) as error:
            raise SynthesisUnavailableError(
                _SERVICE_UNAVAILABLE_MESSAGE
            ) from error
        except SynthesisError:
            raise
        except RequestException as error:
            raise SynthesisFailedError(_SERVICE_REQUEST_MESSAGE) from error
