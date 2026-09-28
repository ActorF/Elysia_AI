"""Test the pinned loopback Ollama embedding adapter entirely offline."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import documents.ollama_embedding as ollama_embedding
from documents.embedding import (
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL_DIGEST,
    DEFAULT_EMBEDDING_MODEL_TAG,
    EMBEDDING_SCHEMA_VERSION,
    EmbeddingFailedError,
    EmbeddingInput,
    EmbeddingLimitError,
    EmbeddingRequest,
    EmbeddingUnavailableError,
    EmbeddingValidationError,
)
from documents.ollama_embedding import (
    MAX_OLLAMA_HTTP_FRAMING_BYTES,
    MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES,
    MAX_OLLAMA_HTTP_FRAMING_LINES,
    OllamaEmbeddingAdapter,
    OllamaEmbeddingConfig,
)


class _FakeSocket:
    """Record per-read socket ceilings without opening a network connection."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        """Record the remaining wall-clock budget pinned by the adapter."""

        self.timeouts.append(timeout)


class _FakeConnection:
    """Expose the pinned urllib3 connection shape used by the adapter."""

    def __init__(self, socket: _FakeSocket | None) -> None:
        self.sock = socket


class _FakeFramingReader:
    """Expose chunk-size or trailer lines before fake body decoding."""

    def __init__(self, lines: tuple[bytes, ...]) -> None:
        self.lines = list(lines)

    def readline(self, limit: int = -1) -> bytes:
        """Return at most the requested bytes from the next framing line."""

        if not self.lines:
            return b""
        line = self.lines.pop(0)
        if limit >= 0 and len(line) > limit:
            self.lines.insert(0, line[limit:])
            return line[:limit]
        return line


class _FakeDeadlineStream:
    """Return scripted arrivals through the ``BufferedReader.read1`` seam."""

    def __init__(self, arrivals: tuple[bytes, ...]) -> None:
        self.arrivals = list(arrivals)
        self.closed = False

    def read1(self, _size: int) -> bytes:
        """Return one arrival so every drip requires another deadline check."""

        if not self.arrivals:
            return b""
        return self.arrivals.pop(0)

    def close(self) -> None:
        """Record the HTTP response releasing its fake stream."""

        self.closed = True

    def flush(self) -> None:
        """Match the no-op flush supported by a buffered socket reader."""


class _FakeHeaderSocket(_FakeSocket):
    """Provide one scripted socket file for status/header parsing."""

    def __init__(self, stream: _FakeDeadlineStream) -> None:
        super().__init__()
        self.stream = stream

    def makefile(self, mode: str) -> _FakeDeadlineStream:
        """Return the stream expected by ``http.client.HTTPResponse``."""

        assert mode == "rb"
        return self.stream


class _FakeHTTPResponse:
    """Expose the ``http.client`` fields wrapped for chunked responses."""

    def __init__(self, framing_lines: tuple[bytes, ...]) -> None:
        self.chunked = True
        self.fp: object = _FakeFramingReader(framing_lines)


class _FakeRaw:
    """Provide bounded ``read1`` behavior compatible with urllib3 responses."""

    def __init__(
        self,
        chunks: tuple[bytes, ...],
        *,
        socket_available: bool,
        read_error: BaseException | None,
        framing_lines: tuple[bytes, ...] | None,
    ) -> None:
        self._chunks = list(chunks)
        self.socket = _FakeSocket() if socket_available else None
        self._connection = _FakeConnection(self.socket)
        self.read_error = read_error
        self.closed = False
        self._fp = (
            None
            if framing_lines is None
            else _FakeHTTPResponse(framing_lines)
        )
        self._framing_checked = False

    def read1(self, size: int, *, decode_content: bool) -> bytes:
        """Return one configured arrival or raise its transport failure."""

        assert size == 64 * 1024
        assert decode_content is False
        if self.read_error is not None:
            error = self.read_error
            self.read_error = None
            raise error
        if self._fp is not None and not self._framing_checked:
            while cast(_FakeHTTPResponse, self._fp).fp.readline(65_537):
                pass
            self._framing_checked = True
        if not self._chunks:
            self.closed = True
            return b""
        chunk = self._chunks.pop(0)
        if not self._chunks:
            self.closed = True
        return chunk


class _FakeResponse:
    """Expose the pinned Requests/urllib3 surface consumed by the adapter."""

    def __init__(
        self,
        body: bytes,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: tuple[bytes, ...] | None = None,
        socket_available: bool = True,
        read_error: BaseException | None = None,
        include_content_length: bool = True,
        framing_lines: tuple[bytes, ...] | None = None,
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        if include_content_length:
            self.headers["Content-Length"] = str(len(body))
        if headers is not None:
            self.headers.update(headers)
        selected_chunks = (body,) if chunks is None else chunks
        self.raw = _FakeRaw(
            selected_chunks,
            socket_available=socket_available,
            read_error=read_error,
            framing_lines=framing_lines,
        )
        self.closed = False

    def close(self) -> None:
        """Record that the adapter released this response."""

        self.closed = True


class _FakeSession:
    """Record one direct Session request and consume a shared response queue."""

    def __init__(self, factory: _SessionFactory) -> None:
        self.factory = factory
        self.trust_env = True
        self.mounts: list[tuple[str, object]] = []

    def __enter__(self) -> _FakeSession:
        """Return the recording session context."""

        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        """Leave the context without suppressing failures."""

        return None

    def mount(self, prefix: str, adapter: object) -> None:
        """Record explicit zero-retry transport mounting."""

        self.mounts.append((prefix, adapter))

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        """Record request metadata and return or raise the next queued value."""

        self.factory.calls.append(
            {
                "session": self,
                "method": method,
                "url": url,
                **kwargs,
            }
        )
        if not self.factory.responses:
            raise AssertionError("Unexpected HTTP request.")
        next_value = self.factory.responses.pop(0)
        if isinstance(next_value, BaseException):
            raise next_value
        return next_value


class _SessionFactory:
    """Create recording sessions around a shared ordered response script."""

    def __init__(
        self,
        responses: list[_FakeResponse | BaseException],
    ) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        """Create one session for the adapter's single HTTP operation."""

        session = _FakeSession(self)
        self.sessions.append(session)
        return session


