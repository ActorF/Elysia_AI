"""Test the strict, engine-independent local speech synthesis contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from io import BytesIO
import wave

import pytest

from voice import (
    SYNTHESIS_MAX_AUDIO_BYTES,
    SYNTHESIS_MAX_IDENTIFIER_LENGTH,
    SYNTHESIS_MAX_TEXT_CODE_POINTS,
    SpeechSynthesizer,
    SynthesisError,
    SynthesisFailedError,
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
    SynthesisValidationError,
)


def _wav_bytes(sample_count: int = 1) -> bytes:
    """Create a minimal PCM WAVE payload with playable sample data."""

    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\x00\x00" * sample_count)
    return buffer.getvalue()


def _ogg_bytes() -> bytes:
    """Create one structurally complete minimal Ogg page for validation tests."""

    return b"OggS\x00" + (b"\x00" * 21) + b"\x01\x01\x00"


def _aac_bytes() -> bytes:
    """Create one complete seven-byte ADTS AAC frame for validation tests."""

    return bytes((0xFF, 0xF1, 0x50, 0x80, 0x00, 0xFF, 0xFC))


class _StubSynthesizer:
    """Record requests and return deterministic encoded speech."""

    def __init__(self, result: SynthesisResult) -> None:
        self.result = result
        self.requests: list[SynthesisRequest] = []

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Record the request before returning the configured result."""

        self.requests.append(request)
        return self.result


def _run_synthesizer(
    synthesizer: SpeechSynthesizer,
    request: SynthesisRequest,
) -> SynthesisResult:
    """Exercise structural SpeechSynthesizer compatibility under mypy."""

    return synthesizer.synthesize(request)


def test_request_preserves_text_and_defaults_to_logical_voice_selection() -> None:
    """Keep callers independent from model paths and upstream API fields."""

    request = SynthesisRequest(text=" 你好，世界。 ")

    assert request.text == " 你好，世界。 "
    assert request.language == "auto"
    assert request.profile_id == "default"
    assert request.emotion == "neutral"


@pytest.mark.parametrize("language", ["auto", "zh", "en"])
def test_request_accepts_supported_language_hints(language: str) -> None:
    """Keep automatic, Chinese, and English as the complete language vocabulary."""

    request = SynthesisRequest(
        text="hello",
        language=language,  # type: ignore[arg-type]
    )

    assert request.language == language


@pytest.mark.parametrize("text", ["", " ", "\r\n\t", "\ufeff", 42])
def test_request_rejects_text_that_cannot_be_spoken(text: object) -> None:
    """Prevent invisible or non-string values from consuming local inference."""

    with pytest.raises(SynthesisValidationError, match="text must"):
        SynthesisRequest(text=text)  # type: ignore[arg-type]


def test_request_accepts_text_at_the_code_point_limit() -> None:
    """Count Unicode code points without changing the caller's exact text."""

    text = "🌸" * SYNTHESIS_MAX_TEXT_CODE_POINTS

    assert SynthesisRequest(text=text).text == text


def test_request_rejects_text_above_the_code_point_limit() -> None:
    """Bound inference work before an adapter allocates a request body."""

    with pytest.raises(SynthesisValidationError, match="code-point limit"):
        SynthesisRequest(text="a" * (SYNTHESIS_MAX_TEXT_CODE_POINTS + 1))


@pytest.mark.parametrize("language", ["", "fr", None, False])
def test_request_rejects_unknown_language(language: object) -> None:
    """Reject engine-specific or malformed language labels at the boundary."""

    with pytest.raises(SynthesisValidationError, match="auto, zh, or en"):
        SynthesisRequest(
            text="hello",
            language=language,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("profile_id", ""),
        ("profile_id", "../outside"),
        ("profile_id", "Uppercase"),
        ("profile_id", "x" * (SYNTHESIS_MAX_IDENTIFIER_LENGTH + 1)),
        ("emotion", "happy/../../outside"),
        ("emotion", None),
    ],
)
def test_request_rejects_unsafe_profile_and_emotion_identifiers(
    field_name: str,
    value: object,
) -> None:
    """Keep logical identifiers from becoming traversal or unbounded path input."""

    values: dict[str, object] = {
        "text": "hello",
        "profile_id": "default",
        "emotion": "neutral",
    }
    values[field_name] = value

    with pytest.raises(SynthesisValidationError, match=field_name):
        SynthesisRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("audio", "audio_format", "media_type"),
    [
        (_wav_bytes(), "wav", "audio/wav"),
        (_ogg_bytes(), "ogg", "audio/ogg"),
        (_aac_bytes(), "aac", "audio/aac"),
    ],
)
def test_result_accepts_supported_non_empty_audio_containers(
    audio: bytes,
    audio_format: str,
    media_type: str,
) -> None:
    """Expose only bounded encoded audio that has a playable container shape."""

    result = SynthesisResult(
        audio=audio,
        audio_format=audio_format,  # type: ignore[arg-type]
        speed_factor=1.0,
    )

    assert result.audio == audio
    assert result.media_type == media_type


