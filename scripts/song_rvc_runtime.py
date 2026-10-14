"""Run one fail-closed, offline RVC v2 singing-voice conversion.

This adapter deliberately exposes only one input WAV, one new output WAV, and
explicit paths for the reviewed RVC source, checkpoint, FAISS index, HuBERT
directory, and RMVPE checkpoint.  Speaker selection, pitch extraction, index
mixing, the upstream Protect argument, loudness-envelope mixing, output sample
rate, and model family are fixed here so an Electron-selected song cannot
expand the upstream RVC command surface.  With the fixed zero index rate, the
Protect feature blend is an identity operation; it must not be treated as a
guarantee that unvoiced content is preserved.  The sole per-song inference
control is a bounded key shift of minus twelve through plus twelve semitones.

The parent process is responsible for digest-pinning the private assets.  This
process independently rejects redirected paths, existing output files,
non-CUDA fallback, unexpected model metadata, and malformed inference output.
The production path owns exact source-time segment crops while keeping the
upstream inference inputs and waveform free of transition replacement. Its
small maintained pipeline adapter recovers already generated right context
instead of losing 10 ms frames at HuBERT's 20 ms boundaries or padding a short
final tail. An experimental language-independent false-voicing analyzer is
retained for offline evaluation, but it is deliberately outside production:
replacing detected spans changed the vocal envelope and created audible stalls
after mixing even when the detector identified the intended transition.
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
from importlib.util import module_from_spec, spec_from_file_location
import math
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
from types import ModuleType
from typing import Any, Callable, Iterator, NoReturn, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from numpy.typing import NDArray


_SPEAKER_ID = 0
_F0_METHOD = "rmvpe"
_INDEX_RATE = 0.00
_PROTECT = 0.33
_RMS_MIX_RATE = 0.25
_RESAMPLE_RATE = 0
_MODEL_SAMPLE_RATE = 40_000
_FULL_SCALE_THRESHOLD = 0.999
_MAX_FULL_SCALE_FRACTION = 0.01
_KEY_SHIFTS = frozenset(range(-12, 13))
_MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ASSET_BYTES = 8 * 1024 * 1024 * 1024
_MAX_OUTPUT_SAMPLES = _MODEL_SAMPLE_RATE * 15 * 60
_INFERENCE_SEED = 114_514
_ARTIFACT_ANALYSIS_SAMPLE_RATE = 44_100
_ARTIFACT_FRAME_LENGTH = 2_048
_ARTIFACT_HOP_LENGTH = 256
_ARTIFACT_F0_MIN_HZ = 70.0
_ARTIFACT_F0_MAX_HZ = 900.0
_ARTIFACT_PITCH_RESOLUTION = 0.20
_ARTIFACT_RESAMPLE_CORE_SECONDS = 10.0
_ARTIFACT_PYIN_CORE_SECONDS = 10.0
_ARTIFACT_PYIN_CONTEXT_SECONDS = 2.0
_ARTIFACT_YIN_SAMPLE_RATE = 11_025
_ARTIFACT_YIN_FRAME_LENGTH = 512
_ARTIFACT_YIN_HOP_LENGTH = 64
_ARTIFACT_YIN_CORE_FRAMES = 2_048
_ARTIFACT_YIN_CONTEXT_FRAMES = 4
_ARTIFACT_YIN_TROUGH_THRESHOLD = 0.20
_ARTIFACT_MAX_VITERBI_STATES = 512
_ARTIFACT_MAX_VITERBI_FRAMES = 3_000
_ARTIFACT_MAX_VITERBI_PREDECESSORS = 64
_ARTIFACT_PREFILTER_OUTPUT_PERIODICITY = 0.93
_ARTIFACT_PREFILTER_PERIODICITY_INCREASE = 0.12
_ARTIFACT_INTEREST_RADIUS_SECONDS = 0.100
_ARTIFACT_FINE_CONTEXT_SECONDS = 0.250
_ARTIFACT_FINE_MINIMUM_BUDGET_SECONDS = 12.0
_ARTIFACT_FINE_MAXIMUM_BUDGET_SECONDS = 30.0
_ARTIFACT_FINE_BUDGET_FRACTION = 0.15
_ARTIFACT_SOURCE_ACTIVE_DBFS = -45.0
_ARTIFACT_OUTPUT_ACTIVE_DBFS = -36.0
_ARTIFACT_OUTPUT_VOICED_PROBABILITY = 0.90
_ARTIFACT_OUTPUT_PERIODICITY = 0.95
_ARTIFACT_PERIODICITY_INCREASE = 0.18
_ARTIFACT_MAX_PITCH_ERROR_CENTS = 150.0
_ARTIFACT_MAX_GAP_SECONDS = 0.012
_ARTIFACT_MIN_RUN_SECONDS = 0.060
_ARTIFACT_DENSITY_WINDOW_SECONDS = 1.0
_ARTIFACT_MIN_DENSITY = 0.10
_ARTIFACT_MAX_ANALYSIS_GAIN_DB = 12.0
_ARTIFACT_MAX_REPAIR_FRACTION = 0.15
_ARTIFACT_SOURCE_HIGHPASS_HZ = 3_200.0
_ARTIFACT_REPAIR_DEPTH = 1.0
_ARTIFACT_SOURCE_GAIN = 0.10
_ARTIFACT_FADE_SECONDS = 0.005
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


class _ArtifactAnalysisSkipped(RuntimeError):
    """Request a fail-open guard result when bounded analysis is unavailable."""


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
    """Hold reviewed constructors and the production instance-configuration hook."""

    config_factory: Callable[[], object]
    converter_factory: Callable[[object], object]
    configure_converter: Optional[Callable[[object], Callable[[], None]]] = None


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


def _load_pipeline_helper() -> ModuleType:
    """Load only the maintained sibling helper under isolated native Python."""

    # The bundled python39._pth omits both the current and script directories.
    # Load the verified sibling by path instead of extending module search or
    # relying on package imports that work only in the development interpreter.
    helper_path = _require_existing_path(
        Path(__file__).with_name("song_rvc_pipeline.py"),
        directory=False,
        code="source_binding_failed",
    )
    spec = spec_from_file_location("_elysia_song_rvc_pipeline", helper_path)
    if spec is None or spec.loader is None:
        _fail("source_binding_failed")
    helper = module_from_spec(spec)
    spec.loader.exec_module(helper)
    _require_module_origin(helper, helper_path)
    return helper


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

        def _configure_converter(converter: object) -> Callable[[], None]:
            # Bind after get_vc constructs this request's Pipeline. Keeping the
            # hook on the real loader preserves model-free test injection and
            # avoids replacing vendor classes or modifying authenticated files.
            pipeline_module = importlib.import_module("infer.vc.pipeline")
            _require_module_origin(
                pipeline_module,
                request.source_root / "infer" / "vc" / "pipeline.py",
            )
            helper = _load_pipeline_helper()
            return helper.bind_exact_timing_pipeline(
                converter, pipeline_module, request.input_audio,
            )

        return _RvcApi(config_factory, converter_factory, _configure_converter)
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


def _active_rms_db(audio: object, *, sample_rate: int, np_module: Any) -> float:
    """Estimate active RMS while preventing silence from setting the level."""

    np = np_module
    samples = np.asarray(audio, dtype=np.float32)
    frame_samples = max(1, int(round(0.4 * sample_rate)))
    hop_samples = max(1, int(round(0.1 * sample_rate)))
    if len(samples) < frame_samples:
        powers = np.asarray(
            [float(np.mean(np.square(samples, dtype=np.float64)))],
            dtype=np.float64,
        )
    else:
        powers = np.asarray(
            [
                float(
                    np.mean(
                        np.square(
                            samples[start : start + frame_samples],
                            dtype=np.float64,
                        )
                    )
                )
                for start in range(
                    0,
                    len(samples) - frame_samples + 1,
                    hop_samples,
                )
            ],
            dtype=np.float64,
        )
    levels = 10.0 * np.log10(np.maximum(powers, 1e-12))
    above_floor = levels > -70.0
    if not bool(np.any(above_floor)):
        return -120.0
    relative_gate = float(np.max(levels[above_floor]) - 30.0)
    selected = powers[above_floor & (levels >= relative_gate)]
    return float(10.0 * np.log10(max(float(np.mean(selected)), 1e-12)))


def _match_analysis_level(
    source: object,
    output: object,
    *,
    np_module: Any,
) -> NDArray[Any]:
    """Level-match detector audio without changing the waveform we publish.

    The comparison is bounded to twelve decibels and exists only so pYIN and
    RMS gates compare source and converted vocals at equivalent active levels.
    The repair itself always uses the untouched RVC result.
    """

    np = np_module
    source_level = _active_rms_db(
        source,
        sample_rate=_ARTIFACT_ANALYSIS_SAMPLE_RATE,
        np_module=np,
    )
    output_level = _active_rms_db(
        output,
        sample_rate=_ARTIFACT_ANALYSIS_SAMPLE_RATE,
        np_module=np,
    )
    gain_db = min(
        _ARTIFACT_MAX_ANALYSIS_GAIN_DB,
        max(-_ARTIFACT_MAX_ANALYSIS_GAIN_DB, source_level - output_level),
    )
    return np.asarray(output, dtype=np.float32) * (10.0 ** (gain_db / 20.0))


def _close_artifact_gaps(flags: Sequence[object], maximum_frames: int) -> list[bool]:
    """Fill only short false runs enclosed by artifact evidence on both sides."""

    closed = [bool(value) for value in flags]
    index = 0
    while index < len(closed):
        if closed[index]:
            index += 1
            continue
        gap_start = index
        while index < len(closed) and not closed[index]:
            index += 1
        if (
            gap_start > 0
            and index < len(closed)
            and index - gap_start <= maximum_frames
        ):
            closed[gap_start:index] = [True] * (index - gap_start)
    return closed


def _persistent_artifact_frames(
    flags: Sequence[object],
    *,
    np_module: Any,
) -> list[bool]:
    """Retain sustained or repeatedly clustered artifact evidence.

    A single pitch-estimation frame is not actionable.  The guard closes at
    most twelve milliseconds of internal uncertainty, then keeps either a
    continuous sixty-millisecond event or suspicious frames occupying at least
    ten percent of a centered one-second window.  Density qualifies only the
    already suspicious frames, never their clean context.
    """

    np = np_module
    hop_seconds = _ARTIFACT_HOP_LENGTH / _ARTIFACT_ANALYSIS_SAMPLE_RATE
    maximum_gap = max(1, int(round(_ARTIFACT_MAX_GAP_SECONDS / hop_seconds)))
    minimum_run = int(math.ceil(_ARTIFACT_MIN_RUN_SECONDS / hop_seconds))
    density_window = max(
        1,
        int(round(_ARTIFACT_DENSITY_WINDOW_SECONDS / hop_seconds)),
    )
    closed = np.asarray(
        _close_artifact_gaps(flags, maximum_gap),
        dtype=bool,
    )
    density_window = min(density_window, max(1, len(closed)))
    long_members = np.zeros(len(closed), dtype=bool)
    run_start = None  # type: Optional[int]
    for index, value in enumerate(np.append(closed, False)):
        if value and run_start is None:
            run_start = index
        elif not value and run_start is not None:
            if index - run_start >= minimum_run:
                long_members[run_start:index] = True
            run_start = None

    kernel = np.ones(density_window, dtype=np.float32)
    numerator = np.convolve(closed.astype(np.float32), kernel, mode="same")
    denominator = np.convolve(
        np.ones(len(closed), dtype=np.float32),
        kernel,
        mode="same",
    )
    density = numerator / np.maximum(denominator, 1.0)
    selected = closed & (
        long_members | (density >= _ARTIFACT_MIN_DENSITY)
    )
    return _close_artifact_gaps(selected.tolist(), maximum_gap)


def _periodicity_at_lags(
    audio: object,
    lags: object,
    *,
    frame_count: int,
    np_module: Any,
) -> NDArray[Any]:
    """Measure normalized autocorrelation at one pitch lag per analysis frame.

    Comparing the converted pitch lag with the key-adjusted corresponding lag
    in the source distinguishes an injected stable oscillator from a legitimate
    tonal-language contour.  Bounded batches prevent a full song from creating
    a song-sized frame matrix while preserving the Hann-windowed detector used
    by the fixed evaluation harness.  Rows with no valid pitch lag are skipped
    before frame materialization because regional pYIN intentionally leaves
    most of a song unassigned; scanning those zero-lag rows recreated the same
    long post-inference pause that regional analysis is designed to avoid.
    """

    np = np_module
    samples = np.asarray(audio, dtype=np.float32)
    padded = np.pad(
        samples,
        (_ARTIFACT_FRAME_LENGTH // 2, _ARTIFACT_FRAME_LENGTH // 2),
        mode="constant",
    )
    requested = np.asarray(lags, dtype=np.int32)[:frame_count]
    result = np.zeros(len(requested), dtype=np.float32)
    offsets = np.arange(_ARTIFACT_FRAME_LENGTH, dtype=np.int64)
    positions = np.arange(_ARTIFACT_FRAME_LENGTH, dtype=np.float32)
    window = (
        0.5
        - 0.5
        * np.cos(2.0 * np.pi * positions / _ARTIFACT_FRAME_LENGTH)
    ).astype(np.float32)
    for batch_start in range(0, len(requested), 512):
        batch_end = min(len(requested), batch_start + 512)
        batch_lags = requested[batch_start:batch_end]
        valid_rows = np.flatnonzero(
            (batch_lags >= 1)
            & (batch_lags < _ARTIFACT_FRAME_LENGTH // 2)
        )
        if len(valid_rows) == 0:
            continue
        frame_indexes = batch_start + valid_rows
        sample_indexes = (
            frame_indexes[:, np.newaxis] * _ARTIFACT_HOP_LENGTH
            + offsets[np.newaxis, :]
        )
        frames = padded[sample_indexes]
        frames = (
            frames - np.mean(frames, axis=1, keepdims=True)
        ) * window
        valid_lags = batch_lags[valid_rows]
        for lag_value in np.unique(valid_lags):
            lag = int(lag_value)
            rows = np.flatnonzero(valid_lags == lag)
            left = frames[rows, :-lag]
            right = frames[rows, lag:]
            numerator = np.einsum("ij,ij->i", left, right)
            denominator = np.sqrt(
                np.einsum("ij,ij->i", left, left)
                * np.einsum("ij,ij->i", right, right)
            )
            result[frame_indexes[rows]] = np.divide(
                numerator,
                denominator,
                out=np.zeros_like(numerator, dtype=np.float32),
                where=denominator > 1e-12,
            )
    return result


def _pitch_lag_tracks(
    output_f0: object,
    *,
    key_shift: int,
    np_module: Any,
) -> tuple[NDArray[Any], NDArray[Any]]:
    """Derive output and key-corresponding source lags independently.

    The source lag must use the unrounded frequency.  Scaling an already
    quantized output lag can shift normalized autocorrelation by a full guard
    threshold on high notes and make the detector disagree with calibration.
    """

    np = np_module
    output_pitch = np.asarray(output_f0, dtype=np.float64)
    finite = np.isfinite(output_pitch) & (output_pitch > 0.0)
    output_lags = np.zeros(len(output_pitch), dtype=np.int32)
    source_lags = np.zeros(len(output_pitch), dtype=np.int32)
    output_lags[finite] = np.rint(
        _ARTIFACT_ANALYSIS_SAMPLE_RATE / output_pitch[finite]
    ).astype(np.int32)
    source_lags[finite] = np.rint(
        _ARTIFACT_ANALYSIS_SAMPLE_RATE
        * (2.0 ** (key_shift / 12.0))
        / output_pitch[finite]
    ).astype(np.int32)
    return output_lags, source_lags


def _resample_poly_chunked(
    audio: object,
    *,
    source_sample_rate: int,
    target_sample_rate: int,
    core_seconds: float,
    np_module: Any,
    signal_module: Any,
) -> NDArray[Any]:
    """Resample a long mono track in bounded, sample-identical blocks.

    ``scipy.signal.resample_poly`` can spend many minutes in one monolithic
    44.1-to-40-kHz call after RVC has already finished.  Its default FIR has
    finite support, so denominator-aligned cores with one complete filter
    support of context on each side can be cropped on exact global output
    coordinates.  This retains element-for-element parity with the monolithic
    call while bounding the work presented to SciPy at one time.
    """

    np = np_module
    samples = np.asarray(audio, dtype=np.float32)
    if (
        samples.ndim != 1
        or len(samples) == 0
        or type(source_sample_rate) is not int
        or type(target_sample_rate) is not int
        or source_sample_rate < 1
        or target_sample_rate < 1
        or not math.isfinite(core_seconds)
        or core_seconds <= 0.0
    ):
        _fail("invalid_audio")
    divisor = math.gcd(source_sample_rate, target_sample_rate)
    up = target_sample_rate // divisor
    down = source_sample_rate // divisor
    if up == down == 1:
        return samples

    total_output_samples = (len(samples) * up + down - 1) // down
    output = np.empty(total_output_samples, dtype=np.float32)
    target_core_samples = max(
        down,
        int(round(core_seconds * source_sample_rate)),
    )
    core_samples = max(
        down,
        (target_core_samples // down) * down,
    )
    # resample_poly's default FIR half-length is ten times the larger reduced
    # rate.  Rounding its input-domain support up to a denominator boundary
    # keeps both convolution phase and every crop coordinate global.
    filter_support = (
        10 * max(up, down) + up - 1
    ) // up + 1
    context_samples = (
        (filter_support + down - 1) // down
    ) * down

    for core_start in range(0, len(samples), core_samples):
        core_end = min(len(samples), core_start + core_samples)
        segment_start = max(0, core_start - context_samples)
        segment_end = min(len(samples), core_end + context_samples)
        segment = signal_module.resample_poly(
            samples[segment_start:segment_end],
            up,
            down,
        ).astype(np.float32, copy=False)
        output_start = core_start * up // down
        output_end = (
            total_output_samples
            if core_end == len(samples)
            else core_end * up // down
        )
        local_start = (core_start - segment_start) * up // down
        local_end = local_start + output_end - output_start
        if local_end > len(segment):
            _fail("invalid_audio")
        output[output_start:output_end] = segment[local_start:local_end]
    return output


def _read_source_vocal(
    path: Path,
    *,
    output_sample_rate: int,
    output_samples: int,
    np_module: Any,
    signal_module: Any,
) -> NDArray[Any]:
    """Load, resample, and length-match the already validated input WAV."""

    np = np_module
    try:
        import soundfile as sf  # type: ignore[import-not-found]

        source, source_rate = sf.read(
            str(path),
            dtype="float32",
            always_2d=True,
        )
        source = np.asarray(source, dtype=np.float32)
        if (
            source.ndim != 2
            or source.shape[0] == 0
            or not 1 <= source.shape[1] <= 2
            or type(source_rate) is not int
            or source_rate < 8_000
            or source_rate > 192_000
            or not bool(np.all(np.isfinite(source)))
        ):
            _fail("invalid_audio")
        mono = np.mean(source, axis=1, dtype=np.float32)
        resampled = _resample_poly_chunked(
            mono,
            source_sample_rate=source_rate,
            target_sample_rate=output_sample_rate,
            core_seconds=_ARTIFACT_RESAMPLE_CORE_SECONDS,
            np_module=np,
            signal_module=signal_module,
        )
        if len(resampled) >= output_samples:
            return resampled[:output_samples]
        return np.pad(resampled, (0, output_samples - len(resampled)))
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("invalid_audio")


def _resample_artifact_analysis(
    audio: object,
    *,
    source_sample_rate: int,
    np_module: Any,
    signal_module: Any,
) -> NDArray[Any]:
    """Resample detector audio in phase-aligned, result-identical blocks.

    SciPy's polyphase output is local: each output depends on only the finite
    FIR support around its corresponding input position.  Core and context
    boundaries are therefore aligned to the reduced ratio denominator, and a
    whole filter support is retained on each side before cropping by exact
    integer output coordinates.  This produces element-for-element the same
    waveform as one monolithic ``resample_poly`` call while avoiding its severe
    long-array performance path on multi-minute 40-kHz songs.
    """

    return _resample_poly_chunked(
        audio,
        source_sample_rate=source_sample_rate,
        target_sample_rate=_ARTIFACT_ANALYSIS_SAMPLE_RATE,
        core_seconds=_ARTIFACT_RESAMPLE_CORE_SECONDS,
        np_module=np_module,
        signal_module=signal_module,
    )


def _frame_rms_db(
    audio: object,
    *,
    np_module: Any,
    librosa_module: Any,
) -> NDArray[Any]:
    """Measure frame RMS on the detector's canonical 44.1-kHz grid."""

    np = np_module
    rms = librosa_module.feature.rms(
        y=np.asarray(audio, dtype=np.float32),
        frame_length=_ARTIFACT_FRAME_LENGTH,
        hop_length=_ARTIFACT_HOP_LENGTH,
        center=True,
    )[0]
    return np.asarray(
        20.0 * np.log10(np.maximum(rms, 1e-9)),
        dtype=np.float32,
    )


