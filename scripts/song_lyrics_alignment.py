"""Align bounded synchronized Mandarin lyrics to SoulX-Singer metadata.

The module intentionally stops before vendor-specific grapheme-to-phoneme
conversion.  It produces an auditable correction plan first, then requires a
caller-supplied G2P callback when rendering inference-ready metadata.  This
two-step boundary prevents corrected text from being paired with stale or
invented phonemes when the official SoulX runtime is unavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Final, NoReturn

from localization import simplify_chinese_text


__all__ = [
    "LyricsAlignmentError",
    "LyricsAlignmentPlan",
    "SegmentLyricCorrection",
    "TimedLyricLine",
    "build_mandarin_hotword",
    "parse_synced_lrc",
    "plan_metadata_lyric_corrections",
    "render_corrected_metadata",
]


_MAX_LRC_BYTES: Final = 1_048_576
_MAX_LRC_LINES: Final = 10_000
_MAX_LINE_CHARACTERS: Final = 2_048
_MAX_TRACK_DURATION_MS: Final = 24 * 60 * 60 * 1_000
_MAX_OFFSET_MS: Final = 60_000
_MAX_METADATA_SEGMENTS: Final = 10_000
_MAX_METADATA_NOTES: Final = 200_000
# Three dense dynamic-programming tables are allocated for one segment.  This
# product bound caps their combined resident memory even when the independent
# note and lyric limits would otherwise permit a pathological square matrix.
_MAX_ALIGNMENT_DP_CELLS: Final = 262_144
_DEFAULT_HOTWORD_CHARACTERS: Final = 2_047
_MAX_HOTWORD_CHARACTERS: Final = 4_095

_TIMESTAMP_PATTERN: Final = re.compile(
    r"\[(?P<minutes>\d{1,3}):(?P<seconds>[0-5]\d)"
    r"(?:(?:\.|:)(?P<fraction>\d{1,3}))?\]"
)
_METADATA_TAG_PATTERN: Final = re.compile(
    r"\[(?P<name>[A-Za-z][A-Za-z0-9_-]{0,31}):(?P<value>[^\]\r\n]*)\]"
)
_INTEGER_PATTERN: Final = re.compile(r"-?\d+")
_HAN_PATTERN: Final = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_PRESERVED_METADATA_FIELDS: Final = (
    "duration",
    "note_pitch",
    "time",
    "f0",
)


class LyricsAlignmentError(ValueError):
    """Report a stable fail-closed reason for an unsafe lyric correction."""

    def __init__(self, code: str, message: str) -> None:
        """Create an error whose code is safe for programmatic handling."""

        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TimedLyricLine:
    """Represent one normalized LRC line and its half-open playback interval."""

    start_ms: int
    end_ms: int
    text: str
    tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SegmentLyricCorrection:
    """Describe one verified metadata segment without vendor phoneme output."""

    segment_index: int
    source_text: str
    source_note_type: str
    authoritative_tokens: tuple[str, ...]
    corrected_note_tokens: tuple[str, ...]
    corrected_note_types: tuple[int, ...]
    source_line_indexes: tuple[int, ...]
    exact_match_ratio: float
    alignment_cost_ratio: float


@dataclass(frozen=True, slots=True)
class LyricsAlignmentPlan:
    """Contain immutable corrections that must be rendered with official G2P."""

    corrections: tuple[SegmentLyricCorrection, ...]
    track_duration_ms: int
    synchronized_line_count: int


@dataclass(frozen=True, slots=True)
class _MetadataSegment:
    """Hold validated note arrays and timing for one target metadata segment."""

    index: int
    start_ms: int
    end_ms: int
    language: str
    source_text: str
    source_note_type: str
    note_tokens: tuple[str, ...]
    note_types: tuple[int, ...]
    group_slots: tuple[tuple[int, ...], ...]
    observations: tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class _SegmentLyricAssignment:
    """Record the exact lyric tokens and source lines owned by one segment."""

    tokens: tuple[str, ...]
    source_line_indexes: tuple[int, ...]


def _raise(code: str, message: str) -> NoReturn:
    """Raise one categorized alignment failure without exposing lyric content."""

    raise LyricsAlignmentError(code, message)


def _require_track_duration(track_duration_ms: object) -> int:
    """Validate the duration used to bound every LRC and segment timestamp."""

    if (
        type(track_duration_ms) is not int
        or not 1 <= track_duration_ms <= _MAX_TRACK_DURATION_MS
    ):
        _raise("invalid_duration", "Track duration is outside the supported bound.")
    return track_duration_ms


def _fraction_to_milliseconds(fraction: str | None) -> int:
    """Convert the one-to-three LRC fractional digits without float rounding."""

    if fraction is None:
        return 0
    return int(fraction) * (10 ** (3 - len(fraction)))


def _timestamp_to_milliseconds(match: re.Match[str]) -> int:
    """Convert one already validated timestamp match to milliseconds."""

    return (
        int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + _fraction_to_milliseconds(match.group("fraction"))
    )


def _validate_text_bound(text: object, *, name: str, maximum_bytes: int) -> str:
    """Require bounded, NUL-free Unicode before applying regular expressions."""

    if not isinstance(text, str):
        _raise("invalid_text", f"{name} must be text.")
    if "\x00" in text:
        _raise("invalid_text", f"{name} contains a forbidden NUL character.")
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        _raise("invalid_text", f"{name} contains invalid Unicode.")
    if encoded_length > maximum_bytes:
        _raise("lyrics_too_large", f"{name} exceeds the supported byte bound.")
    return text


def _canonicalize_lyric_text(text: str, *, allow_empty: bool) -> tuple[str, ...]:
    """Return Simplified-Chinese Han tokens and reject unsupported lyric prose.

    Punctuation, spacing, and decorative symbols do not consume sung syllables.
    Letters and numbers are rejected rather than discarded because silently
    dropping them would shift every subsequent Mandarin note assignment.
    """

    simplified = simplify_chinese_text(text)
    tokens: list[str] = []
    for character in simplified:
        if _HAN_PATTERN.fullmatch(character):
            tokens.append(character)
            continue
        category = unicodedata.category(character)
        if character.isspace() or category[0] in {"P", "S", "Z"}:
            continue
        _raise(
            "unsupported_lyrics",
            "Lyrics contain non-Mandarin content that cannot be aligned safely.",
        )
    if not tokens and not allow_empty:
        _raise("empty_lyrics", "Lyrics contain no alignable Mandarin characters.")
    return tuple(tokens)


def parse_synced_lrc(
    synced_lrc: str,
    track_duration_ms: int,
) -> tuple[TimedLyricLine, ...]:
    """Parse a bounded synchronized LRC document into normalized intervals.

    Metadata tags are ignored except ``offset``, which is applied once to all
    timestamps. Multiple timestamps on one lyric line are expanded. Identical
    duplicate timestamps are deduplicated, while conflicting text at the same
    timestamp fails closed because either choice could corrupt alignment.

    Args:
        synced_lrc: LRCLIB-style synchronized LRC text.
        track_duration_ms: Trusted probed audio duration in milliseconds.

    Returns:
        Time-sorted lyric lines with half-open intervals ending at the next
        timestamp, or at the track duration for the final line.

    Raises:
        LyricsAlignmentError: If syntax, size, timing, or content is unsafe.
    """

    duration_ms = _require_track_duration(track_duration_ms)
    source = _validate_text_bound(
        synced_lrc,
        name="Synchronized lyrics",
        maximum_bytes=_MAX_LRC_BYTES,
    )
    lines = source.splitlines()
    if len(lines) > _MAX_LRC_LINES:
        _raise("lyrics_too_large", "Synchronized lyrics contain too many lines.")

    timestamped: list[tuple[int, str]] = []
    offset_ms: int | None = None
    saw_timestamp = False
    for line_index, raw_line in enumerate(lines):
        line = raw_line.removeprefix("\ufeff") if line_index == 0 else raw_line
        if len(line) > _MAX_LINE_CHARACTERS:
            _raise("lyrics_too_large", "A synchronized lyric line is too long.")
        stripped = line.strip()
        if not stripped:
            continue

        metadata_match = _METADATA_TAG_PATTERN.fullmatch(stripped)
        if metadata_match is not None:
            if metadata_match.group("name").casefold() == "offset":
                raw_offset = metadata_match.group("value").strip()
                if not re.fullmatch(r"[+-]?\d{1,6}", raw_offset):
                    _raise("invalid_lrc", "The LRC offset tag is malformed.")
                parsed_offset = int(raw_offset)
                if abs(parsed_offset) > _MAX_OFFSET_MS:
                    _raise("invalid_lrc", "The LRC offset exceeds the safe bound.")
                if offset_ms is not None and parsed_offset != offset_ms:
                    _raise("ambiguous_lrc", "The LRC contains conflicting offsets.")
                offset_ms = parsed_offset
            continue

        position = 0
        timestamps: list[int] = []
        while True:
            timestamp_match = _TIMESTAMP_PATTERN.match(stripped, position)
            if timestamp_match is None:
                break
            timestamps.append(_timestamp_to_milliseconds(timestamp_match))
            position = timestamp_match.end()
        if not timestamps:
            _raise(
                "invalid_lrc",
                "Synchronized lyrics contain an untimed non-metadata line.",
            )
        saw_timestamp = True
        lyric_text = simplify_chinese_text(stripped[position:].strip())
        for timestamp_ms in timestamps:
            timestamped.append((timestamp_ms, lyric_text))
            if len(timestamped) > _MAX_LRC_LINES:
                _raise("lyrics_too_large", "Synchronized lyrics have too many timestamps.")

    if not saw_timestamp:
        _raise("no_synced_lyrics", "No synchronized lyric timestamps were provided.")

    adjusted_offset = offset_ms or 0
    deduplicated: dict[int, str] = {}
    for raw_timestamp_ms, lyric_text in timestamped:
        timestamp_ms = raw_timestamp_ms + adjusted_offset
        if not 0 <= timestamp_ms < duration_ms:
            _raise("invalid_lrc", "An LRC timestamp lies outside the audio duration.")
        previous = deduplicated.get(timestamp_ms)
        if previous is not None and previous != lyric_text:
            _raise(
                "ambiguous_lrc",
                "Conflicting lyric text shares one synchronized timestamp.",
            )
        deduplicated[timestamp_ms] = lyric_text

    ordered = sorted(deduplicated.items())
    parsed: list[TimedLyricLine] = []
    content_line_count = 0
    for index, (start_ms, lyric_text) in enumerate(ordered):
        end_ms = ordered[index + 1][0] if index + 1 < len(ordered) else duration_ms
        tokens = _canonicalize_lyric_text(lyric_text, allow_empty=True)
        if tokens:
            content_line_count += 1
        parsed.append(
            TimedLyricLine(
                start_ms=start_ms,
                end_ms=end_ms,
                text=lyric_text,
                tokens=tokens,
            )
        )
    if content_line_count == 0:
        _raise("no_synced_lyrics", "Synchronized lyrics contain no timed lyric text.")
    return tuple(parsed)


def build_mandarin_hotword(
    synced_lrc: str,
    track_duration_ms: int,
    *,
    maximum_characters: int = _DEFAULT_HOTWORD_CHARACTERS,
) -> str:
    """Build a bounded FunASR hotword string from already validated lyrics.

    Han characters are normalized to Simplified Chinese, deduplicated in first-
    occurrence order, and separated by one ASCII space as expected by FunASR's
    singular ``hotword`` option. Character-level entries retain coverage across
    a long song without allowing repeated choruses to exhaust the bound. The
    result is only an ASR bias; callers must still run fail-closed post-alignment.

    Args:
        synced_lrc: LRCLIB-style synchronized LRC text.
        track_duration_ms: Trusted probed audio duration in milliseconds.
        maximum_characters: Maximum output length including separating spaces.

    Returns:
        A non-empty, stable, space-delimited Simplified-Chinese hotword string.

    Raises:
        LyricsAlignmentError: If lyrics or the requested bound are invalid.
    """

    if (
        type(maximum_characters) is not int
        or not 1 <= maximum_characters <= _MAX_HOTWORD_CHARACTERS
    ):
        _raise("invalid_hotword_limit", "The hotword length bound is unsupported.")
    lines = parse_synced_lrc(synced_lrc, track_duration_ms)
    unique_tokens: list[str] = []
    seen: set[str] = set()
    current_length = 0
    for line in lines:
        for token in line.tokens:
            if token in seen:
                continue
            added_length = len(token) + (1 if unique_tokens else 0)
            if current_length + added_length > maximum_characters:
                return " ".join(unique_tokens)
            seen.add(token)
            unique_tokens.append(token)
            current_length += added_length
    if not unique_tokens:
        _raise("empty_hotword", "Synchronized lyrics contain no hotword tokens.")
    return " ".join(unique_tokens)


def _parse_integer_series(value: object, *, field: str) -> tuple[int, ...]:
    """Parse one whitespace-delimited integer metadata field strictly."""

    if not isinstance(value, str) or not value.strip():
        _raise("invalid_metadata", f"Metadata field {field!r} must be non-empty text.")
    raw_values = value.split()
    if any(_INTEGER_PATTERN.fullmatch(item) is None for item in raw_values):
        _raise("invalid_metadata", f"Metadata field {field!r} is malformed.")
    return tuple(int(item) for item in raw_values)


def _parse_duration_series(value: object) -> tuple[float, ...]:
    """Parse positive finite note durations without accepting Boolean coercion."""

    if not isinstance(value, str) or not value.strip():
        _raise("invalid_metadata", "Metadata duration must be non-empty text.")
    parsed: list[float] = []
    for raw_value in value.split():
        try:
            number = float(raw_value)
        except ValueError:
            _raise("invalid_metadata", "Metadata duration contains a malformed number.")
        if not math.isfinite(number) or number <= 0:
            _raise("invalid_metadata", "Metadata note durations must be positive and finite.")
        parsed.append(number)
    return tuple(parsed)


def _single_han_observation(token: str) -> str | None:
    """Return one Simplified Han observation or mark an unusable ASR token."""

    simplified = simplify_chinese_text(token)
    han = _HAN_PATTERN.findall(simplified)
    return han[0] if len(han) == 1 else None


def _build_note_groups(
    note_tokens: tuple[str, ...],
    note_types: tuple[int, ...],
) -> tuple[tuple[tuple[int, ...], ...], tuple[str | None, ...]]:
    """Group each type-2 onset with its following type-3 melisma slots."""

    groups: list[list[int]] = []
    for slot_index, note_type in enumerate(note_types):
        if note_type == 1:
            if note_tokens[slot_index] != "<SP>":
                _raise("invalid_metadata", "Silence notes must use the <SP> token.")
            continue
        if note_type == 2:
            groups.append([slot_index])
            continue
        if note_type == 3:
            if not groups:
                _raise("invalid_metadata", "A sustain note has no preceding syllable onset.")
            groups[-1].append(slot_index)
            continue
        _raise("invalid_metadata", "Metadata note_type must contain only 1, 2, or 3.")
    frozen_groups = tuple(tuple(group) for group in groups)
    observations = tuple(
        _single_han_observation(note_tokens[group[0]]) for group in frozen_groups
    )
    return frozen_groups, observations


def _parse_metadata_segments(
    metadata: Sequence[Mapping[str, object]],
    track_duration_ms: int,
) -> tuple[_MetadataSegment, ...]:
    """Validate target metadata before lyric text can influence its note slots."""

    if isinstance(metadata, (str, bytes)) or not isinstance(metadata, Sequence):
        _raise("invalid_metadata", "Target metadata must be a sequence of objects.")
    if not 1 <= len(metadata) <= _MAX_METADATA_SEGMENTS:
        _raise("invalid_metadata", "Target metadata segment count is unsupported.")

    parsed: list[_MetadataSegment] = []
    total_notes = 0
    previous_end_ms = 0
    for segment_index, record in enumerate(metadata):
        if not isinstance(record, Mapping):
            _raise("invalid_metadata", "Every target metadata segment must be an object.")
        time_value = record.get("time")
        if (
            not isinstance(time_value, (list, tuple))
            or len(time_value) != 2
            or any(type(value) is not int for value in time_value)
        ):
            _raise("invalid_metadata", "Metadata time must contain exactly two integers.")
        # Exact-int validation above rejects Boolean values before these casts.
        start_ms, end_ms = int(time_value[0]), int(time_value[1])
        if not 0 <= start_ms < end_ms <= track_duration_ms:
            _raise("invalid_metadata", "A metadata segment lies outside the audio duration.")
        if segment_index > 0 and start_ms < previous_end_ms:
            _raise("invalid_metadata", "Target metadata segments overlap or are unsorted.")
        previous_end_ms = end_ms

        language = record.get("language")
        if not isinstance(language, str) or language not in {"Mandarin", "Chinese"}:
            _raise("invalid_metadata", "Only Mandarin target metadata is supported.")
        source_text = record.get("text")
        source_note_type = record.get("note_type")
        if not isinstance(source_text, str) or not source_text.strip():
            _raise("invalid_metadata", "Metadata text must be non-empty text.")
        if not isinstance(source_note_type, str):
            _raise("invalid_metadata", "Metadata note_type must be text.")
        note_tokens = tuple(source_text.split())
        note_types = _parse_integer_series(source_note_type, field="note_type")
        note_pitch = _parse_integer_series(record.get("note_pitch"), field="note_pitch")
        note_duration = _parse_duration_series(record.get("duration"))
        phoneme = record.get("phoneme")
        if not isinstance(phoneme, str) or len(phoneme.split()) != len(note_tokens):
            _raise("invalid_metadata", "Metadata phonemes do not match the note slots.")
        f0 = record.get("f0")
        if not isinstance(f0, str) or not f0.strip():
            _raise("invalid_metadata", "Metadata f0 must be non-empty text.")
        slot_count = len(note_tokens)
        if slot_count == 0 or not (
            slot_count == len(note_types) == len(note_pitch) == len(note_duration)
        ):
            _raise("invalid_metadata", "Metadata note arrays have inconsistent lengths.")
        total_notes += slot_count
        if total_notes > _MAX_METADATA_NOTES:
            _raise("invalid_metadata", "Target metadata contains too many note slots.")

        group_slots, observations = _build_note_groups(note_tokens, note_types)
        parsed.append(
            _MetadataSegment(
                index=segment_index,
                start_ms=start_ms,
                end_ms=end_ms,
                language=language,
                source_text=source_text,
                source_note_type=source_note_type,
                note_tokens=note_tokens,
                note_types=note_types,
                group_slots=group_slots,
                observations=observations,
            )
        )
    return tuple(parsed)


def _overlap_ms(
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> int:
    """Return the positive intersection length of two half-open intervals."""

    return max(0, min(left_end, right_end) - max(left_start, right_start))


def _allocate_line_token_counts(
    line: TimedLyricLine,
    candidates: tuple[tuple[int, int, int], ...],
) -> tuple[int, ...]:
    """Split one line monotonically using overlap time and remaining capacity.

    Each candidate tuple contains ``(segment_index, overlap_ms, capacity)`` in
    playback order. The initial quota is the exact floor of the line's token
    count weighted by interval overlap. Remaining tokens go to the greatest
    exact quota deficit while respecting each segment's unused onset slots.
    An equal greatest deficit is rejected because timing and capacity cannot
    prove which side of that boundary owns the next character.

    This bounded apportionment avoids a token-by-segment dynamic-programming
    matrix: one LRC line may legally overlap thousands of short SoulX slices,
    while the line itself is bounded to 2,048 characters.
    """

    token_count = len(line.tokens)
    total_overlap = sum(overlap_ms for _index, overlap_ms, _capacity in candidates)
    if token_count == 0 or total_overlap <= 0:
        _raise("unassigned_lyrics", "Timed lyrics have no auditable target interval.")
    if sum(capacity for _index, _overlap_ms, capacity in candidates) < token_count:
        _raise(
            "insufficient_note_capacity",
            "Authoritative lyrics exceed the overlapping note-onset capacity.",
        )

    allocations = [
        min(capacity, (token_count * overlap_ms) // total_overlap)
        for _index, overlap_ms, capacity in candidates
    ]
    unassigned = token_count - sum(allocations)
    while unassigned > 0:
        deficits = [
            token_count * overlap_ms - allocations[position] * total_overlap
            if allocations[position] < capacity
            else None
            for position, (_index, overlap_ms, capacity) in enumerate(candidates)
        ]
        available = [deficit for deficit in deficits if deficit is not None]
        if not available:
            _raise(
                "insufficient_note_capacity",
                "Authoritative lyrics exceed the overlapping note-onset capacity.",
            )
        greatest_deficit = max(available)
        owners = [
            position
            for position, deficit in enumerate(deficits)
            if deficit == greatest_deficit
        ]
        if len(owners) != 1:
            _raise(
                "ambiguous_timing",
                "Lyric timing cannot uniquely place a character across segments.",
            )
        allocations[owners[0]] += 1
        unassigned -= 1
    return tuple(allocations)


def _assign_lines_to_segments(
    lines: tuple[TimedLyricLine, ...],
    segments: tuple[_MetadataSegment, ...],
) -> tuple[_SegmentLyricAssignment, ...]:
    """Assign every non-empty line token exactly once across voiced segments.

    A synchronized line may cross one or more SoulX slice boundaries. Tokens
    are kept in lyric order and partitioned by interval overlap, subject to the
    remaining type-2 onset capacity in each segment. A non-empty line with no
    metadata overlap fails closed: LRC timing alone cannot prove that such text
    is merely an unsung intro, outro, translation, or stage direction.
    """

    assigned_tokens: list[list[str]] = [[] for _segment in segments]
    assigned_line_indexes: list[list[int]] = [[] for _segment in segments]
    remaining_capacity = [len(segment.group_slots) for segment in segments]
    segment_cursor = 0
    for line_index, line in enumerate(lines):
        if not line.tokens:
            continue
        # Both collections are ordered and internally non-overlapping. Keeping
        # the earliest segment that may intersect this line avoids a quadratic
        # scan at the documented 10,000-line/10,000-segment safety bounds.
        while (
            segment_cursor < len(segments)
            and segments[segment_cursor].end_ms <= line.start_ms
        ):
            segment_cursor += 1
        overlap_values: list[tuple[int, int]] = []
        scan_position = segment_cursor
        while (
            scan_position < len(segments)
            and segments[scan_position].start_ms < line.end_ms
        ):
            segment = segments[scan_position]
            overlap_ms = _overlap_ms(
                line.start_ms,
                line.end_ms,
                segment.start_ms,
                segment.end_ms,
            )
            if overlap_ms > 0:
                overlap_values.append((segment.index, overlap_ms))
            scan_position += 1
        overlaps = tuple(overlap_values)
        if not overlaps:
            _raise(
                "unassigned_lyrics",
                "Timed lyrics do not overlap any target metadata segment.",
            )
        voiced = tuple(
            (
                segment_index,
                overlap_ms,
                remaining_capacity[segment_index],
            )
            for segment_index, overlap_ms in overlaps
            if segments[segment_index].group_slots
            and remaining_capacity[segment_index] > 0
        )
        if not voiced:
            _raise(
                "insufficient_note_capacity",
                "Timed lyrics overlap a segment with no voiced note onset.",
            )
        allocations = _allocate_line_token_counts(line, voiced)
        token_cursor = 0
        for candidate, allocation in zip(voiced, allocations, strict=True):
            segment_index = candidate[0]
            if allocation == 0:
                continue
            next_cursor = token_cursor + allocation
            assigned_tokens[segment_index].extend(line.tokens[token_cursor:next_cursor])
            assigned_line_indexes[segment_index].append(line_index)
            remaining_capacity[segment_index] -= allocation
            token_cursor = next_cursor
        if token_cursor != len(line.tokens):
            _raise(
                "unassigned_lyrics",
                "A synchronized lyric line was not assigned completely.",
            )

    assigned_token_count = sum(len(tokens) for tokens in assigned_tokens)
    source_token_count = sum(len(line.tokens) for line in lines)
    if assigned_token_count != source_token_count:
        _raise(
            "unassigned_lyrics",
            "Synchronized lyrics were not assigned exactly once.",
        )
    return tuple(
        _SegmentLyricAssignment(
            tokens=tuple(tokens),
            source_line_indexes=tuple(line_indexes),
        )
        for tokens, line_indexes in zip(
            assigned_tokens,
            assigned_line_indexes,
            strict=True,
        )
    )


def _align_groups(
    observations: tuple[str | None, ...],
    canonical: tuple[str, ...],
    *,
    minimum_exact_match_ratio: float,
    maximum_cost_ratio: float,
) -> tuple[tuple[str, ...], tuple[int, ...], float, float]:
    """Map canonical syllables monotonically onto ASR note groups with DP.

    Every canonical character consumes one type-2 group. Extra groups may only
    extend the preceding canonical syllable and become type 3. Costs favor an
    exact onset match, then an exact repeated-character extension. Equal-cost
    paths are rejected instead of selecting an arbitrary syllable boundary.
    """

    group_count = len(observations)
    canonical_count = len(canonical)
    if canonical_count > group_count:
        _raise(
            "insufficient_note_capacity",
            "Authoritative lyrics contain more syllables than target note onsets.",
        )
    if canonical_count == 0:
        _raise("missing_segment_lyrics", "A voiced segment has no synchronized lyrics.")

    matrix_cell_count = (group_count + 1) * (canonical_count + 1)
    if matrix_cell_count > _MAX_ALIGNMENT_DP_CELLS:
        _raise(
            "alignment_too_large",
            "ASR-to-lyric alignment exceeds the supported resource bound.",
        )

    infinity = 10**12
    costs = [
        [infinity for _canonical_index in range(canonical_count + 1)]
        for _group_index in range(group_count + 1)
    ]
    counts = [
        [0 for _canonical_index in range(canonical_count + 1)]
        for _group_index in range(group_count + 1)
    ]
    predecessors: list[list[tuple[int, int, str] | None]] = [
        [None for _canonical_index in range(canonical_count + 1)]
        for _group_index in range(group_count + 1)
    ]

    # A leading extension has no syllable to extend, so the first note group
    # must carry the first canonical onset.
    costs[1][1] = 0 if observations[0] == canonical[0] else 4
    counts[1][1] = 1
    predecessors[1][1] = (0, 0, "onset")

    def update(
        next_group: int,
        next_canonical: int,
        candidate_cost: int,
        predecessor: tuple[int, int, str],
        path_count: int,
    ) -> None:
        """Retain the best cost and cap path counts at the ambiguity boundary."""

        if candidate_cost < costs[next_group][next_canonical]:
            costs[next_group][next_canonical] = candidate_cost
            counts[next_group][next_canonical] = min(2, path_count)
            predecessors[next_group][next_canonical] = predecessor
        elif candidate_cost == costs[next_group][next_canonical]:
            counts[next_group][next_canonical] = min(
                2,
                counts[next_group][next_canonical] + path_count,
            )

    for used_groups in range(1, group_count):
        for used_canonical in range(1, canonical_count + 1):
            if counts[used_groups][used_canonical] == 0:
                continue
            groups_after_next = group_count - (used_groups + 1)
            canonical_remaining = canonical_count - used_canonical
            if groups_after_next >= canonical_remaining:
                extension_cost = (
                    0
                    if observations[used_groups] == canonical[used_canonical - 1]
                    else 3
                )
                update(
                    used_groups + 1,
                    used_canonical,
                    costs[used_groups][used_canonical] + extension_cost,
                    (used_groups, used_canonical, "extension"),
                    counts[used_groups][used_canonical],
                )
            if used_canonical < canonical_count:
                onset_cost = (
                    0
                    if observations[used_groups] == canonical[used_canonical]
                    else 4
                )
                update(
                    used_groups + 1,
                    used_canonical + 1,
                    costs[used_groups][used_canonical] + onset_cost,
                    (used_groups, used_canonical, "onset"),
                    counts[used_groups][used_canonical],
                )

    if counts[group_count][canonical_count] != 1:
        _raise(
            "ambiguous_alignment",
            "ASR notes admit multiple equally plausible lyric alignments.",
        )

    operations: list[str] = []
    cursor_group = group_count
    cursor_canonical = canonical_count
    while cursor_group > 0:
        predecessor = predecessors[cursor_group][cursor_canonical]
        if predecessor is None:
            _raise("ambiguous_alignment", "The lyric alignment path is incomplete.")
        previous_group, previous_canonical, operation = predecessor
        operations.append(operation)
        cursor_group = previous_group
        cursor_canonical = previous_canonical
    operations.reverse()

    labels: list[str] = []
    corrected_types: list[int] = []
    canonical_index = -1
    exact_matches = 0
    for group_index, operation in enumerate(operations):
        if operation == "onset":
            canonical_index += 1
            corrected_types.append(2)
            if observations[group_index] == canonical[canonical_index]:
                exact_matches += 1
        else:
            corrected_types.append(3)
        labels.append(canonical[canonical_index])

    exact_match_ratio = exact_matches / canonical_count
    maximum_cost = 4 * canonical_count + 3 * (group_count - canonical_count)
    alignment_cost_ratio = costs[group_count][canonical_count] / max(1, maximum_cost)
    if exact_match_ratio < minimum_exact_match_ratio:
        _raise("low_coverage", "ASR and synchronized lyrics have insufficient overlap.")
    if alignment_cost_ratio > maximum_cost_ratio:
        _raise("low_coverage", "ASR-to-lyric alignment cost is too high.")
    return (
        tuple(labels),
        tuple(corrected_types),
        exact_match_ratio,
        alignment_cost_ratio,
    )


def _expand_group_corrections(
    segment: _MetadataSegment,
    group_labels: tuple[str, ...],
    group_types: tuple[int, ...],
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Propagate each corrected onset token across all of its sustain slots."""

    corrected_tokens = list(segment.note_tokens)
    corrected_types = list(segment.note_types)
    for group_index, slots in enumerate(segment.group_slots):
        for slot_offset, slot_index in enumerate(slots):
            corrected_tokens[slot_index] = group_labels[group_index]
            corrected_types[slot_index] = (
                group_types[group_index] if slot_offset == 0 else 3
            )
    return tuple(corrected_tokens), tuple(corrected_types)


