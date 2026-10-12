"""Test strict synchronized-lyric correction for SoulX target metadata."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Sequence
from typing import Any

import pytest

from scripts import song_lyrics_alignment as alignment
from scripts.song_lyrics_alignment import (
    LyricsAlignmentError,
    build_mandarin_hotword,
    parse_synced_lrc,
    plan_metadata_lyric_corrections,
    render_corrected_metadata,
)


def _metadata_segment(
    text: str,
    note_type: str,
    *,
    start_ms: int = 0,
    end_ms: int = 4_000,
) -> dict[str, object]:
    """Build one internally consistent Mandarin metadata test segment."""

    slot_count = len(text.split())
    return {
        "index": f"target_{start_ms}_{end_ms}",
        "language": "Mandarin",
        "time": [start_ms, end_ms],
        "duration": " ".join("0.50" for _index in range(slot_count)),
        "text": text,
        "phoneme": " ".join("old" for _index in range(slot_count)),
        "note_pitch": " ".join(
            "0" if token == "<SP>" else "60" for token in text.split()
        ),
        "note_type": note_type,
        "f0": "0.0 120.0 121.0",
        "private_fixture": "preserve-me",
    }


def _assert_code(expected_code: str, callback: Any) -> None:
    """Assert that one unsafe input fails with its stable category code."""

    with pytest.raises(LyricsAlignmentError) as captured:
        callback()
    assert captured.value.code == expected_code


def test_parse_lrc_normalizes_tags_offsets_duplicates_and_traditional_text() -> None:
    """Expand repeated timestamps while normalizing bounded Mandarin content."""

    parsed = parse_synced_lrc(
        "\ufeff[ar:測試]\n[offset:+100]\n"
        "[00:00.00][00:01.00]繁體，中文！\n"
        "[00:01.00]繁體，中文！\n"
        "[00:02.50]\n",
        4_000,
    )

    assert [(line.start_ms, line.end_ms) for line in parsed] == [
        (100, 1_100),
        (1_100, 2_600),
        (2_600, 4_000),
    ]
    assert parsed[0].text == "繁体，中文！"
    assert parsed[0].tokens == ("繁", "体", "中", "文")
    assert parsed[1].tokens == parsed[0].tokens
    assert parsed[2].tokens == ()


@pytest.mark.parametrize(
    ("lrc", "duration_ms", "code"),
    [
        ("[ar:test]", 1_000, "no_synced_lyrics"),
        ("没有时间", 1_000, "invalid_lrc"),
        ("[00:01.00]越界", 1_000, "invalid_lrc"),
        ("[offset:+70000]\n[00:00.00]歌词", 2_000, "invalid_lrc"),
        ("[00:00.00]hello", 2_000, "unsupported_lyrics"),
    ],
)
def test_parse_lrc_rejects_unbounded_or_unsynchronized_content(
    lrc: str,
    duration_ms: int,
    code: str,
) -> None:
    """Fail closed for missing synchronization, unsafe timing, and mixed script."""

    _assert_code(code, lambda: parse_synced_lrc(lrc, duration_ms))


def test_parse_lrc_rejects_conflicting_duplicate_timestamp_text() -> None:
    """Reject a timestamp whose competing text cannot be resolved safely."""

    _assert_code(
        "ambiguous_lrc",
        lambda: parse_synced_lrc(
            "[00:00.00]第一句\n[00:00.00]第二句",
            2_000,
        ),
    )


def test_hotword_is_simplified_deduplicated_bounded_and_control_free() -> None:
    """Build one safe FunASR bias without trusting it as forced alignment."""

    hotword = build_mandarin_hotword(
        "[00:00.00]繁體繁體\n[00:01.00]中文繁體",
        2_000,
        maximum_characters=7,
    )

    assert hotword == "繁 体 中 文"
    assert len(hotword) == 7
    assert len(hotword.split()) == len(set(hotword.split()))
    assert not any(character.isspace() and character != " " for character in hotword)

    _assert_code(
        "unsupported_lyrics",
        lambda: build_mandarin_hotword("[00:00.00]歌\x01词", 1_000),
    )


@pytest.mark.parametrize("limit", [0, 4_096, True])
def test_hotword_rejects_invalid_length_bounds(limit: object) -> None:
    """Reject Boolean, empty, and excessively large decoder-bias bounds."""

    _assert_code(
        "invalid_hotword_limit",
        lambda: build_mandarin_hotword(
            "[00:00.00]歌词",
            1_000,
            maximum_characters=limit,  # type: ignore[arg-type]
        ),
    )


def test_plan_and_render_correct_extra_onset_and_propagate_sustain() -> None:
    """Convert an ASR-split onset to a sustain and regenerate official phonemes."""

    metadata = [
        _metadata_segment(
            "<SP> 我 我 爱 爱 你 <SP>",
            "1 2 2 2 3 2 1",
        )
    ]
    original = deepcopy(metadata)

    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]我爱你。",
        4_000,
        plain_lyrics="我爱你",
    )

    correction = plan.corrections[0]
    assert correction.authoritative_tokens == ("我", "爱", "你")
    assert correction.corrected_note_tokens == (
        "<SP>",
        "我",
        "我",
        "爱",
        "爱",
        "你",
        "<SP>",
    )
    assert correction.corrected_note_types == (1, 2, 3, 2, 3, 2, 1)
    assert correction.exact_match_ratio == 1.0
    assert correction.alignment_cost_ratio == 0.0

    def official_g2p(tokens: Sequence[str], language: str) -> list[str]:
        """Stand in for SoulX's official G2P at the explicit render seam."""

        assert language == "Mandarin"
        return [token if token == "<SP>" else f"zh_{token}" for token in tokens]

    rendered = render_corrected_metadata(metadata, plan, official_g2p)

    assert metadata == original
    assert rendered[0]["text"] == "<SP> 我 我 爱 爱 你 <SP>"
    assert rendered[0]["note_type"] == "1 2 3 2 3 2 1"
    assert rendered[0]["phoneme"] == (
        "<SP> zh_我 zh_我 zh_爱 zh_爱 zh_你 <SP>"
    )
    for field in ("duration", "note_pitch", "time", "f0", "private_fixture"):
        assert rendered[0][field] == original[0][field]


