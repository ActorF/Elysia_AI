"""Test deterministic, model-free quality control for SoulX candidates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

import numpy as np
import pytest

from scripts.song_svs_quality import (
    CandidateBudget,
    DEFAULT_QUALITY_THRESHOLDS,
    PromptRecord,
    QualityAssessment,
    QualityManifestError,
    QualitySelectionError,
    RescueRequest,
    SegmentCandidate,
    TargetFeatures,
    analyze_waveform,
    assess_waveform,
    canonical_candidate_seed,
    plan_rescue_candidates,
    select_candidate_path,
    select_prompts,
    stitch_candidate_path,
)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _prompt_entry(
    prompt_id: str,
    *,
    median: float = 60.0,
    phonemes: tuple[str, ...] = ("zh_wo", "zh_ai"),
    emotion: str | None = "bright",
    style: str = "clear",
    rank: int = 0,
    audio_sha256: str = _DIGEST_A,
    metadata_sha256: str = _DIGEST_B,
) -> dict[str, object]:
    return {
        "id": prompt_id,
        "role": "candidate",
        "selection_rank": rank,
        "style_proxy": style,
        "emotion": emotion,
        "audio": {
            "path": f"prompts/{prompt_id}.wav",
            "bytes": 100,
            "sha256": audio_sha256,
        },
        "metadata": {
            "path": f"prompts/{prompt_id}.json",
            "bytes": 200,
            "sha256": metadata_sha256,
        },
        "profile": {
            "duration_seconds": 10.0,
            "note_median": median,
            "note_p10": median - 4.0,
            "note_p90": median + 4.0,
            "note_span": 12.0,
            "syllables_per_second": 2.0,
            "phonemes": list(phonemes),
        },
    }


def _target_metadata() -> list[dict[str, object]]:
    return [
        {
            "phoneme": "<SP> zh_wo zh_ai zh_ai <SP>",
            "note_pitch": "0 58 60 62 0",
            "note_type": "1 2 2 3 1",
            "duration": "0.2 0.4 0.4 0.4 0.2",
        }
    ]


def _assessment(loss: float = 0.1, *, passed: bool = True) -> QualityAssessment:
    return QualityAssessment(passed, () if passed else ("failed",), loss)


def _candidate(
    candidate_id: str,
    *,
    segment: int,
    start: int,
    waveform: np.ndarray,
    prompt: str = "neutral",
    loss: float = 0.1,
    passed: bool = True,
    headroom: float = 0.0,
) -> SegmentCandidate:
    return SegmentCandidate(
        candidate_id=candidate_id,
        prompt_id=prompt,
        segment_index=segment,
        start_sample=start,
        end_sample=start + len(waveform),
        waveform=waveform,
        assessment=_assessment(loss, passed=passed),
        required_attenuation_db=headroom,
    )


def test_manifest_parser_accepts_minimum_contract_and_validated_paths() -> None:
    """Use runtime-validated paths while retaining only manifest profile data."""

    record = PromptRecord.from_manifest_entry(
        _prompt_entry("bright-v1"),
        validated_audio_path=Path("/private/verified.wav"),
        validated_metadata_path=Path("/private/verified.json"),
    )
    assert record.prompt_id == "bright-v1"
    assert record.audio.path == Path("/private/verified.wav")
    assert record.profile.note_median == 60.0
    assert record.profile.phonemes == frozenset({"zh_wo", "zh_ai"})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.pop("profile"),
        lambda item: item["audio"].update({"sha256": "BAD"}),
        lambda item: item["profile"].update({"note_p10": 90.0}),
        lambda item: item["profile"].update({"note_span": 1.0}),
        lambda item: item.update({"emotion": ""}),
    ],
)
def test_manifest_parser_rejects_malformed_required_fields(mutation: object) -> None:
    """Reject incomplete digests, profiles, and manually curated labels."""

    entry = _prompt_entry("neutral-v1")
    mutation(entry)  # type: ignore[operator]
    with pytest.raises(QualityManifestError):
        PromptRecord.from_manifest_entry(entry)


def test_target_features_and_prompt_selector_use_emotion_only_as_tie_break() -> None:
    """Prefer register and phoneme coverage before a manual emotion label."""

    target = TargetFeatures.from_metadata(
        _target_metadata(),
        emotion="bright",
        style_proxy="clear",
    )
    acoustic_winner = PromptRecord.from_manifest_entry(
        _prompt_entry(
            "acoustic",
            median=60.0,
            phonemes=("zh_wo", "zh_ai"),
            emotion="soft",
            audio_sha256="1" * 64,
        )
    )
    emotional_but_wrong = PromptRecord.from_manifest_entry(
        _prompt_entry(
            "far",
            median=78.0,
            phonemes=("zh_wo",),
            emotion="bright",
            audio_sha256="2" * 64,
        )
    )
    tie_without_emotion = PromptRecord.from_manifest_entry(
        _prompt_entry("tie-a", emotion="soft", rank=0, audio_sha256="3" * 64)
    )
    tie_with_emotion = PromptRecord.from_manifest_entry(
        _prompt_entry("tie-b", emotion="bright", rank=99, audio_sha256="4" * 64)
    )
    ranked = select_prompts(
        [emotional_but_wrong, acoustic_winner, tie_without_emotion, tie_with_emotion],
        target,
        limit=4,
    )
    assert ranked[0].prompt.prompt_id == "tie-b"
    assert ranked[1].prompt.prompt_id in {"acoustic", "tie-a"}
    assert ranked[-1].prompt.prompt_id == "far"


def test_prompt_selector_deduplicates_authenticated_asset_pairs_after_ranking() -> None:
    """Spend C2 only on a distinct audio-and-metadata reference pair."""

    target = TargetFeatures.from_metadata(_target_metadata())
    shared_audio = "5" * 64
    shared_metadata = "6" * 64
    alias = PromptRecord.from_manifest_entry(
        _prompt_entry(
            "alias",
            rank=99,
            audio_sha256=shared_audio,
            metadata_sha256=shared_metadata,
        )
    )
    preferred = PromptRecord.from_manifest_entry(
        _prompt_entry(
            "preferred",
            rank=0,
            audio_sha256=shared_audio,
            metadata_sha256=shared_metadata,
        )
    )
    different_metadata = PromptRecord.from_manifest_entry(
        _prompt_entry(
            "different-metadata",
            rank=1,
            audio_sha256=shared_audio,
            metadata_sha256="7" * 64,
        )
    )
    different_audio = PromptRecord.from_manifest_entry(
        _prompt_entry(
            "different-audio",
            rank=2,
            audio_sha256="8" * 64,
            metadata_sha256=shared_metadata,
        )
    )

    ranked = select_prompts(
        [alias, different_audio, preferred, different_metadata],
        target,
        limit=4,
    )

    assert {selection.prompt.prompt_id for selection in ranked} == {
        "preferred",
        "different-audio",
        "different-metadata",
    }


def test_canonical_seed_ignores_uuid_paths_and_mapping_order() -> None:
    """Keep candidate randomness stable across job locations and retries."""

    first = canonical_candidate_seed(
        target_audio_sha256=_DIGEST_A,
        segment_metadata={
            "job_id": "123e4567-e89b-42d3-a456-426614174000",
            "path": "C:/private/one.wav",
            "nested": {
                "audio_path": "C:/private/input.wav",
                "vendor_uuid": "123e4567-e89b-42d3-a456-426614174000",
            },
            "text": "无 瑕",
            "time": [0, 1000],
        },
        prompt_sha256=_DIGEST_B,
        candidate_ordinal=0,
    )
    second = canonical_candidate_seed(
        target_audio_sha256=_DIGEST_A,
        segment_metadata={
            "time": [0, 1000],
            "text": "无 瑕",
            "path": "/mnt/d/private/two.wav",
            "job_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "nested": {
                "audio_path": "/mnt/d/private/input.wav",
                "vendor_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            },
        },
        prompt_sha256=_DIGEST_B,
        candidate_ordinal=0,
    )
    assert first == second
    assert first != canonical_candidate_seed(
        target_audio_sha256=_DIGEST_A,
        segment_metadata={"text": "无 瑕", "time": [0, 1000]},
        prompt_sha256=_DIGEST_B,
        candidate_ordinal=1,
    )


def test_rescue_planner_caps_segments_candidates_and_rendered_seconds() -> None:
    """Allocate first rescues before seconds while respecting every hard cap."""

    requests = [RescueRequest(index, 10.0, float(20 - index)) for index in range(20)]
    allocations = plan_rescue_candidates(
        requests,
        total_voiced_seconds=400.0,
    )
    assert len(allocations) == 9
    assert sum(len(item.candidate_ordinals) * 10 for item in allocations) == 90
    assert all(item.candidate_ordinals in {(1,), (1, 2)} for item in allocations)
    assert max(item.segment_index for item in allocations) < 12
    with pytest.raises(ValueError):
        CandidateBudget(max_candidates_per_segment=4)


def test_rescue_planner_grants_one_bounded_single_segment_retry() -> None:
    """Keep a one-segment song recoverable without inventing an unavailable C2."""

    allocations = plan_rescue_candidates(
        [RescueRequest(0, 30.0, 10.0, candidate_ordinals=(1,))],
        total_voiced_seconds=30.0,
    )
    # The highest-priority request may exceed 25 percent once, but remains
    # below the absolute 90-second ceiling.
    assert [(item.segment_index, item.candidate_ordinals) for item in allocations] == [
        (0, (1,)),
    ]


def test_rescue_planner_binds_fraction_exception_to_highest_priority() -> None:
    """Do not let a later soft request steal a hard failure's one-segment retry."""

    hard_failure = RescueRequest(
        0,
        40.0,
        100.0,
        candidate_ordinals=(1,),
    )
    alone = plan_rescue_candidates(
        [hard_failure],
        total_voiced_seconds=100.0,
    )
    with_soft_request = plan_rescue_candidates(
        [hard_failure, RescueRequest(1, 10.0, 1.0)],
        total_voiced_seconds=100.0,
    )
    expected = [(0, (1,))]
    assert [
        (item.segment_index, item.candidate_ordinals) for item in alone
    ] == expected
    assert [
        (item.segment_index, item.candidate_ordinals) for item in with_soft_request
    ] == expected


