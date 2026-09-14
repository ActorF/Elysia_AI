"""Test the loopback-only GPT-SoVITS HTTP synthesis adapter."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from threading import Thread
import time
from typing import Iterator, cast
import wave

import pytest

from voice import (
    GptSovitsConfig,
    GptSovitsStatus,
    GptSovitsSynthesizer,
    GptSovitsVoice,
    SynthesisFailedError,
    SynthesisRequest,
    SynthesisUnavailableError,
)


def _wav_bytes(sample_count: int = 12) -> bytes:
    """Create a small valid PCM WAVE response for adapter tests."""

    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\x01\x00" * sample_count)
    return buffer.getvalue()


def _aac_bytes() -> bytes:
    """Create one complete ADTS frame with a non-empty AAC payload."""

    return bytes((0xFF, 0xF1, 0x50, 0x80, 0x01, 0x1F, 0xFC, 0x01))


class _ServerState:
    """Hold configurable responses and captured requests for a fake API."""

    def __init__(self) -> None:
        self.status = 200
        self.content_type = "audio/wav"
        self.content_encoding: str | None = None
        self.body = _wav_bytes()
        self.content_length: str | None = None
        self.send_content_length = True
        self.transfer_encoding: str | None = None
        self.location: str | None = None
        self.delay_seconds = 0.0
        self.body_chunks: tuple[bytes, ...] | None = None
        self.body_chunk_delay_seconds = 0.0
        self.paths: list[str] = []
        self.payloads: list[object] = []
        self.get_paths: list[str] = []
        self.openapi_status = 200
        self.openapi_content_type = "application/json"
        self.openapi_content_encoding: str | None = None
        self.openapi_content_length: str | None = None
        self.send_openapi_content_length = True
        self.openapi_transfer_encoding: str | None = None
        self.openapi_body = json.dumps(
            {"openapi": "3.1.0", "paths": {"/tts": {"post": {}}}}
        ).encode("utf-8")
        self.openapi_chunks: tuple[bytes, ...] | None = None
        self.openapi_chunk_delay_seconds = 0.0


class _Handler(BaseHTTPRequestHandler):
    """Serve deterministic GPT-SoVITS-like responses without external models."""

    server: "_TestServer"

    def do_POST(self) -> None:  # noqa: N802
        """Capture one JSON request and return the configured binary response."""

        length = int(self.headers.get("Content-Length", "0"))
        raw_payload = self.rfile.read(length)
        self.server.state.paths.append(self.path)
        self.server.state.payloads.append(json.loads(raw_payload))
        if self.server.state.delay_seconds:
            time.sleep(self.server.state.delay_seconds)
        self.send_response(self.server.state.status)
        self.send_header("Content-Type", self.server.state.content_type)
        if self.server.state.content_encoding is not None:
            self.send_header(
                "Content-Encoding",
                self.server.state.content_encoding,
            )
        if self.server.state.transfer_encoding is not None:
            self.send_header(
                "Transfer-Encoding",
                self.server.state.transfer_encoding,
            )
        if self.server.state.send_content_length:
            self.send_header(
                "Content-Length",
                self.server.state.content_length
                if self.server.state.content_length is not None
                else str(len(self.server.state.body)),
            )
        if self.server.state.location is not None:
            self.send_header("Location", self.server.state.location)
        self.end_headers()
        self._write_slow_chunks(
            self.server.state.body,
            self.server.state.body_chunks,
            self.server.state.body_chunk_delay_seconds,
        )

    def do_GET(self) -> None:  # noqa: N802
        """Return a configurable OpenAPI document for readiness probes."""

        self.server.state.get_paths.append(self.path)
        self.send_response(self.server.state.openapi_status)
        self.send_header(
            "Content-Type",
            self.server.state.openapi_content_type,
        )
        if self.server.state.openapi_content_encoding is not None:
            self.send_header(
                "Content-Encoding",
                self.server.state.openapi_content_encoding,
            )
        if self.server.state.openapi_transfer_encoding is not None:
            self.send_header(
                "Transfer-Encoding",
                self.server.state.openapi_transfer_encoding,
            )
        if self.server.state.send_openapi_content_length:
            self.send_header(
                "Content-Length",
                self.server.state.openapi_content_length
                if self.server.state.openapi_content_length is not None
                else str(len(self.server.state.openapi_body)),
            )
        self.end_headers()
        self._write_slow_chunks(
            self.server.state.openapi_body,
            self.server.state.openapi_chunks,
            self.server.state.openapi_chunk_delay_seconds,
        )

    def _write_slow_chunks(
        self,
        body: bytes,
        chunks: tuple[bytes, ...] | None,
        delay_seconds: float,
    ) -> None:
        """Flush small chunks whose individual gaps stay below read timeout."""

        selected = chunks if chunks is not None else (body,)
        for index, chunk in enumerate(selected):
            if index and delay_seconds:
                time.sleep(delay_seconds)
            self.wfile.write(chunk)
            self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        """Suppress the standard HTTP server's stderr output during tests."""


