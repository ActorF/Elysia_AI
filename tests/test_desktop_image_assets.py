"""Verify native-reviewed asset imports, catalog upgrades, and optional LoRA state.

Tiny ordinary fixture files exercise streaming publication and real Windows
file guards without loading a model. Explicit POSIX readiness seams apply only
where a successful native contract is required; import file safety remains real.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Literal, cast

import pytest

import desktop_image_assets as assets
from desktop_image_assets import DesktopImageAssetImportError, copy_reviewed_image_asset
from desktop_image_settings import DesktopImageSettingsStore
from desktop_image_settings_worker import handle_request
from image_generation.gallery import ImageGalleryStore
from tests.image_settings_fixtures import supported_image_preflight
from tests.test_desktop_image_settings_worker import _request


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safetensors(content: bytes, *, checkpoint: bool = False) -> bytes:
    key = "model.diffusion_model.input_blocks.0.0.weight" if checkpoint else "lora_unet_fixture.lora_up.weight"
    header = json.dumps({key: {"dtype": "U8", "shape": [len(content)], "data_offsets": [0, len(content)]}}, separators=(",", ":")).encode("utf-8")
    return len(header).to_bytes(8, "little") + header + content


def _asset(root: Path, name: str, content: bytes) -> dict[str, object]:
    path = root / name
    content = _safetensors(content, checkpoint=name.startswith("model "))
    path.write_bytes(content)
    return {"sourcePath": str(path), "sha256": _digest(content)}


def _import(root: Path, *, first: bool = False) -> tuple[dict[str, object], Path]:
    configured = _request(root)
    data_root = root / "data"
    context = cast(dict[str, object], configured["installation"])
    if not first:
        handle_request(configured, data_root)
    metadata = dict(cast(dict[str, object], context["metadata"]))
    metadata.update(profileId="imported-illustrious", displayName="Reviewed imported model", promptDialect="illustrious-tags")
    checkpoint = _asset(root, "model outside checkpoints.safetensors", b"reviewed imported checkpoint")
    checkpoint["metadata"] = metadata
    lora = _asset(root, "[reviewed] optional.safetensors", b"reviewed optional lora")
    lora.update(targetProfileId="imported-illustrious", loraId="reviewed-style", displayName="Reviewed option", source="https://example.test/lora", license="Fixture only")
    runtime = {key: context[key] for key in ("runtimeRoot", "pythonExecutable", "pythonSha256", "comfyuiMainSha256")}
    return {
        "op": "assets.import", "expectedRevision": 0 if first else 1, "trustedConfirmation": True,
        "checkpoint": checkpoint, "lora": lora, "installation": runtime if first else None,
    }, data_root


def _saved(root: Path) -> Path:
    return root / "workspace" / "settings" / "images.json"


def test_import_appends_profile_and_option_without_changing_existing_default(
    tmp_path: Path, supported_image_preflight: None,
) -> None:
    """Native-picked external files are copied while existing installation identity persists."""

    request, root = _import(tmp_path)
    before = DesktopImageSettingsStore(root).load()
    assert before is not None
    result = handle_request(request, root)
    loaded = DesktopImageSettingsStore(root).load()
    assert loaded is not None
    assert loaded.default_profile_id == before.default_profile_id
    assert loaded.runtime_root == before.runtime_root
    assert loaded.models_root == before.models_root
    assert loaded.profiles.profiles[0] == before.profiles.profiles[0]
    assert result["ready"] is True
    assert result["revision"] == 2
    imported = loaded.profiles.get("imported-illustrious")
    assert imported.loras[0].lora_id == "reviewed-style"
    for key, directory in (("checkpoint", "checkpoints"), ("lora", "loras")):
        source = cast(dict[str, object], request[key])
        original = Path(cast(str, source["sourcePath"]))
        target = loaded.models_root / directory / f"{source['sha256']}.safetensors"
        assert original.read_bytes() == target.read_bytes()
        assert original.stat().st_ino != target.stat().st_ino
        assert not list(target.parent.glob(".image-import-*.tmp"))
    public = json.dumps(result)
    assert str(tmp_path) not in public
    assert imported.checkpoint_sha256 not in public
    assert imported.loras[0].lora_sha256 not in public
    assert cast(list[dict[str, object]], result["profiles"])[1]["loras"] == [{
        "loraId": "reviewed-style", "displayName": "Reviewed option", "available": True,
        "message": "ready", "defaultStrengthModel": 1, "defaultStrengthClip": 1,
    }]


def test_first_external_checkpoint_uses_fixed_application_models_root(
    tmp_path: Path, supported_image_preflight: None,
) -> None:
    """An initial external model never grants its arbitrary parent as a model search root."""

    request, root = _import(tmp_path, first=True)
    result = handle_request(request, root)
    loaded = DesktopImageSettingsStore(root).load()
    assert loaded is not None
    assert result["ready"] is True
    assert loaded.models_root == root / "workspace" / "image-models"
    assert loaded.default_profile_id == "imported-illustrious"
    assert loaded.profiles.profiles[0].checkpoint_name.endswith(".safetensors")
    assert not (root / "workspace" / "gallery").exists()


def test_lora_only_import_attaches_to_explicit_existing_profile(
    tmp_path: Path, supported_image_preflight: None,
) -> None:
    """Adding an option preserves every existing checkpoint and the chosen default."""

    request, root = _import(tmp_path)
    request["checkpoint"] = None
    cast(dict[str, object], request["lora"])["targetProfileId"] = "reviewed-sdxl"
    result = handle_request(request, root)
    loaded = DesktopImageSettingsStore(root).load()
    assert loaded is not None
    assert len(loaded.profiles.profiles) == 1
    assert loaded.profiles.profiles[0].checkpoint_name == "reviewed.safetensors"
    assert loaded.default_profile_id == "reviewed-sdxl"
    assert result["ready"] is True


@pytest.mark.parametrize("fault", ["missing", "changed", "hardlink"])
def test_optional_lora_failure_keeps_base_generation_ready(
    tmp_path: Path, fault: str, supported_image_preflight: None,
) -> None:
    """A missing, changed, or aliased optional asset disables that option alone."""

    request, root = _import(tmp_path)
    handle_request(request, root)
    store = DesktopImageSettingsStore(root)
    installation = store.load()
    assert installation is not None
    lora = installation.profiles.get("imported-illustrious").loras[0]
    path = installation.models_root / "loras" / lora.lora_name
    if fault == "missing":
        path.unlink()
    elif fault == "changed":
        path.write_bytes(b"changed after review")
    else:
        os.link(path, tmp_path / "external alias")
    status = store.status().to_public_dict()
    assert status["ready"] is True
    option = cast(list[dict[str, object]], cast(list[dict[str, object]], status["profiles"])[1]["loras"])[0]
    assert option["available"] is False
    assert option["message"] == {"missing": "missing_lora", "changed": "hash_mismatch", "hardlink": "unavailable"}[fault]


def test_legacy_schema_is_read_without_rewrite_and_upgrades_on_confirmed_save(tmp_path: Path) -> None:
    """Existing installations keep their exact bytes until an explicit versioned save."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    handle_request(request, root)
    path = _saved(root)
    document = json.loads(path.read_bytes())
    document["schema_version"] = 1
    for profile in document["installation"]["profiles"]:
        profile.pop("loras")
    legacy = json.dumps(document).encode("utf-8")
    path.write_bytes(legacy)
    store = DesktopImageSettingsStore(root)
    loaded = store.load()
    assert loaded is not None
    assert loaded.profiles.profiles[0].loras == ()
    assert path.read_bytes() == legacy
    assert "loras" not in cast(list[dict[str, object]], store.status().to_public_dict()["profiles"])[0]
    store.save(loaded, trusted_confirmation=True, expected_revision=1)
    assert json.loads(path.read_bytes())["schema_version"] == 3


