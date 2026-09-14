"""Test the optional Faster-Whisper adapter without loading native packages."""

from __future__ import annotations

import math
import struct
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

import voice as voice_package
import voice.faster_whisper as faster_whisper_module
from voice.capture import (
    VOICE_CAPTURE_FRAME_SAMPLES,
    VOICE_CAPTURE_MIN_SPEECH_SAMPLES,
    VOICE_CAPTURE_SAMPLE_RATE_HZ,
    VoiceCapture,
)
from voice.faster_whisper import (
    FasterWhisperComputeType,
    FasterWhisperConfig,
    FasterWhisperRequestedDevice,
    FasterWhisperResolvedDevice,
    FasterWhisperTranscriber,
    RuntimeCapabilities,
)
from voice.transcription import (
    TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
    TranscriptionFailedError,
    TranscriptionRequest,
    TranscriptionUnavailableError,
)


@dataclass(frozen=True, slots=True)
class _CreateCall:
    """Record one model-construction attempt for exact policy assertions."""

    model_path: Path
    device: str
    device_index: int
    compute_type: str
    cpu_threads: int


class _FakeRuntimeProbe:
    """Return deterministic native-runtime capabilities without importing them."""

    def __init__(self, capabilities: RuntimeCapabilities) -> None:
        self.capabilities = capabilities
        self.device_indexes: list[int] = []

    def probe(self, *, device_index: int) -> RuntimeCapabilities:
        """Record the requested CUDA index and return configured capabilities."""

        self.device_indexes.append(device_index)
        return self.capabilities


class _FakeModel:
    """Mimic the small Faster-Whisper surface consumed by the adapter."""

    def __init__(
        self,
        *,
        segment_texts: list[object] | None = None,
        language: object = "en",
        language_probability: object = 0.9,
        transcribe_error: BaseException | None = None,
        generator_error: BaseException | None = None,
    ) -> None:
        self.segment_texts = ["hello"] if segment_texts is None else segment_texts
        self.language = language
        self.language_probability = language_probability
        self.transcribe_error = transcribe_error
        self.generator_error = generator_error
        self.calls: list[tuple[array[float], dict[str, object]]] = []
        self.consumed_segments = 0

    def transcribe(
        self,
        audio: array[float],
        **options: object,
    ) -> tuple[Iterator[SimpleNamespace], SimpleNamespace]:
        """Record audio/options and return lazy segments plus detection metadata."""

        self.calls.append((audio, dict(options)))
        if self.transcribe_error is not None:
            raise self.transcribe_error

        def _segments() -> Iterator[SimpleNamespace]:
            """Expose whether the adapter consumes the entire lazy result."""

            for text in self.segment_texts:
                self.consumed_segments += 1
                yield SimpleNamespace(text=text)
            if self.generator_error is not None:
                raise self.generator_error

        return (
            _segments(),
            SimpleNamespace(
                language=self.language,
                language_probability=self.language_probability,
            ),
        )


class _FakeModelFactory:
    """Return or raise scripted outcomes for model construction attempts."""

    def __init__(self, outcomes: list[object]) -> None:
        if not outcomes:
            raise ValueError("Fake model factory requires at least one outcome.")
        self.outcomes = outcomes
        self.calls: list[_CreateCall] = []

    def create(
        self,
        model_path: Path,
        *,
        device: FasterWhisperResolvedDevice,
        device_index: int,
        compute_type: FasterWhisperComputeType,
        cpu_threads: int,
    ) -> Any:
        """Record an attempt and resolve its matching scripted outcome."""

        self.calls.append(
            _CreateCall(
                model_path=model_path,
                device=device,
                device_index=device_index,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
            )
        )
        outcome_index = min(len(self.calls) - 1, len(self.outcomes) - 1)
        outcome = self.outcomes[outcome_index]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _capabilities(
    *,
    dependencies_available: bool = True,
    cpu_compute_types: tuple[str, ...] = ("int8",),
    cuda_device_count: int = 0,
    cuda_compute_types: tuple[str, ...] = (),
) -> RuntimeCapabilities:
    """Build one explicit native-runtime capability snapshot."""

    return RuntimeCapabilities(
        dependencies_available=dependencies_available,
        cpu_compute_types=frozenset(cpu_compute_types),
        cuda_device_count=cuda_device_count,
        cuda_compute_types=frozenset(cuda_compute_types),
    )


