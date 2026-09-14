"""Define the engine-independent contract for local speech synthesis."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias


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
_ZERO_WIDTH_NO_BREAK_SPACE: Final = "\ufeff"
_MEDIA_TYPES: Final = {
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
}


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

    # U+FEFF is not whitespace to str.isspace(), but treating it as speech
    # would allow an invisible request to consume expensive local inference.
    return any(
        not (character.isspace() or character == _ZERO_WIDTH_NO_BREAK_SPACE)
        for character in value
    )


def _is_safe_identifier(value: object) -> bool:
    """Return whether a profile or emotion identifier is bounded ASCII."""

    return (
        isinstance(value, str)
        and len(value) <= SYNTHESIS_MAX_IDENTIFIER_LENGTH
        and _IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _has_non_empty_wav_data(audio: bytes) -> bool:
    """Validate RIFF chunk boundaries and require a non-empty data chunk."""

    if (
        len(audio) < 12
        or audio[:4] != b"RIFF"
        or audio[8:12] != b"WAVE"
    ):
        return False
    declared_size = int.from_bytes(audio[4:8], "little") + 8
    if declared_size > len(audio):
        return False

    # RIFF chunks may contain metadata and use one padding byte after odd-sized
    # payloads. Walking the declared chunks avoids accepting a forged WAVE magic
    # prefix with no playable sample data.
    offset = 12
    while offset + 8 <= declared_size:
        chunk_id = audio[offset : offset + 4]
        chunk_size = int.from_bytes(audio[offset + 4 : offset + 8], "little")
        chunk_end = offset + 8 + chunk_size
        if chunk_end > declared_size:
            return False
        if chunk_id == b"data":
            return chunk_size > 0
        offset = chunk_end + (chunk_size % 2)
    return False


def _has_non_empty_ogg_page(audio: bytes) -> bool:
    """Validate the first Ogg page table and require a payload segment."""

    if len(audio) < 27 or audio[:4] != b"OggS" or audio[4] != 0:
        return False
    segment_count = audio[26]
    table_end = 27 + segment_count
    if segment_count == 0 or table_end > len(audio):
        return False
    body_size = sum(audio[27:table_end])
    return body_size > 0 and table_end + body_size <= len(audio)


def _has_complete_aac_frame(audio: bytes) -> bool:
    """Validate one complete ADTS AAC frame without decoding media."""

    if (
        len(audio) < 7
        or audio[0] != 0xFF
        or audio[1] & 0xF6 != 0xF0
    ):
        return False
    frame_length = (
        ((audio[3] & 0x03) << 11)
        | (audio[4] << 3)
        | ((audio[5] & 0xE0) >> 5)
    )
    return frame_length >= 7 and frame_length <= len(audio)


def _has_valid_audio_container(
    audio: bytes,
    audio_format: SynthesisAudioFormat,
) -> bool:
    """Perform bounded structural checks for each supported audio container."""

    if audio_format == "wav":
        return _has_non_empty_wav_data(audio)
    if audio_format == "ogg":
        return _has_non_empty_ogg_page(audio)
    return _has_complete_aac_frame(audio)


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
    """Hold one bounded, structurally valid encoded audio result."""

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
                "Synthesis result is not valid non-empty encoded audio."
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
        """Return playable audio or raise a typed SynthesisError."""
        ...
