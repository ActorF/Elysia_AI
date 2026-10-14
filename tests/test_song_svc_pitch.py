"""Verify conservative singing-F0 recovery against acoustic counterexamples.

Standard-library unittest keeps these checks runnable in the isolated inference
environment without installing pytest there. Numeric tests skip only when their
actual optional dependencies are unavailable. Synthetic candidate tracks isolate
decision gates; their ACF, harmonic and energy evidence comes from real generated
waveforms. Separate tests exercise the public estimator and sparse decoding.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import song_svc_pitch as pitch


class TestPitchImport(unittest.TestCase):
    """Keep optional audio dependencies out of ordinary source-module imports."""

    def test_import_is_standard_library_only(self) -> None:
        """Loading the standalone file must not initialize NumPy or ML runtimes."""

        module = Path(pitch.__file__).resolve()
        code = (
            "import runpy,sys; "
            "runpy.run_path(sys.argv[1]); "
            "assert not any(x in sys.modules for x in "
            "('numpy','scipy','librosa','numba','torch'))"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(module)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestPitchContract(unittest.TestCase):
    """Check input ownership, validation and honest propagation of failures."""

    def setUp(self) -> None:
        """Require NumPy only for the concrete ndarray contract checks."""

        try:
            self.np = importlib.import_module("numpy")
        except ImportError as error:
            self.skipTest(str(error))

    def test_short_source_abstains_with_exact_native_bytes(self) -> None:
        """Insufficient acoustic context retains even signed-zero native bits."""

        np = self.np
        for dtype in (np.float32, np.float64):
            source = np.zeros(481, dtype=np.float32)
            native = np.array([-0.0, 220.125], dtype=dtype)
            before = native.tobytes()
            result = pitch.repair_pitch(source, native)
            self.assertEqual(result.dtype, native.dtype)
            self.assertEqual(result.tobytes(), before)
            self.assertEqual(native.tobytes(), before)
            self.assertFalse(np.shares_memory(native, result))

    def test_rejects_invalid_or_misaligned_input(self) -> None:
        """Reject invalid PCM/F0 instead of analyzing another implicit timeline."""

        np = self.np
        good_source = np.zeros(960, dtype=np.float32)
        good_f0 = np.zeros(2, dtype=np.float64)
        cases = [
            (np.zeros((2, 480), dtype=np.float32), good_f0),
            (np.zeros(960, dtype=np.int16), good_f0),
            (np.empty(0), np.empty(0)),
            (good_source, np.zeros(1)),
            (good_source, np.zeros((2, 1))),
            (good_source, np.zeros(2, dtype=np.int32)),
            (good_source, np.array([0.0, -1.0])),
            (good_source, np.array([0.0, np.nan])),
            (np.full(960, np.inf), good_f0),
            (np.broadcast_to(np.zeros(1), (720*24000+1,)), good_f0),
        ]
        for source, native in cases:
            with self.subTest(shape=source.shape, dtype=str(native.dtype)):
                with self.assertRaises(ValueError):
                    pitch.repair_pitch(source, native)

    def test_real_analysis_failure_is_not_silently_abstained(self) -> None:
        """An implementation failure must propagate to the owning runtime."""

        np = self.np
        imports = lambda name: np if name == "numpy" else SimpleNamespace()
        with mock.patch.object(pitch.importlib, "import_module", side_effect=imports):
            with mock.patch.object(pitch, "_pitch_track", side_effect=RuntimeError("broken estimator")):
                with self.assertRaisesRegex(RuntimeError, "broken estimator"):
                    pitch.repair_pitch(np.zeros(2400), np.zeros(5))


class TestAcousticDecisions(unittest.TestCase):
    """Challenge repair decisions with waveform evidence, including false voicing."""

    def setUp(self) -> None:
        """Load the optional CPU DSP stack used to measure actual source waves."""

        try:
            self.np = importlib.import_module("numpy")
            self.signal = importlib.import_module("scipy.signal")
        except ImportError as error:
            self.skipTest(str(error))

    def _case(self, name: str, hz: float = 440.0):
        np = self.np
        hop, count = 480, 100
        times = np.arange(count*hop)/24000
        audio = sum(.1/h*np.sin(2*np.pi*hz*h*times) for h in range(1, 6))
        native = np.full(count, hz, dtype=np.float64)
        proposed = np.full(count, hz, dtype=np.float64)
        voiced = np.ones(count, dtype=bool)
        probability = np.full(count, .9)
        native[45:48] = hz*.8
        probability[46] = .2
        if name == "weak_periodicity":
            audio[42*hop:51*hop] = np.random.default_rng(71).normal(0, .06, 9*hop)
        elif name in ("genuine_uv", "low_probability_false_voiced_uv"):
            audio[46*hop-hop//2:46*hop+hop//2] = 0
            native[46] = 0
            if name == "genuine_uv":
                voiced[46] = False
        elif name == "discontinuous_candidate":
            segment = slice(46*hop-hop//2, 46*hop+hop//2)
            audio[segment] = sum(.1/h*np.sin(2*np.pi*660*h*times[segment]) for h in range(1, 6))
            proposed[46] = 660
        elif name == "two_harmonic_ladders":
            audio += sum(.09/h*np.sin(2*np.pi*hz*.8*h*times) for h in range(1, 6))
        elif name == "octave_ambiguity":
            native[45:48] = hz/2
        elif name == "long_native_uv":
            native[45:48] = 0
            probability[:] = .9
        elif name == "long_low_probability":
            probability[45:48] = .2
        elif name == "weak_energy":
            audio *= 1e-4
        elif name == "instrument_only_native_unvoiced":
            native[:] = 0
            probability[:] = .9
        elif name == "already_correct":
            native[:] = hz
        elif name == "natural_vibrato":
            actual = hz*2**(50*np.sin(2*np.pi*5*times)/1200)
            phase = np.cumsum(actual)/24000
            audio = sum(.1/h*np.sin(2*np.pi*h*phase) for h in range(1, 6))
            native = actual[::hop].copy()
            proposed = native.copy()
        return audio, native, (proposed, voiced, probability)

    def _evaluate(self, audio, native, tracks):
        before_audio, before_native = audio.tobytes(), native.tobytes()
        rows = pitch._source_evidence(audio, native, tracks, self.np, self.signal)
        corrected, mask, _ = pitch._select(rows, native, self.np)
        self.assertEqual(audio.tobytes(), before_audio)
        self.assertEqual(native.tobytes(), before_native)
        self.assertEqual(corrected[~mask].tobytes(), native[~mask].tobytes())
        self.assertTrue(self.np.array_equal(mask, corrected != native))
        return corrected, mask, rows

    def test_short_mislock_uses_independent_source_evidence(self) -> None:
        """A low-probability frame can repair only inside a proven short loss."""

        for hz in (220.0, 440.0, 660.0):
            with self.subTest(hz=hz):
                audio, native, tracks = self._case("positive", hz)
                result, mask, rows = self._evaluate(audio, native, tracks)
                self.assertEqual(self.np.flatnonzero(mask).tolist(), [45, 46, 47])
                self.assertEqual(result[46], hz)
                self.assertTrue(rows[46]["probability_bridge_supported"])
                self.assertEqual(rows[46]["probability"], .2)

    def test_acoustic_counterexamples_retain_native_pitch(self) -> None:
        """Reject false voicing, ambiguity and natural changes without smoothing."""

        names = (
            "weak_periodicity", "genuine_uv", "low_probability_false_voiced_uv",
            "discontinuous_candidate", "two_harmonic_ladders", "octave_ambiguity",
            "long_native_uv", "long_low_probability", "weak_energy",
            "instrument_only_native_unvoiced", "already_correct", "natural_vibrato",
        )
        for name in names:
            with self.subTest(name=name):
                audio, native, tracks = self._case(name)
                result, mask, _ = self._evaluate(audio, native, tracks)
                self.assertFalse(mask.any())
                self.assertEqual(result.tobytes(), native.tobytes())

    def test_low_register_ambiguity_remains_an_explicit_abstention(self) -> None:
        """Retain the known 110Hz false negative instead of weakening protection."""

        audio, native, tracks = self._case("positive", 110.0)
        _, mask, rows = self._evaluate(audio, native, tracks)
        self.assertFalse(mask.any())
        self.assertIn("two_competing_harmonic_ladders", rows[46]["rejection_reasons"])

    def test_two_frame_bridge_does_not_expand_to_three_frames(self) -> None:
        """Frozen strict seeds must not recursively validate a longer weak span."""

        for weak_length in (2, 3):
            with self.subTest(weak_length=weak_length):
                audio, native, tracks = self._case("positive")
                native[45:49] = 352
                tracks[2][:] = .9
                tracks[2][46:46+weak_length] = .2
                _, mask, _ = self._evaluate(audio, native, tracks)
                expected = [45, 46, 47, 48] if weak_length == 2 else []
                self.assertEqual(self.np.flatnonzero(mask).tolist(), expected)


class TestPitchEstimator(unittest.TestCase):
    """Verify the real estimator, global-state decoder and bounded batching."""

    def setUp(self) -> None:
        """Require the same CPU libraries as the public runtime function."""

        try:
            self.np = importlib.import_module("numpy")
            self.librosa = importlib.import_module("librosa")
            self.stats = importlib.import_module("scipy.stats")
            importlib.import_module("numba")
        except ImportError as error:
            self.skipTest(str(error))

    def test_fft_batch_size_does_not_change_global_pitch(self) -> None:
        """Frame batches preserve exact observation values and one HMM path."""

        np = self.np
        times = np.arange(24000*6)/24000
        frequency = 220*2**(.04*np.sin(2*np.pi*4*times))
        phase = np.cumsum(frequency)/24000
        audio = .2*np.sin(2*np.pi*phase)+.1*np.sin(4*np.pi*phase)
        expected = pitch._pitch_track(audio, np, self.librosa, self.stats)
        with mock.patch.object(pitch, "_OBSERVATION_BATCH_FRAMES", 37):
            actual = pitch._pitch_track(audio, np, self.librosa, self.stats)
        for left, right in zip(expected, actual):
            self.assertTrue(np.array_equal(left, right, equal_nan=True))

    def test_sparse_decoder_matches_dense_recurrence_and_ties(self) -> None:
        """Sparse optimization retains impossible-transition and argmax ties."""

        np = self.np
        for tied in (False, True):
            with self.subTest(tied=tied):
                observations = np.full((25, 6), 1/6) if tied else np.random.default_rng(13).uniform(.001, 1, (25, 6))
                logs = np.log(observations)
                transition = np.eye(6)*.7
                for index in range(6):
                    transition[index, (index+1) % 6] = .3
                if tied:
                    transition[:] = 1/6
                initial = np.ones(6)/6
                scores = logs[0]+np.log(initial)
                pointers = []
                log_transition = np.log(transition+np.finfo(np.float64).tiny)
                for frame in logs[1:]:
                    candidates = scores[:, None]+log_transition
                    best = np.argmax(candidates, axis=0)
                    pointers.append(best)
                    scores = frame+candidates[best, np.arange(6)]
                expected = [int(np.argmax(scores))]
                for back in pointers[::-1]:
                    expected.append(int(back[expected[-1]]))
                if tied:
                    # A fully uniform transition has no meaningful local band;
                    # exercise ties with a valid sparse symmetric alternative.
                    transition = np.eye(6)
                    log_transition = np.log(transition+np.finfo(np.float64).tiny)
                    expected = [0]*len(logs)
                else:
                    expected.reverse()
                actual = pitch._decode(logs, transition, initial, np)
                self.assertEqual(actual.tolist(), expected)

    def test_public_repair_preserves_dtype_and_original_inputs(self) -> None:
        """Actual pYIN corrects a clear injected native lock error locally."""

        np = self.np
        times = np.arange(24000*2)/24000
        source = sum(.1/h*np.sin(2*np.pi*440*h*times) for h in range(1, 6)).astype(np.float32)
        for dtype in (np.float32, np.float64):
            with self.subTest(dtype=str(dtype)):
                native = np.full(100, 440, dtype=dtype)
                native[45:48] = 352
                native[0] = -0.0
                before_audio, before_native = source.tobytes(), native.tobytes()
                result = pitch.repair_pitch(source, native)
                self.assertEqual(result.dtype, native.dtype)
                self.assertEqual(np.flatnonzero(result != native).tolist(), [45, 46, 47])
                self.assertEqual(result[0].tobytes(), native[0].tobytes())
                self.assertEqual(source.tobytes(), before_audio)
                self.assertEqual(native.tobytes(), before_native)


if __name__ == "__main__":
    unittest.main()
