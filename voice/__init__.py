"""Public Voice settings API; audio capture and playback remain in Electron."""

from .domain import (
    MAX_AUDIO_DEVICE_ID_LENGTH,
    AudioDevicePreferences,
    VoiceSettingsSnapshot,
    validate_audio_device_id,
)
from .exceptions import (
    VoiceSettingsConflictError,
    VoiceSettingsError,
    VoiceSettingsStorageError,
    VoiceSettingsValidationError,
)
from .service import VoiceSettingsService, create_voice_settings_service
from .storage import (
    MAX_JSON_SAFE_INTEGER,
    VOICE_SETTINGS_SCHEMA_VERSION,
    JsonVoiceSettingsRepository,
    voice_settings_file_lock,
)

__all__ = [
    "MAX_AUDIO_DEVICE_ID_LENGTH",
    "MAX_JSON_SAFE_INTEGER",
    "VOICE_SETTINGS_SCHEMA_VERSION",
    "AudioDevicePreferences",
    "JsonVoiceSettingsRepository",
    "VoiceSettingsConflictError",
    "VoiceSettingsError",
    "VoiceSettingsService",
    "VoiceSettingsSnapshot",
    "VoiceSettingsStorageError",
    "VoiceSettingsValidationError",
    "create_voice_settings_service",
    "validate_audio_device_id",
    "voice_settings_file_lock",
]