def _model_directory(tmp_path: Path) -> Path:
    """Create the minimum complete local CTranslate2 model directory."""

    model_path = tmp_path / "private-model-path-do-not-leak"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    (model_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_path / "model.bin").write_bytes(b"model weights")
    return model_path


def _capture(samples: list[int] | None = None) -> VoiceCapture:
    """Build a valid full-capture PCM fixture with an interior speech window."""

    sample_count = VOICE_CAPTURE_MIN_SPEECH_SAMPLES + VOICE_CAPTURE_FRAME_SAMPLES
    values = [0] * sample_count if samples is None else list(samples)
    if len(values) != sample_count:
        raise ValueError("Capture fixture must contain exactly 3520 samples.")
    # The marked speech begins after the first frame so tests can prove the
    # adapter forwards contextual audio outside the Voice activity markers.
    values[VOICE_CAPTURE_FRAME_SAMPLES] = (
        values[VOICE_CAPTURE_FRAME_SAMPLES] or 1
    )
    pcm = struct.pack(f"<{sample_count}h", *values)
    return VoiceCapture(
        session_id="voice_faster-whisper-test",
        pcm_s16le=pcm,
        sample_rate_hz=VOICE_CAPTURE_SAMPLE_RATE_HZ,
        sample_count=sample_count,
        speech_start_sample=VOICE_CAPTURE_FRAME_SAMPLES,
        speech_end_sample=sample_count,
    )


def _transcriber(
    model_path: Path,
    *,
    device: FasterWhisperRequestedDevice = "auto",
    device_index: int = 0,
    cpu_threads: int = 8,
    beam_size: int = 5,
    capabilities: RuntimeCapabilities | None = None,
    outcomes: list[object] | None = None,
) -> tuple[FasterWhisperTranscriber, _FakeRuntimeProbe, _FakeModelFactory]:
    """Assemble an injected adapter and expose both observable test doubles."""

    probe = _FakeRuntimeProbe(capabilities or _capabilities())
    factory = _FakeModelFactory(outcomes or [_FakeModel()])
    adapter = FasterWhisperTranscriber(
        FasterWhisperConfig(
            model_path=model_path,
            device=device,
            device_index=device_index,
            cpu_threads=cpu_threads,
            beam_size=beam_size,
        ),
        runtime_probe=probe,
        model_factory=factory,
    )
    return adapter, probe, factory


def _request(*, language: str = "auto", capture: VoiceCapture | None = None) -> TranscriptionRequest:
    """Build a valid request while allowing language and PCM assertions."""

    return TranscriptionRequest(
        capture=capture or _capture(),
        language=language,  # type: ignore[arg-type]
    )


def test_adapter_module_import_does_not_require_optional_native_packages() -> None:
    """Keep Voice importable and expose its API without optional packages."""

    assert voice_package.FasterWhisperConfig is FasterWhisperConfig
    assert voice_package.FasterWhisperTranscriber is FasterWhisperTranscriber
    assert voice_package.RuntimeCapabilities is RuntimeCapabilities