def _banded_viterbi_core(
    log_probability: object,
    predecessor_indexes: object,
    predecessor_scores: object,
    predecessor_counts: object,
    baseline_score: float,
    initial_scores: object,
    state: object,
    pointers: object,
    previous: object,
    current: object,
) -> float:
    """Decode one sparse transition lattice with dense-Viterbi tie semantics.

    librosa represents forbidden pYIN transitions as the floating-point tiny
    value instead of negative infinity.  The best such transition can be found
    once per frame from the global best predecessor; only the narrow band of
    real pitch transitions then needs to be scanned for each destination.  A
    lower source index wins exact score ties, matching ``numpy.argmax`` in the
    reviewed dense decoder.

    This deliberately allocation-free loop is compiled lazily with Numba.
    Keeping array creation in the caller lets this module establish its closed
    runtime environment before importing NumPy or Numba.
    """

    frame_count = log_probability.shape[0]  # type: ignore[attr-defined]
    state_count = log_probability.shape[1]  # type: ignore[attr-defined]
    for source_index in range(state_count):
        previous[source_index] = (
            log_probability[0, source_index] + initial_scores[source_index]
        )

    for frame_index in range(1, frame_count):
        baseline_source = 0
        baseline_value = previous[0]
        for source_index in range(1, state_count):
            if previous[source_index] > baseline_value:
                baseline_source = source_index
                baseline_value = previous[source_index]

        for destination_index in range(state_count):
            best_source = baseline_source
            best_score = baseline_value + baseline_score
            for predecessor_offset in range(
                predecessor_counts[destination_index]
            ):
                source_index = predecessor_indexes[
                    destination_index,
                    predecessor_offset,
                ]
                candidate = (
                    previous[source_index]
                    + predecessor_scores[
                        destination_index,
                        predecessor_offset,
                    ]
                )
                if candidate > best_score or (
                    candidate == best_score and source_index < best_source
                ):
                    best_source = source_index
                    best_score = candidate
            pointers[frame_index, destination_index] = best_source
            current[destination_index] = (
                log_probability[frame_index, destination_index] + best_score
            )

        swap = previous
        previous = current
        current = swap

    final_state = 0
    final_score = previous[0]
    for state_index in range(1, state_count):
        if previous[state_index] > final_score:
            final_state = state_index
            final_score = previous[state_index]
    state[frame_count - 1] = final_state
    for frame_index in range(frame_count - 2, -1, -1):
        state[frame_index] = pointers[
            frame_index + 1,
            state[frame_index + 1],
        ]
    return final_score


