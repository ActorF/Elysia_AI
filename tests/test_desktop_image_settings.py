"""Verify trusted image settings, pinned readiness, and path-free UI summaries.

Tiny fixture files stand in for reviewed runtime/model bytes. No test launches
ComfyUI, interprets a checkpoint, downloads dependencies, or allocates a GPU.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import cast

import pytest

import desktop_image_settings as image_settings
from desktop_image_settings import (
    DesktopImageSettingsError,
    DesktopImageSettingsStore,
    TrustedImageInstallation,
)
from image_generation.exceptions import ImageGenerationUnavailableError
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelProfile,
    ModelProfileCatalog,
    PromptDialect,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _installation(root: Path) -> TrustedImageInstallation:
    runtime = root / "trusted-comfyui"
    runtime.mkdir()
    (runtime / "main.py").write_bytes(b"reviewed entry fixture")
    interpreter = root / "python.exe"
    interpreter.write_bytes(b"reviewed interpreter fixture")
    models = root / "reviewed-models"
    (models / "checkpoints").mkdir(parents=True)
    (models / "checkpoints" / "fixture.safetensors").write_bytes(b"reviewed model fixture")
    profile = ModelProfile(
        profile_id="reviewed-sdxl", display_name="Reviewed SDXL",
        architecture=ModelArchitecture.SDXL, prompt_dialect=PromptDialect.SDXL_NATURAL,
        checkpoint_name="fixture.safetensors", checkpoint_sha256=_digest(b"reviewed model fixture"),
        checkpoint_source="https://example.test/reviewed-model", checkpoint_license="Fixture only",
        size_presets=(ImageSizePreset("square", 1024, 1024), ImageSizePreset("portrait", 832, 1216)),
        default_size_preset_id="portrait",
    )
    return TrustedImageInstallation(
        runtime_root=runtime, python_executable=interpreter, models_root=models,
        python_sha256=_digest(b"reviewed interpreter fixture"),
        comfyui_main_sha256=_digest(b"reviewed entry fixture"),
        profiles=ModelProfileCatalog((profile,)), default_profile_id="reviewed-sdxl",
    )


def _settings_path(data_root: Path) -> Path:
    return data_root / "workspace" / "settings" / "images.json"


def _save(root: Path) -> tuple[DesktopImageSettingsStore, TrustedImageInstallation, Path]:
    installation = _installation(root)
    data_root = root / "data"
    store = DesktopImageSettingsStore(data_root)
    store.save(installation, trusted_confirmation=True, expected_revision=0)
    return store, installation, data_root


def test_unconfigured_status_is_path_free_and_creates_nothing(tmp_path: Path) -> None:
    """An untouched application has no image capability and no generated state."""

    root = tmp_path / "data"
    status = DesktopImageSettingsStore(root).status().to_public_dict()

    assert status["state"] == "unconfigured"
    assert status["configured"] is False
    assert status["ready"] is False
    assert status["profiles"] == []
    assert status["defaultProfileId"] is None
    assert not root.exists()
    assert str(tmp_path) not in json.dumps(status)


def test_confirmed_settings_roundtrip_and_native_readiness(tmp_path: Path) -> None:
    """Restore reviewed profiles and report actual pinned-file readiness."""

    store, installation, data_root = _save(tmp_path)
    loaded = DesktopImageSettingsStore(data_root).load()
    assert loaded is not None
    assert loaded.runtime_root == installation.runtime_root
    assert loaded.profiles.profiles == installation.profiles.profiles
    status = store.status().to_public_dict()
    assert status["state"] == "ready"
    assert status["ready"] is True
    assert status["configured"] is True
    assert status["revision"] == 1
    assert status["defaultProfileId"] == "reviewed-sdxl"
    profiles = cast(list[dict[str, object]], status["profiles"])
    assert profiles[0]["defaultSizePresetId"] == "portrait"
    assert profiles[0]["sizePresets"] == [
        {"presetId": "square", "width": 1024, "height": 1024},
        {"presetId": "portrait", "width": 832, "height": 1216},
    ]
    assert str(tmp_path) not in json.dumps(status)
    assert "fixture.safetensors" not in json.dumps(status)
    assert installation.python_sha256 not in json.dumps(status)
    assert list((data_root / "workspace" / "image-runs").iterdir()) == []
    assert not (data_root / "workspace" / "gallery").exists()


def test_venv_redirector_status_explains_supported_interpreter(tmp_path: Path) -> None:
    """Return actionable path-free Settings guidance before native process creation."""

    installation = _installation(tmp_path)
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    interpreter = scripts / "python.exe"
    interpreter.write_bytes(installation.python_executable.read_bytes())
    (scripts.parent / "pyvenv.cfg").write_text("home = fixture\n", encoding="utf-8")
    store = DesktopImageSettingsStore(tmp_path / "data")
    store.save(replace(installation, python_executable=interpreter), trusted_confirmation=True, expected_revision=0)
    status = store.status().to_public_dict()
    assert status["state"] == "invalid"
    assert status["ready"] is False
    assert "virtual-environment redirectors" in str(status["message"])
    assert "portable" in str(status["message"])
    assert str(tmp_path) not in json.dumps(status)


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_installation_save_requires_literal_confirmation(tmp_path: Path, confirmation: object) -> None:
    """Renderer-shaped or truthy values cannot authorize trusted local paths."""

    installation = _installation(tmp_path)
    root = tmp_path / "data"
    store = DesktopImageSettingsStore(root)

    with pytest.raises(DesktopImageSettingsError, match="Confirm"):
        store.save(installation, trusted_confirmation=cast(bool, confirmation), expected_revision=0)

    assert not root.exists()


def test_revision_conflict_preserves_current_settings(tmp_path: Path) -> None:
    """A stale installer cannot overwrite a newer confirmed configuration."""

    store, installation, root = _save(tmp_path)
    before = _settings_path(root).read_bytes()

    with pytest.raises(DesktopImageSettingsError, match="changed elsewhere"):
        store.save(installation, trusted_confirmation=True, expected_revision=0)

    assert _settings_path(root).read_bytes() == before
    assert store.status().revision == 1


@pytest.mark.parametrize("target", ["model", "python", "main"])
def test_changed_pinned_inputs_disable_readiness(tmp_path: Path, target: str) -> None:
    """Hash checks re-read model, interpreter, and entry point on every status."""

    store, installation, _ = _save(tmp_path)
    paths = {
        "model": installation.models_root / "checkpoints" / "fixture.safetensors",
        "python": installation.python_executable,
        "main": installation.runtime_root / "main.py",
    }
    paths[target].write_bytes(b"changed after review")

    status = store.status().to_public_dict()

    assert status["state"] == "hash_mismatch"
    assert status["ready"] is False
    assert str(tmp_path) not in json.dumps(status)
    with pytest.raises(ImageGenerationUnavailableError) as captured:
        store.create_service()
    assert captured.value.__context__ is None
    assert str(tmp_path) not in str(captured.value)


def test_missing_checkpoint_has_actionable_model_state(tmp_path: Path) -> None:
    """Missing local weights never trigger an implicit network installation."""

    store, installation, _ = _save(tmp_path)
    (installation.models_root / "checkpoints" / "fixture.safetensors").unlink()

    status = store.status().to_public_dict()

    assert status["state"] == "missing_model"
    assert status["configured"] is True
    assert status["ready"] is False
    assert "Install" in cast(str, status["message"])


@pytest.mark.parametrize("unsafe_input", ["extra-model-roots", "leftover-run", "missing-runtime"])
def test_supervisor_preflight_still_rejects_unsafe_installations(tmp_path: Path, unsafe_input: str) -> None:
    """Useful Settings errors cannot bypass the supervisor's ownership policy."""

    store, installation, root = _save(tmp_path)
    if unsafe_input == "extra-model-roots":
        (installation.runtime_root / "extra_model_paths.yaml").write_text("unreviewed: models", encoding="utf-8")
    elif unsafe_input == "leftover-run":
        (root / "workspace" / "image-runs" / "unknown-state").mkdir()
    else:
        (installation.runtime_root / "main.py").unlink()

    assert store.status().state == ("recovery_required" if unsafe_input == "leftover-run" else "invalid")
    with pytest.raises(ImageGenerationUnavailableError):
        store.create_service()


