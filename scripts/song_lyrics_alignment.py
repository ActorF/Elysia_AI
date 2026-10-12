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
# Dense dynamic-programming tables plus interned mismatch signatures are
# allocated for one lyric block. This product bound caps resident memory even
# when the independent note and lyric limits permit a pathological square.
_MAX_ALIGNMENT_DP_CELLS: Final = 262_144
_DEFAULT_HOTWORD_CHARACTERS: Final = 2_047
_MAX_HOTWORD_CHARACTERS: Final = 4_095
_LRC_ONSET_EARLY_TOLERANCE_MS: Final = 100
_LRC_LINE_START_LATE_TOLERANCE_MS: Final = 600
_LRC_LINE_END_TOLERANCE_MS: Final = 100

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
    source_language: str
    source_time: tuple[int, int]
    source_duration: str
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
    source_duration: str
    note_tokens: tuple[str, ...]
    note_types: tuple[int, ...]
    group_slots: tuple[tuple[int, ...], ...]
    observations: tuple[str | None, ...]
    group_onsets_ms: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _LyricBlock:
    """Hold consecutive non-empty LRC lines between explicit silent gaps."""

    start_ms: int
    end_ms: int
    tokens: tuple[str, ...]
    token_onsets_ms: tuple[int | None, ...]
    token_line_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _GroupReference:
    """Identify one voiced onset group in the global playback timeline."""

    segment_index: int
    group_index: int
    onset_ms: int
    observation: str | None


@dataclass(frozen=True, slots=True)
class _GroupAlignment:
    """Retain the verified alignment path and its canonical-token ownership."""

    labels: tuple[str, ...]
    note_types: tuple[int, ...]
    canonical_indexes: tuple[int, ...]
    exact_match_ratio: float
    alignment_cost_ratio: float


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


def _estimate_group_onsets_ms(
    start_ms: int,
    end_ms: int,
    note_durations: tuple[float, ...],
    group_slots: tuple[tuple[int, ...], ...],
) -> tuple[int, ...]:
    """Place ASR onset groups on the trusted segment timeline.

    SoulX duration values are relative note weights. Scaling their cumulative
    positions to the already validated metadata interval avoids assuming a
    vendor-specific unit while retaining the within-segment rhythmic evidence
    needed to distinguish otherwise equal text-alignment paths.
    """

    try:
        total_duration = math.fsum(note_durations)
    except OverflowError:
        _raise("invalid_metadata", "Metadata note durations have an invalid sum.")
    if not math.isfinite(total_duration) or total_duration <= 0:
        _raise("invalid_metadata", "Metadata note durations have an invalid sum.")

    cumulative: list[float] = []
    elapsed = 0.0
    for duration in note_durations:
        cumulative.append(elapsed)
        elapsed += duration
    interval_ms = end_ms - start_ms
    return tuple(
        min(
            end_ms - 1,
            start_ms + round(interval_ms * cumulative[slots[0]] / total_duration),
        )
        for slots in group_slots
    )


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
        source_duration = record.get("duration")
        if not isinstance(source_text, str) or not source_text.strip():
            _raise("invalid_metadata", "Metadata text must be non-empty text.")
        if not isinstance(source_note_type, str):
            _raise("invalid_metadata", "Metadata note_type must be text.")
        if not isinstance(source_duration, str):
            _raise("invalid_metadata", "Metadata duration must be text.")
        note_tokens = tuple(source_text.split())
        note_types = _parse_integer_series(source_note_type, field="note_type")
        note_pitch = _parse_integer_series(record.get("note_pitch"), field="note_pitch")
        note_duration = _parse_duration_series(source_duration)
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
        group_onsets_ms = _estimate_group_onsets_ms(
            start_ms,
            end_ms,
            note_duration,
            group_slots,
        )
        parsed.append(
            _MetadataSegment(
                index=segment_index,
                start_ms=start_ms,
                end_ms=end_ms,
                language=language,
                source_text=source_text,
                source_note_type=source_note_type,
                source_duration=source_duration,
                note_tokens=note_tokens,
                note_types=note_types,
                group_slots=group_slots,
                observations=observations,
                group_onsets_ms=group_onsets_ms,
            )
        )
    return tuple(parsed)