_compiled_banded_viterbi_core: Optional[Callable[..., float]] = None


def _banded_viterbi(
    log_probability: object,
    log_transition: object,
    log_initial: object,
) -> tuple[object, object]:
    """Return the exact pYIN path without librosa's quadratic dense scan.

    The fixed pYIN configuration has 444 states but at most 32 meaningful
    incoming transitions per state.  librosa 0.9 materializes and scans all
    444 squared transitions for every frame, which made a permitted thirty
    seconds of regional analysis consume more than twenty minutes of one CPU
    core.  This adapter validates that the lattice remains within the reviewed
    sparse shape, then uses the common epsilon transition as one global
    baseline plus the explicit local predecessors.  Unexpected future lattice
    shapes fail open at the artifact-guard boundary instead of reintroducing
    an unbounded dense fallback.
    """

    global _compiled_banded_viterbi_core

    try:
        np = importlib.import_module("numpy")
        probability = np.asarray(log_probability, dtype=np.float64)
        transition = np.asarray(log_transition, dtype=np.float64)
        initial = np.asarray(log_initial, dtype=np.float64)
        if (
            probability.ndim != 2
            or transition.ndim != 2
            or initial.ndim != 1
            or probability.shape[0] < 1
            or probability.shape[0] > _ARTIFACT_MAX_VITERBI_FRAMES
            or probability.shape[1] < 1
            or probability.shape[1] > _ARTIFACT_MAX_VITERBI_STATES
            or transition.shape
            != (probability.shape[1], probability.shape[1])
            or initial.shape != (probability.shape[1],)
            or not bool(np.all(np.isfinite(probability)))
            or not bool(np.all(np.isfinite(transition)))
            or not bool(np.all(np.isfinite(initial)))
        ):
            raise _ArtifactAnalysisSkipped

        baseline = float(np.min(transition))
        meaningful = transition > baseline
        predecessor_counts = np.count_nonzero(
            meaningful,
            axis=0,
        ).astype(np.int32)
        maximum_predecessors = int(np.max(predecessor_counts))
        if (
            maximum_predecessors < 1
            or maximum_predecessors > _ARTIFACT_MAX_VITERBI_PREDECESSORS
        ):
            raise _ArtifactAnalysisSkipped

        state_count = probability.shape[1]
        predecessor_indexes = np.full(
            (state_count, maximum_predecessors),
            -1,
            dtype=np.int32,
        )
        predecessor_scores = np.full(
            (state_count, maximum_predecessors),
            baseline,
            dtype=np.float64,
        )
        for destination_index in range(state_count):
            indexes = np.flatnonzero(meaningful[:, destination_index]).astype(
                np.int32,
                copy=False,
            )
            count = len(indexes)
            predecessor_indexes[destination_index, :count] = indexes
            predecessor_scores[destination_index, :count] = transition[
                indexes,
                destination_index,
            ]

        if _compiled_banded_viterbi_core is None:
            numba = importlib.import_module("numba")
            compile_function = getattr(numba, "njit")
            if not callable(compile_function):
                raise TypeError
            # Per-job cache directories are intentionally private and unique.
            # In-memory compilation avoids pretending that a cross-job disk
            # cache exists while still amortizing compilation across every
            # regional pYIN call in this one conversion process.
            _compiled_banded_viterbi_core = compile_function(cache=False)(
                _banded_viterbi_core
            )

        frame_count = probability.shape[0]
        state = np.zeros(frame_count, dtype=np.uint16)
        pointers = np.zeros(
            (frame_count, state_count),
            dtype=np.uint16,
        )
        previous = np.empty(state_count, dtype=np.float64)
        current = np.empty(state_count, dtype=np.float64)
        final_score = _compiled_banded_viterbi_core(
            probability,
            predecessor_indexes,
            predecessor_scores,
            predecessor_counts,
            baseline,
            initial,
            state,
            pointers,
            previous,
            current,
        )
        return state, np.asarray([final_score], dtype=np.float64)
    except _ArtifactAnalysisSkipped:
        raise
    except BaseException as error:
        raise _ArtifactAnalysisSkipped from error