def _json_response(
    value: object,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> _FakeResponse:
    """Encode one fake JSON response with an exact byte length."""

    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return _FakeResponse(
        body,
        status_code=status_code,
        headers=headers,
    )


def _response_without_content_type() -> _FakeResponse:
    """Build a response that omits the mandatory unambiguous media type."""

    response = _FakeResponse(b"{}")
    response.headers.pop("Content-Type")
    return response


def _tags_response(
    *,
    digest: str = DEFAULT_EMBEDDING_MODEL_DIGEST,
    include_model: bool = True,
) -> _FakeResponse:
    """Build one bounded Ollama tag listing for the pinned manifest."""

    models: list[dict[str, object]] = []
    if include_model:
        models.append(
            {
                "name": DEFAULT_EMBEDDING_MODEL_TAG,
                "model": DEFAULT_EMBEDDING_MODEL_TAG,
                "digest": digest,
            }
        )
    return _json_response({"models": models})


def _unit_vector(index: int = 0) -> list[float]:
    """Return one 1024-dimensional one-hot JSON vector."""

    return [1.0 if position == index else 0.0 for position in range(1_024)]


def _embed_response(
    vectors: list[object] | None = None,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL_TAG,
) -> _FakeResponse:
    """Build one ordinary Ollama embed response."""

    return _json_response(
        {
            "model": model,
            "embeddings": [_unit_vector()] if vectors is None else vectors,
            "total_duration": 1,
            "load_duration": 1,
            "prompt_eval_count": 1,
        }
    )


def _chunked_response(
    response: _FakeResponse,
    framing_lines: tuple[bytes, ...],
) -> _FakeResponse:
    """Expose one decoded body behind fake HTTP/1.1 chunk framing."""

    response.headers.pop("Content-Length", None)
    response.headers["Transfer-Encoding"] = "chunked"
    response.raw._fp = _FakeHTTPResponse(framing_lines)
    return response


def _request(text: str = "exact text") -> EmbeddingRequest:
    """Build one valid document embedding request."""

    return EmbeddingRequest(
        purpose="document",
        items=(EmbeddingInput(item_id="item", text=text),),
    )


def _adapter_with(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_FakeResponse | BaseException],
    *,
    config: OllamaEmbeddingConfig | None = None,
) -> tuple[OllamaEmbeddingAdapter, _SessionFactory]:
    """Inject a fully offline Session factory into one adapter."""

    factory = _SessionFactory(responses)
    monkeypatch.setattr(
        "documents.ollama_embedding.requests.Session",
        factory,
    )
    return OllamaEmbeddingAdapter(config), factory


