"""Define persisted audio-device preferences without accessing hardware."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from .exceptions import VoiceSettingsValidationError


MAX_AUDIO_DEVICE_ID_LENGTH: Final = 2_048
_RESERVED_AUDIO_DEVICE_IDS: Final = frozenset({
    "default",
    "communications",
})


def validate_audio_device_id(value: object) -> str | None:
    """Validate one opaque Electron device ID or the system-default sentinel.

    ``None`` is the only persisted representation of following the current
    system default. IDs remain opaque: Python validates their transport safety
    but never parses them, opens a device, or includes them in error messages.
    """

    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_AUDIO_DEVICE_ID_LENGTH
        or value in _RESERVED_AUDIO_DEVICE_IDS
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise VoiceSettingsValidationError(
            "Audio device IDs must be null or bounded hardware IDs without "
            "reserved values or control characters."
        )
    return value


@dataclass(frozen=True, slots=True)
class AudioDevicePreferences:
    """Store desired input and output IDs independently of live availability."""

    input_device_id: str | None = None
    output_device_id: str | None = None

    def __post_init__(self) -> None:
        """Reject unsafe IDs without normalizing or interpreting them."""

        validate_audio_device_id(self.input_device_id)
        validate_audio_device_id(self.output_device_id)


@dataclass(frozen=True, slots=True)
class VoiceSettingsSnapshot:
    """Return one revisioned preference snapshot and recovery warning."""

    revision: int
    updated_at: datetime | None
    values: AudioDevicePreferences
    warning: str | None = None
