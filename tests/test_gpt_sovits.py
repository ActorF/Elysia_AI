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


def _ogg_bytes() -> bytes:
    """Create one structurally complete Ogg page for output-format tests."""

    return b"OggS\x00" + (b"\x00" * 21) + b"\x01\x01\x00"


def _aac_bytes() -> bytes:
    """Create one complete seven-byte ADTS frame for output-format tests."""

    return bytes((0xFF, 0xF1, 0x50, 0x80, 0x00, 0xFF, 0xFC))


class _ServerState:
    """Hold configurable responses and captured requests for a fake API."""

    def __init__(self) -> None:
        self.status = 200
        self.content_type = "audio/wav"
        self.content_encoding: str | None = None
        self.body = _wav_bytes()
        self.content_length: str | None = None
        self.location: str | None = None
        self.delay_seconds = 0.0
        self.paths: list[str] = []
        self.payloads: list[object] = []


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
        if self.server.state.content_length is not None:
            self.send_header(
                "Content-Length",
                self.server.state.content_length,
            )
        if self.server.state.location is not None:
            self.send_header("Location", self.server.state.location)
        self.end_headers()
        self.wfile.write(self.server.state.body)

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
    max_response_bytes: int = 32 * 1024 * 1024,
) -> tuple[GptSovitsSynthesizer, _Resolver]:
    """Create an adapter and expose its recording resolver for assertions."""

    resolver = _Resolver(voice)
    return (
        GptSovitsSynthesizer(
            GptSovitsConfig(
                asset_root=root.resolve(),
                request_timeout_seconds=timeout,
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
        ("ogg", "audio/ogg", _ogg_bytes()),
        ("aac", "audio/aac", _aac_bytes()),
    ],
)
def test_adapter_honors_each_supported_output_format(
    tmp_path: Path,
    audio_format: str,
    content_type: str,
    body: bytes,
) -> None:
    """Map configured media types while preserving bounded encoded bytes."""

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
        ("prompt_language", "auto"),
        ("prompt_language", "ja"),
        ("speed_factor", 0.49),
        ("speed_factor", 2.01),
        ("speed_factor", True),
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
        ("application/json", None, b"{}", None),
        ("audio/wav", "gzip", _wav_bytes(), None),
        ("audio/wav", None, b"not-wave", None),
        ("audio/wav", None, _wav_bytes(), "not-a-number"),
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
    with _serve(state) as base_url:
        adapter, _ = _adapter(tmp_path, _voice(tmp_path, base_url))
        with pytest.raises(SynthesisFailedError) as raised:
            adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service returned invalid audio."
    )


def test_chunked_response_cannot_exceed_configured_byte_limit(
    tmp_path: Path,
) -> None:
    """Enforce the actual streamed byte count when Content-Length is absent."""

    state = _ServerState()
    state.body = _wav_bytes(sample_count=64)
    with _serve(state) as base_url:
        adapter, _ = _adapter(
            tmp_path,
            _voice(tmp_path, base_url),
            max_response_bytes=64,
        )
        with pytest.raises(SynthesisFailedError) as raised:
            adapter.synthesize(SynthesisRequest(text="hello"))

    assert str(raised.value) == (
        "Local speech synthesis service returned invalid audio."
    )