def test_adapter_posts_exact_non_truncating_batch_with_no_proxy_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin payload semantics and the private loopback transport controls."""

    adapter, factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response(), _tags_response()],
    )
    request = EmbeddingRequest(
        purpose="document",
        items=(
            EmbeddingInput(item_id="first", text=" exact e\u0301 "),
            EmbeddingInput(item_id="second", text="第二段"),
        ),
    )
    factory.responses[1] = _embed_response([_unit_vector(0), _unit_vector(1)])

    result = adapter.embed(request)

    assert result.schema_version == EMBEDDING_SCHEMA_VERSION
    assert result.identity.model_tag == DEFAULT_EMBEDDING_MODEL_TAG
    assert result.identity.model_digest == DEFAULT_EMBEDDING_MODEL_DIGEST
    assert result.identity.dimension == DEFAULT_EMBEDDING_DIMENSION
    assert [vector.item_id for vector in result.vectors] == ["first", "second"]
    assert [call["url"] for call in factory.calls] == [
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434/api/embed",
        "http://127.0.0.1:11434/api/tags",
    ]
    assert all(session.trust_env is False for session in factory.sessions)
    assert all(session.mounts[0][0] == "http://" for session in factory.sessions)
    assert all(call["allow_redirects"] is False for call in factory.calls)
    assert all(call["stream"] is True for call in factory.calls)
    post = json.loads(cast(bytes, factory.calls[1]["data"]).decode("utf-8"))
    assert post == {
        "model": DEFAULT_EMBEDDING_MODEL_TAG,
        "input": [" exact e\u0301 ", "第二段"],
        "truncate": False,
        "dimensions": 1_024,
    }
    assert all("/api/pull" not in cast(str, call["url"]) for call in factory.calls)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:11434",
        "http://192.168.1.2:11434",
        "http://example.com:11434",
        "http://user:pass@localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?private=yes",
        "http://localhost:11434/#fragment",
    ],
)
def test_config_rejects_every_non_loopback_origin(base_url: str) -> None:
    """Keep private document text inside an unambiguous loopback boundary."""

    with pytest.raises(ValueError, match="base_url"):
        OllamaEmbeddingConfig(base_url=base_url)


def test_config_canonicalizes_localhost_and_accepts_explicit_ipv6() -> None:
    """Resolve localhost eagerly while preserving explicit IPv6 loopback."""

    assert OllamaEmbeddingConfig(
        base_url="http://localhost:1234/"
    ).base_url == "http://127.0.0.1:1234"
    assert OllamaEmbeddingConfig(
        base_url="http://[::1]:1234"
    ).base_url == "http://[::1]:1234"


def test_adapter_revalidates_and_detaches_its_config_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep later frozen-object bypasses from changing the loopback target."""

    tampered = OllamaEmbeddingConfig()
    object.__setattr__(tampered, "base_url", "http://example.com:11434")
    with pytest.raises(ValueError, match="base_url"):
        OllamaEmbeddingAdapter(tampered)

    config = OllamaEmbeddingConfig(base_url="http://localhost:1234")
    adapter, factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response(), _tags_response()],
        config=config,
    )
    object.__setattr__(config, "base_url", "http://example.com:11434")

    adapter.embed(_request())

    assert all(
        cast(str, call["url"]).startswith("http://127.0.0.1:1234/")
        for call in factory.calls
    )


