"""Expose Voice device preferences through a hardware-independent service."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .domain import AudioDevicePreferences, VoiceSettingsSnapshot
from .storage import JsonVoiceSettingsRepository


class VoiceSettingsRepository(Protocol):
    """Describe the persistence operations required by the Voice service."""

    def load(self) -> VoiceSettingsSnapshot:
        """Return the current saved preference snapshot."""

    def save(
        self,
        values: AudioDevicePreferences,
        *,
        expected_revision: int,
    ) -> VoiceSettingsSnapshot:
        """Replace the snapshot only at the expected revision."""


class VoiceSettingsService:
    """Coordinate preference validation and storage without touching devices."""

    def __init__(self, repository: VoiceSettingsRepository) -> None:
        self._repository = repository

    def get_settings(self) -> VoiceSettingsSnapshot:
        """Return the latest persisted device preferences."""

        return self._repository.load()

    def update_settings(
        self,
        *,
        input_device_id: str | None,
        output_device_id: str | None,
        expected_revision: int,
    ) -> VoiceSettingsSnapshot:
        """Validate and atomically save both desired device selections."""

        values = AudioDevicePreferences(
            input_device_id=input_device_id,
            output_device_id=output_device_id,
        )
        return self._repository.save(
            values,
            expected_revision=expected_revision,
        )


def create_voice_settings_service(base_dir: Path) -> VoiceSettingsService:
    """Create the production service in ignored device-local workspace data."""

    repository = JsonVoiceSettingsRepository(
        Path(base_dir) / "workspace" / "settings" / "audio-device.json"
    )
    return VoiceSettingsService(repository)