def test_managed_models_relocate_with_byte_identical_data_root_copy(
    tmp_path: Path, supported_image_preflight: None,
) -> None:
    """The exact managed marker resolves against the new startup root after a move."""

    request, source_root = _import(tmp_path, first=True)
    handle_request(request, source_root)
    before = _saved(source_root).read_bytes()
    document = json.loads(before)
    assert document["schema_version"] == 3
    assert document["installation"]["models_root"] == {"data_root_relative": "workspace/image-models"}
    destination = tmp_path / "moved-data"
    shutil.copytree(source_root, destination)
    # Renaming the retired source proves no old absolute model reference can
    # accidentally keep readiness green during byte-identical move validation.
    source_root.rename(tmp_path / "retired-data")
    store = DesktopImageSettingsStore(destination)
    loaded = store.load()
    assert loaded is not None
    assert loaded.models_root == destination / "workspace" / "image-models"
    assert _saved(destination).read_bytes() == before
    assert store.status().state == "ready"


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_external_models_remain_absolute_without_guessing_relocation(tmp_path: Path, version: int) -> None:
    """Older absolute installations preserve their meaning and upgrade only on save."""

    configured = _request(tmp_path)
    root = tmp_path / "data"
    handle_request(configured, root)
    path = _saved(root)
    document = json.loads(path.read_bytes())
    document["schema_version"] = version
    if version == 1:
        for profile in document["installation"]["profiles"]:
            profile.pop("loras")
    before = json.dumps(document).encode("utf-8")
    path.write_bytes(before)
    moved = tmp_path / "moved-data"
    shutil.copytree(root, moved)
    loaded = DesktopImageSettingsStore(moved).load()
    assert loaded is not None
    assert loaded.models_root == tmp_path / "models"
    assert _saved(moved).read_bytes() == before