class _TestServer(ThreadingHTTPServer):
    """Attach typed mutable state to the standard threaded test server."""

    def __init__(self, state: _ServerState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), _Handler)

    def handle_error(
        self,
        request: object,
        client_address: object,
    ) -> None:
        """Suppress expected broken pipes from timeout-focused client tests."""


@contextmanager
def _serve(state: _ServerState) -> Iterator[str]:
    """Run a fake loopback API and yield its canonical base URL."""

    server = _TestServer(state)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = cast(str, server.socket.getsockname()[0])
        port = cast(int, server.socket.getsockname()[1])
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class _Resolver:
    """Return one prevalidated voice while recording logical selections."""

    def __init__(self, voice: GptSovitsVoice) -> None:
        self.voice = voice
        self.calls: list[tuple[str, str]] = []

    def resolve(self, profile_id: str, emotion: str) -> GptSovitsVoice:
        """Record profile and emotion before returning the configured voice."""

        self.calls.append((profile_id, emotion))
        return self.voice


class _RejectingResolver:
    """Model a catalog that cannot resolve the requested logical selection."""

    def resolve(self, profile_id: str, emotion: str) -> GptSovitsVoice:
        """Raise the same sanitized domain error as the real catalog."""

        raise SynthesisUnavailableError("selection unavailable")


def _assets(root: Path) -> tuple[Path, Path, Path]:
    """Create harmless stand-ins for required local-only voice assets."""

    gpt = root / "voice.ckpt"
    sovits = root / "voice.pth"
    reference = root / "reference.wav"
    gpt.write_bytes(b"fake-gpt")
    sovits.write_bytes(b"fake-sovits")
    reference.write_bytes(_wav_bytes())
    return gpt, sovits, reference


def _voice(
    root: Path,
    base_url: str,
    *,
    speed_factor: float = 1.0,
    audio_format: str = "wav",
) -> GptSovitsVoice:
    """Build one complete voice selection under the allowed asset root."""

    gpt, sovits, reference = _assets(root)
    return GptSovitsVoice(
        base_url=base_url,
        gpt_weights_path=gpt.resolve(),
        sovits_weights_path=sovits.resolve(),
        reference_audio_path=reference.resolve(),
        prompt_text="这是准确的参考文本。",
        prompt_language="zh",
        speed_factor=speed_factor,
        audio_format=audio_format,  # type: ignore[arg-type]
    )


def _adapter(
    root: Path,
    voice: GptSovitsVoice,
    *,
    timeout: float = 1.0,
    probe_timeout: float = 1.0,
    max_response_bytes: int = 32 * 1024 * 1024,
) -> tuple[GptSovitsSynthesizer, _Resolver]:
    """Create an adapter and expose its recording resolver for assertions."""

    resolver = _Resolver(voice)
    return (
        GptSovitsSynthesizer(
            GptSovitsConfig(
                asset_root=root.resolve(),
                request_timeout_seconds=timeout,
                probe_timeout_seconds=probe_timeout,
                deterministic_seed=2026,
                max_response_bytes=max_response_bytes,
            ),
            resolver,
        ),
        resolver,
    )


def test_adapter_maps_logical_request_to_one_non_streaming_post(
    tmp_path: Path,
) -> None:
    """Send only documented API fields and never invoke global weight switches."""

    state = _ServerState()
    with _serve(state) as base_url:
        adapter, resolver = _adapter(tmp_path, _voice(tmp_path, base_url))
        result = adapter.synthesize(
            SynthesisRequest(
                text="你好，爱莉希雅。",
                language="zh",
                profile_id="elysia",
                emotion="happy",
            )
        )

    assert resolver.calls == [("elysia", "happy")]
    assert state.paths == ["/tts"]
    assert state.payloads == [
        {
            "text": "你好，爱莉希雅。",
            "text_lang": "zh",
            "ref_audio_path": str((tmp_path / "reference.wav").resolve()),
            "prompt_text": "这是准确的参考文本。",
            "prompt_lang": "zh",
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": 1.0,
            "seed": 2026,
            "media_type": "wav",
            "streaming_mode": False,
            "parallel_infer": False,
        }
    ]
    assert result.audio == state.body
    assert result.media_type == "audio/wav"


