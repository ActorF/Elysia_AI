"""Define the engine-independent contract for local speech synthesis."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Final, Literal, Protocol, TypeAlias
import unicodedata
import wave


SynthesisLanguage: TypeAlias = Literal["auto", "zh", "en"]
SynthesisAudioFormat: TypeAlias = Literal["wav", "ogg", "aac"]

SYNTHESIS_MAX_TEXT_CODE_POINTS: Final = 4_096
SYNTHESIS_MAX_AUDIO_BYTES: Final = 32 * 1024 * 1024
SYNTHESIS_MAX_IDENTIFIER_LENGTH: Final = 64
SYNTHESIS_MIN_SPEED_FACTOR: Final = 0.5
SYNTHESIS_MAX_SPEED_FACTOR: Final = 2.0

_LANGUAGES: Final = ("auto", "zh", "en")
_AUDIO_FORMATS: Final = ("wav", "ogg", "aac")
_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_INVISIBLE_FILLERS: Final = frozenset(
    {
        "\u115f",  # Hangul choseong filler
        "\u1160",  # Hangul jungseong filler
        "\u2800",  # Braille pattern blank
        "\u3164",  # Hangul filler
        "\uffa0",  # Halfwidth Hangul filler
    }
)
_MEDIA_TYPES: Final = {
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
}
_MAX_CONTAINER_RECORDS: Final = 65_536
_MAX_OGG_PACKET_BYTES: Final = 64 * 1024
_OGG_CRC_POLYNOMIAL: Final = 0x04C11DB7


class SynthesisError(Exception):
    """Base class for stable failures exposed by a SpeechSynthesizer."""


class SynthesisValidationError(SynthesisError):
    """Report an invalid synthesis request or adapter result."""


class SynthesisUnavailableError(SynthesisError):
    """Report that the configured local synthesis service cannot be reached."""


class SynthesisFailedError(SynthesisError):
    """Report failure while an available engine synthesizes valid text."""


def _contains_spoken_text(value: str) -> bool:
    """Return whether text contains a character worth synthesizing."""

    # Unicode whitespace predicates omit many format controls, combining-only
    # sequences, and several printable filler glyphs. Punctuation-only chunks
    # also represent pauses rather than an utterance. None should consume an
    # expensive local inference slot without a letter, number, or symbol.
    for character in value:
        category = unicodedata.category(character)
        if (
            category[0] in {"L", "N", "S"}
            and character not in _INVISIBLE_FILLERS
            and character.isprintable()
        ):
            return True
    return False


def _is_safe_identifier(value: object) -> bool:
    """Return whether a profile or emotion identifier is bounded ASCII."""

    return (
        isinstance(value, str)
        and len(value) <= SYNTHESIS_MAX_IDENTIFIER_LENGTH
        and _IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _has_complete_pcm_wav(audio: bytes) -> bool:
    """Validate one complete PCM RIFF/WAVE stream with readable sample data."""

    if (
        len(audio) < 12
        or audio[:4] != b"RIFF"
        or audio[8:12] != b"WAVE"
    ):
        return False
    declared_size = int.from_bytes(audio[4:8], "little") + 8
    if declared_size != len(audio):
        return False

    # RIFF chunks may contain metadata and use one padding byte after odd-sized
    # payloads. A complete walk rejects valid-looking prefixes followed by a
    # truncated later chunk, which wave.open() alone does not always consume.
    offset = 12
    chunk_count = 0
    format_fields: tuple[int, int, int, int, int, int] | None = None
    data_size: int | None = None
    while offset < declared_size:
        chunk_count += 1
        if chunk_count > _MAX_CONTAINER_RECORDS or offset + 8 > declared_size:
            return False
        chunk_id = audio[offset : offset + 4]
        chunk_size = int.from_bytes(audio[offset + 4 : offset + 8], "little")
        chunk_end = offset + 8 + chunk_size
        if chunk_end > declared_size:
            return False
        padded_end = chunk_end + (chunk_size % 2)
        if padded_end > declared_size:
            return False

        if chunk_id == b"fmt ":
            if format_fields is not None:
                return False
            # Accept canonical PCM WAVEFORMAT or WAVEFORMATEX with an explicit
            # empty extension. A 17-byte fmt chunk cannot contain the complete
            # cbSize field; extensible/compressed formats need decoder-level
            # validation and therefore remain outside this transport contract.
            if chunk_size == 16:
                pass
            elif (
                chunk_size == 18
                and int.from_bytes(audio[offset + 24 : offset + 26], "little") == 0
            ):
                pass
            else:
                return False
            payload = offset + 8
            format_fields = (
                int.from_bytes(audio[payload : payload + 2], "little"),
                int.from_bytes(audio[payload + 2 : payload + 4], "little"),
                int.from_bytes(audio[payload + 4 : payload + 8], "little"),
                int.from_bytes(audio[payload + 8 : payload + 12], "little"),
                int.from_bytes(audio[payload + 12 : payload + 14], "little"),
                int.from_bytes(audio[payload + 14 : payload + 16], "little"),
            )
        elif chunk_id == b"data":
            if format_fields is None or data_size is not None or chunk_size == 0:
                return False
            data_size = chunk_size
        offset = padded_end

    if format_fields is None or data_size is None:
        return False
    (
        encoding,
        channel_count,
        sample_rate,
        byte_rate,
        block_alignment,
        bits_per_sample,
    ) = format_fields
    if (
        encoding != 1
        or channel_count == 0
        or sample_rate == 0
        or bits_per_sample not in {8, 16, 24, 32}
    ):
        return False
    expected_alignment = channel_count * (bits_per_sample // 8)
    if (
        block_alignment != expected_alignment
        or byte_rate != sample_rate * expected_alignment
        or data_size % expected_alignment != 0
    ):
        return False

    # The stdlib decoder verifies the PCM fmt contract while the explicit RIFF
    # walk above proves every declared chunk and padding byte is present.
    try:
        with wave.open(BytesIO(audio), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            if (
                wav_file.getcomptype() != "NONE"
                or wav_file.getnchannels() != channel_count
                or wav_file.getframerate() != sample_rate
                or wav_file.getsampwidth() * 8 != bits_per_sample
                or frame_count <= 0
                or frame_count * expected_alignment != data_size
            ):
                return False
            if len(wav_file.readframes(frame_count)) != data_size:
                return False
            return wav_file.readframes(1) == b""
    except (EOFError, wave.Error):
        return False


def _build_ogg_crc_table() -> tuple[int, ...]:
    """Build the Ogg-specific, non-reflected CRC lookup table once."""

    table: list[int] = []
    for value in range(256):
        remainder = value << 24
        for _ in range(8):
            remainder = (
                ((remainder << 1) ^ _OGG_CRC_POLYNOMIAL)
                if remainder & 0x80000000
                else remainder << 1
            ) & 0xFFFFFFFF
        table.append(remainder)
    return tuple(table)


_OGG_CRC_TABLE: Final = _build_ogg_crc_table()


def _ogg_page_crc(audio: bytes, start: int, end: int) -> int:
    """Compute one page CRC while treating its stored checksum as zero."""

    checksum = 0
    for index in range(start, end):
        byte = 0 if start + 22 <= index < start + 26 else audio[index]
        checksum = (
            ((checksum << 8) & 0xFFFFFFFF)
            ^ _OGG_CRC_TABLE[((checksum >> 24) & 0xFF) ^ byte]
        )
    return checksum


def _has_valid_opus_head(packet: bytes) -> bool:
    """Validate the common mono/stereo Opus identification packet.

    Mapping family 1 may multiplex several independently framed Opus streams
    inside one Ogg packet. The one-shot TTS contract does not need multistream
    audio, so accepting only the exact version-1 family-0 header avoids claiming
    that a single-stream framing check validated those additional substreams.
    """

    return (
        len(packet) == 19
        and packet[:8] == b"OpusHead"
        and packet[8] == 1
        and 1 <= packet[9] <= 2
        and packet[18] == 0
    )


def _has_valid_opus_tags(packet: bytes) -> bool:
    """Validate all length-prefixed UTF-8 fields in an OpusTags packet."""

    if len(packet) < 16 or packet[:8] != b"OpusTags":
        return False
    offset = 8
    vendor_length = int.from_bytes(packet[offset : offset + 4], "little")
    offset += 4
    vendor_end = offset + vendor_length
    if vendor_end + 4 > len(packet):
        return False
    try:
        packet[offset:vendor_end].decode("utf-8")
    except UnicodeDecodeError:
        return False
    offset = vendor_end
    comment_count = int.from_bytes(packet[offset : offset + 4], "little")
    offset += 4
    if comment_count > (len(packet) - offset) // 4:
        return False
    for _ in range(comment_count):
        comment_length = int.from_bytes(packet[offset : offset + 4], "little")
        offset += 4
        comment_end = offset + comment_length
        if comment_end > len(packet):
            return False
        try:
            packet[offset:comment_end].decode("utf-8")
        except UnicodeDecodeError:
            return False
        offset = comment_end
    # RFC 7845 permits padding or other binary data after the comment list.
    # The length-prefixed fields above are the security boundary; the remaining
    # bytes are opaque metadata and stay inside the global response-size limit.
    return offset <= len(packet)


def _opus_frame_duration_quarters(toc: int) -> int:
    """Return one Opus frame duration in quarter-millisecond units."""

    configuration = toc >> 3
    if configuration < 12:
        return (40, 80, 160, 240)[configuration % 4]
    if configuration < 16:
        return (40, 80)[configuration % 2]
    return (10, 20, 40, 80)[configuration % 4]


def _read_opus_frame_length(packet: bytes, offset: int) -> tuple[int, int] | None:
    """Read one RFC 6716 compact frame length and its following offset."""

    if offset >= len(packet):
        return None
    first = packet[offset]
    if first < 252:
        return first, offset + 1
    if offset + 1 >= len(packet):
        return None
    return first + (packet[offset + 1] * 4), offset + 2


def _has_valid_opus_audio_packet(packet: bytes) -> bool:
    """Validate all four Opus packet-framing layouts without decoding samples.

    The TOC's frame code selects one frame, two equal frames, two variable
    frames, or a code-3 count/padding table. Each branch proves that its length
    fields fit the packet and that the aggregate duration stays within Opus's
    120 ms limit. RFC 6716 permits zero-length frames for DTX/loss signalling,
    so structural validity must not depend on payload bytes being non-zero.
    """

    if not packet:
        return False
    frame_code = packet[0] & 0x03
    duration = _opus_frame_duration_quarters(packet[0])
    if frame_code == 0:
        return len(packet) - 1 <= 1_275
    if frame_code == 1:
        payload_size = len(packet) - 1
        return (
            duration * 2 <= 480
            and payload_size % 2 == 0
            and payload_size // 2 <= 1_275
        )
    if frame_code == 2:
        parsed = _read_opus_frame_length(packet, 1)
        if parsed is None:
            return False
        first_size, payload_offset = parsed
        second_size = len(packet) - payload_offset - first_size
        return (
            duration * 2 <= 480
            and 0 <= first_size <= 1_275
            and 0 <= second_size <= 1_275
        )

    if len(packet) < 2:
        return False
    control = packet[1]
    variable_bitrate = bool(control & 0x80)
    has_padding = bool(control & 0x40)
    frame_count = control & 0x3F
    if frame_count == 0 or frame_count > 48 or duration * frame_count > 480:
        return False
    offset = 2
    padding_size = 0
    if has_padding:
        while True:
            if offset >= len(packet):
                return False
            value = packet[offset]
            offset += 1
            padding_size += 254 if value == 255 else value
            if value != 255:
                break
    payload_end = len(packet) - padding_size
    if payload_end < offset:
        return False
    if not variable_bitrate:
        payload_size = payload_end - offset
        return (
            payload_size % frame_count == 0
            and payload_size // frame_count <= 1_275
        )

    declared_total = 0
    for _ in range(frame_count - 1):
        parsed = _read_opus_frame_length(packet, offset)
        if parsed is None:
            return False
        frame_size, offset = parsed
        if not 0 <= frame_size <= 1_275:
            return False
        declared_total += frame_size
    final_size = payload_end - offset - declared_total
    return 0 <= final_size <= 1_275


def _has_complete_ogg_opus(audio: bytes) -> bool:
    """Walk and validate one complete, single-stream Ogg Opus container.

    Python's standard library has no generic Ogg codec validator. Restricting
    accepted Ogg output to checksummed Opus with verified headers and packet
    framing is safer than accepting an arbitrary Ogg magic prefix as playable.
    """

    offset = 0
    page_count = 0
    serial_number: int | None = None
    expected_sequence = 0
    packet_index = 0
    current_packet = bytearray()
    packet_continues = False
    saw_end = False

    while offset < len(audio):
        page_count += 1
        if page_count > _MAX_CONTAINER_RECORDS or offset + 27 > len(audio):
            return False
        if audio[offset : offset + 4] != b"OggS" or audio[offset + 4] != 0:
            return False
        flags = audio[offset + 5]
        if flags & ~0x07:
            return False
        continued = bool(flags & 0x01)
        beginning = bool(flags & 0x02)
        ending = bool(flags & 0x04)
        if page_count == 1:
            if not beginning or continued:
                return False
        elif beginning or continued != packet_continues:
            return False

        current_serial = int.from_bytes(audio[offset + 14 : offset + 18], "little")
        sequence = int.from_bytes(audio[offset + 18 : offset + 22], "little")
        if serial_number is None:
            serial_number = current_serial
        if current_serial != serial_number or sequence != expected_sequence:
            return False
        expected_sequence = (expected_sequence + 1) & 0xFFFFFFFF

        segment_count = audio[offset + 26]
        table_end = offset + 27 + segment_count
        if segment_count == 0 or table_end > len(audio):
            return False
        lacing_values = audio[offset + 27 : table_end]
        body_size = sum(lacing_values)
        page_end = table_end + body_size
        if page_end > len(audio):
            return False
        stored_crc = int.from_bytes(audio[offset + 22 : offset + 26], "little")
        if _ogg_page_crc(audio, offset, page_end) != stored_crc:
            return False

        page_packet_start = packet_index
        granule_position = int.from_bytes(
            audio[offset + 6 : offset + 14],
            "little",
        )
        body_offset = table_end
        for segment_size in lacing_values:
            segment_end = body_offset + segment_size
            if len(current_packet) + segment_size > _MAX_OGG_PACKET_BYTES:
                return False
            current_packet.extend(audio[body_offset:segment_end])
            body_offset = segment_end
            if segment_size < 255:
                packet = bytes(current_packet)
                if packet_index == 0:
                    if not _has_valid_opus_head(packet):
                        return False
                elif packet_index == 1:
                    if not _has_valid_opus_tags(packet):
                        return False
                elif not _has_valid_opus_audio_packet(packet):
                    return False
                packet_index += 1
                current_packet.clear()

        packet_continues = lacing_values[-1] == 255
        if page_count == 1 and (packet_index != 1 or packet_continues):
            return False
        # OpusHead must occupy the first page by itself, and the page that
        # finishes OpusTags may not also carry audio. Header pages use granule
        # position zero because no decoded samples exist yet.
        if page_packet_start < 2 and (
            granule_position != 0
            or packet_index > 2
            or (packet_index == 2 and bool(current_packet))
        ):
            return False
        if ending:
            if page_end != len(audio) or packet_continues:
                return False
            saw_end = True
        elif page_end == len(audio):
            return False
        offset = page_end

    return saw_end and packet_index >= 3 and not current_packet


def _has_complete_adts_aac(audio: bytes) -> bool:
    """Walk a safe, consistently configured subset of ADTS AAC framing.

    This transport check accepts the ordinary GPT-SoVITS/FFmpeg shape: an
    unprotected seven-byte header, an explicit channel configuration, and one
    raw-data block per frame. CRC-protected, PCE-configured, or multi-block
    streams require deeper parsing and fail closed. Payload bytes remain opaque;
    only a downstream playback decoder can decide whether compressed data decodes.
    """

    offset = 0
    frame_count = 0
    stream_configuration: tuple[int, int, int, int] | None = None
    while offset < len(audio):
        frame_count += 1
        if frame_count > _MAX_CONTAINER_RECORDS or offset + 7 > len(audio):
            return False
        if audio[offset] != 0xFF or audio[offset + 1] & 0xF6 != 0xF0:
            return False
        # CRC-protected ADTS requires parsing the raw-data block to verify its
        # checksum. Reject it rather than silently treating unchecked media as
        # valid; GPT-SoVITS emits the common seven-byte unprotected header.
        if audio[offset + 1] & 0x01 == 0:
            return False
        profile = (audio[offset + 2] >> 6) & 0x03
        sample_rate_index = (audio[offset + 2] >> 2) & 0x0F
        channel_configuration = (
            ((audio[offset + 2] & 0x01) << 2)
            | ((audio[offset + 3] >> 6) & 0x03)
        )
        raw_data_block_count = audio[offset + 6] & 0x03
        if (
            sample_rate_index > 12
            or channel_configuration == 0
            or raw_data_block_count != 0
        ):
            return False
        frame_length = (
            ((audio[offset + 3] & 0x03) << 11)
            | (audio[offset + 4] << 3)
            | ((audio[offset + 5] & 0xE0) >> 5)
        )
        frame_end = offset + frame_length
        if frame_length <= 7 or frame_end > len(audio):
            return False
        configuration = (
            (audio[offset + 1] >> 3) & 0x01,
            profile,
            sample_rate_index,
            channel_configuration,
        )
        if stream_configuration is None:
            stream_configuration = configuration
        elif configuration != stream_configuration:
            return False
        offset = frame_end
    return frame_count > 0 and offset == len(audio)


def _has_valid_audio_container(
    audio: bytes,
    audio_format: SynthesisAudioFormat,
) -> bool:
    """Perform bounded container and transport-framing checks."""

    if audio_format == "wav":
        return _has_complete_pcm_wav(audio)
    if audio_format == "ogg":
        return _has_complete_ogg_opus(audio)
    return _has_complete_adts_aac(audio)


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """Describe text and a logical local voice selection for one synthesis.

    Profile and emotion are logical identifiers rather than paths or upstream
    API fields. This keeps callers independent from GPT-SoVITS and prevents
    renderer-controlled filesystem values from reaching a local model service.
    """

    text: str
    language: SynthesisLanguage = "auto"
    profile_id: str = "default"
    emotion: str = "neutral"

    def __post_init__(self) -> None:
        """Reject blank, excessive, or adapter-specific request values."""

        if not isinstance(self.text, str):
            raise SynthesisValidationError(
                "Synthesis text must be a string."
            )
        if len(self.text) > SYNTHESIS_MAX_TEXT_CODE_POINTS:
            raise SynthesisValidationError(
                "Synthesis text exceeds the code-point limit."
            )
        if not _contains_spoken_text(self.text):
            raise SynthesisValidationError(
                "Synthesis text must not be blank."
            )
        if (
            not isinstance(self.language, str)
            or self.language not in _LANGUAGES
        ):
            raise SynthesisValidationError(
                "Synthesis language must be auto, zh, or en."
            )
        if not _is_safe_identifier(self.profile_id):
            raise SynthesisValidationError(
                "Synthesis profile_id must be a bounded lowercase identifier."
            )
        if not _is_safe_identifier(self.emotion):
            raise SynthesisValidationError(
                "Synthesis emotion must be a bounded lowercase identifier."
            )


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Hold one bounded result with complete supported transport framing.

    Validation detects malformed/truncated containers before bytes cross a
    process boundary. It intentionally does not promise codec decodability;
    the downstream playback layer must still handle a decoder rejecting payloads.
    """

    audio: bytes
    audio_format: SynthesisAudioFormat
    speed_factor: float

    def __post_init__(self) -> None:
        """Reject malformed containers and unsafe result metadata."""

        if not isinstance(self.audio, bytes):
            raise SynthesisValidationError(
                "Synthesis result audio must be bytes."
            )
        if not self.audio:
            raise SynthesisValidationError(
                "Synthesis result audio must not be empty."
            )
        if len(self.audio) > SYNTHESIS_MAX_AUDIO_BYTES:
            raise SynthesisValidationError(
                "Synthesis result audio exceeds the byte limit."
            )
        if (
            not isinstance(self.audio_format, str)
            or self.audio_format not in _AUDIO_FORMATS
        ):
            raise SynthesisValidationError(
                "Synthesis audio_format must be wav, ogg, or aac."
            )
        if not _has_valid_audio_container(self.audio, self.audio_format):
            raise SynthesisValidationError(
                "Synthesis result does not have complete supported encoded audio "
                "framing."
            )
        if (
            not isinstance(self.speed_factor, float)
            or not math.isfinite(self.speed_factor)
            or not SYNTHESIS_MIN_SPEED_FACTOR
            <= self.speed_factor
            <= SYNTHESIS_MAX_SPEED_FACTOR
        ):
            raise SynthesisValidationError(
                "Synthesis speed_factor must be a finite float between "
                "0.5 and 2.0."
            )

    @property
    def media_type(self) -> str:
        """Return the stable MIME type for this validated audio format."""

        return _MEDIA_TYPES[self.audio_format]


class SpeechSynthesizer(Protocol):
    """Convert bounded text into encoded audio without exposing engine types."""

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Return bounded transport-framed audio or raise a typed error.

        Container validation does not decode compressed samples. A playback
        layer must therefore handle decoder rejection without treating it as a
        successful utterance.
        """
        ...