def test_default_runtime_is_lazy_and_forces_local_model_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify lazy imports and force model construction to local-only resolution."""

    imported_modules: list[str] = []
    model_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    numpy_calls: list[tuple[array[float], object]] = []

    class RawModel:
        def __init__(self, *args: object, **options: object) -> None:
            model_calls.append((args, dict(options)))

        def transcribe(
            self,
            audio: object,
            **_options: object,
        ) -> tuple[list[SimpleNamespace], SimpleNamespace]:
            return (
                [SimpleNamespace(text=" local result ")],
                SimpleNamespace(language="en", language_probability=0.8),
            )

    def asarray(audio: array[float], *, dtype: object) -> array[float]:
        """Record the NumPy boundary while keeping the test dependency-free."""

        numpy_calls.append((audio, dtype))
        return audio

    runtime_modules: dict[str, object] = {
        "numpy": SimpleNamespace(asarray=asarray, float32="fake-float32"),
        "ctranslate2": SimpleNamespace(
            get_cuda_device_count=lambda: 0,
            get_supported_compute_types=lambda device, index: (
                {"int8"} if (device, index) == ("cpu", 0) else set()
            ),
        ),
        "faster_whisper": SimpleNamespace(WhisperModel=RawModel),
    }

    def fake_import_module(name: str) -> object:
        """Return controlled optional modules without importing native wheels."""

        imported_modules.append(name)
        return runtime_modules[name]

    monkeypatch.setattr(
        faster_whisper_module,
        "import_module",
        fake_import_module,
    )
    model_path = _model_directory(tmp_path)
    adapter = FasterWhisperTranscriber(
        FasterWhisperConfig(model_path=model_path, device="cpu")
    )

    assert imported_modules == []
    assert adapter.get_status().state == "available"
    assert model_calls == []

    result = adapter.transcribe(_request(language="en"))

    assert result.text == "local result"
    assert model_calls == [
        (
            (str(model_path),),
            {
                "device": "cpu",
                "device_index": 0,
                "compute_type": "int8",
                "cpu_threads": 8,
                "num_workers": 1,
                "local_files_only": True,
            },
        )
    ]
    assert len(numpy_calls) == 1
    assert numpy_calls[0][1] == "fake-float32"


def test_windows_probe_suppresses_cuda_when_required_dlls_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject driver-only CUDA capability before a user records any audio."""

    runtime = SimpleNamespace(
        __file__="C:/safe/ctranslate2/__init__.py",
        get_cuda_device_count=lambda: 1,
        get_supported_compute_types=lambda device, _index: (
            {"float16"} if device == "cuda" else {"int8"}
        ),
    )
    modules = {
        "numpy": object(),
        "ctranslate2": runtime,
        "faster_whisper": object(),
    }
    monkeypatch.setattr(
        faster_whisper_module,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(faster_whisper_module.sys, "platform", "win32")
    monkeypatch.setattr(
        faster_whisper_module,
        "_windows_cuda_runtime_available",
        lambda _runtime: False,
    )

    capabilities = faster_whisper_module._DefaultRuntimeProbe().probe(
        device_index=0
    )

    assert capabilities.dependencies_available is True
    assert capabilities.cpu_compute_types == frozenset({"int8"})
    assert capabilities.cuda_device_count == 0
    assert capabilities.cuda_compute_types == frozenset()


def test_non_windows_probe_keeps_reported_cuda_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the Windows loader preflight from changing other platforms."""

    runtime = SimpleNamespace(
        get_cuda_device_count=lambda: 1,
        get_supported_compute_types=lambda device, _index: (
            {"float16"} if device == "cuda" else {"int8"}
        ),
    )
    modules = {
        "numpy": object(),
        "ctranslate2": runtime,
        "faster_whisper": object(),
    }
    monkeypatch.setattr(
        faster_whisper_module,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(faster_whisper_module.sys, "platform", "linux")

    capabilities = faster_whisper_module._DefaultRuntimeProbe().probe(
        device_index=0
    )

    assert capabilities.cuda_device_count == 1
    assert capabilities.cuda_compute_types == frozenset({"float16"})


def test_cpu_only_default_probe_does_not_inspect_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an explicit CPU choice from loading or querying CUDA libraries."""

    def unexpected_cuda_probe() -> int:
        """Fail if the CPU-only path touches CUDA device enumeration."""

        raise AssertionError("CPU-only readiness must not inspect CUDA.")

    def unexpected_windows_runtime(_runtime: object) -> bool:
        """Fail if the CPU-only path attempts to load CUDA libraries."""

        raise AssertionError("CPU-only readiness must not load CUDA DLLs.")

    runtime = SimpleNamespace(
        get_cuda_device_count=unexpected_cuda_probe,
        get_supported_compute_types=lambda device, _index: (
            {"int8"} if device == "cpu" else set()
        ),
    )
    modules = {
        "numpy": object(),
        "ctranslate2": runtime,
        "faster_whisper": object(),
    }
    monkeypatch.setattr(
        faster_whisper_module,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(faster_whisper_module.sys, "platform", "win32")
    monkeypatch.setattr(
        faster_whisper_module,
        "_windows_cuda_runtime_available",
        unexpected_windows_runtime,
    )

    capabilities = faster_whisper_module._DefaultRuntimeProbe(
        probe_cuda=False
    ).probe(device_index=0)

    assert capabilities.dependencies_available is True
    assert capabilities.cpu_compute_types == frozenset({"int8"})
    assert capabilities.cuda_device_count == 0
    assert capabilities.cuda_compute_types == frozenset()


def test_windows_cuda_preflight_requires_every_runtime_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat a partial CUDA/cuDNN install as unavailable without diagnostics."""

    attempted: list[str] = []

    def load_library(
        library_name: str,
        _directories: tuple[Path, ...],
    ) -> object | None:
        """Fail only the cuDNN operations component and record probe order."""

        attempted.append(library_name)
        if library_name == "cudnn_ops64_9.dll":
            return None
        return object()

    monkeypatch.setattr(
        faster_whisper_module,
        "_windows_cuda_search_directories",
        lambda _runtime: (),
    )
    monkeypatch.setattr(
        faster_whisper_module,
        "_load_windows_runtime_library",
        load_library,
    )

    assert not faster_whisper_module._windows_cuda_runtime_available(object())
    assert attempted == [
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn64_9.dll",
        "cudnn_ops64_9.dll",
    ]


def test_windows_runtime_loader_uses_only_absolute_existing_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent CUDA preflight from falling back to the working directory."""

    import ctypes

    trusted_directory = tmp_path / "trusted-runtime"
    trusted_directory.mkdir()
    library_path = trusted_directory / "cublas64_12.dll"
    library_path.write_bytes(b"test-double")
    loaded_names: list[str] = []

    def load_library(path: str) -> object:
        """Record the fully qualified path used by the safe loader seam."""

        loaded_names.append(path)
        return object()

    monkeypatch.setattr(ctypes, "WinDLL", load_library, raising=False)

    loaded = faster_whisper_module._load_windows_runtime_library(
        library_path.name,
        (Path("relative-runtime"), trusted_directory),
    )

    assert loaded is not None
    assert loaded_names == [str(library_path)]
    assert Path(loaded_names[0]).is_absolute()


def test_missing_windows_cuda_runtime_selects_safe_device_statuses(
    tmp_path: Path,
) -> None:
    """Fall auto back to CPU while explicit CUDA remains unavailable."""

    capabilities = _capabilities(
        cpu_compute_types=("int8",),
        cuda_device_count=0,
    )
    model_path = _model_directory(tmp_path)
    automatic, _probe, _factory = _transcriber(
        model_path,
        device="auto",
        capabilities=capabilities,
    )
    explicit, _probe, _factory = _transcriber(
        model_path,
        device="cuda",
        capabilities=capabilities,
    )

    automatic_status = automatic.get_status()
    explicit_status = explicit.get_status()
    assert (
        automatic_status.state,
        automatic_status.device,
        automatic_status.compute_type,
        automatic_status.reason,
    ) == ("available", "cpu", "int8", "cuda_unavailable")
    assert (
        explicit_status.state,
        explicit_status.device,
        explicit_status.compute_type,
        explicit_status.reason,
    ) == ("unavailable", None, None, "device_unavailable")


def test_cpu_int8_status_and_lazy_initialization(tmp_path: Path) -> None:
    """Report CPU/int8 as available before constructing the model on demand."""

    model = _FakeModel()
    model_path = _model_directory(tmp_path)
    adapter, probe, factory = _transcriber(
        model_path,
        device="cpu",
        cpu_threads=3,
        capabilities=_capabilities(cpu_compute_types=("int8", "float32")),
        outcomes=[model],
    )

    status = adapter.get_status()

    assert status.state == "available"
    assert status.device == "cpu"
    assert status.compute_type == "int8"
    assert status.reason is None
    assert probe.device_indexes == [0]
    assert factory.calls == []

    result = adapter.transcribe(_request(language="en"))

    assert result.text == "hello"
    assert factory.calls == [
        _CreateCall(
            model_path=model_path,
            device="cpu",
            device_index=0,
            compute_type="int8",
            cpu_threads=3,
        )
    ]
    ready_status = adapter.get_status()
    assert ready_status.state == "ready"
    assert ready_status.device == "cpu"
    assert ready_status.compute_type == "int8"
    assert ready_status.reason is None


def test_auto_prefers_cuda_float16_when_runtime_supports_it(tmp_path: Path) -> None:
    """Use the configured CUDA index and float16 ahead of CPU for auto mode."""

    model = _FakeModel(language="zh")
    model_path = _model_directory(tmp_path)
    adapter, probe, factory = _transcriber(
        model_path,
        device_index=1,
        cpu_threads=6,
        capabilities=_capabilities(
            cpu_compute_types=("int8",),
            cuda_device_count=2,
            cuda_compute_types=("float16", "int8_float16"),
        ),
        outcomes=[model],
    )

    status = adapter.get_status()
    result = adapter.transcribe(_request(language="zh"))

    assert (status.state, status.device, status.compute_type) == (
        "available",
        "cuda",
        "float16",
    )
    assert result.language == "zh"
    assert probe.device_indexes == [1, 1]
    assert factory.calls == [
        _CreateCall(
            model_path=model_path,
            device="cuda",
            device_index=1,
            compute_type="float16",
            cpu_threads=6,
        )
    ]


def test_auto_falls_back_once_when_cuda_model_construction_fails(
    tmp_path: Path,
) -> None:
    """Recover from native CUDA initialization by constructing CPU/int8 once."""

    cpu_model = _FakeModel(segment_texts=[" recovered"], language="en")
    model_path = _model_directory(tmp_path)
    adapter, _probe, factory = _transcriber(
        model_path,
        device_index=1,
        capabilities=_capabilities(
            cpu_compute_types=("int8",),
            cuda_device_count=2,
            cuda_compute_types=("float16",),
        ),
        outcomes=[RuntimeError("private CUDA loader detail"), cpu_model],
    )

    first = adapter.transcribe(_request())
    second = adapter.transcribe(_request())

    assert first.text == "recovered"
    assert second.text == "recovered"
    assert factory.calls == [
        _CreateCall(model_path, "cuda", 1, "float16", 8),
        _CreateCall(model_path, "cpu", 0, "int8", 8),
    ]
    status = adapter.get_status()
    assert (status.state, status.device, status.compute_type) == (
        "ready",
        "cpu",
        "int8",
    )
    assert status.reason == "cuda_initialization_failed"


def test_explicit_cuda_never_falls_back_after_construction_failure(
    tmp_path: Path,
) -> None:
    """Honor an explicit CUDA choice instead of silently changing execution."""

    adapter, _probe, factory = _transcriber(
        _model_directory(tmp_path),
        device="cuda",
        capabilities=_capabilities(
            cpu_compute_types=("int8",),
            cuda_device_count=1,
            cuda_compute_types=("float16",),
        ),
        outcomes=[RuntimeError("private CUDA construction detail"), _FakeModel()],
    )

    with pytest.raises(TranscriptionUnavailableError) as raised:
        adapter.transcribe(_request())

    assert [(call.device, call.compute_type) for call in factory.calls] == [
        ("cuda", "float16")
    ]
    assert "private CUDA construction detail" not in str(raised.value)


def test_cached_initialization_failure_is_not_retried(tmp_path: Path) -> None:
    """Avoid repeating an expensive deterministic native-loader failure."""

    adapter, _probe, factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[RuntimeError("private loader failure")],
    )

    messages: list[str] = []
    for _attempt in range(2):
        with pytest.raises(TranscriptionUnavailableError) as raised:
            adapter.transcribe(_request())
        messages.append(str(raised.value))

    assert len(factory.calls) == 1
    assert messages[0] == messages[1]
    assert all("private loader failure" not in message for message in messages)
    status = adapter.get_status()
    assert status.state == "unavailable"
    assert status.reason == "initialization_failed"


def test_concurrent_first_requests_construct_only_one_model(
    tmp_path: Path,
) -> None:
    """Serialize lazy initialization so concurrent work cannot duplicate a model."""

    model = _FakeModel()
    adapter, _probe, factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[model],
    )
    start = Event()

    def run_request() -> str:
        """Wait for both workers before exercising first-use initialization."""

        if not start.wait(timeout=2):
            raise AssertionError("Concurrent test did not start.")
        return adapter.transcribe(_request()).text

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_request) for _index in range(2)]
        start.set()
        results = [future.result(timeout=2) for future in futures]

    assert results == ["hello", "hello"]
    assert len(factory.calls) == 1