def test_same_text_repeatedly_returns_valid_audio(tmp_path: Path) -> None:
    """Keep request mapping deterministic while accepting repeatable audio."""

    state = _ServerState()
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        request = SynthesisRequest(text="愿新的旅途充满美好。", language="zh")
        first = adapter.synthesize(request)
        second = adapter.synthesize(request)

    assert first.audio == state.body
    assert second.audio == state.body
    assert state.paths == ["/tts", "/tts"]
    assert state.payloads[0] == state.payloads[1]


@pytest.mark.parametrize(
    ("audio_format", "content_type", "body"),
    [
        ("wav", "audio/x-wav", _wav_bytes()),
        ("aac", "audio/aac", _aac_bytes()),
    ],
)
def test_adapter_honors_each_supported_output_format(
    tmp_path: Path,
    audio_format: str,
    content_type: str,
    body: bytes,
) -> None:
    """Map supported non-streaming media while preserving bounded bytes."""

    state = _ServerState()
    state.content_type = content_type
    state.body = body
    with _serve(state) as base_url:
        adapter, _ = _adapter(
            tmp_path,
            _voice(tmp_path, base_url, audio_format=audio_format),
        )
        result = adapter.synthesize(SynthesisRequest(text="hello"))

    assert result.audio == body
    assert result.audio_format == audio_format
    assert isinstance(state.payloads[0], dict)
    assert state.payloads[0]["media_type"] == audio_format


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:9880",
        "http://example.com:9880",
        "http://user:secret@127.0.0.1:9880",
        "http://127.0.0.1:9880/api",
        "http://127.0.0.1:9880?query=yes",
        "http://127.0.0.1:0",
        "not-a-url",
        "",
    ],
)
def test_voice_rejects_non_loopback_or_ambiguous_base_urls(
    tmp_path: Path,
    base_url: str,
) -> None:
    """Prevent profile configuration from turning synthesis into SSRF."""

    with pytest.raises(ValueError, match="base_url"):
        _voice(tmp_path, base_url)


def test_voice_normalizes_localhost_before_network_io(tmp_path: Path) -> None:
    """Avoid placing DNS resolution outside the bounded socket lifecycle."""

    voice = _voice(tmp_path, "HTTP://LOCALHOST:9880/")

    assert voice.base_url == "http://127.0.0.1:9880"


