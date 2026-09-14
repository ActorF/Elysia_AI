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


def _ogg_bytes(
    *,
    include_audio: bool = True,
    audio_sequence: int = 2,
    audio_packet: bytes = b"\xf8\xff\xfe",
    tag_suffix: bytes = b"",
    identification_granule: int = 0,
    combine_tags_and_audio: bool = False,
    split_audio_after_tags: bool = False,
) -> bytes:
    """Create a checksummed Ogg Opus stream with optional encoded audio."""

    identification = (
        b"OpusHead"
        + b"\x01\x01"
        + (312).to_bytes(2, "little")
        + (48_000).to_bytes(4, "little")
        + (0).to_bytes(2, "little", signed=True)
        + b"\x00"
    )
    vendor = b"Elysia"
    comments = (
        b"OpusTags"
        + len(vendor).to_bytes(4, "little")
        + vendor
        + (0).to_bytes(4, "little")
        + tag_suffix
    )
    # The default F8 FF FE packet is conventional single-frame Opus silence. A
    # parameter lets edge tests exercise RFC-valid zero-length frame layouts.
    pages = [
        _ogg_page(
            identification,
            flags=0x02,
            sequence=0,
            granule=identification_granule,
        ),
    ]
    if include_audio and combine_tags_and_audio:
        pages.append(
            _ogg_page(
                (comments, audio_packet),
                flags=0x04,
                sequence=1,
                granule=960,
            )
        )
        return b"".join(pages)
    if include_audio and split_audio_after_tags:
        first_audio_segment = b"\x78" + (b"\x00" * 254)
        pages.extend(
            (
                _ogg_page(
                    (comments, first_audio_segment),
                    flags=0,
                    sequence=1,
                    granule=0,
                    continue_last_packet=True,
                ),
                _ogg_page(
                    b"\x00",
                    flags=0x05,
                    sequence=2,
                    granule=960,
                ),
            )
        )
        return b"".join(pages)
    pages.append(
        _ogg_page(
            comments,
            flags=0 if include_audio else 0x04,
            sequence=1,
            granule=0,
        )
    )
    if include_audio:
        pages.append(
            _ogg_page(
                audio_packet,
                flags=0x04,
                sequence=audio_sequence,
                granule=960,
            )
        )
    return b"".join(pages)


def _ogg_crc(page: bytes) -> int:
    """Return the non-reflected CRC used by Ogg pages in test fixtures."""

    checksum = 0
    for byte in page:
        checksum ^= byte << 24
        for _ in range(8):
            checksum = (
                ((checksum << 1) ^ 0x04C11DB7)
                if checksum & 0x80000000
                else checksum << 1
            ) & 0xFFFFFFFF
    return checksum


def _ogg_page(
    packet: bytes | tuple[bytes, ...],
    *,
    flags: int,
    sequence: int,
    granule: int,
    continue_last_packet: bool = False,
) -> bytes:
    """Wrap complete packets in one checksummed single-stream Ogg page."""

    lacing_values: list[int] = []
    body = bytearray()
    packets = (packet,) if isinstance(packet, bytes) else packet
    for index, complete_packet in enumerate(packets):
        remaining = len(complete_packet)
        while remaining >= 255:
            lacing_values.append(255)
            remaining -= 255
        if not (
            continue_last_packet
            and index == len(packets) - 1
            and remaining == 0
        ):
            lacing_values.append(remaining)
        body.extend(complete_packet)
    page = bytearray(b"OggS\x00")
    page.append(flags)
    page.extend(granule.to_bytes(8, "little"))
    page.extend((0x454C5953).to_bytes(4, "little"))
    page.extend(sequence.to_bytes(4, "little"))
    page.extend(b"\x00\x00\x00\x00")
    page.append(len(lacing_values))
    page.extend(lacing_values)
    page.extend(body)
    page[22:26] = _ogg_crc(bytes(page)).to_bytes(4, "little")
    return bytes(page)


