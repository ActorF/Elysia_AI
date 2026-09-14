"""Public Voice settings, bounded capture, and transcription-domain API."""

from .capture import (
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
)

from .domain import (
    MAX_AUDIO_DEVICE_ID_LENGTH,
    AudioDevicePreferences,
    VoiceSettingsSnapshot,
    validate_audio_device_id,
)
from .exceptions import (
    VoiceCaptureValidationError,
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
from .transcription import (
    TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
    TranscriptLanguage,
    Transcriber,
    TranscriptionError,
    TranscriptionFailedError,
    TranscriptionLanguage,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionUnavailableError,
    TranscriptionValidationError,
)

__all__ = [
    "MAX_AUDIO_DEVICE_ID_LENGTH",
    "MAX_JSON_SAFE_INTEGER",
    "TRANSCRIPTION_MAX_TEXT_CODE_POINTS",
    "VOICE_CAPTURE_BYTES_PER_SAMPLE",
    "VOICE_CAPTURE_CHANNEL_COUNT",
    "VOICE_CAPTURE_FRAME_DURATION_MS",
    "VOICE_CAPTURE_FRAME_SAMPLES",
    "VOICE_CAPTURE_MAX_SAMPLES",
    "VOICE_CAPTURE_MAX_SESSION_ID_LENGTH",
    "VOICE_CAPTURE_MIN_SPEECH_SAMPLES",
    "VOICE_CAPTURE_SAMPLE_FORMAT",
    "VOICE_CAPTURE_SAMPLE_RATE_HZ",
    "VOICE_SETTINGS_SCHEMA_VERSION",
    "AudioDevicePreferences",
    "JsonVoiceSettingsRepository",
    "TranscriptLanguage",
    "Transcriber",
    "TranscriptionError",
    "TranscriptionFailedError",
    "TranscriptionLanguage",
    "TranscriptionRequest",
    "TranscriptionResult",
    "TranscriptionUnavailableError",
    "TranscriptionValidationError",
    "VoiceCapture",
    "VoiceCaptureValidationError",
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