def _validate_plain_lyrics(
    plain_lyrics: str | None,
    synchronized_lines: tuple[TimedLyricLine, ...],
) -> None:
    """Cross-check optional LRCLIB plain lyrics against synchronized content."""

    if plain_lyrics is None:
        return
    source = _validate_text_bound(
        plain_lyrics,
        name="Plain lyrics",
        maximum_bytes=_MAX_LRC_BYTES,
    )
    plain_tokens = _canonicalize_lyric_text(source, allow_empty=False)
    synchronized_tokens = tuple(
        token for line in synchronized_lines for token in line.tokens
    )
    if plain_tokens != synchronized_tokens:
        _raise(
            "lyrics_source_mismatch",
            "Plain and synchronized lyric sources disagree.",
        )


def plan_metadata_lyric_corrections(
    metadata: Sequence[Mapping[str, object]],
    synced_lrc: str,
    track_duration_ms: int,
    *,
    plain_lyrics: str | None = None,
    minimum_exact_match_ratio: float = 0.60,
    maximum_cost_ratio: float = 0.45,
) -> LyricsAlignmentPlan:
    """Build a fail-closed Mandarin lyric correction plan for SoulX metadata.

    Synchronized line intervals select the authoritative lyric text for each
    target segment. Dynamic programming then aligns the segment's ASR onset
    sequence to those characters and marks surplus onsets as melisma sustains.
    The function never mutates metadata and never generates phonemes.

    Args:
        metadata: SoulX target metadata objects in chronological order.
        synced_lrc: Bounded synchronized LRCLIB lyrics.
        track_duration_ms: Trusted probed target-audio duration.
        plain_lyrics: Optional LRCLIB plain text used as a source-consistency
            check; synchronized lyrics remain mandatory.
        minimum_exact_match_ratio: Required exact ASR/canonical onset coverage.
        maximum_cost_ratio: Maximum normalized dynamic-programming edit cost.

    Returns:
        An immutable plan containing corrected note tokens and note types.

    Raises:
        LyricsAlignmentError: If timing, metadata, source agreement, confidence,
        capacity, or alignment uniqueness cannot be established.
    """

    duration_ms = _require_track_duration(track_duration_ms)
    if (
        isinstance(minimum_exact_match_ratio, bool)
        or not isinstance(minimum_exact_match_ratio, (int, float))
        or not 0.0 <= minimum_exact_match_ratio <= 1.0
        or isinstance(maximum_cost_ratio, bool)
        or not isinstance(maximum_cost_ratio, (int, float))
        or not 0.0 <= maximum_cost_ratio <= 1.0
    ):
        _raise("invalid_policy", "Lyric alignment thresholds must lie within 0..1.")

    synchronized_lines = parse_synced_lrc(synced_lrc, duration_ms)
    _validate_plain_lyrics(plain_lyrics, synchronized_lines)
    segments = _parse_metadata_segments(metadata, duration_ms)
    segment_assignments = _assign_lines_to_segments(synchronized_lines, segments)

    corrections: list[SegmentLyricCorrection] = []
    for segment, assignment in zip(segments, segment_assignments, strict=True):
        authoritative = assignment.tokens
        line_indexes = assignment.source_line_indexes
        if not segment.group_slots:
            if authoritative:
                _raise(
                    "insufficient_note_capacity",
                    "Lyrics overlap a metadata segment with no voiced note onset.",
                )
            corrections.append(
                SegmentLyricCorrection(
                    segment_index=segment.index,
                    source_text=segment.source_text,
                    source_note_type=segment.source_note_type,
                    authoritative_tokens=(),
                    corrected_note_tokens=segment.note_tokens,
                    corrected_note_types=segment.note_types,
                    source_line_indexes=line_indexes,
                    exact_match_ratio=1.0,
                    alignment_cost_ratio=0.0,
                )
            )
            continue
        if not authoritative:
            _raise(
                "missing_segment_lyrics",
                "A voiced metadata segment has no overlapping synchronized lyrics.",
            )
        labels, group_types, coverage, cost_ratio = _align_groups(
            segment.observations,
            authoritative,
            minimum_exact_match_ratio=float(minimum_exact_match_ratio),
            maximum_cost_ratio=float(maximum_cost_ratio),
        )
        corrected_tokens, corrected_types = _expand_group_corrections(
            segment,
            labels,
            group_types,
        )
        corrections.append(
            SegmentLyricCorrection(
                segment_index=segment.index,
                source_text=segment.source_text,
                source_note_type=segment.source_note_type,
                authoritative_tokens=authoritative,
                corrected_note_tokens=corrected_tokens,
                corrected_note_types=corrected_types,
                source_line_indexes=line_indexes,
                exact_match_ratio=coverage,
                alignment_cost_ratio=cost_ratio,
            )
        )

    return LyricsAlignmentPlan(
        corrections=tuple(corrections),
        track_duration_ms=duration_ms,
        synchronized_line_count=len(synchronized_lines),
    )


