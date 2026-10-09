"""Run one fail-closed, offline RVC v2 singing-voice conversion.

This adapter deliberately exposes only one input WAV, one new output WAV, and
explicit paths for the reviewed RVC source, checkpoint, FAISS index, HuBERT
directory, and RMVPE checkpoint.  Speaker selection, pitch extraction, index
mixing, consonant protection, loudness-envelope mixing, output sample rate,
and model family are fixed here so an Electron-selected song cannot expand the
upstream RVC command surface.  The sole per-song inference control is a
bounded key shift of minus two through plus two semitones.

The parent process is responsible for digest-pinning the private assets.  This
process independently rejects redirected paths, existing output files,
non-CUDA fallback, unexpected model metadata, and malformed inference output.
It also disables Python networking, Hugging Face online access, and CUDA Graph
capture before any vendor module is imported.  CUDA Graph capture is unsafe for
full songs because upstream caches a separate graph for each distinct segment
shape and can exhaust GPU memory.  Vendor imports run from the explicit source
root with an isolated ``sys.argv`` and working directory because upstream RVC
reads both during configuration.  A completed WAV is published under its final
name only after a private sibling file has been written and synchronized.  Each
job also resets the reviewed upstream seed and stable CUDA/cuDNN selection
controls before model construction so known process-launch randomness does not
select a different RVC waveform.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import importlib
from importlib.machinery import ModuleSpec
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
from types import ModuleType
from typing import Callable, Iterator, NoReturn, Optional, Sequence


_SPEAKER_ID = 0
_F0_METHOD = "rmvpe"
_INDEX_RATE = 0.00
_PROTECT = 0.33
_RMS_MIX_RATE = 0.25
_RESAMPLE_RATE = 0
_MODEL_SAMPLE_RATE = 40_000
_FULL_SCALE_THRESHOLD = 0.999
_MAX_FULL_SCALE_FRACTION = 0.01
_KEY_SHIFTS = frozenset({-2, -1, 0, 1, 2})
_MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ASSET_BYTES = 8 * 1024 * 1024 * 1024
_MAX_OUTPUT_SAMPLES = _MODEL_SAMPLE_RATE * 15 * 60
_INFERENCE_SEED = 114_514
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_OPTIONS = (
    "--source-root",
    "--model",
    "--index",
    "--hubert",
    "--rmvpe",
    "--input",
    "--output",
    "--key-shift",
)
_OFFLINE_ENVIRONMENT = {
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "NO_PROXY": "*",
    "no_proxy": "*",
}
_FIXED_RUNTIME_ENVIRONMENT = {
    **_OFFLINE_ENVIRONMENT,
    # Pin cuBLAS workspace selection before the first CUDA context is created.
    # The process-scoped setting removes another source of run-to-run kernel
    # variation without changing the parent application's CUDA behavior.
    "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
    # RVC divides long recordings at quiet points, so segment lengths vary.
    # Its CUDA Graph cache retains shape-specific captures and can consume the
    # entire GPU during a song; eager inference stays bounded and deterministic.
    "RVC_CUDA_GRAPH": "0",
}
_PROXY_KEYS = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
)
_VENDOR_NAMESPACES = {
    "configs": "configs",
    "infer": "infer",
    "tools": "tools",
}


class _RvcRuntimeFailure(RuntimeError):
    """Carry one stable error code without exposing a private native path."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ClosedArgumentParser(argparse.ArgumentParser):
    """Convert argparse diagnostics into the wrapper's path-free failure."""

    def error(self, _message: str) -> NoReturn:
        """Reject an invalid command line without printing native values."""

        raise _RvcRuntimeFailure("invalid_request")


@dataclass(frozen=True)
class _Request:
    """Bind the sole conversion request after its paths have been verified."""

    source_root: Path
    model: Path
    index: Path
    hubert: Path
    rmvpe: Path
    input_audio: Path
    output_audio: Path
    key_shift: int


