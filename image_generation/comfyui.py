"""Adapt fixed image workflows to a bounded loopback ComfyUI HTTP API.

The raw ComfyUI server API is treated as an explicitly pinned local adapter,
not as a stable public product contract.  Every request disables proxies,
redirects, retries, and content encoding.  Runtime filenames remain opaque
HTTP query values and are never resolved as local filesystem paths.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address
import json
import math
import re
from time import monotonic, sleep
from typing import Any, Final, cast
import unicodedata
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout,
)
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError
from urllib3.util import Timeout as Urllib3Timeout

from .exceptions import (
    ImageGenerationCancelledError,
    ImageGenerationError,
    ImageGenerationFailedError,
    ImageGenerationLimitError,
    ImageGenerationProtocolError,
    ImageGenerationUnavailableError,
)
from .png import CanonicalPng, MAX_GENERATED_PNG_BYTES, canonicalize_png
from .profiles import ModelLoraProfile, ModelProfile
from .workflow import CompiledWorkflow, MAX_WORKFLOW_JSON_BYTES


DEFAULT_COMFYUI_BASE_URL: Final = "http://127.0.0.1:8188"
DEFAULT_COMFYUI_PROBE_TIMEOUT_SECONDS: Final = 2.0
DEFAULT_COMFYUI_REQUEST_TIMEOUT_SECONDS: Final = 30.0
DEFAULT_COMFYUI_JOB_TIMEOUT_SECONDS: Final = 600.0
DEFAULT_COMFYUI_POLL_INTERVAL_SECONDS: Final = 0.25
MAX_COMFYUI_JSON_BYTES: Final = 4 * 1024 * 1024
MAX_COMFYUI_QUEUE_RECORDS: Final = 4_096
COMFYUI_ADAPTER_ID: Final = "comfyui-loopback-http"
COMFYUI_ADAPTER_VERSION: Final = "1.0.0"
_REQUIRED_NODE_CLASSES: Final = (
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "KSampler",
    "VAEDecode",
    "SaveImage",
)
_UNAVAILABLE_MESSAGE: Final = "Local image runtime is not running or reachable."
_INVALID_RESPONSE_MESSAGE: Final = "Local image runtime returned an invalid response."
_FAILED_MESSAGE: Final = "Local image generation failed."


class _WallClockDeadlineExceeded(Exception):
    """Mark a local HTTP operation that exhausted its absolute time budget."""


def _require_deadline(deadline: float) -> None:
    """Stop a streamed response once its single monotonic budget expires."""

    if monotonic() >= deadline:
        raise _WallClockDeadlineExceeded


def _remaining_deadline_seconds(deadline: float) -> float:
    """Return positive time remaining under one absolute monotonic deadline."""

    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _WallClockDeadlineExceeded
    return remaining


def _total_http_timeout(total_seconds: float) -> Urllib3Timeout:
    """Share one bounded budget across connection and response setup.

    Requests normally treats its timeout as a renewable inactivity limit.
    Urllib3's total timeout bounds setup phases, while streamed body reads below
    pin every receive to the same non-renewable monotonic deadline.
    """

    return Urllib3Timeout(
        total=total_seconds,
        connect=total_seconds,
        read=total_seconds,
    )


def _set_remaining_socket_timeout(
    response: requests.Response,
    deadline: float,
) -> None:
    """Pin the next raw receive to the request's remaining wall-clock budget.

    Requests has no public total-body deadline.  This project pins Requests and
    urllib3, so this narrow loopback adapter can inspect their active response
    socket.  If that expected shape changes, failing closed is safer than
    silently restoring a renewable per-read timeout.
    """

    remaining = _remaining_deadline_seconds(deadline)
    raw = cast(Any, response.raw)
    connection = getattr(raw, "_connection", None)
    active_socket = getattr(connection, "sock", None)
    if active_socket is None:
        http_response = getattr(raw, "_fp", None)
        buffered_reader = getattr(http_response, "fp", None)
        socket_io = getattr(buffered_reader, "raw", None)
        active_socket = getattr(socket_io, "_sock", None)
    if active_socket is None:
        raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
    try:
        active_socket.settimeout(remaining)
    except (OSError, ValueError) as error:
        raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE) from error


def _read_bounded_identity_body(
    response: requests.Response,
    *,
    deadline: float,
    max_bytes: int,
) -> bytes:
    """Read one identity body under byte and absolute wall-clock ceilings.

    ``read1`` returns currently available bytes rather than waiting to fill the
    entire chunk.  Re-pinning the socket before each such receive prevents a
    loopback peer from keeping the request alive by drip-feeding small chunks.
    Raw urllib3 failures are translated here because they need not inherit from
    Requests' public exception hierarchy.
    """

    chunks: list[bytes] = []
    observed = 0
    raw = cast(Any, response.raw)
    read_one = getattr(raw, "read1", None)
    if not callable(read_one):
        raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
    while True:
        _require_deadline(deadline)
        if bool(getattr(raw, "closed", False)):
            break
        _set_remaining_socket_timeout(response, deadline)
        try:
            chunk = read_one(64 * 1024, decode_content=False)
        except (TimeoutError, Urllib3ReadTimeoutError) as error:
            raise _WallClockDeadlineExceeded from error
        except Exception as error:
            if monotonic() >= deadline:
                raise _WallClockDeadlineExceeded from error
            raise ImageGenerationProtocolError(
                _INVALID_RESPONSE_MESSAGE
            ) from error
        _require_deadline(deadline)
        if not isinstance(chunk, bytes):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        if not chunk:
            break
        observed += len(chunk)
        if observed > max_bytes:
            raise ImageGenerationLimitError(
                "Local image runtime response exceeds its byte budget."
            )
        chunks.append(chunk)
    _require_deadline(deadline)
    return b"".join(chunks)


def normalize_comfyui_base_url(value: object) -> str:
    """Return a canonical loopback HTTP origin or reject possible SSRF input."""

    if type(value) is not str or not value or value != value.strip():
        raise ValueError("ComfyUI base_url must be a non-empty origin.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "http":
        raise ValueError("ComfyUI base_url must use loopback HTTP.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ComfyUI base_url must not contain credentials.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("ComfyUI base_url must not contain a path or query.")
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("ComfyUI base_url must include a loopback host.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("ComfyUI base_url has an invalid port.") from error
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("ComfyUI base_url has an invalid port.")
    normalized_host = hostname.lower()
    if normalized_host == "localhost":
        # Resolving localhost before I/O prevents hosts-file or DNS changes from
        # moving a supposedly local runtime outside the machine.
        normalized_host = "127.0.0.1"
    else:
        try:
            address = ip_address(normalized_host)
        except ValueError as error:
            raise ValueError("ComfyUI base_url must use a loopback host.") from error
        if not address.is_loopback:
            raise ValueError("ComfyUI base_url must use a loopback host.")
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit(("http", netloc, "", "", ""))


def _bounded_float(value: object, field_name: str, maximum: float) -> float:
    """Validate one exact finite positive timeout or polling interval."""

    if (
        type(value) is not float
        or not math.isfinite(value)
        or not 0.01 <= value <= maximum
    ):
        raise ValueError(
            f"{field_name} must be a finite float from 0.01 through {maximum}."
        )
    return value


@dataclass(frozen=True, slots=True)
class ComfyUiConfig:
    """Configure one direct, bounded, loopback-only ComfyUI adapter."""

    base_url: str = DEFAULT_COMFYUI_BASE_URL
    probe_timeout_seconds: float = DEFAULT_COMFYUI_PROBE_TIMEOUT_SECONDS
    request_timeout_seconds: float = DEFAULT_COMFYUI_REQUEST_TIMEOUT_SECONDS
    job_timeout_seconds: float = DEFAULT_COMFYUI_JOB_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_COMFYUI_POLL_INTERVAL_SECONDS
    exclusive_instance: bool = False

    def __post_init__(self) -> None:
        """Canonicalize the origin and reject unbounded network policy."""

        object.__setattr__(
            self,
            "base_url",
            normalize_comfyui_base_url(self.base_url),
        )
        _bounded_float(self.probe_timeout_seconds, "probe_timeout_seconds", 10.0)
        _bounded_float(
            self.request_timeout_seconds,
            "request_timeout_seconds",
            120.0,
        )
        _bounded_float(self.job_timeout_seconds, "job_timeout_seconds", 3_600.0)
        _bounded_float(
            self.poll_interval_seconds,
            "poll_interval_seconds",
            5.0,
        )
        if type(self.exclusive_instance) is not bool:
            raise ValueError("exclusive_instance must be a boolean.")


@dataclass(frozen=True, slots=True)
class ComfyPromptHandle:
    """Correlate one submitted prompt without exposing its workflow graph."""

    prompt_id: str
    client_id: str
    output_filename_prefix: str

    def __post_init__(self) -> None:
        """Require canonical UUIDs before using either value in an HTTP path."""

        for value, field_name in (
            (self.prompt_id, "prompt_id"),
            (self.client_id, "client_id"),
        ):
            if type(value) is not str:
                raise ImageGenerationProtocolError(f"{field_name} is invalid.")
            try:
                parsed = UUID(value)
            except ValueError as error:
                raise ImageGenerationProtocolError(
                    f"{field_name} is invalid."
                ) from error
            if str(parsed) != value:
                raise ImageGenerationProtocolError(f"{field_name} is invalid.")
        if (
            type(self.output_filename_prefix) is not str
            or re.fullmatch(
                r"Elysia-[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                self.output_filename_prefix,
            )
            is None
        ):
            raise ImageGenerationProtocolError(
                "output_filename_prefix is invalid."
            )


@dataclass(frozen=True, slots=True)
class ComfyOutputDescriptor:
    """Identify one runtime output only for a subsequent bounded HTTP fetch."""

    filename: str
    subfolder: str
    output_type: str

    def __post_init__(self) -> None:
        """Reject traversal and aliases without interpreting a server path."""

        if (
            type(self.filename) is not str
            or not self.filename
            or len(self.filename) > 255
            or self.filename != self.filename.strip()
            or "/" in self.filename
            or "\\" in self.filename
            or ":" in self.filename
            or self.filename in {".", ".."}
            or not self.filename.lower().endswith(".png")
            or any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                for character in self.filename
            )
        ):
            raise ImageGenerationProtocolError(
                "Local image runtime returned an invalid output name."
            )
        if (
            type(self.subfolder) is not str
            or self.subfolder != ""
        ):
            raise ImageGenerationProtocolError(
                "Local image runtime returned an invalid output group."
            )
        if self.output_type != "output":
            raise ImageGenerationProtocolError(
                "Local image runtime returned an invalid output type."
            )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject JSON objects whose duplicate keys could change validation."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    """Reject NaN and infinity spellings admitted by Python's JSON parser."""

    raise ValueError(f"non-finite JSON number: {value}")