def _pyin_with_banded_viterbi(
    audio: object,
    *,
    librosa_module: Any,
) -> tuple[object, object, object]:
    """Run fixed pYIN while replacing only its dense decoder for this call.

    The runtime is a single-request process and invokes pYIN serially, so the
    temporary module binding cannot race another analysis.  Test doubles omit
    librosa's sequence module and continue through their injected ``pyin``
    implementation; the pinned production module must expose ``_viterbi``.
    """

    sequence_module = getattr(librosa_module, "sequence", None)
    original_viterbi = (
        None
        if sequence_module is None
        else getattr(sequence_module, "_viterbi", None)
    )
    if sequence_module is not None and not callable(original_viterbi):
        raise _ArtifactAnalysisSkipped
    if sequence_module is not None:
        setattr(sequence_module, "_viterbi", _banded_viterbi)
    try:
        return librosa_module.pyin(
            audio,
            fmin=_ARTIFACT_F0_MIN_HZ,
            fmax=_ARTIFACT_F0_MAX_HZ,
            sr=_ARTIFACT_ANALYSIS_SAMPLE_RATE,
            frame_length=_ARTIFACT_FRAME_LENGTH,
            hop_length=_ARTIFACT_HOP_LENGTH,
            center=True,
            resolution=_ARTIFACT_PITCH_RESOLUTION,
            fill_na=None,
        )
    finally:
        if sequence_module is not None:
            setattr(sequence_module, "_viterbi", original_viterbi)


