"""Test the closed one-shot RVC singing-voice runtime adapter."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import socket
import struct
import sys
from types import ModuleType, SimpleNamespace
import wave

import pytest

from scripts import song_rvc_runtime as runtime


def _runtime_fixture(tmp_path: Path) -> dict[str, Path]:
    """Create a minimal path-valid RVC request without real model contents."""

    source = tmp_path / "rvc-source"
    for relative in (
        Path("configs") / "config.py",
        Path("infer") / "hubert.py",
        Path("infer") / "rmvpe.py",
        Path("infer") / "vc" / "modules.py",
    ):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    (source / "infer" / "vc" / "__init__.py").write_bytes(b"")
    (source / "tools").mkdir()
    model = tmp_path / "weights" / "elysia.pth"
    index = tmp_path / "indices" / "elysia.index"
    rmvpe = tmp_path / "pitch" / "rmvpe.pt"
    input_audio = tmp_path / "job" / "input.wav"
    for path, payload in (
        (model, b"model"),
        (index, b"index"),
        (rmvpe, b"rmvpe"),
        (input_audio, b"RIFF-fixture"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    hubert = tmp_path / "hubert"
    hubert.mkdir()
    for name in ("config.json", "preprocessor_config.json", "pytorch_model.bin"):
        (hubert / name).write_bytes(b"asset")
    return {
        "source": source,
        "model": model,
        "index": index,
        "hubert": hubert,
        "rmvpe": rmvpe,
        "input": input_audio,
        "output": input_audio.parent / "output.wav",
    }


def _arguments(paths: dict[str, Path], key_shift: int = 0) -> list[str]:
    """Build the exact eight-pair command line accepted by the adapter."""

    return [
        "--source-root",
        str(paths["source"]),
        "--model",
        str(paths["model"]),
        "--index",
        str(paths["index"]),
        "--hubert",
        str(paths["hubert"]),
        "--rmvpe",
        str(paths["rmvpe"]),
        "--input",
        str(paths["input"]),
        "--output",
        str(paths["output"]),
        "--key-shift",
        str(key_shift),
    ]


def _request(paths: dict[str, Path], key_shift: int = 0) -> runtime._Request:
    """Parse and validate one fixture through the production boundary."""

    return runtime._validate_request(
        runtime._parse_arguments(_arguments(paths, key_shift))
    )


class _CacheProbe:
    """Record whether explicit HuBERT rebinding clears a prior path cache."""

    def __init__(self) -> None:
        self.cleared = False

    def cache_clear(self) -> None:
        """Record one cache invalidation without loading Transformers."""

        self.cleared = True


def _require_numpy() -> ModuleType:
    """Load the private RVC dependency only for PCM-specific adapter tests."""

    # The application environment intentionally does not install NumPy; the
    # ignored RVC runtime does.  Keeping this import local lets the portable
    # command/path tests run in CI while the real dependency exercises PCM on
    # development machines that can actually host the private runtime.
    return pytest.importorskip(
        "numpy",
        reason="PCM adapter tests require the optional private RVC dependency",
    )


def test_parser_requires_each_explicit_option_once_and_bounds_key_shift(
    tmp_path: Path,
) -> None:
    """Reject missing, duplicate, extra, or out-of-range inference controls."""

    paths = _runtime_fixture(tmp_path)
    assert _request(paths, -2).key_shift == -2
    assert _request(paths, 2).key_shift == 2

    for arguments in (
        _arguments(paths)[:-2],
        _arguments(paths) + ["--unexpected", "value"],
        _arguments(paths)[:-2] + ["--model", str(paths["model"])],
    ):
        with pytest.raises(runtime._RvcRuntimeFailure) as captured:
            runtime._parse_arguments(arguments)
        assert captured.value.code == "invalid_request"

    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        _request(paths, 3)
    assert captured.value.code == "invalid_key_shift"


def test_validation_rejects_directory_input_existing_output_and_asset_aliases(
    tmp_path: Path,
) -> None:
    """Keep directories, overwrite targets, and implicit RMVPE names closed."""

    paths = _runtime_fixture(tmp_path)
    paths["input"] = paths["input"].parent
    with pytest.raises(runtime._RvcRuntimeFailure) as directory_error:
        _request(paths)
    assert directory_error.value.code == "invalid_input"

    paths = _runtime_fixture(tmp_path / "existing")
    paths["output"].write_bytes(b"keep")
    with pytest.raises(runtime._RvcRuntimeFailure) as output_error:
        _request(paths)
    assert output_error.value.code == "output_exists"
    assert paths["output"].read_bytes() == b"keep"

    paths = _runtime_fixture(tmp_path / "alias")
    aliased = paths["rmvpe"].with_name("pitch.pt")
    paths["rmvpe"].replace(aliased)
    paths["rmvpe"] = aliased
    with pytest.raises(runtime._RvcRuntimeFailure) as rmvpe_error:
        _request(paths)
    assert rmvpe_error.value.code == "invalid_rmvpe"


def test_runtime_scope_and_conversion_fix_every_upstream_inference_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use only CUDA, speaker zero, RMVPE, and the reviewed numeric controls."""

    paths = _runtime_fixture(tmp_path)
    request = _request(paths, key_shift=-1)
    original_directory = Path.cwd()
    original_arguments = sys.argv[:]
    original_getaddrinfo = socket.getaddrinfo
    monkeypatch.setenv("RVC_CUDA_GRAPH", "1")
    calls: list[tuple[object, ...]] = []
    published: list[tuple[Path, int, object]] = []

    class _FakeConfig:
        """Model a CUDA configuration while checking the isolated scope."""

        def __init__(self) -> None:
            assert Path.cwd() == request.source_root
            assert sys.path[0] == str(request.source_root)
            assert sys.argv == [str(Path(runtime.__file__).resolve())]
            assert os.environ["HF_HUB_OFFLINE"] == "1"
            assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
            assert os.environ["RVC_CUDA_GRAPH"] == "0"
            assert os.environ["weight_root"] == str(request.model.parent)
            assert os.environ["rmvpe_root"] == str(request.rmvpe.parent)
            with pytest.raises(OSError, match="Network access is disabled"):
                socket.getaddrinfo("example.com", 443)
            self.device = "cuda:0"
            self.dml = False

    class _FakeConverter:
        """Capture the narrow call surface exposed to upstream RVC."""

        def __init__(self, _configuration: object) -> None:
            self.version: str | None = None
            self.if_f0 = 0
            self.tgt_sr = 0

        def get_vc(self, model_name: str, first: float, second: float) -> None:
            """Model checkpoint load and release while recording arguments."""

            calls.append(("get_vc", model_name, first, second))
            if model_name:
                self.version = "v2"
                self.if_f0 = 1
                self.tgt_sr = 40_000

        def vc_single(self, *arguments: object) -> tuple[str, tuple[int, list[float]]]:
            """Return one tiny valid fake RVC waveform."""

            calls.append(("vc_single",) + arguments)
            return "ok", (40_000, [0.0, 0.1, -0.1])

    def _loader(_request_value: runtime._Request) -> runtime._RvcApi:
        return runtime._RvcApi(_FakeConfig, _FakeConverter)

    def _publisher(path: Path, sample_rate: int, samples: object) -> None:
        published.append((path, sample_rate, samples))

    runtime._run_request(request, api_loader=_loader, publisher=_publisher)

    assert Path.cwd() == original_directory
    assert sys.argv == original_arguments
    assert socket.getaddrinfo is original_getaddrinfo
    assert os.environ["RVC_CUDA_GRAPH"] == "1"
    assert published == [(request.output_audio, 40_000, [0.0, 0.1, -0.1])]
    assert calls == [
        ("get_vc", request.model.name, 0.33, 0.33),
        (
            "vc_single",
            0,
            str(request.input_audio),
            -1,
            "rmvpe",
            str(request.index),
            0.0,
            0,
            0.25,
            0.33,
        ),
        ("get_vc", "", 0.33, 0.33),
    ]


