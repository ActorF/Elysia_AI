"""Test the strict, engine-independent local transcription contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from voice import (
    TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
    VOICE_CAPTURE_BYTES_PER_SAMPLE,
    VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    VOICE_CAPTURE_SAMPLE_RATE_HZ,
    TranscriptLanguage,
    Transcriber,
    TranscriptionError,
    TranscriptionFailedError,
    TranscriptionLanguage,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionUnavailableError,
    TranscriptionValidationError,
    VoiceCapture,
)


def _capture() -> VoiceCapture:
    """Build the smallest valid capture accepted by the transcription boundary."""

    pcm = bytearray(
        VOICE_CAPTURE_MIN_SPEECH_SAMPLES * VOICE_CAPTURE_BYTES_PER_SAMPLE
    )
    pcm[:VOICE_CAPTURE_BYTES_PER_SAMPLE] = b"\x01\x00"
    return VoiceCapture(
        session_id="voice_transcription-test",
        pcm_s16le=bytes(pcm),
        sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
        sample_count=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        speech_start_sample=0,
        speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    )


class _StubTranscriber:
    """Record a request and return one deterministic final transcript."""

    def __init__(self, result: TranscriptionResult) -> None:
        self.result = result
        self.requests: list[TranscriptionRequest] = []

    def transcribe(
        self,
        request: TranscriptionRequest,
    ) -> TranscriptionResult:
        """Record the exact request before returning the configured result."""

        self.requests.append(request)
        return self.result


def _run_transcriber(
    transcriber: Transcriber,
    request: TranscriptionRequest,
) -> TranscriptionResult:
    """Exercise structural Transcriber compatibility under mypy."""

    return transcriber.transcribe(request)


@pytest.mark.parametrize("language", ["auto", "zh", "en"])
def test_request_accepts_only_supported_language_hints(
    language: TranscriptionLanguage,
) -> None:
    """Keep automatic, Chinese, and English as the complete request vocabulary."""

    capture = _capture()
    request = TranscriptionRequest(capture=capture, language=language)

    assert request.capture is capture
    assert request.language == language


def test_request_defaults_to_automatic_language_detection() -> None:
    """Use automatic detection when a caller does not provide a language hint."""

    assert TranscriptionRequest(capture=_capture()).language == "auto"


@pytest.mark.parametrize("language", ["", "fr", None, True])
def test_request_rejects_unknown_or_non_string_language_hints(
    language: object,
) -> None:
    """Reject values that would otherwise leak adapter-specific language rules."""

    with pytest.raises(TranscriptionValidationError, match="auto, zh, or en"):
        TranscriptionRequest(
            capture=_capture(),
            language=language,  # type: ignore[arg-type]
        )


def test_request_requires_an_already_validated_voice_capture() -> None:
    """Prevent raw or browser-specific audio objects from crossing the boundary."""

    with pytest.raises(TranscriptionValidationError, match="VoiceCapture"):
        TranscriptionRequest(
            capture=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("language", ["zh", "en"])
def test_result_preserves_final_text_and_resolved_language(
    language: TranscriptLanguage,
) -> None:
    """Preserve editable engine text while returning a concrete language."""

    result = TranscriptionResult(
        text=" 你好，世界。 ",
        language=language,
        language_probability=0.875,
    )

    assert result.text == " 你好，世界。 "
    assert result.language == language
    assert result.language_probability == 0.875


@pytest.mark.parametrize(
    "text",
    ["", " ", "\t\r\n", "\ufeff", "\ufeff \n", 42],
)
def test_result_rejects_text_that_cannot_create_a_chat_message(
    text: object,
) -> None:
    """Block empty transcripts, including the protocol's BOM-only edge case."""

    with pytest.raises(
        TranscriptionValidationError,
        match="Transcription result text must",
    ):
        TranscriptionResult(
            text=text,  # type: ignore[arg-type]
            language="zh",
            language_probability=0.5,
        )


def test_result_accepts_text_at_the_code_point_limit_without_normalizing_it(
) -> None:
    """Count Unicode code points while preserving exact boundary-length text."""

    text = "🌸" * TRANSCRIPTION_MAX_TEXT_CODE_POINTS

    result = TranscriptionResult(
        text=text,
        language="zh",
        language_probability=1.0,
    )

    assert result.text == text
    assert len(result.text) == TRANSCRIPTION_MAX_TEXT_CODE_POINTS


def test_result_rejects_text_above_the_code_point_limit() -> None:
    """Bound output from a corrupt adapter before it reaches later protocols."""

    text = "a" * (TRANSCRIPTION_MAX_TEXT_CODE_POINTS + 1)

    with pytest.raises(TranscriptionValidationError, match="code-point limit"):
        TranscriptionResult(
            text=text,
            language="en",
            language_probability=0.75,
        )


@pytest.mark.parametrize("language", ["auto", "", "fr", None, False])
def test_result_requires_a_resolved_supported_language(
    language: object,
) -> None:
    """Do not let automatic or engine-specific language labels escape as final."""

    with pytest.raises(TranscriptionValidationError, match="must be zh or en"):
        TranscriptionResult(
            text="hello",
            language=language,  # type: ignore[arg-type]
            language_probability=0.5,
        )


@pytest.mark.parametrize("probability", [0.0, 0.5, 1.0])
def test_result_accepts_finite_language_probability_boundaries(
    probability: float,
) -> None:
    """Accept finite language confidence across the full closed interval."""

    result = TranscriptionResult(
        text="hello",
        language="en",
        language_probability=probability,
    )

    assert result.language_probability == probability


@pytest.mark.parametrize(
    "probability",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        1.01,
        True,
        "0.5",
        None,
    ],
)
def test_result_rejects_invalid_language_probabilities(
    probability: object,
) -> None:
    """Reject non-finite, out-of-range, Boolean, and non-numeric confidence."""

    with pytest.raises(
        TranscriptionValidationError,
        match="language_probability",
    ):
        TranscriptionResult(
            text="hello",
            language="en",
            language_probability=probability,  # type: ignore[arg-type]
        )


def test_request_and_result_are_immutable() -> None:
    """Keep queued work and returned transcript metadata stable across threads."""

    request = TranscriptionRequest(capture=_capture())
    result = TranscriptionResult(
        text="hello",
        language="en",
        language_probability=0.9,
    )

    with pytest.raises(FrozenInstanceError):
        request.language = "en"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.text = "changed"  # type: ignore[misc]


def test_plain_class_structurally_satisfies_transcriber_protocol() -> None:
    """Allow deterministic fakes without inheriting from a concrete engine base."""

    request = TranscriptionRequest(capture=_capture(), language="en")
    expected = TranscriptionResult(
        text="hello",
        language="en",
        language_probability=0.9,
    )
    transcriber = _StubTranscriber(expected)

    assert _run_transcriber(transcriber, request) is expected
    assert transcriber.requests == [request]


@pytest.mark.parametrize(
    "error_type",
    [
        TranscriptionValidationError,
        TranscriptionUnavailableError,
        TranscriptionFailedError,
    ],
)
def test_specific_failures_share_one_stable_base_error(
    error_type: type[TranscriptionError],
) -> None:
    """Let future orchestration catch all transcription failures at one boundary."""

    error = error_type("safe public explanation")

    assert isinstance(error, TranscriptionError)
    assert str(error) == "safe public explanation"
