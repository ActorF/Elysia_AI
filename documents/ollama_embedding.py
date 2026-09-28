"""Adapt exact embedding requests to a bounded loopback Ollama API.

The adapter accepts only the one reviewed embedding manifest and never pulls,
downloads, or resolves a mutable alias.  It uses direct HTTP so the project's
contract does not depend on the transitively installed ``ollama`` package.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from http.client import HTTPResponse as HTTPClientResponse
from ipaddress import ip_address
import json
import math
from time import monotonic
from typing import Any, Final, cast
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout,
)
from urllib3 import PoolManager
from urllib3.connection import HTTPConnection as Urllib3HTTPConnection
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError
from urllib3.util import Timeout as Urllib3Timeout

from .embedding import (
    DEFAULT_EMBEDDING_ADAPTER_ID,
    DEFAULT_EMBEDDING_ADAPTER_VERSION,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MAX_BATCH_ITEMS,
    DEFAULT_EMBEDDING_MODEL_DIGEST,
    DEFAULT_EMBEDDING_MODEL_TAG,
    DEFAULT_EMBEDDING_NORMALIZATION,
    DEFAULT_EMBEDDING_PROVIDER,
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingFailedError,
    EmbeddingInput,
    EmbeddingLimitError,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingUnavailableError,
    EmbeddingValidationError,
    EmbeddingVector,
    _unit_vector,
)


DEFAULT_OLLAMA_EMBEDDING_BASE_URL: Final = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS: Final = 120.0
DEFAULT_OLLAMA_EMBEDDING_PROBE_TIMEOUT_SECONDS: Final = 2.0
DEFAULT_OLLAMA_EMBEDDING_MAX_REQUEST_BYTES: Final = 256 * 1024
DEFAULT_OLLAMA_EMBEDDING_MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
MAX_OLLAMA_EMBEDDING_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_OLLAMA_MODEL_RECORDS: Final = 4_096
MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES: Final = 8 * 1024
MAX_OLLAMA_HTTP_FRAMING_BYTES: Final = 64 * 1024
MAX_OLLAMA_HTTP_FRAMING_LINES: Final = 1_024

_SERVICE_UNAVAILABLE_MESSAGE: Final = (
    "Local embedding service is not running or reachable."
)
_MODEL_UNAVAILABLE_MESSAGE: Final = (
    "Configured local embedding model is unavailable."
)
_REQUEST_FAILED_MESSAGE: Final = "Local embedding request failed."
_INVALID_RESPONSE_MESSAGE: Final = (
    "Local embedding service returned an invalid response."
)
_MODEL_CHANGED_MESSAGE: Final = (
    "Local embedding model identity changed during inference."
)


class _WallClockDeadlineExceeded(Exception):
    """Mark a local HTTP operation that exhausted its absolute time budget."""


class _HTTPFramingLimitExceeded(Exception):
    """Mark chunk-size or trailer framing beyond a fixed resource ceiling."""


class _AbsoluteDeadlineReader:
    """Read a socket-backed stream under one non-renewable deadline.

    Socket inactivity timeouts renew whenever a slow peer produces another
    byte.  This wrapper instead performs at most one underlying ``read1`` per
    deadline check, resetting the socket timeout to the *remaining* absolute
    budget before every operation.  A small private buffer preserves bytes
    read past a header or framing newline without losing body data.
    """

    def __init__(self, reader: object, socket: object, deadline: float) -> None:
        """Capture the buffered reader, active socket, and absolute deadline."""

        self._reader = reader
        self._socket = socket
        self._deadline = deadline
        self._pending = bytearray()

    def _read_once(self, size: int) -> bytes:
        """Perform at most one raw read using the remaining wall-clock budget."""

        if size <= 0:
            return b""
        remaining = _remaining_deadline_seconds(self._deadline)
        set_timeout = getattr(self._socket, "settimeout", None)
        read_one = getattr(self._reader, "read1", None)
        if not callable(set_timeout) or not callable(read_one):
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
        try:
            set_timeout(remaining)
        except (OSError, ValueError) as error:
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error
        data = read_one(size)
        _require_deadline(self._deadline)
        if not isinstance(data, bytes):
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
        return data

    def read1(self, size: int = -1) -> bytes:
        """Return pending bytes or make one deadline-pinned raw read."""

        _require_deadline(self._deadline)
        if size == 0:
            return b""
        if self._pending:
            count = len(self._pending) if size < 0 else min(size, len(self._pending))
            data = bytes(self._pending[:count])
            del self._pending[:count]
            _require_deadline(self._deadline)
            return data
        request_size = 64 * 1024 if size < 0 else size
        return self._read_once(request_size)

    def read(self, size: int = -1) -> bytes:
        """Read the requested bytes without renewing the absolute deadline."""

        if size == 0:
            return b""
        result = bytearray()
        while size < 0 or len(result) < size:
            remaining = 64 * 1024 if size < 0 else size - len(result)
            chunk = self.read1(remaining)
            if not chunk:
                break
            result.extend(chunk)
        return bytes(result)

    def readline(self, limit: int = -1) -> bytes:
        """Read one line while repinning every possible socket receive."""

        result = bytearray()
        while limit < 0 or len(result) < limit:
            _require_deadline(self._deadline)
            if not self._pending:
                request_size = 4 * 1024
                if limit >= 0:
                    request_size = min(request_size, limit - len(result))
                chunk = self._read_once(request_size)
                if not chunk:
                    break
                self._pending.extend(chunk)
            available = len(self._pending)
            if limit >= 0:
                available = min(available, limit - len(result))
            newline = self._pending.find(b"\n", 0, available)
            take = newline + 1 if newline >= 0 else available
            result.extend(self._pending[:take])
            del self._pending[:take]
            if newline >= 0:
                break
        _require_deadline(self._deadline)
        return bytes(result)

    def readinto(self, buffer: Any) -> int:
        """Fill one writable buffer through the same absolute-deadline path."""

        view = memoryview(buffer)
        data = self.read(len(view))
        view[:len(data)] = data
        return len(data)

    def __getattr__(self, name: str) -> object:
        """Delegate lifecycle and inspection attributes to the real reader."""

        return getattr(self._reader, name)


class _BoundedHTTPFramingReader:
    """Delegate socket reads while imposing a strict framing-line ceiling.

    ``http.client`` removes chunk-size and trailer lines below urllib3's body
    interface.  Wrapping its buffered reader is therefore the only place this
    adapter can bound those bytes before they disappear from the observed body
    count.  Ordinary body ``read``/``read1`` operations remain delegated.
    """

    def __init__(self, reader: object, deadline: float) -> None:
        """Capture the reader, deadline, and aggregate framing counters."""

        self._reader = reader
        self._deadline = deadline
        self._observed_bytes = 0
        self._observed_lines = 0

    def readline(self, limit: int = -1) -> bytes:
        """Read one framing line with one byte of overflow detection."""

        _require_deadline(self._deadline)
        read_line = getattr(self._reader, "readline", None)
        if not callable(read_line):
            raise _HTTPFramingLimitExceeded
        bounded_limit = MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES + 1
        if limit >= 0:
            bounded_limit = min(limit, bounded_limit)
        line = read_line(bounded_limit)
        _require_deadline(self._deadline)
        if (
            not isinstance(line, bytes)
            or len(line) > MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES
        ):
            raise _HTTPFramingLimitExceeded
        self._observed_bytes += len(line)
        self._observed_lines += 1
        if (
            self._observed_bytes > MAX_OLLAMA_HTTP_FRAMING_BYTES
            or self._observed_lines > MAX_OLLAMA_HTTP_FRAMING_LINES
        ):
            raise _HTTPFramingLimitExceeded
        return line

    def __getattr__(self, name: str) -> object:
        """Delegate non-line operations required by ``http.client``."""

        return getattr(self._reader, name)


class _DeadlineHTTPClientResponse(HTTPClientResponse):
    """Install absolute-deadline reads before status/header parsing starts."""

    def __init__(
        self,
        socket: Any,
        debuglevel: int = 0,
        method: str | None = None,
        url: str | None = None,
        *,
        absolute_deadline: float,
    ) -> None:
        """Wrap the socket file immediately after ``HTTPResponse`` creates it."""

        super().__init__(socket, debuglevel, method=method, url=url)
        if self.fp is None:
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
        self.fp = cast(
            Any,
            _AbsoluteDeadlineReader(self.fp, socket, absolute_deadline),
        )


class _DeadlineHTTPConnection(Urllib3HTTPConnection):
    """Create loopback connections whose headers share one absolute deadline."""

    def __init__(
        self,
        *args: Any,
        absolute_deadline: float,
        **kwargs: Any,
    ) -> None:
        """Bind the per-request deadline to connect and response construction."""

        self._absolute_deadline = absolute_deadline
        super().__init__(*args, **kwargs)
        self.response_class = cast(
            Any,
            partial(
                _DeadlineHTTPClientResponse,
                absolute_deadline=absolute_deadline,
            ),
        )

    def _new_conn(self) -> Any:
        """Bound the loopback connect itself by the remaining absolute budget."""

        remaining = _remaining_deadline_seconds(self._absolute_deadline)
        if isinstance(self.timeout, (int, float)):
            self.timeout = min(float(self.timeout), remaining)
        else:
            self.timeout = remaining
        socket = super()._new_conn()
        remaining = _remaining_deadline_seconds(self._absolute_deadline)
        try:
            socket.settimeout(remaining)
        except (OSError, ValueError) as error:
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error
        return socket

    def send(self, data: Any) -> None:
        """Keep request headers and body inside the same absolute deadline."""

        if self.sock is not None:
            remaining = _remaining_deadline_seconds(self._absolute_deadline)
            try:
                self.sock.settimeout(remaining)
            except (OSError, ValueError) as error:
                raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error
        super().send(data)
        _require_deadline(self._absolute_deadline)


class _DeadlineHTTPConnectionPool(HTTPConnectionPool):
    """Create only absolute-deadline HTTP connections for one local request."""

    ConnectionCls = _DeadlineHTTPConnection


class _DeadlinePoolManager(PoolManager):
    """Inject one request deadline after ordinary urllib3 pool-key creation."""

    def __init__(self, *args: Any, absolute_deadline: float, **kwargs: Any) -> None:
        """Create an isolated manager without mutating urllib3 global maps."""

        self._absolute_deadline = absolute_deadline
        super().__init__(*args, **kwargs)
        self.pool_classes_by_scheme = self.pool_classes_by_scheme.copy()
        self.pool_classes_by_scheme["http"] = _DeadlineHTTPConnectionPool

    def _new_pool(
        self,
        scheme: str,
        host: str,
        port: int,
        request_context: dict[str, Any] | None = None,
    ) -> HTTPConnectionPool:
        """Pass the deadline to a new pool without adding it to the pool key."""

        context = (
            self.connection_pool_kw.copy()
            if request_context is None
            else request_context.copy()
        )
        context["absolute_deadline"] = self._absolute_deadline
        return super()._new_pool(scheme, host, port, context)


class _DeadlineHTTPAdapter(HTTPAdapter):
    """Mount one zero-retry PoolManager carrying an absolute deadline."""

    def __init__(self, absolute_deadline: float) -> None:
        """Capture the request deadline before HTTPAdapter builds its manager."""

        self._absolute_deadline = absolute_deadline
        super().__init__(max_retries=0)

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        """Build an isolated deadline-aware HTTP-only connection pool."""

        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        self.poolmanager = _DeadlinePoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            absolute_deadline=self._absolute_deadline,
            **pool_kwargs,
        )


def _is_strict_integer(value: object) -> bool:
    """Return whether a resource setting is an integer but not a Boolean."""

    return isinstance(value, int) and not isinstance(value, bool)


def _to_float_coordinate(value: int | float) -> float:
    """Convert one parsed JSON number through the bounded response seam."""

    return float(value)


def _snapshot_embedding_request(request: object) -> EmbeddingRequest:
    """Deep-copy one exact request graph through all public validators.

    Frozen dataclasses are not an authority boundary because callers can use
    ``object.__setattr__`` after construction.  Exact outer, container, item,
    and scalar types prevent subclasses or mutable aliases from changing the
    v1 input policy between validation and HTTP serialization.
    """

    if type(request) is not EmbeddingRequest:
        raise EmbeddingValidationError(
            "Embedding request must be validated before inference."
        )
    purpose = request.purpose
    source_items = request.items
    if type(purpose) is not str or type(source_items) is not tuple:
        raise EmbeddingValidationError(
            "Embedding request must be validated before inference."
        )
    if not source_items:
        raise EmbeddingValidationError(
            "Embedding request must contain validated inputs."
        )
    if len(source_items) > DEFAULT_EMBEDDING_MAX_BATCH_ITEMS:
        raise EmbeddingLimitError(
            "Embedding request exceeds the batch item limit."
        )
    copied_items: list[EmbeddingInput] = []
    for item in source_items:
        if type(item) is not EmbeddingInput:
            raise EmbeddingValidationError(
                "Embedding request must contain exact validated inputs."
            )
        item_id = item.item_id
        text = item.text
        if type(item_id) is not str or type(text) is not str:
            raise EmbeddingValidationError(
                "Embedding request input fields must be exact strings."
            )
        copied_items.append(EmbeddingInput(item_id=item_id, text=text))
    return EmbeddingRequest(purpose=purpose, items=tuple(copied_items))


def _normalize_loopback_base_url(value: object) -> str:
    """Return a canonical loopback HTTP origin or reject possible SSRF input."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Ollama embedding base_url must be non-empty.")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "http":
        raise ValueError("Ollama embedding base_url must use loopback HTTP.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Ollama embedding base_url must not contain credentials.")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError(
            "Ollama embedding base_url must be an origin without a path."
        )
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("Ollama embedding base_url must include a loopback host.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            "Ollama embedding base_url has an invalid port."
        ) from error
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("Ollama embedding base_url has an invalid port.")

    normalized_host = hostname.lower()
    if normalized_host == "localhost":
        # Resolving the sole accepted hostname before I/O prevents DNS settings
        # from moving a supposedly local request outside the loopback boundary.
        normalized_host = "127.0.0.1"
    else:
        try:
            address = ip_address(normalized_host)
        except ValueError as error:
            raise ValueError(
                "Ollama embedding base_url must use a loopback host."
            ) from error
        if not address.is_loopback:
            raise ValueError(
                "Ollama embedding base_url must use a loopback host."
            )

    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit(("http", netloc, "", "", ""))