def test_uneven_boundary_line_stays_with_the_dominant_overlap_segment() -> None:
    """Keep a short boundary overlap from duplicating authoritative tokens."""

    metadata = [
        _metadata_segment("你 好", "2 2"),
        _metadata_segment("世 界", "2 2", start_ms=4_000, end_ms=8_000),
    ]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]你好\n[00:03.50]世界",
        8_000,
        plain_lyrics="你好\n世界",
    )

    assert plan.corrections[0].source_line_indexes == (0,)
    assert plan.corrections[0].authoritative_tokens == ("你", "好")
    assert plan.corrections[1].source_line_indexes == (1,)
    assert plan.corrections[1].authoritative_tokens == ("世", "界")


def test_cross_segment_line_is_split_monotonically_and_rendered_with_g2p() -> None:
    """Partition one long line across adjacent slices without duplicating it."""

    metadata = [
        _metadata_segment("你 好", "2 2"),
        _metadata_segment("世 界", "2 2", start_ms=4_000, end_ms=8_000),
    ]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]你好世界",
        8_000,
        plain_lyrics="你好世界",
    )

    assert plan.corrections[0].source_line_indexes == (0,)
    assert plan.corrections[0].authoritative_tokens == ("你", "好")
    assert plan.corrections[1].source_line_indexes == (0,)
    assert plan.corrections[1].authoritative_tokens == ("世", "界")
    assert tuple(
        token
        for correction in plan.corrections
        for token in correction.authoritative_tokens
    ) == ("你", "好", "世", "界")

    rendered = render_corrected_metadata(
        metadata,
        plan,
        lambda tokens, _language: [f"zh_{token}" for token in tokens],
    )

    assert rendered[0]["text"] == "你 好"
    assert rendered[0]["phoneme"] == "zh_你 zh_好"
    assert rendered[1]["text"] == "世 界"
    assert rendered[1]["phoneme"] == "zh_世 zh_界"


