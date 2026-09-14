"""Test lazy local speech synthesis composition and sanitized readiness."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from threading import Thread
from typing import Iterator, cast
from contextlib import contextmanager
import wave

import pytest

from voice import (
    GptSovitsStatus,
    LocalSpeechSynthesisService,
    SynthesisRequest,
    SynthesisUnavailableError,
    create_local_speech_synthesis_service,
)


def _wav_bytes() -> bytes:
    """Create deterministic encoded speech for the fake local service."""

    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(b"\x01\x00" * 24)
    return output.getvalue()


class _Handler(BaseHTTPRequestHandler):
    """Expose only the two GPT-SoVITS endpoints used by composition tests."""

    def do_GET(self) -> None:  # noqa: N802
        """Return an OpenAPI document advertising POST ``/tts``."""

        body = json.dumps(
            {"openapi": "3.1.0", "paths": {"/tts": {"post": {}}}}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        """Return valid local WAVE bytes for one synthesis request."""

        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = _wav_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Suppress standard request logs during the local fake test."""


@contextmanager
def _serve() -> Iterator[str]:
    """Run one local fake service and yield its loopback origin."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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


def _install_local_config(
    base_dir: Path,
    base_url: str,
    *,
    rights_status: str = "verified",
) -> None:
    """Create synthetic ignored-style assets and their device-local catalog."""

    asset_root = base_dir / "models" / "weights" / "gpt-sovits"
    profile_root = asset_root / "sample"
    weights = profile_root / "weights"
    references = profile_root / "references"
    weights.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    (weights / "voice.ckpt").write_bytes(b"fake-gpt")
    (weights / "voice.pth").write_bytes(b"fake-sovits")
    (references / "neutral.wav").write_bytes(_wav_bytes())
    document = {
        "schema_version": 1,
        "default_profile_id": "sample",
        "profiles": [
            {
                "profile_id": "sample",
                "display_name": "Synthetic test voice",
                "base_url": base_url,
                "gpt_weights": "sample/weights/voice.ckpt",
                "sovits_weights": "sample/weights/voice.pth",
                "speed_factor": 1.0,
                "audio_format": "wav",
                "rights_status": rights_status,
                "references": [
                    {
                        "emotion": "neutral",
                        "audio": "sample/references/neutral.wav",
                        "prompt_text": "Original test reference sentence.",
                        "prompt_language": "en",
                    }
                ],
            }
        ],
    }
    catalog_path = base_dir / "workspace" / "settings" / "voice-profiles.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )


def test_construction_performs_no_filesystem_or_network_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep text Chat startup independent from every optional TTS component."""

    calls: list[str] = []

    def fail_if_probed(*args: object, **kwargs: object) -> None:
        """Record and fail if lazy construction accidentally performs I/O."""

        calls.append("probed")
        raise AssertionError("construction must remain lazy")

    monkeypatch.setattr(Path, "is_file", fail_if_probed)
    service = create_local_speech_synthesis_service(tmp_path.resolve())

    assert isinstance(service, LocalSpeechSynthesisService)
    assert calls == []


def test_missing_catalog_returns_closed_unavailable_status(tmp_path: Path) -> None:
    """Describe an unconfigured optional service without raising at startup."""

    service = create_local_speech_synthesis_service(tmp_path.resolve())

    status = service.get_status()

    assert status.adapter == GptSovitsStatus("unavailable", "catalog_missing")
    assert status.profiles == ()


def test_invalid_catalog_returns_closed_unavailable_status(tmp_path: Path) -> None:
    """Keep malformed private configuration details outside readiness output."""

    catalog = tmp_path / "workspace" / "settings" / "voice-profiles.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("not-json", encoding="utf-8")
    (tmp_path / "models" / "weights" / "gpt-sovits").mkdir(parents=True)
    service = create_local_speech_synthesis_service(tmp_path.resolve())

    status = service.get_status()

    assert status.adapter == GptSovitsStatus("unavailable", "catalog_invalid")
    assert "not-json" not in repr(status)


def test_missing_asset_root_is_distinct_from_missing_catalog(tmp_path: Path) -> None:
    """Report an installed catalog whose local model root is absent."""

    catalog = tmp_path / "workspace" / "settings" / "voice-profiles.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{}", encoding="utf-8")
    service = create_local_speech_synthesis_service(tmp_path.resolve())

    assert service.get_status().adapter == GptSovitsStatus(
        "unavailable",
        "assets_unavailable",
    )