def test_adapter_rejects_config_subclasses() -> None:
    """Keep behavior-bearing subclasses outside the validated config seam."""

    class _ConfigSubclass(OllamaEmbeddingConfig):
        """Model an otherwise valid subclass with caller-controlled behavior."""

    with pytest.raises(TypeError, match="config"):
        OllamaEmbeddingAdapter(_ConfigSubclass())


def test_adapter_returns_detached_identity_and_policy_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent frozen-object bypasses from rewriting the adapter contract."""

    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response(), _tags_response()],
    )
    exposed_identity = adapter.identity
    exposed_policy = adapter.policy
    object.__setattr__(exposed_identity, "model_tag", "other:1b")
    object.__setattr__(exposed_policy, "truncate", True)

    result = adapter.embed(_request())
    object.__setattr__(result.identity, "model_digest", "0" * 64)
    object.__setattr__(result.policy, "max_batch_items", 1)

    assert adapter.identity.model_tag == DEFAULT_EMBEDDING_MODEL_TAG
    assert adapter.identity.model_digest == DEFAULT_EMBEDDING_MODEL_DIGEST
    assert adapter.policy.truncate is False
    assert adapter.policy.max_batch_items == 16
    assert result.identity is not adapter.identity
    assert result.policy is not adapter.policy


@pytest.mark.parametrize(
    "tags",
    [
        _tags_response(include_model=False),
        _tags_response(digest="0" * 64),
        _json_response({"models": "invalid"}),
        _json_response({"models": [{"name": DEFAULT_EMBEDDING_MODEL_TAG}]}),
    ],
)
def test_adapter_rejects_absent_or_unreviewed_model_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tags: _FakeResponse,
) -> None:
    """Require exact tag and full digest before any private input is posted."""

    adapter, factory = _adapter_with(monkeypatch, [tags])
    with pytest.raises(EmbeddingUnavailableError, match="model is unavailable"):
        adapter.embed(_request("PRIVATE_INPUT"))

    assert len(factory.calls) == 1
    assert cast(str, factory.calls[0]["url"]).endswith("/api/tags")
    assert factory.calls[0]["data"] is None


def test_adapter_rejects_manifest_drift_after_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard the whole batch when a mutable tag changes during one call."""

    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response(), _tags_response(digest="0" * 64)],
    )
    with pytest.raises(EmbeddingUnavailableError, match="changed during"):
        adapter.embed(_request())


@pytest.mark.parametrize(
    "bad_response",
    [
        _json_response([]),
        _json_response({"model": DEFAULT_EMBEDDING_MODEL_TAG}),
        _embed_response([]),
        _embed_response([_unit_vector()[:-1]]),
        _embed_response([[float("nan")] + [0.0] * 1_023]),
        _embed_response([[0.0] * 1_024]),
        _embed_response([[2.0] + [0.0] * 1_023]),
        _embed_response([_unit_vector()], model="other:1b"),
    ],
)
def test_adapter_rejects_invalid_body_count_dimension_and_vector_values(
    monkeypatch: pytest.MonkeyPatch,
    bad_response: _FakeResponse,
) -> None:
    """Fail closed on malformed JSON shapes, NaN, zero, and non-unit vectors."""

    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), bad_response],
    )
    with pytest.raises(EmbeddingFailedError, match="invalid response"):
        adapter.embed(_request())