def _voicing_track_segment(
    audio: object,
    *,
    np_module: Any,
    librosa_module: Any,
) -> tuple[NDArray[Any], NDArray[Any], NDArray[Any], NDArray[Any]]:
    """Analyze one memory-bounded pYIN segment on the shared frame grid."""

    np = np_module
    segment = np.asarray(audio, dtype=np.float32)
    f0, voiced, probability = _pyin_with_banded_viterbi(
        segment,
        librosa_module=librosa_module,
    )
    rms_db = _frame_rms_db(
        segment,
        np_module=np,
        librosa_module=librosa_module,
    )
    frame_count = min(len(f0), len(voiced), len(probability), len(rms_db))
    if frame_count == 0:
        _fail("invalid_audio")
    return (
        np.asarray(f0[:frame_count], dtype=np.float64),
        np.asarray(voiced[:frame_count], dtype=bool),
        np.nan_to_num(
            np.asarray(probability[:frame_count], dtype=np.float32),
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        ),
        np.asarray(rms_db[:frame_count], dtype=np.float32),
    )


def _voicing_track(
    audio: object,
    *,
    np_module: Any,
    librosa_module: Any,
) -> tuple[NDArray[Any], NDArray[Any], NDArray[Any], NDArray[Any]]:
    """Return aligned pYIN and RMS tracks without whole-song Viterbi state.

    Twenty-cent pitch bins remain far narrower than the detector's 150-cent
    agreement bound.  Ten-second cores with two seconds of context preserve
    the calibrated decisions at joins while bounding pYIN observations and
    back-pointers.  The scoped banded decoder preserves librosa 0.9's exact
    path without its quadratic dense transition scan.  ``fill_na=None`` keeps
    pYIN's best candidate on unvoiced frames because those are precisely the
    transition frames whose source/output pitch agreement must be assessed.
    """

    np = np_module
    samples = np.asarray(audio, dtype=np.float32)
    total_frames = len(samples) // _ARTIFACT_HOP_LENGTH + 1
    core_frames = max(
        1,
        int(
            round(
                _ARTIFACT_PYIN_CORE_SECONDS
                * _ARTIFACT_ANALYSIS_SAMPLE_RATE
                / _ARTIFACT_HOP_LENGTH
            )
        ),
    )
    context_frames = max(
        1,
        int(
            round(
                _ARTIFACT_PYIN_CONTEXT_SECONDS
                * _ARTIFACT_ANALYSIS_SAMPLE_RATE
                / _ARTIFACT_HOP_LENGTH
            )
        ),
    )
    if total_frames <= core_frames:
        return _voicing_track_segment(
            samples,
            np_module=np,
            librosa_module=librosa_module,
        )

    stitched: list[list[NDArray[Any]]] = [[], [], [], []]
    for core_start in range(0, total_frames, core_frames):
        core_end = min(total_frames, core_start + core_frames)
        context_start = max(0, core_start - context_frames)
        context_end = min(total_frames, core_end + context_frames)
        sample_start = context_start * _ARTIFACT_HOP_LENGTH
        sample_end = min(
            len(samples),
            context_end * _ARTIFACT_HOP_LENGTH,
        )
        segment_tracks = _voicing_track_segment(
            samples[sample_start:sample_end],
            np_module=np,
            librosa_module=librosa_module,
        )
        keep_start = core_start - context_start
        keep_end = keep_start + core_end - core_start
        if any(len(track) < keep_end for track in segment_tracks):
            _fail("invalid_audio")
        for destination, track in zip(stitched, segment_tracks):
            destination.append(track[keep_start:keep_end])

    tracks = tuple(np.concatenate(parts) for parts in stitched)
    if any(len(track) != total_frames for track in tracks):
        _fail("invalid_audio")
    return tracks  # type: ignore[return-value]


def _yin_track(
    audio: object,
    *,
    np_module: Any,
    librosa_module: Any,
) -> NDArray[Any]:
    """Return a fast YIN pitch track with exact bounded-memory stitching.

    The quarter-rate frame and hop sizes preserve the 44.1-kHz detector's time
    grid.  Four context frames provide the complete half-frame acoustic support
    needed at every retained join without invoking pYIN's dense Viterbi path.
    """

    np = np_module
    samples = np.asarray(audio, dtype=np.float32)
    total_frames = len(samples) // _ARTIFACT_YIN_HOP_LENGTH + 1
    parts: list[NDArray[Any]] = []
    for core_start in range(0, total_frames, _ARTIFACT_YIN_CORE_FRAMES):
        core_end = min(
            total_frames,
            core_start + _ARTIFACT_YIN_CORE_FRAMES,
        )
        context_start = max(
            0,
            core_start - _ARTIFACT_YIN_CONTEXT_FRAMES,
        )
        context_end = min(
            total_frames,
            core_end + _ARTIFACT_YIN_CONTEXT_FRAMES,
        )
        sample_start = context_start * _ARTIFACT_YIN_HOP_LENGTH
        sample_end = (
            len(samples)
            if context_end == total_frames
            else (context_end - 1) * _ARTIFACT_YIN_HOP_LENGTH + 1
        )
        segment = np.asarray(
            librosa_module.yin(
                samples[sample_start:sample_end],
                fmin=_ARTIFACT_F0_MIN_HZ,
                fmax=_ARTIFACT_F0_MAX_HZ,
                sr=_ARTIFACT_YIN_SAMPLE_RATE,
                frame_length=_ARTIFACT_YIN_FRAME_LENGTH,
                hop_length=_ARTIFACT_YIN_HOP_LENGTH,
                trough_threshold=_ARTIFACT_YIN_TROUGH_THRESHOLD,
                center=True,
            ),
            dtype=np.float64,
        )
        keep_start = core_start - context_start
        keep_end = keep_start + core_end - core_start
        if len(segment) < keep_end:
            _fail("invalid_audio")
        parts.append(segment[keep_start:keep_end])
    result = np.concatenate(parts)
    if len(result) != total_frames:
        _fail("invalid_audio")
    return result


def _pitch_error_track(
    source_f0: object,
    output_f0: object,
    *,
    key_shift: int,
    np_module: Any,
) -> tuple[NDArray[Any], NDArray[Any]]:
    """Return finite-pitch flags and key-adjusted error in absolute cents."""

    np = np_module
    source_pitch = np.asarray(source_f0, dtype=np.float64)
    output_pitch = np.asarray(output_f0, dtype=np.float64)
    finite = (
        np.isfinite(source_pitch)
        & (source_pitch > 0.0)
        & np.isfinite(output_pitch)
        & (output_pitch > 0.0)
    )
    error = np.full(len(output_pitch), np.inf, dtype=np.float64)
    expected_ratio = 2.0 ** (key_shift / 12.0)
    error[finite] = np.abs(
        1200.0
        * np.log2(
            output_pitch[finite]
            / (source_pitch[finite] * expected_ratio)
        )
    )
    return finite, error