def _build_lyric_blocks(lines: tuple[TimedLyricLine, ...]) -> tuple[_LyricBlock, ...]:
    """Join adjacent content lines while preserving explicit empty-line gaps.

    Ordinary LRC authenticates each line start but does not authenticate where
    the final syllable stops. Aligning a whole consecutive block lets exact
    later line starts resolve small timestamp drift without pretending that a
    short line occupies an entire instrumental pause. An explicit empty line
    closes the block, so unlabelled vocals in that gap remain untouched.
    """

    blocks: list[_LyricBlock] = []
    block_start_ms: int | None = None
    block_end_ms = 0
    block_tokens: list[str] = []
    block_onsets: list[int | None] = []
    block_line_indexes: list[int] = []

    def flush() -> None:
        """Persist one accumulated block before starting the next interval."""

        nonlocal block_start_ms, block_end_ms
        if block_start_ms is None:
            return
        blocks.append(
            _LyricBlock(
                start_ms=block_start_ms,
                end_ms=block_end_ms,
                tokens=tuple(block_tokens),
                token_onsets_ms=tuple(block_onsets),
                token_line_indexes=tuple(block_line_indexes),
            )
        )
        block_start_ms = None
        block_end_ms = 0
        block_tokens.clear()
        block_onsets.clear()
        block_line_indexes.clear()

    for line_index, line in enumerate(lines):
        if not line.tokens:
            flush()
            continue
        if block_start_ms is None:
            block_start_ms = line.start_ms
        block_end_ms = line.end_ms
        block_tokens.extend(line.tokens)
        # A line timestamp is an exact anchor only for its first character.
        # Assigning evenly spaced times to the rest would invent word timing.
        block_onsets.extend(
            line.start_ms if token_index == 0 else None
            for token_index in range(len(line.tokens))
        )
        block_line_indexes.extend(line_index for _token in line.tokens)
    flush()
    return tuple(blocks)


def _global_group_references(
    segments: tuple[_MetadataSegment, ...],
) -> tuple[_GroupReference, ...]:
    """Flatten segment onset groups into their already validated time order."""

    return tuple(
        _GroupReference(
            segment_index=segment.index,
            group_index=group_index,
            onset_ms=onset_ms,
            observation=segment.observations[group_index],
        )
        for segment in segments
        for group_index, onset_ms in enumerate(segment.group_onsets_ms)
    )


def _require_line_reference_coverage(
    blocks: tuple[_LyricBlock, ...],
    lines: tuple[TimedLyricLine, ...],
    references: tuple[_GroupReference, ...],
    segments: tuple[_MetadataSegment, ...],
) -> None:
    """Require plausible metadata ownership for every non-empty LRC line.

    This gate preserves the distinct ``unassigned_lyrics`` failure for lyric
    lines that have no candidate interval at all. Raw segment overlap remains
    sufficient so the later checks can distinguish silence-only capacity and
    bad timing. Otherwise, the same early/late tolerances as the DP windows
    admit a legitimate boundary onset before alignment evaluates it.
    """

    block_end_by_line: dict[int, int] = {}
    for block in blocks:
        for line_index in dict.fromkeys(block.token_line_indexes):
            block_end_by_line[line_index] = block.end_ms

    reference_cursor = 0
    for line_index, line in enumerate(lines):
        if not line.tokens:
            continue
        if any(
            segment.start_ms < line.end_ms and line.start_ms < segment.end_ms
            for segment in segments
        ):
            # Preserve the later capacity and timing diagnostics when metadata
            # owns the raw interval but contains only silence or a bad onset.
            continue
        block_end_ms = block_end_by_line.get(line_index)
        if block_end_ms is None:
            _raise("invalid_lrc", "A synchronized lyric line has no timing block.")
        lower_ms = line.start_ms - _LRC_ONSET_EARLY_TOLERANCE_MS
        upper_ms = min(
            line.end_ms + _LRC_LINE_END_TOLERANCE_MS,
            block_end_ms,
        )
        if len(line.tokens) == 1:
            upper_ms = min(
                upper_ms,
                line.start_ms + _LRC_LINE_START_LATE_TOLERANCE_MS + 1,
            )
        while (
            reference_cursor < len(references)
            and references[reference_cursor].onset_ms < lower_ms
        ):
            reference_cursor += 1
        if (
            reference_cursor >= len(references)
            or references[reference_cursor].onset_ms >= upper_ms
        ):
            _raise(
                "unassigned_lyrics",
                "Timed lyrics do not overlap any plausible voiced note onset.",
            )


