"""Run the isolated, fail-closed GPT-SoVITS inference worker.

The worker speaks only the bounded binary protocol in the sibling
``gpt_sovits_protocol.py`` module.  It verifies every selected asset again in
the runtime process, binds the verified identities to one challenge, loads one
immutable GPT-SoVITS v2 engine, and emits complete mono PCM WAV responses.

READY proves a live Elysia-owned process observed the declared assets and
runtime consistency anchors; it is not a third-party supply-chain signature.
The parent must keep no-write/no-delete guards on every checked path until the
worker exits.  This worker deliberately does not issue the application-level
``binding_verified`` lease by itself.

This file is intentionally Python 3.9 compatible and imports only the standard
library at module load time.  Heavy upstream imports happen after the protocol
pipe has been duplicated and process stdout/stderr have been redirected to the
null device, preventing model diagnostics from corrupting binary responses.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
from typing import Any, BinaryIO, Callable, Dict, Iterator, Optional, Protocol, Tuple


_SCRIPT_PATH = Path(__file__).resolve(strict=True)
_SCRIPTS_DIR = _SCRIPT_PATH.parent
_PROJECT_DIR = _SCRIPTS_DIR.parent
_PROTOCOL_PATH = _SCRIPTS_DIR / "gpt_sovits_protocol.py"
_PROTOCOL_MODULE_NAME = "_elysia_gpt_sovits_protocol_v1"


def _load_sibling_protocol() -> Any:
    """Load the exact sibling protocol without trusting cwd or ``sys.path``."""

    existing = sys.modules.get(_PROTOCOL_MODULE_NAME)
    if existing is not None:
        return existing
    try:
        candidate = _PROTOCOL_PATH.resolve(strict=True)
        if candidate.parent != _SCRIPTS_DIR or not candidate.is_file():
            raise ImportError
        specification = importlib.util.spec_from_file_location(
            _PROTOCOL_MODULE_NAME,
            str(candidate),
        )
        if specification is None or specification.loader is None:
            raise ImportError
        module = importlib.util.module_from_spec(specification)
        sys.modules[_PROTOCOL_MODULE_NAME] = module
        try:
            specification.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(_PROTOCOL_MODULE_NAME, None)
            raise
        return module
    except BaseException as error:
        raise ImportError("The managed speech protocol is unavailable.") from error


_protocol = _load_sibling_protocol()
FrameKind = _protocol.FrameKind
ProtocolError = _protocol.ProtocolError
ProtocolFrame = _protocol.ProtocolFrame
read_frame = _protocol.read_frame
write_frame = _protocol.write_frame
_MAX_AUDIO_BYTES = int(_protocol.PROTOCOL_MAX_PAYLOAD_BYTES)

_SCHEMA_VERSION = 1
_CHALLENGE_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_INIT_FIELDS = frozenset(
    {
        "schema",
        "challenge",
        "runtime_root",
        "gpt_weights",
        "sovits_weights",
        "reference_audio",
        "prompt_text",
        "prompt_language",
        "speed_milli",
        "seed",
        "device",
        "runtime_manifest_digest",
    }
)
_ASSET_FIELDS = frozenset({"path", "bytes", "sha256"})
_SYNTHESIZE_FIELDS = frozenset(
    {"schema", "challenge", "text", "text_language"}
)
_STOP_FIELDS = frozenset({"schema", "challenge"})
_PROMPT_LANGUAGES = frozenset({"zh", "en"})
_TEXT_LANGUAGES = frozenset({"auto", "zh", "en"})
_DEVICES = frozenset({"cpu", "cuda"})
_ERROR_CODES = frozenset(
    {
        "protocol_invalid",
        "binding_failed",
        "engine_failed",
        "synthesis_failed",
    }
)
_MAX_TEXT_CODE_POINTS = 4096
_MAX_PROMPT_CODE_POINTS = 4096
_MAX_ASSET_BYTES = 8 * 1024 * 1024 * 1024
_MIN_SAMPLE_RATE = 8000
_MAX_SAMPLE_RATE = 192000
_HASH_CHUNK_BYTES = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_BINDING_DOMAIN = b"ELYTTS-BINDING-V1\0"
_RUNTIME_MANIFEST_DOMAIN = b"ELYTTS-RUNTIME-MANIFEST-V1\0"
_BASE_BERT_PATH = (
    "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
)
_BASE_HUBERT_PATH = "GPT_SoVITS/pretrained_models/chinese-hubert-base"
_RUNTIME_MANIFEST_RELATIVE_FILES = (
    ("runtime-python", "runtime/python.exe"),
    ("ffmpeg", "ffmpeg.exe"),
    ("tts-entry", "GPT_SoVITS/TTS_infer_pack/TTS.py"),
    ("audio-loader", "tools/my_utils.py"),
    ("bert-config", _BASE_BERT_PATH + "/config.json"),
    ("bert-model", _BASE_BERT_PATH + "/pytorch_model.bin"),
    ("bert-tokenizer", _BASE_BERT_PATH + "/tokenizer.json"),
    ("hubert-config", _BASE_HUBERT_PATH + "/config.json"),
    ("hubert-preprocessor", _BASE_HUBERT_PATH + "/preprocessor_config.json"),
    ("hubert-model", _BASE_HUBERT_PATH + "/pytorch_model.bin"),
)


class _Engine(Protocol):
    """Describe the narrow engine surface owned by the worker state machine."""

    def synthesize(self, text: str, text_language: str) -> object:
        """Return one iterator that yields exactly one sample-rate/PCM pair."""

    def close(self) -> None:
        """Release engine-owned model state without producing protocol output."""


@dataclass(frozen=True, repr=False)
class _AssetDescriptor:
    """Hold one sensitive absolute asset declaration outside diagnostics."""

    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, repr=False)
class _WorkerConfig:
    """Hold the exact INIT binding while suppressing paths and prompts in repr."""

    challenge: str
    runtime_root: Path
    gpt_weights: _AssetDescriptor
    sovits_weights: _AssetDescriptor
    reference_audio: _AssetDescriptor
    prompt_text: str
    prompt_language: str
    speed_milli: int
    seed: int
    device: str
    runtime_manifest_digest: str
    binding_sha256: str


class _WorkerFailure(Exception):
    """Carry one closed public code and never a native failure detail."""

    def __init__(self, code: str) -> None:
        """Reject accidental expansion of the stable error vocabulary."""

        if code not in _ERROR_CODES:
            code = "engine_failed"
        self.code = code
        super().__init__(code)


class _ResponseFailure(Exception):
    """Mark a response pipe that must never receive a retry or ERROR append."""


def _require_exact_object(value: object, fields: frozenset) -> Dict[str, object]:
    """Return a plain JSON object only when its field set is exact."""

    if type(value) is not dict or set(value) != fields:
        raise _WorkerFailure("protocol_invalid")
    return value  # type: ignore[return-value]


def _require_schema(value: object) -> None:
    """Require the sole worker metadata schema without Boolean coercion."""

    if type(value) is not int or value != _SCHEMA_VERSION:
        raise _WorkerFailure("protocol_invalid")


def _require_challenge(value: object) -> str:
    """Return one canonical 256-bit lowercase hexadecimal challenge."""

    if type(value) is not str or _CHALLENGE_PATTERN.fullmatch(value) is None:
        raise _WorkerFailure("protocol_invalid")
    return value


def _require_sha256(value: object) -> str:
    """Return one canonical lowercase SHA-256 declaration."""

    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise _WorkerFailure("protocol_invalid")
    return value


def _require_absolute_path(value: object) -> Path:
    """Return one bounded, lexical absolute path without resolving links."""

    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or len(value) > 32767
        or os.path.normpath(value) != value
    ):
        raise _WorkerFailure("protocol_invalid")
    path = Path(value)
    if not path.is_absolute():
        raise _WorkerFailure("protocol_invalid")
    return path


def _require_text(value: object, *, maximum: int) -> str:
    """Return bounded spoken text while excluding empty/control-only input."""

    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or "\x00" in value
        or not any(character.isalnum() or character.isprintable() and not character.isspace() for character in value)
    ):
        raise _WorkerFailure("protocol_invalid")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise _WorkerFailure("protocol_invalid") from None
    return value


def _parse_asset(value: object, *, suffix: str, reference: bool = False) -> _AssetDescriptor:
    """Parse one exact absolute-path, byte-length, and digest declaration."""

    fields = _require_exact_object(value, _ASSET_FIELDS)
    path = _require_absolute_path(fields["path"])
    size = fields["bytes"]
    maximum = _MAX_AUDIO_BYTES if reference else _MAX_ASSET_BYTES
    minimum = 12 if reference else 1
    if type(size) is not int or not minimum <= size <= maximum:
        raise _WorkerFailure("protocol_invalid")
    if path.suffix.casefold() != suffix:
        raise _WorkerFailure("protocol_invalid")
    return _AssetDescriptor(path, size, _require_sha256(fields["sha256"]))


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows reparse metadata while remaining portable in tests."""

    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _stat_identity(info: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    """Return fields that reveal replacement or mutation during verification."""

    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
        int(getattr(info, "st_file_attributes", 0)),
    )