def _coarse_artifact_eligibility(
    *,
    source_f0: object,
    output_f0: object,
    output_rms_db: object,
    key_shift: int,
    np_module: Any,
) -> NDArray[Any]:
    """Identify frames that can still pass the coarse periodicity gates.

    YIN returns a finite best estimate even for silence and unrelated pitches.
    Eliminating those frames before autocorrelation is decision-preserving
    because pitch agreement and output activity are mandatory coarse gates,
    and it prevents a long song from materializing thousands of 2,048-sample
    frames that can never open a fine-analysis window.
    """

    np = np_module
    finite_pitch, pitch_error = _pitch_error_track(
        source_f0,
        output_f0,
        key_shift=key_shift,
        np_module=np,
    )
    return (
        finite_pitch
        & (pitch_error <= _ARTIFACT_MAX_PITCH_ERROR_CENTS)
        & (np.asarray(output_rms_db) >= _ARTIFACT_OUTPUT_ACTIVE_DBFS)
    )


def _coarse_artifact_candidates(
    *,
    eligible_frames: object,
    source_periodicity: object,
    output_periodicity: object,
    np_module: Any,
) -> NDArray[Any]:
    """Return frames passing both permissive coarse periodicity gates."""

    np = np_module
    eligible = np.asarray(eligible_frames, dtype=bool)
    output_period = np.asarray(output_periodicity)
    source_period = np.asarray(source_periodicity)
    if (
        eligible.ndim != 1
        or source_period.ndim != 1
        or output_period.ndim != 1
        or len(source_period) != len(eligible)
        or len(output_period) != len(eligible)
        or not bool(np.all(np.isfinite(source_period)))
        or not bool(np.all(np.isfinite(output_period)))
    ):
        _fail("invalid_audio")
    return (
        eligible
        & (output_period >= _ARTIFACT_PREFILTER_OUTPUT_PERIODICITY)
        & (
            output_period - source_period
            >= _ARTIFACT_PREFILTER_PERIODICITY_INCREASE
        )
    )


def _coarse_artifact_interest(
    *,
    source_f0: object,
    output_f0: object,
    output_rms_db: object,
    source_periodicity: object,
    output_periodicity: object,
    key_shift: int,
    np_module: Any,
) -> NDArray[Any]:
    """Dilate a permissive YIN prefilter into frames needing fine pYIN.

    Thresholds are deliberately wider than the final detector.  The prefilter
    is allowed to over-select but must not decide repairs; it only avoids dense
    pYIN work where RMS, pitch agreement, and autocorrelation already rule the
    transition defect out.
    """

    np = np_module
    eligible = _coarse_artifact_eligibility(
        source_f0=source_f0,
        output_f0=output_f0,
        output_rms_db=output_rms_db,
        key_shift=key_shift,
        np_module=np,
    )
    candidate = _coarse_artifact_candidates(
        eligible_frames=eligible,
        source_periodicity=source_periodicity,
        output_periodicity=output_periodicity,
        np_module=np,
    )
    radius = int(
        math.ceil(
            _ARTIFACT_INTEREST_RADIUS_SECONDS
            * _ARTIFACT_ANALYSIS_SAMPLE_RATE
            / _ARTIFACT_HOP_LENGTH
        )
    )
    indexes = np.flatnonzero(candidate)
    interest = np.zeros(len(candidate), dtype=bool)
    if len(indexes) == 0:
        return interest
    starts = np.maximum(0, indexes - radius)
    ends = np.minimum(len(candidate), indexes + radius + 1)
    differences = np.zeros(len(candidate) + 1, dtype=np.int32)
    np.add.at(differences, starts, 1)
    np.add.at(differences, ends, -1)
    return np.cumsum(differences[:-1]) > 0


def _fine_analysis_windows(
    interest_frames: object,
    *,
    np_module: Any,
) -> Optional[list[tuple[int, int]]]:
    """Expand, merge, and budget local pYIN windows.

    ``None`` means the permissive prefilter spread beyond the localized defect
    for which the experiment was calibrated.  Failing open keeps the reviewed
    RVC waveform unchanged instead of applying an unvalidated detector across
    another singing style.  The twelve-second floor and fifteen-percent
    allowance retain headroom around the 8.713-second calibration workload;
    the thirty-second ceiling bounds long-song analysis.
    """

    np = np_module
    interest = np.asarray(interest_frames, dtype=bool)
    if interest.ndim != 1:
        raise _ArtifactAnalysisSkipped
    context_frames = int(
        math.ceil(
            _ARTIFACT_FINE_CONTEXT_SECONDS
            * _ARTIFACT_ANALYSIS_SAMPLE_RATE
            / _ARTIFACT_HOP_LENGTH
        )
    )
    windows: list[tuple[int, int]] = []
    index = 0
    while index < len(interest):
        if not interest[index]:
            index += 1
            continue
        run_start = index
        while index < len(interest) and interest[index]:
            index += 1
        start = max(0, run_start - context_frames)
        end = min(len(interest), index + context_frames)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))

    analyzed_seconds = sum(
        end - start for start, end in windows
    ) * _ARTIFACT_HOP_LENGTH / _ARTIFACT_ANALYSIS_SAMPLE_RATE
    song_seconds = (
        len(interest) * _ARTIFACT_HOP_LENGTH
        / _ARTIFACT_ANALYSIS_SAMPLE_RATE
    )
    budget_seconds = min(
        _ARTIFACT_FINE_MAXIMUM_BUDGET_SECONDS,
        max(
            _ARTIFACT_FINE_MINIMUM_BUDGET_SECONDS,
            song_seconds * _ARTIFACT_FINE_BUDGET_FRACTION,
        ),
    )
    if analyzed_seconds > budget_seconds:
        return None
    return windows


def _voicing_tracks_for_windows(
    audio: object,
    windows: Sequence[tuple[int, int]],
    *,
    total_frames: int,
    np_module: Any,
    librosa_module: Any,
) -> tuple[NDArray[Any], NDArray[Any], NDArray[Any], NDArray[Any]]:
    """Run fine pYIN only inside merged windows on the global frame grid."""

    np = np_module
    samples = np.asarray(audio, dtype=np.float32)
    f0 = np.full(total_frames, np.nan, dtype=np.float64)
    voiced = np.zeros(total_frames, dtype=bool)
    probability = np.zeros(total_frames, dtype=np.float32)
    rms_db = np.full(total_frames, -120.0, dtype=np.float32)
    destinations = (f0, voiced, probability, rms_db)
    for start, end in windows:
        sample_start = start * _ARTIFACT_HOP_LENGTH
        sample_end = (
            len(samples)
            if end == total_frames
            else min(
                len(samples),
                (end - 1) * _ARTIFACT_HOP_LENGTH + 1,
            )
        )
        segment_tracks = _voicing_track(
            samples[sample_start:sample_end],
            np_module=np,
            librosa_module=librosa_module,
        )
        expected_frames = end - start
        if any(len(track) < expected_frames for track in segment_tracks):
            _fail("invalid_audio")
        for destination, track in zip(destinations, segment_tracks):
            destination[start:end] = track[:expected_frames]
    return destinations