def test_adapter_bounds_encoded_request_before_posting_private_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply a byte ceiling after JSON escaping and before model inference."""

    config = OllamaEmbeddingConfig(max_request_body_bytes=1_024)
    adapter, factory = _adapter_with(monkeypatch, [_tags_response()], config=config)

    with pytest.raises(EmbeddingLimitError, match="encoded byte"):
        adapter.embed(_request("😀" * 2_000))
    assert len(factory.calls) == 1


@pytest.mark.parametrize(
    "bad_response",
    [
        _FakeResponse(
            b"{}",
            headers={"Content-Length": str(64 * 1024 + 1)},
        ),
        _FakeResponse(
            b"{}",
            headers={"Content-Encoding": "gzip", "Content-Length": "2"},
        ),
        _FakeResponse(
            b"{}",
            headers={"Transfer-Encoding": "chunked"},
        ),
        _FakeResponse(
            b"{}",
            headers={"Content-Length": "3"},
        ),
        _FakeResponse(
            b"{}",
            headers={"Content-Type": "text/html", "Content-Length": "2"},
        ),
        _FakeResponse(
            b"{}",
            headers={
                "Content-Type": "application/json, application/json",
                "Content-Length": "2",
            },
        ),
        _response_without_content_type(),
    ],
)
def test_adapter_bounds_and_validates_response_transport(
    monkeypatch: pytest.MonkeyPatch,
    bad_response: _FakeResponse,
) -> None:
    """Reject oversized, encoded, mistyped, or inconsistent response bodies."""

    config = OllamaEmbeddingConfig(max_response_body_bytes=64 * 1024)
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), bad_response],
        config=config,
    )
    with pytest.raises(EmbeddingFailedError, match="invalid response"):
        adapter.embed(_request())


def test_adapter_accepts_bounded_chunked_ollama_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve Gin's normal chunked response path behind framing ceilings."""

    chunked = _chunked_response(
        _embed_response(),
        (b"4000\r\n", b"\r\n"),
    )
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), chunked, _tags_response()],
    )

    result = adapter.embed(_request())

    assert len(result.vectors) == 1
    assert len(result.vectors[0].values) == DEFAULT_EMBEDDING_DIMENSION


@pytest.mark.parametrize(
    "framing_lines",
    [
        (
            b"1;" + b"x" * MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES + b"\r\n",
        ),
        (
            b"4000\r\n",
            b"X-Long-Trailer: "
            + b"x" * MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES
            + b"\r\n",
        ),
    ],
)
def test_adapter_rejects_oversized_chunk_or_trailer_lines(
    monkeypatch: pytest.MonkeyPatch,
    framing_lines: tuple[bytes, ...],
) -> None:
    """Fail with one fixed error before unbounded framing reaches urllib3."""

    chunked = _chunked_response(_embed_response(), framing_lines)
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), chunked],
    )

    with pytest.raises(EmbeddingFailedError) as captured:
        adapter.embed(_request())

    assert str(captured.value) == (
        "Local embedding service returned an invalid response."
    )


@pytest.mark.parametrize(
    "framing_lines",
    [
        (b"X:\r\n",) * (MAX_OLLAMA_HTTP_FRAMING_LINES + 1),
        (
            b"X:" + b"x" * (MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES - 4) + b"\r\n",
        )
        * (
            MAX_OLLAMA_HTTP_FRAMING_BYTES
            // MAX_OLLAMA_HTTP_FRAMING_LINE_BYTES
            + 2
        ),
    ],
)
def test_adapter_rejects_aggregate_chunk_framing_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
    framing_lines: tuple[bytes, ...],
) -> None:
    """Bound many short trailers as well as individually oversized lines."""

    chunked = _chunked_response(_embed_response(), framing_lines)
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), chunked],
    )

    with pytest.raises(EmbeddingFailedError, match="invalid response"):
        adapter.embed(_request())


