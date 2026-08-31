"""Test the revisioned, non-sensitive desktop settings store."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from config.desktop_settings import (
    DESKTOP_SETTINGS_SCHEMA_VERSION,
    DesktopSettingsConflictError,
    DesktopSettingsRepository,
    DesktopSettingsStorageError,
    DesktopSettingsValidationError,
    EditableDesktopSettings,
    MAX_JSON_SAFE_INTEGER,
    ReplaceFile,
    apply_editable_settings,
    changed_setting_names,
    editable_from_app_settings,
    validate_desktop_settings_document,
    validate_ollama_host,
)
from config.settings import AppSettings


SAVED_AT = datetime(2026, 8, 30, 12, 34, 56, tzinfo=timezone.utc)


def _app_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        base_dir=tmp_path,
        model_name="bootstrap-model",
        log_level="INFO",
        debug=False,
        ollama_host="http://localhost:11434",
        short_term_memory_token_budget=2_048,
        memory_retrieval_limit=5,
        data_import_max_bytes=16 * 1024 * 1024,
    )


def _editable(tmp_path: Path) -> EditableDesktopSettings:
    return editable_from_app_settings(_app_settings(tmp_path))


def _changed_values(tmp_path: Path) -> EditableDesktopSettings:
    return replace(
        _editable(tmp_path),
        model_name="saved-model",
        ollama_host="https://ollama.example.test:11434",
        short_term_memory_token_budget=4_096,
        memory_retrieval_limit=8,
        data_import_max_bytes=32 * 1024 * 1024,
    )


def _settings_document(
    values: EditableDesktopSettings,
    *,
    revision: int,
) -> dict[str, object]:
    return {
        "schema_version": DESKTOP_SETTINGS_SCHEMA_VERSION,
        "revision": revision,
        "updated_at": SAVED_AT.isoformat(),
        "settings": {
            "model_name": values.model_name,
            "ollama_host": values.ollama_host,
            "short_term_memory_token_budget": (
                values.short_term_memory_token_budget
            ),
            "memory_retrieval_limit": values.memory_retrieval_limit,
            "data_import_max_bytes": values.data_import_max_bytes,
        },
    }


def _repository(
    tmp_path: Path,
    *,
    replace_file: ReplaceFile = os.replace,
) -> DesktopSettingsRepository:
    return DesktopSettingsRepository(
        tmp_path / "workspace" / "settings" / "global.json",
        _editable(tmp_path),
        clock=lambda: SAVED_AT,
        replace_file=replace_file,
    )


def test_first_load_returns_bootstrap_defaults_without_creating_a_file(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    snapshot = repository.load()

    assert snapshot.revision == 0
    assert snapshot.updated_at is None
    assert snapshot.values == _editable(tmp_path)
    assert snapshot.warning is None
    assert not repository.path.exists()


def test_save_and_reload_round_trip_the_complete_allowlist(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    values = _changed_values(tmp_path)

    saved = repository.save(values, expected_revision=0)
    reloaded = repository.load()

    assert saved.revision == 1
    assert saved.updated_at == SAVED_AT
    assert saved.warning is None
    assert reloaded == saved
    document = json.loads(repository.path.read_text(encoding="utf-8"))
    assert document == {
        "revision": 1,
        "schema_version": DESKTOP_SETTINGS_SCHEMA_VERSION,
        "settings": {
            "data_import_max_bytes": 32 * 1024 * 1024,
            "memory_retrieval_limit": 8,
            "model_name": "saved-model",
            "ollama_host": "https://ollama.example.test:11434",
            "short_term_memory_token_budget": 4_096,
        },
        "updated_at": SAVED_AT.isoformat(),
    }
    assert not any(
        marker in repository.path.read_text(encoding="utf-8").lower()
        for marker in ('"api_key"', '"password"', '"secret"')
    )


def test_saving_identical_values_is_a_no_op_without_revision_bump(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    values = _changed_values(tmp_path)
    first = repository.save(values, expected_revision=0)
    before = repository.path.read_bytes()

    second = repository.save(values, expected_revision=first.revision)

    assert second == first
    assert repository.path.read_bytes() == before


def test_stale_revision_cannot_overwrite_newer_settings(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    first = repository.save(_changed_values(tmp_path), expected_revision=0)
    before = repository.path.read_bytes()

    with pytest.raises(
        DesktopSettingsConflictError,
        match="changed elsewhere",
    ):
        repository.save(
            replace(first.values, model_name="stale-write"),
            expected_revision=0,
        )

    assert repository.path.read_bytes() == before
    assert repository.load() == first


def test_revision_above_json_safe_integer_is_rejected() -> None:
    values = EditableDesktopSettings(
        model_name="safe-model",
        ollama_host="http://localhost:11434",
        short_term_memory_token_budget=2_048,
        memory_retrieval_limit=5,
        data_import_max_bytes=16 * 1024 * 1024,
    )

    with pytest.raises(
        DesktopSettingsValidationError,
        match="JSON-safe integer",
    ):
        validate_desktop_settings_document(
            _settings_document(
                values,
                revision=MAX_JSON_SAFE_INTEGER + 1,
            )
        )


def test_maximum_revision_cannot_increment_or_modify_the_file(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    current_values = _changed_values(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text(
        json.dumps(
            _settings_document(
                current_values,
                revision=MAX_JSON_SAFE_INTEGER,
            )
        ),
        encoding="utf-8",
    )
    before = repository.path.read_bytes()

    with pytest.raises(
        DesktopSettingsStorageError,
        match="revision limit",
    ):
        repository.save(
            replace(current_values, model_name="must-not-be-written"),
            expected_revision=MAX_JSON_SAFE_INTEGER,
        )

    assert repository.path.read_bytes() == before
    assert repository.load().revision == MAX_JSON_SAFE_INTEGER
    assert list(repository.path.parent.glob("*.tmp")) == []


def test_repository_instances_cannot_both_commit_the_same_revision(
    tmp_path: Path,
) -> None:
    initial_repository = _repository(tmp_path)
    first = initial_repository.save(
        _changed_values(tmp_path),
        expected_revision=0,
    )
    left_repository = _repository(tmp_path)
    right_repository = _repository(tmp_path)
    ready = Barrier(2)

    def commit(
        repository: DesktopSettingsRepository,
        model_name: str,
    ) -> str:
        ready.wait()
        try:
            repository.save(
                replace(first.values, model_name=model_name),
                expected_revision=first.revision,
            )
        except DesktopSettingsConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(commit, left_repository, "left-model")
        right = executor.submit(commit, right_repository, "right-model")
        outcomes = {left.result(), right.result()}

    assert outcomes == {"saved", "conflict"}
    final = initial_repository.load()
    assert final.revision == first.revision + 1
    assert final.values.model_name in {"left-model", "right-model"}


@pytest.mark.parametrize("extra_field", ["unexpected", "api_key", "password"])
def test_unknown_or_sensitive_persisted_fields_are_quarantined(
    tmp_path: Path,
    extra_field: str,
) -> None:
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    document = {
        "schema_version": DESKTOP_SETTINGS_SCHEMA_VERSION,
        "revision": 1,
        "updated_at": SAVED_AT.isoformat(),
        "settings": {
            "model_name": "saved-model",
            "ollama_host": "http://localhost:11434",
            "short_term_memory_token_budget": 2_048,
            "memory_retrieval_limit": 5,
            "data_import_max_bytes": 1024,
            extra_field: "must-not-be-accepted",
        },
    }
    repository.path.write_text(json.dumps(document), encoding="utf-8")

    recovered = repository.load()

    assert recovered.revision == 0
    assert recovered.values == _editable(tmp_path)
    assert recovered.warning is not None
    assert not repository.path.exists()
    quarantined = list(repository.path.parent.glob("global.corrupt-*.json"))
    assert len(quarantined) == 1
    assert extra_field in quarantined[0].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "localhost:11434",
        "ftp://localhost:11434",
        "http://user:password@localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?api_key=secret",
        "http://localhost:11434#fragment",
        "http://local host:11434",
        "http://localhost:99999",
    ],
)
def test_invalid_ollama_origins_are_rejected_without_echoing_values(
    value: str,
) -> None:
    with pytest.raises(DesktopSettingsValidationError) as raised:
        validate_ollama_host(value)

    if value:
        assert value not in str(raised.value)


def test_failed_atomic_replace_preserves_old_file_and_removes_temporary_file(
    tmp_path: Path,
) -> None:
    working_repository = _repository(tmp_path)
    first = working_repository.save(
        _changed_values(tmp_path),
        expected_revision=0,
    )
    old_bytes = working_repository.path.read_bytes()

    def fail_replace(
        _source: object,
        _target: object,
    ) -> None:
        raise OSError("simulated replace failure")

    failing_repository = _repository(tmp_path, replace_file=fail_replace)
    with pytest.raises(
        DesktopSettingsStorageError,
        match="previous values remain active",
    ):
        failing_repository.save(
            replace(first.values, model_name="not-committed"),
            expected_revision=first.revision,
        )

    assert working_repository.path.read_bytes() == old_bytes
    assert working_repository.load() == first
    assert list(working_repository.path.parent.glob("*.tmp")) == []


def test_corrupt_json_is_quarantined_and_defaults_remain_recoverable(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text('{"api_key": "do-not-log"', encoding="utf-8")

    recovered = repository.load()

    assert recovered.revision == 0
    assert recovered.updated_at is None
    assert recovered.values == _editable(tmp_path)
    assert recovered.warning == (
        "Saved settings were invalid and bootstrap defaults were restored."
    )
    assert not repository.path.exists()
    quarantined = list(repository.path.parent.glob("global.corrupt-*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == (
        '{"api_key": "do-not-log"'
    )


def test_runtime_settings_apply_desired_values_and_explicit_model_override(
    tmp_path: Path,
) -> None:
    base = _app_settings(tmp_path)
    desired = _changed_values(tmp_path)

    runtime = apply_editable_settings(
        base,
        desired,
        model_override="session-model",
    )

    assert runtime.model_name == "session-model"
    assert runtime.ollama_host == desired.ollama_host
    assert runtime.short_term_memory_token_budget == 4_096
    assert runtime.memory_retrieval_limit == 8
    assert runtime.data_import_max_bytes == 32 * 1024 * 1024
    assert runtime.base_dir == base.base_dir
    assert changed_setting_names(desired, editable_from_app_settings(runtime)) == (
        "modelName",
    )