@pytest.mark.parametrize("corruption", ["unknown-field", "boolean-version", "duplicate", "oversized", "workflow", "relative-path", "unknown-preset"])
def test_untrusted_settings_fail_closed_without_quarantine(tmp_path: Path, corruption: str) -> None:
    """Strict files reject ambiguous schema and retain corrupt bytes for repair."""

    store, _, root = _save(tmp_path)
    path = _settings_path(root)
    document = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "unknown-field":
        document["unreviewed"] = True
    elif corruption == "boolean-version":
        document["schema_version"] = True
    elif corruption == "workflow":
        document["installation"]["workflow"] = {"1": {"class_type": "CustomNode"}}
    elif corruption == "relative-path":
        document["installation"]["runtime_root"] = "relative-runtime"
    elif corruption == "unknown-preset":
        document["installation"]["profiles"][0]["default_size_preset_id"] = "not-allowlisted"
    if corruption == "duplicate":
        encoded = b'{"schema_version":1,"schema_version":1}'
    elif corruption == "oversized":
        encoded = b" " * 1_048_577
    else:
        encoded = json.dumps(document).encode("utf-8")
    path.write_bytes(encoded)

    status = store.status().to_public_dict()

    assert status["state"] == "invalid"
    assert status["ready"] is False
    assert path.read_bytes() == encoded
    assert not list(path.parent.glob("*.corrupt-*"))
    with pytest.raises(DesktopImageSettingsError) as captured:
        store.load()
    assert captured.value.__context__ is None