def _block_timing_windows(
    block: _LyricBlock,
    lines: tuple[TimedLyricLine, ...],
) -> tuple[tuple[int, int], ...]:
    """Build hard onset windows from authenticated LRC line boundaries.

    LRC timestamps and ASR-derived note onsets can differ slightly, so a small
    boundary tolerance is intentional. The first word receives a larger late
    allowance because the real singing corpus starts lines as much as 466 ms
    after their timestamp. Keeping the end allowance narrow prevents a unique
    text match from relocating words into the following lyric line.
    """

    windows: list[tuple[int, int]] = []
    for token_index, line_index in enumerate(block.token_line_indexes):
        line = lines[line_index]
        lower_ms = line.start_ms - _LRC_ONSET_EARLY_TOLERANCE_MS
        upper_ms = min(
            line.end_ms + _LRC_LINE_END_TOLERANCE_MS,
            block.end_ms,
        )
        if block.token_onsets_ms[token_index] is not None:
            # Windows are half-open, hence the extra millisecond preserves the
            # documented inclusive +600 ms allowance for integer timestamps.
            upper_ms = min(
                upper_ms,
                line.start_ms + _LRC_LINE_START_LATE_TOLERANCE_MS + 1,
            )
        windows.append((lower_ms, upper_ms))
    return tuple(windows)