def test_cross_segment_melisma_keeps_the_complete_line_with_its_onsets() -> None:
    """Carry a finished line across a slice without moving its final words."""

    metadata = [
        _metadata_segment("无 可 奈 何", "2 2 2 2"),
        _metadata_segment(
            "我 可 爱 的 不 知",
            "2 2 2 2 2 2",
            start_ms=4_000,
            end_ms=8_000,
        ),
    ]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]无可奈何\n[00:06.40]不知",
        8_000,
        plain_lyrics="无可奈何不知",
        minimum_exact_match_ratio=0.50,
    )

    assert plan.corrections[0].authoritative_tokens == ("无", "可", "奈", "何")
    assert plan.corrections[1].authoritative_tokens == ("不", "知")
    assert plan.corrections[1].corrected_note_tokens == (
        "何",
        "何",
        "何",
        "何",
        "不",
        "知",
    )
    assert plan.corrections[1].corrected_note_types == (2, 3, 3, 3, 2, 2)
    assert plan.corrections[1].source_line_indexes == (0, 1)


def test_cross_segment_split_respects_each_segments_remaining_capacity() -> None:
    """Move overflow forward while preserving the authoritative token order."""

    metadata = [
        _metadata_segment("春", "2", end_ms=3_000),
        _metadata_segment("夏 秋 冬", "2 2 2", start_ms=3_000, end_ms=6_000),
    ]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]春夏秋冬",
        6_000,
    )

    assert plan.corrections[0].authoritative_tokens == ("春",)
    assert plan.corrections[1].authoritative_tokens == ("夏", "秋", "冬")


def test_line_interval_selects_the_group_after_a_segment_boundary() -> None:
    """Use actual note onset time instead of dividing tokens by overlap area."""

    metadata = [
        _metadata_segment("旧", "2", end_ms=2_000),
        _metadata_segment("你", "2", start_ms=2_000, end_ms=6_000),
    ]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:02.00]你\n[00:06.00]",
        8_000,
    )

    assert plan.corrections[0].authoritative_tokens == ()
    assert plan.corrections[0].corrected_note_tokens == ("旧",)
    assert plan.corrections[1].authoritative_tokens == ("你",)
    assert plan.corrections[1].corrected_note_tokens == ("你",)


def test_nonempty_timed_line_without_metadata_overlap_fails_closed() -> None:
    """Do not silently classify unmatched text as an unsung intro or outro."""

    metadata = [
        _metadata_segment("你", "2", start_ms=2_000, end_ms=4_000),
    ]
    _assert_code(
        "unassigned_lyrics",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]我\n[00:01.00]\n[00:02.00]你",
            4_000,
        ),
    )


def test_nonempty_timed_outro_without_metadata_overlap_fails_closed() -> None:
    """Require auditable ownership for text after the final target segment."""

    metadata = [
        _metadata_segment("你", "2", start_ms=0, end_ms=2_000),
    ]
    _assert_code(
        "unassigned_lyrics",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]你\n[00:02.00]我",
            4_000,
        ),
    )


def test_authoritative_lyrics_cannot_exceed_note_onset_capacity() -> None:
    """Reject canonical syllables that cannot fit the detected onset groups."""

    metadata = [_metadata_segment("我 爱", "2 2")]
    _assert_code(
        "insufficient_note_capacity",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]我爱你",
            4_000,
        ),
    )


def test_timed_lyrics_cannot_be_assigned_to_a_silence_only_segment() -> None:
    """Reject authoritative words where target metadata has no voiced capacity."""

    metadata = [_metadata_segment("<SP>", "1")]
    _assert_code(
        "insufficient_note_capacity",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]我",
            4_000,
        ),
    )


def test_low_asr_coverage_fails_instead_of_overwriting_unrelated_notes() -> None:
    """Reject a same-length lyric whose observed characters do not agree."""

    metadata = [_metadata_segment("他 好 呀", "2 2 2")]
    _assert_code(
        "low_coverage",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]我爱你",
            4_000,
        ),
    )


def test_equal_text_and_timing_cost_paths_fail_as_ambiguous() -> None:
    """Reject equal text paths when no trusted timing can distinguish them."""

    _assert_code(
        "ambiguous_alignment",
        lambda: alignment._align_groups(
            ("我", "他", "她"),
            ("我", "你"),
            minimum_exact_match_ratio=0.0,
            maximum_cost_ratio=1.0,
        ),
    )