def _pathname_identity(
    identity: Tuple[int, int, int, int, int, int],
) -> Tuple[int, int, int, int, int]:
    """Drop Windows creation-time rounding when comparing path and handle APIs.

    Windows may report ``st_ctime_ns`` at slightly different precision through
    ``lstat`` and ``fstat`` for the same file.  Handle-to-handle comparisons
    retain ctime to detect mutation; pathname comparisons use the stable file
    ID, size, mtime, and attributes so valid fresh files are not rejected.
    """

    return identity[:4] + identity[5:]


def _require_safe_path_tree(path: Path, *, leaf_is_file: bool) -> os.stat_result:
    """Reject link/reparse traversal and require the expected final node type."""

    try:
        current = Path(path.anchor)
        parts = path.parts[1:] if path.anchor else path.parts
        leaf_info = None  # type: Optional[os.stat_result]
        for index, part in enumerate(parts):
            current = current / part
            info = os.lstat(str(current))
            final = index == len(parts) - 1
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise OSError
            if final and leaf_is_file:
                if not stat.S_ISREG(info.st_mode):
                    raise OSError
                leaf_info = info
            elif not stat.S_ISDIR(info.st_mode):
                raise OSError
        if leaf_info is None:
            info = os.lstat(str(path))
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise OSError
            if leaf_is_file and not stat.S_ISREG(info.st_mode):
                raise OSError
            if not leaf_is_file and not stat.S_ISDIR(info.st_mode):
                raise OSError
            leaf_info = info
        return leaf_info
    except (OSError, RuntimeError, ValueError):
        raise _WorkerFailure("binding_failed") from None