def test_preloaded_vendor_namespace_is_rejected_before_import_or_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent a preloaded regular package from shadowing reviewed namespaces."""

    request = _request(_runtime_fixture(tmp_path))
    foreign = ModuleType("infer")
    foreign.__path__ = [str(tmp_path / "foreign-infer")]  # type: ignore[attr-defined]
    invoked: list[bool] = []

    def _loader(_request_value: runtime._Request) -> runtime._RvcApi:
        invoked.append(True)
        raise AssertionError("conflicting namespace reached the loader")

    monkeypatch.setitem(sys.modules, "infer", foreign)
    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._run_request(request, api_loader=_loader)

    assert captured.value.code == "source_binding_failed"
    assert invoked == []
    assert sys.modules["infer"] is foreign


def test_vendor_audit_accepts_a_reviewed_empty_package_initializer(
    tmp_path: Path,
) -> None:
    """Allow an exact zero-byte __init__.py without relaxing asset checks."""

    request = _request(_runtime_fixture(tmp_path))
    initializer = request.source_root / "infer" / "vc" / "__init__.py"
    assert initializer.stat().st_size == 0

    with runtime._runtime_scope(request):
        package = runtime.importlib.import_module("infer.vc")
        assert Path(package.__file__) == initializer
        runtime._audit_vendor_modules(request)


@pytest.mark.parametrize(
    ("device", "dml"),
    (("cpu", False), ("privateuseone:0", True)),
)
def test_non_cuda_and_directml_fallbacks_are_rejected_before_model_load(
    tmp_path: Path,
    device: str,
    dml: bool,
) -> None:
    """Fail closed when upstream selects either CPU or DirectML."""

    request = _request(_runtime_fixture(tmp_path))
    constructed: list[object] = []

    class _FallbackConfig:
        """Expose one unsupported upstream execution device."""

        def __init__(self) -> None:
            self.device = device
            self.dml = dml

    def _converter_factory(configuration: object) -> object:
        constructed.append(configuration)
        return object()

    def _loader(_request_value: runtime._Request) -> runtime._RvcApi:
        return runtime._RvcApi(_FallbackConfig, _converter_factory)

    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._run_request(request, api_loader=_loader)

    assert captured.value.code == "cuda_required"
    assert constructed == []


def test_explicit_hubert_directory_rebinds_the_verified_vendor_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override the upstream default HuBERT path before VC is constructed."""

    request = _request(_runtime_fixture(tmp_path))
    cache_probe = _CacheProbe()
    hubert_module = SimpleNamespace(
        __file__=str(request.source_root / "infer" / "hubert.py"),
        HUBERT_MODEL_PATH=request.source_root / "assets" / "hubert_base",
        hubert_audio_requires_normalization=cache_probe,
    )
    config_factory = lambda: object()
    converter_factory = lambda _configuration: object()
    modules = {
        "infer.hubert": hubert_module,
        "configs.config": SimpleNamespace(
            __file__=str(request.source_root / "configs" / "config.py"),
            Config=config_factory,
        ),
        "infer.vc.modules": SimpleNamespace(
            __file__=str(request.source_root / "infer" / "vc" / "modules.py"),
            VC=converter_factory,
        ),
        "infer.rmvpe": SimpleNamespace(
            __file__=str(request.source_root / "infer" / "rmvpe.py"),
        ),
    }

    def _import(name: str) -> object:
        return modules[name]

    monkeypatch.setattr(runtime.importlib, "import_module", _import)
    with runtime._runtime_scope(request):
        api = runtime._load_rvc_api(request)

    assert hubert_module.HUBERT_MODEL_PATH == request.hubert
    assert cache_probe.cleared is True
    assert api.config_factory is config_factory
    assert api.converter_factory is converter_factory