@pytest.mark.parametrize(
    ("field_name", "suffix"),
    [
        ("gpt_weights_path", ".bin"),
        ("sovits_weights_path", ".ckpt"),
        ("reference_audio_path", ".mp3"),
    ],
)
def test_voice_rejects_incorrect_asset_types(
    tmp_path: Path,
    field_name: str,
    suffix: str,
) -> None:
    """Reject accidentally swapped weights and unsupported reference formats."""

    gpt, sovits, reference = _assets(tmp_path)
    values: dict[str, object] = {
        "base_url": "http://127.0.0.1:9880",
        "gpt_weights_path": gpt.resolve(),
        "sovits_weights_path": sovits.resolve(),
        "reference_audio_path": reference.resolve(),
        "prompt_text": "准确文本",
        "prompt_language": "zh",
    }
    values[field_name] = (tmp_path / f"wrong{suffix}").resolve()

    with pytest.raises(ValueError, match=field_name):
        GptSovitsVoice(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("prompt_text", ""),
        ("prompt_text", "\ufeff \n"),
        ("prompt_text", "\u200b\u2060"),
        ("prompt_text", "...——"),
        ("prompt_text", "contains\x00nul"),
        ("prompt_language", "auto"),
        ("prompt_language", "ja"),
        ("speed_factor", 0.49),
        ("speed_factor", 2.01),
        ("speed_factor", True),
        ("audio_format", "ogg"),
        ("audio_format", "mp3"),
    ],
)
def test_voice_rejects_invalid_reference_and_output_configuration(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    """Keep exact prompt and output controls inside the supported vocabulary."""

    gpt, sovits, reference = _assets(tmp_path)
    values: dict[str, object] = {
        "base_url": "http://127.0.0.1:9880",
        "gpt_weights_path": gpt.resolve(),
        "sovits_weights_path": sovits.resolve(),
        "reference_audio_path": reference.resolve(),
        "prompt_text": "准确文本",
        "prompt_language": "zh",
        "speed_factor": 1.0,
        "audio_format": "wav",
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        GptSovitsVoice(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("asset_root", Path("relative")),
        ("request_timeout_seconds", 0.0),
        ("request_timeout_seconds", 301.0),
        ("request_timeout_seconds", float("nan")),
        ("request_timeout_seconds", True),
        ("probe_timeout_seconds", 0.0),
        ("probe_timeout_seconds", 10.01),
        ("probe_timeout_seconds", float("nan")),
        ("probe_timeout_seconds", True),
        ("deterministic_seed", -1),
        ("deterministic_seed", 2_147_483_648),
        ("deterministic_seed", True),
        ("max_response_bytes", 63),
        ("max_response_bytes", 32 * 1024 * 1024 + 1),
        ("max_response_bytes", True),
    ],
)
def test_config_rejects_unsafe_values(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    """Reject unbounded network work, random behavior, and ambiguous roots."""

    values: dict[str, object] = {
        "asset_root": tmp_path.resolve(),
        "request_timeout_seconds": 1.0,
        "probe_timeout_seconds": 1.0,
        "deterministic_seed": 42,
        "max_response_bytes": 32 * 1024 * 1024,
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        GptSovitsConfig(**values)  # type: ignore[arg-type]


def test_missing_asset_returns_sanitized_unavailable_error(tmp_path: Path) -> None:
    """Report local setup failure without exposing an asset path."""

    voice = _voice(tmp_path, "http://127.0.0.1:9880")
    voice.reference_audio_path.unlink()
    adapter, _ = _adapter(tmp_path, voice)

    with pytest.raises(SynthesisUnavailableError) as raised:
        adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Configured local speech synthesis assets are unavailable."
    )
    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize(
    "asset_content",
    [b"", b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\n"],
)
def test_empty_or_lfs_pointer_asset_is_unavailable(
    tmp_path: Path,
    asset_content: bytes,
) -> None:
    """Reject incomplete local model installs before contacting the service."""

    voice = _voice(tmp_path, "http://127.0.0.1:9880")
    voice.gpt_weights_path.write_bytes(asset_content)
    adapter, _ = _adapter(tmp_path, voice)

    with pytest.raises(SynthesisUnavailableError) as raised:
        adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Configured local speech synthesis assets are unavailable."
    )


def test_non_wave_reference_asset_is_unavailable(tmp_path: Path) -> None:
    """Reject a renamed or corrupt reference clip before inference begins."""

    voice = _voice(tmp_path, "http://127.0.0.1:9880")
    voice.reference_audio_path.write_bytes(b"not-a-wave")
    adapter, _ = _adapter(tmp_path, voice)

    with pytest.raises(SynthesisUnavailableError, match="assets are unavailable"):
        adapter.synthesize(SynthesisRequest(text="hello"))


def test_symlink_escape_returns_sanitized_unavailable_error(
    tmp_path: Path,
) -> None:
    """Resolve links before enforcing the local voice asset containment root."""

    asset_root = tmp_path / "assets"
    outside = tmp_path / "outside"
    asset_root.mkdir()
    outside.mkdir()
    gpt, sovits, reference = _assets(outside)
    linked_reference = asset_root / "reference.wav"
    try:
        linked_reference.symlink_to(reference)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links.")
    voice = GptSovitsVoice(
        base_url="http://127.0.0.1:9880",
        gpt_weights_path=gpt.resolve(),
        sovits_weights_path=sovits.resolve(),
        reference_audio_path=linked_reference.absolute(),
        prompt_text="准确文本",
        prompt_language="zh",
    )
    adapter, _ = _adapter(asset_root, voice)

    with pytest.raises(SynthesisUnavailableError) as raised:
        adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Configured local speech synthesis assets are unavailable."
    )


def test_service_not_running_returns_actionable_unavailable_error(
    tmp_path: Path,
) -> None:
    """Map connection refusal to one stable message without native details."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host = cast(str, server.socket.getsockname()[0])
    port = cast(int, server.socket.getsockname()[1])
    server.server_close()
    adapter, _ = _adapter(tmp_path, _voice(tmp_path, f"http://{host}:{port}"))

    with pytest.raises(SynthesisUnavailableError) as raised:
        adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service is not running or reachable."
    )
    assert str(port) not in str(raised.value)


def test_service_timeout_returns_the_same_unavailable_error(tmp_path: Path) -> None:
    """Treat a stalled local inference service as unavailable without retrying."""

    state = _ServerState()
    state.delay_seconds = 0.25
    with _serve(state) as base_url:
        adapter, _ = _adapter(
            tmp_path,
            _voice(tmp_path, base_url),
            timeout=0.1,
        )
        with pytest.raises(SynthesisUnavailableError) as raised:
            adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service is not running or reachable."
    )
    assert state.paths == ["/tts"]


def test_total_synthesis_deadline_rejects_continuous_slow_chunks(
    tmp_path: Path,
) -> None:
    """Prevent frequent body bytes from extending the total synthesis budget."""

    state = _ServerState()
    state.body_chunks = (
        state.body[:1],
        state.body[1:2],
        state.body[2:],
    )
    # Each 300 ms gap is below the original 400 ms inactivity timeout. Only
    # shrinking the next socket timeout to the remaining total budget can stop
    # the third read near 400 ms instead of letting it complete near 600 ms.
    state.body_chunk_delay_seconds = 0.3
    with _serve(state) as base_url:
        adapter, _ = _adapter(
            tmp_path,
            _voice(tmp_path, base_url),
            timeout=0.4,
        )
        started = time.monotonic()
        with pytest.raises(SynthesisUnavailableError) as raised:
            adapter.synthesize(SynthesisRequest(text="private local text"))
        elapsed = time.monotonic() - started

    assert elapsed == pytest.approx(0.4, abs=0.12)
    assert str(raised.value) == (
        "Local speech synthesis service is not running or reachable."
    )
    assert "private" not in str(raised.value)
    assert state.paths == ["/tts"]


def test_total_probe_deadline_rejects_continuous_slow_chunks(
    tmp_path: Path,
) -> None:
    """Prevent frequent OpenAPI bytes from extending the total probe budget."""

    state = _ServerState()
    state.openapi_chunks = (
        state.openapi_body[:1],
        state.openapi_body[1:2],
        state.openapi_body[2:],
    )
    state.openapi_chunk_delay_seconds = 0.3
    with _serve(state) as base_url:
        adapter, _ = _adapter(
            tmp_path,
            _voice(tmp_path, base_url),
            probe_timeout=0.4,
        )
        started = time.monotonic()
        status = adapter.get_status()
        elapsed = time.monotonic() - started

    assert elapsed == pytest.approx(0.4, abs=0.12)
    assert status == GptSovitsStatus("unavailable", "service_unreachable")
    assert state.get_paths == ["/openapi.json"]


def test_http_error_is_sanitized_and_does_not_leak_service_body(
    tmp_path: Path,
) -> None:
    """Keep upstream exception text and local paths out of public failures."""

    state = _ServerState()
    state.status = 400
    state.content_type = "application/json"
    state.body = b'{"Exception":"C:/private/model failed"}'
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        with pytest.raises(SynthesisFailedError) as raised:
            adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service rejected the request."
    )
    assert "private" not in str(raised.value)
    assert state.paths == ["/tts"]


def test_redirect_is_rejected_without_contacting_destination(tmp_path: Path) -> None:
    """Prevent a loopback service from redirecting voice data to another host."""

    state = _ServerState()
    state.status = 307
    state.location = "https://example.com/collect"
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        with pytest.raises(SynthesisFailedError, match="rejected"):
            adapter.synthesize(SynthesisRequest(text="secret local text"))

    assert state.paths == ["/tts"]


@pytest.mark.parametrize(
    ("content_type", "content_encoding", "body", "content_length"),
    [
        ("audio/wav", None, _wav_bytes(), None),
        ("application/json", None, b"{}", None),
        ("audio/wav", "gzip", _wav_bytes(), None),
        ("audio/wav", None, b"not-wave", None),
        ("audio/wav", None, _wav_bytes(), "not-a-number"),
        ("audio/wav", None, _wav_bytes(), "9" * 5_000),
        ("audio/wav", None, _wav_bytes(), "0"),
        ("audio/wav", None, b"", str(32 * 1024 * 1024 + 1)),
    ],
)
def test_invalid_service_audio_is_rejected(
    tmp_path: Path,
    content_type: str,
    content_encoding: str | None,
    body: bytes,
    content_length: str | None,
) -> None:
    """Reject mismatched, compressed, malformed, or excessive responses."""

    state = _ServerState()
    state.content_type = content_type
    state.content_encoding = content_encoding
    state.body = body
    state.content_length = content_length
    state.send_content_length = content_length is not None
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        with pytest.raises(SynthesisFailedError) as raised:
            adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service returned invalid audio."
    )


def test_transfer_encoded_audio_response_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject chunk framing whose size lines could renew socket inactivity."""

    state = _ServerState()
    state.send_content_length = False
    state.transfer_encoding = "chunked"
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        with pytest.raises(SynthesisFailedError) as raised:
            adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service returned invalid audio."
    )