def test_chunk_framing_reader_enforces_its_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop trailer parsing that crosses the body deadline inside one read1."""

    moments = iter((0.0, 0.2))
    monkeypatch.setattr(
        "documents.ollama_embedding.monotonic",
        lambda: next(moments),
    )
    reader = ollama_embedding._BoundedHTTPFramingReader(
        _FakeFramingReader((b"X-Trailer: value\r\n",)),
        0.1,
    )

    with pytest.raises(ollama_embedding._WallClockDeadlineExceeded):
        reader.readline()


def test_http_response_header_reader_stops_slow_drip_at_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent renewable socket inactivity timeouts during header parsing."""

    moment = -0.02

    def advancing_monotonic() -> float:
        """Advance time while every fake socket read continues to make progress."""

        nonlocal moment
        moment += 0.02
        return moment

    monkeypatch.setattr(
        "documents.ollama_embedding.monotonic",
        advancing_monotonic,
    )
    stream = _FakeDeadlineStream(
        (
            b"HTTP/1.1 200 OK\r\n",
            b"X-Slow: ",
            b"a",
            b"a",
            b"a",
            b"\r\n",
            b"Content-Length: 2\r\n",
            b"\r\n",
        )
    )
    socket = _FakeHeaderSocket(stream)
    response = ollama_embedding._DeadlineHTTPClientResponse(
        socket,
        absolute_deadline=0.15,
    )

    try:
        with pytest.raises(ollama_embedding._WallClockDeadlineExceeded):
            response.begin()
    finally:
        response.close()
    assert socket.timeouts


def test_adapter_pins_each_raw_read_to_remaining_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a slow body even when every individual read could make progress."""

    response = _tags_response()
    moments = iter((0.0, 0.01, 0.02, 0.2))
    monkeypatch.setattr(
        "documents.ollama_embedding.monotonic",
        lambda: next(moments),
    )
    adapter, _factory = _adapter_with(
        monkeypatch,
        [response],
        config=OllamaEmbeddingConfig(probe_timeout_seconds=0.1),
    )

    with pytest.raises(EmbeddingUnavailableError, match="not running"):
        adapter.embed(_request())
    assert response.raw.socket is not None
    assert response.raw.socket.timeouts == [pytest.approx(0.08)]


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(b"{}", socket_available=False),
        _FakeResponse(b"{}", read_error=TimeoutError("private timeout")),
    ],
)
def test_adapter_fails_closed_on_unsupported_or_timed_out_raw_transport(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse,
) -> None:
    """Never fall back to a renewable timeout when raw pinning is unavailable."""

    adapter, _factory = _adapter_with(monkeypatch, [response])
    with pytest.raises(EmbeddingUnavailableError, match="not running"):
        adapter.embed(_request("PRIVATE_INPUT"))


def test_adapter_rejects_duplicate_json_members_and_invalid_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat ambiguous objects and undecodable bytes as sanitized failures."""

    for body in (
        b'{"model":"qwen3-embedding:0.6b","model":"other"}',
        b"\xff\xfe",
    ):
        adapter, _factory = _adapter_with(
            monkeypatch,
            [_tags_response(), _FakeResponse(body)],
        )
        with pytest.raises(EmbeddingFailedError, match="invalid response"):
            adapter.embed(_request())


def test_adapter_translates_oversized_integer_coordinate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map integer-to-float overflow to the fixed invalid-response failure."""

    vector = [10**400] + [0] * 1_023
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response([vector])],
    )

    with pytest.raises(EmbeddingFailedError) as captured:
        adapter.embed(_request())

    assert str(captured.value) == (
        "Local embedding service returned an invalid response."
    )
    assert isinstance(captured.value.__cause__, OverflowError)


def test_adapter_translates_coordinate_conversion_memory_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map conversion allocation failure to the same stable response error."""

    def fail_conversion(_value: object) -> float:
        """Model a failed numeric conversion without allocating large memory."""

        raise MemoryError

    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response([[1] + [0] * 1_023])],
    )
    monkeypatch.setattr(
        "documents.ollama_embedding._to_float_coordinate",
        fail_conversion,
    )

    with pytest.raises(EmbeddingFailedError) as captured:
        adapter.embed(_request())

    assert str(captured.value) == (
        "Local embedding service returned an invalid response."
    )
    assert isinstance(captured.value.__cause__, MemoryError)


