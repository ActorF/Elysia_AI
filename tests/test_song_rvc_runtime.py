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
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", ":16:8")
    calls: list[tuple[object, ...]] = []
    published: list[tuple[Path, int, object]] = []

    class _FakeConfig:
        """Model a CUDA configuration while checking the isolated scope."""

        def __init__(self) -> None:
            calls.append(("config",))
            assert Path.cwd() == request.source_root
            assert sys.path[0] == str(request.source_root)
            assert sys.argv == [str(Path(runtime.__file__).resolve())]
            assert os.environ["HF_HUB_OFFLINE"] == "1"
            assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
            assert os.environ["RVC_CUDA_GRAPH"] == "0"
            assert os.environ["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"
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
        calls.append(("load_api",))
        return runtime._RvcApi(_FakeConfig, _FakeConverter)

    def _seed() -> None:
        calls.append(("seed", runtime._INFERENCE_SEED))

    def _publisher(path: Path, sample_rate: int, samples: object) -> None:
        published.append((path, sample_rate, samples))

    def _repairer(
        input_audio: Path,
        sample_rate: int,
        samples: object,
        key_shift: int,
    ) -> object:
        """Record that validated inference passes through the transition guard."""

        calls.append(
            (
                "repair",
                input_audio,
                sample_rate,
                samples,
                key_shift,
            )
        )
        return [0.0, 0.05, -0.05]

    runtime._run_request(
        request,
        api_loader=_loader,
        publisher=_publisher,
        seed_initializer=_seed,
        artifact_repairer=_repairer,
    )

    assert Path.cwd() == original_directory
    assert sys.argv == original_arguments
    assert socket.getaddrinfo is original_getaddrinfo
    assert os.environ["RVC_CUDA_GRAPH"] == "1"
    assert os.environ["CUBLAS_WORKSPACE_CONFIG"] == ":16:8"
    assert published == [(request.output_audio, 40_000, [0.0, 0.05, -0.05])]
    assert calls == [
        ("seed", 114_514),
        ("load_api",),
        ("config",),
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
        (
            "repair",
            request.input_audio,
            40_000,
            [0.0, 0.1, -0.1],
            -1,
        ),
    ]


def test_torch_seed_resets_cpu_and_every_cuda_generator_for_each_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply seed 114514 and stable cuDNN selection on every invocation."""

    calls: list[tuple[object, ...]] = []
    cudnn = SimpleNamespace(benchmark=True, deterministic=False)
    torch_module = SimpleNamespace(
        manual_seed=lambda seed: calls.append(("manual_seed", seed)),
        cuda=SimpleNamespace(
            manual_seed_all=lambda seed: calls.append(
                ("cuda_manual_seed_all", seed)
            )
        ),
        backends=SimpleNamespace(cudnn=cudnn),
    )

    def _import(name: str) -> object:
        assert name == "torch"
        calls.append(("import", name))
        return torch_module

    monkeypatch.setattr(runtime.importlib, "import_module", _import)

    runtime._seed_torch_inference()
    runtime._seed_torch_inference()

    one_job = [
        ("import", "torch"),
        ("manual_seed", 114_514),
        ("cuda_manual_seed_all", 114_514),
    ]
    assert calls == one_job + one_job
    assert cudnn.benchmark is False
    assert cudnn.deterministic is True


def test_transition_guard_keeps_only_persistent_evidence() -> None:
    """Bridge tiny holes but reject isolated evidence below persistence gates."""

    np = _require_numpy()
    flags = np.zeros(400, dtype=bool)
    flags[20:31] = True
    flags[200:206] = True
    flags[208:214] = True
    flags[380:385] = True

    selected = runtime._persistent_artifact_frames(
        flags,
        np_module=np,
    )
    expected = np.zeros(400, dtype=bool)
    expected[20:31] = True
    expected[200:214] = True

    assert selected == expected.tolist()


def test_transition_guard_requires_joint_unvoiced_and_periodicity_evidence() -> None:
    """Reject legal tones and weak estimates while admitting one clear defect."""

    np = _require_numpy()
    selected = runtime._candidate_artifact_frames(
        source_f0=np.asarray([400.0, 400.0, 400.0, np.nan]),
        source_voiced_flag=np.asarray([True, False, False, False]),
        source_probability=np.asarray([0.99, 0.10, 0.10, 0.10]),
        source_rms_db=np.asarray([-20.0, -20.0, -20.0, -20.0]),
        output_f0=np.asarray([400.0, 400.0, 800.0, 400.0]),
        output_voiced_flag=np.asarray([True, True, True, True]),
        output_probability=np.asarray([0.99, 0.99, 0.99, 0.99]),
        output_rms_db=np.asarray([-20.0, -20.0, -20.0, -20.0]),
        source_periodicity=np.asarray([0.60, 0.60, 0.60, 0.60]),
        output_periodicity=np.asarray([0.99, 0.99, 0.99, 0.99]),
        key_shift=0,
        np_module=np,
    )

    assert selected.tolist() == [False, True, False, False]


def test_voicing_track_pins_the_calibrated_fast_pitch_grid() -> None:
    """Keep the validated twenty-cent pYIN grid and analysis frame geometry."""

    np = _require_numpy()
    arguments: dict[str, object] = {}

    def _pyin(_audio: object, **keywords: object) -> tuple[object, object, object]:
        arguments.update(keywords)
        return (
            np.asarray([400.0, 400.0]),
            np.asarray([True, True]),
            np.asarray([0.99, 0.99]),
        )

    fake_librosa = SimpleNamespace(
        pyin=_pyin,
        feature=SimpleNamespace(
            rms=lambda **_keywords: np.asarray([[0.25, 0.25]])
        ),
    )

    runtime._voicing_track(
        np.zeros(runtime._ARTIFACT_FRAME_LENGTH, dtype=np.float32),
        np_module=np,
        librosa_module=fake_librosa,
    )

    assert arguments["sr"] == 44_100
    assert arguments["frame_length"] == 2_048
    assert arguments["hop_length"] == 256
    assert arguments["resolution"] == 0.20
    assert arguments["fill_na"] is None


def test_analysis_resampler_chunks_without_changing_polyphase_samples() -> None:
    """Keep long-song block seams bit-identical to monolithic polyphase."""

    np = _require_numpy()
    signal = pytest.importorskip(
        "scipy.signal",
        reason="Analysis resampling requires the private RVC SciPy runtime",
    )
    sample_count = 800_137
    audio = np.random.default_rng(114_514).normal(
        0.0,
        0.1,
        sample_count,
    ).astype(np.float32)

    expected = signal.resample_poly(audio, 441, 400).astype(
        np.float32,
        copy=False,
    )
    actual = runtime._resample_artifact_analysis(
        audio,
        source_sample_rate=40_000,
        np_module=np,
        signal_module=signal,
    )

    assert np.array_equal(actual, expected)
    assert len(actual) == (sample_count * 441 + 399) // 400
    for seam in (441_000, 882_000):
        assert np.array_equal(actual[seam - 8 : seam + 8], expected[seam - 8 : seam + 8])


def test_voicing_track_stitches_bounded_context_segments_on_one_frame_grid() -> None:
    """Bound long-song pYIN state without shifting or duplicating frames."""

    np = _require_numpy()
    sample_count = (
        round(25.0 * runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE) + 37
    )
    audio = np.arange(sample_count, dtype=np.float32)
    analyzed_spans: list[tuple[int, int]] = []

    def _frame_indexes(segment: object) -> object:
        values = np.asarray(segment, dtype=np.float32)
        start_frame = int(
            round(float(values[0]) / runtime._ARTIFACT_HOP_LENGTH)
        )
        count = len(values) // runtime._ARTIFACT_HOP_LENGTH + 1
        return np.arange(start_frame, start_frame + count, dtype=np.float64)

    def _pyin(segment: object, **_keywords: object) -> tuple[object, object, object]:
        values = np.asarray(segment, dtype=np.float32)
        analyzed_spans.append((int(values[0]), len(values)))
        frames = _frame_indexes(values)
        return (
            frames,
            np.ones(len(frames), dtype=bool),
            np.full(len(frames), 0.99, dtype=np.float32),
        )

    def _rms(**keywords: object) -> object:
        frames = _frame_indexes(keywords["y"])
        return np.full((1, len(frames)), 0.25, dtype=np.float32)

    f0, voiced, probability, rms_db = runtime._voicing_track(
        audio,
        np_module=np,
        librosa_module=SimpleNamespace(
            pyin=_pyin,
            feature=SimpleNamespace(rms=_rms),
        ),
    )

    total_frames = sample_count // runtime._ARTIFACT_HOP_LENGTH + 1
    core_frames = round(
        runtime._ARTIFACT_PYIN_CORE_SECONDS
        * runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE
        / runtime._ARTIFACT_HOP_LENGTH
    )
    expected_segments = (total_frames + core_frames - 1) // core_frames
    assert len(analyzed_spans) == expected_segments
    assert f0.tolist() == np.arange(total_frames, dtype=np.float64).tolist()
    assert bool(np.all(voiced))
    assert bool(np.all(probability == np.float32(0.99)))
    assert bool(np.allclose(rms_db, 20.0 * np.log10(0.25)))
    context_frames = round(
        runtime._ARTIFACT_PYIN_CONTEXT_SECONDS
        * runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE
        / runtime._ARTIFACT_HOP_LENGTH
    )
    maximum_frames = core_frames + 2 * context_frames
    assert max(length for _start, length in analyzed_spans) <= (
        maximum_frames * runtime._ARTIFACT_HOP_LENGTH
    )


def test_yin_track_stitches_the_quarter_rate_prefilter_grid() -> None:
    """Keep fast YIN bounded and exactly aligned with the fine frame grid."""

    np = _require_numpy()
    total_frames = 2 * runtime._ARTIFACT_YIN_CORE_FRAMES + 17
    sample_count = (
        (total_frames - 1) * runtime._ARTIFACT_YIN_HOP_LENGTH + 19
    )
    audio = np.arange(sample_count, dtype=np.float32)
    calls: list[dict[str, object]] = []

    def _yin(segment: object, **keywords: object) -> object:
        values = np.asarray(segment, dtype=np.float32)
        calls.append({"start": int(values[0]), "length": len(values), **keywords})
        start_frame = int(values[0]) // runtime._ARTIFACT_YIN_HOP_LENGTH
        frame_count = len(values) // runtime._ARTIFACT_YIN_HOP_LENGTH + 1
        return np.arange(
            start_frame,
            start_frame + frame_count,
            dtype=np.float64,
        )

    result = runtime._yin_track(
        audio,
        np_module=np,
        librosa_module=SimpleNamespace(yin=_yin),
    )

    assert result.tolist() == np.arange(total_frames, dtype=np.float64).tolist()
    assert len(calls) == 3
    assert int(calls[-1]["start"]) + int(calls[-1]["length"]) == sample_count
    assert all(call["sr"] == 11_025 for call in calls)
    assert all(call["frame_length"] == 512 for call in calls)
    assert all(call["hop_length"] == 64 for call in calls)
    assert all(call["trough_threshold"] == 0.20 for call in calls)
    maximum_frames = (
        runtime._ARTIFACT_YIN_CORE_FRAMES
        + 2 * runtime._ARTIFACT_YIN_CONTEXT_FRAMES
    )
    assert max(int(call["length"]) for call in calls) <= (
        (maximum_frames - 1) * runtime._ARTIFACT_YIN_HOP_LENGTH + 1
    )


def test_coarse_prefilter_dilates_only_joint_artifact_evidence() -> None:
    """Use permissive joint gates and add the calibrated search radius."""

    np = _require_numpy()
    frame_count = 220
    source_f0 = np.full(frame_count, np.nan, dtype=np.float64)
    output_f0 = np.full(frame_count, np.nan, dtype=np.float64)
    output_rms_db = np.full(frame_count, -120.0, dtype=np.float32)
    source_periodicity = np.zeros(frame_count, dtype=np.float32)
    output_periodicity = np.zeros(frame_count, dtype=np.float32)
    ratio = 2.0 ** (2.0 / 12.0)

    source_f0[100] = 400.0
    output_f0[100] = 400.0 * ratio
    output_rms_db[100] = -20.0
    source_periodicity[100] = 0.70
    output_periodicity[100] = 0.95

    # Each separated decoy fails one required gate and must not open a window.
    for index in (20, 55, 145, 190):
        source_f0[index] = 400.0
        output_f0[index] = 400.0 * ratio
        output_rms_db[index] = -20.0
        source_periodicity[index] = 0.70
        output_periodicity[index] = 0.95
    output_f0[20] = 800.0
    output_rms_db[55] = -40.0
    output_periodicity[145] = 0.90
    source_periodicity[190] = 0.85

    interest = runtime._coarse_artifact_interest(
        source_f0=source_f0,
        output_f0=output_f0,
        output_rms_db=output_rms_db,
        source_periodicity=source_periodicity,
        output_periodicity=output_periodicity,
        key_shift=2,
        np_module=np,
    )

    radius = int(
        np.ceil(
            runtime._ARTIFACT_INTEREST_RADIUS_SECONDS
            * runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE
            / runtime._ARTIFACT_HOP_LENGTH
        )
    )
    expected = np.zeros(frame_count, dtype=bool)
    expected[100 - radius : 100 + radius + 1] = True
    assert interest.tolist() == expected.tolist()


def test_coarse_prefilter_skips_frames_that_cannot_reach_acf_gates() -> None:
    """Avoid autocorrelation for silence, absent pitch, and pitch mismatch."""

    np = _require_numpy()
    ratio = 2.0 ** (2.0 / 12.0)
    eligible = runtime._coarse_artifact_eligibility(
        source_f0=np.asarray([400.0, 400.0, np.nan, 400.0]),
        output_f0=np.asarray(
            [400.0 * ratio, 800.0, 400.0 * ratio, 400.0 * ratio]
        ),
        output_rms_db=np.asarray([-20.0, -20.0, -20.0, -40.0]),
        key_shift=2,
        np_module=np,
    )

    assert eligible.tolist() == [True, False, False, False]


def test_fine_analysis_windows_merge_context_and_fail_open_over_budget() -> None:
    """Merge nearby searches while refusing song-wide expensive pYIN work."""

    np = _require_numpy()
    interest = np.zeros(1_000, dtype=bool)
    interest[100:110] = True
    interest[175:180] = True
    interest[500:510] = True
    context = int(
        np.ceil(
            runtime._ARTIFACT_FINE_CONTEXT_SECONDS
            * runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE
            / runtime._ARTIFACT_HOP_LENGTH
        )
    )

    windows = runtime._fine_analysis_windows(interest, np_module=np)

    assert windows == [
        (100 - context, 180 + context),
        (500 - context, 510 + context),
    ]
    widespread = np.ones(10_000, dtype=bool)
    assert runtime._fine_analysis_windows(widespread, np_module=np) is None


def test_fine_analysis_budget_accepts_its_boundary_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the inclusive budget boundary without context obscuring its math."""

    np = _require_numpy()
    monkeypatch.setattr(runtime, "_ARTIFACT_FINE_CONTEXT_SECONDS", 0.0)
    monkeypatch.setattr(runtime, "_ARTIFACT_FINE_MINIMUM_BUDGET_SECONDS", 0.0)
    monkeypatch.setattr(runtime, "_ARTIFACT_FINE_MAXIMUM_BUDGET_SECONDS", 1e9)
    monkeypatch.setattr(runtime, "_ARTIFACT_FINE_BUDGET_FRACTION", 0.5)
    at_budget = np.zeros(100, dtype=bool)
    at_budget[:50] = True
    over_budget = at_budget.copy()
    over_budget[50] = True

    assert runtime._fine_analysis_windows(at_budget, np_module=np) == [(0, 50)]
    assert runtime._fine_analysis_windows(over_budget, np_module=np) is None


def test_fine_analysis_production_budget_preserves_calibration_headroom() -> None:
    """Pin the reviewed regional workload and the long-song hard ceiling."""

    assert runtime._ARTIFACT_FINE_MINIMUM_BUDGET_SECONDS == 12.0
    assert runtime._ARTIFACT_FINE_MAXIMUM_BUDGET_SECONDS == 30.0
    assert runtime._ARTIFACT_FINE_BUDGET_FRACTION == 0.15
    known_budget = min(
        runtime._ARTIFACT_FINE_MAXIMUM_BUDGET_SECONDS,
        max(
            runtime._ARTIFACT_FINE_MINIMUM_BUDGET_SECONDS,
            81.72 * runtime._ARTIFACT_FINE_BUDGET_FRACTION,
        ),
    )
    long_song_budget = min(
        runtime._ARTIFACT_FINE_MAXIMUM_BUDGET_SECONDS,
        max(
            runtime._ARTIFACT_FINE_MINIMUM_BUDGET_SECONDS,
            200.0 * runtime._ARTIFACT_FINE_BUDGET_FRACTION,
        ),
    )

    assert known_budget == pytest.approx(12.258)
    assert known_budget > 8.7133
    assert long_song_budget == 30.0


def test_local_voicing_tracks_map_windows_back_to_global_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Place local pYIN results exactly without treating context as evidence."""

    np = _require_numpy()
    audio = np.arange(
        20 * runtime._ARTIFACT_HOP_LENGTH + 37,
        dtype=np.float32,
    )
    spans: list[tuple[int, int, int]] = []

    def _track(
        segment: object,
        *,
        np_module: object,
        librosa_module: object,
    ) -> tuple[object, object, object, object]:
        del np_module, librosa_module
        values = np.asarray(segment, dtype=np.float32)
        start = int(values[0]) // runtime._ARTIFACT_HOP_LENGTH
        count = len(values) // runtime._ARTIFACT_HOP_LENGTH + 1
        spans.append((start, count, len(values)))
        return (
            np.arange(start, start + count, dtype=np.float64),
            np.ones(count, dtype=bool),
            np.full(count, 0.99, dtype=np.float32),
            np.full(count, -12.0, dtype=np.float32),
        )

    monkeypatch.setattr(runtime, "_voicing_track", _track)
    f0, voiced, probability, rms_db = runtime._voicing_tracks_for_windows(
        audio,
        [(3, 6), (19, 21)],
        total_frames=21,
        np_module=np,
        librosa_module=object(),
    )

    assert spans == [
        (3, 3, 2 * runtime._ARTIFACT_HOP_LENGTH + 1),
        (19, 2, runtime._ARTIFACT_HOP_LENGTH + 37),
    ]
    assert f0[3:6].tolist() == [3.0, 4.0, 5.0]
    assert f0[19:21].tolist() == [19.0, 20.0]
    outside = np.ones(21, dtype=bool)
    outside[3:6] = False
    outside[19:21] = False
    assert bool(np.all(np.isnan(f0[outside])))
    assert bool(np.all(~voiced[outside]))
    assert bool(np.all(probability[outside] == 0.0))
    assert bool(np.all(rms_db[outside] == -120.0))


def test_periodicity_comparison_separates_tone_from_aperiodic_transition() -> None:
    """Measure a stable oscillator strongly without mislabeling seeded noise."""

    np = _require_numpy()
    sample_count = runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE * 2
    positions = np.arange(sample_count, dtype=np.float32)
    tone = np.sin(
        2.0
        * np.pi
        * 400.0
        * positions
        / runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE
    ).astype(np.float32)
    noise = np.random.default_rng(114_514).normal(
        0.0,
        0.25,
        sample_count,
    ).astype(np.float32)
    frame_count = sample_count // runtime._ARTIFACT_HOP_LENGTH + 1
    lags = np.full(
        frame_count,
        round(runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE / 400.0),
        dtype=np.int32,
    )

    tone_periodicity = runtime._periodicity_at_lags(
        tone,
        lags,
        frame_count=frame_count,
        np_module=np,
    )
    noise_periodicity = runtime._periodicity_at_lags(
        noise,
        lags,
        frame_count=frame_count,
        np_module=np,
    )

    interior = slice(4, -4)
    assert float(np.median(tone_periodicity[interior])) > 0.98
    assert abs(float(np.median(noise_periodicity[interior]))) < 0.10


def test_periodicity_skips_unassigned_regional_pitch_rows() -> None:
    """Keep sparse regional ACF identical without scanning zero-lag frames."""

    np = _require_numpy()
    sample_count = runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE * 7
    positions = np.arange(sample_count, dtype=np.float32)
    tone = np.sin(
        2.0
        * np.pi
        * 400.0
        * positions
        / runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE
    ).astype(np.float32)
    frame_count = sample_count // runtime._ARTIFACT_HOP_LENGTH + 1
    first_lag = round(runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE / 400.0)
    second_lag = round(runtime._ARTIFACT_ANALYSIS_SAMPLE_RATE / 500.0)
    dense_lags = np.where(
        np.arange(frame_count) % 2 == 0,
        first_lag,
        second_lag,
    ).astype(np.int32)
    sparse_lags = np.zeros(frame_count, dtype=np.int32)
    selected = np.asarray([0, 511, 512, 513, 1_023, frame_count - 1])
    sparse_lags[selected] = dense_lags[selected]

    dense = runtime._periodicity_at_lags(
        tone,
        dense_lags,
        frame_count=frame_count,
        np_module=np,
    )
    sparse = runtime._periodicity_at_lags(
        tone,
        sparse_lags,
        frame_count=frame_count,
        np_module=np,
    )

    assert np.array_equal(sparse[selected], dense[selected])
    outside = np.ones(frame_count, dtype=bool)
    outside[selected] = False
    assert bool(np.all(sparse[outside] == 0.0))


def test_transition_guard_derives_shifted_lag_before_rounding() -> None:
    """Prevent quantized output lags from moving source ACF decisions."""

    np = _require_numpy()
    output_lags, source_lags = runtime._pitch_lag_tracks(
        np.asarray([703.0, np.nan, 0.0]),
        key_shift=2,
        np_module=np,
    )

    assert output_lags.tolist() == [63, 0, 0]
    assert source_lags.tolist() == [70, 0, 0]
    assert round(output_lags[0] * (2.0 ** (2.0 / 12.0))) == 71


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
        runtime._run_request(
            request,
            api_loader=_loader,
            seed_initializer=lambda: None,
        )

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
        runtime._run_request(
            request,
            api_loader=_loader,
            seed_initializer=lambda: None,
        )

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