def test_rescue_planner_does_not_charge_an_unavailable_second_prompt() -> None:
    """Spend later budget on a real C1 instead of a nonexistent C2 candidate."""

    allocations = plan_rescue_candidates(
        [
            RescueRequest(0, 10.0, 20.0, candidate_ordinals=(1,)),
            RescueRequest(1, 10.0, 10.0, candidate_ordinals=(1, 2)),
        ],
        total_voiced_seconds=120.0,
    )
    assert [(item.segment_index, item.candidate_ordinals) for item in allocations] == [
        (0, (1,)),
        (1, (1, 2)),
    ]


def test_waveform_metrics_accept_a_clean_tone_with_exact_pitch() -> None:
    """Measure a deterministic clean note without invoking a private F0 model."""

    sample_rate = 24_000
    duration = 0.8
    timeline = np.arange(int(sample_rate * duration)) / sample_rate
    waveform = 0.2 * np.sin(2.0 * np.pi * 220.0 * timeline)
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
        expected_f0_hz=220.0,
        expected_voiced_mask=True,
        expected_silence_mask=False,
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.pitch_median_abs_cents is not None
    assert metrics.pitch_median_abs_cents < 20.0
    assert metrics.dropout_rate == 0.0
    assert assessment.passed


def test_pitch_loss_increases_from_gross_to_octave_scale_errors() -> None:
    """Never rank a severe octave error above a smaller gross pitch error."""

    sample_rate = 24_000
    timeline = np.arange(sample_rate, dtype=np.float64) / sample_rate
    results = []
    for frequency in (220.0, 261.625565, 880.0):
        waveform = 0.2 * np.sin(2.0 * np.pi * frequency * timeline)
        metrics = analyze_waveform(
            waveform,
            sample_rate=sample_rate,
            expected_samples=len(waveform),
            expected_f0_hz=220.0,
            expected_voiced_mask=True,
        )
        results.append((metrics, assess_waveform(metrics, sample_rate=sample_rate)))

    assert results[1][0].gross_pitch_error_rate == 1.0
    assert results[1][0].octave_error_rate == 0.0
    assert results[2][0].octave_error_rate == 1.0
    assert results[0][1].local_loss < results[1][1].local_loss
    assert results[1][1].local_loss < results[2][1].local_loss