def test_atomic_publisher_creates_once_and_cleans_private_sibling(
    tmp_path: Path,
) -> None:
    """Expose only a complete output and never overwrite a prior destination."""

    output = tmp_path / "cover.wav"
    observed: list[Path] = []

    def _writer(path: Path, sample_rate: int, _samples: object) -> None:
        assert sample_rate == 40_000
        assert not output.exists()
        observed.append(path)
        path.write_bytes(b"RIFF-complete")

    runtime._publish_audio_atomic(output, 40_000, [0.0], writer=_writer)

    assert output.read_bytes() == b"RIFF-complete"
    assert len(observed) == 1
    assert not observed[0].exists()
    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._publish_audio_atomic(output, 40_000, [0.0], writer=_writer)
    assert captured.value.code == "output_exists"
    assert output.read_bytes() == b"RIFF-complete"


def test_pcm_writer_normalizes_integer_quantization_without_full_scale_clipping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve int16 half-scale PCM instead of interpreting it as float gain."""

    np = _require_numpy()
    output = tmp_path / "integer-pcm.wav"

    def _write(
        path: str,
        audio: object,
        sample_rate: int,
        *,
        format: str,
        subtype: str,
    ) -> None:
        assert format == "WAV" and subtype == "PCM_16"
        quantized = [
            max(-32_768, min(32_767, int(round(float(value) * 32_768))))
            for value in audio
        ]
        with wave.open(path, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(sample_rate)
            destination.writeframes(struct.pack(f"<{len(quantized)}h", *quantized))

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=_write))
    runtime._write_pcm16_wav(
        output,
        40_000,
        np.asarray([0, 16_384, -32_768], dtype=np.int16),
    )

    with wave.open(str(output), "rb") as source:
        assert source.getframerate() == 40_000
        decoded_pcm = struct.unpack("<3h", source.readframes(3))
    decoded = [value / 32_768 for value in decoded_pcm]
    assert decoded == pytest.approx([0.0, 0.5, -1.0], abs=1 / 32_768)


@pytest.mark.parametrize(
    ("values", "dtype"),
    (
        ([0.0, 1.01], "float32"),
        ([0.0, float("inf")], "float32"),
        ([0.0, float("nan")], "float64"),
    ),
)
def test_pcm_writer_rejects_unbounded_or_nonfinite_float_audio(
    tmp_path: Path,
    values: list[float],
    dtype: str,
) -> None:
    """Reject float vectors that soundfile would otherwise silently clip."""

    np = _require_numpy()
    samples = np.asarray(values, dtype=dtype)
    output = tmp_path / "invalid-float.wav"
    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._write_pcm16_wav(output, 40_000, samples)

    assert captured.value.code == "invalid_audio"
    assert not output.exists()


@pytest.mark.parametrize(
    "dtype",
    (
        "int8",
        "int32",
        "uint16",
    ),
)
def test_pcm_writer_rejects_integer_formats_other_than_int16(
    tmp_path: Path,
    dtype: str,
) -> None:
    """Keep the upstream integer PCM contract exact instead of guessing scale."""

    np = _require_numpy()
    samples = np.asarray([0, 1], dtype=dtype)
    output = tmp_path / "unsupported-pcm.wav"
    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._write_pcm16_wav(output, 40_000, samples)

    assert captured.value.code == "invalid_audio"
    assert not output.exists()


@pytest.mark.parametrize(
    ("value", "dtype"),
    (
        (0, "int16"),
        (32_767, "int16"),
        (-1.0, "float32"),
    ),
)
def test_pcm_writer_rejects_silent_or_sustained_full_scale_audio(
    tmp_path: Path,
    value: float,
    dtype: str,
) -> None:
    """Reject silent inference and long clipping that would sound electric."""

    np = _require_numpy()
    samples = np.full(40_000, value, dtype=dtype)
    output = tmp_path / "pathological.wav"
    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._write_pcm16_wav(output, 40_000, samples)

    assert captured.value.code == "invalid_audio"
    assert not output.exists()


def test_failed_writer_leaves_neither_output_nor_partial_file(tmp_path: Path) -> None:
    """Remove the adapter-owned sibling when audio encoding fails midway."""

    output = tmp_path / "cover.wav"

    def _broken_writer(path: Path, _sample_rate: int, _samples: object) -> None:
        path.write_bytes(b"partial")
        raise RuntimeError("encoder stopped")

    with pytest.raises(RuntimeError, match="encoder stopped"):
        runtime._publish_audio_atomic(
            output,
            40_000,
            [0.0],
            writer=_broken_writer,
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_unsupported_hardlink_uses_same_directory_no_clobber_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish successfully when the private job volume has no hard links."""

    output = tmp_path / "fallback.wav"

    def _unsupported_link(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EOPNOTSUPP, "hard links unavailable")

    def _writer(path: Path, _sample_rate: int, _samples: object) -> None:
        path.write_bytes(b"RIFF-fallback")

    monkeypatch.setattr(runtime.os, "link", _unsupported_link)
    runtime._publish_audio_atomic(output, 40_000, [0.0], writer=_writer)

    assert output.read_bytes() == b"RIFF-fallback"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["fallback.wav"]


