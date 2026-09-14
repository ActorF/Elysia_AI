"""Validate bounded mono PCM captures without accessing audio hardware."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from typing import Final

from .exceptions import VoiceCaptureValidationError


VOICE_CAPTURE_SAMPLE_RATE_HZ: Final = 16_000
VOICE_CAPTURE_CHANNEL_COUNT: Final = 1
VOICE_CAPTURE_SAMPLE_FORMAT: Final = "s16le"
VOICE_CAPTURE_FRAME_DURATION_MS: Final = 20
VOICE_CAPTURE_FRAME_SAMPLES: Final = 320
VOICE_CAPTURE_BYTES_PER_SAMPLE: Final = 2
VOICE_CAPTURE_MAX_SAMPLES: Final = 480_000
VOICE_CAPTURE_MIN_SPEECH_SAMPLES: Final = 3_200
VOICE_CAPTURE_MAX_SESSION_ID_LENGTH: Final = 128

_SESSION_ID_PATTERN = re.compile(r"voice_[A-Za-z0-9_-]+\Z")
_MAX_PCM_BYTES: Final = (
    VOICE_CAPTURE_MAX_SAMPLES * VOICE_CAPTURE_BYTES_PER_SAMPLE
)
_MAX_BASE64_CHARACTERS: Final = ((_MAX_PCM_BYTES + 2) // 3) * 4


def _is_strict_integer(value: object) -> bool:
    """Reject booleans even though bool is an int subclass in Python."""

    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class VoiceCapture:
    """Represent one validated, frame-aligned local speech capture.

    ``speech_start_sample`` is inclusive and ``speech_end_sample`` is
    exclusive. The byte payload is always mono signed 16-bit little-endian
    PCM; keeping that format implicit prevents callers from labelling arbitrary
    bytes as another encoding or channel layout.
    """

    session_id: str
    pcm_s16le: bytes
    sample_rate_hz: int
    sample_count: int
    speech_start_sample: int
    speech_end_sample: int

    def __post_init__(self) -> None:
        """Enforce the fixed capture contract before bytes enter Voice work."""

        if (
            not isinstance(self.session_id, str)
            or len(self.session_id) > VOICE_CAPTURE_MAX_SESSION_ID_LENGTH
            or _SESSION_ID_PATTERN.fullmatch(self.session_id) is None
        ):
            raise VoiceCaptureValidationError(
                "Voice capture session_id is invalid."
            )

        if (
            not _is_strict_integer(self.sample_rate_hz)
            or self.sample_rate_hz != VOICE_CAPTURE_SAMPLE_RATE_HZ
        ):
            raise VoiceCaptureValidationError(
                "Voice capture sample_rate_hz must be 16000."
            )

        if (
            not _is_strict_integer(self.sample_count)
            or self.sample_count <= 0
            or self.sample_count > VOICE_CAPTURE_MAX_SAMPLES
            or self.sample_count % VOICE_CAPTURE_FRAME_SAMPLES != 0
        ):
            raise VoiceCaptureValidationError(
                "Voice capture sample_count must be a positive multiple of "
                "320 no greater than 480000."
            )

        if not isinstance(self.pcm_s16le, bytes):
            raise VoiceCaptureValidationError(
                "Voice capture PCM must be immutable bytes."
            )
        expected_bytes = self.sample_count * VOICE_CAPTURE_BYTES_PER_SAMPLE
        if len(self.pcm_s16le) != expected_bytes:
            raise VoiceCaptureValidationError(
                "Voice capture PCM byte length does not match sample_count."
            )

        if (
            not _is_strict_integer(self.speech_start_sample)
            or not _is_strict_integer(self.speech_end_sample)
            or self.speech_start_sample < 0
            or self.speech_start_sample >= self.speech_end_sample
            or self.speech_end_sample > self.sample_count
            or self.speech_start_sample % VOICE_CAPTURE_FRAME_SAMPLES != 0
            or self.speech_end_sample % VOICE_CAPTURE_FRAME_SAMPLES != 0
        ):
            raise VoiceCaptureValidationError(
                "Voice capture speech markers must be frame-aligned and bound "
                "an ordered window within the capture."
            )

        speech_samples = self.speech_end_sample - self.speech_start_sample
        if speech_samples < VOICE_CAPTURE_MIN_SPEECH_SAMPLES:
            raise VoiceCaptureValidationError(
                "Voice capture speech window must contain at least 3200 samples."
            )

        speech_bytes = self.pcm_s16le[
            self.speech_start_sample * VOICE_CAPTURE_BYTES_PER_SAMPLE:
            self.speech_end_sample * VOICE_CAPTURE_BYTES_PER_SAMPLE
        ]
        if not any(speech_bytes):
            raise VoiceCaptureValidationError(
                "Voice capture speech window must contain non-silent PCM."
            )

    @classmethod
    def from_base64(
        cls,
        *,
        session_id: str,
        pcm_s16le_base64: str,
        sample_rate_hz: int,
        sample_count: int,
        speech_start_sample: int,
        speech_end_sample: int,
    ) -> VoiceCapture:
        """Strictly decode one canonical Base64 payload and validate it.

        The encoded value is bounded before decoding. Error messages never
        include the supplied value, so microphone data cannot leak through a
        validation response or log that records exception text.
        """

        if (
            not isinstance(pcm_s16le_base64, str)
            or len(pcm_s16le_base64) > _MAX_BASE64_CHARACTERS
        ):
            raise VoiceCaptureValidationError(
                "Voice capture PCM Base64 payload is invalid."
            )
        try:
            decoded = base64.b64decode(pcm_s16le_base64, validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise VoiceCaptureValidationError(
                "Voice capture PCM Base64 payload is invalid."
            ) from error

        # validate=True rejects non-alphabet bytes; this round trip additionally
        # rejects non-canonical padding and pad bits accepted by some decoders.
        canonical = base64.b64encode(decoded).decode("ascii")
        if canonical != pcm_s16le_base64:
            raise VoiceCaptureValidationError(
                "Voice capture PCM Base64 payload is invalid."
            )

        return cls(
            session_id=session_id,
            pcm_s16le=decoded,
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
            speech_start_sample=speech_start_sample,
            speech_end_sample=speech_end_sample,
        )

    @property
    def duration_ms(self) -> int:
        """Return the exact total capture duration in milliseconds."""

        return self.sample_count * 1_000 // self.sample_rate_hz

    @property
    def speech_duration_ms(self) -> int:
        """Return the exact marked speech duration in milliseconds."""

        speech_samples = self.speech_end_sample - self.speech_start_sample
        return speech_samples * 1_000 // self.sample_rate_hz

    @property
    def sha256_hex(self) -> str:
        """Return a lowercase digest for integrity checks without exposing PCM."""

        return hashlib.sha256(self.pcm_s16le).hexdigest()