def _total_http_timeout(total_seconds: float) -> Urllib3Timeout:
    """Share one bounded budget across connection and response headers.

    Requests otherwise treats a timeout as a renewable inactivity ceiling.
    Body reads additionally pin the active urllib3 socket to the remaining
    monotonic deadline below, so a loopback peer cannot evade the total budget
    by continuously dripping small JSON fragments.
    """

    return Urllib3Timeout(
        total=total_seconds,
        connect=total_seconds,
        read=total_seconds,
    )


def _require_deadline(deadline: float) -> None:
    """Stop a streamed response after its single monotonic time budget."""

    if monotonic() >= deadline:
        raise _WallClockDeadlineExceeded


def _remaining_deadline_seconds(deadline: float) -> float:
    """Return positive time remaining under one absolute monotonic deadline."""

    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _WallClockDeadlineExceeded
    return remaining


def _set_remaining_socket_timeout(
    response: requests.Response,
    deadline: float,
) -> None:
    """Pin the next raw read to the remaining wall-clock budget.

    Requests has no public total-body deadline.  The project pins Requests and
    urllib3, so this narrow adapter may inspect their active response socket.
    If that expected shape changes, failing closed is safer than silently
    falling back to a renewable per-read timeout.
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
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    try:
        active_socket.settimeout(remaining)
    except (OSError, ValueError) as error:
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error


def _declared_response_length(
    value: str | None,
    *,
    maximum: int,
) -> int | None:
    """Validate an optional canonical Content-Length before reading a body."""

    if value is None:
        return None
    if (
        not value.isascii()
        or not value.isdigit()
        or len(value) > len(str(maximum))
    ):
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    try:
        declared = int(value)
    except ValueError as error:
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error
    if not 1 <= declared <= maximum:
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    return declared


def _install_bounded_chunk_framing_reader(
    response: requests.Response,
    deadline: float,
) -> None:
    """Bound chunk-size and trailer lines before urllib3 removes framing."""

    raw = cast(Any, response.raw)
    http_response = getattr(raw, "_fp", None)
    reader = getattr(http_response, "fp", None)
    if (
        http_response is None
        or getattr(http_response, "chunked", None) is not True
        or reader is None
        or not callable(getattr(reader, "readline", None))
    ):
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    http_response.fp = _BoundedHTTPFramingReader(reader, deadline)


def _read_bounded_json_body(
    response: requests.Response,
    *,
    maximum: int,
    deadline: float,
) -> bytes:
    """Read one identity JSON body under byte and total-time ceilings."""

    transfer_encoding = response.headers.get("Transfer-Encoding")
    if transfer_encoding is not None:
        # Gin uses ordinary HTTP/1.1 chunking for large Ollama JSON bodies.
        # Accept that exact interoperable form, reject ambiguous coding chains
        # or Content-Length mixtures, and cap framing before urllib3 strips it.
        if (
            transfer_encoding != "chunked"
            or response.headers.get("Content-Length") is not None
        ):
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
        _install_bounded_chunk_framing_reader(response, deadline)
    content_type = response.headers.get("Content-Type")
    if not isinstance(content_type, str) or any(
        character in content_type for character in "\r\n,"
    ):
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    media_parts = tuple(part.strip().lower() for part in content_type.split(";"))
    if not (
        media_parts == ("application/json",)
        or media_parts == ("application/json", "charset=utf-8")
    ):
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if content_encoding not in ("", "identity"):
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    declared = _declared_response_length(
        response.headers.get("Content-Length"),
        maximum=maximum,
    )
    chunks: list[bytes] = []
    observed = 0
    raw = cast(Any, response.raw)
    read_one = getattr(raw, "read1", None)
    if not callable(read_one):
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    try:
        while True:
            _require_deadline(deadline)
            if bool(getattr(raw, "closed", False)):
                break
            _set_remaining_socket_timeout(response, deadline)
            try:
                chunk = read_one(64 * 1024, decode_content=False)
            except _HTTPFramingLimitExceeded as error:
                raise EmbeddingFailedError(
                    _INVALID_RESPONSE_MESSAGE
                ) from error
            except _WallClockDeadlineExceeded:
                raise
            except (TimeoutError, Urllib3ReadTimeoutError) as error:
                raise _WallClockDeadlineExceeded from error
            except Exception as error:
                if monotonic() >= deadline:
                    raise _WallClockDeadlineExceeded from error
                raise EmbeddingFailedError(_REQUEST_FAILED_MESSAGE) from error
            if not isinstance(chunk, bytes):
                raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum:
                raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
            chunks.append(chunk)
    except EmbeddingFailedError:
        raise
    _require_deadline(deadline)
    if observed == 0 or declared is not None and observed != declared:
        raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
    return b"".join(chunks)


def _reject_duplicate_json_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build one JSON object while rejecting ambiguous duplicate members."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object member.")
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> object:
    """Reject JSON extensions such as NaN and Infinity at parse time."""

    raise ValueError(f"Non-finite JSON number: {value}")


@dataclass(frozen=True, slots=True)
class OllamaEmbeddingConfig:
    """Configure bounded access to the reviewed local Ollama manifest."""

    base_url: str = DEFAULT_OLLAMA_EMBEDDING_BASE_URL
    request_timeout_seconds: float = (
        DEFAULT_OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS
    )
    probe_timeout_seconds: float = DEFAULT_OLLAMA_EMBEDDING_PROBE_TIMEOUT_SECONDS
    max_request_body_bytes: int = DEFAULT_OLLAMA_EMBEDDING_MAX_REQUEST_BYTES
    max_response_body_bytes: int = DEFAULT_OLLAMA_EMBEDDING_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        """Reject remote origins and ambiguous or excessive resource values."""

        object.__setattr__(
            self,
            "base_url",
            _normalize_loopback_base_url(self.base_url),
        )
        if (
            not isinstance(self.request_timeout_seconds, float)
            or not math.isfinite(self.request_timeout_seconds)
            or not 0.1 <= self.request_timeout_seconds <= 300.0
        ):
            raise ValueError(
                "request_timeout_seconds must be a finite float from 0.1 to 300.0."
            )
        if (
            not isinstance(self.probe_timeout_seconds, float)
            or not math.isfinite(self.probe_timeout_seconds)
            or not 0.1 <= self.probe_timeout_seconds <= 10.0
        ):
            raise ValueError(
                "probe_timeout_seconds must be a finite float from 0.1 to 10.0."
            )
        if (
            not _is_strict_integer(self.max_request_body_bytes)
            or not 1_024
            <= self.max_request_body_bytes
            <= DEFAULT_OLLAMA_EMBEDDING_MAX_REQUEST_BYTES
        ):
            raise ValueError(
                "max_request_body_bytes must be from 1024 through 262144."
            )
        if (
            not _is_strict_integer(self.max_response_body_bytes)
            or not 64 * 1024
            <= self.max_response_body_bytes
            <= MAX_OLLAMA_EMBEDDING_RESPONSE_BYTES
        ):
            raise ValueError(
                "max_response_body_bytes must be from 65536 through 8388608."
            )


class OllamaEmbeddingAdapter:
    """Embed exact text with one pinned Ollama model over loopback HTTP.

    The adapter verifies the full manifest digest both before and after every
    batch.  This closes the ordinary mutable-tag race; it cannot provide a
    transactional lease over the external Ollama process, so any observed
    identity change fails the whole batch instead of publishing partial data.
    """

    def __init__(self, config: OllamaEmbeddingConfig | None = None) -> None:
        """Create a light adapter without probing, pulling, or loading a model."""

        if config is not None and type(config) is not OllamaEmbeddingConfig:
            raise TypeError("config must be OllamaEmbeddingConfig.")
        source = OllamaEmbeddingConfig() if config is None else config
        # Frozen dataclasses prevent ordinary assignment but are still
        # bypassable with ``object.__setattr__``.  Rebuilding through the
        # public constructor both revalidates a possibly tampered value and
        # prevents later mutation of the caller-owned object from changing the
        # loopback destination or resource ceilings held by this adapter.
        self._config = OllamaEmbeddingConfig(
            base_url=source.base_url,
            request_timeout_seconds=source.request_timeout_seconds,
            probe_timeout_seconds=source.probe_timeout_seconds,
            max_request_body_bytes=source.max_request_body_bytes,
            max_response_body_bytes=source.max_response_body_bytes,
        )
        self._identity = EmbeddingModelIdentity(
            provider=DEFAULT_EMBEDDING_PROVIDER,
            adapter_id=DEFAULT_EMBEDDING_ADAPTER_ID,
            adapter_version=DEFAULT_EMBEDDING_ADAPTER_VERSION,
            model_tag=DEFAULT_EMBEDDING_MODEL_TAG,
            model_digest=DEFAULT_EMBEDDING_MODEL_DIGEST,
            dimension=DEFAULT_EMBEDDING_DIMENSION,
            normalization=DEFAULT_EMBEDDING_NORMALIZATION,
            document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
            query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
        )
        self._policy = EmbeddingBatchPolicy()

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the pinned model, adapter, dimension, and template identity."""

        return EmbeddingModelIdentity(
            provider=self._identity.provider,
            adapter_id=self._identity.adapter_id,
            adapter_version=self._identity.adapter_version,
            model_tag=self._identity.model_tag,
            model_digest=self._identity.model_digest,
            dimension=self._identity.dimension,
            normalization=self._identity.normalization,
            document_template_version=(
                self._identity.document_template_version
            ),
            query_template_version=self._identity.query_template_version,
            embedding_space_id=self._identity.embedding_space_id,
        )

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the fixed non-truncating batch policy."""

        return EmbeddingBatchPolicy(
            max_batch_items=self._policy.max_batch_items,
            max_input_code_points=self._policy.max_input_code_points,
            max_batch_code_points=self._policy.max_batch_code_points,
            truncate=self._policy.truncate,
        )

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return one verified ordered batch or a sanitized typed failure."""

        snapshot = _snapshot_embedding_request(request)
        self._require_expected_manifest()
        payload: dict[str, object] = {
            "model": self._identity.model_tag,
            "input": [item.text for item in snapshot.items],
            "truncate": False,
            "dimensions": self._identity.dimension,
        }
        response = self._request_json(
            "POST",
            "/api/embed",
            payload=payload,
            timeout_seconds=self._config.request_timeout_seconds,
            unavailable=False,
        )
        vectors = self._decode_vectors(response, snapshot.items)
        try:
            self._require_expected_manifest()
        except EmbeddingUnavailableError as error:
            raise EmbeddingUnavailableError(_MODEL_CHANGED_MESSAGE) from error
        return EmbeddingBatch(
            schema_version=EMBEDDING_SCHEMA_VERSION,
            identity=self.identity,
            policy=self.policy,
            purpose=snapshot.purpose,
            vectors=vectors,
        )

    def _require_expected_manifest(self) -> None:
        """Require one exact installed tag and full reviewed manifest digest."""

        response = self._request_json(
            "GET",
            "/api/tags",
            payload=None,
            timeout_seconds=self._config.probe_timeout_seconds,
            unavailable=True,
        )
        records = response.get("models")
        if (
            not isinstance(records, list)
            or len(records) > MAX_OLLAMA_MODEL_RECORDS
        ):
            raise EmbeddingUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)

        matching_digests: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if self._identity.model_tag not in (
                record.get("name"),
                record.get("model"),
            ):
                continue
            digest = record.get("digest")
            if not isinstance(digest, str):
                raise EmbeddingUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)
            matching_digests.append(digest)
        if (
            not matching_digests
            or any(
                digest != self._identity.model_digest
                for digest in matching_digests
            )
        ):
            raise EmbeddingUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)

    def _decode_vectors(
        self,
        response: dict[str, object],
        inputs: tuple[EmbeddingInput, ...],
    ) -> tuple[EmbeddingVector, ...]:
        """Validate model identity, count, dimensions, numbers, and unit norm."""

        if response.get("model") != self._identity.model_tag:
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
        raw_vectors = response.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(inputs):
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)

        vectors: list[EmbeddingVector] = []
        for item, raw_vector in zip(inputs, raw_vectors, strict=True):
            if (
                not isinstance(raw_vector, list)
                or len(raw_vector) != self._identity.dimension
            ):
                raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
            values: list[float] = []
            for raw_value in raw_vector:
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                ):
                    raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
                try:
                    value = _to_float_coordinate(raw_value)
                except (OverflowError, MemoryError) as error:
                    raise EmbeddingFailedError(
                        _INVALID_RESPONSE_MESSAGE
                    ) from error
                if not math.isfinite(value):
                    raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
                values.append(value)
            try:
                validated = _unit_vector(tuple(values), self._identity.dimension)
            except EmbeddingValidationError as error:
                raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error
            vectors.append(
                EmbeddingVector(item_id=item.item_id, values=validated)
            )
        return tuple(vectors)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None,
        timeout_seconds: float,
        unavailable: bool,
    ) -> dict[str, object]:
        """Send one non-retrying bounded request and return a JSON object."""

        body: bytes | None = None
        if payload is not None:
            try:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise EmbeddingValidationError(
                    "Embedding request could not be encoded safely."
                ) from error
            if len(body) > self._config.max_request_body_bytes:
                raise EmbeddingLimitError(
                    "Embedding request exceeds the encoded byte limit."
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
                # Private text must not inherit proxy variables, and an explicit
                # zero-retry transport prevents a hidden duplicate inference.
                session.trust_env = False
                adapter = _DeadlineHTTPAdapter(deadline)
                session.mount("http://", adapter)
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
                            raise EmbeddingUnavailableError(
                                _SERVICE_UNAVAILABLE_MESSAGE
                            )
                        raise EmbeddingFailedError(_REQUEST_FAILED_MESSAGE)
                finally:
                    response.close()
        except EmbeddingLimitError:
            raise
        except EmbeddingUnavailableError:
            raise
        except EmbeddingFailedError as error:
            if unavailable:
                raise EmbeddingUnavailableError(
                    _SERVICE_UNAVAILABLE_MESSAGE
                ) from error
            raise
        except (
            RequestsConnectionError,
            Timeout,
            _WallClockDeadlineExceeded,
        ) as error:
            raise EmbeddingUnavailableError(
                _SERVICE_UNAVAILABLE_MESSAGE
            ) from error
        except RequestException as error:
            if unavailable:
                raise EmbeddingUnavailableError(
                    _SERVICE_UNAVAILABLE_MESSAGE
                ) from error
            raise EmbeddingFailedError(_REQUEST_FAILED_MESSAGE) from error

        try:
            decoded: object = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_pairs,
                parse_constant=_reject_non_finite_json_number,
            )
        except (
            UnicodeError,
            ValueError,
            RecursionError,
            MemoryError,
        ) as error:
            if unavailable:
                raise EmbeddingUnavailableError(
                    _MODEL_UNAVAILABLE_MESSAGE
                ) from error
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE) from error
        if not isinstance(decoded, dict):
            if unavailable:
                raise EmbeddingUnavailableError(_MODEL_UNAVAILABLE_MESSAGE)
            raise EmbeddingFailedError(_INVALID_RESPONSE_MESSAGE)
        return cast(dict[str, object], decoded)