def test_leftmost_exact_tiebreak_does_not_hide_a_mismatched_onset() -> None:
    """Keep primary-equal paths ambiguous when either guesses an onset word."""

    _assert_code(
        "ambiguous_alignment",
        lambda: alignment._align_groups(
            ("我", "我", "你", "你"),
            ("我", "他", "你"),
            minimum_exact_match_ratio=0.0,
            maximum_cost_ratio=1.0,
            group_onsets_ms=(0, 100, 200, 300),
            canonical_onsets_ms=(0, None, None),
        ),
    )


def test_note_and_lrc_timing_break_an_equal_text_cost_safely() -> None:
    """Use exact LRC line starts only after text leaves two optimal paths."""

    labels, note_types, coverage, cost_ratio = alignment._align_groups(
        ("我", "他", "她"),
        ("我", "你"),
        minimum_exact_match_ratio=0.0,
        maximum_cost_ratio=1.0,
        group_onsets_ms=(0, 1_300, 2_700),
        canonical_onsets_ms=(0, 2_500),
    )

    assert labels == ("我", "我", "你")
    assert note_types == (2, 3, 2)
    assert coverage == 0.5
    assert cost_ratio == 7 / 11


def test_repeated_exact_line_uses_its_leftmost_observed_tokens() -> None:
    """Treat a later exact repetition as trailing melisma, not shifted words."""

    labels, note_types, coverage, cost_ratio = alignment._align_groups(
        ("无", "可", "奈", "何", "无", "可", "奈", "何", "不"),
        ("无", "可", "奈", "何", "不"),
        minimum_exact_match_ratio=0.0,
        maximum_cost_ratio=1.0,
        group_onsets_ms=(0, 100, 200, 300, 1_000, 1_100, 1_200, 1_300, 2_000),
        canonical_onsets_ms=(0, None, None, None, 2_000),
    )

    assert labels == ("无", "可", "奈", "何", "何", "何", "何", "何", "不")
    assert note_types == (2, 2, 2, 2, 3, 3, 3, 3, 2)
    assert coverage == 1.0
    assert cost_ratio == 9 / 32


def test_planner_routes_segment_and_lrc_timing_into_alignment() -> None:
    """Resolve a public planning ambiguity with derived note and lyric times."""

    plan = plan_metadata_lyric_corrections(
        [_metadata_segment("我 他 她", "2 2 2")],
        "[00:00.00]我\n[00:02.50]你",
        4_000,
        minimum_exact_match_ratio=0.0,
        maximum_cost_ratio=1.0,
    )

    correction = plan.corrections[0]
    assert correction.corrected_note_tokens == ("我", "我", "你")
    assert correction.corrected_note_types == (2, 3, 2)


def test_unique_text_path_cannot_override_synchronized_line_timing() -> None:
    """Reject an exact word that occurs far outside its LRC line window."""

    metadata = [_metadata_segment("甲 乙", "2 2", end_ms=2_000)]
    metadata[0]["duration"] = "0.05 0.95"

    _assert_code(
        "line_timing_mismatch",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]甲\n[00:01.00]乙\n[00:02.00]",
            2_100,
        ),
    )


@pytest.mark.parametrize("second_onset_ms", [932, 1_466])
def test_line_start_window_allows_observed_lrc_jitter(second_onset_ms: int) -> None:
    """Accept the measured early and late line-start drift from the real song."""

    labels, note_types, coverage, cost_ratio = alignment._align_groups(
        ("甲", "乙"),
        ("甲", "乙"),
        minimum_exact_match_ratio=1.0,
        maximum_cost_ratio=0.0,
        group_onsets_ms=(0, second_onset_ms),
        canonical_onsets_ms=(0, 1_000),
        canonical_windows_ms=((-100, 601), (900, 1_601)),
    )

    assert labels == ("甲", "乙")
    assert note_types == (2, 2)
    assert coverage == 1.0
    assert cost_ratio == 0.0