def test_status_reports_available_without_claiming_model_binding(
    tmp_path: Path,
) -> None:
    """Recognize the API surface while reserving ready for trusted attestation."""

    state = _ServerState()
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        status = adapter.get_status()

    assert status == GptSovitsStatus(
        state="available",
        reason="service_binding_unverified",
    )
    assert state.get_paths == ["/openapi.json"]
    assert state.paths == []


def test_status_distinguishes_selection_assets_and_offline_service(
    tmp_path: Path,
) -> None:
    """Return closed sanitized reasons without generating probe audio."""

    online_state = _ServerState()
    with _serve(online_state) as base_url:
        voice = _voice(tmp_path, base_url)
        adapter, _ = _adapter(tmp_path, voice)
        voice.reference_audio_path.unlink()
        missing_assets = adapter.get_status()

    missing_selection = GptSovitsSynthesizer(
        GptSovitsConfig(
            asset_root=tmp_path.resolve(),
            request_timeout_seconds=1.0,
        ),
        _RejectingResolver(),
    ).get_status(profile_id="missing")

    offline_voice = _voice(tmp_path, "http://127.0.0.1:1")
    offline, _ = _adapter(tmp_path, offline_voice)
    service_offline = offline.get_status()

    assert missing_selection == GptSovitsStatus(
        "unavailable",
        "selection_unavailable",
    )
    assert missing_assets == GptSovitsStatus("unavailable", "assets_unavailable")
    assert service_offline == GptSovitsStatus(
        "unavailable",
        "service_unreachable",
    )


