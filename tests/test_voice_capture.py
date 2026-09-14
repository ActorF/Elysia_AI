"""Test the fixed, transport-safe Voice PCM capture domain."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import FrozenInstanceError

import pytest

from voice import (
    VOICE_CAPTURE_BYTES_PER_SAMPLE,
    VOICE_CAPTURE_CHANNEL_COUNT,
    VOICE_CAPTURE_FRAME_DURATION_MS,
    VOICE_CAPTURE_FRAME_SAMPLES,
    VOICE_CAPTURE_MAX_SAMPLES,
    VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
    VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    VOICE_CAPTURE_SAMPLE_FORMAT,
    VOICE_CAPTURE_SAMPLE_RATE_HZ,
    VoiceCapture,
    VoiceCaptureValidationError,
)


def _pcm(
    sample_count: int = VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    *,
    nonzero_sample: int = 0,
) -> bytes:
    """Build frame-compatible s16le PCM with one non-silent sample."""
    payload = bytearray(sample_count * VOICE_CAPTURE_BYTES_PER_SAMPLE)
    offset = nonzero_sample * VOICE_CAPTURE_BYTES_PER_SAMPLE
    payload[offset:offset + VOICE_CAPTURE_BYTES_PER_SAMPLE] = b"\x01\x00"
    return bytes(payload)


def _capture(
    *,
    session_id: str = "voice_session-1",
    pcm_s16le: bytes | None = None,
    sample_rate_hz: int = VOICE_CAPTURE_SAMPLE_RATE_HZ,
    sample_count: int = VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    speech_start_sample: int = 0,
    speech_end_sample: int = VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
) -> VoiceCapture:
    """Build a valid baseline capture with individually overridable boundaries."""
    return VoiceCapture(
        session_id=session_id,
        pcm_s16le=_pcm(sample_count) if pcm_s16le is None else pcm_s16le,
        sample_rate_hz=sample_rate_hz,
        sample_count=sample_count,
        speech_start_sample=speech_start_sample,
        speech_end_sample=speech_end_sample,
    )


def test_fixed_capture_contract_and_minimum_speech_boundary() -> None:
    """Verify that fixed capture contract and minimum speech boundary."""
    capture = _capture()

    assert VOICE_CAPTURE_SAMPLE_RATE_HZ == 16_000
    assert VOICE_CAPTURE_CHANNEL_COUNT == 1
    assert VOICE_CAPTURE_SAMPLE_FORMAT == "s16le"
    assert VOICE_CAPTURE_FRAME_DURATION_MS == 20
    assert VOICE_CAPTURE_FRAME_SAMPLES == 320
    assert VOICE_CAPTURE_MAX_SAMPLES == 480_000
    assert VOICE_CAPTURE_MIN_SPEECH_SAMPLES == 3_200
    assert capture.duration_ms == 200
    assert capture.speech_duration_ms == 200


def test_canonical_base64_round_trips_into_immutable_pcm() -> None:
    """Verify that canonical Base64 round trips into immutable PCM."""
    pcm = _pcm()
    capture = VoiceCapture.from_base64(
        session_id="voice_base64_1",
        pcm_s16le_base64=base64.b64encode(pcm).decode("ascii"),
        sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
        sample_count=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        speech_start_sample=0,
        speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    )

    assert capture.pcm_s16le == pcm
    assert isinstance(capture.pcm_s16le, bytes)


def test_maximum_capture_and_edge_speech_window_are_accepted() -> None:
    """Verify that maximum capture and edge speech window are accepted."""
    start = VOICE_CAPTURE_MAX_SAMPLES - VOICE_CAPTURE_MIN_SPEECH_SAMPLES
    capture = _capture(
        pcm_s16le=_pcm(
            VOICE_CAPTURE_MAX_SAMPLES,
            nonzero_sample=start,
        ),
        sample_count=VOICE_CAPTURE_MAX_SAMPLES,
        speech_start_sample=start,
        speech_end_sample=VOICE_CAPTURE_MAX_SAMPLES,
    )

    assert capture.duration_ms == 30_000
    assert capture.speech_duration_ms == 200


@pytest.mark.parametrize(
    "session_id",
    [
        "voice_",
        "VOICE_session",
        "voice_has space",
        "voice_has.period",
        "voice_\nline",
        "voice_" + ("x" * (VOICE_CAPTURE_MAX_SESSION_ID_LENGTH - 5)),
        "session_without_prefix",
    ],
)
def test_invalid_session_ids_are_rejected_without_echoing_them(
    session_id: str,
) -> None:
    """Verify that invalid session IDs are rejected without echoing them."""
    with pytest.raises(VoiceCaptureValidationError) as raised:
        _capture(session_id=session_id)

    assert session_id not in str(raised.value)


def test_session_id_at_maximum_length_is_accepted() -> None:
    """Verify that session ID at maximum length is accepted."""
    session_id = "voice_" + ("x" * (VOICE_CAPTURE_MAX_SESSION_ID_LENGTH - 6))

    assert _capture(session_id=session_id).session_id == session_id


@pytest.mark.parametrize("sample_rate_hz", [0, 8_000, 48_000, True])
def test_only_the_fixed_sample_rate_is_accepted(sample_rate_hz: int) -> None:
    """Verify that only the fixed sample rate is accepted."""
    with pytest.raises(VoiceCaptureValidationError, match="sample_rate_hz"):
        _capture(sample_rate_hz=sample_rate_hz)


@pytest.mark.parametrize(
    "sample_count",
    [0, -320, 319, 321, VOICE_CAPTURE_MAX_SAMPLES + 320, True],
)
def test_sample_count_must_be_bounded_and_frame_aligned(
    sample_count: int,
) -> None:
    """Verify that sample count must be bounded and frame aligned."""
    with pytest.raises(VoiceCaptureValidationError, match="sample_count"):
        _capture(pcm_s16le=_pcm(), sample_count=sample_count)


@pytest.mark.parametrize(
    ("pcm_s16le", "sample_count"),
    [
        (b"\x01\x00" * (VOICE_CAPTURE_MIN_SPEECH_SAMPLES - 1),
         VOICE_CAPTURE_MIN_SPEECH_SAMPLES),
        (b"\x01\x00" * (VOICE_CAPTURE_MIN_SPEECH_SAMPLES + 1),
         VOICE_CAPTURE_MIN_SPEECH_SAMPLES),
        (b"\x01", VOICE_CAPTURE_MIN_SPEECH_SAMPLES),
    ],
)
def test_pcm_length_must_match_the_declared_sample_count(
    pcm_s16le: bytes,
    sample_count: int,
) -> None:
    """Verify that PCM length must match the declared sample count."""
    with pytest.raises(VoiceCaptureValidationError, match="byte length"):
        _capture(pcm_s16le=pcm_s16le, sample_count=sample_count)


def test_mutable_pcm_input_is_rejected() -> None:
    """Verify that mutable PCM input is rejected."""
    with pytest.raises(VoiceCaptureValidationError, match="immutable bytes"):
        VoiceCapture(
            session_id="voice_mutable",
            pcm_s16le=bytearray(_pcm()),  # type: ignore[arg-type]
            sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
            sample_count=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
            speech_start_sample=0,
            speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        )


@pytest.mark.parametrize(
    ("speech_start_sample", "speech_end_sample"),
    [
        (-320, VOICE_CAPTURE_MIN_SPEECH_SAMPLES),
        (0, 0),
        (320, 0),
        (0, VOICE_CAPTURE_MIN_SPEECH_SAMPLES + 320),
        (1, VOICE_CAPTURE_MIN_SPEECH_SAMPLES),
        (0, VOICE_CAPTURE_MIN_SPEECH_SAMPLES - 1),
        (False, VOICE_CAPTURE_MIN_SPEECH_SAMPLES),
    ],
)
def test_speech_markers_must_be_ordered_bounded_and_frame_aligned(
    speech_start_sample: int,
    speech_end_sample: int,
) -> None:
    """Verify that speech markers must be ordered bounded and frame aligned."""
    with pytest.raises(VoiceCaptureValidationError, match="markers"):
        _capture(
            speech_start_sample=speech_start_sample,
            speech_end_sample=speech_end_sample,
        )


def test_speech_window_shorter_than_ten_frames_is_rejected() -> None:
    """Verify that speech window shorter than ten frames is rejected."""
    with pytest.raises(VoiceCaptureValidationError, match="at least 3200"):
        _capture(
            sample_count=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
            speech_start_sample=320,
            speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        )


def test_all_zero_speech_window_is_rejected_even_if_other_pcm_is_nonzero() -> None:
    """Verify that all zero speech window is rejected even if other PCM is nonzero."""
    sample_count = VOICE_CAPTURE_MIN_SPEECH_SAMPLES + 320
    with pytest.raises(VoiceCaptureValidationError, match="non-silent"):
        _capture(
            pcm_s16le=_pcm(sample_count, nonzero_sample=0),
            sample_count=sample_count,
            speech_start_sample=320,
            speech_end_sample=sample_count,
        )


@pytest.mark.parametrize(
    "invalid_payload",
    [
        "not+base64!SECRET",
        "AA==\n",
        "_A==",
        "AQ",
        "AB==",
        "AAAA====",
        "音频",
    ],
)
def test_base64_decoder_is_strict_and_does_not_echo_payload(
    invalid_payload: str,
) -> None:
    """Verify that Base64 decoder is strict and does not echo payload."""
    with pytest.raises(VoiceCaptureValidationError) as raised:
        VoiceCapture.from_base64(
            session_id="voice_invalid_base64",
            pcm_s16le_base64=invalid_payload,
            sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
            sample_count=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
            speech_start_sample=0,
            speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        )

    assert invalid_payload not in str(raised.value)


def test_oversized_base64_is_rejected_before_domain_construction() -> None:
    """Verify that oversized Base64 is rejected before domain construction."""
    oversized_pcm = bytes(
        (VOICE_CAPTURE_MAX_SAMPLES + 1) * VOICE_CAPTURE_BYTES_PER_SAMPLE
    )
    payload = base64.b64encode(oversized_pcm).decode("ascii")

    with pytest.raises(VoiceCaptureValidationError, match="Base64") as raised:
        VoiceCapture.from_base64(
            session_id="voice_oversized",
            pcm_s16le_base64=payload,
            sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
            sample_count=VOICE_CAPTURE_MAX_SAMPLES,
            speech_start_sample=0,
            speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        )

    assert payload not in str(raised.value)


def test_decoded_base64_length_must_match_sample_count() -> None:
    """Verify that decoded Base64 length must match sample count."""
    payload = base64.b64encode(_pcm() + b"\x00\x00").decode("ascii")

    with pytest.raises(VoiceCaptureValidationError, match="byte length"):
        VoiceCapture.from_base64(
            session_id="voice_wrong_length",
            pcm_s16le_base64=payload,
            sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
            sample_count=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
            speech_start_sample=0,
            speech_end_sample=VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
        )


def test_voice_capture_is_frozen_and_hashes_exact_pcm_bytes() -> None:
    """Verify that voice capture is frozen and hashes exact PCM bytes."""
    capture = _capture()

    assert capture.sha256_hex == hashlib.sha256(capture.pcm_s16le).hexdigest()
    assert len(capture.sha256_hex) == 64
    with pytest.raises(FrozenInstanceError):
        capture.sample_count = 320  # type: ignore[misc]