class HttpComfyUiClient:
    """Submit and observe fixed workflows through the raw local ComfyUI API.

    The caller owns higher-level job admission and GPU coordination.  This
    adapter serializes no global state and only interrupts a running prompt when
    ``exclusive_instance`` proves Elysia owns the whole ComfyUI instance.
    """

    def __init__(
        self,
        config: ComfyUiConfig | None = None,
        *,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        """Capture immutable network policy without performing any I/O."""

        if config is not None and type(config) is not ComfyUiConfig:
            raise TypeError("config must be ComfyUiConfig.")
        if not callable(session_factory):
            raise TypeError("session_factory must be callable.")
        source = ComfyUiConfig() if config is None else config
        self._config = ComfyUiConfig(
            base_url=source.base_url,
            probe_timeout_seconds=source.probe_timeout_seconds,
            request_timeout_seconds=source.request_timeout_seconds,
            job_timeout_seconds=source.job_timeout_seconds,
            poll_interval_seconds=source.poll_interval_seconds,
            exclusive_instance=source.exclusive_instance,
        )
        self._session_factory = session_factory

    @property
    def config(self) -> ComfyUiConfig:
        """Return the immutable normalized adapter configuration."""

        return self._config

    @property
    def is_managed_private_runtime(self) -> bool:
        """Return false because this adapter attaches to an external runtime."""

        return False

    @property
    def managed_runtime_capability(self) -> None:
        """Expose no supervisor capability for a raw external attachment."""

        return None

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        expected_content_type: str | None,
        body: bytes | None = None,
        params: dict[str, str] | None = None,
    ) -> bytes:
        """Perform one zero-redirect request and read a bounded identity body."""

        headers = {"Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        deadline = monotonic() + timeout_seconds
        try:
            with self._session_factory() as session:
                session.trust_env = False
                session.mount("http://", HTTPAdapter(max_retries=0))
                response = session.request(
                    method,
                    f"{self._config.base_url}{path}",
                    allow_redirects=False,
                    data=body,
                    headers=headers,
                    params=params,
                    stream=True,
                    timeout=cast(Any, _total_http_timeout(timeout_seconds)),
                )
                try:
                    if response.is_redirect or not 200 <= response.status_code < 300:
                        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
                    content_encoding = response.headers.get(
                        "Content-Encoding",
                        "identity",
                    ).lower()
                    if content_encoding not in {"", "identity"}:
                        raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
                    content_type = response.headers.get("Content-Type", "")
                    if (
                        expected_content_type is not None
                        and content_type.split(";", 1)[0].strip().lower()
                        != expected_content_type
                    ):
                        raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
                    declared_length = response.headers.get("Content-Length")
                    if declared_length is not None:
                        try:
                            parsed_length = int(declared_length, 10)
                        except ValueError as error:
                            raise ImageGenerationProtocolError(
                                _INVALID_RESPONSE_MESSAGE
                            ) from error
                        if parsed_length < 0 or parsed_length > max_bytes:
                            raise ImageGenerationLimitError(
                                "Local image runtime response exceeds its byte budget."
                            )
                    result = _read_bounded_identity_body(
                        response,
                        deadline=deadline,
                        max_bytes=max_bytes,
                    )
                    if (
                        declared_length is not None
                        and len(result) != parsed_length
                    ):
                        raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
                    return bytes(result)
                finally:
                    response.close()
        except (ImageGenerationLimitError, ImageGenerationProtocolError, ImageGenerationUnavailableError):
            raise
        except (
            _WallClockDeadlineExceeded,
            RequestsConnectionError,
            Timeout,
            RequestException,
            OSError,
        ) as error:
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        timeout_seconds: float,
        body: bytes | None = None,
    ) -> Any:
        """Decode one bounded exact JSON response without duplicate keys."""

        raw = self._request_bytes(
            method,
            path,
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_COMFYUI_JSON_BYTES,
            expected_content_type="application/json",
            body=body,
        )
        try:
            return json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_non_finite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE) from error

    def _request_empty_success(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
    ) -> None:
        """Accept one bounded successful control response with no media contract."""

        self._request_bytes(
            method,
            path,
            timeout_seconds=self._config.request_timeout_seconds,
            max_bytes=64 * 1024,
            expected_content_type=None,
            body=body,
        )

    def ensure_ready(self, profile: ModelProfile, *, lora: ModelLoraProfile | None = None) -> None:
        """Verify required built-in nodes and the selected checkpoint/LoRA keys.

        The raw ComfyUI model-list endpoint does not expose a file digest.  The
        profile's SHA-256 records the administrator's declaration; the managed
        runtime verifies and pins actual files before exposing this adapter.
        Unselected optional LoRAs do not affect base runtime readiness.
        """

        if type(profile) is not ModelProfile:
            raise TypeError("profile must be ModelProfile.")
        if lora is not None and (
            type(lora) is not ModelLoraProfile or profile.lora(lora.lora_id) is not lora
        ):
            raise TypeError("lora must be the exact reviewed profile LoRA.")
        node_classes = (*_REQUIRED_NODE_CLASSES, "LoraLoader") if lora is not None else _REQUIRED_NODE_CLASSES
        for node_class in node_classes:
            document = self._request_json(
                "GET",
                f"/object_info/{node_class}",
                timeout_seconds=self._config.probe_timeout_seconds,
            )
            if not isinstance(document, dict) or node_class not in document:
                raise ImageGenerationUnavailableError(
                    "Local image runtime is missing a required built-in node."
                )
        models = self._request_json(
            "GET",
            "/models/checkpoints",
            timeout_seconds=self._config.probe_timeout_seconds,
        )
        if (
            not isinstance(models, list)
            or len(models) > MAX_COMFYUI_QUEUE_RECORDS
            or not all(type(model) is str for model in models)
        ):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        if profile.checkpoint_name not in cast(list[str], models):
            raise ImageGenerationUnavailableError(
                "The configured local image checkpoint is unavailable."
            )
        if lora is not None:
            loras = self._request_json(
                "GET", "/models/loras", timeout_seconds=self._config.probe_timeout_seconds,
            )
            if (
                not isinstance(loras, list)
                or len(loras) > MAX_COMFYUI_QUEUE_RECORDS
                or not all(type(model) is str for model in loras)
            ):
                raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
            if lora.lora_name not in loras:
                raise ImageGenerationUnavailableError("The selected reviewed LoRA is unavailable.")

    def probe_ready(self) -> None:
        """Confirm that ComfyUI finished route and node initialization.

        Current ComfyUI registers ``/system_stats`` only after extra-node
        initialization and route assembly complete.  The response also embeds
        the child command line, so this method validates only the minimum
        readiness fields and never returns or logs the document.
        """

        document = self._request_json(
            "GET",
            "/system_stats",
            timeout_seconds=self._config.probe_timeout_seconds,
        )
        if not isinstance(document, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        system = document.get("system")
        devices = document.get("devices")
        if (
            not isinstance(system, dict)
            or type(system.get("comfyui_version")) is not str
            or not system["comfyui_version"].strip()
            or not isinstance(devices, list)
            or len(devices) > 256
        ):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Mint a prompt identity before any submission network I/O occurs.

        Separating identity creation from submission lets a durable caller
        record the handle first and reconcile ComfyUI after a lost POST
        response instead of creating an untraceable GPU job.
        """

        if type(workflow) is not CompiledWorkflow:
            raise TypeError("workflow must be CompiledWorkflow.")
        return ComfyPromptHandle(
            prompt_id=str(uuid4()),
            client_id=str(uuid4()),
            output_filename_prefix=workflow.output_filename_prefix,
        )

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> None:
        """Submit one fixed graph under a pre-recordable correlated handle."""

        if type(handle) is not ComfyPromptHandle:
            raise TypeError("handle must be ComfyPromptHandle.")
        if type(workflow) is not CompiledWorkflow:
            raise TypeError("workflow must be CompiledWorkflow.")
        if handle.output_filename_prefix != workflow.output_filename_prefix:
            raise ImageGenerationProtocolError(
                "Prepared prompt does not match the compiled workflow."
            )
        payload = json.dumps(
            {
                "client_id": handle.client_id,
                "prompt_id": handle.prompt_id,
                "prompt": workflow.request_graph(),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_WORKFLOW_JSON_BYTES:
            raise ImageGenerationLimitError(
                "Compiled workflow request exceeds its fixed byte budget."
            )
        document = self._request_json(
            "POST",
            "/prompt",
            timeout_seconds=self._config.request_timeout_seconds,
            body=payload,
        )
        if not isinstance(document, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        node_errors = document.get("node_errors", {})
        if not isinstance(node_errors, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        if node_errors:
            raise ImageGenerationFailedError(_FAILED_MESSAGE)
        prompt_id = document.get("prompt_id")
        if prompt_id != handle.prompt_id:
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)

    def submit(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Prepare and submit one graph for compatibility with sync callers."""

        handle = self.prepare(workflow)
        self.submit_prepared(handle, workflow)
        return handle

    def _history_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        timeout_seconds: float,
    ) -> ComfyOutputDescriptor | None:
        """Read a terminal descriptor or report that one prompt is still active."""

        document = self._request_json(
            "GET",
            f"/history/{handle.prompt_id}",
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(document, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        if not document:
            return None
        if set(document) != {handle.prompt_id}:
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        record = document[handle.prompt_id]
        if not isinstance(record, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        prompt_record = record.get("prompt")
        if (
            not isinstance(prompt_record, list)
            or len(prompt_record) < 4
            or prompt_record[1] != handle.prompt_id
            or not isinstance(prompt_record[3], dict)
            or prompt_record[3].get("client_id") != handle.client_id
        ):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        status = record.get("status")
        if not isinstance(status, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        completed = status.get("completed")
        status_text = status.get("status_str")
        if status_text in {"error", "failed"}:
            raise ImageGenerationFailedError(_FAILED_MESSAGE)
        if completed is False:
            return None
        if completed is not True or status_text != "success":
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        outputs = record.get("outputs")
        if not isinstance(outputs, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        node_output = outputs.get(expected_output_node_id)
        if not isinstance(node_output, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        images = node_output.get("images")
        if not isinstance(images, list) or len(images) != 1:
            raise ImageGenerationProtocolError(
                "Local image runtime returned an unexpected output count."
            )
        image = images[0]
        if (
            not isinstance(image, dict)
            or not {"filename", "subfolder", "type"}.issubset(image)
        ):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        filename = image["filename"]
        if (
            type(filename) is not str
            or re.fullmatch(
                re.escape(handle.output_filename_prefix) + r"_\d{5}_\.png",
                filename,
            )
            is None
        ):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        return ComfyOutputDescriptor(
            filename=filename,
            subfolder=image["subfolder"],
            output_type=image["type"],
        )

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Poll one correlated prompt until output, cancellation, or deadline."""

        if type(handle) is not ComfyPromptHandle:
            raise TypeError("handle must be ComfyPromptHandle.")
        if type(expected_output_node_id) is not str or not expected_output_node_id:
            raise TypeError("expected_output_node_id must be non-empty text.")
        deadline = monotonic() + self._config.job_timeout_seconds
        while True:
            if cancel_requested is not None and cancel_requested():
                self._cancel_without_masking_outcome(handle)
                raise ImageGenerationCancelledError(
                    "Local image generation was cancelled."
                )
            remaining = deadline - monotonic()
            if remaining <= 0:
                self._cancel_without_masking_outcome(handle)
                raise ImageGenerationUnavailableError(
                    "Local image generation exceeded its time budget."
                )
            try:
                output = self._history_output(
                    handle,
                    expected_output_node_id,
                    timeout_seconds=min(
                        self._config.request_timeout_seconds,
                        remaining,
                    ),
                )
            except ImageGenerationUnavailableError as error:
                # The final history request may consume the remaining job
                # budget.  Preserve the operation-level deadline rather than
                # exposing a race-dependent transport-unavailable message.
                if monotonic() < deadline:
                    raise
                self._cancel_without_masking_outcome(handle)
                raise ImageGenerationUnavailableError(
                    "Local image generation exceeded its time budget."
                ) from error
            if output is not None:
                return output
            remaining = deadline - monotonic()
            if remaining <= 0:
                self._cancel_without_masking_outcome(handle)
                raise ImageGenerationUnavailableError(
                    "Local image generation exceeded its time budget."
                )
            sleep(min(self._config.poll_interval_seconds, remaining))

    def _cancel_without_masking_outcome(self, handle: ComfyPromptHandle) -> None:
        """Attempt safe physical cancellation while preserving caller intent.

        Cancellation is optional on a shared/raw ComfyUI instance and logical
        discard remains authoritative.  A queue transport failure must not
        replace a user cancellation or elapsed job deadline with an unrelated
        availability error; the managed supervisor later owns terminal cleanup.
        """

        try:
            self.cancel(handle)
        except ImageGenerationError:
            return

    @staticmethod
    def _queue_owners(value: object) -> tuple[tuple[str, str | None], ...]:
        """Extract bounded prompt/client ownership from ComfyUI queue records."""

        if not isinstance(value, list) or len(value) > MAX_COMFYUI_QUEUE_RECORDS:
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        owners: list[tuple[str, str | None]] = []
        for record in value:
            if (
                not isinstance(record, list)
                or len(record) < 4
                or type(record[1]) is not str
                or not isinstance(record[3], dict)
            ):
                raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
            try:
                parsed = UUID(record[1])
            except ValueError as error:
                raise ImageGenerationProtocolError(
                    _INVALID_RESPONSE_MESSAGE
                ) from error
            if str(parsed) != record[1]:
                raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
            client_id = record[3].get("client_id")
            if client_id is not None and type(client_id) is not str:
                raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
            owners.append((record[1], client_id))
        prompt_ids = tuple(prompt_id for prompt_id, _client_id in owners)
        if len(set(prompt_ids)) != len(prompt_ids):
            # A prompt identifier must describe one queue record.  Conflicting
            # ownership is ambiguous, so cancellation must not mutate state.
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        return tuple(owners)

    def cancel(self, handle: ComfyPromptHandle) -> bool:
        """Remove our queued prompt or safely interrupt an exclusive instance.

        ComfyUI's active interrupt endpoint is instance-global.  On a shared
        instance an active prompt is therefore only cancelled logically by the
        caller; its eventual output must be discarded.  The method returns
        ``True`` only when a physical cancellation request was safely issued.
        """

        if type(handle) is not ComfyPromptHandle:
            raise TypeError("handle must be ComfyPromptHandle.")
        document = self._request_json(
            "GET",
            "/queue",
            timeout_seconds=self._config.request_timeout_seconds,
        )
        if not isinstance(document, dict):
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        running = self._queue_owners(document.get("queue_running"))
        pending = self._queue_owners(document.get("queue_pending"))
        if {prompt_id for prompt_id, _client_id in running}.intersection(
            prompt_id for prompt_id, _client_id in pending
        ):
            # The same prompt cannot safely be both active and pending.  Treat
            # an inconsistent queue snapshot as untrusted instead of guessing.
            raise ImageGenerationProtocolError(_INVALID_RESPONSE_MESSAGE)
        owner = (handle.prompt_id, handle.client_id)
        if owner in pending:
            payload = json.dumps(
                {"delete": [handle.prompt_id]},
                separators=(",", ":"),
            ).encode("utf-8")
            self._request_empty_success(
                "POST",
                "/queue",
                body=payload,
            )
            return True
        if (
            self._config.exclusive_instance
            and running == (owner,)
        ):
            payload = json.dumps(
                {"prompt_id": handle.prompt_id},
                separators=(",", ":"),
            ).encode("utf-8")
            self._request_empty_success(
                "POST",
                "/interrupt",
                body=payload,
            )
            return True
        return False

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Fetch one opaque runtime output and return a canonical verified PNG."""

        if type(descriptor) is not ComfyOutputDescriptor:
            raise TypeError("descriptor must be ComfyOutputDescriptor.")
        for value, field_name in (
            (expected_width, "expected_width"),
            (expected_height, "expected_height"),
        ):
            if type(value) is not int or value <= 0:
                raise TypeError(f"{field_name} must be a positive integer.")
        raw = self._request_bytes(
            "GET",
            "/view",
            timeout_seconds=self._config.request_timeout_seconds,
            max_bytes=MAX_GENERATED_PNG_BYTES,
            expected_content_type="image/png",
            params={
                "filename": descriptor.filename,
                "subfolder": descriptor.subfolder,
                "type": descriptor.output_type,
            },
        )
        return canonicalize_png(
            raw,
            expected_width=expected_width,
            expected_height=expected_height,
        )


__all__ = [
    "COMFYUI_ADAPTER_ID",
    "COMFYUI_ADAPTER_VERSION",
    "ComfyOutputDescriptor",
    "ComfyPromptHandle",
    "ComfyUiConfig",
    "DEFAULT_COMFYUI_BASE_URL",
    "HttpComfyUiClient",
    "MAX_COMFYUI_JSON_BYTES",
    "normalize_comfyui_base_url",
]