def test_concurrent_inference_is_serialized(tmp_path: Path) -> None:
    """Keep a single-worker native model from processing two captures at once."""

    first_entered = Event()
    concurrent_entry = Event()
    release = Event()
    state_lock = Lock()
    active = 0

    class BlockingModel:
        def transcribe(
            self,
            _audio: array[float],
            **_options: object,
        ) -> tuple[Iterator[SimpleNamespace], SimpleNamespace]:
            def segments() -> Iterator[SimpleNamespace]:
                nonlocal active
                with state_lock:
                    active += 1
                    if active > 1:
                        concurrent_entry.set()
                    first_entered.set()
                if not release.wait(timeout=2):
                    raise AssertionError("Concurrent test was not released.")
                yield SimpleNamespace(text="serialized")
                with state_lock:
                    active -= 1

            return (
                segments(),
                SimpleNamespace(language="en", language_probability=1.0),
            )

    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[BlockingModel()],
    )
    start = Event()

    def run_request() -> str:
        """Begin one inference only after both worker futures exist."""

        if not start.wait(timeout=2):
            raise AssertionError("Concurrent test did not start.")
        return adapter.transcribe(_request()).text

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_request) for _index in range(2)]
        start.set()
        assert first_entered.wait(timeout=2)
        assert not concurrent_entry.wait(timeout=0.1)
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert results == ["serialized", "serialized"]
    assert not concurrent_entry.is_set()


