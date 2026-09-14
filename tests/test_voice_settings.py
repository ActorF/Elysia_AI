"""Test strict, device-local Voice preference persistence and service use."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Event
from typing import cast

import pytest

from voice import (
    MAX_AUDIO_DEVICE_ID_LENGTH,
    MAX_JSON_SAFE_INTEGER,
    VOICE_SETTINGS_SCHEMA_VERSION,
    AudioDevicePreferences,
    JsonVoiceSettingsRepository,
    VoiceSettingsConflictError,
    VoiceSettingsService,
    VoiceSettingsStorageError,
    VoiceSettingsValidationError,
    create_voice_settings_service,
)


SAVED_AT = datetime(2026, 9, 9, 12, 34, 56, tzinfo=timezone.utc)


def _path(tmp_path: Path) -> Path:
    """Return the device-local Voice settings path inside an isolated workspace."""
    return tmp_path / "workspace" / "settings" / "audio-device.json"


def _repository(
    tmp_path: Path,
    *,
    replace_file=os.replace,
) -> JsonVoiceSettingsRepository:
    """Create a deterministic repository with an injectable atomic replace."""
    return JsonVoiceSettingsRepository(
        _path(tmp_path),
        clock=lambda: SAVED_AT,
        replace_file=replace_file,
    )


def _document(
    preferences: AudioDevicePreferences,
    *,
    revision: int,
) -> dict[str, object]:
    """Build the exact persisted Voice settings schema for disk assertions."""
    return {
        "schema_version": VOICE_SETTINGS_SCHEMA_VERSION,
        "revision": revision,
        "updated_at": SAVED_AT.isoformat(),
        "preferences": {
            "input_device_id": preferences.input_device_id,
            "output_device_id": preferences.output_device_id,
        },
    }


def test_first_load_follows_system_defaults_without_creating_a_file(
    tmp_path: Path,
) -> None:
    """Verify that first load follows system defaults without creating a file."""
    repository = _repository(tmp_path)

    snapshot = repository.load()

    assert snapshot.revision == 0
    assert snapshot.updated_at is None
    assert snapshot.values == AudioDevicePreferences()
    assert snapshot.warning is None
    assert not repository.path.exists()


def test_explicit_device_ids_round_trip_across_repository_instances(
    tmp_path: Path,
) -> None:
    """Verify that explicit device IDs round trip across repository instances."""
    preferences = AudioDevicePreferences(
        input_device_id="opaque-input-id",
        output_device_id="opaque-output-id",
    )
    saved = _repository(tmp_path).save(preferences, expected_revision=0)
    reloaded = _repository(tmp_path).load()

    assert saved.revision == 1
    assert saved.updated_at == SAVED_AT
    assert saved.warning is None
    assert reloaded == saved
    assert json.loads(_path(tmp_path).read_text(encoding="utf-8")) == (
        _document(preferences, revision=1)
    )


def test_null_is_the_only_persisted_system_default_representation(
    tmp_path: Path,
) -> None:
    """Verify that null is the only persisted system default representation."""
    repository = _repository(tmp_path)
    explicit = repository.save(
        AudioDevicePreferences(
            input_device_id="hardware-input-id",
            output_device_id="hardware-output-id",
        ),
        expected_revision=0,
    )
    reset = repository.save(
        AudioDevicePreferences(),
        expected_revision=explicit.revision,
    )

    assert explicit.values.input_device_id == "hardware-input-id"
    assert reset.values == AudioDevicePreferences(None, None)
    assert repository.load() == reset


@pytest.mark.parametrize(
    "invalid_id",
    [
        "",
        "default",
        "communications",
        "x" * (MAX_AUDIO_DEVICE_ID_LENGTH + 1),
        "private\x00device",
        "private\ndevice",
        42,
    ],
)
def test_invalid_device_ids_are_rejected_without_echoing_them(
    invalid_id: object,
) -> None:
    """Verify that invalid device IDs are rejected without echoing them."""
    with pytest.raises(VoiceSettingsValidationError) as raised:
        AudioDevicePreferences(input_device_id=invalid_id)  # type: ignore[arg-type]

    if isinstance(invalid_id, str) and invalid_id:
        assert invalid_id not in str(raised.value)


def test_reserved_output_pseudo_id_is_also_rejected() -> None:
    """Verify that reserved output pseudo ID is also rejected."""
    with pytest.raises(VoiceSettingsValidationError):
        AudioDevicePreferences(output_device_id="communications")


def test_saving_identical_preferences_is_a_no_op(
    tmp_path: Path,
) -> None:
    """Verify that saving identical preferences is a no op."""
    repository = _repository(tmp_path)
    preferences = AudioDevicePreferences(input_device_id="input-id")
    first = repository.save(preferences, expected_revision=0)
    before = repository.path.read_bytes()

    second = repository.save(preferences, expected_revision=first.revision)

    assert second == first
    assert repository.path.read_bytes() == before


def test_stale_revision_cannot_overwrite_newer_preferences(
    tmp_path: Path,
) -> None:
    """Verify that stale revision cannot overwrite newer preferences."""
    repository = _repository(tmp_path)
    first = repository.save(
        AudioDevicePreferences(input_device_id="first-input"),
        expected_revision=0,
    )
    before = repository.path.read_bytes()

    with pytest.raises(VoiceSettingsConflictError, match="changed elsewhere"):
        repository.save(
            AudioDevicePreferences(output_device_id="stale-output"),
            expected_revision=0,
        )

    assert repository.path.read_bytes() == before
    assert repository.load() == first


def test_repository_instances_cannot_both_commit_the_same_revision(
    tmp_path: Path,
) -> None:
    """Allow only one concurrent Voice writer to advance a shared revision."""
    initial = _repository(tmp_path)
    first = initial.save(
        AudioDevicePreferences(input_device_id="initial-input"),
        expected_revision=0,
    )
    left = _repository(tmp_path)
    right = _repository(tmp_path)
    ready = Barrier(2)

    def commit(
        repository: JsonVoiceSettingsRepository,
        device_id: str,
    ) -> str:
        """Race one revision-checked save and report its observable outcome."""
        ready.wait()
        try:
            repository.save(
                AudioDevicePreferences(input_device_id=device_id),
                expected_revision=first.revision,
            )
        except VoiceSettingsConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        left_result = executor.submit(commit, left, "left-input")
        right_result = executor.submit(commit, right, "right-input")
        outcomes = {left_result.result(), right_result.result()}

    assert outcomes == {"saved", "conflict"}
    assert initial.load().revision == first.revision + 1


def test_corrupt_recovery_cannot_quarantine_a_concurrent_valid_save(
    tmp_path: Path,
) -> None:
    """Serialize quarantine and save so recovery cannot discard newer valid state."""
    path = _path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    quarantine_started = Event()
    release_quarantine = Event()

    def delayed_replace(source: object, target: object) -> None:
        """Pause corrupt-file quarantine while another repository attempts save."""
        quarantine_started.set()
        if not release_quarantine.wait(timeout=5):
            raise OSError("simulated quarantine timeout")
        os.replace(source, target)  # type: ignore[arg-type]

    recovering = _repository(tmp_path, replace_file=delayed_replace)
    saving = _repository(tmp_path)
    preferences = AudioDevicePreferences(input_device_id="saved-input")

    with ThreadPoolExecutor(max_workers=2) as executor:
        recovered_future = executor.submit(recovering.load)
        assert quarantine_started.wait(timeout=5)
        saved_future = executor.submit(
            saving.save,
            preferences,
            expected_revision=0,
        )
        try:
            with pytest.raises(FutureTimeoutError):
                saved_future.result(timeout=0.2)
        finally:
            release_quarantine.set()
        recovered = recovered_future.result(timeout=5)
        saved = saved_future.result(timeout=5)

    assert recovered.revision == 0
    assert recovered.warning is not None
    assert saved.revision == 1
    assert saving.load() == saved


def test_unknown_or_live_fields_are_quarantined_without_becoming_state(
    tmp_path: Path,
) -> None:
    """Quarantine unknown or live device fields instead of accepting them."""
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    document = _document(AudioDevicePreferences(), revision=1)
    preferences = dict(
        cast(dict[str, object], document["preferences"])
    )
    preferences["permission"] = "granted"
    document["preferences"] = preferences
    repository.path.write_text(json.dumps(document), encoding="utf-8")

    recovered = repository.load()

    assert recovered.revision == 0
    assert recovered.values == AudioDevicePreferences()
    assert recovered.warning is not None
    assert not repository.path.exists()
    quarantined = list(repository.path.parent.glob("audio-device.corrupt-*.json"))
    assert len(quarantined) == 1
    assert "permission" in quarantined[0].read_text(encoding="utf-8")


def test_duplicate_json_fields_are_quarantined(
    tmp_path: Path,
) -> None:
    """Quarantine duplicate JSON keys whose last-value semantics are ambiguous."""
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text(
        '{"schema_version":1,"schema_version":1,"revision":1,'
        '"updated_at":"2026-09-09T12:34:56+00:00",'
        '"preferences":{"input_device_id":null,'
        '"output_device_id":null}}',
        encoding="utf-8",
    )

    recovered = repository.load()

    assert recovered.revision == 0
    assert recovered.warning is not None
    assert not repository.path.exists()


def test_invalid_utf8_is_quarantined_and_can_be_repaired(
    tmp_path: Path,
) -> None:
    """Quarantine invalid UTF-8 while leaving the settings store repairable."""
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_bytes(b"\xff\xfe\x00")

    recovered = repository.load()
    saved = repository.save(
        AudioDevicePreferences(input_device_id="repaired-input"),
        expected_revision=recovered.revision,
    )

    assert recovered.revision == 0
    assert recovered.warning is not None
    assert saved.revision == 1
    assert repository.load() == saved
    assert len(list(
        repository.path.parent.glob("audio-device.corrupt-*.json")
    )) == 1


def test_oversized_json_integer_is_quarantined(
    tmp_path: Path,
) -> None:
    """Quarantine revisions beyond the transport-safe JSON integer boundary."""
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text(
        '{"schema_version":1,"revision":'
        + ("9" * 5_000)
        + ',"updated_at":"2026-09-09T12:34:56+00:00",'
        '"preferences":{"input_device_id":null,'
        '"output_device_id":null}}',
        encoding="utf-8",
    )

    recovered = repository.load()

    assert recovered.revision == 0
    assert recovered.warning is not None
    assert not repository.path.exists()
    assert len(list(
        repository.path.parent.glob("audio-device.corrupt-*.json")
    )) == 1


def test_failed_atomic_replace_preserves_previous_preferences(
    tmp_path: Path,
) -> None:
    """Retain previous Voice preferences when atomic replacement fails."""
    working = _repository(tmp_path)
    first = working.save(
        AudioDevicePreferences(input_device_id="working-input"),
        expected_revision=0,
    )
    before = working.path.read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        """Simulate atomic replacement failing while saving preferences."""
        raise OSError("simulated replace failure")

    failing = _repository(tmp_path, replace_file=fail_replace)
    with pytest.raises(VoiceSettingsStorageError, match="previous values"):
        failing.save(
            AudioDevicePreferences(output_device_id="new-output"),
            expected_revision=first.revision,
        )

    assert working.path.read_bytes() == before
    assert working.load() == first
    assert list(working.path.parent.glob("*.tmp")) == []


def test_corruption_is_not_silently_overwritten_if_quarantine_fails(
    tmp_path: Path,
) -> None:
    """Refuse to overwrite corrupt state when its quarantine step itself fails."""
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text("{broken", encoding="utf-8")
    before = repository.path.read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        """Simulate failure while quarantining a corrupt settings document."""
        raise OSError("simulated quarantine failure")

    failing = _repository(tmp_path, replace_file=fail_replace)
    with pytest.raises(VoiceSettingsStorageError, match="quarantined"):
        failing.load()

    assert repository.path.read_bytes() == before


def test_maximum_revision_cannot_increment_or_modify_the_file(
    tmp_path: Path,
) -> None:
    """Verify that maximum revision cannot increment or modify the file."""
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text(
        json.dumps(
            _document(
                AudioDevicePreferences(input_device_id="current-input"),
                revision=MAX_JSON_SAFE_INTEGER,
            )
        ),
        encoding="utf-8",
    )
    before = repository.path.read_bytes()

    with pytest.raises(VoiceSettingsStorageError, match="revision limit"):
        repository.save(
            AudioDevicePreferences(input_device_id="new-input"),
            expected_revision=MAX_JSON_SAFE_INTEGER,
        )

    assert repository.path.read_bytes() == before


def test_service_factory_uses_the_device_local_settings_path(
    tmp_path: Path,
) -> None:
    """Verify that service factory uses the device local settings path."""
    service = create_voice_settings_service(tmp_path)
    saved = service.update_settings(
        input_device_id="input-id",
        output_device_id=None,
        expected_revision=0,
    )

    assert saved.values == AudioDevicePreferences("input-id", None)
    assert (
        tmp_path / "workspace" / "settings" / "audio-device.json"
    ).exists()
    assert isinstance(service, VoiceSettingsService)