def _align_group_path(
    observations: tuple[str | None, ...],
    canonical: tuple[str, ...],
    *,
    minimum_exact_match_ratio: float,
    maximum_cost_ratio: float,
    group_onsets_ms: tuple[int, ...] | None = None,
    canonical_onsets_ms: tuple[int | None, ...] | None = None,
    canonical_windows_ms: tuple[tuple[int, int], ...] | None = None,
) -> _GroupAlignment:
    """Map canonical syllables monotonically onto ASR note groups with DP.

    Every canonical character consumes one type-2 group. Extra groups may only
    extend the preceding canonical syllable and become type 3. Text costs favor
    an exact onset match, then an exact repeated-character extension. When text
    alone leaves multiple optimal paths, note timing and LRC timing act as a
    lexicographic secondary cost. A final leftmost-exact cost resolves only the
    case where the recognizer repeats the same lyric tokens after their timed
    line start; it does not choose between mismatching observations. Paths tied
    on all evidence are rejected instead of selecting an arbitrary boundary.
    Optional half-open timing windows prevent a text match from moving a lyric
    onset outside the LRC line that authorizes it.
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
    timing_enabled = group_onsets_ms is not None or canonical_onsets_ms is not None
    if timing_enabled and (
        group_onsets_ms is None
        or canonical_onsets_ms is None
        or len(group_onsets_ms) != group_count
        or len(canonical_onsets_ms) != canonical_count
    ):
        _raise("invalid_metadata", "Lyric alignment timing evidence is incomplete.")
    if canonical_windows_ms is not None and (
        group_onsets_ms is None
        or len(canonical_windows_ms) != canonical_count
        or any(
            type(lower_ms) is not int
            or type(upper_ms) is not int
            or lower_ms >= upper_ms
            for lower_ms, upper_ms in canonical_windows_ms
        )
    ):
        _raise("invalid_metadata", "Lyric alignment timing windows are incomplete.")

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
    timing_costs = [
        [infinity for _canonical_index in range(canonical_count + 1)]
        for _group_index in range(group_count + 1)
    ]
    exact_onset_position_costs = [
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
    unset_signature = -2
    ambiguous_signature = -1
    mismatch_signatures = [
        [unset_signature for _canonical_index in range(canonical_count + 1)]
        for _group_index in range(group_count + 1)
    ]
    signature_nodes: dict[tuple[int, int, int], int] = {}

    def append_mismatch(
        signature: int,
        canonical_index: int,
        group_index: int,
    ) -> int:
        """Intern an exact mismatch-position sequence without hash collisions."""

        if signature == ambiguous_signature:
            return ambiguous_signature
        key = (signature, canonical_index, group_index)
        existing = signature_nodes.get(key)
        if existing is not None:
            return existing
        identifier = len(signature_nodes) + 1
        signature_nodes[key] = identifier
        return identifier

    def onset_is_admissible(group_index: int, canonical_index: int) -> bool:
        """Keep each canonical onset inside its authenticated LRC line window."""

        if canonical_windows_ms is None or group_onsets_ms is None:
            return True
        lower_ms, upper_ms = canonical_windows_ms[canonical_index]
        return lower_ms <= group_onsets_ms[group_index] < upper_ms

    # A leading extension has no syllable to extend, so the first note group
    # must carry the first canonical onset.
    if not onset_is_admissible(0, 0):
        _raise(
            "line_timing_mismatch",
            "A lyric onset lies outside its synchronized line window.",
        )
    initial_exact = observations[0] == canonical[0]
    costs[1][1] = 0 if initial_exact else 4
    timing_costs[1][1] = (
        abs(group_onsets_ms[0] - canonical_onsets_ms[0])
        if group_onsets_ms is not None
        and canonical_onsets_ms is not None
        and canonical_onsets_ms[0] is not None
        else 0
    )
    exact_onset_position_costs[1][1] = 0
    counts[1][1] = 1
    mismatch_signatures[1][1] = (
        0 if initial_exact else append_mismatch(0, 0, 0)
    )
    predecessors[1][1] = (0, 0, "onset")

    def update(
        next_group: int,
        next_canonical: int,
        candidate_cost: int,
        candidate_timing_cost: int,
        candidate_exact_onset_position_cost: int,
        candidate_mismatch_signature: int,
        predecessor: tuple[int, int, str],
        path_count: int,
    ) -> None:
        """Retain leftmost exact evidence only for one mismatch placement."""

        candidate_primary = (candidate_cost, candidate_timing_cost)
        current_primary = (
            costs[next_group][next_canonical],
            timing_costs[next_group][next_canonical],
        )
        if candidate_primary < current_primary:
            costs[next_group][next_canonical] = candidate_cost
            timing_costs[next_group][next_canonical] = candidate_timing_cost
            exact_onset_position_costs[next_group][next_canonical] = (
                candidate_exact_onset_position_cost
            )
            mismatch_signatures[next_group][next_canonical] = (
                candidate_mismatch_signature
            )
            counts[next_group][next_canonical] = min(2, path_count)
            predecessors[next_group][next_canonical] = predecessor
            return
        if candidate_primary != current_primary:
            return

        current_signature = mismatch_signatures[next_group][next_canonical]
        # Different mismatch placements are genuinely different guesses. They
        # remain ambiguous even if one happens to place other exact tokens
        # earlier. Identical placements may safely use the leftmost exact cost.
        if (
            current_signature == ambiguous_signature
            or candidate_mismatch_signature == ambiguous_signature
            or current_signature != candidate_mismatch_signature
        ):
            mismatch_signatures[next_group][next_canonical] = ambiguous_signature
            counts[next_group][next_canonical] = 2
            predecessors[next_group][next_canonical] = None
            return
        current_position_cost = exact_onset_position_costs[next_group][next_canonical]
        if candidate_exact_onset_position_cost < current_position_cost:
            exact_onset_position_costs[next_group][next_canonical] = (
                candidate_exact_onset_position_cost
            )
            counts[next_group][next_canonical] = min(2, path_count)
            predecessors[next_group][next_canonical] = predecessor
        elif candidate_exact_onset_position_cost == current_position_cost:
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
            current_signature = mismatch_signatures[used_groups][used_canonical]
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
                    timing_costs[used_groups][used_canonical],
                    exact_onset_position_costs[used_groups][used_canonical],
                    current_signature,
                    (used_groups, used_canonical, "extension"),
                    counts[used_groups][used_canonical],
                )
            if (
                used_canonical < canonical_count
                and onset_is_admissible(used_groups, used_canonical)
            ):
                onset_exact = observations[used_groups] == canonical[used_canonical]
                onset_cost = 0 if onset_exact else 4
                canonical_onset = (
                    canonical_onsets_ms[used_canonical]
                    if canonical_onsets_ms is not None
                    else None
                )
                next_signature = (
                    current_signature
                    if onset_exact
                    else append_mismatch(
                        current_signature,
                        used_canonical,
                        used_groups,
                    )
                )
                update(
                    used_groups + 1,
                    used_canonical + 1,
                    costs[used_groups][used_canonical] + onset_cost,
                    timing_costs[used_groups][used_canonical]
                    + (
                        abs(group_onsets_ms[used_groups] - canonical_onset)
                        if group_onsets_ms is not None and canonical_onset is not None
                        else 0
                    ),
                    exact_onset_position_costs[used_groups][used_canonical]
                    + (used_groups if onset_exact else 0),
                    next_signature,
                    (used_groups, used_canonical, "onset"),
                    counts[used_groups][used_canonical],
                )

    if counts[group_count][canonical_count] == 0:
        if canonical_windows_ms is not None:
            _raise(
                "line_timing_mismatch",
                "Lyric onsets cannot remain inside their synchronized line windows.",
            )
        _raise("ambiguous_alignment", "The lyric alignment path is incomplete.")
    if (
        counts[group_count][canonical_count] != 1
        or mismatch_signatures[group_count][canonical_count] == ambiguous_signature
    ):
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
    canonical_indexes: list[int] = []
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
        canonical_indexes.append(canonical_index)

    exact_match_ratio = exact_matches / canonical_count
    maximum_cost = 4 * canonical_count + 3 * (group_count - canonical_count)
    alignment_cost_ratio = costs[group_count][canonical_count] / max(
        1,
        maximum_cost,
    )
    if exact_match_ratio < minimum_exact_match_ratio:
        _raise("low_coverage", "ASR and synchronized lyrics have insufficient overlap.")
    if alignment_cost_ratio > maximum_cost_ratio:
        _raise("low_coverage", "ASR-to-lyric alignment cost is too high.")
    return _GroupAlignment(
        labels=tuple(labels),
        note_types=tuple(corrected_types),
        canonical_indexes=tuple(canonical_indexes),
        exact_match_ratio=exact_match_ratio,
        alignment_cost_ratio=alignment_cost_ratio,
    )


def _align_groups(
    observations: tuple[str | None, ...],
    canonical: tuple[str, ...],
    *,
    minimum_exact_match_ratio: float,
    maximum_cost_ratio: float,
    group_onsets_ms: tuple[int, ...] | None = None,
    canonical_onsets_ms: tuple[int | None, ...] | None = None,
    canonical_windows_ms: tuple[tuple[int, int], ...] | None = None,
) -> tuple[tuple[str, ...], tuple[int, ...], float, float]:
    """Expose the compact alignment result used by focused unit tests."""

    alignment = _align_group_path(
        observations,
        canonical,
        minimum_exact_match_ratio=minimum_exact_match_ratio,
        maximum_cost_ratio=maximum_cost_ratio,
        group_onsets_ms=group_onsets_ms,
        canonical_onsets_ms=canonical_onsets_ms,
        canonical_windows_ms=canonical_windows_ms,
    )
    return (
        alignment.labels,
        alignment.note_types,
        alignment.exact_match_ratio,
        alignment.alignment_cost_ratio,
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

    Consecutive non-empty LRC lines form independently auditable lyric blocks.
    Dynamic programming aligns each block across the global note timeline, so
    a SoulX segment boundary cannot split a short line merely because the next
    line starts later. Explicit empty LRC intervals remain outside correction;
    unlabelled ad-libs or spoken audio there retain their ASR text. The function
    never mutates metadata and never generates phonemes.

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
    blocks = _build_lyric_blocks(synchronized_lines)
    references = _global_group_references(segments)
    _require_line_reference_coverage(
        blocks,
        synchronized_lines,
        references,
        segments,
    )

    group_labels = [
        [segment.note_tokens[slots[0]] for slots in segment.group_slots]
        for segment in segments
    ]
    group_types = [
        [segment.note_types[slots[0]] for slots in segment.group_slots]
        for segment in segments
    ]
    aligned_types: list[list[int | None]] = [
        [None for _group in segment.group_slots] for segment in segments
    ]
    aligned_line_indexes: list[list[int | None]] = [
        [None for _group in segment.group_slots] for segment in segments
    ]

    # Blocks and references are both ordered. Advancing one cursor keeps the
    # selection linear at the documented 200,000-note safety ceiling.
    reference_cursor = 0
    for block in blocks:
        selection_start_ms = block.start_ms - _LRC_ONSET_EARLY_TOLERANCE_MS
        while (
            reference_cursor < len(references)
            and references[reference_cursor].onset_ms < selection_start_ms
        ):
            reference_cursor += 1
        block_end_cursor = reference_cursor
        while (
            block_end_cursor < len(references)
            and references[block_end_cursor].onset_ms < block.end_ms
        ):
            block_end_cursor += 1
        block_references = references[reference_cursor:block_end_cursor]
        reference_cursor = block_end_cursor
        if not block_references:
            overlaps_metadata = any(
                segment.start_ms < block.end_ms
                and block.start_ms < segment.end_ms
                for segment in segments
            )
            if overlaps_metadata:
                _raise(
                    "insufficient_note_capacity",
                    "Timed lyrics overlap metadata with no voiced note onset.",
                )
            _raise(
                "unassigned_lyrics",
                "Timed lyrics do not overlap any target metadata segment.",
            )

        alignment = _align_group_path(
            tuple(reference.observation for reference in block_references),
            block.tokens,
            minimum_exact_match_ratio=float(minimum_exact_match_ratio),
            maximum_cost_ratio=float(maximum_cost_ratio),
            group_onsets_ms=tuple(
                reference.onset_ms for reference in block_references
            ),
            canonical_onsets_ms=block.token_onsets_ms,
            canonical_windows_ms=_block_timing_windows(block, synchronized_lines),
        )
        for reference, label, note_type, canonical_index in zip(
            block_references,
            alignment.labels,
            alignment.note_types,
            alignment.canonical_indexes,
            strict=True,
        ):
            group_labels[reference.segment_index][reference.group_index] = label
            group_types[reference.segment_index][reference.group_index] = note_type
            aligned_types[reference.segment_index][reference.group_index] = note_type
            aligned_line_indexes[reference.segment_index][reference.group_index] = (
                block.token_line_indexes[canonical_index]
            )

    corrections: list[SegmentLyricCorrection] = []
    for segment in segments:
        segment_aligned_types = aligned_types[segment.index]
        segment_line_indexes = aligned_line_indexes[segment.index]
        authoritative = tuple(
            group_labels[segment.index][group_index]
            for group_index, note_type in enumerate(segment_aligned_types)
            if note_type == 2
        )
        line_indexes = tuple(
            dict.fromkeys(
                line_index
                for line_index in segment_line_indexes
                if line_index is not None
            )
        )

        # Each metadata slice is synthesized independently. If a melisma began
        # in the preceding slice, the first local group must restart as type 2;
        # retaining its verified label while normalizing only this boundary is
        # the representable equivalent of a cross-slice sustain.
        if group_types[segment.index] and group_types[segment.index][0] == 3:
            group_types[segment.index][0] = 2

        corrected_tokens, corrected_types = _expand_group_corrections(
            segment,
            tuple(group_labels[segment.index]),
            tuple(group_types[segment.index]),
        )

        exact_matches = sum(
            1
            for group_index, note_type in enumerate(segment_aligned_types)
            if note_type == 2
            and segment.observations[group_index]
            == group_labels[segment.index][group_index]
        )
        onset_count = len(authoritative)
        extension_indexes = tuple(
            group_index
            for group_index, note_type in enumerate(segment_aligned_types)
            if note_type == 3
        )
        local_cost = (
            4 * (onset_count - exact_matches)
            + sum(
                0
                if segment.observations[group_index]
                == group_labels[segment.index][group_index]
                else 3
                for group_index in extension_indexes
            )
        )
        maximum_local_cost = 4 * onset_count + 3 * len(extension_indexes)
        coverage = exact_matches / onset_count if onset_count else 1.0
        cost_ratio = local_cost / maximum_local_cost if maximum_local_cost else 0.0
        onset_cost_ratio = (
            (onset_count - exact_matches) / onset_count if onset_count else 0.0
        )
        # SoulX synthesizes each metadata slice independently. Rechecking its
        # canonical onsets prevents strong neighboring slices from masking a
        # completely unrelated local transcription. Sustain observations are
        # excluded because a legitimate cross-slice melisma can retain the
        # preceding lyric while the recognizer proposes a new word.
        if onset_count and (
            coverage < minimum_exact_match_ratio
            or onset_cost_ratio > maximum_cost_ratio
        ):
            _raise(
                "low_coverage",
                "A target metadata segment has insufficient local lyric overlap.",
            )
        corrections.append(
            SegmentLyricCorrection(
                segment_index=segment.index,
                source_text=segment.source_text,
                source_note_type=segment.source_note_type,
                source_language=segment.language,
                source_time=(segment.start_ms, segment.end_ms),
                source_duration=segment.source_duration,
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
        record_time = record.get("time")
        language = record.get("language")
        if (
            correction.segment_index != segment_index
            or record.get("text") != correction.source_text
            or record.get("note_type") != correction.source_note_type
            or language != correction.source_language
            or record.get("duration") != correction.source_duration
            or not isinstance(record_time, (list, tuple))
            or len(record_time) != 2
            or any(type(value) is not int for value in record_time)
            or (record_time[0], record_time[1]) != correction.source_time
        ):
            _raise(
                "stale_plan",
                "Metadata text, language, note types, or timing changed after planning.",
            )
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
