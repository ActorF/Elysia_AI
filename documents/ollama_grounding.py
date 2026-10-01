"""Adapt structured grounded-answer requests to bounded loopback Ollama HTTP.

The adapter discovers the exact installed model digest from ``/api/tags`` and
binds that identity to every request.  It checks the digest immediately before
and after inference so a mutable Ollama tag cannot silently change the model
inside one grounded answer.  Requests are non-streaming, tool-free,
reasoning-free, non-truncating, and deterministic; responses are read under
strict byte and wall-clock limits before the model-independent grounding layer
validates their citation contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
import json
import math
import re
from time import monotonic
from typing import Any, Final, cast
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout,
)

from .embedding import EmbeddingError
from .exceptions import DocumentOperationCancelledError
from .grounding import (
    MAX_GROUNDED_RESPONSE_UTF8_BYTES,
    GroundedAnswerError,
    GroundedAnswerFailedError,
    GroundedAnswerGeneratorIdentity,
    GroundedAnswerLimitError,
    GroundedAnswerRequest,
    GroundedAnswerUnavailableError,
    GroundedAnswerValidationError,
    _snapshot_request,
)
from .ollama_embedding import (
    _DeadlineHTTPAdapter,
    _WallClockDeadlineExceeded,
    _read_bounded_json_body,
    _reject_duplicate_json_pairs,
    _reject_non_finite_json_number,
    _total_http_timeout,
)


DEFAULT_OLLAMA_GROUNDED_BASE_URL: Final = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_GROUNDED_MODEL_TAG: Final = "qwen3.5:9b"
DEFAULT_OLLAMA_GROUNDED_REQUEST_TIMEOUT_SECONDS: Final = 120.0
DEFAULT_OLLAMA_GROUNDED_PROBE_TIMEOUT_SECONDS: Final = 2.0
DEFAULT_OLLAMA_GROUNDED_MAX_REQUEST_BYTES: Final = 2 * 1024 * 1024
DEFAULT_OLLAMA_GROUNDED_MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
MAX_OLLAMA_GROUNDED_HTTP_BODY_BYTES: Final = 4 * 1024 * 1024
MAX_OLLAMA_GROUNDED_MODEL_RECORDS: Final = 4_096

_PROVIDER: Final = "ollama"
_ADAPTER_ID: Final = "ollama-grounding-http"
_ADAPTER_VERSION: Final = "1.0.0"
_MODEL_TAG_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,199}$")
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_UNAVAILABLE_MESSAGE: Final = (
    "Local grounded-answer service is not running or reachable."
)
_MODEL_UNAVAILABLE_MESSAGE: Final = (
    "Configured local grounded-answer model is unavailable."
)
_REQUEST_FAILED_MESSAGE: Final = "Local grounded-answer request failed."
_INVALID_RESPONSE_MESSAGE: Final = (
    "Local grounded-answer service returned an invalid response."
)
_MODEL_CHANGED_MESSAGE: Final = (
    "Local grounded-answer model identity changed during inference."
)


def _raise_if_ollama_generation_cancelled(
    cancel_requested: Callable[[], bool] | None,
) -> None:
    """Discard a late synchronous Ollama result when cancellation won."""

    if cancel_requested is not None and cancel_requested():
        raise DocumentOperationCancelledError(
            "Grounded answer generation was cancelled before publication."
        )


def _normalize_loopback_base_url(value: object) -> str:
    """Return a canonical loopback HTTP origin or reject possible SSRF input."""

    if type(value) is not str or not value or value != value.strip():
        raise ValueError("Ollama grounded-answer base_url must be non-empty.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "http":
        raise ValueError(
            "Ollama grounded-answer base_url must use loopback HTTP."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            "Ollama grounded-answer base_url must not contain credentials."
        )
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError(
            "Ollama grounded-answer base_url must be an origin without a path."
        )
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError(
            "Ollama grounded-answer base_url must include a loopback host."
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            "Ollama grounded-answer base_url has an invalid port."
        ) from error
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError(
            "Ollama grounded-answer base_url has an invalid port."
        )

    normalized_host = hostname.lower()
    if normalized_host == "localhost":
        # Resolve the only admitted hostname before I/O so host-file or DNS
        # changes cannot move a supposedly local request off loopback.
        normalized_host = "127.0.0.1"
    else:
        try:
            address = ip_address(normalized_host)
        except ValueError as error:
            raise ValueError(
                "Ollama grounded-answer base_url must use a loopback host."
            ) from error
        if not address.is_loopback:
            raise ValueError(
                "Ollama grounded-answer base_url must use a loopback host."
            )
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit(("http", netloc, "", "", ""))


def _strict_positive_float(value: object, field_name: str, maximum: float) -> float:
    """Return one exact finite positive float within its public ceiling."""

    if (
        type(value) is not float
        or not math.isfinite(value)
        or not 0.1 <= value <= maximum
    ):
        raise ValueError(
            f"{field_name} must be a finite float from 0.1 through {maximum}."
        )
    return value


def _strict_body_limit(
    value: object,
    field_name: str,
    *,
    minimum: int,
) -> int:
    """Return one exact HTTP body limit without accepting booleans or floats."""

    if (
        type(value) is not int
        or not minimum <= value <= MAX_OLLAMA_GROUNDED_HTTP_BODY_BYTES
    ):
        raise ValueError(
            f"{field_name} must be from {minimum} through "
            f"{MAX_OLLAMA_GROUNDED_HTTP_BODY_BYTES}."
        )
    return value


@dataclass(frozen=True, slots=True)
class OllamaGroundedAnswerConfig:
    """Configure one deterministic, bounded, loopback-only Ollama generator."""

    model_tag: str = DEFAULT_OLLAMA_GROUNDED_MODEL_TAG
    base_url: str = DEFAULT_OLLAMA_GROUNDED_BASE_URL
    request_timeout_seconds: float = (
        DEFAULT_OLLAMA_GROUNDED_REQUEST_TIMEOUT_SECONDS
    )
    probe_timeout_seconds: float = DEFAULT_OLLAMA_GROUNDED_PROBE_TIMEOUT_SECONDS
    max_request_body_bytes: int = DEFAULT_OLLAMA_GROUNDED_MAX_REQUEST_BYTES
    max_response_body_bytes: int = DEFAULT_OLLAMA_GROUNDED_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        """Reject mutable aliases, remote origins, and unbounded HTTP policy."""

        if (
            type(self.model_tag) is not str
            or self.model_tag != self.model_tag.strip()
            or _MODEL_TAG_PATTERN.fullmatch(self.model_tag) is None
        ):
            raise ValueError("Ollama grounded-answer model_tag is invalid.")
        object.__setattr__(
            self,
            "base_url",
            _normalize_loopback_base_url(self.base_url),
        )
        _strict_positive_float(
            self.request_timeout_seconds,
            "request_timeout_seconds",
            300.0,
        )
        _strict_positive_float(
            self.probe_timeout_seconds,
            "probe_timeout_seconds",
            10.0,
        )
        _strict_body_limit(
            self.max_request_body_bytes,
            "max_request_body_bytes",
            minimum=1_024,
        )
        _strict_body_limit(
            self.max_response_body_bytes,
            "max_response_body_bytes",
            minimum=64 * 1_024,
        )


class OllamaGroundedAnswerAdapter:
    """Generate strict grounded-answer JSON through one exact local model.

    ``identity`` resolves the mutable Ollama tag to its current full digest.
    ``generate`` then requires that exact identity before inference and checks it
    again afterward.  The adapter never pulls models, follows redirects, trusts
    proxy environment variables, streams output, or forwards exception text.
    """

    def __init__(
        self,
        config: OllamaGroundedAnswerConfig | None = None,
    ) -> None:
        """Create a light adapter without probing, loading, or pulling a model."""

        if config is not None and type(config) is not OllamaGroundedAnswerConfig:
            raise TypeError("config must be OllamaGroundedAnswerConfig.")
        source = OllamaGroundedAnswerConfig() if config is None else config
        # Reconstruct the frozen input because ``object.__setattr__`` can mutate
        # a caller-held frozen dataclass after this constructor returns.
        self._config = OllamaGroundedAnswerConfig(
            model_tag=source.model_tag,
            base_url=source.base_url,
            request_timeout_seconds=source.request_timeout_seconds,
            probe_timeout_seconds=source.probe_timeout_seconds,
            max_request_body_bytes=source.max_request_body_bytes,
            max_response_body_bytes=source.max_response_body_bytes,
        )

    @property
    def identity(self) -> GroundedAnswerGeneratorIdentity:
        """Return the exact installed model digest without exposing local paths."""

        digest = self._read_model_digest()
        return self._identity_for_digest(digest)

    def generate(
        self,
        request: GroundedAnswerRequest,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str:
        """Return bounded JSON unless cancellation wins around inference."""

        try:
            if cancel_requested is not None and not callable(cancel_requested):
                raise GroundedAnswerValidationError(
                    "cancel_requested must be callable or None."
                )
            _raise_if_ollama_generation_cancelled(cancel_requested)
            snapshot = _snapshot_request(request)
            before_digest = self._read_model_digest()
            _raise_if_ollama_generation_cancelled(cancel_requested)
            expected_identity = self._identity_for_digest(before_digest)
            if snapshot.identity != expected_identity:
                raise GroundedAnswerUnavailableError(_MODEL_CHANGED_MESSAGE)

            policy = snapshot.policy
            if (
                policy.stream
                or policy.tools_enabled
                or policy.reasoning_enabled
                or policy.truncate
                or policy.temperature != 0.0
            ):
                raise GroundedAnswerValidationError(
                    "Grounded-answer generation policy is unsupported."
                )
            payload: dict[str, object] = {
                "model": self._config.model_tag,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in snapshot.messages
                ],
                "stream": False,
                "tools": [],
                "think": False,
                # Ollama currently uses this flag for embedding requests and
                # ignores unknown chat fields. Keeping it explicit makes the
                # adapter contract fail-safe as chat support evolves; a length
                # terminal below is rejected independently.
                "truncate": False,
                "format": "json",
                "options": {"temperature": 0.0},
            }
            response = self._request_json(
                "POST",
                "/api/chat",
                payload=payload,
                timeout_seconds=self._config.request_timeout_seconds,
                unavailable=False,
            )
            _raise_if_ollama_generation_cancelled(cancel_requested)
            after_digest = self._read_model_digest()
            _raise_if_ollama_generation_cancelled(cancel_requested)
            if after_digest != before_digest:
                raise GroundedAnswerUnavailableError(_MODEL_CHANGED_MESSAGE)
            return self._decode_chat_response(response, snapshot)
        except (GroundedAnswerError, DocumentOperationCancelledError):
            raise
        except (MemoryError, RecursionError):
            raise GroundedAnswerLimitError(
                "Local grounded-answer generation exceeded a safe resource limit."
            ) from None
        except Exception:
            raise GroundedAnswerFailedError(_REQUEST_FAILED_MESSAGE) from None

    def _identity_for_digest(
        self,
        digest: str,
    ) -> GroundedAnswerGeneratorIdentity:
        """Build one detached public identity for an already validated digest."""

        return GroundedAnswerGeneratorIdentity(
            provider=_PROVIDER,
            adapter_id=_ADAPTER_ID,
            adapter_version=_ADAPTER_VERSION,
            model_tag=self._config.model_tag,
            model_digest=digest,
        )

    def _read_model_digest(self) -> str:
        """Resolve one exact installed tag and reject conflicting tag records."""

        response = self._request_json(
            "GET",
            "/api/tags",
            payload=None,
            timeout_seconds=self._config.probe_timeout_seconds,
            unavailable=True,
        )
        records = response.get("models")
        if (
            type(records) is not list
            or len(records) > MAX_OLLAMA_GROUNDED_MODEL_RECORDS
        ):
            raise GroundedAnswerUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)

        digests: list[str] = []
        for record in records:
            if type(record) is not dict:
                continue
            if self._config.model_tag not in (
                record.get("name"),
                record.get("model"),
            ):
                continue
            digest = record.get("digest")
            if type(digest) is not str or _DIGEST_PATTERN.fullmatch(digest) is None:
                raise GroundedAnswerUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)
            digests.append(digest)
        if not digests or len(set(digests)) != 1:
            raise GroundedAnswerUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)
        return digests[0]

    def _decode_chat_response(
        self,
        response: dict[str, object],
        request: GroundedAnswerRequest,
    ) -> str:
        """Validate Ollama completion state and return exact structured text."""

        if (
            response.get("model") != self._config.model_tag
            or response.get("done") is not True
            or response.get("done_reason") != "stop"
        ):
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE)
        message = response.get("message")
        if type(message) is not dict or not set(message).issubset(
            {"role", "content", "thinking", "tool_calls", "images"}
        ):
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE)
        content = message.get("content")
        if (
            message.get("role") != "assistant"
            or type(content) is not str
            or not content
            or message.get("thinking") not in (None, "")
            or message.get("tool_calls") not in (None, [])
            or message.get("images") not in (None, [])
        ):
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE)
        try:
            encoded = content.encode("utf-8")
        except UnicodeError:
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE) from None
        if len(encoded) > request.policy.max_response_utf8_bytes:
            raise GroundedAnswerLimitError(
                "Local grounded-answer response exceeds its byte limit."
            )
        try:
            decoded = json.loads(
                content,
                object_pairs_hook=_reject_duplicate_json_pairs,
                parse_constant=_reject_non_finite_json_number,
            )
        except (UnicodeError, ValueError, RecursionError, MemoryError):
            # JSON decoder errors retain the complete source document on their
            # ``doc`` attribute, so never chain an untrusted model response.
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE) from None
        if type(decoded) is not dict:
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE)
        return content

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None,
        timeout_seconds: float,
        unavailable: bool,
    ) -> dict[str, object]:
        """Send one non-retrying bounded request and return one JSON object."""

        body: bytes | None = None
        if payload is not None:
            try:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                raise GroundedAnswerValidationError(
                    "Grounded-answer request could not be encoded safely."
                ) from None
            if len(body) > self._config.max_request_body_bytes:
                raise GroundedAnswerLimitError(
                    "Grounded-answer request exceeds its encoded byte limit."
                )

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        deadline = monotonic() + timeout_seconds
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.mount("http://", _DeadlineHTTPAdapter(deadline))
                response = session.request(
                    method,
                    f"{self._config.base_url}{path}",
                    data=body,
                    headers=headers,
                    timeout=cast(Any, _total_http_timeout(timeout_seconds)),
                    allow_redirects=False,
                    stream=True,
                )
                try:
                    raw = _read_bounded_json_body(
                        response,
                        maximum=self._config.max_response_body_bytes,
                        deadline=deadline,
                    )
                    if response.status_code != 200:
                        if unavailable:
                            raise GroundedAnswerUnavailableError(
                                _SERVICE_UNAVAILABLE_MESSAGE
                            )
                        raise GroundedAnswerFailedError(_REQUEST_FAILED_MESSAGE)
                finally:
                    response.close()
        except (GroundedAnswerError,):
            raise
        except (
            RequestsConnectionError,
            Timeout,
            _WallClockDeadlineExceeded,
        ):
            raise GroundedAnswerUnavailableError(
                _SERVICE_UNAVAILABLE_MESSAGE
            ) from None
        except EmbeddingError:
            if unavailable:
                raise GroundedAnswerUnavailableError(
                    _MODEL_UNAVAILABLE_MESSAGE
                ) from None
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE) from None
        except RequestException:
            if unavailable:
                raise GroundedAnswerUnavailableError(
                    _SERVICE_UNAVAILABLE_MESSAGE
                ) from None
            raise GroundedAnswerFailedError(_REQUEST_FAILED_MESSAGE) from None
        except (MemoryError, RecursionError):
            raise GroundedAnswerLimitError(
                "Grounded-answer HTTP work exceeded a safe resource limit."
            ) from None
        except Exception:
            if unavailable:
                raise GroundedAnswerUnavailableError(
                    _SERVICE_UNAVAILABLE_MESSAGE
                ) from None
            raise GroundedAnswerFailedError(_REQUEST_FAILED_MESSAGE) from None

        try:
            decoded: object = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_pairs,
                parse_constant=_reject_non_finite_json_number,
            )
        except (UnicodeError, ValueError, RecursionError, MemoryError):
            if unavailable:
                raise GroundedAnswerUnavailableError(
                    _MODEL_UNAVAILABLE_MESSAGE
                ) from None
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE) from None
        if type(decoded) is not dict:
            if unavailable:
                raise GroundedAnswerUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)
            raise GroundedAnswerFailedError(_INVALID_RESPONSE_MESSAGE)
        return cast(dict[str, object], decoded)
