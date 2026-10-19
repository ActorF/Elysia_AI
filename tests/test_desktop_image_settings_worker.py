"""Verify private image installation DTOs and bounded path-free CLI responses.

Tiny regular fixture files exercise persistence and native preflight without
launching ComfyUI, loading checkpoints, downloading dependencies, or using GPU.
Successful native configurations use a scoped POSIX test seam; Windows keeps
its production platform and volume verification.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

from tests.image_settings_fixtures import supported_image_preflight

from desktop_image_settings_worker import handle_request


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _request(root: Path) -> dict[str, object]:
    runtime = root / "comfyui"
    runtime.mkdir()
    (runtime / "main.py").write_bytes(b"reviewed main")
    python = root / "python.exe"
    python.write_bytes(b"reviewed python")
    checkpoint = root / "models" / "checkpoints" / "reviewed.safetensors"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"reviewed checkpoint")
    return {
        "op": "configure", "expectedRevision": 0, "trustedConfirmation": True,
        "installation": {
            "runtimeRoot": str(runtime), "pythonExecutable": str(python),
            "checkpointPath": str(checkpoint), "pythonSha256": _digest(b"reviewed python"),
            "comfyuiMainSha256": _digest(b"reviewed main"),
            "checkpointSha256": _digest(b"reviewed checkpoint"),
            "metadata": {
                "displayName": "Reviewed SDXL", "profileId": "reviewed-sdxl",
                "promptDialect": "sdxl-natural", "checkpointSource": "https://example.test/reviewed",
                "checkpointLicense": "Fixture license", "defaultSizePresetId": "portrait",
            },
        },
    }


def test_worker_unconfigured_status_has_no_paths_or_side_effects(tmp_path: Path) -> None:
    """An unconfigured status creates no runtime, settings, or Gallery files."""

    root = tmp_path / "data"
    status = handle_request({"op": "status"}, root)
    assert status["state"] == "unconfigured"
    assert status["profiles"] == []
    assert str(tmp_path) not in json.dumps(status)
    assert not root.exists()


def test_worker_configures_fixed_profile_and_derives_model_root(
    tmp_path: Path, supported_image_preflight: None,
) -> None:
    """Only reviewed metadata and the selected checkpoint form the saved catalog."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    status = handle_request(request, root)
    assert status["ready"] is True
    assert status["revision"] == 1
    profile = cast(list[dict[str, object]], status["profiles"])[0]
    assert profile["sizePresets"] == [
        {"presetId": "square", "width": 1024, "height": 1024},
        {"presetId": "portrait", "width": 832, "height": 1216},
        {"presetId": "landscape", "width": 1216, "height": 832},
    ]
    assert profile["defaultSizePresetId"] == "portrait"
    document = json.loads((root / "workspace" / "settings" / "images.json").read_text(encoding="utf-8"))
    assert document["installation"]["models_root"] == str(tmp_path / "models")
    assert str(tmp_path) not in json.dumps(status)
    assert "reviewed.safetensors" not in json.dumps(status)


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_worker_requires_literal_native_confirmation(tmp_path: Path, confirmation: object) -> None:
    """Truthy request values never bypass the separate native consent decision."""

    request = _request(tmp_path)
    request["trustedConfirmation"] = confirmation
    with pytest.raises(ValueError):
        handle_request(request, tmp_path / "data")
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("mutation", ["workflow", "model-root", "generation-path", "nested-checkpoint", "revision-bool", "size-id"])
def test_worker_rejects_unreviewed_configuration_extensions(tmp_path: Path, mutation: str) -> None:
    """Unknown graphs, roots, path overrides, and defaults fail before persistence."""

    request = _request(tmp_path)
    installation = cast(dict[str, object], request["installation"])
    metadata = cast(dict[str, object], installation["metadata"])
    if mutation == "workflow":
        request["workflow"] = {"1": {"class_type": "CustomNode"}}
    elif mutation == "model-root":
        installation["modelsRoot"] = str(tmp_path)
    elif mutation == "generation-path":
        metadata["modelPath"] = str(tmp_path / "other.safetensors")
    elif mutation == "nested-checkpoint":
        installation["checkpointPath"] = str(tmp_path / "models" / "checkpoints" / "nested" / "model.safetensors")
    elif mutation == "revision-bool":
        request["expectedRevision"] = True
    else:
        metadata["defaultSizePresetId"] = "custom"
    with pytest.raises(Exception):
        handle_request(request, tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_worker_revision_conflict_preserves_prior_configuration(tmp_path: Path) -> None:
    """A stale settings dialog cannot overwrite another native installation save."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    handle_request(request, root)
    saved = root / "workspace" / "settings" / "images.json"
    before = saved.read_bytes()
    with pytest.raises(Exception, match="changed elsewhere"):
        handle_request(request, root)
    assert saved.read_bytes() == before


def test_worker_status_detects_changed_reviewed_model(tmp_path: Path) -> None:
    """Subsequent readiness rehashes local bytes against the explicit review pin."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    handle_request(request, root)
    (tmp_path / "models" / "checkpoints" / "reviewed.safetensors").write_bytes(b"changed")
    assert handle_request({"op": "status"}, root)["state"] == "hash_mismatch"


def test_worker_native_repair_requires_explicit_flag_and_preserves_corrupt_bytes(
    tmp_path: Path, supported_image_preflight: None,
) -> None:
    """Only an explicitly confirmed repair can replace invalid private configuration."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    saved = root / "workspace" / "settings" / "images.json"
    saved.parent.mkdir(parents=True)
    corrupt = b"{malformed private settings"
    saved.write_bytes(corrupt)
    assert handle_request({"op": "status"}, root)["state"] == "invalid"
    with pytest.raises(Exception):
        handle_request(request, root)
    request["repairInvalid"] = True

    status = handle_request(request, root)

    assert status["ready"] is True
    assert saved.with_name("images.invalid-backup.json").read_bytes() == corrupt
    assert str(tmp_path) not in json.dumps(status)


@pytest.mark.parametrize("repair_flag", [1, "true", None])
def test_worker_repair_flag_rejects_truthy_non_booleans(tmp_path: Path, repair_flag: object) -> None:
    """Private worker requests cannot coerce repair consent from arbitrary values."""

    request = _request(tmp_path)
    request["repairInvalid"] = repair_flag
    with pytest.raises(ValueError):
        handle_request(request, tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_worker_repair_cannot_override_a_valid_nonzero_revision(tmp_path: Path) -> None:
    """A repair request never grants permission to overwrite valid versioned settings."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    handle_request(request, root)
    saved = root / "workspace" / "settings" / "images.json"
    before = saved.read_bytes()
    request["expectedRevision"] = 1
    request["repairInvalid"] = True
    with pytest.raises(ValueError):
        handle_request(request, root)
    assert saved.read_bytes() == before


@pytest.mark.parametrize("payload", [
    b'{"op":"status","op":"status"}\n',
    b'{"op":"status"}\n{"op":"status"}\n',
    b'{"op":NaN}\n',
    b"x" * 1_048_577,
    b"\xff\n",
], ids=["duplicate", "multiple-lines", "nan", "oversized", "invalid-utf8"])
def test_worker_cli_rejects_ambiguous_or_oversized_input(tmp_path: Path, payload: bytes) -> None:
    """Malformed private requests produce exactly one fixed response and no diagnostics."""

    script = Path(__file__).resolve().parents[1] / "desktop_image_settings_worker.py"
    process = subprocess.run(
        [sys.executable, str(script), "--data-root", str(tmp_path / "data")],
        input=payload, capture_output=True, timeout=120, check=False,
    )
    assert process.returncode == 1
    assert json.loads(process.stdout) == {"error": "image.settings.unavailable"}
    assert len(process.stdout.splitlines()) == 1
    assert process.stderr == b""
    assert str(tmp_path).encode() not in process.stdout


def test_worker_cli_returns_one_path_free_status(tmp_path: Path) -> None:
    """The production CLI responds only after the one request and process close."""

    script = Path(__file__).resolve().parents[1] / "desktop_image_settings_worker.py"
    process = subprocess.run(
        [sys.executable, str(script), "--data-root", str(tmp_path / "data")],
        input=b'{"op":"status"}\n', capture_output=True, timeout=120, check=False,
    )
    assert process.returncode == 0
    assert json.loads(process.stdout)["state"] == "unconfigured"
    assert process.stderr == b""
    assert len(process.stdout.splitlines()) == 1


def test_worker_cli_inspects_a_native_checkpoint_without_exposing_its_source(tmp_path: Path) -> None:
    """The private inspection operation has one bounded path-free response and no side effects."""

    source = tmp_path / "selected_checkpoint.safetensors"
    header = b'{"model.diffusion_model.fixture":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}'
    content = len(header).to_bytes(8, "little") + header + b"a"
    source.write_bytes(content)
    root = tmp_path / "data"
    request = {
        "op": "assets.inspect", "kind": "checkpoint", "sourcePath": str(source),
        "sha256": _digest(content), "targetProfileId": None,
    }
    script = Path(__file__).resolve().parents[1] / "desktop_image_settings_worker.py"
    process = subprocess.run(
        [sys.executable, str(script), "--data-root", str(root)],
        input=(json.dumps(request) + "\n").encode("utf-8"), capture_output=True, timeout=120, check=False,
    )
    assert process.returncode == 0
    result = json.loads(process.stdout)
    assert result["kind"] == "image.asset.metadata"
    assert result["metadata"]["displayName"] == "selected checkpoint"
    assert str(tmp_path).encode() not in process.stdout
    assert process.stderr == b""
    assert len(process.stdout.splitlines()) == 1
    assert not root.exists()