@pytest.mark.parametrize(
    ("status_code", "content_type", "encoding", "body", "length"),
    [
        (404, "application/json", None, b"{}", None),
        (200, "text/html", None, b"{}", None),
        (200, "application/json", "gzip", b"{}", None),
        (200, "application/json", None, b"not-json", None),
        (200, "application/json", None, b"[]", None),
        (200, "application/json", None, b'{"paths":{}}', None),
        (
            200,
            "application/json",
            None,
            b'{"openapi":"3.1.0","paths":{"/tts":{"post":{}}}}',
            None,
        ),
        (
            200,
            "application/json",
            None,
            b'{"paths":{"/tts":{"get":{}}}}',
            None,
        ),
        (200, "application/json", None, b"{}", "0"),
        (200, "application/json", None, b"{}", "not-a-number"),
        (200, "application/json", None, b"{}", "9" * 5_000),
        (200, "application/json", None, b"", str(512 * 1024 + 1)),
    ],
)
def test_status_rejects_invalid_or_ambiguous_service_api(
    tmp_path: Path,
    status_code: int,
    content_type: str,
    encoding: str | None,
    body: bytes,
    length: str | None,
) -> None:
    """Require the exact bounded POST API shape without exposing its response."""

    state = _ServerState()
    state.openapi_status = status_code
    state.openapi_content_type = content_type
    state.openapi_content_encoding = encoding
    state.openapi_body = body
    state.openapi_content_length = length
    state.send_openapi_content_length = length is not None
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        status = adapter.get_status()

    assert status == GptSovitsStatus("unavailable", "invalid_service")


def test_status_rejects_transfer_encoded_openapi(tmp_path: Path) -> None:
    """Keep readiness on a length-declared identity response shape."""

    state = _ServerState()
    state.send_openapi_content_length = False
    state.openapi_transfer_encoding = "chunked"
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        status = adapter.get_status()

    assert status == GptSovitsStatus("unavailable", "invalid_service")


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("unavailable", None),
        ("unavailable", "service_binding_unverified"),
        ("available", None),
        ("available", "service_unreachable"),
        ("ready", "service_binding_unverified"),
        ("unknown", None),
    ],
)
def test_status_rejects_inconsistent_state_reason_combinations(
    state: object,
    reason: object,
) -> None:
    """Keep future protocol serialization on one exact readiness vocabulary."""

    with pytest.raises(ValueError, match="status|Status|reason|binding"):
        GptSovitsStatus(
            state=state,  # type: ignore[arg-type]
            reason=reason,  # type: ignore[arg-type]
        )