def _candidate_artifact_frames(
    *,
    source_f0: object,
    source_voiced_flag: object,
    source_probability: object,
    source_rms_db: object,
    output_f0: object,
    output_voiced_flag: object,
    output_probability: object,
    output_rms_db: object,
    source_periodicity: object,
    output_periodicity: object,
    key_shift: int,
    np_module: Any,
) -> NDArray[Any]:
    """Select low-confidence source pitch made unnaturally stable by RVC.

    The pitch-consistency check rejects octave and subharmonic estimates that
    occur naturally on breathy high notes.  A genuine transition defect keeps
    the expected shifted contour but increases normalized autocorrelation, so
    both signals are needed to separate it from legitimate tonal singing.
    """

    np = np_module
    finite_pitch, pitch_error = _pitch_error_track(
        source_f0,
        output_f0,
        key_shift=key_shift,
        np_module=np,
    )
    source_voiced = (
        np.asarray(source_voiced_flag, dtype=bool)
        & (np.asarray(source_probability) >= 0.50)
        & (np.asarray(source_rms_db) >= _ARTIFACT_SOURCE_ACTIVE_DBFS)
    )
    output_voiced = (
        np.asarray(output_voiced_flag, dtype=bool)
        & (np.asarray(output_probability) >= 0.50)
        & (np.asarray(output_rms_db) >= _ARTIFACT_SOURCE_ACTIVE_DBFS)
    )
    return (
        ~source_voiced
        & output_voiced
        & finite_pitch
        & (pitch_error <= _ARTIFACT_MAX_PITCH_ERROR_CENTS)
        & (
            np.asarray(output_probability)
            >= _ARTIFACT_OUTPUT_VOICED_PROBABILITY
        )
        & (np.asarray(output_rms_db) >= _ARTIFACT_OUTPUT_ACTIVE_DBFS)
        & (
            np.asarray(output_periodicity)
            >= _ARTIFACT_OUTPUT_PERIODICITY
        )
        & (
            np.asarray(output_periodicity) - np.asarray(source_periodicity)
            >= _ARTIFACT_PERIODICITY_INCREASE
        )
    )


def _artifact_intervals(
    selected_frames: object,
    *,
    output_sample_rate: int,
    output_samples: int,
    np_module: Any,
) -> list[tuple[int, int]]:
    """Map selected analysis-frame centers to exact output sample intervals."""

    np = np_module
    selected = np.asarray(selected_frames, dtype=bool)
    intervals: list[tuple[int, int]] = []
    index = 0
    while index < len(selected):
        if not selected[index]:
            index += 1
            continue
        start_frame = index
        while index < len(selected) and selected[index]:
            index += 1
        analysis_start = max(
            0,
            start_frame * _ARTIFACT_HOP_LENGTH
            - _ARTIFACT_HOP_LENGTH // 2,
        )
        analysis_end = (
            (index - 1) * _ARTIFACT_HOP_LENGTH
            + _ARTIFACT_HOP_LENGTH // 2
        )
        start = int(
            round(
                analysis_start
                * output_sample_rate
                / _ARTIFACT_ANALYSIS_SAMPLE_RATE
            )
        )
        end = min(
            output_samples,
            int(
                round(
                    analysis_end
                    * output_sample_rate
                    / _ARTIFACT_ANALYSIS_SAMPLE_RATE
                )
            ),
        )
        if end > start:
            intervals.append((start, end))
    return intervals


def _artifact_envelope(
    intervals: Sequence[tuple[int, int]],
    *,
    sample_rate: int,
    sample_count: int,
    np_module: Any,
) -> NDArray[Any]:
    """Build a core-one envelope with five-millisecond cosine boundaries."""

    np = np_module
    envelope = np.zeros(sample_count, dtype=np.float32)
    fade_samples = max(1, int(round(_ARTIFACT_FADE_SECONDS * sample_rate)))
    for start, end in intervals:
        envelope[start:end] = 1.0
        left_start = max(0, start - fade_samples)
        left_count = start - left_start
        if left_count:
            phase = np.linspace(0.0, 1.0, left_count, endpoint=False)
            fade = 0.5 - 0.5 * np.cos(np.pi * phase)
            envelope[left_start:start] = np.maximum(
                envelope[left_start:start],
                fade,
            )
        right_end = min(sample_count, end + fade_samples)
        right_count = right_end - end
        if right_count:
            phase = np.linspace(0.0, 1.0, right_count, endpoint=False)
            fade = 0.5 + 0.5 * np.cos(np.pi * phase)
            envelope[end:right_end] = np.maximum(
                envelope[end:right_end],
                fade,
            )
    return envelope