def _aac_bytes(
    payload: bytes = bytes.fromhex("21 10 04 60 8C 1C"),
    *,
    protected: bool = False,
) -> bytes:
    """Create one structurally complete AAC-LC ADTS frame."""

    header_length = 9 if protected else 7
    frame_length = header_length + len(payload)
    header = bytes(
        (
            0xFF,
            0xF0 | (0 if protected else 1),
            0x50,
            0x80 | ((frame_length >> 11) & 0x03),
            (frame_length >> 3) & 0xFF,
            ((frame_length & 0x07) << 5) | 0x1F,
            0xFC,
        )
    )
    return header + (b"\x00\x00" if protected else b"") + payload


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


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "\r\n\t",
        "\x00\x1f\x7f",
        "\u200b\u200d\u2060\u202e\ufeff",
        "\u0301\u20dd\ufe0f",
        "\u115f\u1160\u2800\u3164\uffa0",
        "...——！？",
        42,
    ],
)
def test_request_rejects_text_that_cannot_be_spoken(text: object) -> None:
    """Prevent invisible or punctuation-only input from consuming inference."""

    with pytest.raises(SynthesisValidationError, match="text must"):
        SynthesisRequest(text=text)  # type: ignore[arg-type]


def test_request_accepts_visible_text_with_unicode_formatting() -> None:
    """Preserve exact mixed text when at least one speakable base character exists."""

    text = "爱莉希雅\u200d\ufe0f"

    assert SynthesisRequest(text=text).text == text


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
        (_wav_bytes()[:-1], "wav"),
        (b"OggS\x00" + (b"\x00" * 22), "ogg"),
        (_ogg_bytes()[:-1], "ogg"),
        (b"\xff\xf1\x50\x80\x01\xff\xfc", "aac"),
        (_aac_bytes()[:-1], "aac"),
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


@pytest.mark.parametrize(
    ("audio", "audio_format"),
    [
        (_wav_bytes() + b"\x00", "wav"),
        (_ogg_bytes() + b"\x00", "ogg"),
        (_aac_bytes() + b"\x00", "aac"),
    ],
)
def test_result_rejects_bytes_after_a_complete_container(
    audio: bytes,
    audio_format: str,
) -> None:
    """Reject hidden trailing data rather than validating only the first record."""

    with pytest.raises(SynthesisValidationError, match="encoded audio"):
        SynthesisResult(
            audio=audio,
            audio_format=audio_format,  # type: ignore[arg-type]
            speed_factor=1.0,
        )


def test_result_rejects_inconsistent_or_non_pcm_wav() -> None:
    """Require a complete PCM fmt/data relationship and whole sample frames."""

    valid = _wav_bytes()
    data_offset = valid.index(b"data")

    empty_data = _wav_bytes(0)
    misaligned = bytearray(valid[:-1])
    misaligned[4:8] = (len(misaligned) - 8).to_bytes(4, "little")
    misaligned[data_offset + 4 : data_offset + 8] = (1).to_bytes(4, "little")
    invalid_byte_rate = bytearray(valid)
    invalid_byte_rate[28:32] = (0).to_bytes(4, "little")
    non_pcm = bytearray(valid)
    non_pcm[20:22] = (3).to_bytes(2, "little")
    incomplete_extended_format = bytearray(valid)
    incomplete_extended_format[16:20] = (17).to_bytes(4, "little")
    incomplete_extended_format[4:8] = (
        len(incomplete_extended_format) + 2 - 8
    ).to_bytes(4, "little")
    incomplete_extended_format[36:36] = b"\x00\x00"

    for audio in (
        empty_data,
        bytes(misaligned),
        bytes(invalid_byte_rate),
        bytes(non_pcm),
        bytes(incomplete_extended_format),
    ):
        with pytest.raises(SynthesisValidationError, match="encoded audio"):
            SynthesisResult(audio=audio, audio_format="wav", speed_factor=1.0)


def test_result_accepts_pcm_waveformatex_with_empty_extension() -> None:
    """Accept the complete 18-byte PCM form while rejecting partial cbSize."""

    audio = bytearray(_wav_bytes())
    audio[16:20] = (18).to_bytes(4, "little")
    audio[4:8] = (len(audio) + 2 - 8).to_bytes(4, "little")
    audio[36:36] = b"\x00\x00"

    assert SynthesisResult(bytes(audio), "wav", 1.0).audio == bytes(audio)


