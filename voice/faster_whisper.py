"""Adapt validated PCM through explicit local Faster-Whisper model paths."""

from __future__ import annotations

from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
import os
from pathlib import Path
import sys
from threading import Lock
from typing import Final, Literal, Protocol, TypeAlias, cast

from .transcription import (
    TranscriptionFailedError,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionUnavailableError,
    TranscriptionValidationError,
)


FasterWhisperRequestedDevice: TypeAlias = Literal["auto", "cuda", "cpu"]
FasterWhisperResolvedDevice: TypeAlias = Literal["cuda", "cpu"]
FasterWhisperComputeType: TypeAlias = Literal[
    "float16",
    "int8_float16",
    "int8",
    "float32",
]
FasterWhisperStatusState: TypeAlias = Literal[
    "unavailable",
    "available",
    "ready",
]
FasterWhisperStatusReason: TypeAlias = Literal[
    "model_missing",
    "dependencies_missing",
    "runtime_probe_failed",
    "device_unavailable",
    "cuda_unavailable",
    "cuda_initialization_failed",
    "initialization_failed",
]

_REQUESTED_DEVICES = ("auto", "cuda", "cpu")
_REQUIRED_MODEL_FILES: Final = (
    "config.json",
    "model.bin",
    "tokenizer.json",
)
_GIT_LFS_POINTER_PREFIX: Final = (
    b"version https://git-lfs.github.com/spec/v1"
)
_OPTIONAL_RUNTIME_MODULES: Final = (
    "numpy",
    "ctranslate2",
    "faster_whisper",
)
_WINDOWS_CUDA_RUNTIME_DLLS: Final = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
)

_DEPENDENCIES_MESSAGE: Final = (
    "Local transcription dependencies are not installed."
)
_MODEL_MESSAGE: Final = "A local Faster-Whisper model is not installed."
_RUNTIME_MESSAGE: Final = "Local transcription runtime is unavailable."
_CUDA_MESSAGE: Final = "CUDA transcription is unavailable."
_CPU_MESSAGE: Final = "CPU transcription is unavailable."
_INITIALIZATION_MESSAGE: Final = "Local transcription could not initialize."
_TRANSCRIPTION_MESSAGE: Final = "Local transcription failed."
_INVALID_RESULT_MESSAGE: Final = (
    "Local transcription returned an invalid result."
)