def test_inference_failure_does_not_trigger_device_fallback(tmp_path: Path) -> None:
    """Reserve fallback for initialization because retrying inference duplicates work."""

    model = _FakeModel(
        transcribe_error=RuntimeError("private inference and audio detail")
    )
    adapter, _probe, factory = _transcriber(
        _model_directory(tmp_path),
        capabilities=_capabilities(
            cpu_compute_types=("int8",),
            cuda_device_count=1,
            cuda_compute_types=("float16",),
        ),
        outcomes=[model, _FakeModel()],
    )

    with pytest.raises(TranscriptionFailedError) as raised:
        adapter.transcribe(_request())

    assert [(call.device, call.compute_type) for call in factory.calls] == [
        ("cuda", "float16")
    ]
    assert "private inference and audio detail" not in str(raised.value)


def test_transcribe_consumes_all_lazy_segments_and_joins_then_trims(
    tmp_path: Path,
) -> None:
    """Force lazy inference to completion and retain only surrounding trim changes."""

    model = _FakeModel(
        segment_texts=["  Hello", ", ", "world!  "],
        language="en",
        language_probability=0.75,
    )
    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[model],
    )

    result = adapter.transcribe(_request())

    assert result.text == "Hello, world!"
    assert result.language == "en"
    assert result.language_probability == 0.75
    assert model.consumed_segments == 3