def test_planner_keeps_an_onset_just_before_the_first_lyric_timestamp() -> None:
    """Pass a non-overlapping 68 ms early onset through both timing gates."""

    plan = plan_metadata_lyric_corrections(
        [_metadata_segment("甲", "2", start_ms=932, end_ms=999)],
        "[00:01.00]甲\n[00:01.90]",
        2_000,
    )

    assert plan.corrections[0].authoritative_tokens == ("甲",)


def test_planner_allows_an_onset_just_after_an_internal_line_boundary() -> None:
    """Keep the documented 99 ms late allowance in the public planner path."""

    metadata = [_metadata_segment("甲 乙 丙", "2 2 2", end_ms=1_300)]
    metadata[0]["duration"] = "1.099 0.101 0.100"
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]甲乙\n[00:01.00]丙\n[00:01.30]",
        1_400,
    )

    assert plan.corrections[0].authoritative_tokens == ("甲", "乙", "丙")


@pytest.mark.parametrize(
    ("second_onset_ms", "expected_code"),
    [(1_099, None), (1_101, "line_timing_mismatch")],
)
def test_previous_line_onset_cannot_move_into_the_next_line(
    second_onset_ms: int,
    expected_code: str | None,
) -> None:
    """Enforce the narrow cross-line allowance without constraining sustains."""

    callback = lambda: alignment._align_groups(  # noqa: E731
        ("甲", "乙", "丙"),
        ("甲", "乙", "丙"),
        minimum_exact_match_ratio=1.0,
        maximum_cost_ratio=0.0,
        group_onsets_ms=(0, second_onset_ms, 1_200),
        canonical_onsets_ms=(0, None, 1_000),
        canonical_windows_ms=((-100, 601), (-100, 1_100), (900, 1_601)),
    )
    if expected_code is not None:
        _assert_code(expected_code, callback)
        return

    labels, note_types, coverage, cost_ratio = callback()
    assert labels == ("甲", "乙", "丙")
    assert note_types == (2, 2, 2)
    assert coverage == 1.0
    assert cost_ratio == 0.0


def test_line_end_guess_cannot_resolve_an_intraline_ambiguity() -> None:
    """Keep a short lyric before a long pause ambiguous without word timing."""

    _assert_code(
        "ambiguous_alignment",
        lambda: plan_metadata_lyric_corrections(
            [_metadata_segment("我 他 她", "2 2 2")],
            "[00:00.00]我你\n[00:03.90]",
            4_000,
            minimum_exact_match_ratio=0.0,
            maximum_cost_ratio=1.0,
        ),
    )


def test_alignment_accepts_the_exact_dynamic_programming_cell_bound() -> None:
    """Allow the documented cell ceiling so the guard is not off by one."""

    # 512 rows by 512 columns is exactly the closed 262,144-cell limit.
    token_count = 511
    labels, note_types, coverage, cost_ratio = alignment._align_groups(
        ("我",) * token_count,
        ("我",) * token_count,
        minimum_exact_match_ratio=1.0,
        maximum_cost_ratio=0.0,
    )

    assert labels == ("我",) * token_count
    assert note_types == (2,) * token_count
    assert coverage == 1.0
    assert cost_ratio == 0.0


