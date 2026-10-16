"""Persist trusted local-image installation settings and report safe readiness.

Only the desktop application's installation flow may create these settings.
Generation requests continue to carry logical profile and preset identifiers;
interpreter, model, and runtime paths never enter the renderer-facing summary.
Readiness hashes local files without starting ComfyUI or loading GPU weights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
from tempfile import mkstemp
from threading import RLock
from typing import Literal, cast

from config.data_layout import ProductionDataLayout
from config.desktop_settings import desktop_settings_file_lock
from desktop_image_recovery import image_recovery_required
from image_generation.application import ImageGenerationApplicationService
from image_generation.composition import create_managed_image_application_service
from image_generation.exceptions import ImageGenerationUnavailableError
from image_generation.managed_comfyui import ManagedComfyUiConfig, ManagedComfyUiRuntime
from image_generation.python_environment import PythonEnvironmentError, check_python_environment
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelProfile,
    ModelProfileCatalog,
    PromptDialect,
)


_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_REVISION = 9_007_199_254_740_991
_REPARSE_POINT = 0x00000400
_INSTALLATION_FIELDS = frozenset({
    "runtime_root", "python_executable", "models_root", "python_sha256",
    "comfyui_main_sha256", "profiles", "default_profile_id",
})
_PROFILE_FIELDS = frozenset({
    "profile_id", "display_name", "architecture", "prompt_dialect",
    "checkpoint_name", "checkpoint_sha256", "checkpoint_source",
    "checkpoint_license", "size_presets", "default_size_preset_id",
    "sampler_name", "scheduler", "steps", "cfg_scale",
})
ImageSettingsState = Literal[
    "unconfigured", "missing_model", "hash_mismatch", "ready", "invalid", "recovery_required",
]


class DesktopImageSettingsError(Exception):
    """Report a path-free validation, conflict, or persistence failure."""


@dataclass(frozen=True, slots=True)
class TrustedImageInstallation:
    """Bind a confirmed installation to pinned files and reviewed profiles.

    This is a private Python configuration value, never a generation DTO.
    Digests must come from the installation review rather than an implicit
    first-run trust decision. Saving it requires a separate explicit consent.
    """

    runtime_root: Path
    python_executable: Path
    models_root: Path
    python_sha256: str
    comfyui_main_sha256: str
    profiles: ModelProfileCatalog
    default_profile_id: str

    def __post_init__(self) -> None:
        """Require absolute trusted inputs and an existing logical default."""

        for value in (self.runtime_root, self.python_executable, self.models_root):
            _absolute_path(value)
        if type(self.profiles) is not ModelProfileCatalog:
            raise DesktopImageSettingsError("Image profiles must be reviewed configuration.")
        if len(self.profiles.profiles) > 8:
            raise DesktopImageSettingsError("At most eight image profiles are supported.")
        self.profiles.get(self.default_profile_id)
        for digest in (self.python_sha256, self.comfyui_main_sha256):
            if (
                type(digest) is not str or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise DesktopImageSettingsError("Runtime files require pinned SHA-256 digests.")
        for profile in self.profiles.profiles:
            if len(profile.size_presets) > 8:
                raise DesktopImageSettingsError("At most eight size presets are supported.")


@dataclass(frozen=True, slots=True)
class ImageSettingsStatus:
    """Expose configuration readiness without private installation locations."""

    state: ImageSettingsState
    message: str
    revision: int = 0
    _installation: TrustedImageInstallation | None = field(default=None, repr=False)

    def to_public_dict(self) -> dict[str, object]:
        """Return the small renderer contract, excluding filenames and hashes."""

        installation = self._installation
        profiles: list[dict[str, object]] = []
        if installation is not None:
            for profile in installation.profiles.profiles:
                profiles.append({
                    "profileId": profile.profile_id,
                    "displayName": profile.display_name,
                    "architecture": profile.architecture.value,
                    "promptDialect": profile.prompt_dialect.value,
                    "defaultSizePresetId": profile.default_size_preset_id,
                    "sizePresets": [
                        {"presetId": item.preset_id, "width": item.width, "height": item.height}
                        for item in profile.size_presets
                    ],
                })
        return {
            "configured": installation is not None,
            "ready": self.state == "ready",
            "state": self.state,
            "message": self.message,
            "revision": self.revision,
            "defaultProfileId": installation.default_profile_id if installation else None,
            "profiles": profiles,
        }


def _absolute_path(value: object) -> Path:
    """Reject relative locations and lexical traversal before filesystem work."""

    if (
        not isinstance(value, Path) or not value.is_absolute()
        or ".." in value.parts or "\x00" in str(value)
    ):
        raise DesktopImageSettingsError("Image configuration requires absolute local paths.")
    return value


def _snapshot(path: Path, *, directory: bool) -> os.stat_result:
    """Reject links and reparse points at every existing path component."""

    for component in reversed((path, *path.parents)):
        item = os.lstat(component)
        if stat.S_ISLNK(item.st_mode) or int(getattr(item, "st_file_attributes", 0)) & _REPARSE_POINT:
            raise DesktopImageSettingsError("Image storage contains an unsafe filesystem link.")
    item = os.lstat(path)
    if not (stat.S_ISDIR(item.st_mode) if directory else stat.S_ISREG(item.st_mode)):
        raise DesktopImageSettingsError("Image storage has an unsupported filesystem type.")
    return item


def _mkdir(path: Path) -> None:
    """Create only missing ordinary directories, never adopt a link target."""

    try:
        _snapshot(path, directory=True)
        return
    except FileNotFoundError:
        _mkdir(path.parent)
    try:
        path.mkdir()
    except FileExistsError:
        # Another startup may have created the same fixed root. Validate its
        # type instead of accepting mkdir(exist_ok=True)'s symlink behavior.
        pass
    _snapshot(path, directory=True)


def _identity(item: os.stat_result) -> tuple[int, int, int, int]:
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns


def _read_regular(path: Path, *, maximum: int | None = None) -> bytes | str:
    """Read bounded config bytes or hash a stable regular local input file."""

    before = _snapshot(path, directory=False)
    if maximum is not None and (before.st_size > maximum or before.st_nlink != 1):
        raise DesktopImageSettingsError("Saved image settings are oversized or aliased.")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise DesktopImageSettingsError("Image input changed while opening.")
        while chunk := stream.read(1024 * 1024 if maximum is None else min(65_536, maximum + 1)):
            total += len(chunk)
            if maximum is not None:
                if total > maximum:
                    raise DesktopImageSettingsError("Saved image settings exceed their size limit.")
                chunks.append(chunk)
            else:
                digest.update(chunk)
        opened_after = os.fstat(stream.fileno())
    after = _snapshot(path, directory=False)
    if (
        _identity(before) != _identity(opened_after) or _identity(before) != _identity(after)
        or (maximum is not None and after.st_nlink != 1)
    ):
        raise DesktopImageSettingsError("Image input changed during verification.")
    return b"".join(chunks) if maximum is not None else digest.hexdigest()


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys so human review and parser meaning agree."""

    value: dict[str, object] = {}
    for name, item in pairs:
        if name in value:
            raise DesktopImageSettingsError("Saved image settings contain duplicate fields.")
        value[name] = item
    return value