@pytest.mark.parametrize("parse_error", [RecursionError(), MemoryError()])
def test_adapter_translates_json_parser_resource_failures(
    monkeypatch: pytest.MonkeyPatch,
    parse_error: BaseException,
) -> None:
    """Convert parser exhaustion into the stable typed response failure."""

    real_loads = json.loads
    call_count = 0

    def fail_second_parse(
        value: str | bytes | bytearray,
        **kwargs: Any,
    ) -> object:
        """Let manifest parsing succeed, then fail the inference response."""

        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise parse_error
        return real_loads(value, **kwargs)

    monkeypatch.setattr(
        "documents.ollama_embedding.json.loads",
        fail_second_parse,
    )
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), _embed_response()],
    )
    with pytest.raises(EmbeddingFailedError, match="invalid response"):
        adapter.embed(_request())


def test_adapter_sanitizes_http_and_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never expose raw local response bodies, private input, or paths."""

    private = "PRIVATE_QUERY"
    raw = f'{{"error":"{private} C:/private/model.gguf"}}'.encode("utf-8")
    rejected = _FakeResponse(raw, status_code=500)
    adapter, _factory = _adapter_with(
        monkeypatch,
        [_tags_response(), rejected],
    )
    with pytest.raises(EmbeddingFailedError) as captured:
        adapter.embed(_request(private))
    assert str(captured.value) == "Local embedding request failed."
    assert private not in str(captured.value)
    assert "model.gguf" not in str(captured.value)

    connection_adapter, _ = _adapter_with(
        monkeypatch,
        [RequestsConnectionError(f"{private} C:/private/model.gguf")],
    )
    with pytest.raises(EmbeddingUnavailableError) as connection:
        connection_adapter.embed(_request(private))
    assert str(connection.value) == (
        "Local embedding service is not running or reachable."
    )
    assert private not in str(connection.value)


def test_adapter_rejects_invalid_request_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require the engine-independent validated request at the adapter edge."""

    adapter, factory = _adapter_with(monkeypatch, [])
    with pytest.raises(EmbeddingValidationError, match="validated"):
        adapter.embed(object())  # type: ignore[arg-type]
    assert factory.calls == []


def test_adapter_rebuilds_request_and_rejects_frozen_bypasses_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject subclasses, mutable containers, bad scalars, and limit drift."""

    class _RequestSubclass(EmbeddingRequest):
        """Model a behavior-bearing request subtype."""

    class _InputSubclass(EmbeddingInput):
        """Model a behavior-bearing input subtype."""

    class _StringSubclass(str):
        """Model a behavior-bearing scalar subtype."""

    base_item = EmbeddingInput(item_id="item", text="exact text")
    request_subclass = _RequestSubclass(
        purpose="document",
        items=(base_item,),
    )
    input_subclass = EmbeddingRequest(
        purpose="document",
        items=(_InputSubclass(item_id="item", text="exact text"),),
    )
    scalar_subclass = EmbeddingRequest(
        purpose=_StringSubclass("document"),
        items=(base_item,),
    )
    mutable_items = _request()
    object.__setattr__(mutable_items, "items", [base_item])
    boolean_purpose = _request()
    object.__setattr__(boolean_purpose, "purpose", True)
    oversized_text = _request()
    object.__setattr__(oversized_text.items[0], "text", "x" * 2_001)
    too_many_items = _request()
    object.__setattr__(too_many_items, "items", (base_item,) * 17)

    cases: tuple[tuple[EmbeddingRequest, type[Exception]], ...] = (
        (request_subclass, EmbeddingValidationError),
        (input_subclass, EmbeddingValidationError),
        (scalar_subclass, EmbeddingValidationError),
        (mutable_items, EmbeddingValidationError),
        (boolean_purpose, EmbeddingValidationError),
        (oversized_text, EmbeddingLimitError),
        (too_many_items, EmbeddingLimitError),
    )
    for forged, expected_error in cases:
        adapter, factory = _adapter_with(monkeypatch, [])
        with pytest.raises(expected_error):
            adapter.embed(forged)
        assert factory.calls == []