def _reproduce_rejected_false_voicing_repair(
    input_audio: Path,
    sample_rate: int,
    samples: object,
    key_shift: int,
) -> object:
    """Reproduce the rejected transition replacement for offline analysis.

    A fast quarter-rate YIN and autocorrelation pass first identifies small
    regions that could contain the defect; bounded pYIN then makes the final
    decision only inside those regions.  A pYIN decision alone is not
    sufficient: breathy high notes can look unvoiced in the source while RVC
    makes them easier to track.  Repair also requires key-shift-consistent
    source and output pitch plus a large increase in normalized
    autocorrelation.  Persistence removes isolated estimates.  Every coarse-
    positive region receives the bounded fine pass, but only frames that still
    meet the original sixty-millisecond or repeated-density gate may alter the
    output; brief strong-event shortcuts and event growth are intentionally
    excluded.
    A 3.2-kHz high-pass strongly suppresses the source fundamental and low
    harmonics; mixing it at -20 dB preserves mainly consonant/noise energy while
    limiting audible source-timbre leakage into the converted voice.

    This exact replacement is intentionally unavailable to ``_run_request``.
    Listening review showed that its low-level substitute creates envelope
    holes and downstream sidechain pumping even when detection is correct.  It
    remains only to reproduce and measure that failed experiment.
    """

    try:
        import numpy as np  # type: ignore[import-not-found]

        converted_source = np.asarray(samples)
        if converted_source.ndim != 1 or converted_source.size == 0:
            _fail("invalid_audio")
        if converted_source.dtype == np.dtype(np.int16):
            converted = converted_source.astype(np.float32) / 32_768.0
        elif np.issubdtype(converted_source.dtype, np.floating):
            converted = converted_source.astype(np.float32)
        else:
            _fail("invalid_audio")
        if (
            not bool(np.all(np.isfinite(converted)))
            or float(np.max(np.abs(converted))) > 1.000001
        ):
            _fail("invalid_audio")
        if len(converted) < sample_rate:
            # Sub-second clips cannot contain the sustained transition defect
            # this guard targets, and pYIN edge estimates would dominate them.
            return samples

        import librosa  # type: ignore[import-not-found]
        from scipy import signal  # type: ignore[import-not-found, import-untyped]

        source = _read_source_vocal(
            input_audio,
            output_sample_rate=sample_rate,
            output_samples=len(converted),
            np_module=np,
            signal_module=signal,
        )
        source_analysis = _resample_artifact_analysis(
            source,
            source_sample_rate=sample_rate,
            np_module=np,
            signal_module=signal,
        )
        output_analysis = _resample_artifact_analysis(
            converted,
            source_sample_rate=sample_rate,
            np_module=np,
            signal_module=signal,
        )
        analysis_length = min(len(source_analysis), len(output_analysis))
        source_analysis = source_analysis[:analysis_length]
        output_analysis = output_analysis[:analysis_length]
        matched_output_analysis = _match_analysis_level(
            source_analysis,
            output_analysis,
            np_module=np,
        )

        yin_divisor = math.gcd(
            _ARTIFACT_ANALYSIS_SAMPLE_RATE,
            _ARTIFACT_YIN_SAMPLE_RATE,
        )
        yin_up = _ARTIFACT_YIN_SAMPLE_RATE // yin_divisor
        yin_down = _ARTIFACT_ANALYSIS_SAMPLE_RATE // yin_divisor
        source_yin_audio = signal.resample_poly(
            source_analysis,
            yin_up,
            yin_down,
        ).astype(np.float32, copy=False)
        output_yin_audio = signal.resample_poly(
            matched_output_analysis,
            yin_up,
            yin_down,
        ).astype(np.float32, copy=False)
        source_coarse_f0 = _yin_track(
            source_yin_audio,
            np_module=np,
            librosa_module=librosa,
        )
        output_coarse_f0 = _yin_track(
            output_yin_audio,
            np_module=np,
            librosa_module=librosa,
        )
        output_coarse_rms_db = _frame_rms_db(
            matched_output_analysis,
            np_module=np,
            librosa_module=librosa,
        )
        coarse_frame_count = min(
            len(source_coarse_f0),
            len(output_coarse_f0),
            len(output_coarse_rms_db),
        )
        source_coarse_f0 = source_coarse_f0[:coarse_frame_count]
        output_coarse_f0 = output_coarse_f0[:coarse_frame_count]
        coarse_eligible = _coarse_artifact_eligibility(
            source_f0=source_coarse_f0,
            output_f0=output_coarse_f0,
            output_rms_db=output_coarse_rms_db[:coarse_frame_count],
            key_shift=key_shift,
            np_module=np,
        )
        coarse_output_lags, coarse_source_lags = _pitch_lag_tracks(
            output_coarse_f0,
            key_shift=key_shift,
            np_module=np,
        )
        coarse_output_lags[~coarse_eligible] = 0
        coarse_source_lags[~coarse_eligible] = 0
        coarse_output_periodicity = _periodicity_at_lags(
            matched_output_analysis,
            coarse_output_lags,
            frame_count=coarse_frame_count,
            np_module=np,
        )
        coarse_source_periodicity = _periodicity_at_lags(
            source_analysis,
            coarse_source_lags,
            frame_count=coarse_frame_count,
            np_module=np,
        )
        interest = _coarse_artifact_interest(
            source_f0=source_coarse_f0,
            output_f0=output_coarse_f0,
            output_rms_db=output_coarse_rms_db[:coarse_frame_count],
            source_periodicity=coarse_source_periodicity,
            output_periodicity=coarse_output_periodicity,
            key_shift=key_shift,
            np_module=np,
        )
        if not bool(np.any(interest)):
            return samples
        fine_windows = _fine_analysis_windows(interest, np_module=np)
        if fine_windows is None or not fine_windows:
            return samples

        source_f0, source_voiced_flag, source_probability, source_rms_db = (
            _voicing_tracks_for_windows(
                source_analysis,
                fine_windows,
                total_frames=coarse_frame_count,
                np_module=np,
                librosa_module=librosa,
            )
        )
        output_f0, output_voiced_flag, output_probability, output_rms_db = (
            _voicing_tracks_for_windows(
                matched_output_analysis,
                fine_windows,
                total_frames=coarse_frame_count,
                np_module=np,
                librosa_module=librosa,
            )
        )
        output_lags, source_lags = _pitch_lag_tracks(
            output_f0,
            key_shift=key_shift,
            np_module=np,
        )
        output_periodicity = _periodicity_at_lags(
            matched_output_analysis,
            output_lags,
            frame_count=coarse_frame_count,
            np_module=np,
        )
        source_periodicity = _periodicity_at_lags(
            source_analysis,
            source_lags,
            frame_count=coarse_frame_count,
            np_module=np,
        )

        raw_artifact = _candidate_artifact_frames(
            source_f0=source_f0,
            source_voiced_flag=source_voiced_flag,
            source_probability=source_probability,
            source_rms_db=source_rms_db,
            output_f0=output_f0,
            output_voiced_flag=output_voiced_flag,
            output_probability=output_probability,
            output_rms_db=output_rms_db,
            source_periodicity=source_periodicity,
            output_periodicity=output_periodicity,
            key_shift=key_shift,
            np_module=np,
        )
        raw_artifact &= interest
        selected = np.asarray(
            _persistent_artifact_frames(
                raw_artifact.tolist(),
                np_module=np,
            ),
            dtype=bool,  # type: ignore[type-var]
        )
        selected_count = int(np.count_nonzero(selected))
        if selected_count == 0:
            return samples
        if selected_count / len(selected) > _ARTIFACT_MAX_REPAIR_FRACTION:
            # A widespread mismatch is no longer the localized transition
            # defect this guard was calibrated for.  Preserve the reviewed RVC
            # output instead of muting a different singing style or language.
            return samples

        intervals = _artifact_intervals(
            selected,
            output_sample_rate=sample_rate,
            output_samples=len(converted),
            np_module=np,
        )
        envelope = _artifact_envelope(
            intervals,
            sample_rate=sample_rate,
            sample_count=len(converted),
            np_module=np,
        )
        envelope *= _ARTIFACT_REPAIR_DEPTH
        highpass = signal.butter(
            2,
            _ARTIFACT_SOURCE_HIGHPASS_HZ,
            btype="highpass",
            fs=sample_rate,
            output="sos",
        )
        consonants = signal.sosfiltfilt(highpass, source).astype(
            np.float32,
            copy=False,
        )
        consonants = np.clip(consonants, -1.0, 1.0)
        repaired = (
            converted * (1.0 - envelope)
            + consonants * (_ARTIFACT_SOURCE_GAIN * envelope)
        )
        if not bool(np.all(np.isfinite(repaired))):
            _fail("invalid_audio")
        return np.clip(repaired, -1.0, 1.0).astype(np.float32, copy=False)
    except _ArtifactAnalysisSkipped:
        # The rejected experiment is not a prerequisite for a valid RVC result.
        # Preserve that result if the pinned sparse pYIN shape cannot be honored
        # within its explicit computation bounds.
        return samples
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("inference_failed")


def _convert(
    request: _Request,
    api_loader: Callable[[_Request], _RvcApi],
    seed_initializer: Callable[[], None],
) -> tuple[int, object]:
    """Run fixed RVC inference and return its waveform without postprocessing."""

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
    restore_pipeline = None  # type: Optional[Callable[[], None]]
    result_rate = 0
    result_samples = None  # type: Optional[object]
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
        if api.configure_converter is not None:
            restore_pipeline = api.configure_converter(converter)
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
        result_rate = result[0]
        result_samples = result[1]
    except _RvcRuntimeFailure:
        raise
    except BaseException:
        _fail("inference_failed")
    finally:
        if restore_pipeline is not None:
            try:
                restore_pipeline()
            except BaseException:
                # This request owns a short-lived process. Best-effort method
                # restoration must never prevent checkpoint/resource release.
                pass
        if loaded and converter is not None:
            try:
                getattr(converter, "get_vc")("", _PROTECT, _PROTECT)
            except BaseException:
                # Release is best effort because the process exits after this
                # job; it must not replace a completed inference result.
                pass

    # The retired transition replacement created short envelope holes that the
    # downstream sidechain amplified into audible stalls.  Production therefore
    # preserves generated source cores; the analyzer above remains available
    # only for controlled offline experiments with explicit listening gates.
    if result_rate != _MODEL_SAMPLE_RATE or result_samples is None:
        _fail("inference_failed")
    return result_rate, result_samples


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
    """Publish fixed-profile RVC PCM with exact generated source-core ownership."""

    with _runtime_scope(request):
        sample_rate, samples = _convert(
            request,
            api_loader,
            seed_initializer,
        )
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