def test_alignment_rejects_oversized_matrix_before_table_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed before allocating any row of an oversized DP matrix."""

    original_range = range
    oversized_row_length = 513
    allocation_started = False

    def _observed_range(*args: int) -> range:
        """Detect the first matrix-row comprehension without changing behavior."""

        nonlocal allocation_started
        if args == (oversized_row_length,):
            allocation_started = True
        return original_range(*args)

    monkeypatch.setattr(alignment, "range", _observed_range, raising=False)

    _assert_code(
        "alignment_too_large",
        lambda: alignment._align_groups(
            ("我",) * 512,
            ("我",) * 511,
            minimum_exact_match_ratio=0.0,
            maximum_cost_ratio=1.0,
        ),
    )
    assert allocation_started is False


def test_plain_and_synchronized_lyrics_must_describe_the_same_song_text() -> None:
    """Reject conflicting LRCLIB fields before either can alter note metadata."""

    metadata = [_metadata_segment("我 爱 你", "2 2 2")]
    _assert_code(
        "lyrics_source_mismatch",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]我爱你",
            4_000,
            plain_lyrics="我想你",
        ),
    )


def test_each_synthesis_segment_must_meet_local_alignment_thresholds() -> None:
    """Prevent a strong neighboring slice from masking one unrelated slice."""

    metadata = [
        _metadata_segment("我 爱 中 国", "2 2 2 2"),
        _metadata_segment("他", "2", start_ms=4_000, end_ms=5_000),
    ]
    _assert_code(
        "low_coverage",
        lambda: plan_metadata_lyric_corrections(
            metadata,
            "[00:00.00]我爱中国\n[00:04.00]你",
            5_001,
        ),
    )


def test_unlabelled_voiced_segment_preserves_its_transcribed_text() -> None:
    """Keep ad-libs in an explicit empty LRC interval outside correction."""

    metadata = [
        _metadata_segment("我 爱", "2 2"),
        _metadata_segment("你 好", "2 2", start_ms=5_000, end_ms=7_000),
    ]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]我爱\n[00:04.00]",
        7_000,
    )

    assert plan.corrections[0].authoritative_tokens == ("我", "爱")
    assert plan.corrections[1].authoritative_tokens == ()
    assert plan.corrections[1].corrected_note_tokens == ("你", "好")
    assert plan.corrections[1].source_line_indexes == ()


def test_invalid_note_group_and_array_shapes_are_rejected() -> None:
    """Reject sustain-without-onset and mismatched parallel metadata arrays."""

    orphan_sustain = [_metadata_segment("你 好", "3 2")]
    _assert_code(
        "invalid_metadata",
        lambda: plan_metadata_lyric_corrections(
            orphan_sustain,
            "[00:00.00]你好",
            4_000,
        ),
    )

    mismatched = _metadata_segment("你 好", "2 2")
    mismatched["duration"] = "0.50"
    _assert_code(
        "invalid_metadata",
        lambda: plan_metadata_lyric_corrections(
            [mismatched],
            "[00:00.00]你好",
            4_000,
        ),
    )


def test_render_rejects_stale_plan_and_malformed_g2p_output() -> None:
    """Require source identity and one valid official phoneme per note slot."""

    metadata = [_metadata_segment("我 爱 你", "2 2 2")]
    plan = plan_metadata_lyric_corrections(
        metadata,
        "[00:00.00]我爱你",
        4_000,
    )

    changed = deepcopy(metadata)
    changed[0]["text"] = "我 想 你"
    _assert_code(
        "stale_plan",
        lambda: render_corrected_metadata(
            changed,
            plan,
            lambda tokens, _language: list(tokens),
        ),
    )
    changed_timing = deepcopy(metadata)
    changed_timing[0]["duration"] = "0.40 0.50 0.60"
    _assert_code(
        "stale_plan",
        lambda: render_corrected_metadata(
            changed_timing,
            plan,
            lambda tokens, _language: list(tokens),
        ),
    )
    changed_interval = deepcopy(metadata)
    changed_interval[0]["time"] = [0, 3_999]
    _assert_code(
        "stale_plan",
        lambda: render_corrected_metadata(
            changed_interval,
            plan,
            lambda tokens, _language: list(tokens),
        ),
    )
    for invalid_interval in ([False, 4_000], [0.0, 4_000.0]):
        changed_interval_type = deepcopy(metadata)
        changed_interval_type[0]["time"] = invalid_interval
        _assert_code(
            "stale_plan",
            lambda changed=changed_interval_type: render_corrected_metadata(
                changed,
                plan,
                lambda tokens, _language: list(tokens),
            ),
        )
    changed_language = deepcopy(metadata)
    changed_language[0]["language"] = "English"
    _assert_code(
        "stale_plan",
        lambda: render_corrected_metadata(
            changed_language,
            plan,
            lambda tokens, _language: list(tokens),
        ),
    )
    _assert_code(
        "g2p_failed",
        lambda: render_corrected_metadata(
            metadata,
            plan,
            lambda _tokens, _language: ["too-short"],
        ),
    )
    _assert_code(
        "g2p_unavailable",
        lambda: render_corrected_metadata(metadata, plan, None),  # type: ignore[arg-type]
    )
