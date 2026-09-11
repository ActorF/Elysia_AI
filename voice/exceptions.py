"""Define stable failures for local Voice settings operations."""


class VoiceSettingsError(Exception):
    """Base error for Voice device-preference operations."""


class VoiceSettingsValidationError(VoiceSettingsError):
    """Raised when a Voice preference document or value is malformed."""


class VoiceSettingsConflictError(VoiceSettingsError):
    """Raised when a stale caller attempts to replace newer preferences."""


class VoiceSettingsStorageError(VoiceSettingsError):
    """Raised when Voice preferences cannot be read or saved safely."""