@pytest.mark.parametrize(
    ("request_language", "engine_language"),
    [("auto", None), ("zh", "zh"), ("en", "en")],
)
def test_language_hints_are_mapped_to_engine_arguments(
    tmp_path: Path,
    request_language: str,
    engine_language: str | None,
) -> None:
    """Translate auto to None while preserving explicit Chinese or English hints."""

    model = _FakeModel(language="zh" if request_language == "zh" else "en")
    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        beam_size=7,
        outcomes=[model],
    )

    adapter.transcribe(_request(language=request_language))

    _audio, options = model.calls[0]
    assert options == {
        "language": engine_language,
        "task": "transcribe",
        "beam_size": 7,
        "condition_on_previous_text": False,
        "vad_filter": False,
        "word_timestamps": False,
    }


def test_pcm_is_normalized_to_float32_extrema_and_forwards_entire_capture(
    tmp_path: Path,
) -> None:
    """Decode little-endian s16 samples once without cropping Voice context."""

    sample_count = VOICE_CAPTURE_MIN_SPEECH_SAMPLES + VOICE_CAPTURE_FRAME_SAMPLES
    samples = [0] * sample_count
    samples[:5] = [-32_768, -16_384, 0, 16_384, 32_767]
    samples[VOICE_CAPTURE_FRAME_SAMPLES] = 12_345
    capture = _capture(samples)
    model = _FakeModel()
    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[model],
    )

    adapter.transcribe(_request(capture=capture))

    audio, _options = model.calls[0]
    assert isinstance(audio, array)
    assert audio.typecode == "f"
    assert len(audio) == capture.sample_count
    assert list(audio[:5]) == pytest.approx(
        [-1.0, -0.5, 0.0, 0.5, 32_767 / 32_768]
    )
    assert audio[VOICE_CAPTURE_FRAME_SAMPLES] == pytest.approx(12_345 / 32_768)
    assert audio[-1] == 0.0


def test_integer_probability_is_converted_to_domain_float(tmp_path: Path) -> None:
    """Normalize numeric engine metadata before strict domain validation."""

    model = _FakeModel(language_probability=1)
    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[model],
    )

    result = adapter.transcribe(_request())

    assert result.language_probability == 1.0
    assert isinstance(result.language_probability, float)


