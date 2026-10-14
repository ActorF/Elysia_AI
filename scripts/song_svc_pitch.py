"""Conservatively recover short, source-supported errors in unshifted singing F0.

The public function accepts mono 24 kHz audio and the native 50 Hz contour,
returns the same dtype/length, and changes only approved source frames. Strong
periodicity, independent harmonic evidence, and original trusted anchors on
both sides are required; octave/voice ambiguity, weak breath, long UV gaps and
natural transitions retain native pitch. This is a bounded, language-agnostic
heuristic, not proof of singer identity or perceptual quality. Low-register
ambiguity deliberately causes false negatives rather than forced correction.

Candidate extraction preserves the reviewed librosa 0.9.2 pYIN behavior even
when the inference environment has librosa 0.11, whose difference function and
initial voicing prior changed. FFT/difference observations use fixed batches;
one global sparse Viterbi decode preserves full-song temporal context without
whole-song FFT arrays or per-chunk pitch-state resets. At the 720-second limit,
log observations occupy about 128 MB and traceback about 32 MB. Dependencies are
loaded lazily; the API takes no file/model paths, performs no network requests,
and patches no library globals. The source audio is never changed. Decisions with
insufficient evidence return
native values; invalid inputs and dependency/algorithm errors propagate.

The frozen v2 decision rules were checked on nine excerpts and one full song,
plus acoustic counterexamples. They do not guarantee language fidelity or fix
polyphonic singing. Pitch shifting belongs to the caller and happens after this
unshifted-source comparison, exactly once. For RVC, raw UV must be retained and
only approved post-interpolation/coarse frames changed; this function does not
implement that separate RVC contract.

The legacy difference, candidate-probability and interpolation orchestration is
adapted from librosa 0.9.2, librosa/core/pitch.py:
https://github.com/librosa/librosa/blob/0.9.2/librosa/core/pitch.py

ISC License
Copyright (c) 2013--2017, librosa development team.
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.
THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

from __future__ import annotations

from functools import lru_cache
import importlib
from types import MappingProxyType
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from numpy.typing import NDArray

_RATE = 24_000
_HOP = 480
_MAX_SECONDS = 720
_MAX_FRAMES = _MAX_SECONDS * 50 + 1
_OBSERVATION_BATCH_FRAMES = 256

_RULES = MappingProxyType({
    "version": 2,
    "sample_rate": _RATE, "hop_samples": _HOP,
    "pyin_frame_samples": 2048, "pyin_resolution_semitones": 0.2,
    "minimum_hz": 70.0, "maximum_hz": 900.0,
    "minimum_rms_dbfs": -45.0, "local_activity_drop_max_db": 20.0,
    "acf_window_ms": 50, "acf_candidate_min": 0.85,
    "acf_native_max_for_replacement": 0.60, "acf_advantage_min": 0.25,
    "spectral_window_ms": 80, "fft_samples": 16384,
    "harmonic_count_min": 3, "overtone_count_min": 2,
    "harmonics_examined": 5, "harmonic_relative_floor_db": -25.0,
    "harmonic_prominence_min_db": 6.0,
    "overtone_advantage_min_db": 6.0,
    "min_difference_cents": 80.0, "reject_difference_at_least_cents": 600.0,
    "source_voicing_probability_min": 0.4,
    "strict_source_voicing_probability_min": 0.5,
    "anchor_probability_min": 0.5, "anchor_agreement_max_cents": 50.0,
    "anchor_acf_min": 0.85, "anchor_distance_max_frames": 3,
    "island_max_frames": 5, "candidate_step_max_cents": 50.0,
    "candidate_anchor_distance_max_cents": 80.0,
    "weak_probability_seed_distance_frames": 2,
    "weak_probability_seed_pitch_max_cents": 50.0,
    "uv_run_max_frames": 2, "uv_candidate_acf_min": 0.90,
    "low_probability_bridge_max_frames": 2,
})

def _runs(mask, np):
    """Return half-open true intervals without bridging rejected frames."""

    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _cents(left: float, right: float, np) -> float:
    """Treat invalid pitch comparisons as disagreement, never evidence."""

    if not np.isfinite(left) or not np.isfinite(right) or left <= 0 or right <= 0:
        return float("inf")
    return float(abs(1200 * np.log2(left / right)))


def _acf(window, hz: float, np) -> float:
    """Compare normalized source repetition at one proposed pitch period."""

    if not np.isfinite(hz) or hz <= 0 or len(window) == 0:
        return 0.0
    lag = round(_RATE / hz)
    if lag < 1 or lag >= len(window):
        return 0.0
    centered = window - window.mean()
    left, right = centered[lag:], centered[:-lag]
    denominator = max(float(np.linalg.norm(left) * np.linalg.norm(right)), 1e-15)
    return float(np.dot(left, right) / denominator)


def _harmonics(power, frequencies, hz: float, np) -> dict:
    """Require spectral peaks above shared energy and local-background floors."""

    if not np.isfinite(hz) or hz <= 0:
        return {"supported": 0, "overtones": 0, "overtone_power": 0.0, "peaks": []}
    # A common whole-spectrum floor prevents tiny noise peaks from appearing
    # to be a strong ladder merely because they dominate their own hypothesis.
    common_floor = max(float(power.max()), 1e-15) * 10 ** (
        _RULES["harmonic_relative_floor_db"] / 10
    )
    result = []
    for harmonic in range(1, int(_RULES["harmonics_examined"]) + 1):
        expected = hz * harmonic
        tolerance = max(18.0, 0.02 * expected)
        difference = np.abs(frequencies - expected)
        bins = np.flatnonzero(difference <= tolerance)
        peak = int(bins[np.argmax(power[bins])])
        # The zero-padded FFT only locates peaks; the actual 80ms Hann window
        # limits resolution. A surrounding annulus estimates local background.
        annulus = power[(difference >= 2 * tolerance) & (difference <= 4 * tolerance)]
        floor = max(float(np.median(annulus)), 1e-15)
        prominence = float(10 * np.log10(max(float(power[peak]), 1e-15) / floor))
        supported = bool(
            power[peak] >= common_floor
            and prominence >= _RULES["harmonic_prominence_min_db"]
        )
        result.append({
            "harmonic": harmonic, "hz": float(frequencies[peak]),
            "power": float(power[peak]), "prominence_db": prominence,
            "supported": supported,
        })
    return {
        "supported": sum(row["supported"] for row in result),
        "overtones": sum(row["supported"] for row in result if row["harmonic"] > 1),
        "overtone_power": sum(
            row["power"] / row["harmonic"]
            for row in result if row["harmonic"] > 1
        ),
        "peaks": result,
    }


def _source_evidence(audio, native, tracks, np, signal):
    """Measure the unchanged source at exact native frame centers."""

    candidate, voiced, probability = tracks
    count = len(native)
    if len(candidate) < count:
        raise ValueError("Source pitch track does not cover native frame grid")
    candidate, voiced, probability = (
        candidate[:count], voiced[:count], probability[:count]
    )
    acf_half = round(_RULES["acf_window_ms"] * _RATE / 2000)
    spectrum_half = round(_RULES["spectral_window_ms"] * _RATE / 2000)
    frequencies = np.fft.rfftfreq(_RULES["fft_samples"], 1 / _RATE)
    window = signal.windows.hann(2 * spectrum_half, sym=False)
    rows = []
    for index in range(count):
        center = index * _HOP
        valid = center >= spectrum_half and center + spectrum_half <= len(audio)
        clip = audio[max(0, center-acf_half):min(len(audio), center+acf_half)]
        energy = float(10 * np.log10(max(float(np.mean(clip ** 2)), 1e-15)))
        spectrum = (
            audio[center-spectrum_half:center+spectrum_half]
            if valid else np.zeros(len(window))
        )
        power = np.abs(np.fft.rfft(
            (spectrum-spectrum.mean()) * window, _RULES["fft_samples"]
        )) ** 2
        proposed = float(candidate[index]) if np.isfinite(candidate[index]) else 0.0
        left = _harmonics(power, frequencies, proposed, np)
        right = _harmonics(power, frequencies, float(native[index]), np)
        advantage = float(10*np.log10(
            max(left["overtone_power"], 1e-15)
            / max(right["overtone_power"], 1e-15)
        ))
        rows.append({
            "frame": index, "seconds": index / 50,
            "native_hz": float(native[index]), "proposed_hz": proposed,
            "source_voiced": bool(voiced[index]), "probability": float(probability[index]),
            "source_rms_dbfs": energy, "complete_source_context": valid,
            "candidate_acf": _acf(clip, proposed, np),
            "native_acf": _acf(clip, float(native[index]), np),
            "candidate_harmonics": left, "native_harmonics": right,
            "candidate_overtone_advantage_db": advantage,
            "difference_cents": (
                _cents(proposed, float(native[index]), np)
                if proposed > 0 and native[index] > 0 else None
            ),
        })
    # This is an activity gate, not waveform normalization. It rejects very
    # weak transition windows without flattening the source dynamics.
    energy = np.array([row["source_rms_dbfs"] for row in rows])
    for index, row in enumerate(rows):
        reference = float(np.median(energy[max(0,index-15):min(count,index+16)]))
        row["active"] = bool(row["source_rms_dbfs"] >= max(
            _RULES["minimum_rms_dbfs"], reference-_RULES["local_activity_drop_max_db"]
        ))
    return rows


def _select(rows, native, np):
    """Choose bounded disagreements using original evidence and original anchors."""

    count = len(rows)
    original_uv = native <= 0
    uv_length = np.zeros(count, dtype=int)
    for start, end in _runs(original_uv, np):
        uv_length[start:end] = end-start
    anchors = np.zeros(count, dtype=bool)
    seeds = np.zeros(count, dtype=bool)
    eligible = np.zeros(count, dtype=bool)
    for index, row in enumerate(rows):
        ladder = row["candidate_harmonics"]
        original_ladder = row["native_harmonics"]
        source_supported = (
            row["complete_source_context"] and row["active"] and row["source_voiced"]
            and row["probability"] >= _RULES["source_voicing_probability_min"]
            and row["candidate_acf"] >= _RULES["acf_candidate_min"]
            and ladder["supported"] >= _RULES["harmonic_count_min"]
            and ladder["overtones"] >= _RULES["overtone_count_min"]
        )
        difference = row["difference_cents"]
        anchors[index] = bool(
            source_supported and native[index] > 0
            and row["probability"] >= _RULES["anchor_probability_min"]
            and difference is not None and difference <= _RULES["anchor_agreement_max_cents"]
            and row["native_acf"] >= _RULES["anchor_acf_min"]
        )
        reasons = []
        if not row["complete_source_context"]: reasons.append("source_boundary_context")
        if not row["active"]: reasons.append("weak_source_energy")
        if not row["source_voiced"] or row["proposed_hz"] <= 0: reasons.append("source_pyin_unvoiced")
        if row["probability"] < _RULES["source_voicing_probability_min"]: reasons.append("source_probability")
        if row["candidate_acf"] < _RULES["acf_candidate_min"]: reasons.append("candidate_weak_periodicity")
        if ladder["supported"] < _RULES["harmonic_count_min"] or ladder["overtones"] < _RULES["overtone_count_min"]:
            reasons.append("candidate_lacks_harmonic_ladder")
        if native[index] > 0:
            if difference is None or difference < _RULES["min_difference_cents"]: reasons.append("already_agrees")
            elif difference >= _RULES["reject_difference_at_least_cents"]: reasons.append("octave_or_voice_ambiguity")
            if row["native_acf"] > _RULES["acf_native_max_for_replacement"]: reasons.append("native_still_periodic")
            if row["candidate_acf"]-row["native_acf"] < _RULES["acf_advantage_min"]: reasons.append("insufficient_periodicity_advantage")
            if row["candidate_overtone_advantage_db"] < _RULES["overtone_advantage_min_db"]: reasons.append("insufficient_overtone_advantage")
            if original_ladder["supported"] >= _RULES["harmonic_count_min"] and original_ladder["overtones"] >= _RULES["overtone_count_min"]:
                reasons.append("two_competing_harmonic_ladders")
        else:
            if uv_length[index] > _RULES["uv_run_max_frames"]: reasons.append("native_uv_run_too_long")
            if row["probability"] < _RULES["strict_source_voicing_probability_min"]: reasons.append("uv_needs_strict_voicing")
            if row["candidate_acf"] < _RULES["uv_candidate_acf_min"]: reasons.append("uv_needs_strong_periodicity")
        row["trusted_original_anchor"] = bool(anchors[index])
        row["rejection_reasons"] = reasons
        eligible[index] = not reasons
        seeds[index] = eligible[index] and row["probability"] >= _RULES["strict_source_voicing_probability_min"]
    # Bridge a short dip in pYIN probability only when every non-probability
    # gate already passes. Original UV still requires strict probability, so
    # this bridge cannot itself turn a low-confidence UV frame into voicing.
    # Seeds are frozen before bridging; an accepted bridge never seeds another.
    low_probability = np.array([
        row["probability"] < _RULES["source_voicing_probability_min"]
        for row in rows
    ])
    for row in rows:
        row["probability_bridge_supported"] = False
    for start, end in _runs(low_probability, np):
        if end-start > _RULES["low_probability_bridge_max_frames"]:
            continue
        if any(
            set(rows[index]["rejection_reasons"]) != {"source_probability"}
            for index in range(start, end)
        ):
            continue
        supported = True
        for index in range(start,end):
            nearby = np.flatnonzero(seeds[
                max(0, index-_RULES["weak_probability_seed_distance_frames"]):
                min(count, index+_RULES["weak_probability_seed_distance_frames"]+1)
            ]) + max(0, index-_RULES["weak_probability_seed_distance_frames"])
            qualifying = []
            for seed in nearby:
                path = rows[min(index,int(seed)):max(index,int(seed))+1]
                if (
                    _cents(
                        rows[index]["proposed_hz"], rows[int(seed)]["proposed_hz"], np
                    ) <= _RULES["weak_probability_seed_pitch_max_cents"]
                    and all(row["source_voiced"] for row in path)
                    and all(
                        _cents(a["proposed_hz"], b["proposed_hz"], np)
                        <= _RULES["candidate_step_max_cents"]
                        for a, b in zip(path, path[1:])
                    )
                ):
                    qualifying.append(int(seed))
            if not qualifying:
                supported = False
                break
        if supported:
            for index in range(start,end):
                rows[index]["probability_bridge_supported"] = True
                rows[index]["rejection_reasons"].remove("source_probability")
                eligible[index] = True
    accepted = np.zeros(count, dtype=bool)
    islands = []
    for start, end in _runs(eligible, np):
        reasons = []
        lefts = np.flatnonzero(anchors[
            max(0, start-_RULES["anchor_distance_max_frames"]):start
        ]) + max(0, start-_RULES["anchor_distance_max_frames"])
        rights = np.flatnonzero(anchors[
            end:min(count, end+_RULES["anchor_distance_max_frames"])
        ]) + end
        left = int(lefts[-1]) if len(lefts) else None
        right = int(rights[0]) if len(rights) else None
        if end-start > _RULES["island_max_frames"]: reasons.append("loss_interval_too_long")
        if left is None or right is None:
            reasons.append("missing_bilateral_original_anchors")
        else:
            span = rows[left:right+1]
            if not all(
                row["source_voiced"] and row["active"]
                and (
                    row["probability"] >= _RULES["source_voicing_probability_min"]
                    or row["probability_bridge_supported"]
                )
                and row["candidate_acf"] >= _RULES["acf_candidate_min"]
                for row in span
            ):
                reasons.append("source_not_continuously_voiced_between_anchors")
            if any(
                _cents(a["proposed_hz"], b["proposed_hz"], np)
                > _RULES["candidate_step_max_cents"]
                for a, b in zip(span, span[1:])
            ):
                reasons.append("candidate_natural_transition_or_discontinuity")
            if any(
                _cents(row["proposed_hz"], rows[anchor]["native_hz"], np)
                > _RULES["candidate_anchor_distance_max_cents"]
                for row in rows[start:end] for anchor in (left, right)
            ):
                reasons.append("candidate_not_supported_by_both_anchors")
        for index in range(start,end):
            if rows[index]["probability"] < _RULES["strict_source_voicing_probability_min"]:
                nearby = np.flatnonzero(seeds[
                    max(0, index-_RULES["weak_probability_seed_distance_frames"]):
                    min(count, index+_RULES["weak_probability_seed_distance_frames"]+1)
                ]) + max(0, index-_RULES["weak_probability_seed_distance_frames"])
                if not any(
                    _cents(rows[index]["proposed_hz"], rows[other]["proposed_hz"], np)
                    <= _RULES["weak_probability_seed_pitch_max_cents"]
                    for other in nearby
                ):
                    reasons.append("weak_probability_lacks_nonrecursive_strict_seed")
                    break
        reasons = sorted(set(reasons))
        for index in range(start,end):
            rows[index]["rejection_reasons"].extend(reasons)
            rows[index]["left_original_anchor"] = left
            rows[index]["right_original_anchor"] = right
        if not reasons:
            accepted[start:end] = True
        islands.append({
            "frames": [int(start), int(end)], "seconds": [start/50, end/50],
            "accepted": not reasons, "left_anchor": left, "right_anchor": right,
            "rejection_reasons": reasons,
        })
    candidate = native.copy()
    values = np.array([row["proposed_hz"] for row in rows])
    candidate[accepted] = values[accepted]
    if (
        not np.array_equal(candidate[~accepted], native[~accepted])
        or not np.array_equal(candidate != native, accepted)
    ):
        raise ValueError("Local decision mask must exactly own changed F0 values")
    for index,row in enumerate(rows):
        row["accepted"] = bool(accepted[index])
        row["final_hz"] = float(candidate[index])
    return candidate, accepted, islands



def _legacy_difference(
    y_frames, frame_length, win_length, min_period, max_period, np
):
    """Cumulative mean normalized difference function (equation 8 in [#]_)

    .. [#] De Cheveigné, Alain, and Hideki Kawahara.
        "YIN, a fundamental frequency estimator for speech and music."
        The Journal of the Acoustical Society of America 111.4 (2002): 1917-1930.

    Parameters
    ----------
    y_frames : np.ndarray [shape=(frame_length, n_frames)]
        framed audio time series.
    frame_length : int > 0 [scalar]
        length of the frames in samples.
    win_length : int > 0 [scalar]
        length of the window for calculating autocorrelation in samples.
    min_period : int > 0 [scalar]
        minimum period.
    max_period : int > 0 [scalar]
        maximum period.

    Returns
    -------
    yin_frames : np.ndarray [shape=(max_period-min_period+1,n_frames)]
        Cumulative mean normalized difference function for each frame.
    """
    # Autocorrelation.
    a = np.fft.rfft(y_frames, frame_length, axis=-2)
    b = np.fft.rfft(y_frames[..., win_length:0:-1, :], frame_length, axis=-2)
    acf_frames = np.fft.irfft(a * b, frame_length, axis=-2)[..., win_length:, :]
    acf_frames[np.abs(acf_frames) < 1e-6] = 0

    # Energy terms.
    energy_frames = np.cumsum(y_frames ** 2, axis=-2)
    energy_frames = (
        energy_frames[..., win_length:, :] - energy_frames[..., :-win_length, :]
    )
    energy_frames[np.abs(energy_frames) < 1e-6] = 0

    # Difference function.
    yin_frames = energy_frames[..., :1, :] + energy_frames - 2 * acf_frames

    # Cumulative mean normalized difference function.
    yin_numerator = yin_frames[..., min_period : max_period + 1, :]
    # Only mono input is supported; the column broadcasts over source frames.
    tau_range = np.arange(1, max_period + 1)[:, np.newaxis]

    cumulative_mean = (
        np.cumsum(yin_frames[..., 1 : max_period + 1, :], axis=-2) / tau_range
    )
    yin_denominator = cumulative_mean[..., min_period - 1 : max_period, :]
    yin_frames = yin_numerator / (yin_denominator + np.finfo(yin_denominator.dtype).tiny)
    return yin_frames


def _legacy_parabolic_interpolation(y_frames, np):
    """Piecewise parabolic interpolation for yin and pyin.

    Parameters
    ----------
    y_frames : np.ndarray [shape=(frame_length, n_frames)]
        framed audio time series.

    Returns
    -------
    parabolic_shifts : np.ndarray [shape=(frame_length, n_frames)]
        position of the parabola optima
    """

    parabolic_shifts = np.zeros_like(y_frames)
    parabola_a = (
        y_frames[..., :-2, :] + y_frames[..., 2:, :] - 2 * y_frames[..., 1:-1, :]
    ) / 2
    parabola_b = (y_frames[..., 2:, :] - y_frames[..., :-2, :]) / 2
    parabolic_shifts[..., 1:-1, :] = -parabola_b / (
        2 * parabola_a + np.finfo(parabola_a.dtype).tiny
    )
    parabolic_shifts[np.abs(parabolic_shifts) > 1] = 0
    return parabolic_shifts


def _observations(
    yin_frames,
    parabolic_shifts,
    sr,
    thresholds,
    boltzmann_parameter,
    beta_probs,
    no_trough_prob,
    min_period,
    fmin,
    n_pitch_bins,
    n_bins_per_semitone,
    np, librosa, stats,
):
    """Retain legacy pYIN probability mass and bin-assignment semantics."""

    yin_probs = np.zeros_like(yin_frames)

    for i, yin_frame in enumerate(yin_frames.T):
        is_trough = librosa.util.localmin(yin_frame)

        is_trough[0] = yin_frame[0] < yin_frame[1]
        (trough_index,) = np.nonzero(is_trough)

        if len(trough_index) == 0:
            continue

        trough_heights = yin_frame[trough_index]
        trough_thresholds = np.less.outer(trough_heights, thresholds[1:])

        # The legacy Boltzmann prior favors shorter-period troughs instead of
        # distributing equal mass among all local minima below a threshold.
        trough_positions = np.cumsum(trough_thresholds, axis=0) - 1
        n_troughs = np.count_nonzero(trough_thresholds, axis=0)

        trough_prior = stats.boltzmann.pmf(
            trough_positions, boltzmann_parameter, n_troughs
        )

        trough_prior[~trough_thresholds] = 0

        probs = trough_prior.dot(beta_probs)

        global_min = np.argmin(trough_heights)
        n_thresholds_below_min = np.count_nonzero(~trough_thresholds[global_min, :])
        # Preserve the small global-minimum fallback when no trough passes a
        # threshold; removing it changes the HMM path around weak syllables.
        probs[global_min] += no_trough_prob * np.sum(
            beta_probs[:n_thresholds_below_min]
        )

        yin_probs[trough_index, i] = probs

    yin_period, frame_index = np.nonzero(yin_probs)

    # Refine peak by parabolic interpolation.
    period_candidates = min_period + yin_period
    period_candidates = period_candidates + parabolic_shifts[yin_period, frame_index]
    f0_candidates = sr / period_candidates

    # Find pitch bin corresponding to each f0 candidate.
    bin_index = 12 * n_bins_per_semitone * np.log2(f0_candidates / fmin)
    bin_index = np.clip(np.round(bin_index), 0, n_pitch_bins).astype(int)

    # Observation probabilities.
    observation_probs = np.zeros((2 * n_pitch_bins, yin_frames.shape[1]))
    observation_probs[bin_index, frame_index] = yin_probs[yin_period, frame_index]

    voiced_prob = np.clip(
        np.sum(observation_probs[:n_pitch_bins, :], axis=0, keepdims=True), 0, 1
    )
    observation_probs[n_pitch_bins:, :] = (1 - voiced_prob) / n_pitch_bins

    return observation_probs[np.newaxis], voiced_prob


def _banded_viterbi_core(
    log_probability: NDArray[Any],
    predecessor_indexes: NDArray[Any],
    predecessor_scores: NDArray[Any],
    predecessor_counts: NDArray[Any],
    baseline_score: float,
    initial_scores: NDArray[Any],
    state: NDArray[Any],
    pointers: NDArray[Any],
    previous: NDArray[Any],
    current: NDArray[Any],
) -> float:
    """Decode one sparse transition lattice with dense-Viterbi tie semantics.

    librosa represents forbidden pYIN transitions as the floating-point tiny
    value instead of negative infinity.  The best such transition can be found
    once per frame from the global best predecessor; only the narrow band of
    real pitch transitions then needs to be scanned for each destination.  A
    lower source index wins exact score ties, matching ``numpy.argmax`` in the
    reviewed dense decoder.

    This deliberately allocation-free loop is compiled lazily with Numba.
    Keeping compilation lazy lets the owning process establish its closed
    runtime environment before importing NumPy or Numba.
    """

    frame_count = log_probability.shape[0]
    state_count = log_probability.shape[1]
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


@lru_cache(maxsize=1)
def _compiled_decoder() -> Any:
    """Compile only the allocation-free recurrence; keep all arrays per call."""

    numba = importlib.import_module("numba")
    return numba.njit(cache=False)(_banded_viterbi_core)


def _decode(log_probability: Any, transition: Any, initial: Any, np: Any) -> Any:
    """Decode the whole song exactly with sparse predecessors and bounded RAM."""

    frame_count, state_count = log_probability.shape
    if (
        not 1 <= frame_count <= _MAX_FRAMES or not 1 <= state_count <= 512
        or transition.shape != (state_count, state_count)
        or initial.shape != (state_count,)
        or not np.isfinite(log_probability).all()
    ):
        raise ValueError("Invalid bounded pYIN decoding lattice")
    tiny = np.finfo(np.float64).tiny
    scores = np.log(transition + tiny)
    initial_scores = np.log(initial + tiny)
    baseline = float(scores.min())
    meaningful = scores > baseline
    counts = np.count_nonzero(meaningful, axis=0).astype(np.int32)
    maximum = int(counts.max())
    if not 1 <= maximum <= 128:
        raise RuntimeError("Unsupported pYIN transition bandwidth")
    indexes = np.full((state_count, maximum), -1, dtype=np.int32)
    weights = np.full((state_count, maximum), baseline, dtype=np.float64)
    for destination in range(state_count):
        sources = np.flatnonzero(meaningful[:, destination])
        indexes[destination, :len(sources)] = sources
        weights[destination, :len(sources)] = scores[sources, destination]
    state = np.empty(frame_count, dtype=np.uint16)
    pointers = np.empty((frame_count, state_count), dtype=np.uint16)
    previous = np.empty(state_count, dtype=np.float64)
    current = np.empty(state_count, dtype=np.float64)
    _compiled_decoder()(
        log_probability, indexes, weights, counts, baseline, initial_scores,
        state, pointers, previous, current,
    )
    return state


def _pitch_track(audio: Any, np: Any, librosa: Any, stats: Any) -> tuple[Any, Any, Any]:
    """Preserve 0.9.2 pYIN while bounding per-frame FFT/difference allocation.

    Acoustic observations are frame-local and can be batched without changing
    their support. The HMM is decoded once over every observation, including
    the original endpoint frame. It therefore retains global state and exact
    native frame centers without stitching independently decoded pitch tracks.
    """

    frame_length = _RULES["pyin_frame_samples"]
    win_length = frame_length // 2
    padded = np.pad(audio, (win_length, win_length), mode="constant")
    frames = librosa.util.frame(padded, frame_length=frame_length, hop_length=_HOP)
    frame_count = frames.shape[1]
    if frame_count > _MAX_FRAMES:
        raise ValueError("Source exceeds the bounded pYIN timeline")
    fmin, fmax = _RULES["minimum_hz"], _RULES["maximum_hz"]
    minimum = max(int(np.floor(_RATE / fmax)), 1)
    maximum = min(int(np.ceil(_RATE / fmin)), frame_length-win_length-1)
    thresholds = np.linspace(0, 1, 101)
    beta = np.diff(stats.beta.cdf(thresholds, 2, 18))
    bins_per_semitone = int(np.ceil(1 / _RULES["pyin_resolution_semitones"]))
    pitch_bins = int(np.floor(12*bins_per_semitone*np.log2(fmax/fmin))) + 1
    logs = np.empty((frame_count, 2*pitch_bins), dtype=np.float64)
    probability = np.empty(frame_count, dtype=np.float64)
    tiny = np.finfo(np.float64).tiny
    for start in range(0, frame_count, _OBSERVATION_BATCH_FRAMES):
        end = min(frame_count, start+_OBSERVATION_BATCH_FRAMES)
        difference = _legacy_difference(
            frames[:, start:end], frame_length, win_length, minimum, maximum, np,
        )
        shifts = _legacy_parabolic_interpolation(difference, np)
        observations, voiced = _observations(
            difference, shifts, _RATE, thresholds, 2, beta, 0.01,
            minimum, fmin, pitch_bins, bins_per_semitone, np, librosa, stats,
        )
        logs[start:end] = np.log(observations[0].T + tiny)
        probability[start:end] = voiced[0]
    width = round(35.92*12*_HOP/_RATE)*bins_per_semitone + 1
    local = librosa.sequence.transition_local(
        pitch_bins, width, window="triangle", wrap=False,
    )
    transition = np.kron(librosa.sequence.transition_loop(2, 0.99), local)
    # librosa 0.11 starts uniformly in both voicing states. Retain the reviewed
    # 0.9.2 prior (initially unvoiced), rather than changing quiet song openings.
    initial = np.zeros(2*pitch_bins, dtype=np.float64)
    initial[pitch_bins:] = 1/pitch_bins
    states = _decode(logs, transition, initial, np)
    frequencies = fmin*2**(np.arange(pitch_bins)/(12*bins_per_semitone))
    f0 = frequencies[states % pitch_bins]
    voiced = states < pitch_bins
    f0[~voiced] = np.nan
    return f0, voiced, probability


def repair_pitch(audio24k: Any, native_f0: Any) -> Any:
    """Return native F0 with only strongly supported short errors corrected.

    ``audio24k`` is finite mono floating audio at exactly 24 kHz, between one
    sample and 720 seconds. ``native_f0`` is a finite nonnegative float32/float64
    vector with ``ceil(len(audio24k)/480)`` unshifted Hz values; zero means UV.
    The caller must supply the actual 24 kHz timeline: no rate inference or
    resampling occurs here. Both arguments remain unchanged. The returned array
    retains native dtype/shape and every unapproved value at the byte level.

    Uncertain, ambiguous and out-of-range source estimates leave native values
    alone. Malformed input raises ValueError; dependency/runtime defects are
    allowed to propagate so failed analysis is not reported as successful audio.
    This function makes no speaker, language or audible-quality guarantee.
    """

    np = importlib.import_module("numpy")
    source = np.asarray(audio24k)
    native = np.asarray(native_f0)
    if (
        source.ndim != 1 or source.dtype.kind != "f"
        or not 1 <= len(source) <= _RATE*_MAX_SECONDS
        or native.ndim != 1 or native.dtype not in (np.dtype("float32"), np.dtype("float64"))
        or len(native) != (len(source)+_HOP-1)//_HOP
        or not np.isfinite(source).all() or not np.isfinite(native).all()
        or np.any(native < 0)
    ):
        raise ValueError("Expected finite mono 24kHz audio and its unshifted 50Hz F0")
    # A complete 80ms source window is mandatory for every decision. Short
    # inputs cannot provide that evidence and are an ordinary abstention.
    if len(source) < round(_RULES["spectral_window_ms"]*_RATE/1000):
        return native.copy()
    source = source.astype(np.float64, copy=False)
    librosa = importlib.import_module("librosa")
    signal = importlib.import_module("scipy.signal")
    stats = importlib.import_module("scipy.stats")
    tracks = _pitch_track(source, np, librosa, stats)
    rows = _source_evidence(source, native, tracks, np, signal)
    corrected, accepted, _ = _select(rows, native, np)
    if native[~accepted].tobytes() != corrected[~accepted].tobytes():
        raise RuntimeError("Unapproved pitch values changed")
    return corrected


__all__ = ["repair_pitch"]