def test_waveform_metrics_hard_gate_only_calibrated_structural_defects() -> None:
    """Rank uncertain acoustic evidence without rejecting otherwise real audio."""

    sample_rate = 8_000
    count = sample_rate
    timeline = np.arange(count) / sample_rate
    waveform = np.zeros(count)
    waveform[: count // 2] = 1.0
    waveform[count // 2 :] = 0.25 * np.sin(
        2.0 * np.pi * 200.0 * timeline[count // 2 :]
    ) + 0.05
    voiced = np.zeros(count, dtype=bool)
    voiced[: count // 2] = True
    silence = ~voiced
    expected_f0 = np.zeros(count)
    expected_f0[: count // 2] = 200.0
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=count,
        expected_f0_hz=expected_f0,
        expected_voiced_mask=voiced,
        expected_silence_mask=silence,
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert not assessment.passed
    assert {"clipping", "dc_offset"} <= set(assessment.violations)
    assert "headroom" not in assessment.violations
    assert not {"dropout", "false_voicing", "pitch_p90"} & set(
        assessment.violations
    )
    assert metrics.false_voicing_max_run_seconds is not None
    assert metrics.false_voicing_max_run_seconds > 0.12


def test_unclamped_model_float_overflow_is_bounded_global_headroom() -> None:
    """Accept repairable float overflow but reject peaks beyond the six-dB cap."""

    sample_rate = 24_000
    timeline = np.arange(sample_rate, dtype=np.float64) / sample_rate
    repairable = 1.5 * np.sin(2.0 * np.pi * 220.0 * timeline)
    repairable_metrics = analyze_waveform(
        repairable,
        sample_rate=sample_rate,
        expected_samples=len(repairable),
        expected_f0_hz=220.0,
        expected_voiced_mask=True,
        expected_silence_mask=False,
    )
    repairable_assessment = assess_waveform(
        repairable_metrics,
        sample_rate=sample_rate,
    )
    assert repairable_metrics.clipped_fraction == 0.0
    assert repairable_assessment.passed

    excessive = repairable.copy()
    excessive[100] = 2.1
    excessive_metrics = analyze_waveform(
        excessive,
        sample_rate=sample_rate,
        expected_samples=len(excessive),
        expected_f0_hz=220.0,
        expected_voiced_mask=True,
        expected_silence_mask=False,
    )
    excessive_assessment = assess_waveform(excessive_metrics, sample_rate=sample_rate)
    assert not excessive_assessment.passed
    assert "headroom" in excessive_assessment.violations


def test_headroom_six_db_boundary_is_inclusive_and_next_value_fails() -> None:
    """Keep the documented six-dB recovery bound numerically unambiguous."""

    sample_rate = 24_000
    boundary_peak = 0.994 * (10.0 ** (6.0 / 20.0))
    boundary_waveform: np.ndarray | None = None
    for multiplier, should_pass in ((1.0, True), (1.000_002, False)):
        waveform = np.zeros(1_000, dtype=np.float64)
        waveform[100] = boundary_peak * multiplier
        waveform[200] = -boundary_peak * multiplier
        metrics = analyze_waveform(
            waveform,
            sample_rate=sample_rate,
            expected_samples=len(waveform),
        )
        assessment = assess_waveform(metrics, sample_rate=sample_rate)
        assert assessment.passed is should_pass
        assert ("headroom" in assessment.violations) is (not should_pass)
        if should_pass:
            boundary_waveform = waveform
    assert boundary_waveform is not None
    candidate = _candidate(
        "six-db-boundary",
        segment=0,
        start=0,
        waveform=boundary_waveform.astype(np.float32),
    )
    stitched = stitch_candidate_path(
        [candidate],
        total_samples=len(boundary_waveform),
        sample_rate=sample_rate,
    )
    assert float(np.max(np.abs(stitched))) < 0.995


@pytest.mark.parametrize("frequency", [40.0, 55.0, 110.0, 220.0, 440.0])
def test_smooth_over_unity_sines_are_not_flat_top_clipping(frequency: float) -> None:
    """Do not confuse naturally adjacent sine-apex samples with a clipped rail."""

    sample_rate = 24_000
    timeline = np.arange(sample_rate // 5, dtype=np.float64) / sample_rate
    waveform = 1.5 * np.sin(2.0 * np.pi * frequency * timeline)
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
        expected_voiced_mask=True,
    )
    assert metrics.clipped_fraction == 0.0
    assert "clipping" not in assess_waveform(
        metrics,
        sample_rate=sample_rate,
    ).violations


@pytest.mark.parametrize(
    ("frequency", "phase", "amplitude", "add_impulse"),
    [
        (40.0, 0.17, 0.8, False),
        (55.0, 0.79, 0.8, False),
        (20.0, 0.31, 1.0, True),
    ],
)
def test_fp16_quantized_low_sines_are_not_flat_top_clipping(
    frequency: float,
    phase: float,
    amplitude: float,
    add_impulse: bool,
) -> None:
    """Accept smooth FP16 apex dwells, including below an unrelated impulse."""

    sample_rate = 24_000
    timeline = np.arange(sample_rate // 5, dtype=np.float64) / sample_rate
    waveform = (
        amplitude * np.sin(2.0 * np.pi * frequency * timeline + phase)
    ).astype(np.float16).astype(np.float32)
    if add_impulse:
        waveform[0] = 1.5
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
        expected_voiced_mask=True,
    )
    assert metrics.flat_top_max_run_samples == 0
    assert metrics.clipped_fraction == 0.0
    assert "clipping" not in assess_waveform(
        metrics,
        sample_rate=sample_rate,
    ).violations


@pytest.mark.parametrize("fp16_quantized", [False, True])
def test_sustained_float_plateau_is_hard_clipping(
    fp16_quantized: bool,
) -> None:
    """Reject float32 and production-like FP16 clipped sine rails."""

    sample_rate = 24_000
    timeline = np.arange(sample_rate // 5, dtype=np.float64) / sample_rate
    waveform = np.clip(1.5 * np.sin(2.0 * np.pi * 220.0 * timeline), -1.0, 1.0)
    if fp16_quantized:
        waveform = waveform.astype(np.float16).astype(np.float32)
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
        expected_voiced_mask=True,
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.clipped_fraction > 0.0
    assert metrics.flat_top_max_run_samples >= 4
    assert not assessment.passed
    assert "clipping" in assessment.violations


def test_four_sample_flat_top_fails_even_when_song_fraction_is_tiny() -> None:
    """Do not dilute a minimum-length clipping rail inside a long segment."""

    sample_rate = 24_000
    waveform = np.zeros(sample_rate * 2, dtype=np.float64)
    waveform[10_000:10_004] = 1.0
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.clipped_fraction < DEFAULT_QUALITY_THRESHOLDS.max_clipped_fraction
    assert metrics.flat_top_max_run_samples == 4
    assert "clipping" in assessment.violations


@pytest.mark.parametrize("rail_level", [-1.0, 1.0])
def test_over_scale_impulse_does_not_hide_a_unit_full_scale_rail(
    rail_level: float,
) -> None:
    """Detect a clipped unit rail independently of a higher isolated sample."""

    sample_rate = 24_000
    waveform = np.zeros(sample_rate, dtype=np.float64)
    waveform[4_000:4_004] = rail_level
    waveform[12_000] = 1.5
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.flat_top_max_run_samples == 4
    assert "clipping" in assessment.violations


def test_three_sample_near_peak_coincidence_is_not_flat_top_clipping() -> None:
    """Require four rail samples so a short sampled apex remains acceptable."""

    sample_rate = 24_000
    waveform = np.zeros(sample_rate, dtype=np.float64)
    waveform[10_000:10_003] = 1.0
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.flat_top_max_run_samples == 0
    assert "clipping" not in assessment.violations


def test_lower_constant_region_is_not_a_rail_below_unrelated_impulse() -> None:
    """Bind plateau evidence to the peak instead of any high quantile level."""

    sample_rate = 24_000
    waveform = np.zeros(sample_rate, dtype=np.float64)
    waveform[4_000:4_020] = 0.15
    waveform[12_000] = 1.5
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=len(waveform),
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.flat_top_max_run_samples == 0
    assert "clipping" not in assessment.violations


def test_realistic_harmonics_and_consonant_noise_are_ranking_only() -> None:
    """Accept voice-like vibrato/noise despite imperfect proxy pitch evidence."""

    sample_rate = 24_000
    count = sample_rate * 2
    timeline = np.arange(count, dtype=np.float64) / sample_rate
    cents = 55.0 * np.sin(2.0 * np.pi * 5.2 * timeline)
    frequency = 220.0 * np.power(2.0, cents / 1_200.0)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    rng = np.random.default_rng(20261009)
    waveform = (
        0.14 * np.sin(phase)
        + 0.035 * np.sin(2.0 * phase)
        + 0.004 * rng.standard_normal(count)
    ).astype(np.float32)
    explicit_silence = np.zeros(count, dtype=bool)
    explicit_silence[count // 3 : count // 3 + sample_rate // 8] = True
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=count,
        expected_f0_hz=196.0,
        expected_voiced_mask=True,
        expected_silence_mask=explicit_silence,
    )
    assessment = assess_waveform(metrics, sample_rate=sample_rate)
    assert metrics.pitch_p90_abs_cents is not None
    assert metrics.pitch_p90_abs_cents > 120.0
    assert metrics.false_voicing_rate is not None
    assert assessment.passed
    assert assessment.local_loss > 0.0


def test_nonpitched_frames_are_not_implicitly_digital_silence() -> None:
    """Avoid treating consonants and breaths as score silence without evidence."""

    samples = np.zeros(24_000, dtype=np.float32)
    metrics = analyze_waveform(
        samples,
        sample_rate=24_000,
        expected_samples=len(samples),
        expected_voiced_mask=False,
    )
    assert metrics.expected_silence_frames == 0


def test_three_minute_analysis_keeps_linear_fft_frame_cost() -> None:
    """Analyze a three-minute signal within the 20 ms deterministic frame bound."""

    sample_rate = 24_000
    samples = np.zeros(sample_rate * 180, dtype=np.float32)
    started = time.perf_counter()
    metrics = analyze_waveform(
        samples,
        sample_rate=sample_rate,
        expected_samples=len(samples),
        expected_voiced_mask=True,
    )
    elapsed = time.perf_counter() - started
    assert metrics.expected_voiced_frames == 9_000
    # This broad guard catches accidental quadratic full-frame correlation while
    # leaving generous headroom for shared CI hosts.
    assert elapsed < 10.0


def test_false_voicing_runs_do_not_join_across_expected_speech() -> None:
    """Keep disjoint silence defects separate on the absolute frame timeline."""

    sample_rate = 8_000
    count = sample_rate
    timeline = np.arange(count) / sample_rate
    waveform = 0.2 * np.sin(2.0 * np.pi * 200.0 * timeline)
    voiced = np.zeros(count, dtype=bool)
    voiced[count // 5 : -(count // 5)] = True
    expected_f0 = np.where(voiced, 200.0, 0.0)
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=count,
        expected_f0_hz=expected_f0,
        expected_voiced_mask=voiced,
        expected_silence_mask=~voiced,
    )
    assert metrics.false_voicing_max_run_seconds is not None
    assert 0.12 < metrics.false_voicing_max_run_seconds < 0.30


def test_spectral_flux_uses_a_sample_rate_independent_scale() -> None:
    """Measure repeated timbre jumps on a useful zero-to-one flux scale."""

    sample_rate = 8_000
    count = sample_rate
    timeline = np.arange(count) / sample_rate
    section = (np.arange(count) // int(0.08 * sample_rate)) % 2
    frequency = np.where(section == 0, 180.0, 1_200.0)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    waveform = 0.2 * np.sin(phase)
    metrics = analyze_waveform(
        waveform,
        sample_rate=sample_rate,
        expected_samples=count,
        expected_voiced_mask=True,
    )
    assert metrics.spectral_flux_p95 > 0.10


def test_hard_gate_precedes_global_dynamic_programming() -> None:
    """Never select a failed low-loss candidate over a safe alternative."""

    wave = np.full(100, 0.01, dtype=np.float32)
    rejected = _candidate(
        "a-rejected",
        segment=0,
        start=0,
        waveform=wave,
        loss=0.0,
        passed=False,
    )
    safe = _candidate("b-safe", segment=0, start=0, waveform=wave, loss=0.2)
    selected = select_candidate_path([[rejected, safe]], sample_rate=1_000)
    assert [candidate.candidate_id for candidate in selected] == ["b-safe"]


def test_dynamic_programming_uses_transition_cost_and_stable_ties() -> None:
    """Choose a coherent prompt path and use candidate id as final tie-break."""

    first_wave = np.linspace(0.0, 0.02, 100, dtype=np.float32)
    continuation = np.linspace(0.02, 0.03, 100, dtype=np.float32)
    discontinuity = np.linspace(-0.5, -0.4, 100, dtype=np.float32)
    first = _candidate(
        "a0",
        segment=0,
        start=0,
        waveform=first_wave,
        prompt="neutral",
    )
    coherent = _candidate(
        "b0",
        segment=1,
        start=100,
        waveform=continuation,
        prompt="neutral",
        loss=0.1,
    )
    unsafe = _candidate(
        "a1",
        segment=1,
        start=100,
        waveform=discontinuity,
        prompt="bright",
        loss=0.0,
    )
    selected = select_candidate_path(
        [[first], [unsafe, coherent]],
        sample_rate=1_000,
    )
    assert [candidate.candidate_id for candidate in selected] == ["a0", "b0"]


@pytest.mark.parametrize(("gap", "expected_id"), [(80, "a-switch"), (81, "z-same")])
def test_eighty_ms_gap_clears_only_waveform_boundary_cost(
    gap: int,
    expected_id: str,
) -> None:
    """Retain reference consistency when silence makes edge evidence irrelevant."""

    first_wave = np.full(20, 0.5, dtype=np.float32)
    start = len(first_wave) + gap
    first = _candidate(
        "first",
        segment=0,
        start=0,
        waveform=first_wave,
        prompt="neutral",
        loss=0.0,
    )
    switched = _candidate(
        "a-switch",
        segment=1,
        start=start,
        waveform=np.full(20, 0.5, dtype=np.float32),
        prompt="bright",
        loss=0.0,
    )
    same_prompt = _candidate(
        "z-same",
        segment=1,
        start=start,
        waveform=np.full(20, 0.05, dtype=np.float32),
        prompt="neutral",
        loss=0.01,
    )

    selected = select_candidate_path(
        [[first], [switched, same_prompt]],
        sample_rate=1_001,
    )

    assert selected[-1].candidate_id == expected_id


def test_dynamic_programming_rejects_ambiguous_candidate_identity_and_timing() -> None:
    """Reject groups that cannot map deterministically to one target interval."""

    wave = np.full(10, 0.01, dtype=np.float32)
    first = _candidate("same", segment=0, start=0, waveform=wave)
    duplicate = _candidate("same", segment=1, start=10, waveform=wave)
    with pytest.raises(ValueError, match="globally unique"):
        select_candidate_path([[first], [duplicate]], sample_rate=1_000)
    shifted = _candidate("shifted", segment=0, start=1, waveform=wave)
    with pytest.raises(ValueError, match="share an interval"):
        select_candidate_path([[first, shifted]], sample_rate=1_000)


def test_dynamic_programming_uses_headroom_only_as_a_stable_tie_break() -> None:
    """Prefer the equally acoustic path that attenuates the whole song less."""

    wave = np.linspace(0.0, 0.01, 100, dtype=np.float32)
    louder = _candidate(
        "a-louder",
        segment=0,
        start=0,
        waveform=wave,
        headroom=4.0,
    )
    quieter = _candidate(
        "z-quieter",
        segment=0,
        start=0,
        waveform=wave,
        headroom=1.0,
    )
    selected = select_candidate_path([[louder, quieter]], sample_rate=1_000)
    assert [candidate.candidate_id for candidate in selected] == ["z-quieter"]


def test_dynamic_programming_retains_lexicographic_headroom_pareto_prefix() -> None:
    """Keep a lexicographic prefix when a future peak erases its headroom cost."""

    wave = np.zeros(10, dtype=np.float32)
    lexicographic = _candidate(
        "a-higher-prefix-headroom",
        segment=0,
        start=0,
        waveform=wave,
        loss=0.0,
        headroom=1.0,
    )
    lower_headroom = _candidate(
        "z-lower-prefix-headroom",
        segment=0,
        start=0,
        waveform=wave,
        loss=0.0,
        headroom=0.0,
    )
    middle = _candidate(
        "middle",
        segment=1,
        start=100,
        waveform=wave,
        loss=0.0,
    )
    future_peak = _candidate(
        "future-peak",
        segment=2,
        start=200,
        waveform=wave,
        loss=0.0,
        headroom=2.0,
    )
    selected = select_candidate_path(
        [[lexicographic, lower_headroom], [middle], [future_peak]],
        sample_rate=1_000,
    )
    assert [candidate.candidate_id for candidate in selected] == [
        "a-higher-prefix-headroom",
        "middle",
        "future-peak",
    ]


def test_stitch_places_absolute_offsets_and_applies_one_global_gain() -> None:
    """Preserve silent gaps while ramping segment edges safely to zero."""

    first = _candidate(
        "first",
        segment=0,
        start=0,
        waveform=np.full(4, 0.1, dtype=np.float32),
    )
    second = _candidate(
        "second",
        segment=1,
        start=6,
        waveform=np.full(3, 0.2, dtype=np.float32),
    )
    stitched = stitch_candidate_path(
        [first, second],
        total_samples=10,
        sample_rate=1_000,
        global_gain_db=-3.0,
    )
    gain = 10.0 ** (-3.0 / 20.0)
    np.testing.assert_allclose(stitched[:3], 0.1 * gain, rtol=1e-6)
    assert stitched[3] == 0.0
    np.testing.assert_array_equal(stitched[4:6], np.zeros(2, dtype=np.float32))
    np.testing.assert_allclose(stitched[6:9], [0.0, 0.2 * gain, 0.0], rtol=1e-6)
    assert stitched.shape == (10,)


def test_stitch_uses_one_bounded_gain_for_model_float_headroom() -> None:
    """Attenuate an entire selected path once instead of clipping each segment."""

    candidate = _candidate(
        "overflow",
        segment=0,
        start=0,
        waveform=np.asarray([0.0, 1.5, -0.75, 0.0], dtype=np.float32),
    )
    stitched = stitch_candidate_path(
        [candidate],
        total_samples=4,
        sample_rate=1_000,
    )
    assert float(np.max(np.abs(stitched))) == pytest.approx(0.994, abs=1e-6)
    assert stitched[2] == pytest.approx(-0.497, abs=1e-6)


def test_contiguous_independent_segments_ramp_to_zero_without_moving_time() -> None:
    """Keep a discontinuous contiguous boundary safe without overlap or rejection."""

    first = _candidate(
        "first-contiguous",
        segment=0,
        start=0,
        waveform=np.full(20, 0.8, dtype=np.float32),
    )
    second = _candidate(
        "second-contiguous",
        segment=1,
        start=20,
        waveform=np.full(20, -0.05, dtype=np.float32),
    )
    selected = select_candidate_path([[first], [second]], sample_rate=1_000)
    stitched = stitch_candidate_path(
        selected,
        total_samples=40,
        sample_rate=1_000,
    )
    assert stitched.shape == (40,)
    assert stitched[19] == 0.0
    assert stitched[20] == 0.0
    assert stitched[18] > 0.0
    assert stitched[21] < 0.0


def test_stitch_rejects_overlap_bad_length_clipping_and_failed_candidates() -> None:
    """Fail instead of hiding unsafe timing, quality, or amplitude defects."""

    safe = _candidate(
        "safe",
        segment=0,
        start=0,
        waveform=np.full(4, 2.1, dtype=np.float32),
    )
    overlap = _candidate(
        "overlap",
        segment=1,
        start=3,
        waveform=np.full(4, 0.1, dtype=np.float32),
    )
    failed = replace(safe, candidate_id="failed", assessment=_assessment(passed=False))
    bad_length = replace(safe, candidate_id="short", end_sample=5)
    with pytest.raises(ValueError, match="overlap"):
        stitch_candidate_path([safe, overlap], total_samples=10, sample_rate=1_000)
    with pytest.raises(QualitySelectionError):
        stitch_candidate_path([failed], total_samples=10, sample_rate=1_000)
    with pytest.raises(ValueError, match="exact interval"):
        stitch_candidate_path([bad_length], total_samples=10, sample_rate=1_000)
    with pytest.raises(QualitySelectionError, match="At least one"):
        stitch_candidate_path([], total_samples=10, sample_rate=1_000)
    with pytest.raises(ValueError, match="headroom capacity"):
        stitch_candidate_path(
            [safe],
            total_samples=4,
            sample_rate=1_000,
        )
