"""Verify exact RVC segment ownership without loading private models or CUDA."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import wave

import pytest

from scripts import song_rvc_pipeline as pipeline_adapter


def _numpy() -> Any:
    """Load the optional audio dependency only for waveform-specific checks."""

    return pytest.importorskip("numpy", reason="RVC waveform checks require NumPy")


def _prepared_wav(path: Path, frames: int, rate: int = 16_000) -> Path:
    """Write a real PCM header whose timeline is independent of fake inference."""

    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(bytes(frames * 2))
    return path


def _fixture(np: Any, *, segmented: bool = True) -> tuple[Any, Any, dict[str, Any]]:
    """Model HuBERT's actual length loss with coordinate-labelled generated PCM."""

    calls: dict[str, Any] = {"f0": [], "vc": [], "rms": []}

    class _Tensor:
        """Provide only the tensor indexing/casts needed by orchestration."""

        def __init__(self, values: Any) -> None:
            self.values = np.asarray(values)

        def unsqueeze(self, axis: int) -> Any:
            """Add one dimension without modifying the source tensor."""

            return _Tensor(np.expand_dims(self.values, axis))

        def long(self) -> Any:
            """Represent the integer speaker or coarse-pitch tensor."""

            return _Tensor(self.values.astype(np.int64))

        def float(self) -> Any:
            """Represent the float32 continuous-pitch tensor."""

            return _Tensor(self.values.astype(np.float32))

        def __getitem__(self, key: Any) -> Any:
            return _Tensor(self.values[key])

    class _Pipeline:
        """Generate indexed source-time samples with the reviewed frame geometry."""

        def __init__(self) -> None:
            self.sr = 16_000
            self.window = 160
            self.t_pad = 1_600
            self.t_pad2 = 3_200
            self.t_pad_tgt = 4_000
            self.t_query = 160
            self.t_center = 800
            self.t_max = 1_000 if segmented else 1_000_000
            self.device = "cuda:fixture"

        def pipeline(self, *_arguments: Any) -> str:
            """Expose the original instance method to verify scoped restoration."""

            return "original pipeline"

        def get_f0(self, audio: Any, frames: int, key: int, method: str) -> tuple[Any, Any]:
            """Return globally indexed F0 frames so resets and shifted slices show."""

            calls["f0"].append((audio.copy(), frames, key, method))
            return np.arange(frames, dtype=np.int32), np.arange(frames, dtype=np.float32) + 100

        def vc(
            self,
            model: Any,
            net_g: Any,
            speaker: Any,
            audio: Any,
            pitch: Any,
            pitchf: Any,
            times: Any,
            index: Any,
            index_vectors: Any,
            index_rate: float,
            version: str,
            protect: float,
        ) -> Any:
            """Return generated context with the real 20 ms HuBERT frame count."""

            start = round((float(audio[self.t_pad]) - 0.1) / 0.00001)
            frames = 2 * ((len(audio) - 80) // 320)
            values = (
                np.arange(frames * 400, dtype=np.float64)
                + pipeline_adapter._output_position(start) - self.t_pad_tgt
            ) * 0.00001
            calls["vc"].append({
                "audio": audio.copy(), "pitch": pitch.values.copy(),
                "pitchf": pitchf.values.copy(), "generated": values.copy(),
                "start": start, "model": model, "net_g": net_g,
                "index": index, "index_vectors": index_vectors,
                "index_rate": index_rate, "version": version, "protect": protect,
            })
            return values

    def _change_rms(source: Any, source_rate: int, output: Any, rate: int, mix: float) -> Any:
        calls["rms"].append((source.copy(), source_rate, output.copy(), rate, mix))
        return output

    vendor = SimpleNamespace(
        Pipeline=_Pipeline, np=np,
        torch=SimpleNamespace(
            tensor=lambda values, device: _Tensor(values),
            cuda=SimpleNamespace(is_available=lambda: False),
        ),
        signal=SimpleNamespace(filtfilt=lambda _b, _a, audio: audio.copy()),
        bh=object(), ah=object(), ttime=lambda: 1.0, change_rms=_change_rms,
    )
    return SimpleNamespace(pipeline=_Pipeline()), vendor, calls


def _convert(converter: Any, np: Any, frames: int, **overrides: Any) -> Any:
    """Invoke the same fixed positional contract as the upstream VC wrapper."""

    source = np.arange(frames, dtype=np.float64) * 0.00001 + 0.1
    options = {
        "sid": 0, "key": 0, "method": "rmvpe", "index": "private.index",
        "index_rate": 0.0, "if_f0": 1, "rate": 40_000, "resample": 0,
        "rms": 0.25, "version": "v2", "protect": 0.33,
    }
    options.update(overrides)
    return converter.pipeline.pipeline(
        "hubert", "generator", options["sid"], source, [0.0, 0.0, 0.0],
        options["key"], options["method"], options["index"], options["index_rate"],
        options["if_f0"], options["rate"], options["resample"], options["rms"],
        options["version"], options["protect"],
    )


@pytest.mark.parametrize("remainder", [0, 1, 79, 80, 159, 160, 239, 319])
@pytest.mark.parametrize("segmented", [False, True])
def test_odd_even_cores_and_fractional_tail_keep_every_absolute_sample(
    tmp_path: Path, remainder: int, segmented: bool,
) -> None:
    """Recover actual generated context without gaps, repeats, or accumulated drift."""

    np = _numpy()
    frames = 3_200 + remainder
    converter, vendor, calls = _fixture(np, segmented=segmented)
    restore = pipeline_adapter.bind_exact_timing_pipeline(
        converter, vendor, _prepared_wav(tmp_path / "input.wav", frames),
    )
    try:
        actual = _convert(converter, np, frames)
    finally:
        restore()

    count = round(frames * 2.5)
    expected_float = np.arange(count, dtype=np.float64) * 0.00001
    assert np.array_equal(calls["rms"][0][2], expected_float)
    assert np.array_equal(actual, (expected_float * 32768).astype(np.int16))
    assert len(actual) == count
    # The prior symmetric crop really loses frames in this same fixture; the
    # new path must select existing generated samples rather than fake length.
    legacy_count = sum(len(call["generated"][4_000:-4_000]) for call in calls["vc"])
    assert legacy_count < count
    if segmented:
        assert len(calls["vc"]) > 1
    else:
        assert len(calls["vc"]) == 1


def test_inference_receives_original_contexts_and_one_global_pitch_track(tmp_path: Path) -> None:
    """Keep all model inputs and F0 slices unchanged while changing output crops."""

    np = _numpy()
    frames = 3_379
    converter, vendor, calls = _fixture(np)
    original = converter.pipeline.pipeline
    other = vendor.Pipeline()
    restore = pipeline_adapter.bind_exact_timing_pipeline(
        converter, vendor, _prepared_wav(tmp_path / "input.wav", frames),
    )
    try:
        _convert(converter, np, frames, key=-12)
        assert other.pipeline() == "original pipeline"
    finally:
        restore()
        restore()
    assert converter.pipeline.pipeline == original
    assert len(calls["f0"]) == 1
    padded, frame_count, key, method = calls["f0"][0]
    assert (frame_count, key, method) == ((frames + 3_200) // 160, -12, "rmvpe")
    starts = [call["start"] for call in calls["vc"]]
    # An increasing signal's first quiet point is the left edge of each search
    # window. These are the vendor's 10 ms cuts, including odd frame lengths.
    assert starts == [0, 640, 1_440, 2_240, 3_040]
    for index, call in enumerate(calls["vc"]):
        start = starts[index]
        final = index == len(starts) - 1
        end = frames if final else starts[index + 1]
        assert np.array_equal(call["audio"], padded[start:None if final else end + 3_360])
        pitch_end = frame_count if final else (end + 3_200) // 160
        expected_pitch = np.arange(start // 160, pitch_end)
        assert np.array_equal(call["pitch"][0], expected_pitch)
        assert np.array_equal(call["pitchf"][0], expected_pitch + 100)
        assert call["index"] is None and call["index_vectors"] is None
        assert (call["model"], call["net_g"], call["index_rate"], call["version"], call["protect"]) == (
            "hubert", "generator", 0.0, "v2", 0.33,
        )
    assert calls["rms"][0][1] == 16_000
    assert calls["rms"][0][3:] == (40_000, 0.25)


def test_final_length_uses_original_wav_not_resampler_rounding(tmp_path: Path) -> None:
    """Keep the exact prepared 44.1 kHz duration when the 16 kHz loader rounds up."""

    np = _numpy()
    source_frames = 9_001
    decoded_frames = (source_frames * 16_000 + 44_099) // 44_100
    converter, vendor, calls = _fixture(np)
    restore = pipeline_adapter.bind_exact_timing_pipeline(
        converter, vendor, _prepared_wav(tmp_path / "input.wav", source_frames, 44_100),
    )
    try:
        actual = _convert(converter, np, decoded_frames)
    finally:
        restore()
    expected = round(source_frames * 40_000 / 44_100)
    assert expected != round(decoded_frames * 2.5)
    assert len(actual) == expected
    assert np.array_equal(calls["rms"][0][2], np.arange(expected) * 0.00001)


def test_missing_generated_context_fails_instead_of_filling_samples(tmp_path: Path) -> None:
    """Reject a truncated generator even when it has a nonempty old-style crop."""

    np = _numpy()
    converter, vendor, calls = _fixture(np)
    original_vc = converter.pipeline.vc

    def _truncated(*arguments: Any) -> Any:
        return original_vc(*arguments)[:4_100]

    converter.pipeline.vc = _truncated
    restore = pipeline_adapter.bind_exact_timing_pipeline(
        converter, vendor, _prepared_wav(tmp_path / "input.wav", 3_200),
    )
    try:
        with pytest.raises(pipeline_adapter._PipelineContractError, match="complete source core"):
            _convert(converter, np, 3_200)
    finally:
        restore()
    assert calls["rms"] == []
    assert converter.pipeline.pipeline() == "original pipeline"


def test_header_decode_mismatch_fails_before_model_calls(tmp_path: Path) -> None:
    """Do not turn a truncated decoded input into apparently complete audio."""

    np = _numpy()
    converter, vendor, calls = _fixture(np)
    restore = pipeline_adapter.bind_exact_timing_pipeline(
        converter, vendor, _prepared_wav(tmp_path / "input.wav", 3_200),
    )
    try:
        with pytest.raises(pipeline_adapter._PipelineContractError, match="prepared timeline"):
            _convert(converter, np, 3_180)
    finally:
        restore()
    assert calls["f0"] == [] and calls["vc"] == []


@pytest.mark.parametrize("override", [
    {"index_rate": 0.5}, {"method": "pm"}, {"rate": 48_000},
    {"rms": 0.0}, {"resample": 44_100}, {"version": "v1"}, {"key": 13},
])
def test_binding_rejects_profiles_outside_the_reviewed_adapter(
    tmp_path: Path, override: dict[str, Any],
) -> None:
    """Keep the maintained orchestration closed to the one authenticated profile."""

    np = _numpy()
    converter, vendor, calls = _fixture(np)
    restore = pipeline_adapter.bind_exact_timing_pipeline(
        converter, vendor, _prepared_wav(tmp_path / "input.wav", 3_200),
    )
    try:
        with pytest.raises(pipeline_adapter._PipelineContractError, match="inference profile"):
            _convert(converter, np, 3_200, **override)
    finally:
        restore()
    assert calls["vc"] == []
