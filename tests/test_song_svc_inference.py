"""Check source-conversion timeline, offline loading, and raw-output contracts."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import os
import random
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import song_svc_inference as svc


@pytest.mark.parametrize("length", [24_000, 24_000 * 45 + 7, 24_000 * 248 + 333])
def test_context_partition_preserves_every_source_sample_and_zero_pitch(length, monkeypatch):
    """Echo contextual inference must reproduce the full timeline, including zero F0."""
    audio = (np.arange(length, dtype=np.float32) % 997) / 1000
    pitch = np.zeros((length + 479) // 480, dtype=np.float64)
    seen = []

    def _echo(_model, window, contour, _prompt, _prompt_pitch, key, cfg):
        assert key == 12 and cfg == 3
        assert np.all(contour == 0)
        seen.append(len(window))
        return window.copy(), (-len(window)) % 480

    monkeypatch.setattr(svc, "_render_core", _echo)
    cores = svc._plan(audio)
    generated, receipts = svc._assemble(None, audio, pitch, None, None, cores, 12, 3)
    np.testing.assert_array_equal(generated, audio)
    assert max(seen) < 30 * 24_000
    assert len(receipts) == len(cores)
    assert sum(core.end - core.start for core in cores) == length


def test_plan_chooses_quiet_grid_cut_and_rejects_corrupted_ownership():
    """A natural gap should attract a cut; a hole or overlap must fail closed."""
    audio = np.ones(50 * 24_000, dtype=np.float32)
    audio[17 * 24_000:18 * 24_000] = 0
    cores = svc._plan(audio)
    assert 17.05 <= cores[0].end / 24_000 <= 17.95
    for offset in (-480, 480):
        corrupt = [cores[0], replace(cores[1], start=cores[1].start + offset), *cores[2:]]
        with pytest.raises(ValueError, match="hole, overlap"):
            svc._validate_plan(corrupt, len(audio))


@pytest.mark.parametrize("kind", ["short", "excess", "modified", "nonfinite"])
def test_raw_audit_rejects_hidden_padding_or_changed_pcm(kind):
    """Upstream tail repair cannot make defective raw generation acceptable."""
    raw = np.linspace(-.2, .2, 960, dtype=np.float32)
    generated = raw[:801].copy()
    if kind == "short":
        raw = raw[:800]
    elif kind == "excess":
        raw = np.pad(raw, (0, 480))
    elif kind == "modified":
        generated[-1] = 0
    else:
        raw[20] = np.nan
    with pytest.raises(ValueError):
        svc._audit_raw(raw, generated, 801)


def test_raw_audit_allows_only_the_partial_frame_tail():
    """A non-frame-aligned final source permits a strict prefix crop, not padding."""
    raw = np.zeros(960, dtype=np.float32)
    assert svc._audit_raw(raw, raw[:801], 801) == 159


@pytest.mark.parametrize("delta", [-1, 0, 1, 2])
def test_resampling_rounds_length_without_padding(tmp_path, monkeypatch, delta):
    """The librosa ceil convention permits at most one extra destination sample."""
    source = np.zeros(44_101, dtype=np.float32)
    expected = round(len(source) * 24_000 / 44_100)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(read=lambda *a, **k: (source, 44_100)))
    monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace(
        resample=lambda *a, **k: np.zeros(expected + delta, dtype=np.float32)))
    if delta not in (0, 1):
        with pytest.raises(ValueError, match="unexpected or short"):
            svc._read_source(tmp_path / "source.wav")
    else:
        audio, receipt = svc._read_source(tmp_path / "source.wav")
        assert len(audio) == expected
        assert receipt["resample_tail_cropped_samples"] == delta


def test_constant_gain_preserves_quiet_to_loud_dynamics():
    """Output headroom must not introduce compression or independent segment gains."""
    audio = np.array([0, .01, -.02, 1, -2], dtype=np.float32)
    output, gain = svc._attenuate(audio)
    assert gain == pytest.approx(.49)
    np.testing.assert_allclose(output, audio * .49)
    assert float(np.max(np.abs(output))) <= .980001
    quiet = audio / 10
    np.testing.assert_array_equal(svc._attenuate(quiet)[0], quiet)


def test_opposite_phase_join_reduces_click_without_mixing():
    """A near-zero ownership cut reduces a large phase jump without cancellation."""
    positions = np.arange(8_000)
    previous = (.2 * np.sin(2 * np.pi * 237 * positions / 24_000 + .7)).astype(np.float32)
    current = -previous
    nominal = 4_000
    actual = svc._select_join(previous, current, nominal, 0, 0)
    assert abs(actual - nominal) <= 1_200
    old_jump = abs(current[nominal] - previous[nominal - 1])
    new_jump = abs(current[actual] - previous[actual - 1])
    assert new_jump < old_jump * .05
    assembled = np.concatenate((previous[:actual], current[actual:]))
    # Ownership alone preserves every original magnitude; a crossfade between
    # these opposite signals would instead produce a level dip near the join.
    np.testing.assert_array_equal(np.abs(assembled), np.abs(previous))


def test_transients_remain_at_exact_sample_positions_when_predictions_agree(monkeypatch):
    """Moving a join cannot duplicate, omit, stretch, or shift consonant-like pulses."""
    audio = np.zeros(40 * 24_000 + 7, dtype=np.float32)
    centers = [18 * 24_000 - 900, 18 * 24_000, 18 * 24_000 + 900]
    for position in centers:
        audio[position:position + 3] = [.3, -.9, .5]
    cores = [svc._Core(0, 18 * 24_000, 0, 20 * 24_000),
             svc._Core(18 * 24_000, len(audio), 16 * 24_000, len(audio))]
    monkeypatch.setattr(svc, "_render_core", lambda model, window, *args: (window.copy(), 0))
    pitch = np.zeros((len(audio) + 479) // 480)
    result, records = svc._assemble(None, audio, pitch, None, None, cores, 0, 1)
    np.testing.assert_array_equal(result, audio)
    assert records[0]["actual_end"] == records[1]["actual_start"]
    assert records[1]["actual_end"] == len(audio)
    assert records[1]["nominal_start"] == 18 * 24_000


def test_silent_join_tie_preserves_nominal_boundary():
    """Equally compatible silent positions prefer the planned cut deterministically."""
    silence = np.zeros(8_000, dtype=np.float32)
    assert svc._select_join(silence, silence, 4_000, 0, 0) == 4_000


def test_offline_scope_blocks_connections_and_restores_process_state(tmp_path, monkeypatch):
    """Offline patches remain bounded to the leased call, including failure paths."""
    monkeypatch.setenv("HTTPS_PROXY", "http://unusable.example")
    previous = socket.create_connection, socket.socket.connect, socket.socket.connect_ex
    with pytest.raises(ValueError, match="intentional"):
        with svc._offline_scope(tmp_path, tmp_path):
            assert "HTTPS_PROXY" not in os.environ
            assert os.environ["HF_HUB_OFFLINE"] == "1"
            with pytest.raises(RuntimeError, match="disabled"):
                socket.create_connection(("example.com", 443))
            raise ValueError("intentional")
    assert (socket.create_connection, socket.socket.connect, socket.socket.connect_ex) == previous
    assert os.environ["HTTPS_PROXY"] == "http://unusable.example"


def test_native_pitch_uses_complete_duration_and_releases_extractor(tmp_path, monkeypatch):
    """Native zeros and full frame coverage survive the safe, temporary-file extractor."""
    audio = np.zeros(45 * 24_000 + 7, dtype=np.float32)
    native = np.zeros((len(audio) + 479) // 480, dtype=np.float64)
    native[10:20] = 440
    events = []
    torch = SimpleNamespace(load=lambda *a, **k: events.append(k),
                            inference_mode=nullcontext,
                            cuda=SimpleNamespace(empty_cache=lambda: events.append("free")))
    original_load = torch.load

    class _Extractor:
        """Expose only the native extractor operations needed by production."""

        def __init__(self, path, **kwargs):
            assert kwargs["max_duration"] == 47
            assert kwargs["target_sr"] == 24_000 and kwargs["hop_size"] == 480
            torch.load(path)

        def process(self, path):
            """Return an unchanged complete contour from the temporary source."""
            assert Path(path).exists()
            return native

    module = SimpleNamespace(F0Extractor=_Extractor)
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
    monkeypatch.setattr(svc.importlib.util, "spec_from_file_location", lambda *a: spec)
    monkeypatch.setattr(svc.importlib.util, "module_from_spec", lambda spec: module)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=lambda *a, **k: None))
    result = svc._extract_pitch(audio, tmp_path, tmp_path / "rmvpe.pt", tmp_path)
    np.testing.assert_array_equal(result, native)
    assert events == [{"weights_only": True}, "free"]
    assert torch.load is original_load
    assert not (tmp_path / "pitch-source-24k.wav").exists()


@pytest.mark.parametrize("reference,key", [("unknown", 0), ("fairy", True), ("elysia", 13)])
def test_request_rejects_unreviewed_controls(tmp_path, reference, key):
    """Request fields cannot select arbitrary references or unbounded key changes."""
    with pytest.raises(ValueError):
        svc.SvcInferenceRequest(tmp_path, tmp_path, tmp_path / "vocal.wav", tmp_path, reference, key)


@pytest.mark.parametrize("reference,cfg", [("fairy", 1.0), ("elysia", 3.0)])
def test_orchestration_uses_selected_preset_and_pcm24_output(tmp_path, monkeypatch, reference, cfg):
    """A leased run performs repair before model load and writes bounded PCM24 once."""
    from scripts import song_svc_assets, song_svc_pitch

    audio = np.linspace(-2, 2, 24_000, dtype=np.float32)
    native = np.zeros(50, dtype=np.float64)
    events = []
    progress = []
    writes = []

    class _Tensor:
        """Stand in for device transfers without importing or allocating CUDA."""

        def unsqueeze(self, axis):
            """Keep the test token for the batch-dimension operation."""
            return self

        def to(self, device):
            """Keep the test token for the isolated CUDA transfer operation."""
            return self

        def float(self):
            """Keep the test token for conversion to float32."""
            return self

    def _repair(source, contour):
        events.append("repair")
        assert contour is not native
        return contour

    def _assemble(model, source, pitch, prompt, prompt_pitch, cores, key, selected_cfg, **options):
        assert key == 12 and selected_cfg == cfg
        events.append("render")
        for percent in (64, 65, 65, 70, 87):
            options["progress"](percent)
        return source.copy(), []

    def _write(stream, output, rate, **kwargs):
        assert rate == 24_000 and len(output) == len(audio)
        assert float(np.max(np.abs(output))) <= .980001
        writes.append(kwargs)
        stream.write(b"test-pcm24")

    monkeypatch.setattr(song_svc_assets, "verify_svc_assets", lambda root: {
        "rmvpe": tmp_path / "rmvpe.pt", f"reference_{reference}": tmp_path / "reference.wav",
        f"reference_f0_{reference}": tmp_path / "reference.npy"})
    monkeypatch.setattr(song_svc_pitch, "repair_pitch", _repair)
    monkeypatch.setattr(svc, "_read_source", lambda path: (audio.copy(), {"samples_24k": len(audio)}))
    monkeypatch.setattr(svc, "_extract_pitch", lambda *a: native)
    monkeypatch.setattr(svc, "_load_model", lambda *a: events.append("load"))
    monkeypatch.setattr(svc, "_assemble", _assemble)
    monkeypatch.setattr(np, "load", lambda *a, **k: native)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(
        read=lambda *a, **k: (np.zeros_like(audio), 24_000), write=_write))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(from_numpy=lambda array: _Tensor(),
        cuda=SimpleNamespace(empty_cache=lambda: None)))
    request = svc.SvcInferenceRequest(tmp_path, tmp_path, tmp_path / "vocal.wav", tmp_path, reference, 12)
    output = svc.run_svc_inference(request, progress=progress.append)
    assert output == tmp_path / "generated.wav" and output.exists()
    assert events == ["repair", "load", "render"]
    assert writes == [{"format": "WAV", "subtype": "PCM_24"}]
    assert progress == [59, 60, 61, 62, 64, 65, 70, 87, 88]
    with pytest.raises(ValueError, match="already used"):
        svc.run_svc_inference(request)


@pytest.mark.parametrize("interrupted", [False, True])
def test_sampling_observer_keeps_waveform_and_rng_unchanged_and_removes_hook(interrupted):
    """Returned-forward telemetry must preserve values and random state even on failure."""
    callbacks = []
    removed = []
    observed = []

    def _register(callback):
        callbacks.append(callback)
        return SimpleNamespace(remove=lambda: removed.append(True))

    estimator = SimpleNamespace(register_forward_hook=_register)
    model = SimpleNamespace(cfm_decoder=SimpleNamespace(model=SimpleNamespace(diff_estimator=estimator)))
    waveform = np.linspace(-.9, .9, 50, dtype=np.float32)
    original = waveform.copy()
    numpy_state = np.random.get_state()
    python_state = random.getstate()

    def _run():
        with svc._sampling_progress(model, 3.0, observed.append):
            for index in range(64):
                assert callbacks[0](estimator, (waveform,), waveform) is None
                if interrupted and index == 5:
                    raise RuntimeError("cancelled")

    if interrupted:
        with pytest.raises(RuntimeError, match="cancelled"):
            _run()
    else:
        _run()
    assert observed == list(range(1, 4 if interrupted else 33))
    assert removed == [True]
    np.testing.assert_array_equal(waveform, original)
    after = np.random.get_state()
    assert after[0] == numpy_state[0] and after[2:] == numpy_state[2:]
    np.testing.assert_array_equal(after[1], numpy_state[1])
    assert random.getstate() == python_state


def test_segment_progress_uses_real_completed_windows(monkeypatch):
    """Completed segment milestones follow actual rendering, including an unvoiced core."""
    audio = np.zeros(45 * 24_000, dtype=np.float32)
    pitch = np.zeros(len(audio) // 480)
    events = []

    def _render(model, window, contour, prompt, prompt_pitch, key, cfg, **kwargs):
        events.append("render")
        kwargs["progress_step"](16)
        return window.copy(), 0

    monkeypatch.setattr(svc, "_render_core", _render)
    result, _ = svc._assemble(None, audio, pitch, None, None, svc._plan(audio), 0, 1,
                              progress=events.append)
    np.testing.assert_array_equal(result, audio)
    assert events[0] == "render" and events[-1] == 87
    assert sum(value == "render" for value in events) == 3