@pytest.mark.parametrize("marker", [
    {"data_root_relative": "../outside"}, {"data_root_relative": "workspace/gallery"},
    {"data_root_relative": "/absolute"}, {"data_root_relative": "workspace/image-models", "extra": True},
])
def test_managed_location_marker_cannot_authorize_arbitrary_paths(tmp_path: Path, marker: dict[str, object]) -> None:
    """Only the exact fixed managed model root is a supported relocatable marker."""

    request = _request(tmp_path)
    root = tmp_path / "data"
    handle_request(request, root)
    path = _saved(root)
    document = json.loads(path.read_bytes())
    document["installation"]["models_root"] = marker
    path.write_text(json.dumps(document), encoding="utf-8")
    assert DesktopImageSettingsStore(root).status().state == "invalid"


@pytest.mark.parametrize("mutation", ["duplicate-profile", "wrong-target", "unknown-target", "empty", "runtime-override", "first-no-runtime", "first-no-checkpoint", "extra-path", "truthy", "stale"])
def test_invalid_import_contract_does_not_change_settings_or_copy_assets(tmp_path: Path, mutation: str) -> None:
    """Ambiguous targets, stale consent, and private DTO extensions fail before copying."""

    first = mutation in ("first-no-runtime", "first-no-checkpoint")
    request, root = _import(tmp_path, first=first)
    before = _saved(root).read_bytes() if _saved(root).exists() else None
    checkpoint = cast(dict[str, object], request["checkpoint"])
    lora = cast(dict[str, object], request["lora"])
    if mutation == "duplicate-profile":
        cast(dict[str, object], checkpoint["metadata"])["profileId"] = "reviewed-sdxl"
    elif mutation == "wrong-target":
        lora["targetProfileId"] = "reviewed-sdxl"
    elif mutation == "unknown-target":
        request["checkpoint"] = None
        lora["targetProfileId"] = "unknown-profile"
    elif mutation == "empty":
        request.update(checkpoint=None, lora=None)
    elif mutation == "runtime-override":
        request["installation"] = {"runtimeRoot": str(tmp_path)}
    elif mutation == "first-no-runtime":
        request["installation"] = None
    elif mutation == "first-no-checkpoint":
        request["checkpoint"] = None
    elif mutation == "extra-path":
        lora["modelPath"] = str(tmp_path)
    elif mutation == "truthy":
        request["trustedConfirmation"] = 1
    else:
        request["expectedRevision"] = 0
    with pytest.raises(Exception):
        handle_request(request, root)
    assert (_saved(root).read_bytes() if _saved(root).exists() else None) == before
    assert not (root / "workspace" / "image-models").exists()
    assert not (tmp_path / "models" / "loras").exists()