@pytest.mark.parametrize(
    ("segment_texts", "language", "probability"),
    [
        (["", " \n\t"], "en", 0.5),
        (["x" * (TRANSCRIPTION_MAX_TEXT_CODE_POINTS + 1)], "en", 0.5),
        (["valid", 42], "en", 0.5),
        (["valid"], "fr", 0.5),
        (["valid"], None, 0.5),
        (["valid"], "en", float("nan")),
        (["valid"], "en", float("inf")),
        (["valid"], "en", -0.01),
        (["valid"], "en", 1.01),
        (["valid"], "en", True),
        (["valid"], "en", "0.5"),
    ],
)
def test_invalid_engine_results_become_sanitized_transcription_failures(
    tmp_path: Path,
    segment_texts: list[object],
    language: object,
    probability: object,
) -> None:
    """Contain corrupt optional-engine output behind one stable failure type."""

    private_marker = "PRIVATE_ENGINE_VALUE"
    poisoned_segments = [
        text.replace("valid", private_marker) if isinstance(text, str) else text
        for text in segment_texts
    ]
    model = _FakeModel(
        segment_texts=poisoned_segments,
        language=language,
        language_probability=probability,
    )
    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[model],
    )

    with pytest.raises(TranscriptionFailedError) as raised:
        adapter.transcribe(_request())

    message = str(raised.value)
    assert message
    assert private_marker not in message
    assert str(probability) not in message


def test_generator_failure_is_sanitized_after_partial_consumption(
    tmp_path: Path,
) -> None:
    """Catch failures raised only while Faster-Whisper's lazy iterator runs."""

    model = _FakeModel(
        segment_texts=["partial PRIVATE_TRANSCRIPT"],
        generator_error=RuntimeError("PRIVATE_GENERATOR_FAILURE"),
    )
    adapter, _probe, _factory = _transcriber(
        _model_directory(tmp_path),
        device="cpu",
        outcomes=[model],
    )

    with pytest.raises(TranscriptionFailedError) as raised:
        adapter.transcribe(_request())

    assert model.consumed_segments == 1
    assert "PRIVATE_TRANSCRIPT" not in str(raised.value)
    assert "PRIVATE_GENERATOR_FAILURE" not in str(raised.value)


def test_missing_optional_dependencies_report_sanitized_unavailable(
    tmp_path: Path,
) -> None:
    """Separate a missing optional install from Backend connection health."""

    model_path = _model_directory(tmp_path)
    adapter, _probe, factory = _transcriber(
        model_path,
        capabilities=_capabilities(dependencies_available=False),
    )

    status = adapter.get_status()
    with pytest.raises(TranscriptionUnavailableError) as raised:
        adapter.transcribe(_request())

    assert status.state == "unavailable"
    assert status.device is None
    assert status.compute_type is None
    assert status.reason
    assert factory.calls == []
    assert str(model_path) not in status.reason
    assert model_path.name not in str(raised.value)


@pytest.mark.parametrize(
    ("broken_name", "broken_kind"),
    [
        ("directory", "missing"),
        ("config.json", "missing"),
        ("tokenizer.json", "missing"),
        ("model.bin", "missing"),
        ("config.json", "empty"),
        ("tokenizer.json", "empty"),
        ("model.bin", "empty"),
        ("model.bin", "git-lfs-pointer"),
        ("model.bin", "directory"),
    ],
)
def test_incomplete_local_model_reports_sanitized_unavailable(
    tmp_path: Path,
    broken_name: str,
    broken_kind: str,
) -> None:
    """Require complete nonempty local assets without exposing their path."""

    if broken_name == "directory":
        model_path = tmp_path / "PRIVATE_MISSING_MODEL_DIRECTORY"
    else:
        model_path = _model_directory(tmp_path)
        target = model_path / broken_name
        if broken_kind == "missing":
            target.unlink()
        elif broken_kind == "empty":
            target.write_bytes(b"")
        elif broken_kind == "git-lfs-pointer":
            target.write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                "oid sha256:PRIVATE_POINTER\nsize 123\n",
                encoding="utf-8",
            )
        else:
            target.unlink()
            target.mkdir()
    adapter, _probe, factory = _transcriber(model_path)

    status = adapter.get_status()
    with pytest.raises(TranscriptionUnavailableError) as raised:
        adapter.transcribe(_request())

    assert status.state == "unavailable"
    assert status.reason
    assert factory.calls == []
    assert str(model_path) not in status.reason
    assert model_path.name not in str(raised.value)