def test_hardlink_fallback_preserves_a_destination_that_races_into_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never replace a competing final name while entering the fallback path."""

    output = tmp_path / "raced.wav"

    def _unsupported_after_race(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"competing-output")
        raise OSError(errno.EOPNOTSUPP, "hard links unavailable")

    def _writer(path: Path, _sample_rate: int, _samples: object) -> None:
        path.write_bytes(b"RIFF-generated")

    monkeypatch.setattr(runtime.os, "link", _unsupported_after_race)
    with pytest.raises(runtime._RvcRuntimeFailure) as captured:
        runtime._publish_audio_atomic(output, 40_000, [0.0], writer=_writer)

    assert captured.value.code == "output_exists"
    assert output.read_bytes() == b"competing-output"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["raced.wav"]


def test_main_collapses_private_paths_and_vendor_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return one stable error without printing native paths or tracebacks."""

    paths = _runtime_fixture(tmp_path)
    secret = str(paths["model"])

    def _explode(_request_value: runtime._Request) -> None:
        raise RuntimeError(f"failed at {secret}")

    monkeypatch.setattr(runtime, "_run_request", _explode)
    assert runtime.main(_arguments(paths)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "RVC_RUNTIME_ERROR:runtime_failed\n"
    assert secret not in captured.err

    assert runtime.main(["--model", secret]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "RVC_RUNTIME_ERROR:invalid_request\n"
    assert secret not in captured.err
