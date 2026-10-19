"""Inspect native-picked assets and import reviewed bytes into a private catalog.

Only Electron Main's native installation worker calls these APIs. Source paths
and pins never belong to a generation request. Imports preserve user originals,
publish without overwriting existing files, and save the catalog with revision
comparison after copying. Verified unreferenced files may remain after a failed
catalog save; retaining immutable bytes is safer than deleting a concurrent
import's adopted asset. Only this call's still-identical temporary is removed.
Read-only inspection derives display metadata without trusting embedded license
claims or loading tensors; publication still requires separate native consent.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import mkstemp
from typing import Iterator, Literal
import unicodedata

from desktop_image_recovery import _gallery_lease, _rename_exclusive
from desktop_image_settings import (
    DesktopImageSettingsStore, ImageSettingsStatus, TrustedImageInstallation,
    _absolute_path, _identity, _mkdir, _read_regular, _snapshot,
)
from image_generation.managed_comfyui import _require_fixed_local_volume
from image_generation.domain import validate_public_id
from image_generation.profiles import ModelLoraProfile, ModelProfile, ModelProfileCatalog
from voice import _windows_file_guard as native_guard


_MAX_ASSET_BYTES = 64 * 1024 * 1024 * 1024
_MAX_HEADER_BYTES = 16 * 1024 * 1024
_FAILURE = "Reviewed image assets could not be imported safely. Existing settings and original files remain preserved."


@dataclass(frozen=True, slots=True)
class _KnownAsset:
    """Bind reviewed publisher metadata to exact bytes, never to a filename hint."""

    kind: Literal["checkpoint", "lora"]
    logical_id: str
    display_name: str
    source: str
    license: str
    prompt_dialect: str = "sdxl-natural"


_KNOWN_ASSETS: dict[str, _KnownAsset] = {
    "63e5c28bf8ae348136c9f9cdbfde14d8a4a5daa13306e09a7752104b2fae2006": _KnownAsset(
        "checkpoint", "wai-illustrious-v80", "WAI Illustrious SDXL v8.0",
        "https://civitai.com/models/827184?modelVersionId=1183765 (WAI0731)",
        "Publisher terms reviewed 2026-10-18: commercial use Image and RentCivit; derivatives allowed. Review the linked version before redistribution.",
        "illustrious-tags",
    ),
    "06ba4cd1cfb9b2e9ce181299f2546fc1db4cb64bab8a3c8ceb0aa108075a9e18": _KnownAsset(
        "lora", "hikari-elysia-il-v1", "Hikari Elysia IL v1",
        "https://civitai.com/models/774891?modelVersionId=1318587 (hi_ka_ri)",
        "Publisher terms reviewed 2026-10-18: credit hi_ka_ri; commercial use RentCivit only; no derivatives or reposting. Review the linked version before use or redistribution.",
    ),
    "0b1876635cfc93bbab11731a224472a5316b05cc4dd3213370a868d0e944c18b": _KnownAsset(
        "lora", "honkai-starrail-style-v1", "Honkai Star Rail Style v1",
        "https://civitai.com/models/1636274?modelVersionId=1852115 (FlyingOyster)",
        "Publisher terms reviewed 2026-10-18: commercial use Image, RentCivit, Rent, Sell and SellMerge; derivatives allowed; credit optional. Review the linked version before use or redistribution.",
    ),
}


def _asset_display_name(source: Path, *, kind: Literal["checkpoint", "lora"]) -> str:
    """Turn a local basename into a short label without publishing path syntax."""

    name = unicodedata.normalize("NFC", source.stem).replace("_", " ")
    name = "".join(
        " " if character in "\\/:" or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        else character for character in name
    )
    # The UI measures labels as UTF-16, so a conservative code-unit budget
    # also accepts astral characters without a Main/worker length mismatch.
    name = " ".join(name.split()).strip(" .")
    while len(name.encode("utf-16-le")) > 160:
        name = name[:-1]
    return name.rstrip() or ("Local checkpoint" if kind == "checkpoint" else "Local LoRA")


def _asset_metadata(
    source: Path, digest: str, *, kind: Literal["checkpoint", "lora"], target_profile_id: str | None,
) -> dict[str, object]:
    known = _KNOWN_ASSETS.get(digest)
    if known is not None and known.kind != kind:
        raise DesktopImageAssetImportError(_FAILURE)
    logical_id = known.logical_id if known is not None else f"{'model' if kind == 'checkpoint' else 'lora'}-{digest[:16]}"
    display_name = known.display_name if known is not None else _asset_display_name(source, kind=kind)
    provenance = known.source if known is not None else "Local user-selected safetensors; publisher identity not verified."
    license_text = known.license if known is not None else "Publisher usage terms are unknown. Confirm usage rights before use or redistribution."
    # Embedded strings and filenames are untrusted labels, not evidence of
    # provenance or permission. Only a reviewed exact digest earns a source.
    if kind == "checkpoint":
        return {
            "profileId": logical_id, "displayName": display_name,
            "promptDialect": known.prompt_dialect if known is not None else "sdxl-natural",
            "checkpointSource": provenance, "checkpointLicense": license_text,
            "defaultSizePresetId": "square",
        }
    return {
        "loraId": logical_id, "displayName": display_name, "targetProfileId": target_profile_id,
        "source": provenance, "license": license_text,
    }


class DesktopImageAssetImportError(RuntimeError):
    """Report a fixed import failure without source paths or native diagnostics."""


@dataclass(frozen=True, slots=True)
class ImageCheckpointImport:
    """Pair a native-picked source with one reviewed logical checkpoint profile."""

    source_path: Path
    profile: ModelProfile


@dataclass(frozen=True, slots=True)
class ImageLoraImport:
    """Bind one reviewed LoRA source to its explicit compatible parent profile."""

    source_path: Path
    target_profile_id: str
    lora: ModelLoraProfile


@contextmanager
def _directory_guards(paths: tuple[Path, ...]) -> Iterator[None]:
    """Keep Windows directory identities fixed while adding their new children."""

    handles: list[int] = []
    try:
        if os.name == "nt":
            seen: set[str] = set()
            for path in paths:
                _require_fixed_local_volume(path)
                for component in (*reversed(path.parents), path):
                    key = os.path.normcase(str(component))
                    if key not in seen:
                        checked = native_guard._open_directory(str(component), handles)
                        # Windows ignores delete sharing for attribute-only
                        # handles during some directory renames. Requesting
                        # FILE_LIST_DIRECTORY makes denial effective; compare
                        # both handles before relying on this stronger pin.
                        kernel = native_guard._kernel32
                        assert kernel is not None
                        raw = kernel.CreateFileW(str(component), 0x81, 3, None, 3, 0x02200000, None)
                        handle = native_guard._handle_value(raw)
                        if handle in (0, native_guard._INVALID_HANDLE_VALUE):
                            raise DesktopImageAssetImportError(_FAILURE)
                        native_guard._adopt_new_handle(handle, handles)
                        if native_guard._snapshot(handle).identity != native_guard._snapshot(checked).identity:
                            raise DesktopImageAssetImportError(_FAILURE)
                        seen.add(key)
        yield
    finally:
        if handles and not native_guard._rollback_handles(reversed(handles)):
            raise DesktopImageAssetImportError(_FAILURE)


def _pin(value: object) -> str:
    if type(value) is not str or len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise DesktopImageAssetImportError(_FAILURE)
    return value


def _existing(path: Path, digest: str) -> bool:
    try:
        metadata = _snapshot(path, directory=False)
    except FileNotFoundError:
        return False
    if metadata.st_nlink != 1 or _read_regular(path) != digest:
        raise DesktopImageAssetImportError(_FAILURE)
    return True


def _header_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise DesktopImageAssetImportError(_FAILURE)
        result[name] = value
    return result


def _review_header(source: Path, size: int, *, kind: Literal["checkpoints", "loras"]) -> None:
    """Validate bounded safetensors metadata and reject an unambiguous role swap.

    This is a structural check, not a model interpreter or compatibility claim.
    Unknown tensor families remain acceptable reviewed inputs; familiar full
    checkpoint keys cannot silently masquerade as LoRA factors or vice versa.
    The selected runtime still verifies its actual architecture when loading.
    """

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(source, flags), "rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise DesktopImageAssetImportError(_FAILURE)
        length = int.from_bytes(prefix, "little")
        if not 2 <= length <= min(_MAX_HEADER_BYTES, size - 8):
            raise DesktopImageAssetImportError(_FAILURE)
        encoded = stream.read(length)
        if len(encoded) != length:
            raise DesktopImageAssetImportError(_FAILURE)
    raw = json.loads(encoded.decode("utf-8", errors="strict"), object_pairs_hook=_header_object)
    if type(raw) is not dict:
        raise DesktopImageAssetImportError(_FAILURE)
    ranges: list[tuple[int, int]] = []
    checkpoint_family = False
    lora_family = False
    for key, tensor in raw.items():
        if key == "__metadata__":
            if type(tensor) is not dict or any(type(item) is not str for item in tensor.values()):
                raise DesktopImageAssetImportError(_FAILURE)
            continue
        if type(tensor) is not dict or set(tensor) != {"dtype", "shape", "data_offsets"}:
            raise DesktopImageAssetImportError(_FAILURE)
        dtype, shape, offsets = tensor["dtype"], tensor["shape"], tensor["data_offsets"]
        if (type(dtype) is not str or re.fullmatch(r"[A-Z][A-Z0-9_.]{0,31}", dtype) is None
                or type(shape) is not list or len(shape) > 32
                or any(type(edge) is not int or not 0 <= edge <= 9_007_199_254_740_991 for edge in shape)
                or type(offsets) is not list or len(offsets) != 2
                or any(type(offset) is not int for offset in offsets)):
            raise DesktopImageAssetImportError(_FAILURE)
        begin, end = offsets
        if not 0 <= begin <= end <= size - 8 - length:
            raise DesktopImageAssetImportError(_FAILURE)
        item_bytes = {"BOOL": 1, "U8": 1, "I8": 1, "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
                      "I32": 4, "U32": 4, "F32": 4, "I64": 8, "U64": 8, "F64": 8}.get(dtype)
        if item_bytes is not None:
            elements = 1
            for edge in shape:
                elements *= edge
                if elements > _MAX_ASSET_BYTES:
                    raise DesktopImageAssetImportError(_FAILURE)
            if elements * item_bytes != end - begin:
                raise DesktopImageAssetImportError(_FAILURE)
        ranges.append((begin, end))
        lowered = key.lower()
        factor = any(part in lowered for part in ("lora_up", "lora_down", "lora_a.", "lora_b.", "lora_mid"))
        lora_family = lora_family or factor or lowered.startswith(("lora_unet_", "lora_te_", "lora_te1_", "lora_te2_"))
        checkpoint_family = checkpoint_family or (not factor and lowered.startswith((
            "model.diffusion_model.", "conditioner.embedders.", "first_stage_model.", "diffusion_model.",
        )))
    if not ranges or (kind == "checkpoints" and lora_family) or (kind == "loras" and checkpoint_family):
        raise DesktopImageAssetImportError(_FAILURE)
    # Sorting only bounded header records catches overlapping storage without
    # reading or allocating tensor payloads from a multi-gigabyte source.
    previous_end = 0
    for begin, end in sorted(ranges):
        if begin < previous_end:
            raise DesktopImageAssetImportError(_FAILURE)
        previous_end = end


def inspect_image_asset(
    source: Path, *, kind: Literal["checkpoint", "lora"], sha256: str, target_profile_id: str | None,
) -> dict[str, object]:
    """Derive private import metadata from one stable native-picked safetensors.

    No tensor is loaded and no file is copied. The bounded header establishes
    the selected asset role; the complete SHA-256 binds the reviewed bytes.
    Publisher identity and terms come only from an exact reviewed digest.
    Unknown files receive explicit unknown provenance instead of claims from
    their untrusted embedded metadata. Main must still obtain native consent
    before the separate import request revalidates and publishes the source.
    """

    try:
        _absolute_path(source)
        digest = _pin(sha256)
        if (kind not in ("checkpoint", "lora") or source.suffix.lower() != ".safetensors"
                or (kind == "checkpoint" and target_profile_id is not None)):
            raise DesktopImageAssetImportError(_FAILURE)
        if kind == "lora":
            validate_public_id(target_profile_id, "target_profile_id")
        before = _snapshot(source, directory=False)
        if before.st_nlink != 1 or not 0 < before.st_size <= _MAX_ASSET_BYTES:
            raise DesktopImageAssetImportError(_FAILURE)
        with ExitStack() as stack:
            if os.name == "nt":
                stack.enter_context(native_guard.WindowsReadOnlyFileGuardSet.acquire((
                    native_guard.GuardedFileDeclaration(source, before.st_size, digest),
                )))
            elif _read_regular(source) != digest:
                raise DesktopImageAssetImportError(_FAILURE)
            _review_header(source, before.st_size, kind="checkpoints" if kind == "checkpoint" else "loras")
            after = _snapshot(source, directory=False)
            if _identity(before) != _identity(after) or after.st_nlink != 1:
                raise DesktopImageAssetImportError(_FAILURE)
            # Production Windows holds its hash-verifying handle throughout.
            # POSIX contract hosts repeat the hash after the separate header
            # read so a pathname replacement cannot attach metadata to bytes
            # that were never reviewed together.
            if os.name != "nt" and _read_regular(source) != digest:
                raise DesktopImageAssetImportError(_FAILURE)
            return _asset_metadata(source, digest, kind=kind, target_profile_id=target_profile_id)
    except Exception:
        pass
    raise DesktopImageAssetImportError(_FAILURE)


def _copy_reviewed_image_asset(
    source: Path, models_root: Path, *, kind: Literal["checkpoints", "loras"], sha256: str,
) -> str:
    """Copy a stable ordinary safetensors file and return its digest-based key.

    Native Windows guards deny source writes and directory renames throughout
    the copy. The fallback used by contract tests compares the open descriptor
    and path before and after reading. Exclusive same-directory publication
    never replaces a target introduced by a competing importer.
    """

    temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        _absolute_path(source)
        _absolute_path(models_root)
        digest = _pin(sha256)
        if kind not in ("checkpoints", "loras") or source.suffix.lower() != ".safetensors":
            raise DesktopImageAssetImportError(_FAILURE)
        before = _snapshot(source, directory=False)
        if before.st_nlink != 1 or not 0 < before.st_size <= _MAX_ASSET_BYTES:
            raise DesktopImageAssetImportError(_FAILURE)
        _mkdir(models_root)
        destination_root = models_root / kind
        _mkdir(destination_root)
        name = f"{digest}.safetensors"
        destination = destination_root / name
        with ExitStack() as stack:
            stack.enter_context(_directory_guards((destination_root,)))
            if os.name == "nt":
                stack.enter_context(native_guard.WindowsReadOnlyFileGuardSet.acquire((
                    native_guard.GuardedFileDeclaration(source, before.st_size, digest),
                )))
            elif _read_regular(source) != digest:
                raise DesktopImageAssetImportError(_FAILURE)
            _review_header(source, before.st_size, kind=kind)
            if _existing(destination, digest):
                return name
            descriptor, raw_name = mkstemp(prefix=".image-import-", suffix=".tmp", dir=destination_root)
            temporary = Path(raw_name)
            initial = os.fstat(descriptor)
            temporary_identity = initial.st_dev, initial.st_ino
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            content = hashlib.sha256()
            total = 0
            with os.fdopen(descriptor, "wb") as output, os.fdopen(os.open(source, flags), "rb") as input_stream:
                if _identity(os.fstat(input_stream.fileno())) != _identity(before):
                    raise DesktopImageAssetImportError(_FAILURE)
                while chunk := input_stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > before.st_size:
                        raise DesktopImageAssetImportError(_FAILURE)
                    content.update(chunk)
                    output.write(chunk)
                if _identity(os.fstat(input_stream.fileno())) != _identity(before):
                    raise DesktopImageAssetImportError(_FAILURE)
                output.flush()
                os.fsync(output.fileno())
            if (_identity(_snapshot(source, directory=False)) != _identity(before)
                    or total != before.st_size or content.hexdigest() != digest
                    or not _existing(temporary, digest)):
                raise DesktopImageAssetImportError(_FAILURE)
            _snapshot(destination_root, directory=True)
            try:
                _rename_exclusive(temporary, destination)
            except Exception:
                # Another confirmed import may have published exactly these
                # bytes. Reuse only its reverified single-link ordinary file.
                if not _existing(destination, digest):
                    raise
            if not _existing(destination, digest):
                raise DesktopImageAssetImportError(_FAILURE)
            return name
    except Exception:
        raise DesktopImageAssetImportError(_FAILURE) from None
    finally:
        if temporary is not None:
            try:
                metadata = _snapshot(temporary, directory=False)
                if (metadata.st_dev, metadata.st_ino) == temporary_identity and metadata.st_nlink == 1:
                    temporary.unlink()
            except FileNotFoundError:
                pass


def copy_reviewed_image_asset(
    source: Path, models_root: Path, *, kind: Literal["checkpoints", "loras"], sha256: str,
) -> str:
    """Publish a reviewed ordinary model without exposing private failure details.

    The original remains unchanged. A digest-based existing target is reused
    only after verification; an unexpected target is never replaced. The native
    worker's explicit consent, stable source pin, and bounded temporary cleanup
    remain independent of later generation requests.
    """

    try:
        return _copy_reviewed_image_asset(source, models_root, kind=kind, sha256=sha256)
    except Exception:
        pass
    raise DesktopImageAssetImportError(_FAILURE)


def _require_missing_asset(models_root: Path, *, kind: Literal["checkpoints", "loras"], name: str) -> None:
    """Admit restoration only for an absent leaf below ordinary trusted parents."""

    try:
        _snapshot(models_root / kind / name, directory=False)
    except FileNotFoundError:
        return
    # Existing good bytes, bad hashes, aliases, and unexpected filesystem types
    # are never a restoration invitation. Publication still uses the ordinary
    # no-overwrite importer if another writer creates the missing file later.
    raise DesktopImageAssetImportError(_FAILURE)


def import_image_assets(
    data_root: Path, *, checkpoint: ImageCheckpointImport | None, lora: ImageLoraImport | None,
    installation: TrustedImageInstallation | None, expected_revision: int, trusted_confirmation: bool,
) -> ImageSettingsStatus:
    """Append assets or restore exact missing bytes without changing declarations.

    First installation uses only ``workspace/image-models`` beneath the fixed
    startup-owned data root. Existing installations retain their reviewed model
    root. Main must stop and join its Backend before this operation; when an
    existing Gallery is present its exact lease also excludes other Backends.
    No Gallery files are rewritten or recovered by this import.

    A repeated ID can restore only the configured SHA-256 and digest filename
    when that exact destination is absent. Its existing metadata, options, and
    defaults remain authoritative. Existing files are never overwritten, even
    if their bytes are wrong; repairing changed assets requires a separate
    explicitly reviewed operation rather than a duplicate-ID import.
    """

    if (trusted_confirmation is not True or type(expected_revision) is not int
            or not 0 <= expected_revision <= 9_007_199_254_740_991
            or (checkpoint is None and lora is None)):
        raise DesktopImageAssetImportError(_FAILURE)
    try:
        _absolute_path(data_root)
        store = DesktopImageSettingsStore(data_root)
        revision, previous = store.load_snapshot()
        if revision != expected_revision:
            raise DesktopImageAssetImportError(_FAILURE)
        if previous is None:
            if checkpoint is None or type(installation) is not TrustedImageInstallation:
                raise DesktopImageAssetImportError(_FAILURE)
            if (installation.models_root != data_root / "workspace" / "image-models"
                    or installation.profiles.profiles != (checkpoint.profile,)
                    or installation.default_profile_id != checkpoint.profile.profile_id):
                raise DesktopImageAssetImportError(_FAILURE)
            base = installation
            profiles: tuple[ModelProfile, ...] = ()
        else:
            if installation is not None:
                raise DesktopImageAssetImportError(_FAILURE)
            base = previous
            profiles = previous.profiles.profiles
        if checkpoint is not None:
            if (type(checkpoint) is not ImageCheckpointImport or checkpoint.profile.loras
                    or checkpoint.profile.checkpoint_name != f"{checkpoint.profile.checkpoint_sha256}.safetensors"):
                raise DesktopImageAssetImportError(_FAILURE)
            existing_profile = next((item for item in profiles if item.profile_id == checkpoint.profile.profile_id), None)
            if existing_profile is not None:
                if (existing_profile.checkpoint_sha256 != checkpoint.profile.checkpoint_sha256
                        or existing_profile.checkpoint_name != checkpoint.profile.checkpoint_name):
                    raise DesktopImageAssetImportError(_FAILURE)
                _require_missing_asset(base.models_root, kind="checkpoints", name=existing_profile.checkpoint_name)
            else:
                if len(profiles) >= 8:
                    raise DesktopImageAssetImportError(_FAILURE)
                profiles += (checkpoint.profile,)
        if lora is not None:
            if (type(lora) is not ImageLoraImport
                    or lora.lora.lora_name != f"{lora.lora.lora_sha256}.safetensors"
                    or (checkpoint is not None and lora.target_profile_id != checkpoint.profile.profile_id)):
                raise DesktopImageAssetImportError(_FAILURE)
            target = ModelProfileCatalog(profiles).get(lora.target_profile_id)
            existing_lora = next((item for item in target.loras if item.lora_id == lora.lora.lora_id), None)
            if existing_lora is not None:
                if (existing_lora.lora_sha256 != lora.lora.lora_sha256
                        or existing_lora.lora_name != lora.lora.lora_name):
                    raise DesktopImageAssetImportError(_FAILURE)
                _require_missing_asset(base.models_root, kind="loras", name=existing_lora.lora_name)
            else:
                # Constructing the replacement validates architecture, unique
                # asset names, and the option bound before any source is copied.
                updated = replace(target, loras=target.loras + (lora.lora,))
                profiles = tuple(updated if item is target else item for item in profiles)
        revised = replace(base, profiles=ModelProfileCatalog(profiles))
        with ExitStack() as stack:
            gallery = data_root / "workspace" / "gallery"
            if gallery.exists() or gallery.is_symlink():
                stack.enter_context(_gallery_lease(data_root))
            if checkpoint is not None:
                name = copy_reviewed_image_asset(checkpoint.source_path, base.models_root, kind="checkpoints", sha256=checkpoint.profile.checkpoint_sha256)
                if name != checkpoint.profile.checkpoint_name:
                    raise DesktopImageAssetImportError(_FAILURE)
            if lora is not None:
                name = copy_reviewed_image_asset(lora.source_path, base.models_root, kind="loras", sha256=lora.lora.lora_sha256)
                if name != lora.lora.lora_name:
                    raise DesktopImageAssetImportError(_FAILURE)
            return store.save(revised, trusted_confirmation=True, expected_revision=expected_revision)
    except Exception:
        pass
    raise DesktopImageAssetImportError(_FAILURE)


__all__ = ["DesktopImageAssetImportError", "ImageCheckpointImport", "ImageLoraImport", "copy_reviewed_image_asset", "import_image_assets", "inspect_image_asset"]