@dataclass(frozen=True)
class _RvcApi:
    """Hold the two reviewed upstream constructors used by the adapter."""

    config_factory: Callable[[], object]
    converter_factory: Callable[[object], object]


def _fail(code: str) -> NoReturn:
    """Stop processing with one renderer-safe, path-free failure code."""

    raise _RvcRuntimeFailure(code)


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows reparse points without breaking portable tests."""

    return bool(
        getattr(info, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_path(value: str, *, code: str) -> Path:
    """Return one normalized absolute spelling without resolving links."""

    if not value or "\x00" in value or len(value) > 32_767:
        _fail(code)
    candidate = Path(value)
    if not candidate.is_absolute():
        _fail(code)
    try:
        return Path(os.path.abspath(str(candidate)))
    except (OSError, RuntimeError, ValueError):
        _fail(code)


def _require_existing_path(
    path: Path,
    *,
    directory: bool,
    code: str,
    minimum_bytes: int = 1,
    maximum_bytes: Optional[int] = None,
) -> Path:
    """Require an unredirected regular file or directory and safe parents."""

    try:
        current = Path(path.anchor)
        parts = path.parts[1:] if path.anchor else path.parts
        if not parts:
            raise OSError
        leaf_info = None  # type: Optional[os.stat_result]
        for index, part in enumerate(parts):
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise OSError
            final = index == len(parts) - 1
            if final:
                leaf_info = info
            elif not stat.S_ISDIR(info.st_mode):
                raise OSError
        if leaf_info is None:
            raise OSError
        if directory:
            if not stat.S_ISDIR(leaf_info.st_mode):
                raise OSError
        elif not stat.S_ISREG(leaf_info.st_mode):
            raise OSError
        if not directory and maximum_bytes is not None:
            if not minimum_bytes <= leaf_info.st_size <= maximum_bytes:
                raise OSError
    except (OSError, RuntimeError, ValueError):
        _fail(code)
    return path


def _require_new_output(path: Path) -> Path:
    """Require a WAV destination below an existing unredirected directory."""

    if path.suffix.casefold() != ".wav":
        _fail("invalid_output")
    _require_existing_path(
        path.parent,
        directory=True,
        code="invalid_output",
    )
    try:
        os.lstat(path)
    except FileNotFoundError:
        return path
    except (OSError, RuntimeError, ValueError):
        _fail("invalid_output")
    _fail("output_exists")


def _require_hubert_directory(path: Path) -> Path:
    """Require the explicit local Transformers HuBERT directory and files."""

    _require_existing_path(path, directory=True, code="invalid_hubert")
    for name in ("config.json", "preprocessor_config.json", "pytorch_model.bin"):
        _require_existing_path(
            path / name,
            directory=False,
            code="invalid_hubert",
            maximum_bytes=_MAX_ASSET_BYTES,
        )
    return path


def _validate_request(namespace: argparse.Namespace) -> _Request:
    """Validate all explicit assets before any vendor code or GPU is loaded."""

    source_root = _require_existing_path(
        _absolute_path(namespace.source_root, code="invalid_source"),
        directory=True,
        code="invalid_source",
    )
    for relative in (
        Path("configs") / "config.py",
        Path("infer") / "hubert.py",
        Path("infer") / "rmvpe.py",
        Path("infer") / "vc" / "modules.py",
    ):
        _require_existing_path(
            source_root / relative,
            directory=False,
            code="invalid_source",
            maximum_bytes=16 * 1024 * 1024,
        )

    model = _require_existing_path(
        _absolute_path(namespace.model, code="invalid_model"),
        directory=False,
        code="invalid_model",
        maximum_bytes=_MAX_ASSET_BYTES,
    )
    if model.suffix.casefold() != ".pth":
        _fail("invalid_model")

    index = _require_existing_path(
        _absolute_path(namespace.index, code="invalid_index"),
        directory=False,
        code="invalid_index",
        maximum_bytes=_MAX_ASSET_BYTES,
    )
    if index.suffix.casefold() != ".index":
        _fail("invalid_index")

    hubert = _require_hubert_directory(
        _absolute_path(namespace.hubert, code="invalid_hubert")
    )
    rmvpe = _require_existing_path(
        _absolute_path(namespace.rmvpe, code="invalid_rmvpe"),
        directory=False,
        code="invalid_rmvpe",
        maximum_bytes=_MAX_ASSET_BYTES,
    )
    # Upstream joins ``rmvpe_root`` with this fixed basename.  Refusing an
    # alias ensures the explicit file is the exact file vendor code will open.
    if rmvpe.name != "rmvpe.pt":
        _fail("invalid_rmvpe")

    input_audio = _require_existing_path(
        _absolute_path(namespace.input_audio, code="invalid_input"),
        directory=False,
        code="invalid_input",
        maximum_bytes=_MAX_INPUT_BYTES,
    )
    if input_audio.suffix.casefold() != ".wav":
        _fail("invalid_input")

    output_audio = _require_new_output(
        _absolute_path(namespace.output_audio, code="invalid_output")
    )
    key_shift = namespace.key_shift
    if type(key_shift) is not int or key_shift not in _KEY_SHIFTS:
        _fail("invalid_key_shift")

    return _Request(
        source_root=source_root,
        model=model,
        index=index,
        hubert=hubert,
        rmvpe=rmvpe,
        input_audio=input_audio,
        output_audio=output_audio,
        key_shift=key_shift,
    )


def _parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    """Parse exactly eight unique option/value pairs without abbreviation."""

    values = list(arguments)
    if (
        len(values) != len(_OPTIONS) * 2
        or len(set(values[::2])) != len(_OPTIONS)
        or set(values[::2]) != set(_OPTIONS)
    ):
        _fail("invalid_request")
    parser = _ClosedArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--hubert", required=True)
    parser.add_argument("--rmvpe", required=True)
    parser.add_argument("--input", dest="input_audio", required=True)
    parser.add_argument("--output", dest="output_audio", required=True)
    parser.add_argument("--key-shift", required=True, type=int)
    return parser.parse_args(values)


def _deny_network(*_args: object, **_kwargs: object) -> NoReturn:
    """Reject every Python socket operation attempted by vendor code."""

    raise OSError("Network access is disabled for RVC inference.")


def _is_vendor_module_name(name: str) -> bool:
    """Return whether one module name belongs to an RVC source namespace."""

    return any(
        name == root or name.startswith(root + ".")
        for root in _VENDOR_NAMESPACES
    )


def _reject_preloaded_vendor_modules() -> None:
    """Reject namespace state that could redirect imports before validation.

    The reviewed checkout uses namespace packages without ``__init__.py``.
    Merely prepending ``sys.path`` would therefore let a previously imported
    regular package named ``infer``, ``configs``, or ``tools`` control all
    subsequent submodule lookups before an origin check could run.
    """

    if any(_is_vendor_module_name(name) for name in sys.modules):
        _fail("source_binding_failed")


def _install_vendor_namespaces(request: _Request) -> None:
    """Install deterministic source-root-only namespace package bindings."""

    for name, relative in _VENDOR_NAMESPACES.items():
        directory = _require_existing_path(
            request.source_root / relative,
            directory=True,
            code="source_binding_failed",
        )
        package = ModuleType(name)
        package.__package__ = name
        package.__path__ = [str(directory)]  # type: ignore[attr-defined]
        specification = ModuleSpec(name, loader=None, is_package=True)
        specification.submodule_search_locations = [str(directory)]
        package.__spec__ = specification
        sys.modules[name] = package


def _audit_vendor_modules(request: _Request) -> None:
    """Authenticate every loaded RVC namespace module and package path."""

    for name, module in tuple(sys.modules.items()):
        if not _is_vendor_module_name(name):
            continue
        root_name = name.split(".", 1)[0]
        expected_root = request.source_root / _VENDOR_NAMESPACES[root_name]
        relative_parts = name.split(".")[1:]
        expected_node = expected_root.joinpath(*relative_parts)
        module_file = getattr(module, "__file__", None)
        package_paths = getattr(module, "__path__", None)
        if type(module_file) is str:
            candidate = Path(os.path.abspath(module_file))
            allowed_files = (
                expected_node.with_suffix(".py"),
                expected_node / "__init__.py",
            )
            if not any(
                os.path.normcase(str(candidate)) == os.path.normcase(str(expected))
                for expected in allowed_files
            ):
                _fail("source_binding_failed")
            _require_existing_path(
                candidate,
                directory=False,
                code="source_binding_failed",
                minimum_bytes=0,
                maximum_bytes=64 * 1024 * 1024,
            )
            origin = getattr(getattr(module, "__spec__", None), "origin", None)
            if type(origin) is str and origin not in {"namespace", "built-in"}:
                normalized_origin = Path(os.path.abspath(origin))
                if os.path.normcase(str(normalized_origin)) != os.path.normcase(
                    str(candidate)
                ):
                    _fail("source_binding_failed")
        elif package_paths is not None:
            try:
                paths = tuple(package_paths)
            except (TypeError, ValueError):
                _fail("source_binding_failed")
            if not paths:
                _fail("source_binding_failed")
            for value in paths:
                if type(value) is not str:
                    _fail("source_binding_failed")
                candidate = Path(os.path.abspath(value))
                if os.path.normcase(str(candidate)) != os.path.normcase(
                    str(expected_node)
                ):
                    _fail("source_binding_failed")
                _require_existing_path(
                    candidate,
                    directory=True,
                    code="source_binding_failed",
                )
        else:
            _fail("source_binding_failed")


def _remove_vendor_modules() -> None:
    """Remove source-scoped modules after the one-shot inference finishes."""

    for name in tuple(sys.modules):
        if _is_vendor_module_name(name):
            sys.modules.pop(name, None)


@contextmanager
def _runtime_scope(request: _Request) -> Iterator[None]:
    """Isolate vendor imports, arguments, working directory, and networking."""

    _reject_preloaded_vendor_modules()
    previous_directory = Path.cwd()
    previous_arguments = sys.argv[:]
    previous_path = sys.path[:]
    previous_dont_write_bytecode = sys.dont_write_bytecode
    environment_keys = set(_FIXED_RUNTIME_ENVIRONMENT) | set(_PROXY_KEYS) | {
        "weight_root",
        "index_root",
        "outside_index_root",
        "rmvpe_root",
    }
    missing = object()
    previous_environment: dict[str, object] = {
        key: os.environ.get(key, missing) for key in environment_keys
    }
    socket_methods = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "send": socket.socket.send,
        "sendall": socket.socket.sendall,
        "sendto": socket.socket.sendto,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
        "getnameinfo": socket.getnameinfo,
    }
    try:
        for key in _PROXY_KEYS:
            os.environ.pop(key, None)
        os.environ.update(_FIXED_RUNTIME_ENVIRONMENT)
        os.environ["weight_root"] = str(request.model.parent)
        os.environ["index_root"] = str(request.index.parent)
        os.environ["outside_index_root"] = str(request.index.parent)
        os.environ["rmvpe_root"] = str(request.rmvpe.parent)
        socket.socket.connect = _deny_network  # type: ignore[method-assign]
        socket.socket.connect_ex = _deny_network  # type: ignore[method-assign]
        socket.socket.send = _deny_network  # type: ignore[method-assign]
        socket.socket.sendall = _deny_network  # type: ignore[method-assign]
        socket.socket.sendto = _deny_network  # type: ignore[method-assign]
        socket.create_connection = _deny_network
        socket.getaddrinfo = _deny_network
        socket.gethostbyname = _deny_network
        socket.gethostbyname_ex = _deny_network
        socket.getnameinfo = _deny_network
        os.chdir(request.source_root)
        sys.dont_write_bytecode = True
        sys.path[:] = [str(request.source_root)] + [
            entry for entry in sys.path if entry != str(request.source_root)
        ]
        _install_vendor_namespaces(request)
        # Config.arg_parse() consumes process arguments.  Hiding wrapper flags
        # prevents private paths from becoming an accidental vendor interface.
        sys.argv[:] = [str(Path(__file__).resolve())]
        yield
    finally:
        socket.socket.connect = socket_methods["connect"]  # type: ignore[method-assign,assignment]
        socket.socket.connect_ex = socket_methods["connect_ex"]  # type: ignore[method-assign,assignment]
        socket.socket.send = socket_methods["send"]  # type: ignore[method-assign,assignment]
        socket.socket.sendall = socket_methods["sendall"]  # type: ignore[method-assign,assignment]
        socket.socket.sendto = socket_methods["sendto"]  # type: ignore[method-assign,assignment]
        socket.create_connection = socket_methods["create_connection"]  # type: ignore[assignment]
        socket.getaddrinfo = socket_methods["getaddrinfo"]  # type: ignore[assignment]
        socket.gethostbyname = socket_methods["gethostbyname"]  # type: ignore[assignment]
        socket.gethostbyname_ex = socket_methods["gethostbyname_ex"]  # type: ignore[assignment]
        socket.getnameinfo = socket_methods["getnameinfo"]  # type: ignore[assignment]
        _remove_vendor_modules()
        sys.dont_write_bytecode = previous_dont_write_bytecode
        sys.argv[:] = previous_arguments
        sys.path[:] = previous_path
        os.chdir(previous_directory)
        for key, value in previous_environment.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)


def _require_module_origin(module: object, expected: Path) -> None:
    """Ensure an imported vendor module came from the explicit source root."""

    reported = getattr(module, "__file__", None)
    if type(reported) is not str:
        _fail("source_binding_failed")
    try:
        actual = Path(os.path.abspath(reported))
    except (OSError, RuntimeError, ValueError):
        _fail("source_binding_failed")
    if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
        _fail("source_binding_failed")


def _load_rvc_api(request: _Request) -> _RvcApi:
    """Import the explicit RVC tree and bind its HuBERT path before inference."""

    try:
        hubert_module = importlib.import_module("infer.hubert")
        _require_module_origin(
            hubert_module,
            request.source_root / "infer" / "hubert.py",
        )
        setattr(hubert_module, "HUBERT_MODEL_PATH", request.hubert)
        normalization_probe = getattr(
            hubert_module,
            "hubert_audio_requires_normalization",
            None,
        )
        cache_clear = getattr(normalization_probe, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()

        config_module = importlib.import_module("configs.config")
        converter_module = importlib.import_module("infer.vc.modules")
        rmvpe_module = importlib.import_module("infer.rmvpe")
        _require_module_origin(
            config_module,
            request.source_root / "configs" / "config.py",
        )
        _require_module_origin(
            converter_module,
            request.source_root / "infer" / "vc" / "modules.py",
        )
        _require_module_origin(
            rmvpe_module,
            request.source_root / "infer" / "rmvpe.py",
        )
        config_factory = getattr(config_module, "Config")
        converter_factory = getattr(converter_module, "VC")
        if not callable(config_factory) or not callable(converter_factory):
            raise TypeError
        if Path(getattr(hubert_module, "HUBERT_MODEL_PATH")) != request.hubert:
            raise TypeError
        _audit_vendor_modules(request)
        return _RvcApi(config_factory, converter_factory)
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("source_binding_failed")


def _cuda_device(configuration: object) -> str:
    """Require upstream to select CUDA rather than CPU or DirectML fallback."""

    device = str(getattr(configuration, "device", ""))
    if bool(getattr(configuration, "dml", False)) or not device.casefold().startswith(
        "cuda"
    ):
        _fail("cuda_required")
    return device


def _seed_torch_inference() -> None:
    """Reset PyTorch and CUDA to the reviewed reproducible inference seed.

    Upstream RVC's WebUI fixes ``torch.manual_seed`` to 114514 before it
    constructs the model.  This one-shot adapter additionally reseeds every
    CUDA generator and disables cuDNN benchmarking to remove the reviewed
    run-to-run randomness; independent jobs produced byte-identical PCM during
    fixed-runtime validation.  PyTorch's strict deterministic-algorithm mode
    cannot be enabled here because the pitch-conditioned generator uses a CUDA
    cumulative sum for which the pinned Torch build provides no
    deterministic-mode implementation, so cross-runtime reproducibility is
    deliberately not promised.
    """

    try:
        torch_module = importlib.import_module("torch")
        manual_seed = getattr(torch_module, "manual_seed")
        cuda_module = getattr(torch_module, "cuda")
        cuda_manual_seed_all = getattr(cuda_module, "manual_seed_all")
        cudnn_backend = getattr(getattr(torch_module, "backends"), "cudnn")
        if not all(
            callable(operation)
            for operation in (
                manual_seed,
                cuda_manual_seed_all,
            )
        ):
            raise TypeError

        # Reset on every request rather than relying on process startup state.
        # ``manual_seed`` follows the upstream WebUI; ``manual_seed_all`` makes
        # the all-device CUDA intent explicit and remains safe before lazy CUDA
        # initialization.  CPU or DirectML still fail at ``_cuda_device``.
        manual_seed(_INFERENCE_SEED)
        cuda_manual_seed_all(_INFERENCE_SEED)
        setattr(cudnn_backend, "benchmark", False)
        setattr(cudnn_backend, "deterministic", True)
        if bool(getattr(cudnn_backend, "benchmark")) or not bool(
            getattr(cudnn_backend, "deterministic")
        ):
            raise TypeError
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("configuration_failed")


def _convert(
    request: _Request,
    api_loader: Callable[[_Request], _RvcApi],
    seed_initializer: Callable[[], None],
) -> tuple[int, object]:
    """Run one fixed speaker-zero, RMVPE RVC inference request."""

    seed_initializer()
    api = api_loader(request)
    try:
        configuration = api.config_factory()
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("configuration_failed")
    _cuda_device(configuration)

    converter = None  # type: Optional[object]
    loaded = False
    try:
        converter = api.converter_factory(configuration)
        get_vc = getattr(converter, "get_vc")
        vc_single = getattr(converter, "vc_single")
        if not callable(get_vc) or not callable(vc_single):
            raise TypeError
        get_vc(request.model.name, _PROTECT, _PROTECT)
        loaded = True
        if (
            getattr(converter, "version", None) != "v2"
            or int(getattr(converter, "if_f0", 0)) != 1
            or int(getattr(converter, "tgt_sr", 0)) != _MODEL_SAMPLE_RATE
        ):
            _fail("model_incompatible")
        _status, result = vc_single(
            _SPEAKER_ID,
            str(request.input_audio),
            request.key_shift,
            _F0_METHOD,
            str(request.index),
            _INDEX_RATE,
            _RESAMPLE_RATE,
            _RMS_MIX_RATE,
            _PROTECT,
        )
        _audit_vendor_modules(request)
        if (
            type(result) is not tuple
            or len(result) != 2
            or type(result[0]) is not int
            or result[0] != _MODEL_SAMPLE_RATE
            or result[1] is None
        ):
            _fail("inference_failed")
        return result[0], result[1]
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("inference_failed")
    finally:
        if loaded and converter is not None:
            try:
                getattr(converter, "get_vc")("", _PROTECT, _PROTECT)
            except BaseException:
                # Model release is best effort because the process exits after
                # this sole job; it must not replace a completed audio result.
                pass


def _write_pcm16_wav(path: Path, sample_rate: int, samples: object) -> None:
    """Write one validated mono integer/float vector as signed 16-bit PCM."""

    try:
        import numpy as np  # type: ignore[import-not-found]

        source = np.asarray(samples)
        if (
            source.ndim != 1
            or not 1 <= source.size <= _MAX_OUTPUT_SAMPLES
        ):
            _fail("invalid_audio")
        # RVC returns int16 PCM, while other reviewed adapters may return
        # normalized floats.  Converting integers directly to float32 would
        # reinterpret 16384 as +16384.0 and soundfile would clip almost every
        # voiced sample to full scale, producing the reported electric buzz.
        if source.dtype == np.dtype(np.int16):
            audio = source.astype(np.float32) / 32_768.0
        elif np.issubdtype(source.dtype, np.integer):
            # Upstream's reviewed VC contract is signed 16-bit PCM.  Rejecting
            # every other integer width/signedness avoids guessing whether an
            # arbitrary integer vector is raw PCM, biased unsigned PCM, or an
            # already scaled signal.
            _fail("invalid_audio")
        elif np.issubdtype(source.dtype, np.floating):
            audio = source.astype(np.float32)
            if (
                not bool(np.all(np.isfinite(audio)))
                or float(np.max(np.abs(audio))) > 1.000001
            ):
                _fail("invalid_audio")
            # Permit only floating-point roundoff beyond full scale; clipping
            # a genuinely unnormalized upstream vector would recreate the
            # all-or-nothing waveform that users hear as electric distortion.
            audio = np.clip(audio, -1.0, 1.0)
        else:
            _fail("invalid_audio")
        if not bool(np.all(np.isfinite(audio))):
            _fail("invalid_audio")
        absolute = np.abs(audio)
        if not bool(np.any(absolute > 0.0)):
            # A zero-length failure is caught above; a full-length zero vector
            # instead means upstream silently failed after accepting the job.
            _fail("invalid_audio")
        if source.size >= _MODEL_SAMPLE_RATE and (
            float(np.count_nonzero(absolute >= _FULL_SCALE_THRESHOLD))
            / float(source.size)
            > _MAX_FULL_SCALE_FRACTION
        ):
            # RVC deliberately normalizes its output below full scale.  A long
            # vector pinned there is therefore corrupted or was rescaled as
            # unbounded PCM, the exact failure users hear as electric buzzing.
            _fail("invalid_audio")
        import soundfile as sf  # type: ignore[import-not-found]

        sf.write(str(path), audio, sample_rate, format="WAV", subtype="PCM_16")
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("output_failed")


def _hardlink_is_unsupported(error: OSError) -> bool:
    """Recognize filesystems that cannot atomically create a hard-link name."""

    unsupported_errnos = {
        errno.EPERM,
        errno.EXDEV,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    unsupported_windows_errors = {
        1,  # ERROR_INVALID_FUNCTION
        50,  # ERROR_NOT_SUPPORTED
        1314,  # ERROR_PRIVILEGE_NOT_HELD
    }
    return error.errno in unsupported_errnos or getattr(
        error, "winerror", None
    ) in unsupported_windows_errors


def _identity(path: Path) -> tuple[int, int]:
    """Return the stable device/inode identity of one unredirected file."""

    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise OSError
        return int(info.st_dev), int(info.st_ino)
    except (OSError, RuntimeError, ValueError):
        _fail("output_failed")


def _remove_owned_reservation(output: Path, identity: tuple[int, int]) -> None:
    """Remove a failed fallback reservation only if it is still our file."""

    try:
        if _identity(output) == identity:
            output.unlink()
    except (OSError, _RvcRuntimeFailure):
        pass


def _publish_without_hardlink(staged: Path, output: Path) -> None:
    """Publish without clobbering when the volume rejects hard links.

    Windows ``rename`` is an atomic no-replace operation.  POSIX ``rename``
    overwrites, so the portable fallback first claims the final name with
    ``O_EXCL`` and then atomically replaces only that private placeholder.
    The latter has a tiny empty-placeholder window, but both paths preserve a
    pre-existing file and operate inside the parent-owned private UUID job
    directory where untrusted concurrent writers are excluded.
    """

    if os.name == "nt":
        try:
            os.rename(staged, output)
            return
        except FileExistsError:
            _fail("output_exists")
        except OSError:
            _fail("output_failed")

    descriptor = -1
    reservation: Optional[tuple[int, int]] = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(output, flags, 0o600)
        info = os.fstat(descriptor)
        reservation = (int(info.st_dev), int(info.st_ino))
    except FileExistsError:
        _fail("output_exists")
    except OSError:
        _fail("output_failed")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if reservation is None or _identity(output) != reservation:
        _fail("output_exists")
    try:
        os.replace(staged, output)
    except OSError:
        _remove_owned_reservation(output, reservation)
        _fail("output_failed")


def _publish_new_name(staged: Path, output: Path) -> None:
    """Prefer atomic hard-link publication and use one safe local fallback."""

    try:
        os.link(staged, output)
    except FileExistsError:
        _fail("output_exists")
    except OSError as error:
        if not _hardlink_is_unsupported(error):
            _fail("output_failed")
        _publish_without_hardlink(staged, output)


def _publish_audio_atomic(
    output: Path,
    sample_rate: int,
    samples: object,
    writer: Callable[[Path, int, object], None] = _write_pcm16_wav,
) -> None:
    """Publish a complete new WAV atomically without replacing any file.

    A hard link gives both Windows and POSIX an atomic create-if-absent step.
    Volumes without hard links use the documented same-directory no-clobber
    fallback.  The private sibling is removed immediately after publication.
    """

    _require_new_output(output)
    staged = output.parent / (
        f".{output.name}.{secrets.token_hex(16)}.partial.wav"
    )
    published = False
    try:
        writer(staged, sample_rate, samples)
        _require_existing_path(
            staged,
            directory=False,
            code="output_failed",
            maximum_bytes=_MAX_INPUT_BYTES,
        )
        try:
            # Windows rejects ``fsync`` on a read-only CRT descriptor even
            # though the underlying file is readable, so keep this private
            # staging handle writable until its contents reach the volume.
            with staged.open("rb+") as stream:
                os.fsync(stream.fileno())
        except OSError:
            _fail("output_failed")
        _publish_new_name(staged, output)
        published = True
        try:
            staged.unlink()
        except OSError:
            # Publication succeeded, so keep the valid final output and let
            # the managed job-root cleanup remove this unique private sibling.
            pass
        _require_existing_path(
            output,
            directory=False,
            code="output_failed",
            maximum_bytes=_MAX_INPUT_BYTES,
        )
    except BaseException:
        if published:
            try:
                if output.is_file() and not output.is_symlink():
                    output.unlink()
            except OSError:
                pass
        try:
            if staged.is_file() and not staged.is_symlink():
                staged.unlink()
        except OSError:
            pass
        raise


def _run_request(
    request: _Request,
    *,
    api_loader: Callable[[_Request], _RvcApi] = _load_rvc_api,
    publisher: Callable[[Path, int, object], None] = _publish_audio_atomic,
    seed_initializer: Callable[[], None] = _seed_torch_inference,
) -> None:
    """Execute one validated request inside the isolated vendor scope."""

    with _runtime_scope(request):
        sample_rate, samples = _convert(request, api_loader, seed_initializer)
        publisher(request.output_audio, sample_rate, samples)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one explicit RVC conversion and return a stable process status."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        request = _validate_request(_parse_arguments(arguments))
        _run_request(request)
        return 0
    except _RvcRuntimeFailure as error:
        print(f"RVC_RUNTIME_ERROR:{error.code}", file=sys.stderr, flush=True)
        return 2 if error.code == "invalid_request" else 1
    except KeyboardInterrupt:
        print("RVC_RUNTIME_ERROR:cancelled", file=sys.stderr, flush=True)
        return 130
    except BaseException:
        # Vendor exceptions can include private paths, model metadata, and
        # environment details.  Collapse every unclassified failure here.
        print("RVC_RUNTIME_ERROR:runtime_failed", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