def render_corrected_metadata(
    metadata: Sequence[Mapping[str, object]],
    plan: LyricsAlignmentPlan,
    phoneme_generator: Callable[[Sequence[str], str], Sequence[str]],
) -> list[dict[str, object]]:
    """Render corrected metadata only through an explicit trusted G2P callback.

    The downstream SoulX worker should pass the official
    ``preprocess.tools.g2p.g2p_transform`` function. This module deliberately
    does not import vendor code, because a missing vendor runtime must leave the
    correction as tokens rather than create guessed phonemes. Duration, pitch,
    segment time, and F0 fields are copied byte-for-byte from the source.

    Args:
        metadata: The same metadata sequence used to create ``plan``.
        plan: A correction plan returned by
            :func:`plan_metadata_lyric_corrections`.
        phoneme_generator: Trusted callback accepting note tokens and language.

    Returns:
        New JSON-serializable metadata objects with regenerated text, note type,
        and phoneme fields; input objects remain unchanged.

    Raises:
        LyricsAlignmentError: If the plan is stale or G2P output is malformed.
    """

    if not isinstance(plan, LyricsAlignmentPlan):
        _raise("invalid_plan", "A LyricsAlignmentPlan is required.")
    if not callable(phoneme_generator):
        _raise("g2p_unavailable", "A trusted phoneme generator is required.")
    if len(metadata) != len(plan.corrections):
        _raise("stale_plan", "Metadata no longer matches the correction plan.")

    rendered: list[dict[str, object]] = []
    for segment_index, (record, correction) in enumerate(
        zip(metadata, plan.corrections, strict=True)
    ):
        if not isinstance(record, Mapping):
            _raise("invalid_metadata", "Every target metadata segment must be an object.")
        if (
            correction.segment_index != segment_index
            or record.get("text") != correction.source_text
            or record.get("note_type") != correction.source_note_type
        ):
            _raise("stale_plan", "Metadata text or note types changed after planning.")
        language = record.get("language")
        if not isinstance(language, str):
            _raise("invalid_metadata", "Metadata language is missing.")

        try:
            generated = phoneme_generator(
                list(correction.corrected_note_tokens),
                language,
            )
        except Exception as error:
            raise LyricsAlignmentError(
                "g2p_failed",
                "The trusted phoneme generator failed.",
            ) from error
        if isinstance(generated, (str, bytes)) or not isinstance(generated, Sequence):
            _raise("g2p_failed", "The phoneme generator returned an invalid sequence.")
        phonemes = tuple(generated)
        if (
            len(phonemes) != len(correction.corrected_note_tokens)
            or any(
                not isinstance(phoneme, str)
                or not phoneme
                or any(character.isspace() for character in phoneme)
                for phoneme in phonemes
            )
        ):
            _raise("g2p_failed", "The phoneme generator returned malformed tokens.")

        copied = dict(record)
        preserved = {field: record.get(field) for field in _PRESERVED_METADATA_FIELDS}
        copied["text"] = " ".join(correction.corrected_note_tokens)
        copied["note_type"] = " ".join(
            str(note_type) for note_type in correction.corrected_note_types
        )
        copied["phoneme"] = " ".join(phonemes)
        if any(copied.get(field) != value for field, value in preserved.items()):
            _raise("render_failed", "A preserved metadata field changed unexpectedly.")
        rendered.append(copied)
    return rendered