def test_result_rejects_corrupt_or_incomplete_ogg_opus() -> None:
    """Require checksums, ordered pages, Opus headers, and an audio packet."""

    corrupt_crc = bytearray(_ogg_bytes())
    corrupt_crc[-1] ^= 0x01
    unsupported_codec = _ogg_page(
        b"not-an-opus-header",
        flags=0x06,
        sequence=0,
        granule=0,
    )
    family_one_header = (
        b"OpusHead"
        + b"\x01\x02"
        + (312).to_bytes(2, "little")
        + (48_000).to_bytes(4, "little")
        + (0).to_bytes(2, "little", signed=True)
        + b"\x01\x01\x01\x00\x01"
    )
    default_stream = _ogg_bytes()
    tail_offset = default_stream.find(b"OggS", 4)
    assert tail_offset > 0
    unsupported_multistream = (
        _ogg_page(family_one_header, flags=0x02, sequence=0, granule=0)
        + default_stream[tail_offset:]
    )

    for audio in (
        bytes(corrupt_crc),
        _ogg_bytes(include_audio=False),
        _ogg_bytes(audio_sequence=3),
        _ogg_bytes(identification_granule=1),
        _ogg_bytes(combine_tags_and_audio=True),
        _ogg_bytes(split_audio_after_tags=True),
        _ogg_bytes(audio_packet=b"\x03"),
        unsupported_codec,
        unsupported_multistream,
    ):
        with pytest.raises(SynthesisValidationError, match="encoded audio"):
            SynthesisResult(audio=audio, audio_format="ogg", speed_factor=1.0)


@pytest.mark.parametrize("audio_packet", [b"\x00", b"\x02\x00"])
def test_result_accepts_opus_zero_length_frames(audio_packet: bytes) -> None:
    """Preserve RFC 6716 DTX/loss packets whose frames contain zero bytes."""

    audio = _ogg_bytes(audio_packet=audio_packet)

    assert SynthesisResult(audio, "ogg", 1.0).audio == audio


def test_result_accepts_opus_tags_trailing_metadata() -> None:
    """Permit RFC-defined opaque padding after the parsed Opus comment list."""

    audio = _ogg_bytes(tag_suffix=b"\x00\xffopaque")

    assert SynthesisResult(audio, "ogg", 1.0).audio == audio


def test_result_accepts_multiple_consistent_adts_frames() -> None:
    """Accept a fully walked AAC stream instead of stopping at its first frame."""

    audio = _aac_bytes() * 2

    assert SynthesisResult(audio, "aac", 1.0).audio == audio


def test_result_rejects_malformed_or_inconsistent_adts_frames() -> None:
    """Reject header-only, unchecked, reserved, and mixed AAC frame sequences."""

    header_only = _aac_bytes(b"")
    protected = _aac_bytes(protected=True)
    reserved_sample_rate = bytearray(_aac_bytes())
    reserved_sample_rate[2] = (reserved_sample_rate[2] & 0xC3) | (0x0F << 2)
    no_channel_configuration = bytearray(_aac_bytes())
    no_channel_configuration[3] &= 0x3F
    different_sample_rate = bytearray(_aac_bytes())
    different_sample_rate[2] = (
        (different_sample_rate[2] & 0xC3) | (0x03 << 2)
    )

    for audio in (
        header_only,
        protected,
        bytes(reserved_sample_rate),
        bytes(no_channel_configuration),
        _aac_bytes() + bytes(different_sample_rate),
    ):
        with pytest.raises(SynthesisValidationError, match="encoded audio"):
            SynthesisResult(audio=audio, audio_format="aac", speed_factor=1.0)


def test_result_accepts_zero_filled_adts_payload_as_transport_framing() -> None:
    """Leave AAC codec decodability to playback instead of guessing from bytes."""

    audio = _aac_bytes(b"\x00" * 6)

    assert SynthesisResult(audio, "aac", 1.0).audio == audio


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