def _fields(value: object, names: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != names:
        raise DesktopImageSettingsError("Saved image settings contain unsupported fields.")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if type(value) is not str:
        raise DesktopImageSettingsError("Saved image settings require text fields.")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_REVISION:
        raise DesktopImageSettingsError("Image settings revision is invalid.")
    return value


def _decode(value: object) -> TrustedImageInstallation:
    raw = _fields(value, _INSTALLATION_FIELDS)
    profiles = raw["profiles"]
    if type(profiles) is not list or not 1 <= len(profiles) <= 8:
        raise DesktopImageSettingsError("Saved image profiles are invalid.")
    parsed: list[ModelProfile] = []
    for item in profiles:
        profile = _fields(item, _PROFILE_FIELDS)
        presets = profile["size_presets"]
        if type(presets) is not list or not 1 <= len(presets) <= 8:
            raise DesktopImageSettingsError("Saved image presets are invalid.")
        sizes: list[ImageSizePreset] = []
        for preset in presets:
            size = _fields(preset, frozenset({"preset_id", "width", "height"}))
            sizes.append(ImageSizePreset(
                _text(size["preset_id"]), cast(int, size["width"]), cast(int, size["height"]),
            ))
        parsed.append(ModelProfile(
            profile_id=_text(profile["profile_id"]),
            display_name=_text(profile["display_name"]),
            architecture=ModelArchitecture(_text(profile["architecture"])),
            prompt_dialect=PromptDialect(_text(profile["prompt_dialect"])),
            checkpoint_name=_text(profile["checkpoint_name"]),
            checkpoint_sha256=_text(profile["checkpoint_sha256"]),
            checkpoint_source=_text(profile["checkpoint_source"]),
            checkpoint_license=_text(profile["checkpoint_license"]),
            size_presets=tuple(sizes),
            default_size_preset_id=_text(profile["default_size_preset_id"]),
            sampler_name=_text(profile["sampler_name"]), scheduler=_text(profile["scheduler"]),
            steps=cast(int, profile["steps"]), cfg_scale=cast(float, profile["cfg_scale"]),
        ))
    return TrustedImageInstallation(
        runtime_root=Path(_text(raw["runtime_root"])),
        python_executable=Path(_text(raw["python_executable"])),
        models_root=Path(_text(raw["models_root"])),
        python_sha256=_text(raw["python_sha256"]),
        comfyui_main_sha256=_text(raw["comfyui_main_sha256"]),
        profiles=ModelProfileCatalog(tuple(parsed)),
        default_profile_id=_text(raw["default_profile_id"]),
    )


def _encode(value: TrustedImageInstallation) -> dict[str, object]:
    profiles: list[dict[str, object]] = []
    for profile in value.profiles.profiles:
        profiles.append({
            "profile_id": profile.profile_id, "display_name": profile.display_name,
            "architecture": profile.architecture.value, "prompt_dialect": profile.prompt_dialect.value,
            "checkpoint_name": profile.checkpoint_name, "checkpoint_sha256": profile.checkpoint_sha256,
            "checkpoint_source": profile.checkpoint_source, "checkpoint_license": profile.checkpoint_license,
            "size_presets": [
                {"preset_id": size.preset_id, "width": size.width, "height": size.height}
                for size in profile.size_presets
            ],
            "default_size_preset_id": profile.default_size_preset_id,
            "sampler_name": profile.sampler_name, "scheduler": profile.scheduler,
            "steps": profile.steps, "cfg_scale": profile.cfg_scale,
        })
    return {
        "runtime_root": str(value.runtime_root), "python_executable": str(value.python_executable),
        "models_root": str(value.models_root), "python_sha256": value.python_sha256,
        "comfyui_main_sha256": value.comfyui_main_sha256, "profiles": profiles,
        "default_profile_id": value.default_profile_id,
    }


def _saved_document(encoded: bytes) -> tuple[int, TrustedImageInstallation]:
    """Decode one already bounded private document with an exact schema."""

    document = json.loads(encoded, object_pairs_hook=_object)
    raw = _fields(document, frozenset({"schema_version", "revision", "installation"}))
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise DesktopImageSettingsError("Saved image settings schema is unsupported.")
    revision = _revision(raw["revision"])
    if revision == 0:
        raise DesktopImageSettingsError("Saved image settings revision must be positive.")
    return revision, _decode(raw["installation"])


class DesktopImageSettingsStore:
    """Own one fixed private settings file and side-effect-free model checks."""

    def __init__(self, data_root: Path) -> None:
        """Fix settings, runtime scratch space, and Gallery beneath one root."""

        self._data_root = _absolute_path(data_root)
        self._path = ProductionDataLayout(data_root).settings / "images.json"
        self._private_root = data_root / "workspace" / "image-runs"
        self._lock = RLock()

    def _load(self) -> tuple[int, TrustedImageInstallation | None]:
        try:
            os.lstat(self._path)
        except FileNotFoundError:
            # An absent leaf must not disguise a linked settings directory.
            ancestor = self._path.parent
            while True:
                try:
                    os.lstat(ancestor)
                    break
                except FileNotFoundError:
                    ancestor = ancestor.parent
            _snapshot(ancestor, directory=True)
            return 0, None
        return _saved_document(cast(bytes, _read_regular(self._path, maximum=_MAX_DOCUMENT_BYTES)))

    def load(self) -> TrustedImageInstallation | None:
        """Read private trusted configuration, failing closed on corrupt data."""

        with self._lock:
            try:
                return self._load()[1]
            except Exception:
                pass
        raise DesktopImageSettingsError("Saved local image settings are invalid.")

    def save(
        self,
        installation: TrustedImageInstallation,
        *,
        trusted_confirmation: bool,
        expected_revision: int,
        repair_invalid: bool = False,
    ) -> ImageSettingsStatus:
        """Atomically save an explicitly confirmed installation at one revision.

        The caller owns the native picker and consent dialog. This API never
        discovers or trusts file digests automatically, and must not be wired
        to a renderer request containing filesystem paths.

        Explicit native repair may replace a malformed, bounded ordinary
        configuration only at revision zero. The old bytes are preserved in
        a fixed private backup before replacement; Gallery files are outside
        this transaction and are never modified by configuration recovery.
        """

        if type(installation) is not TrustedImageInstallation or trusted_confirmation is not True:
            raise DesktopImageSettingsError("Confirm the trusted local image installation before saving.")
        _revision(expected_revision)
        if type(repair_invalid) is not bool or (repair_invalid and expected_revision != 0):
            raise DesktopImageSettingsError("Invalid image settings repair requires revision zero.")
        failure = "Image settings could not be saved; previous settings remain unchanged."
        with self._lock:
            try:
                _mkdir(self._path.parent)
                lock_path = self._path.with_name(f".{self._path.name}.lock")
                if lock_path.exists() or lock_path.is_symlink():
                    if _snapshot(lock_path, directory=False).st_nlink != 1:
                        raise DesktopImageSettingsError("Image settings lock is aliased.")
                with desktop_settings_file_lock(self._path):
                    if _snapshot(lock_path, directory=False).st_nlink != 1:
                        raise DesktopImageSettingsError("Image settings lock is aliased.")
                    invalid_bytes: bytes | None = None
                    try:
                        current_revision, _ = self._load()
                    except Exception:
                        if not repair_invalid:
                            raise
                        # Re-read under the same cross-process lock. Only JSON
                        # or schema failure grants repair: unsafe paths, links,
                        # aliased files, and oversized input still fail closed.
                        invalid_bytes = cast(bytes, _read_regular(self._path, maximum=_MAX_DOCUMENT_BYTES))
                        try:
                            _saved_document(invalid_bytes)
                        except Exception:
                            current_revision = 0
                        else:
                            raise DesktopImageSettingsError("Image settings changed during repair.")
                    if repair_invalid and invalid_bytes is None:
                        raise DesktopImageSettingsError("Only invalid saved image settings can be repaired.")
                    if current_revision != expected_revision:
                        failure = "Image settings changed elsewhere. Reload before saving."
                        raise DesktopImageSettingsError(failure)
                    if current_revision == _MAX_REVISION:
                        raise DesktopImageSettingsError("Image settings revision limit reached.")
                    document = {
                        "schema_version": 1, "revision": current_revision + 1,
                        "installation": _encode(installation),
                    }
                    # Roundtrip through the strict reader keeps the trusted
                    # Python API and on-disk schema subject to identical rules.
                    _decode(document["installation"])
                    encoded = (json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
                    if len(encoded) > _MAX_DOCUMENT_BYTES:
                        raise DesktopImageSettingsError("Image settings exceed their size limit.")
                    if invalid_bytes is not None:
                        backup = self._path.with_name("images.invalid-backup.json")
                        try:
                            _read_regular(backup, maximum=_MAX_DOCUMENT_BYTES)
                        except FileNotFoundError:
                            pass
                        self._atomic_write(invalid_bytes, invalid_backup=True)
                    self._atomic_write(encoded)
                return self.status()
            except Exception:
                pass
        raise DesktopImageSettingsError(failure)

    def _atomic_write(self, encoded: bytes, *, invalid_backup: bool = False) -> None:
        """Flush a sibling before replacing only a fixed settings or backup file."""

        destination = self._path.with_name("images.invalid-backup.json") if invalid_backup else self._path
        descriptor, name = mkstemp(prefix=".images-", suffix=".tmp", dir=self._path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _snapshot(self._path.parent, directory=True)
            try:
                snapshot = _snapshot(destination, directory=False)
                if snapshot.st_nlink != 1:
                    raise DesktopImageSettingsError("Image settings destination is aliased.")
            except FileNotFoundError:
                pass
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _config(self, installation: TrustedImageInstallation) -> ManagedComfyUiConfig:
        return ManagedComfyUiConfig(
            runtime_root=installation.runtime_root, python_executable=installation.python_executable,
            models_root=installation.models_root, private_state_root=self._private_root,
            python_sha256=installation.python_sha256, comfyui_main_sha256=installation.comfyui_main_sha256,
        )

    def _preflight(self, installation: TrustedImageInstallation) -> tuple[ImageSettingsState, str]:
        """Explain missing/hash failures before the authoritative native check.

        The supervisor deliberately exposes a single unavailable error. A small
        file-only check supplies useful Settings states without exposing its
        private paths or weakening the supervisor's own repeated verification.
        The fixed empty scratch root is the only resource created here.
        """

        config = self._config(installation)
        _mkdir(self._private_root)
        if image_recovery_required(self._data_root):
            return "recovery_required", "Image runtime residue requires confirmed recovery. Its private files will be preserved in quarantine."
        if os.name == "nt":
            try:
                check_python_environment(config.python_executable, config.runtime_root)
            except PythonEnvironmentError as error:
                return "invalid", str(error)
        for profile in installation.profiles.profiles:
            checkpoint = config.models_root / "checkpoints" / Path(profile.checkpoint_name.replace("\\", "/"))
            try:
                digest = _read_regular(checkpoint)
            except FileNotFoundError:
                return "missing_model", "A configured checkpoint is missing. Install the reviewed model before generating."
            if digest != profile.checkpoint_sha256:
                return "hash_mismatch", "A configured model hash does not match its reviewed SHA-256 digest."
        for path, digest in (
            (config.python_executable, config.python_sha256),
            (config.main_script, config.comfyui_main_sha256),
        ):
            if _read_regular(path) != digest:
                return "hash_mismatch", "A runtime file hash does not match its reviewed SHA-256 digest."
        runtime = ManagedComfyUiRuntime(config)
        try:
            for profile in installation.profiles.profiles:
                runtime.ensure_ready(profile)
        finally:
            runtime.close()
        return "ready", "The trusted local image installation is ready."

    def status(self) -> ImageSettingsStatus:
        """Hash local inputs and return a path-free state without launching GPU work."""

        revision = 0
        installation: TrustedImageInstallation | None = None
        recovery_required = False
        with self._lock:
            try:
                recovery_required = image_recovery_required(self._data_root)
                revision, installation = self._load()
                if installation is None:
                    if recovery_required:
                        return ImageSettingsStatus("recovery_required", "Image runtime residue requires confirmed recovery. Private files will be preserved.")
                    return ImageSettingsStatus("unconfigured", "Choose and confirm a trusted ComfyUI installation.")
                state, message = self._preflight(installation)
                return ImageSettingsStatus(state, message, revision, installation)
            except Exception:
                pass
        if recovery_required:
            return ImageSettingsStatus("recovery_required", "Image runtime residue requires confirmed recovery. Private files will be preserved.", revision, installation)
        return ImageSettingsStatus("invalid", "Local image settings or runtime readiness checks failed. Review the installation.", revision, installation)

    def create_service(self) -> ImageGenerationApplicationService:
        """Compose the exclusive managed service only after full local readiness."""

        with self._lock:
            try:
                _, installation = self._load()
                if installation is not None and self._preflight(installation)[0] == "ready":
                    return create_managed_image_application_service(
                        installation.profiles, self._config(installation), self._data_root,
                    )
            except Exception:
                pass
        raise ImageGenerationUnavailableError("The trusted local image installation is not ready.")


__all__ = [
    "DesktopImageSettingsError", "DesktopImageSettingsStore", "ImageSettingsStatus",
    "TrustedImageInstallation",
]
