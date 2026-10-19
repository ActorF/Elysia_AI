"""Bridge one private Main-owned image installation request to trusted settings.

The worker accepts a bounded single NDJSON request on stdin and writes one
path-free status or derived metadata response on stdout. It never launches
ComfyUI, downloads dependencies, or accepts a generation workflow. Native-picked
sources and digests arrive only from Electron Main. Read-only inspection needs
no installation form; writing the catalog requires separate native confirmation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Literal, NoReturn, Sequence, cast

from desktop_image_settings import DesktopImageSettingsStore, TrustedImageInstallation
from desktop_image_assets import ImageCheckpointImport, ImageLoraImport, import_image_assets, inspect_image_asset
from desktop_image_recovery import preview_image_recovery, quarantine_image_recovery
from image_generation.profiles import (
    ImageSizePreset, ModelArchitecture, ModelLoraProfile, ModelProfile, ModelProfileCatalog, PromptDialect,
)


_MAX_REQUEST_BYTES = 1_048_576
_METADATA_FIELDS = frozenset({
    "displayName", "profileId", "promptDialect", "checkpointSource",
    "checkpointLicense", "defaultSizePresetId",
})
_INSTALLATION_FIELDS = frozenset({
    "runtimeRoot", "pythonExecutable", "checkpointPath", "pythonSha256",
    "comfyuiMainSha256", "checkpointSha256", "metadata",
})
_PRESETS = (
    ImageSizePreset("square", 1024, 1024),
    ImageSizePreset("portrait", 832, 1216),
    ImageSizePreset("landscape", 1216, 832),
)


class _ArgumentParser(argparse.ArgumentParser):
    """Avoid reflecting malformed private command arguments to native stderr."""

    def error(self, message: str) -> NoReturn:
        """Reject invalid flags without argparse's argument-bearing diagnostics."""

        raise ValueError("Image settings worker arguments are invalid.")


