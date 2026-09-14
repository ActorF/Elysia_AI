"""Test the privacy-preserving local synthesis smoke command."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import cast
import wave

import pytest

from scripts import smoke_gpt_sovits
from voice import (
    GptSovitsStatus,
    GptSovitsStatusReason,
    GptSovitsStatusState,
    LocalSpeechSynthesisService,
    LocalSpeechSynthesisStatus,
    SynthesisFailedError,
    SynthesisRequest,
    SynthesisResult,
)


def _wav_bytes(sample_count: int = 24) -> bytes:
    """Create deterministic playable WAV bytes for smoke-command tests."""

    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(b"\x01\x00" * sample_count)
    return output.getvalue()


def _ogg_bytes() -> bytes:
    """Create one structurally valid Ogg page for format rejection tests."""

    return b"OggS\x00" + (b"\x00" * 21) + b"\x01\x01\x00"


class _FakeService:
    """Record selections and return configurable synthesis outcomes."""

    def __init__(
        self,
        results: list[SynthesisResult] | None = None,
        *,
        state: str = "available",
        reason: str = "service_binding_unverified",
        error: Exception | None = None,
    ) -> None:
        """Configure readiness, ordered results, and an optional failure."""

        self.results = results or [
            SynthesisResult(_wav_bytes(), "wav", 1.0)
        ]
        self.state = state
        self.reason = reason
        self.error = error
        self.status_calls: list[tuple[str, str]] = []
        self.requests: list[SynthesisRequest] = []

    def get_status(
        self,
        *,
        profile_id: str = "default",
        emotion: str = "neutral",
    ) -> LocalSpeechSynthesisStatus:
        """Return configured sanitized readiness and record the selection."""

        self.status_calls.append((profile_id, emotion))
        return LocalSpeechSynthesisStatus(
            GptSovitsStatus(
                cast(GptSovitsStatusState, self.state),
                cast(GptSovitsStatusReason, self.reason),
            ),
            (),
        )

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Record a request, raise the configured error, or cycle results."""

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.results[(len(self.requests) - 1) % len(self.results)]


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeService,
) -> None:
    """Replace production composition without weakening CLI assertions."""

    monkeypatch.setattr(
        smoke_gpt_sovits,
        "_create_service",
        lambda: cast(LocalSpeechSynthesisService, service),
    )


def test_create_service_uses_all_application_tts_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep manual acceptance aligned with production composition settings."""

    captured: dict[str, object] = {}
    settings = SimpleNamespace(
        base_dir=tmp_path,
        gpt_sovits_allow_local_evaluation=True,
        gpt_sovits_request_timeout_seconds=45.0,
        gpt_sovits_probe_timeout_seconds=2.0,
        gpt_sovits_deterministic_seed=99,
    )

    def capture_factory(
        base_dir: Path,
        **options: object,
    ) -> LocalSpeechSynthesisService:
        """Capture factory arguments without constructing a real service."""

        captured["base_dir"] = base_dir
        captured.update(options)
        return cast(LocalSpeechSynthesisService, object())

    monkeypatch.setattr(smoke_gpt_sovits, "SETTINGS", settings)
    monkeypatch.setattr(
        smoke_gpt_sovits,
        "create_local_speech_synthesis_service",
        capture_factory,
    )

    smoke_gpt_sovits._create_service()

    assert captured == {
        "base_dir": tmp_path,
        "allow_local_evaluation": True,
        "request_timeout_seconds": 45.0,
        "probe_timeout_seconds": 2.0,
        "deterministic_seed": 99,
    }


def test_main_synthesizes_same_request_twice_for_each_emotion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise repetition and ordered multi-emotion selection in one run."""

    service = _FakeService()
    _install_fake(monkeypatch, service)

    status = smoke_gpt_sovits.main(
        [
            "--profile",
            "sample",
            "--emotion",
            "neutral",
            "--emotion",
            "happy",
        ]
    )

    output = capsys.readouterr()
    assert status == 0
    assert output.err == ""
    assert service.status_calls == [
        ("sample", "neutral"),
        ("sample", "happy"),
    ]
    assert [request.emotion for request in service.requests] == [
        "neutral",
        "neutral",
        "happy",
        "happy",
    ]
    assert service.requests[0] == service.requests[1]
    assert service.requests[2] == service.requests[3]


def test_repeated_audio_may_have_distinct_hashes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require valid repeated output without promising byte determinism."""

    service = _FakeService(
        [
            SynthesisResult(_wav_bytes(24), "wav", 1.0),
            SynthesisResult(_wav_bytes(48), "wav", 1.0),
        ]
    )
    _install_fake(monkeypatch, service)

    assert smoke_gpt_sovits.main([]) == 0

    summaries = json.loads(capsys.readouterr().out)["results"]
    assert len(summaries) == 2
    assert summaries[0]["sha256"] != summaries[1]["sha256"]


def test_success_output_contains_only_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never emit text, selections, endpoints, prompts, paths, or audio bytes."""

    service = _FakeService()
    _install_fake(monkeypatch, service)

    assert smoke_gpt_sovits.main(
        ["--profile", "private-profile", "--emotion", "private-emotion"]
    ) == 0

    output = capsys.readouterr()
    document = json.loads(output.out)
    summaries = document["results"]
    assert document["readiness"] == "service_binding_unverified"
    assert all(
        set(summary) == {"format", "bytes", "duration_seconds", "sha256"}
        for summary in summaries
    )
    assert "private-profile" not in output.out
    assert "private-emotion" not in output.out
    assert "本地语音" not in output.out


def test_unavailable_status_stops_before_synthesis(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return a stable readiness reason without invoking expensive inference."""

    service = _FakeService(state="unavailable", reason="service_unreachable")
    _install_fake(monkeypatch, service)

    assert smoke_gpt_sovits.main([]) == 3

    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "service_unreachable"}
    assert service.requests == []


def test_synthesis_error_details_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Suppress private upstream context from expected synthesis failures."""

    private_details = "D:/private/model http://127.0.0.1:9880 secret prompt"
    service = _FakeService(error=SynthesisFailedError(private_details))
    _install_fake(monkeypatch, service)

    assert smoke_gpt_sovits.main([]) == 4

    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "synthesis_failed"}
    assert private_details not in output.err


def test_non_wav_result_reports_unknown_duration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Accept validated containers without inventing unavailable duration data."""

    service = _FakeService(
        [SynthesisResult(_ogg_bytes(), "ogg", 1.0)]
    )
    _install_fake(monkeypatch, service)

    assert smoke_gpt_sovits.main([]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    summary = json.loads(output.out)["results"][0]
    assert summary["format"] == "ogg"
    assert summary["duration_seconds"] is None


def test_script_path_entry_point_works_outside_repository(
    tmp_path: Path,
) -> None:
    """Support the direct CMD form without relying on the current directory."""

    script_path = Path(smoke_gpt_sovits.__file__).resolve()

    completed = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--profile" in completed.stdout
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", "INVALID"],
        ["--emotion", "neutral", "--emotion", "neutral"],
        sum((["--emotion", f"emotion-{index}"] for index in range(9)), []),
    ],
)
def test_invalid_selections_fail_before_composition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    """Bound GPU work and reject malformed identifiers before local I/O."""

    monkeypatch.setattr(
        smoke_gpt_sovits,
        "_create_service",
        lambda: pytest.fail("invalid input must not compose the service"),
    )

    assert smoke_gpt_sovits.main(arguments) == 2

    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "invalid_selection"}