def _verify_asset(asset: _AssetDescriptor) -> None:
    """Stream and attest one unchanged regular file against its declaration.

    The path is checked before and after opening, while ``fstat`` brackets the
    stream hash.  Comparing descriptor and pathname identities prevents a
    replacement race from making the digest describe a file no longer bound to
    the configured name during this check.  The managed parent must hold its
    directory/leaf guards across the later upstream path opens; this worker
    performs a second check after loading as defense in depth.
    """

    before_path = _require_safe_path_tree(asset.path, leaf_is_file=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(str(asset.path), flags)
        before_handle = os.fstat(descriptor)
        before_path_identity = _stat_identity(before_path)
        before_handle_identity = _stat_identity(before_handle)
        if (
            not stat.S_ISREG(before_handle.st_mode)
            or _is_reparse(before_handle)
            or _pathname_identity(before_path_identity)
            != _pathname_identity(before_handle_identity)
            or before_handle.st_size != asset.size_bytes
        ):
            raise _WorkerFailure("binding_failed")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > asset.size_bytes:
                raise _WorkerFailure("binding_failed")
            digest.update(chunk)
        after_handle = os.fstat(descriptor)
        after_path = os.lstat(str(asset.path))
        after_handle_identity = _stat_identity(after_handle)
        after_path_identity = _stat_identity(after_path)
        if (
            total != asset.size_bytes
            or digest.hexdigest() != asset.sha256
            or before_handle_identity != after_handle_identity
            or _pathname_identity(after_handle_identity)
            != _pathname_identity(after_path_identity)
            or stat.S_ISLNK(after_path.st_mode)
            or _is_reparse(after_path)
        ):
            raise _WorkerFailure("binding_failed")
    except _WorkerFailure:
        raise
    except (OSError, OverflowError, RuntimeError, ValueError):
        raise _WorkerFailure("binding_failed") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _hash_manifest_file(path: Path) -> Tuple[int, str]:
    """Hash one unchanged non-link manifest file under bracketed identities."""

    before_path = _require_safe_path_tree(path, leaf_is_file=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(str(path), flags)
        before_handle = os.fstat(descriptor)
        before_path_identity = _stat_identity(before_path)
        before_handle_identity = _stat_identity(before_handle)
        if (
            not stat.S_ISREG(before_handle.st_mode)
            or _is_reparse(before_handle)
            or _pathname_identity(before_path_identity)
            != _pathname_identity(before_handle_identity)
            or not 1 <= before_handle.st_size <= _MAX_ASSET_BYTES
        ):
            raise _WorkerFailure("binding_failed")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_ASSET_BYTES:
                raise _WorkerFailure("binding_failed")
            digest.update(chunk)
        after_handle = os.fstat(descriptor)
        after_path = os.lstat(str(path))
        after_handle_identity = _stat_identity(after_handle)
        after_path_identity = _stat_identity(after_path)
        if (
            total != before_handle.st_size
            or before_handle_identity != after_handle_identity
            or _pathname_identity(after_handle_identity)
            != _pathname_identity(after_path_identity)
            or stat.S_ISLNK(after_path.st_mode)
            or _is_reparse(after_path)
        ):
            raise _WorkerFailure("binding_failed")
        return total, digest.hexdigest()
    except _WorkerFailure:
        raise
    except (OSError, OverflowError, RuntimeError, ValueError):
        raise _WorkerFailure("binding_failed") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def compute_runtime_manifest_digest(runtime_root: Path) -> str:
    """Attest the closed runtime anchors used by the managed speech worker.

    The digest identifies content by stable logical roles, never by deployment
    paths.  It covers the worker/protocol sources plus the executable, FFmpeg,
    upstream entry points, and both fixed base models.  The parent computes the
    same value before launch; the worker recomputes it before importing any
    upstream code so a caller cannot turn an arbitrary 64-hex string into a
    READY attestation.
    """

    if (
        not isinstance(runtime_root, Path)
        or not runtime_root.is_absolute()
        or os.path.normpath(str(runtime_root)) != str(runtime_root)
    ):
        raise _WorkerFailure("binding_failed")
    _require_safe_path_tree(runtime_root, leaf_is_file=False)
    entries = []
    manifest_paths = (
        ("elysia-worker", _SCRIPT_PATH),
        ("elysia-protocol", _PROTOCOL_PATH),
    ) + tuple(
        (role, runtime_root / Path(relative_path))
        for role, relative_path in _RUNTIME_MANIFEST_RELATIVE_FILES
    )
    for role, path in manifest_paths:
        size_bytes, sha256 = _hash_manifest_file(path)
        entries.append(
            {"bytes": size_bytes, "id": role, "sha256": sha256}
        )
    canonical = json.dumps(
        {"files": entries, "schema": _SCHEMA_VERSION},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(_RUNTIME_MANIFEST_DOMAIN + canonical).hexdigest()


def _canonical_binding(config: _WorkerConfig) -> bytes:
    """Encode the stable verified configuration used for process attestation."""

    def asset_value(asset: _AssetDescriptor) -> Dict[str, object]:
        """Convert one verified descriptor into canonical binding fields."""

        return {
            "bytes": asset.size_bytes,
            "path": str(asset.path),
            "sha256": asset.sha256,
        }

    value = {
        "device": config.device,
        "gpt_weights": asset_value(config.gpt_weights),
        "prompt_language": config.prompt_language,
        "prompt_text": config.prompt_text,
        "reference_audio": asset_value(config.reference_audio),
        "runtime_manifest_digest": config.runtime_manifest_digest,
        "runtime_root": str(config.runtime_root),
        "schema": _SCHEMA_VERSION,
        "seed": config.seed,
        "sovits_weights": asset_value(config.sovits_weights),
        "speed_milli": config.speed_milli,
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")


def _parse_init(metadata: object) -> _WorkerConfig:
    """Validate INIT metadata, attest assets, and compute its binding digest."""

    fields = _require_exact_object(metadata, _INIT_FIELDS)
    _require_schema(fields["schema"])
    challenge = _require_challenge(fields["challenge"])
    runtime_root = _require_absolute_path(fields["runtime_root"])
    _require_safe_path_tree(runtime_root, leaf_is_file=False)
    gpt = _parse_asset(fields["gpt_weights"], suffix=".ckpt")
    sovits = _parse_asset(fields["sovits_weights"], suffix=".pth")
    reference = _parse_asset(
        fields["reference_audio"],
        suffix=".wav",
        reference=True,
    )
    prompt = _require_text(fields["prompt_text"], maximum=_MAX_PROMPT_CODE_POINTS)
    prompt_language = fields["prompt_language"]
    speed_milli = fields["speed_milli"]
    seed = fields["seed"]
    device = fields["device"]
    if type(prompt_language) is not str or prompt_language not in _PROMPT_LANGUAGES:
        raise _WorkerFailure("protocol_invalid")
    if type(speed_milli) is not int or not 500 <= speed_milli <= 2000:
        raise _WorkerFailure("protocol_invalid")
    if type(seed) is not int or not 0 <= seed <= 2_147_483_647:
        raise _WorkerFailure("protocol_invalid")
    if type(device) is not str or device not in _DEVICES:
        raise _WorkerFailure("protocol_invalid")
    manifest_digest = _require_sha256(fields["runtime_manifest_digest"])
    actual_manifest_digest = compute_runtime_manifest_digest(runtime_root)
    if not hmac.compare_digest(manifest_digest, actual_manifest_digest):
        raise _WorkerFailure("binding_failed")

    for asset in (gpt, sovits, reference):
        _verify_asset(asset)
    unbound = _WorkerConfig(
        challenge,
        runtime_root,
        gpt,
        sovits,
        reference,
        prompt,
        prompt_language,
        speed_milli,
        seed,
        device,
        manifest_digest,
        "",
    )
    binding = hashlib.sha256(_BINDING_DOMAIN + _canonical_binding(unbound)).hexdigest()
    return _WorkerConfig(
        challenge,
        runtime_root,
        gpt,
        sovits,
        reference,
        prompt,
        prompt_language,
        speed_milli,
        seed,
        device,
        manifest_digest,
        binding,
    )


def _normalized_sys_path(entry: str) -> Optional[Path]:
    """Resolve one import entry for security cleanup without surfacing errors."""

    try:
        return Path(entry or os.getcwd()).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _prepare_upstream_import(runtime_root: Path) -> None:
    """Isolate upstream imports and silence all non-protocol OS output.

    GPT-SoVITS imports a top-level package named ``tools``.  Removing the
    Elysia scripts/project entries and stale ambiguous modules before inserting
    the runtime directories guarantees that its own package wins.  ``dup2`` is
    deliberately performed before importing torch/GPT-SoVITS; the executable
    entry point has already duplicated the response pipe to an independent
    unbuffered handle.
    """

    cleaned = []
    for entry in sys.path:
        normalized = _normalized_sys_path(entry)
        if normalized in (_SCRIPTS_DIR, _PROJECT_DIR):
            continue
        cleaned.append(entry)
    runtime_package_root = runtime_root / "GPT_SoVITS"
    sys.path[:] = [str(runtime_root), str(runtime_package_root)] + [
        entry
        for entry in cleaned
        if _normalized_sys_path(entry) not in (runtime_root, runtime_package_root)
    ]
    ambiguous_roots = (
        "tools",
        "AR",
        "feature_extractor",
        "module",
        "TTS_infer_pack",
        "GPT_SoVITS",
    )
    for name in tuple(sys.modules):
        if any(name == root or name.startswith(root + ".") for root in ambiguous_roots):
            sys.modules.pop(name, None)
    try:
        null_descriptor = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_descriptor, 1)
            os.dup2(null_descriptor, 2)
        finally:
            os.close(null_descriptor)
        os.chdir(str(runtime_root))
    except (OSError, RuntimeError, ValueError):
        raise _WorkerFailure("engine_failed") from None


def _load_upstream_api(runtime_root: Path) -> Tuple[object, object]:
    """Import the pinned GPT-SoVITS TTS classes after process isolation."""

    _prepare_upstream_import(runtime_root)
    try:
        module = importlib.import_module("GPT_SoVITS.TTS_infer_pack.TTS")
        config_class = getattr(module, "TTS_Config")
        tts_class = getattr(module, "TTS")
        if not callable(config_class) or not callable(tts_class):
            raise TypeError
        return config_class, tts_class
    except BaseException:
        raise _WorkerFailure("engine_failed") from None


def _disabled_save_configs(_self: object, _path: object = None) -> None:
    """Prevent upstream configuration persistence in the managed worker."""


def _disabled_reload(*_args: object, **_kwargs: object) -> None:
    """Fail any upstream attempt to mutate the attested model binding."""

    raise RuntimeError("Managed model reload is disabled.")


@dataclass(repr=False)
class _ProductionEngine:
    """Adapt the pinned upstream TTS object to the worker's narrow contract."""

    _config: _WorkerConfig
    _loader: Callable[[Path], Tuple[object, object]] = _load_upstream_api

    def __post_init__(self) -> None:
        """Load one v2 model/reference and permanently disable hot reloads."""

        self._tts = None  # type: Any
        try:
            config_class, tts_class = self._loader(self._config.runtime_root)
            setattr(config_class, "save_configs", _disabled_save_configs)
            config_value = {
                "version": "v2",
                "custom": {
                    "bert_base_path": str(self._config.runtime_root / _BASE_BERT_PATH),
                    "cnhuhbert_base_path": str(self._config.runtime_root / _BASE_HUBERT_PATH),
                    "device": self._config.device,
                    "is_half": self._config.device == "cuda",
                    "t2s_weights_path": str(self._config.gpt_weights.path),
                    "version": "v2",
                    "vits_weights_path": str(self._config.sovits_weights.path),
                },
            }
            memory_config = config_class(config_value)  # type: ignore[operator]
            expected_config = {
                "bert_base_path": str(
                    self._config.runtime_root / _BASE_BERT_PATH
                ),
                "cnhuhbert_base_path": str(
                    self._config.runtime_root / _BASE_HUBERT_PATH
                ),
                "device": self._config.device,
                "is_half": self._config.device == "cuda",
                "t2s_weights_path": str(self._config.gpt_weights.path),
                "version": "v2",
                "vits_weights_path": str(self._config.sovits_weights.path),
            }
            # TTS_Config silently replaces missing custom paths with bundled
            # defaults.  Exact comparison before model construction prevents a
            # successful READY frame from attesting one file while loading
            # another.
            if any(
                getattr(memory_config, name, None) != expected
                for name, expected in expected_config.items()
            ):
                raise RuntimeError
            tts = tts_class(memory_config)  # type: ignore[operator]
            if getattr(tts, "configs", None) is not memory_config or any(
                getattr(memory_config, name, None) != expected
                for name, expected in expected_config.items()
            ):
                raise RuntimeError
            tts.set_ref_audio(str(self._config.reference_audio.path))
            if (
                getattr(tts, "prompt_cache", {}).get("ref_audio_path")
                != str(self._config.reference_audio.path)
            ):
                raise RuntimeError
            # Parent-held no-delete handles are the cross-process TOCTOU
            # guarantee.  Rechecking after all upstream opens also catches
            # accidental fallback or mutation in fake/test launchers.
            for asset in (
                self._config.gpt_weights,
                self._config.sovits_weights,
                self._config.reference_audio,
            ):
                _verify_asset(asset)
            # Upstream's exception recovery normally reloads weights.  Once the
            # READY digest is issued that behavior would silently invalidate
            # attestation, so any recovery path must poison this process.
            tts.init_t2s_weights = _disabled_reload
            tts.init_vits_weights = _disabled_reload
            self._tts = tts
        except _WorkerFailure:
            raise
        except BaseException:
            raise _WorkerFailure("engine_failed") from None

    def synthesize(self, text: str, text_language: str) -> object:
        """Run one request with all sampling/reference controls fixed by INIT."""

        if self._tts is None:
            raise _WorkerFailure("synthesis_failed")
        values = {
            "aux_ref_audio_paths": [],
            "batch_size": 1,
            "batch_threshold": 75 / 100,
            "fragment_interval": 3 / 10,
            "parallel_infer": False,
            "prompt_lang": self._config.prompt_language,
            "prompt_text": self._config.prompt_text,
            # ``None`` tells upstream to reuse the reference loaded during
            # INIT.  An empty string is treated as a new missing path and makes
            # every synthesis fail despite a populated prompt cache.
            "ref_audio_path": None,
            "repetition_penalty": 135 / 100,
            "return_fragment": False,
            "seed": self._config.seed,
            "speed_factor": self._config.speed_milli / 1000,
            "split_bucket": False,
            "temperature": 1,
            "text": text,
            "text_lang": text_language,
            "text_split_method": "cut0",
            "top_k": 5,
            "top_p": 1,
        }
        try:
            return self._tts.run(values)
        except BaseException:
            raise _WorkerFailure("synthesis_failed") from None

    def close(self) -> None:
        """Drop the sole TTS object and request its bounded cache cleanup."""

        tts = self._tts
        self._tts = None
        if tts is not None:
            empty_cache = getattr(tts, "empty_cache", None)
            if callable(empty_cache):
                empty_cache()


def _create_production_engine(config: _WorkerConfig) -> _Engine:
    """Construct the one production adapter selected by ``run_worker``."""

    return _ProductionEngine(config)


def _parse_synthesize(frame: Any, config: _WorkerConfig, last_id: int) -> Tuple[str, str]:
    """Validate one challenge-bound, monotonically identified text request."""

    if frame.kind is not FrameKind.SYNTHESIZE or frame.request_id <= last_id:
        raise _WorkerFailure("protocol_invalid")
    fields = _require_exact_object(frame.metadata, _SYNTHESIZE_FIELDS)
    _require_schema(fields["schema"])
    if _require_challenge(fields["challenge"]) != config.challenge:
        raise _WorkerFailure("protocol_invalid")
    text = _require_text(fields["text"], maximum=_MAX_TEXT_CODE_POINTS)
    language = fields["text_language"]
    if type(language) is not str or language not in _TEXT_LANGUAGES:
        raise _WorkerFailure("protocol_invalid")
    return text, language


def _require_stop(frame: Any, challenge: str) -> None:
    """Validate the sole graceful terminal control for a ready worker."""

    if frame.kind is not FrameKind.STOP:
        raise _WorkerFailure("protocol_invalid")
    fields = _require_exact_object(frame.metadata, _STOP_FIELDS)
    _require_schema(fields["schema"])
    if _require_challenge(fields["challenge"]) != challenge:
        raise _WorkerFailure("protocol_invalid")


def _close_iterator(iterator: object) -> None:
    """Best-effort close an invalid inference generator without exposing errors."""

    closer = getattr(iterator, "close", None)
    if callable(closer):
        try:
            closer()
        except BaseException:
            pass


def _build_wav(result: Any) -> Tuple[int, bytes]:
    """Drain one inference iterator and encode strict mono signed-16 PCM WAV."""

    try:
        iterator = iter(result)  # type: Iterator[object]
        first = next(iterator)
    except BaseException:
        raise _WorkerFailure("synthesis_failed") from None
    try:
        if type(first) is not tuple or len(first) != 2:
            raise _WorkerFailure("synthesis_failed")
        sample_rate, samples = first
        if (
            type(sample_rate) is not int
            or not _MIN_SAMPLE_RATE <= sample_rate <= _MAX_SAMPLE_RATE
        ):
            raise _WorkerFailure("synthesis_failed")
        try:
            view = memoryview(samples)
        except (TypeError, ValueError):
            raise _WorkerFailure("synthesis_failed") from None
        if (
            view.ndim != 1
            or view.itemsize != 2
            or view.format not in ("h", "<h", "=h", "@h")
            or not view.c_contiguous
            or len(view) == 0
        ):
            raise _WorkerFailure("synthesis_failed")
        pcm = view.tobytes()
        if len(pcm) != len(view) * 2 or not any(pcm):
            # Upstream yields one second of all-zero PCM before executing its
            # destructive model-reload error path.  Close at the suspended
            # yield instead of resuming that path inside an attested process.
            raise _WorkerFailure("synthesis_failed")
        if len(pcm) + 44 > _MAX_AUDIO_BYTES:
            raise _WorkerFailure("synthesis_failed")
        try:
            next(iterator)
        except StopIteration:
            pass
        except BaseException:
            raise _WorkerFailure("synthesis_failed") from None
        else:
            raise _WorkerFailure("synthesis_failed")
        header = (
            b"RIFF"
            + struct.pack("<I", len(pcm) + 36)
            + b"WAVEfmt "
            + struct.pack(
                "<IHHIIHH",
                16,
                1,
                1,
                sample_rate,
                sample_rate * 2,
                2,
                16,
            )
            + b"data"
            + struct.pack("<I", len(pcm))
        )
        return sample_rate, header + pcm
    except _WorkerFailure:
        _close_iterator(iterator)
        raise
    except (OverflowError, struct.error, TypeError, ValueError):
        _close_iterator(iterator)
        raise _WorkerFailure("synthesis_failed") from None


def _write_response(stream: BinaryIO, frame: object) -> None:
    """Write one response once; a failed pipe is never retried or resynchronized."""

    try:
        write_frame(stream, frame)
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()
    except BaseException:
        raise _ResponseFailure from None


def _send_error(stream: BinaryIO, code: str, request_id: int = 0) -> None:
    """Attempt one code-only ERROR response and deliberately ignore pipe loss."""

    if code not in _ERROR_CODES:
        code = "engine_failed"
    try:
        _write_response(
            stream,
            ProtocolFrame(FrameKind.ERROR, request_id, {"code": code}),
        )
    except BaseException:
        pass


def _close_engine(engine: object) -> bool:
    """Close one engine exactly once and report only whether cleanup succeeded."""

    try:
        close = getattr(engine, "close")
        if not callable(close):
            return False
        close()
        return True
    except BaseException:
        return False


def run_worker(
    request_stream: BinaryIO,
    response_stream: BinaryIO,
    engine_factory: Callable[[_WorkerConfig], _Engine] = _create_production_engine,
) -> int:
    """Serve one strict INIT-to-STOP worker session.

    ``request_stream`` and ``response_stream`` must be distinct binary streams;
    production uses duplicated unbuffered OS handles.  Any framing, schema,
    challenge, identity, inference, or output failure emits at most one stable
    code-only ERROR and poisons the session.  The function returns zero only
    after the engine closes and a STOPPED frame is written successfully.
    """

    engine = None  # type: Optional[_Engine]
    current_request_id = 0
    try:
        if request_stream is response_stream or not callable(engine_factory):
            raise _WorkerFailure("protocol_invalid")
        try:
            first = read_frame(request_stream)
        except ProtocolError:
            raise _WorkerFailure("protocol_invalid") from None
        if first.kind is not FrameKind.INIT:
            raise _WorkerFailure("protocol_invalid")
        config = _parse_init(first.metadata)
        try:
            candidate = engine_factory(config)
            if not callable(getattr(candidate, "synthesize", None)) or not callable(
                getattr(candidate, "close", None)
            ):
                raise TypeError
            engine = candidate
        except _WorkerFailure:
            raise
        except BaseException:
            raise _WorkerFailure("engine_failed") from None
        _write_response(
            response_stream,
            ProtocolFrame(
                FrameKind.READY,
                0,
                {
                    "binding_sha256": config.binding_sha256,
                    "challenge": config.challenge,
                    "schema": _SCHEMA_VERSION,
                },
            ),
        )

        while True:
            try:
                frame = read_frame(request_stream)
            except ProtocolError:
                raise _WorkerFailure("protocol_invalid") from None
            if frame.kind is FrameKind.STOP:
                _require_stop(frame, config.challenge)
                if not _close_engine(engine):
                    engine = None
                    raise _WorkerFailure("engine_failed")
                engine = None
                _write_response(
                    response_stream,
                    ProtocolFrame(
                        FrameKind.STOPPED,
                        0,
                        {"challenge": config.challenge, "schema": _SCHEMA_VERSION},
                    ),
                )
                return 0
            text, language = _parse_synthesize(
                frame,
                config,
                current_request_id,
            )
            current_request_id = frame.request_id
            try:
                result = engine.synthesize(text, language)
                sample_rate, wav = _build_wav(result)
            except _WorkerFailure:
                raise
            except BaseException:
                raise _WorkerFailure("synthesis_failed") from None
            _write_response(
                response_stream,
                ProtocolFrame(
                    FrameKind.AUDIO,
                    frame.request_id,
                    {
                        "challenge": config.challenge,
                        "format": "wav",
                        "sample_rate": sample_rate,
                        "schema": _SCHEMA_VERSION,
                    },
                    wav,
                ),
            )
    except _WorkerFailure as failure:
        error_request_id = (
            current_request_id if failure.code == "synthesis_failed" else 0
        )
        _send_error(response_stream, failure.code, error_request_id)
        return 1
    except _ResponseFailure:
        # A failed write may already have emitted a frame prefix. Appending an
        # ERROR would corrupt framing, so the only safe action is pipe closure.
        return 1
    except BaseException:
        _send_error(response_stream, "engine_failed", current_request_id)
        return 1
    finally:
        if engine is not None:
            _close_engine(engine)


def _main() -> int:
    """Duplicate protocol descriptors before upstream output is silenced."""

    request_descriptor = -1
    response_descriptor = -1
    try:
        request_descriptor = os.dup(sys.stdin.fileno())
        response_descriptor = os.dup(sys.stdout.fileno())
        with os.fdopen(request_descriptor, "rb", buffering=0) as request_stream:
            request_descriptor = -1
            with os.fdopen(response_descriptor, "wb", buffering=0) as response_stream:
                response_descriptor = -1
                return run_worker(request_stream, response_stream)
    except BaseException:
        return 1
    finally:
        for descriptor in (request_descriptor, response_descriptor):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["compute_runtime_manifest_digest", "run_worker"]