def test_atomic_write_failure_preserves_last_confirmed_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacement failure leaves the old config and removes its temporary file."""

    store, installation, root = _save(tmp_path)
    before = _settings_path(root).read_bytes()

    def _fail_replace(source: object, destination: object) -> None:
        raise OSError("D:/private/runtime/secret")

    monkeypatch.setattr(image_settings.os, "replace", _fail_replace)
    with pytest.raises(DesktopImageSettingsError) as captured:
        store.save(installation, trusted_confirmation=True, expected_revision=1)

    assert _settings_path(root).read_bytes() == before
    assert not list(_settings_path(root).parent.glob(".images-*.tmp"))
    assert captured.value.__context__ is None
    assert "secret" not in str(captured.value)


def test_service_factory_receives_only_fixed_private_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service composition uses reviewed profiles and application-owned roots."""

    store, installation, root = _save(tmp_path)
    result = object()
    calls: list[object] = []

    def _factory(profiles: object, config: object, data_root: object) -> object:
        calls.extend((profiles, config, data_root))
        return result

    monkeypatch.setattr(image_settings, "create_managed_image_application_service", _factory)

    assert store.create_service() is result
    assert cast(ModelProfileCatalog, calls[0]).profiles == installation.profiles.profiles
    assert calls[2] == root
    assert getattr(calls[1], "private_state_root") == root / "workspace" / "image-runs"


def test_hardlinked_settings_are_rejected(tmp_path: Path) -> None:
    """The private configuration cannot alias a separate user's editable file."""

    store, _, root = _save(tmp_path)
    os.link(_settings_path(root), tmp_path / "config-alias.json")

    assert store.status().state == "invalid"


@pytest.mark.parametrize("link_kind", ["config", "settings-directory", "dangling-directory", "model"])
def test_filesystem_links_cannot_redirect_configuration_or_models(tmp_path: Path, link_kind: str) -> None:
    """Existing and dangling links must fail closed before their targets are used."""

    store, installation, root = _save(tmp_path)
    if link_kind == "model":
        original = installation.models_root / "checkpoints" / "fixture.safetensors"
        target = original.with_name("other.safetensors")
        original.rename(target)
        directory = False
    elif link_kind == "config":
        original = _settings_path(root)
        target = original.with_name("other.json")
        original.rename(target)
        directory = False
    else:
        original = _settings_path(root).parent
        target = tmp_path / "other-settings"
        original.rename(target)
        if link_kind == "dangling-directory":
            target = tmp_path / "absent-settings"
        directory = True
    try:
        original.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("The host does not grant local symlink creation.")

    assert store.status().state == "invalid"
    with pytest.raises(ImageGenerationUnavailableError):
        store.create_service()


def test_aliased_lock_file_cannot_receive_private_settings_writes(tmp_path: Path) -> None:
    """Cross-process locking never writes to an external hardlinked lock alias."""

    store, installation, root = _save(tmp_path)
    lock_path = _settings_path(root).with_name(".images.json.lock")
    os.link(lock_path, tmp_path / "external-lock")
    before = _settings_path(root).read_bytes()

    with pytest.raises(DesktopImageSettingsError):
        store.save(installation, trusted_confirmation=True, expected_revision=1)

    assert _settings_path(root).read_bytes() == before


