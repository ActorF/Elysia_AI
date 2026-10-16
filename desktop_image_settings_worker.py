"""Bridge one private Main-owned image installation request to trusted settings.

The worker accepts a bounded single NDJSON request on stdin and writes one
path-free status on stdout. It never launches ComfyUI, downloads dependencies,
or accepts a generation workflow. Paths and reviewed digests arrive only from
Electron Main's native picker and explicit installation confirmation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import NoReturn, Sequence, cast

from desktop_image_settings import DesktopImageSettingsStore, TrustedImageInstallation
from image_generation.profiles import (
    ImageSizePreset, ModelArchitecture, ModelProfile, ModelProfileCatalog, PromptDialect,
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