@pytest.mark.parametrize(
    ("audio", "audio_format"),
    [
        (b"", "wav"),
        (b"RIFF\x04\x00\x00\x00WAVE", "wav"),
        (b"not-wave-data", "wav"),
        (b"OggS\x00" + (b"\x00" * 22), "ogg"),
        (b"\xff\xf1\x50\x80\x01\xff\xfc", "aac"),
    ],
)
def test_result_rejects_empty_or_truncated_audio(
    audio: bytes,
    audio_format: str,
) -> None:
    """Reject magic-only and truncated responses before later playback."""

    with pytest.raises(SynthesisValidationError, match="audio|encoded audio"):
        SynthesisResult(
            audio=audio,
            audio_format=audio_format,  # type: ignore[arg-type]
            speed_factor=1.0,
        )


def test_result_rejects_audio_above_the_memory_limit() -> None:
    """Bound a local service response before it can exhaust backend memory."""

    oversized = _wav_bytes((SYNTHESIS_MAX_AUDIO_BYTES // 2) + 1)

    with pytest.raises(SynthesisValidationError, match="byte limit"):
        SynthesisResult(
            audio=oversized,
            audio_format="wav",
            speed_factor=1.0,
        )


@pytest.mark.parametrize("audio_format", ["raw", "mp3", "", None, True])
def test_result_rejects_unsupported_audio_formats(audio_format: object) -> None:
    """Keep later playback code on the three explicitly supported containers."""

    with pytest.raises(SynthesisValidationError, match="audio_format"):
        SynthesisResult(
            audio=_wav_bytes(),
            audio_format=audio_format,  # type: ignore[arg-type]
            speed_factor=1.0,
        )


@pytest.mark.parametrize(
    "speed_factor",
    [0.5, 1.0, 2.0],
)
def test_result_accepts_speed_factor_boundaries(speed_factor: float) -> None:
    """Preserve the effective configured speed for downstream diagnostics."""

    result = SynthesisResult(
        audio=_wav_bytes(),
        audio_format="wav",
        speed_factor=speed_factor,
    )

    assert result.speed_factor == speed_factor


@pytest.mark.parametrize(
    "speed_factor",
    [0.49, 2.01, float("nan"), float("inf"), True, "1.0", None],
)
def test_result_rejects_invalid_speed_factors(speed_factor: object) -> None:
    """Reject unsafe, non-finite, and Boolean speed metadata."""

    with pytest.raises(SynthesisValidationError, match="speed_factor"):
        SynthesisResult(
            audio=_wav_bytes(),
            audio_format="wav",
            speed_factor=speed_factor,  # type: ignore[arg-type]
        )


def test_request_and_result_are_immutable() -> None:
    """Keep queued work and returned media stable across worker boundaries."""

    request = SynthesisRequest(text="hello")
    result = SynthesisResult(
        audio=_wav_bytes(),
        audio_format="wav",
        speed_factor=1.0,
    )

    with pytest.raises(FrozenInstanceError):
        request.text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.audio = b"changed"  # type: ignore[misc]


def test_plain_class_structurally_satisfies_synthesizer_protocol() -> None:
    """Allow deterministic test fakes without inheriting from an engine base."""

    request = SynthesisRequest(
        text="你好",
        language="zh",
        profile_id="elysia",
        emotion="happy",
    )
    expected = SynthesisResult(
        audio=_wav_bytes(),
        audio_format="wav",
        speed_factor=1.0,
    )
    synthesizer = _StubSynthesizer(expected)

    assert _run_synthesizer(synthesizer, request) is expected
    assert synthesizer.requests == [request]


@pytest.mark.parametrize(
    "error_type",
    [
        SynthesisValidationError,
        SynthesisUnavailableError,
        SynthesisFailedError,
    ],
)
def test_specific_failures_share_one_stable_base_error(
    error_type: type[SynthesisError],
) -> None:
    """Let future orchestration catch all synthesis failures at one boundary."""

    error = error_type("safe public explanation")

    assert isinstance(error, SynthesisError)
    assert str(error) == "safe public explanation"