def test_explicit_native_repair_preserves_corrupt_config_and_gallery(tmp_path: Path) -> None:
    """A confirmed repair replaces only invalid settings after making a private backup."""

    store, installation, root = _save(tmp_path)
    path = _settings_path(root)
    corrupt = b'{"private-invalid-document":true}'
    path.write_bytes(corrupt)
    gallery = root / "workspace" / "gallery"
    gallery.mkdir()
    artifact = gallery / "private-artifact.png"
    artifact.write_bytes(b"retained generated image")
    with pytest.raises(DesktopImageSettingsError):
        store.save(installation, trusted_confirmation=True, expected_revision=0)

    result = store.save(installation, trusted_confirmation=True, expected_revision=0, repair_invalid=True)

    assert result.state == "ready"
    assert result.revision == 1
    assert path.with_name("images.invalid-backup.json").read_bytes() == corrupt
    assert artifact.read_bytes() == b"retained generated image"
    assert store.load() is not None


@pytest.mark.parametrize("invalid_flag", [1, "true", None])
def test_repair_requires_a_literal_boolean_flag(tmp_path: Path, invalid_flag: object) -> None:
    """Truthy repair options cannot widen the trusted Python configuration contract."""

    store, installation, root = _save(tmp_path)
    before = _settings_path(root).read_bytes()
    with pytest.raises(DesktopImageSettingsError):
        store.save(installation, trusted_confirmation=True, expected_revision=0, repair_invalid=cast(bool, invalid_flag))
    assert _settings_path(root).read_bytes() == before


@pytest.mark.parametrize("revision", [0, 1])
def test_repair_cannot_replace_a_valid_versioned_configuration(tmp_path: Path, revision: int) -> None:
    """Recovery never bypasses the ordinary revision conflict check for valid files."""

    store, installation, root = _save(tmp_path)
    before = _settings_path(root).read_bytes()
    with pytest.raises(DesktopImageSettingsError):
        store.save(installation, trusted_confirmation=True, expected_revision=revision, repair_invalid=True)
    assert _settings_path(root).read_bytes() == before
    assert not _settings_path(root).with_name("images.invalid-backup.json").exists()


@pytest.mark.parametrize("unsafe_target", ["oversized-config", "aliased-config", "backup-directory", "aliased-backup", "oversized-backup"])
def test_repair_refuses_unsafe_config_or_backup_targets(tmp_path: Path, unsafe_target: str) -> None:
    """Repair cannot adopt links, oversized files, or an unexpected backup location."""

    store, installation, root = _save(tmp_path)
    path = _settings_path(root)
    corrupt = b"invalid reviewed settings"
    path.write_bytes(corrupt)
    backup = path.with_name("images.invalid-backup.json")
    if unsafe_target == "oversized-config":
        path.write_bytes(b" " * 1_048_577)
    elif unsafe_target == "aliased-config":
        os.link(path, tmp_path / "aliased-settings")
    elif unsafe_target == "backup-directory":
        backup.mkdir()
    elif unsafe_target == "aliased-backup":
        backup.write_bytes(b"external private bytes")
        os.link(backup, tmp_path / "aliased-backup")
    else:
        backup.write_bytes(b" " * 1_048_577)
    before = path.read_bytes()

    with pytest.raises(DesktopImageSettingsError):
        store.save(installation, trusted_confirmation=True, expected_revision=0, repair_invalid=True)

    assert path.read_bytes() == before
    assert not list(path.parent.glob(".images-*.tmp"))


def test_failed_repair_replacement_retains_original_and_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed final replacement leaves recovery bytes intact in both fixed files."""

    store, installation, root = _save(tmp_path)
    path = _settings_path(root)
    corrupt = b"invalid previous installation"
    path.write_bytes(corrupt)
    replace_file = os.replace

    def _fail_config_only(source: Path, destination: Path) -> None:
        if destination == path:
            raise OSError("private installation path")
        replace_file(source, destination)

    monkeypatch.setattr(image_settings.os, "replace", _fail_config_only)
    with pytest.raises(DesktopImageSettingsError):
        store.save(installation, trusted_confirmation=True, expected_revision=0, repair_invalid=True)

    assert path.read_bytes() == corrupt
    assert path.with_name("images.invalid-backup.json").read_bytes() == corrupt
    assert not list(path.parent.glob(".images-*.tmp"))


def test_relative_root_and_unknown_default_profile_are_rejected(tmp_path: Path) -> None:
    """Installation identity never falls back to a cwd or different model."""

    with pytest.raises(DesktopImageSettingsError):
        DesktopImageSettingsStore(Path("relative"))
    installation = _installation(tmp_path)
    with pytest.raises(Exception, match="unknown"):
        replace(installation, default_profile_id="unknown")