def test_online_service_reports_available_but_binding_unverified(
    tmp_path: Path,
) -> None:
    """Never claim model identity from an unauthenticated upstream API alone."""

    with _serve() as base_url:
        _install_local_config(tmp_path, base_url)
        service = create_local_speech_synthesis_service(tmp_path.resolve())
        status = service.get_status()

    assert status.adapter == GptSovitsStatus(
        "available",
        "service_binding_unverified",
    )
    assert status.profiles[0].profile_id == "sample"
    assert status.profiles[0].is_default is True


def test_offline_service_preserves_safe_profile_choices(tmp_path: Path) -> None:
    """Return service_unreachable while retaining non-sensitive local choices."""

    _install_local_config(tmp_path, "http://127.0.0.1:1")
    service = create_local_speech_synthesis_service(tmp_path.resolve())

    status = service.get_status()

    assert status.adapter == GptSovitsStatus(
        "unavailable",
        "service_unreachable",
    )
    assert status.profiles[0].display_name == "Synthetic test voice"
    assert not hasattr(status.profiles[0], "base_url")


def test_local_evaluation_policy_is_explicit_and_visible(tmp_path: Path) -> None:
    """Require opt-in for unresolved rights while preserving honest metadata."""

    _install_local_config(
        tmp_path,
        "http://127.0.0.1:1",
        rights_status="local-evaluation-only",
    )
    disabled = create_local_speech_synthesis_service(tmp_path.resolve())
    enabled = create_local_speech_synthesis_service(
        tmp_path.resolve(),
        allow_local_evaluation=True,
    )

    assert disabled.get_status().adapter.reason == "selection_unavailable"
    assert disabled.get_status().profiles[0].rights_status == (
        "local-evaluation-only"
    )
    assert enabled.get_status().adapter.reason == "service_unreachable"


def test_service_synthesizes_through_fresh_local_catalog(tmp_path: Path) -> None:
    """Connect catalog resolution and the HTTP adapter without desktop IPC."""

    with _serve() as base_url:
        _install_local_config(tmp_path, base_url)
        service = create_local_speech_synthesis_service(tmp_path.resolve())
        result = service.synthesize(
            SynthesisRequest(text="Hello from local speech.", language="en")
        )

    assert result.audio == _wav_bytes()
    assert result.media_type == "audio/wav"


def test_service_recovers_after_catalog_is_installed_without_restart(
    tmp_path: Path,
) -> None:
    """Reload repaired device-local setup instead of caching startup failure."""

    service = create_local_speech_synthesis_service(tmp_path.resolve())
    assert service.get_status().adapter.reason == "catalog_missing"

    with _serve() as base_url:
        _install_local_config(tmp_path, base_url)
        recovered = service.get_status()

    assert recovered.adapter.reason == "service_binding_unverified"


def test_synthesis_maps_missing_and_invalid_catalogs_to_stable_errors(
    tmp_path: Path,
) -> None:
    """Avoid leaking parser or filesystem details from explicit TTS calls."""

    service = create_local_speech_synthesis_service(tmp_path.resolve())
    with pytest.raises(SynthesisUnavailableError) as missing:
        service.synthesize(SynthesisRequest(text="hello"))
    assert str(missing.value) == "Local speech synthesis is not configured."

    catalog = tmp_path / "workspace" / "settings" / "voice-profiles.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("invalid", encoding="utf-8")
    (tmp_path / "models" / "weights" / "gpt-sovits").mkdir(parents=True)
    with pytest.raises(SynthesisUnavailableError) as invalid:
        service.synthesize(SynthesisRequest(text="hello"))
    assert str(invalid.value) == (
        "Local speech synthesis configuration is invalid."
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("base_dir", Path("relative")),
        ("allow_local_evaluation", "yes"),
        ("request_timeout_seconds", 0.0),
        ("probe_timeout_seconds", 11.0),
        ("deterministic_seed", -1),
    ],
)
def test_factory_rejects_invalid_runtime_configuration(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    """Reject ambiguous paths and unbounded runtime values before optional use."""

    values: dict[str, object] = {
        "base_dir": tmp_path.resolve(),
        "allow_local_evaluation": False,
        "request_timeout_seconds": 120.0,
        "probe_timeout_seconds": 1.0,
        "deterministic_seed": 42,
    }
    values[field_name] = value

    with pytest.raises((TypeError, ValueError)):
        create_local_speech_synthesis_service(**values)  # type: ignore[arg-type]