def _fields(value: object, names: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != names:
        raise ValueError("Image settings request fields are invalid.")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("Image settings request text is invalid.")
    return value


def _path(value: object) -> Path:
    result = Path(_text(value))
    if not result.is_absolute() or ".." in result.parts or "\x00" in str(result):
        raise ValueError("Image settings paths must be absolute.")
    return result


def _digest(value: object) -> str:
    result = _text(value)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError("Image settings digests are invalid.")
    return result


def _installation(value: object) -> TrustedImageInstallation:
    raw = _fields(value, _INSTALLATION_FIELDS)
    metadata = _fields(raw["metadata"], _METADATA_FIELDS)
    checkpoint = _path(raw["checkpointPath"])
    # Main exposes only a checkpoint picker, never a free-form model root.
    # Repeating this derivation prevents a private DTO from granting a broader
    # model search path or a nested checkpoint key than the reviewed first UI.
    if (
        os.path.normcase(checkpoint.parent.name) != os.path.normcase("checkpoints")
        or checkpoint.suffix.lower() != ".safetensors"
    ):
        raise ValueError("Select a safetensors checkpoint directly under checkpoints.")
    profile_id = _text(metadata["profileId"])
    profile = ModelProfile(
        profile_id=profile_id,
        display_name=_text(metadata["displayName"]),
        architecture=ModelArchitecture.SDXL,
        prompt_dialect=PromptDialect(_text(metadata["promptDialect"])),
        checkpoint_name=checkpoint.name,
        checkpoint_sha256=_digest(raw["checkpointSha256"]),
        checkpoint_source=_text(metadata["checkpointSource"]),
        checkpoint_license=_text(metadata["checkpointLicense"]),
        size_presets=_PRESETS,
        default_size_preset_id=_text(metadata["defaultSizePresetId"]),
    )
    return TrustedImageInstallation(
        runtime_root=_path(raw["runtimeRoot"]),
        python_executable=_path(raw["pythonExecutable"]),
        models_root=checkpoint.parent.parent,
        python_sha256=_digest(raw["pythonSha256"]),
        comfyui_main_sha256=_digest(raw["comfyuiMainSha256"]),
        profiles=ModelProfileCatalog((profile,)),
        default_profile_id=profile_id,
    )


def _asset_import(raw: dict[str, object], data_root: Path) -> dict[str, object]:
    """Decode native-only asset sources without extending generation requests."""

    _fields(raw, frozenset({"op", "expectedRevision", "trustedConfirmation", "checkpoint", "lora", "installation"}))
    if raw["trustedConfirmation"] is not True:
        raise ValueError("Confirm the image asset import in the native dialog.")
    revision = raw["expectedRevision"]
    if type(revision) is not int or not 0 <= revision <= 9_007_199_254_740_991:
        raise ValueError("Image settings revision is invalid.")
    checkpoint: ImageCheckpointImport | None = None
    lora: ImageLoraImport | None = None
    installation: TrustedImageInstallation | None = None
    if raw["checkpoint"] is not None:
        source = _fields(raw["checkpoint"], frozenset({"sourcePath", "sha256", "metadata"}))
        metadata = _fields(source["metadata"], _METADATA_FIELDS)
        digest = _digest(source["sha256"])
        checkpoint = ImageCheckpointImport(_path(source["sourcePath"]), ModelProfile(
            profile_id=_text(metadata["profileId"]), display_name=_text(metadata["displayName"]),
            architecture=ModelArchitecture.SDXL, prompt_dialect=PromptDialect(_text(metadata["promptDialect"])),
            checkpoint_name=f"{digest}.safetensors", checkpoint_sha256=digest,
            checkpoint_source=_text(metadata["checkpointSource"]), checkpoint_license=_text(metadata["checkpointLicense"]),
            size_presets=_PRESETS, default_size_preset_id=_text(metadata["defaultSizePresetId"]),
        ))
    if raw["lora"] is not None:
        source = _fields(raw["lora"], frozenset({"sourcePath", "sha256", "targetProfileId", "loraId", "displayName", "source", "license"}))
        digest = _digest(source["sha256"])
        lora = ImageLoraImport(_path(source["sourcePath"]), _text(source["targetProfileId"]), ModelLoraProfile(
            lora_id=_text(source["loraId"]), display_name=_text(source["displayName"]),
            architecture=ModelArchitecture.SDXL, lora_name=f"{digest}.safetensors", lora_sha256=digest,
            lora_source=_text(source["source"]), lora_license=_text(source["license"]),
        ))
    if raw["installation"] is not None:
        runtime = _fields(raw["installation"], frozenset({"runtimeRoot", "pythonExecutable", "pythonSha256", "comfyuiMainSha256"}))
        if checkpoint is None:
            raise ValueError("First installation requires a reviewed checkpoint.")
        installation = TrustedImageInstallation(
            runtime_root=_path(runtime["runtimeRoot"]), python_executable=_path(runtime["pythonExecutable"]),
            models_root=data_root / "workspace" / "image-models", python_sha256=_digest(runtime["pythonSha256"]),
            comfyui_main_sha256=_digest(runtime["comfyuiMainSha256"]),
            profiles=ModelProfileCatalog((checkpoint.profile,)), default_profile_id=checkpoint.profile.profile_id,
        )
    return import_image_assets(
        data_root, checkpoint=checkpoint, lora=lora, installation=installation,
        expected_revision=revision, trusted_confirmation=True,
    ).to_public_dict()


def _asset_inspect(raw: dict[str, object], data_root: Path) -> dict[str, object]:
    """Resolve Main-picked metadata without creating settings or runtime state."""

    _fields(raw, frozenset({"op", "kind", "sourcePath", "sha256", "targetProfileId"}))
    kind = raw["kind"]
    if kind not in ("checkpoint", "lora"):
        raise ValueError("Image asset kind is unsupported.")
    target = raw["targetProfileId"]
    if kind == "lora":
        target = _text(target)
        installation = DesktopImageSettingsStore(data_root).load()
        if installation is None:
            raise ValueError("A LoRA requires an existing model profile.")
        installation.profiles.get(target)
    elif target is not None:
        raise ValueError("Checkpoint inspection cannot target another profile.")
    metadata = inspect_image_asset(
        _path(raw["sourcePath"]), kind=cast(Literal["checkpoint", "lora"], kind),
        sha256=_digest(raw["sha256"]), target_profile_id=cast(str | None, target),
    )
    return {"kind": "image.asset.metadata", "metadata": metadata}


def handle_request(request: object, data_root: Path) -> dict[str, object]:
    """Apply one closed private request and return the store's path-free status.

    Configuration requires literal native confirmation and the revision Main
    read before showing its dialogs. Explicit invalid-document repair also
    requires revision zero and preserves the previous fixed private file.
    Reviewed pins remain authoritative even if files change afterwards;
    readiness and every run reverify those pins.
    """

    if type(request) is not dict:
        raise ValueError("Image settings request must be an object.")
    raw = cast(dict[str, object], request)
    store = DesktopImageSettingsStore(data_root)
    if raw.get("op") == "status":
        _fields(raw, frozenset({"op"}))
        return store.status().to_public_dict()
    if raw.get("op") == "recovery.preview":
        _fields(raw, frozenset({"op"}))
        return preview_image_recovery(data_root).to_private_dict()
    if raw.get("op") == "recovery.apply":
        _fields(raw, frozenset({"op", "snapshot", "trustedConfirmation"}))
        if raw["trustedConfirmation"] is not True:
            raise ValueError("Confirm private image recovery in the native dialog.")
        count = quarantine_image_recovery(data_root, snapshot=_digest(raw["snapshot"]), trusted_confirmation=True)
        return {"kind": "image.recovery.result", "quarantinedCount": count, "status": store.status().to_public_dict()}
    if raw.get("op") == "assets.import":
        return _asset_import(raw, data_root)
    if raw.get("op") == "assets.inspect":
        return _asset_inspect(raw, data_root)
    if raw.get("op") != "configure":
        raise ValueError("Image settings operation is unsupported.")
    expected_fields = frozenset({"op", "installation", "expectedRevision", "trustedConfirmation"})
    if "repairInvalid" in raw:
        expected_fields |= {"repairInvalid"}
    _fields(raw, expected_fields)
    if raw["trustedConfirmation"] is not True:
        raise ValueError("Confirm the installation in the native dialog.")
    revision = raw["expectedRevision"]
    if type(revision) is not int or not 0 <= revision <= 9_007_199_254_740_991:
        raise ValueError("Image settings revision is invalid.")
    repair_invalid = raw.get("repairInvalid", False)
    if type(repair_invalid) is not bool or (repair_invalid and revision != 0):
        raise ValueError("Image settings repair requires revision zero and explicit native confirmation.")
    return store.save(
        _installation(raw["installation"]), trusted_confirmation=True, expected_revision=revision,
        repair_invalid=repair_invalid,
    ).to_public_dict()


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("Duplicate image settings request field.")
        value[name] = item
    return value


def _constant(value: str) -> object:
    raise ValueError("Image settings numeric constants are invalid.")


def main(arguments: Sequence[str] | None = None) -> int:
    """Execute one bounded stdin operation without exposing native diagnostics."""

    response: dict[str, object]
    success = False
    try:
        parser = _ArgumentParser(add_help=False)
        parser.add_argument("--data-root", required=True)
        parsed = parser.parse_args(arguments)
        root = _path(parsed.data_root)
        raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > _MAX_REQUEST_BYTES or len(raw.splitlines()) != 1:
            raise ValueError("Image settings request exceeds its framing limit.")
        request = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_object, parse_constant=_constant)
        response = handle_request(request, root)
        success = True
    except Exception:
        response = {"error": "image.settings.unavailable"}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