@pytest.mark.parametrize("fault", ["wrong-pin", "hardlink", "suffix", "empty", "oversized", "conflicting-target"])
def test_copy_refuses_unreviewed_or_aliased_bytes_without_overwrite(tmp_path: Path, fault: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Source and existing destination safety failures preserve every original byte."""

    source = tmp_path / "native picked.safetensors"
    content = _safetensors(b"reviewed bytes")
    source.write_bytes(content)
    digest = _digest(content)
    models = tmp_path / "models"
    if fault == "wrong-pin":
        digest = "0" * 64
    elif fault == "hardlink":
        os.link(source, tmp_path / "source alias")
    elif fault == "suffix":
        source = source.rename(source.with_suffix(".bin"))
    elif fault == "empty":
        source.write_bytes(b"")
    elif fault == "oversized":
        monkeypatch.setattr(assets, "_MAX_ASSET_BYTES", 2)
    else:
        (models / "loras").mkdir(parents=True)
        (models / "loras" / f"{digest}.safetensors").write_bytes(b"foreign conflicting bytes")
    with pytest.raises(DesktopImageAssetImportError) as captured:
        copy_reviewed_image_asset(source, models, kind="loras", sha256=digest)
    assert str(tmp_path) not in str(captured.value)
    assert captured.value.__context__ is None
    assert source.read_bytes() == (b"" if fault == "empty" else content)
    assert not list(models.glob("**/.image-import-*.tmp"))
    if fault == "conflicting-target":
        assert (models / "loras" / f"{digest}.safetensors").read_bytes() == b"foreign conflicting bytes"


def test_copy_reuses_only_identical_content_and_handles_late_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent identical publication is idempotent without replacing its inode."""

    source = tmp_path / "picked.safetensors"
    source.write_bytes(_safetensors(b"reviewed content"))
    digest = _digest(source.read_bytes())
    models = tmp_path / "models"
    published: list[int] = []

    def _publish_elsewhere(temporary: Path, destination: Path) -> None:
        destination.write_bytes(temporary.read_bytes())
        published.append(destination.stat().st_ino)
        raise FileExistsError("competing publication")

    monkeypatch.setattr(assets, "_rename_exclusive", _publish_elsewhere)
    name = copy_reviewed_image_asset(source, models, kind="loras", sha256=digest)
    assert (models / "loras" / name).stat().st_ino == published[0]
    assert copy_reviewed_image_asset(source, models, kind="loras", sha256=digest) == name
    assert len(published) == 1
    assert not list(models.glob("**/.image-import-*.tmp"))


def test_failed_publication_cleans_only_owned_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed atomic publish leaves no partial model and preserves unrelated files."""

    source = tmp_path / "picked.safetensors"
    source.write_bytes(_safetensors(b"reviewed content"))
    models = tmp_path / "models"
    (models / "loras").mkdir(parents=True)
    foreign = models / "loras" / ".image-import-foreign.tmp"
    foreign.write_bytes(b"retain")

    def _fail(source: Path, destination: Path) -> None:
        raise OSError("private source path")

    monkeypatch.setattr(assets, "_rename_exclusive", _fail)
    with pytest.raises(DesktopImageAssetImportError):
        copy_reviewed_image_asset(source, models, kind="loras", sha256=_digest(source.read_bytes()))
    assert list((models / "loras").iterdir()) == [foreign]
    assert foreign.read_bytes() == b"retain"


@pytest.mark.parametrize("linked", ["source", "destination"])
def test_linked_ancestors_cannot_redirect_import_bytes(tmp_path: Path, linked: str) -> None:
    """A native path's ordinary leaf never authorizes a linked source or model parent."""

    real = tmp_path / "ordinary"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("The host does not grant local symlink creation.")
    source = real / "picked.safetensors"
    source.write_bytes(_safetensors(b"reviewed content"))
    models = tmp_path / "models"
    if linked == "source":
        source = alias / source.name
    else:
        models = alias / "models"
    with pytest.raises(DesktopImageAssetImportError):
        copy_reviewed_image_asset(source, models, kind="loras", sha256=_digest(source.read_bytes()))
    assert not list(real.glob("**/.image-import-*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="Windows real source guards prevent this mutation before copying.")
def test_changed_source_after_review_never_publishes_partial_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Descriptor and content comparison detects a source changed after its reviewed hash."""

    source = tmp_path / "picked.safetensors"
    original = _safetensors(b"reviewed content")
    source.write_bytes(original)
    models = tmp_path / "models"
    review = assets._review_header

    def _change_after_header(path: Path, size: int, *, kind: Literal["checkpoints", "loras"]) -> None:
        review(path, size, kind=kind)
        source.write_bytes(_safetensors(b"changed content"))

    monkeypatch.setattr(assets, "_review_header", _change_after_header)
    with pytest.raises(DesktopImageAssetImportError):
        copy_reviewed_image_asset(source, models, kind="loras", sha256=_digest(original))
    assert not list(models.glob("**/*.safetensors"))
    assert not list(models.glob("**/.image-import-*.tmp"))


def test_catalog_conflict_retains_verified_assets_but_preserves_newer_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Long copies cannot overwrite a concurrent save or delete safely published bytes."""

    request, root = _import(tmp_path)
    request["lora"] = None
    original = assets.copy_reviewed_image_asset
    store = DesktopImageSettingsStore(root)
    old = store.load()
    assert old is not None
    new_bytes: list[bytes] = []

    def _copy_then_change(source: Path, models: Path, *, kind: object, sha256: str) -> str:
        result = original(source, models, kind=cast(Literal["checkpoints", "loras"], kind), sha256=sha256)
        store.save(old, trusted_confirmation=True, expected_revision=1)
        new_bytes.append(_saved(root).read_bytes())
        return result

    monkeypatch.setattr(assets, "copy_reviewed_image_asset", _copy_then_change)
    with pytest.raises(DesktopImageAssetImportError):
        handle_request(request, root)
    assert _saved(root).read_bytes() == new_bytes[0]
    source = cast(dict[str, object], request["checkpoint"])
    assert (old.models_root / "checkpoints" / f"{source['sha256']}.safetensors").exists()
    loaded = store.load()
    assert loaded is not None
    assert len(loaded.profiles.profiles) == 1


def test_live_gallery_lease_blocks_import_without_touching_index(tmp_path: Path) -> None:
    """Another Backend's lease excludes installation writes and preserves Gallery bytes."""

    request, root = _import(tmp_path)
    with ImageGalleryStore(root / "workspace" / "gallery"):
        index = root / "workspace" / "gallery" / "index.json"
        before = index.read_bytes()
        settings_before = _saved(root).read_bytes()
        with pytest.raises(DesktopImageAssetImportError):
            handle_request(request, root)
        assert index.read_bytes() == before
        assert _saved(root).read_bytes() == settings_before
    handle_request(request, root)
    assert index.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Actual Win32 native source and directory guards.")
def test_native_copy_holds_source_and_target_directory_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows denies source mutation and target-root rename until publication completes."""

    source = tmp_path / "picked.safetensors"
    source.write_bytes(_safetensors(b"reviewed content"))
    models = tmp_path / "models"
    original = assets._rename_exclusive

    def _guarded_publish(temporary: Path, destination: Path) -> None:
        with pytest.raises(PermissionError):
            source.write_bytes(b"changed")
        with pytest.raises(PermissionError):
            destination.parent.rename(models / "renamed-loras")
        original(temporary, destination)

    monkeypatch.setattr(assets, "_rename_exclusive", _guarded_publish)
    name = copy_reviewed_image_asset(source, models, kind="loras", sha256=_digest(source.read_bytes()))
    assert (models / "loras" / name).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("fault", ["short-prefix", "oversized-header", "duplicate", "offset-range", "overlap", "shape", "checkpoint-as-lora", "lora-as-checkpoint"])
def test_safetensors_header_rejects_malformed_layout_or_explicit_role_swap(tmp_path: Path, fault: str) -> None:
    """Correct SHA pins cannot authorize malformed tensors or swapped asset categories."""

    kind: Literal["checkpoints", "loras"] = "loras"
    key = "lora_unet_fixture.lora_up.weight"
    tensor: dict[str, object] = {"dtype": "U8", "shape": [2], "data_offsets": [0, 2]}
    header = json.dumps({key: tensor}).encode("utf-8")
    payload = b"ab"
    if fault == "short-prefix":
        content = b"short"
    elif fault == "oversized-header":
        content = (16 * 1024 * 1024 + 1).to_bytes(8, "little") + b"{}"
    elif fault == "checkpoint-as-lora":
        content = _safetensors(payload, checkpoint=True)
    elif fault == "lora-as-checkpoint":
        content = _safetensors(payload)
        kind = "checkpoints"
    else:
        if fault == "duplicate":
            header = ('{"' + key + '":' + json.dumps(tensor) + ',"' + key + '":' + json.dumps(tensor) + '}').encode("utf-8")
        elif fault == "offset-range":
            tensor["data_offsets"] = [0, 3]
            header = json.dumps({key: tensor}).encode("utf-8")
        elif fault == "overlap":
            header = json.dumps({key: tensor, "lora_unet_second.lora_down.weight": tensor}).encode("utf-8")
        elif fault == "shape":
            tensor["shape"] = [3]
            header = json.dumps({key: tensor}).encode("utf-8")
        content = len(header).to_bytes(8, "little") + header + payload
    source = tmp_path / "picked.safetensors"
    source.write_bytes(content)
    models = tmp_path / "models"
    with pytest.raises(DesktopImageAssetImportError):
        copy_reviewed_image_asset(source, models, kind=kind, sha256=_digest(content))
    assert source.read_bytes() == content
    assert not list(models.glob("**/*.safetensors"))
    assert not list(models.glob("**/.image-import-*.tmp"))


@pytest.mark.parametrize("kind", ["checkpoints", "loras"])
def test_unknown_reviewed_tensor_family_is_not_rejected_by_name(tmp_path: Path, kind: Literal["checkpoints", "loras"]) -> None:
    """The bounded structural review does not invent an exhaustive architecture key allowlist."""

    header = b'{"reviewed.future_family":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}'
    content = len(header).to_bytes(8, "little") + header + b"a"
    source = tmp_path / "picked.safetensors"
    source.write_bytes(content)
    models = tmp_path / "models"
    key = copy_reviewed_image_asset(source, models, kind=kind, sha256=_digest(content))
    assert (models / kind / key).read_bytes() == content