def _is_strict_integer(value: object) -> bool:
    """Reject booleans where runtime configuration requires an integer."""

    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class FasterWhisperConfig:
    """Configure one offline Faster-Whisper adapter.

    ``model_path`` must be an absolute path to an already installed local
    CTranslate2 model. Model aliases are deliberately rejected because passing
    one to Faster-Whisper can trigger a Hugging Face download. Device and
    compute selection happen through the runtime probe, not GPU-name matching.
    """

    model_path: Path
    device: FasterWhisperRequestedDevice = "auto"
    device_index: int = 0
    cpu_threads: int = 8
    beam_size: int = 5

    def __post_init__(self) -> None:
        """Reject ambiguous paths and unsafe or excessive runtime settings."""

        if (
            not isinstance(self.model_path, Path)
            or not self.model_path.is_absolute()
        ):
            raise ValueError(
                "Faster-Whisper model_path must be an absolute Path."
            )
        if (
            not isinstance(self.device, str)
            or self.device not in _REQUESTED_DEVICES
        ):
            raise ValueError(
                "Faster-Whisper device must be auto, cuda, or cpu."
            )
        if (
            not _is_strict_integer(self.device_index)
            or not 0 <= self.device_index <= 15
        ):
            raise ValueError(
                "Faster-Whisper device_index must be between 0 and 15."
            )
        if (
            not _is_strict_integer(self.cpu_threads)
            or not 1 <= self.cpu_threads <= 64
        ):
            raise ValueError(
                "Faster-Whisper cpu_threads must be between 1 and 64."
            )
        if (
            not _is_strict_integer(self.beam_size)
            or not 1 <= self.beam_size <= 10
        ):
            raise ValueError(
                "Faster-Whisper beam_size must be between 1 and 10."
            )


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Describe import and compute support without loading a speech model."""

    dependencies_available: bool
    cpu_compute_types: frozenset[str]
    cuda_device_count: int
    cuda_compute_types: frozenset[str]

    def __post_init__(self) -> None:
        """Reject malformed probe data before it influences device selection."""

        if not isinstance(self.dependencies_available, bool):
            raise ValueError("dependencies_available must be a Boolean.")
        if not isinstance(self.cpu_compute_types, frozenset) or not all(
            isinstance(value, str) for value in self.cpu_compute_types
        ):
            raise ValueError("cpu_compute_types must be a string frozenset.")
        if (
            not _is_strict_integer(self.cuda_device_count)
            or self.cuda_device_count < 0
        ):
            raise ValueError("cuda_device_count must be a non-negative integer.")
        if not isinstance(self.cuda_compute_types, frozenset) or not all(
            isinstance(value, str) for value in self.cuda_compute_types
        ):
            raise ValueError("cuda_compute_types must be a string frozenset.")


@dataclass(frozen=True, slots=True)
class FasterWhisperStatus:
    """Expose sanitized adapter readiness without revealing the model path."""

    state: FasterWhisperStatusState
    device: FasterWhisperResolvedDevice | None
    compute_type: FasterWhisperComputeType | None
    reason: FasterWhisperStatusReason | None


class _WhisperSegment(Protocol):
    """Describe the only segment field used by the adapter."""

    text: object


class _WhisperInfo(Protocol):
    """Describe the language metadata used by the adapter."""

    language: object
    language_probability: object


class _WhisperModel(Protocol):
    """Hide concrete Faster-Whisper types behind the testable adapter seam."""

    def transcribe(
        self,
        audio: array[float],
        *,
        language: str | None,
        task: str,
        beam_size: int,
        condition_on_previous_text: bool,
        vad_filter: bool,
        word_timestamps: bool,
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]:
        """Start one lazy transcription operation for the supplied samples."""
        ...


class _RuntimeProbe(Protocol):
    """Describe a dependency and compute-capability probe."""

    def probe(self, *, device_index: int) -> RuntimeCapabilities:
        """Return capabilities without constructing or loading a model."""
        ...


class _ModelFactory(Protocol):
    """Describe construction of one explicit-local-path model adapter."""

    def create(
        self,
        model_path: Path,
        *,
        device: FasterWhisperResolvedDevice,
        device_index: int,
        compute_type: FasterWhisperComputeType,
        cpu_threads: int,
    ) -> _WhisperModel:
        """Load a model from the supplied local path without a model alias."""
        ...


@dataclass(frozen=True, slots=True)
class _Selection:
    """Hold one ordered model-initialization candidate."""

    device: FasterWhisperResolvedDevice
    compute_type: FasterWhisperComputeType
    reason: FasterWhisperStatusReason | None


class _DefaultRuntimeProbe:
    """Inspect optional Faster-Whisper dependencies only when requested."""

    def __init__(self, *, probe_cuda: bool = True) -> None:
        """Choose whether this adapter configuration needs CUDA inspection."""

        if not isinstance(probe_cuda, bool):
            raise TypeError("probe_cuda must be a Boolean.")
        self._probe_cuda = probe_cuda

    def probe(self, *, device_index: int) -> RuntimeCapabilities:
        """Import the optional runtime and query CPU/CUDA compute support.

        Imports happen here instead of module scope so text Chat and ``voice``
        imports keep working on installations that omit local STT. Third-party
        probe exceptions become empty capability sets rather than leaking DLL
        paths or driver details across the Voice boundary.
        """

        modules: dict[str, object] = {}
        try:
            for module_name in _OPTIONAL_RUNTIME_MODULES:
                modules[module_name] = import_module(module_name)
        except Exception:
            return RuntimeCapabilities(
                dependencies_available=False,
                cpu_compute_types=frozenset(),
                cuda_device_count=0,
                cuda_compute_types=frozenset(),
            )

        runtime = modules["ctranslate2"]
        cpu_compute_types = self._supported_compute_types(runtime, "cpu", 0)

        cuda_device_count = 0
        if self._probe_cuda:
            try:
                raw_device_count = getattr(runtime, "get_cuda_device_count")()
                if (
                    not _is_strict_integer(raw_device_count)
                    or raw_device_count < 0
                ):
                    raise ValueError("Invalid CUDA device count.")
                cuda_device_count = raw_device_count
            except Exception:
                cuda_device_count = 0

        # CTranslate2 can enumerate a driver-backed CUDA device and advertise
        # float16 even when the dynamically loaded CUDA 12/cuDNN 9 libraries
        # required by speech inference are absent. On Windows that false
        # positive would select CUDA in auto mode and fail only after the user
        # records audio, so verify the concrete DLL set before advertising it.
        if (
            cuda_device_count > 0
            and sys.platform == "win32"
            and not _windows_cuda_runtime_available(runtime)
        ):
            cuda_device_count = 0

        cuda_compute_types = frozenset[str]()
        if device_index < cuda_device_count:
            cuda_compute_types = self._supported_compute_types(
                runtime,
                "cuda",
                device_index,
            )

        return RuntimeCapabilities(
            dependencies_available=True,
            cpu_compute_types=cpu_compute_types,
            cuda_device_count=cuda_device_count,
            cuda_compute_types=cuda_compute_types,
        )

    @staticmethod
    def _supported_compute_types(
        runtime: object,
        device: str,
        device_index: int,
    ) -> frozenset[str]:
        """Normalize one CTranslate2 query and contain runtime failures."""

        try:
            values = getattr(runtime, "get_supported_compute_types")(
                device,
                device_index,
            )
            return frozenset(
                value for value in values if isinstance(value, str)
            )
        except Exception:
            return frozenset()


def _windows_cuda_search_directories(runtime: object) -> tuple[Path, ...]:
    """Return bounded trusted candidates for separately installed CUDA DLLs.

    ``ctypes`` deliberately excludes the process working directory from its
    default DLL search. Explicit absolute candidates retain that protection
    while supporting normal CUDA ``PATH`` installs, virtual-environment NVIDIA
    wheels, and the CTranslate2 package directory.
    """

    candidates: list[Path] = []
    runtime_file = getattr(runtime, "__file__", None)
    if isinstance(runtime_file, (str, os.PathLike)):
        candidates.append(Path(runtime_file).resolve().parent)

    prefix = Path(sys.prefix).resolve()
    candidates.extend(
        (
            prefix / "Library" / "bin",
            prefix / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
            prefix / "Lib" / "site-packages" / "nvidia" / "cublas" / "lib" / "x64",
            prefix / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        )
    )
    cuda_root = os.environ.get("CUDA_PATH")
    if cuda_root:
        root = Path(cuda_root.strip('"'))
        if root.is_absolute():
            candidates.append(root / "bin")
    for raw_directory in os.get_exec_path():
        directory = Path(raw_directory.strip('"'))
        if directory.is_absolute():
            candidates.append(directory)

    # Preserve search priority while avoiding repeated file probes. Resolving
    # only absolute directory strings prevents a hostile current directory
    # from influencing readiness through a relative PATH entry.
    return tuple(dict.fromkeys(candidate.resolve() for candidate in candidates))


def _load_windows_runtime_library(
    library_name: str,
    search_directories: tuple[Path, ...],
) -> object | None:
    """Load one known DLL only from explicit absolute search directories."""

    import ctypes

    loader = getattr(ctypes, "WinDLL", None)
    if not callable(loader):
        return None

    for directory in search_directories:
        if not directory.is_absolute():
            continue
        candidate = directory / library_name
        try:
            if candidate.is_file():
                # A fully qualified path makes Python add the DLL's directory
                # only for resolving its dependencies and never searches CWD.
                return loader(str(candidate))
        except (OSError, ValueError):
            continue
    return None


def _windows_cuda_runtime_available(runtime: object) -> bool:
    """Return whether CUDA 12 and cuDNN 9 speech DLLs really load on Windows."""

    try:
        search_directories = _windows_cuda_search_directories(runtime)
    except (OSError, ValueError):
        # Environment search paths and optional package metadata are local
        # inputs. A malformed entry means CUDA is unavailable; it must never
        # escape as a path-bearing readiness error.
        return False
    loaded_libraries: list[object] = []
    for library_name in _WINDOWS_CUDA_RUNTIME_DLLS:
        try:
            library = _load_windows_runtime_library(
                library_name,
                search_directories,
            )
        except Exception:
            return False
        if library is None:
            return False
        # Keep each dependency loaded until the complete set has been checked;
        # releasing an early handle can make a later component look missing.
        loaded_libraries.append(library)
    return len(loaded_libraries) == len(_WINDOWS_CUDA_RUNTIME_DLLS)


class _DefaultModelFactory:
    """Construct a lazy-imported model from an explicit local directory."""

    def create(
        self,
        model_path: Path,
        *,
        device: FasterWhisperResolvedDevice,
        device_index: int,
        compute_type: FasterWhisperComputeType,
        cpu_threads: int,
    ) -> _WhisperModel:
        """Load one verified local model without resolving a Hub model alias."""

        faster_whisper = import_module("faster_whisper")
        numpy = import_module("numpy")
        model_class = getattr(faster_whisper, "WhisperModel")

        tokenizer_path = model_path / "tokenizer.json"
        # Keeping the required tokenizer open narrows the check/use race on
        # Windows. local_files_only prevents Hub model-alias resolution; later
        # Desktop wiring must also enforce its declared network policy because
        # third-party library behavior is outside this adapter's full control.
        with tokenizer_path.open("rb") as tokenizer_stream:
            if not tokenizer_stream.read(1):
                raise OSError("The local tokenizer is empty.")
            raw_model = model_class(
                str(model_path),
                device=device,
                device_index=device_index,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=1,
                local_files_only=True,
            )
        return _DefaultWhisperModel(raw_model, numpy)


class _DefaultWhisperModel:
    """Translate dependency-free float arrays to NumPy at the library edge."""

    def __init__(self, raw_model: object, numpy: object) -> None:
        self._raw_model = raw_model
        self._numpy = numpy

    def transcribe(
        self,
        audio: array[float],
        *,
        language: str | None,
        task: str,
        beam_size: int,
        condition_on_previous_text: bool,
        vad_filter: bool,
        word_timestamps: bool,
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]:
        """Call the concrete model with a one-dimensional float32 array."""

        numpy_audio = getattr(self._numpy, "asarray")(
            audio,
            dtype=getattr(self._numpy, "float32"),
        )
        raw_result = getattr(self._raw_model, "transcribe")(
            numpy_audio,
            language=language,
            task=task,
            beam_size=beam_size,
            condition_on_previous_text=condition_on_previous_text,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
        )
        return cast(
            tuple[Iterable[_WhisperSegment], _WhisperInfo],
            raw_result,
        )


def _local_model_is_complete(model_path: Path) -> bool:
    """Require files that keep local model loading complete and offline."""

    try:
        if not model_path.is_dir():
            return False
        for file_name in _REQUIRED_MODEL_FILES:
            candidate = model_path / file_name
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                return False

        # A Git LFS pointer has a valid filename but is not a usable model. This
        # check gives an actionable missing-model state before loading a backend.
        with (model_path / "model.bin").open("rb") as stream:
            if stream.read(len(_GIT_LFS_POINTER_PREFIX)) == _GIT_LFS_POINTER_PREFIX:
                return False
    except OSError:
        return False
    return True


def _pcm_s16le_to_float32(pcm_s16le: bytes) -> array[float]:
    """Convert little-endian signed PCM to normalized dependency-free floats."""

    integer_samples = array("h")
    integer_samples.frombytes(pcm_s16le)
    if sys.byteorder != "little":
        integer_samples.byteswap()

    # Dividing by 32768 maps the asymmetric int16 domain to [-1.0, 1.0),
    # matching the normalized waveform expected by Faster-Whisper.
    return array("f", (sample / 32_768.0 for sample in integer_samples))


class FasterWhisperTranscriber:
    """Run one reusable local model behind the engine-independent contract.

    Construction and package import are light. ``get_status`` performs only
    file/import/capability checks, while the first ``transcribe`` call loads the
    model. Initialization is cached and serialized so a failed CUDA runtime is
    not probed for every utterance. Inference failures never trigger a second
    transcription on another device because duplicate inference can create
    surprising latency and inconsistent text.
    """

    def __init__(
        self,
        config: FasterWhisperConfig,
        *,
        runtime_probe: _RuntimeProbe | None = None,
        model_factory: _ModelFactory | None = None,
    ) -> None:
        """Store offline configuration without importing or loading STT code."""

        if not isinstance(config, FasterWhisperConfig):
            raise TypeError("config must be a FasterWhisperConfig.")
        self._config = config
        self._runtime_probe = runtime_probe or _DefaultRuntimeProbe(
            probe_cuda=config.device != "cpu"
        )
        self._model_factory = model_factory or _DefaultModelFactory()
        self._model: _WhisperModel | None = None
        self._resolved_device: FasterWhisperResolvedDevice | None = None
        self._compute_type: FasterWhisperComputeType | None = None
        self._ready_reason: FasterWhisperStatusReason | None = None
        self._initialization_failed = False
        self._model_lock = Lock()
        self._inference_lock = Lock()

    def get_status(self) -> FasterWhisperStatus:
        """Return sanitized availability without loading the configured model."""

        with self._model_lock:
            if self._model is not None:
                return FasterWhisperStatus(
                    state="ready",
                    device=self._resolved_device,
                    compute_type=self._compute_type,
                    reason=self._ready_reason,
                )
            if self._initialization_failed:
                return FasterWhisperStatus(
                    state="unavailable",
                    device=None,
                    compute_type=None,
                    reason="initialization_failed",
                )

            selections, unavailable_reason = self._probe_selections()
            if not selections:
                return FasterWhisperStatus(
                    state="unavailable",
                    device=None,
                    compute_type=None,
                    reason=unavailable_reason,
                )
            selection = selections[0]
            return FasterWhisperStatus(
                state="available",
                device=selection.device,
                compute_type=selection.compute_type,
                reason=selection.reason,
            )

    def transcribe(
        self,
        request: TranscriptionRequest,
    ) -> TranscriptionResult:
        """Return one final local transcript or a sanitized typed failure."""

        if not isinstance(request, TranscriptionRequest):
            raise TranscriptionValidationError(
                "Transcription request must be validated before inference."
            )

        model = self._ensure_model()
        samples = _pcm_s16le_to_float32(request.capture.pcm_s16le)
        if len(samples) != request.capture.sample_count:
            # This should be unreachable after VoiceCapture validation, but it
            # prevents a platform conversion anomaly from reaching native code.
            raise TranscriptionValidationError(
                "Transcription PCM conversion produced an invalid sample count."
            )

        language = None if request.language == "auto" else request.language
        with self._inference_lock:
            try:
                segments_iter, info = model.transcribe(
                    samples,
                    language=language,
                    task="transcribe",
                    beam_size=self._config.beam_size,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    word_timestamps=False,
                )

                # Faster-Whisper returns a lazy generator. Consuming it inside
                # this guarded scope is what actually completes inference and
                # ensures a late exception cannot masquerade as a final result.
                segments = tuple(segments_iter)
            except Exception:
                raise TranscriptionFailedError(_TRANSCRIPTION_MESSAGE) from None

        try:
            segment_text: list[str] = []
            for segment in segments:
                text = segment.text
                if not isinstance(text, str):
                    raise TypeError("Invalid segment text.")
                segment_text.append(text)

            transcript = "".join(segment_text).strip()
            detected_language = info.language
            if detected_language not in ("zh", "en"):
                raise ValueError("Unsupported detected language.")

            probability = info.language_probability
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
            ):
                raise TypeError("Invalid language probability.")

            return TranscriptionResult(
                text=transcript,
                language=detected_language,
                language_probability=float(probability),
            )
        except (AttributeError, TypeError, ValueError, TranscriptionValidationError):
            raise TranscriptionFailedError(_INVALID_RESULT_MESSAGE) from None

    def _ensure_model(self) -> _WhisperModel:
        """Load exactly one model and limit fallback to auto initialization."""

        with self._model_lock:
            if self._model is not None:
                return self._model
            if self._initialization_failed:
                raise TranscriptionUnavailableError(
                    _INITIALIZATION_MESSAGE
                )

            selections, unavailable_reason = self._probe_selections()
            if not selections:
                raise self._unavailable_error(unavailable_reason)

            for selection in selections:
                try:
                    model = self._model_factory.create(
                        self._config.model_path,
                        device=selection.device,
                        device_index=(
                            self._config.device_index
                            if selection.device == "cuda"
                            else 0
                        ),
                        compute_type=selection.compute_type,
                        cpu_threads=self._config.cpu_threads,
                    )
                except Exception:
                    # Auto may make one CPU attempt after CUDA initialization
                    # fails. Explicit device requests and CPU failures stop here.
                    if (
                        self._config.device == "auto"
                        and selection.device == "cuda"
                    ):
                        continue
                    self._initialization_failed = True
                    raise TranscriptionUnavailableError(
                        _INITIALIZATION_MESSAGE
                    ) from None

                self._model = model
                self._resolved_device = selection.device
                self._compute_type = selection.compute_type
                self._ready_reason = selection.reason
                return model

            self._initialization_failed = True
            raise TranscriptionUnavailableError(
                _INITIALIZATION_MESSAGE
            ) from None

    def _probe_selections(
        self,
    ) -> tuple[list[_Selection], FasterWhisperStatusReason]:
        """Return ordered candidates plus a sanitized empty-list reason."""

        if not _local_model_is_complete(self._config.model_path):
            return [], "model_missing"
        try:
            capabilities = self._runtime_probe.probe(
                device_index=self._config.device_index
            )
        except Exception:
            return [], "runtime_probe_failed"
        if not capabilities.dependencies_available:
            return [], "dependencies_missing"

        cpu_selection = self._cpu_selection(capabilities)
        cuda_selection = self._cuda_selection(capabilities)

        if self._config.device == "cpu":
            return (
                [cpu_selection] if cpu_selection is not None else [],
                "device_unavailable",
            )
        if self._config.device == "cuda":
            return (
                [cuda_selection] if cuda_selection is not None else [],
                "device_unavailable",
            )

        selections: list[_Selection] = []
        if cuda_selection is not None:
            selections.append(cuda_selection)
        if cpu_selection is not None:
            fallback_reason: FasterWhisperStatusReason = (
                "cuda_initialization_failed"
                if cuda_selection is not None
                else "cuda_unavailable"
            )
            selections.append(
                _Selection(
                    device=cpu_selection.device,
                    compute_type=cpu_selection.compute_type,
                    reason=fallback_reason,
                )
            )
        return selections, "device_unavailable"

    def _cpu_selection(
        self,
        capabilities: RuntimeCapabilities,
    ) -> _Selection | None:
        """Choose the fastest conservative CPU type supported at runtime."""

        if "int8" in capabilities.cpu_compute_types:
            return _Selection("cpu", "int8", None)
        if "float32" in capabilities.cpu_compute_types:
            return _Selection("cpu", "float32", None)
        return None

    def _cuda_selection(
        self,
        capabilities: RuntimeCapabilities,
    ) -> _Selection | None:
        """Choose a CUDA type only when the requested index is queryable."""

        if self._config.device_index >= capabilities.cuda_device_count:
            return None
        if "float16" in capabilities.cuda_compute_types:
            return _Selection("cuda", "float16", None)
        if "int8_float16" in capabilities.cuda_compute_types:
            return _Selection("cuda", "int8_float16", None)
        return None

    def _unavailable_error(
        self,
        reason: FasterWhisperStatusReason,
    ) -> TranscriptionUnavailableError:
        """Map internal status reasons to stable user-actionable failures."""

        if reason == "model_missing":
            return TranscriptionUnavailableError(_MODEL_MESSAGE)
        if reason == "dependencies_missing":
            return TranscriptionUnavailableError(_DEPENDENCIES_MESSAGE)
        if self._config.device == "cuda":
            return TranscriptionUnavailableError(_CUDA_MESSAGE)
        if self._config.device == "cpu":
            return TranscriptionUnavailableError(_CPU_MESSAGE)
        return TranscriptionUnavailableError(_RUNTIME_MESSAGE)