@pytest.mark.parametrize(
    ("device", "device_index", "capabilities"),
    [
        ("cpu", 0, _capabilities(cpu_compute_types=())),
        (
            "cuda",
            2,
            _capabilities(
                cpu_compute_types=("int8",),
                cuda_device_count=1,
                cuda_compute_types=("float16",),
            ),
        ),
        (
            "cuda",
            0,
            _capabilities(
                cpu_compute_types=("int8",),
                cuda_device_count=1,
                cuda_compute_types=("int8",),
            ),
        ),
        ("auto", 0, _capabilities(cpu_compute_types=())),
    ],
)
def test_unusable_requested_device_reports_sanitized_unavailable(
    tmp_path: Path,
    device: FasterWhisperRequestedDevice,
    device_index: int,
    capabilities: RuntimeCapabilities,
) -> None:
    """Reject devices lacking the adapter's required safe compute type."""

    model_path = _model_directory(tmp_path)
    adapter, _probe, factory = _transcriber(
        model_path,
        device=device,
        device_index=device_index,
        capabilities=capabilities,
    )

    status = adapter.get_status()
    with pytest.raises(TranscriptionUnavailableError) as raised:
        adapter.transcribe(_request())

    assert status.state == "unavailable"
    assert status.reason
    assert factory.calls == []
    assert str(model_path) not in status.reason
    assert model_path.name not in str(raised.value)


def test_failure_messages_never_include_model_path_pcm_or_caught_secret(
    tmp_path: Path,
) -> None:
    """Keep local paths, microphone bytes, and native diagnostics private."""

    model_path = _model_directory(tmp_path)
    capture = _capture()
    private_exception = "PRIVATE_NATIVE_EXCEPTION_DETAIL"
    model = _FakeModel(transcribe_error=RuntimeError(private_exception))
    adapter, _probe, _factory = _transcriber(
        model_path,
        device="cpu",
        outcomes=[model],
    )

    with pytest.raises(TranscriptionFailedError) as raised:
        adapter.transcribe(_request(capture=capture))

    message = str(raised.value)
    assert str(model_path) not in message
    assert model_path.name not in message
    assert repr(capture.pcm_s16le[:32]) not in message
    assert private_exception not in message


@pytest.mark.parametrize("model_path", ["small", Path("small")])
def test_config_rejects_model_aliases_and_relative_paths(model_path: object) -> None:
    """Prevent implicit network downloads by requiring an absolute Path."""

    with pytest.raises((TypeError, ValueError)):
        FasterWhisperConfig(model_path=model_path)  # type: ignore[arg-type]


@pytest.mark.parametrize("device", ["", "gpu", "CPU", None, True])
def test_config_rejects_unknown_devices(device: object, tmp_path: Path) -> None:
    """Keep device selection limited to auto, cpu, and cuda."""

    with pytest.raises((TypeError, ValueError)):
        FasterWhisperConfig(
            model_path=tmp_path.resolve(),
            device=device,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_index", -1),
        ("device_index", True),
        ("device_index", 0.0),
        ("device_index", 16),
        ("cpu_threads", 0),
        ("cpu_threads", -1),
        ("cpu_threads", True),
        ("cpu_threads", 1.0),
        ("cpu_threads", 65),
        ("beam_size", 0),
        ("beam_size", -1),
        ("beam_size", True),
        ("beam_size", 1.0),
        ("beam_size", 11),
    ],
)
def test_config_rejects_invalid_integer_controls(
    field: str,
    value: object,
    tmp_path: Path,
) -> None:
    """Reject booleans, non-integers, and unusable worker/search values."""

    arguments: dict[str, object] = {
        "model_path": tmp_path.resolve(),
        "device": "auto",
        "device_index": 0,
        "cpu_threads": 8,
        "beam_size": 5,
    }
    arguments[field] = value

    with pytest.raises((TypeError, ValueError)):
        FasterWhisperConfig(**arguments)  # type: ignore[arg-type]


def test_valid_config_preserves_explicit_values(tmp_path: Path) -> None:
    """Expose immutable configuration inputs for status and diagnostics."""

    model_path = tmp_path.resolve()
    config = FasterWhisperConfig(
        model_path=model_path,
        device="cuda",
        device_index=2,
        cpu_threads=4,
        beam_size=9,
    )

    assert config.model_path == model_path
    assert config.device == "cuda"
    assert config.device_index == 2
    assert config.cpu_threads == 4
    assert config.beam_size == 9
