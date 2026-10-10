"""Score and select private SoulX singing candidates without loading models.

This module is the deterministic quality core for lyrics-driven singing.  It
parses already validated prompt-manifest records, derives target features,
plans a bounded rescue search, measures raw mono waveforms, applies hard
quality gates before ranking, chooses a globally coherent candidate path, and
places that path on an exact output timeline.  It deliberately performs no
filesystem validation, model inference, network access, or audio publication;
the private WSL runtime remains responsible for those trust boundaries.

NumPy is imported only inside waveform functions so manifest parsing and seed
planning remain usable in lightweight control processes.  The pitch estimator
is a bounded autocorrelation proxy intended for candidate comparison and hard
failure detection, not a replacement for the independently pinned RMVPE and
ASR acceptance oracles used by the surrounding singing runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import unicodedata
from typing import Any, Mapping, Sequence


_PROMPT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SPECIAL_PHONEMES = frozenset(
    {"<AP>", "<BOW>", "<EOW>", "<PAD>", "<SEP>", "<SP>"}
)
_VOLATILE_SEED_KEYS = frozenset(
    {
        "directory",
        "jobdir",
        "jobid",
        "jobroot",
        "jobtoken",
        "path",
        "requestid",
        "requestuuid",
        "runid",
        "sessionid",
        "sourcepath",
        "taskid",
        "traceid",
        "uuid",
    }
)
_AUTOMATIC_HEADROOM_TARGET_PEAK = 0.994
_MAX_AUTOMATIC_ATTENUATION_DB = 6.0
_HEADROOM_DB_TOLERANCE = 1e-6


class QualityManifestError(ValueError):
    """Report malformed prompt metadata without touching the referenced path."""


class QualitySelectionError(RuntimeError):
    """Report that no hard-gate-safe global candidate path exists."""


@dataclass(frozen=True, slots=True)
class PromptAsset:
    """Describe one prompt locator and its manifest-authenticated identity.

    The path is a locator, not a durable security capability.  The inference
    boundary must reopen the asset without following links and authenticate the
    same descriptor that it passes to the private model pipeline.
    """

    path: Path
    byte_count: int
    sha256: str

    @classmethod
    def from_manifest(
        cls,
        value: Mapping[str, object],
        *,
        validated_path: Path | None = None,
    ) -> "PromptAsset":
        """Parse an asset record while trusting path validation to the runtime."""

        if not isinstance(value, Mapping):
            raise QualityManifestError("Prompt asset must be an object.")
        for key in ("path", "bytes", "sha256"):
            if key not in value:
                raise QualityManifestError(f"Prompt asset is missing {key!r}.")
        raw_path = value["path"]
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise QualityManifestError("Prompt asset path must be non-empty text.")
        byte_count = value["bytes"]
        if type(byte_count) is not int or byte_count <= 0:
            raise QualityManifestError("Prompt asset byte count must be positive.")
        digest = value["sha256"]
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise QualityManifestError("Prompt asset SHA-256 must be lowercase hex.")
        candidate_path = validated_path if validated_path is not None else Path(raw_path)
        return cls(candidate_path, byte_count, digest)


@dataclass(frozen=True, slots=True)
class PromptProfile:
    """Hold precomputed acoustic and phoneme features for one private prompt."""

    duration_seconds: float
    note_median: float
    note_p10: float
    note_p90: float
    note_span: float
    syllables_per_second: float
    phonemes: frozenset[str]

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> "PromptProfile":
        """Parse and cross-check the bounded profile portion of a prompt entry."""

        if not isinstance(value, Mapping):
            raise QualityManifestError("Prompt profile must be an object.")
        numeric_names = (
            "duration_seconds",
            "note_median",
            "note_p10",
            "note_p90",
            "note_span",
            "syllables_per_second",
        )
        parsed: dict[str, float] = {}
        for name in numeric_names:
            raw = value.get(name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise QualityManifestError(f"Prompt profile {name!r} must be numeric.")
            number = float(raw)
            if not math.isfinite(number):
                raise QualityManifestError(f"Prompt profile {name!r} must be finite.")
            parsed[name] = number
        if not 0.1 <= parsed["duration_seconds"] <= 120.0:
            raise QualityManifestError("Prompt duration is outside the supported bound.")
        if not 0.0 <= parsed["syllables_per_second"] <= 20.0:
            raise QualityManifestError("Prompt syllable rate is outside the supported bound.")
        if not (
            0.0 <= parsed["note_p10"]
            <= parsed["note_median"]
            <= parsed["note_p90"]
            <= 127.0
        ):
            raise QualityManifestError("Prompt note quantiles are inconsistent.")
        if not 0.0 <= parsed["note_span"] <= 127.0:
            raise QualityManifestError("Prompt note span is outside the supported bound.")
        if parsed["note_span"] + 1e-6 < parsed["note_p90"] - parsed["note_p10"]:
            raise QualityManifestError("Prompt note span contradicts its quantiles.")
        raw_phonemes = value.get("phonemes")
        if (
            isinstance(raw_phonemes, (str, bytes))
            or not isinstance(raw_phonemes, Sequence)
            or not raw_phonemes
        ):
            raise QualityManifestError("Prompt phonemes must be a non-empty list.")
        phonemes: set[str] = set()
        for raw_phoneme in raw_phonemes:
            if not isinstance(raw_phoneme, str) or not raw_phoneme.strip():
                raise QualityManifestError("Prompt phonemes must be non-empty text.")
            phoneme = unicodedata.normalize("NFC", raw_phoneme.strip())
            if len(phoneme) > 64:
                raise QualityManifestError("Prompt phoneme is too long.")
            phonemes.add(phoneme)
        return cls(
            duration_seconds=parsed["duration_seconds"],
            note_median=parsed["note_median"],
            note_p10=parsed["note_p10"],
            note_p90=parsed["note_p90"],
            note_span=parsed["note_span"],
            syllables_per_second=parsed["syllables_per_second"],
            phonemes=frozenset(phonemes),
        )


@dataclass(frozen=True, slots=True)
class PromptRecord:
    """Represent one selectable prompt whose files were validated elsewhere."""

    prompt_id: str
    role: str
    selection_rank: int
    style_proxy: str
    emotion: str | None
    audio: PromptAsset
    metadata: PromptAsset
    profile: PromptProfile

    @classmethod
    def from_manifest_entry(
        cls,
        value: Mapping[str, object],
        *,
        validated_audio_path: Path | None = None,
        validated_metadata_path: Path | None = None,
    ) -> "PromptRecord":
        """Parse the minimum private prompt-manifest entry contract.

        The optional paths are the canonical paths already checked by the WSL
        runtime.  Supplying them prevents an untrusted serialized path from
        crossing into later orchestration while keeping this pure parser free
        of filesystem policy.
        """

        if not isinstance(value, Mapping):
            raise QualityManifestError("Prompt entry must be an object.")
        required = {
            "id",
            "role",
            "selection_rank",
            "style_proxy",
            "emotion",
            "audio",
            "metadata",
            "profile",
        }
        missing = required - set(value)
        if missing:
            raise QualityManifestError(
                f"Prompt entry is missing {sorted(missing)[0]!r}."
            )
        prompt_id = value["id"]
        if (
            not isinstance(prompt_id, str)
            or _PROMPT_ID_PATTERN.fullmatch(prompt_id) is None
        ):
            raise QualityManifestError("Prompt id has an invalid shape.")
        role = value["role"]
        style_proxy = value["style_proxy"]
        if not isinstance(role, str) or not role.strip() or len(role) > 64:
            raise QualityManifestError("Prompt role must be bounded text.")
        if (
            not isinstance(style_proxy, str)
            or not style_proxy.strip()
            or len(style_proxy) > 64
        ):
            raise QualityManifestError("Prompt style proxy must be bounded text.")
        selection_rank = value["selection_rank"]
        if type(selection_rank) is not int or not 0 <= selection_rank <= 10_000:
            raise QualityManifestError("Prompt selection rank is unsupported.")
        emotion = value["emotion"]
        if emotion is not None and (
            not isinstance(emotion, str) or not emotion.strip() or len(emotion) > 64
        ):
            raise QualityManifestError("Prompt emotion must be null or bounded text.")
        audio_value = value["audio"]
        metadata_value = value["metadata"]
        profile_value = value["profile"]
        if not isinstance(audio_value, Mapping):
            raise QualityManifestError("Prompt audio record must be an object.")
        if not isinstance(metadata_value, Mapping):
            raise QualityManifestError("Prompt metadata record must be an object.")
        if not isinstance(profile_value, Mapping):
            raise QualityManifestError("Prompt profile record must be an object.")
        return cls(
            prompt_id=prompt_id,
            role=unicodedata.normalize("NFC", role.strip()),
            selection_rank=selection_rank,
            style_proxy=unicodedata.normalize("NFC", style_proxy.strip()),
            emotion=(
                unicodedata.normalize("NFC", emotion.strip())
                if isinstance(emotion, str)
                else None
            ),
            audio=PromptAsset.from_manifest(
                audio_value,
                validated_path=validated_audio_path,
            ),
            metadata=PromptAsset.from_manifest(
                metadata_value,
                validated_path=validated_metadata_path,
            ),
            profile=PromptProfile.from_manifest(profile_value),
        )


@dataclass(frozen=True, slots=True)
class TargetFeatures:
    """Summarize score and phoneme evidence used for deterministic prompt choice."""

    duration_seconds: float
    note_median: float
    note_p10: float
    note_p90: float
    note_span: float
    syllables_per_second: float
    phonemes: frozenset[str]
    style_proxy: str | None = None
    emotion: str | None = None

    @classmethod
    def from_metadata(
        cls,
        metadata: Sequence[Mapping[str, object]],
        *,
        style_proxy: str | None = None,
        emotion: str | None = None,
    ) -> "TargetFeatures":
        """Derive bounded target features from corrected SoulX metadata."""

        if (
            isinstance(metadata, (str, bytes))
            or not isinstance(metadata, Sequence)
            or not metadata
        ):
            raise ValueError("Target metadata must be a non-empty sequence.")
        pitches: list[float] = []
        phonemes: set[str] = set()
        onset_count = 0
        total_duration = 0.0
        for record in metadata:
            if not isinstance(record, Mapping):
                raise ValueError("Every target metadata record must be an object.")
            raw_pitch = record.get("note_pitch")
            raw_type = record.get("note_type")
            raw_duration = record.get("duration")
            raw_phoneme = record.get("phoneme")
            if not (
                isinstance(raw_pitch, str)
                and isinstance(raw_type, str)
                and isinstance(raw_duration, str)
                and isinstance(raw_phoneme, str)
            ):
                raise ValueError("Target note arrays must be whitespace-delimited text.")
            try:
                note_pitches = [float(token) for token in raw_pitch.split()]
                note_types = [int(token) for token in raw_type.split()]
                durations = [float(token) for token in raw_duration.split()]
            except ValueError as error:
                raise ValueError("Target note arrays contain malformed values.") from error
            phones = raw_phoneme.split()
            if not (
                note_pitches
                and len(note_pitches)
                == len(note_types)
                == len(durations)
                == len(phones)
            ):
                raise ValueError("Target note arrays have inconsistent lengths.")
            for pitch, note_type, duration, phoneme in zip(
                note_pitches,
                note_types,
                durations,
                phones,
                strict=True,
            ):
                if (
                    not math.isfinite(pitch)
                    or not 0.0 <= pitch <= 127.0
                    or not math.isfinite(duration)
                    or duration <= 0.0
                    or note_type not in {1, 2, 3}
                ):
                    raise ValueError("Target note values are outside supported bounds.")
                total_duration += duration
                if note_type != 1 and pitch > 0.0:
                    pitches.append(pitch)
                if note_type == 2:
                    onset_count += 1
                normalized_phone = unicodedata.normalize("NFC", phoneme)
                if normalized_phone not in _SPECIAL_PHONEMES:
                    phonemes.add(normalized_phone)
        if not pitches or not phonemes or not 0.1 <= total_duration <= 12 * 60:
            raise ValueError("Target metadata has no bounded voiced content.")
        ordered = sorted(pitches)
        normalized_style = _optional_label(style_proxy, "Target style proxy")
        normalized_emotion = _optional_label(emotion, "Target emotion")
        note_p10 = _quantile(ordered, 0.1)
        note_p90 = _quantile(ordered, 0.9)
        return cls(
            duration_seconds=total_duration,
            note_median=_quantile(ordered, 0.5),
            note_p10=note_p10,
            note_p90=note_p90,
            note_span=note_p90 - note_p10,
            syllables_per_second=onset_count / total_duration,
            phonemes=frozenset(phonemes),
            style_proxy=normalized_style,
            emotion=normalized_emotion,
        )


@dataclass(frozen=True, slots=True)
class PromptSelection:
    """Expose one deterministic prompt ranking without private audio content."""

    prompt: PromptRecord
    acoustic_cost: float
    missing_phoneme_rate: float
    emotion_match: bool
    style_match: bool


def select_prompts(
    prompts: Sequence[PromptRecord],
    target: TargetFeatures,
    *,
    limit: int = 2,
) -> tuple[PromptSelection, ...]:
    """Rank unique prompt assets, using labels only as acoustic tie-breaks.

    Manually curated emotion and style labels never compensate for a worse
    acoustic match.  They are considered only after the register/phoneme/rate
    score has been quantized, which prevents subjective labels from silently
    changing the target melody.  Aliases that authenticate the same audio and
    metadata bytes are collapsed after ranking so a manifest alias cannot
    consume the second-prompt rescue slot.
    """

    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("Prompt selection limit must be between one and eight.")
    if not prompts:
        raise ValueError("At least one prompt is required.")
    seen: set[str] = set()
    selections: list[PromptSelection] = []
    for prompt in prompts:
        if prompt.prompt_id in seen:
            raise ValueError("Prompt ids must be unique.")
        seen.add(prompt.prompt_id)
        missing = target.phonemes - prompt.profile.phonemes
        missing_rate = len(missing) / len(target.phonemes)
        register_cost = (
            abs(target.note_median - prompt.profile.note_median) / 12.0
            + abs(target.note_p10 - prompt.profile.note_p10) / 24.0
            + abs(target.note_p90 - prompt.profile.note_p90) / 24.0
            + abs(target.note_span - prompt.profile.note_span) / 48.0
        )
        rate_cost = abs(
            target.syllables_per_second - prompt.profile.syllables_per_second
        ) / 4.0
        acoustic_cost = register_cost + (4.0 * missing_rate) + (0.25 * rate_cost)
        selections.append(
            PromptSelection(
                prompt=prompt,
                acoustic_cost=round(acoustic_cost, 6),
                missing_phoneme_rate=missing_rate,
                emotion_match=(
                    target.emotion is not None and prompt.emotion == target.emotion
                ),
                style_match=(
                    target.style_proxy is not None
                    and prompt.style_proxy == target.style_proxy
                ),
            )
        )
    selections.sort(
        key=lambda selection: (
            selection.acoustic_cost,
            round(selection.missing_phoneme_rate, 6),
            not selection.emotion_match,
            not selection.style_match,
            selection.prompt.selection_rank,
            selection.prompt.prompt_id,
        )
    )
    unique_selections: list[PromptSelection] = []
    seen_assets: set[tuple[int, str, int, str]] = set()
    for selection in selections:
        prompt = selection.prompt
        # Paths are mutable locators.  The authenticated byte counts and
        # digests identify the two assets even when aliases use different ids
        # or filesystem spellings; sorting first retains the best alias.
        asset_identity = (
            prompt.audio.byte_count,
            prompt.audio.sha256,
            prompt.metadata.byte_count,
            prompt.metadata.sha256,
        )
        if asset_identity in seen_assets:
            continue
        seen_assets.add(asset_identity)
        unique_selections.append(selection)
        if len(unique_selections) == limit:
            break
    return tuple(unique_selections)


def canonical_candidate_seed(
    *,
    target_audio_sha256: str,
    segment_metadata: Mapping[str, object] | Sequence[object],
    prompt_sha256: str,
    candidate_ordinal: int,
    engine_version: str = "elysia-svs-quality-v1",
) -> int:
    """Derive a stable 63-bit seed that ignores job UUIDs and local paths.

    Known orchestration-only keys are removed recursively before canonical JSON
    encoding.  Callers must still pass target metadata rather than an arbitrary
    job manifest; audio and prompt identities enter only through their digests.
    """

    if _SHA256_PATTERN.fullmatch(target_audio_sha256) is None:
        raise ValueError("Target audio SHA-256 must be lowercase hex.")
    if _SHA256_PATTERN.fullmatch(prompt_sha256) is None:
        raise ValueError("Prompt SHA-256 must be lowercase hex.")
    if type(candidate_ordinal) is not int or not 0 <= candidate_ordinal <= 2:
        raise ValueError("Candidate ordinal must be zero, one, or two.")
    if not isinstance(engine_version, str) or not engine_version.strip():
        raise ValueError("Engine version must be non-empty text.")
    portable_metadata = _portable_seed_value(segment_metadata)
    try:
        encoded_metadata = json.dumps(
            portable_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Segment metadata is not canonically serializable.") from error
    digest = hashlib.sha256()
    for component in (
        unicodedata.normalize("NFC", engine_version.strip()).encode("utf-8"),
        target_audio_sha256.encode("ascii"),
        prompt_sha256.encode("ascii"),
        str(candidate_ordinal).encode("ascii"),
        encoded_metadata,
    ):
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return int.from_bytes(digest.digest()[:8], "big") & ((1 << 63) - 1)


@dataclass(frozen=True, slots=True)
class CandidateBudget:
    """Bound adaptive candidate generation independently of song length."""

    max_candidates_per_segment: int = 3
    max_rescue_segments: int = 12
    rescue_fraction: float = 0.25
    max_rescue_seconds: float = 90.0

    def __post_init__(self) -> None:
        """Reject values that would bypass the reviewed search ceiling."""

        if type(self.max_candidates_per_segment) is not int or not (
            1 <= self.max_candidates_per_segment <= 3
        ):
            raise ValueError("Candidate count must be between one and three.")
        if type(self.max_rescue_segments) is not int or not (
            0 <= self.max_rescue_segments <= 12
        ):
            raise ValueError("Rescue segment count is outside the reviewed bound.")
        if not 0.0 <= self.rescue_fraction <= 0.25:
            raise ValueError("Rescue fraction is outside the reviewed bound.")
        if not 0.0 <= self.max_rescue_seconds <= 90.0:
            raise ValueError("Rescue seconds are outside the reviewed bound.")


DEFAULT_CANDIDATE_BUDGET = CandidateBudget()


@dataclass(frozen=True, slots=True)
class RescueRequest:
    """Describe one segment eligible for additional deterministic candidates."""

    segment_index: int
    duration_seconds: float
    severity: float
    candidate_ordinals: tuple[int, ...] = (1, 2)

    def __post_init__(self) -> None:
        """Validate bounded request values before budget allocation."""

        if type(self.segment_index) is not int or self.segment_index < 0:
            raise ValueError("Segment index must be a non-negative integer.")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0.0:
            raise ValueError("Rescue duration must be positive and finite.")
        if not math.isfinite(self.severity) or self.severity < 0.0:
            raise ValueError("Rescue severity must be non-negative and finite.")
        if (
            not self.candidate_ordinals
            or len(set(self.candidate_ordinals)) != len(self.candidate_ordinals)
            or tuple(sorted(self.candidate_ordinals)) != self.candidate_ordinals
            or any(ordinal not in (1, 2) for ordinal in self.candidate_ordinals)
        ):
            raise ValueError("Rescue candidate ordinals must be unique ordered C1/C2 slots.")


@dataclass(frozen=True, slots=True)
class RescueAllocation:
    """Record fixed rescue ordinals granted to one target segment."""

    segment_index: int
    candidate_ordinals: tuple[int, ...]


def plan_rescue_candidates(
    requests: Sequence[RescueRequest],
    *,
    total_voiced_seconds: float,
    budget: CandidateBudget = DEFAULT_CANDIDATE_BUDGET,
) -> tuple[RescueAllocation, ...]:
    """Allocate available C1/C2 slots under one bounded global time budget.

    The fractional budget normally controls added rendering work.  A single
    highest-priority segment can itself exceed that fraction, so that request
    is allowed once when it still fits the absolute seconds ceiling.  Binding
    the exception to priority keeps a lower-severity short request from
    displacing the hard failure that made rescue necessary.  Callers declare
    available ordinals so nonexistent C2 prompts never consume budget.
    """

    if not math.isfinite(total_voiced_seconds) or total_voiced_seconds < 0.0:
        raise ValueError("Total voiced duration must be non-negative and finite.")
    by_index: dict[int, RescueRequest] = {}
    for request in requests:
        if request.segment_index in by_index:
            raise ValueError("Rescue requests must have unique segment indexes.")
        by_index[request.segment_index] = request
    ordered = sorted(requests, key=lambda item: (-item.severity, item.segment_index))
    ordered = ordered[: budget.max_rescue_segments]
    fractional_seconds = min(
        budget.max_rescue_seconds,
        total_voiced_seconds * budget.rescue_fraction,
    )
    allowed_seconds = fractional_seconds
    priority_request = ordered[0] if ordered else None
    priority_ordinal = (
        next(
            (
                ordinal
                for ordinal in priority_request.candidate_ordinals
                if ordinal < budget.max_candidates_per_segment
            ),
            None,
        )
        if priority_request is not None
        else None
    )
    exception_request: RescueRequest | None = None
    exception_ordinal: int | None = None
    if (
        priority_request is not None
        and priority_ordinal is not None
        and priority_request.duration_seconds > fractional_seconds + 1e-9
        and priority_request.duration_seconds <= budget.max_rescue_seconds
    ):
        exception_request = priority_request
        exception_ordinal = priority_ordinal
        allowed_seconds = exception_request.duration_seconds
    used_seconds = 0.0
    slots: dict[int, list[int]] = {item.segment_index: [] for item in ordered}
    if exception_request is not None and exception_ordinal is not None:
        # Reserve the one over-fraction render before ordinary round-robin
        # allocation so a lower-priority C1 cannot consume its capacity.
        slots[exception_request.segment_index].append(exception_ordinal)
        used_seconds = exception_request.duration_seconds
    for ordinal in range(1, budget.max_candidates_per_segment):
        for request in ordered:
            if ordinal not in request.candidate_ordinals:
                continue
            if ordinal in slots[request.segment_index]:
                continue
            if used_seconds + request.duration_seconds > allowed_seconds + 1e-9:
                continue
            slots[request.segment_index].append(ordinal)
            used_seconds += request.duration_seconds
    return tuple(
        RescueAllocation(index, tuple(slots[index]))
        for index in sorted(slots)
        if slots[index]
    )


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Define reviewed hard gates for one unmixed generated vocal segment."""

    max_length_error_seconds: float = 0.020
    max_peak: float = 0.995
    max_clipped_fraction: float = 0.0001
    max_abs_dc_offset: float = 0.01
    min_active_rms_dbfs: float = -80.0
    max_silence_rms_dbfs: float = -45.0
    min_voiced_recall: float = 0.88
    max_dropout_rate: float = 0.12
    max_false_voicing_rate: float = 0.03
    max_false_voicing_run_seconds: float = 0.120
    max_pitch_median_cents: float = 50.0
    max_pitch_p90_cents: float = 120.0
    max_gross_pitch_error_rate: float = 0.10
    max_octave_error_rate: float = 0.01
    max_spectral_flatness_p95: float = 0.45
    max_high_frequency_ratio_p95: float = 0.35
    max_spectral_flux_p95: float = 0.90

    def __post_init__(self) -> None:
        """Ensure every threshold is finite and directionally meaningful."""

        values = tuple(self.__dict__.values()) if hasattr(self, "__dict__") else (
            self.max_length_error_seconds,
            self.max_peak,
            self.max_clipped_fraction,
            self.max_abs_dc_offset,
            self.min_active_rms_dbfs,
            self.max_silence_rms_dbfs,
            self.min_voiced_recall,
            self.max_dropout_rate,
            self.max_false_voicing_rate,
            self.max_false_voicing_run_seconds,
            self.max_pitch_median_cents,
            self.max_pitch_p90_cents,
            self.max_gross_pitch_error_rate,
            self.max_octave_error_rate,
            self.max_spectral_flatness_p95,
            self.max_high_frequency_ratio_p95,
            self.max_spectral_flux_p95,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("Quality thresholds must be finite.")
        rates = (
            self.max_clipped_fraction,
            self.min_voiced_recall,
            self.max_dropout_rate,
            self.max_false_voicing_rate,
            self.max_gross_pitch_error_rate,
            self.max_octave_error_rate,
            self.max_spectral_flatness_p95,
            self.max_high_frequency_ratio_p95,
            self.max_spectral_flux_p95,
        )
        if any(not 0.0 <= value <= 1.0 for value in rates):
            raise ValueError("Quality rate thresholds must lie between zero and one.")
        if not (
            self.max_length_error_seconds >= 0.0
            and 0.0 < self.max_peak <= 1.0
            and self.max_abs_dc_offset >= 0.0
            and self.max_false_voicing_run_seconds >= 0.0
            and self.max_pitch_median_cents >= 0.0
            and self.max_pitch_p90_cents >= self.max_pitch_median_cents
        ):
            raise ValueError("Quality thresholds have inconsistent bounds.")


DEFAULT_QUALITY_THRESHOLDS = QualityThresholds()


@dataclass(frozen=True, slots=True)
class WaveformMetrics:
    """Contain objective measurements for one raw mono candidate waveform."""

    sample_count: int
    expected_samples: int
    length_error_samples: int
    nonfinite_count: int
    peak: float
    required_attenuation_db: float
    clipped_fraction: float
    flat_top_max_run_samples: int
    dc_offset: float
    active_rms_dbfs: float
    expected_voiced_frames: int
    expected_silence_frames: int
    voiced_recall: float | None
    dropout_rate: float | None
    false_voicing_rate: float | None
    false_voicing_max_run_seconds: float | None
    silence_rms_dbfs: float | None
    pitch_median_abs_cents: float | None
    pitch_p90_abs_cents: float | None
    gross_pitch_error_rate: float | None
    octave_error_rate: float | None
    spectral_flatness_p95: float
    high_frequency_ratio_p95: float
    spectral_flux_p95: float


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    """Record hard-gate outcome and subordinate candidate-ranking loss."""

    passed: bool
    violations: tuple[str, ...]
    local_loss: float

    def __post_init__(self) -> None:
        """Keep manually constructed assessments consistent with hard gates."""

        if not math.isfinite(self.local_loss) or self.local_loss < 0.0:
            raise ValueError("Candidate local loss must be non-negative and finite.")
        if self.passed == bool(self.violations):
            raise ValueError("Assessment pass state contradicts its violations.")


def analyze_waveform(
    waveform: object,
    *,
    sample_rate: int,
    expected_samples: int,
    expected_f0_hz: object | None = None,
    expected_voiced_mask: object | None = None,
    expected_silence_mask: object | None = None,
) -> WaveformMetrics:
    """Measure structure, pitch, silence, and spectrum from raw mono samples.

    Reference F0 and masks may be scalars, one value per sample, or one value
    per analysis frame.  Pitch uses a deterministic FFT autocorrelation proxy;
    production integration should retain RMVPE and an independent pYIN veto as
    external acceptance evidence.
    """

    np = _import_numpy()
    if type(sample_rate) is not int or not 8_000 <= sample_rate <= 192_000:
        raise ValueError("Sample rate is outside the supported bound.")
    if type(expected_samples) is not int or expected_samples <= 0:
        raise ValueError("Expected sample count must be positive.")
    samples = np.asarray(waveform, dtype=np.float32)
    if samples.ndim != 1 or samples.size == 0:
        raise ValueError("Candidate waveform must be a non-empty mono vector.")
    nonfinite_count = int(np.count_nonzero(~np.isfinite(samples)))
    clean = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    sample_count = int(clean.size)
    peak = float(np.max(np.abs(clean)))
    required_attenuation_db = _required_attenuation_db(peak)
    clipped_fraction, flat_top_max_run_samples = _flat_top_evidence(
        clean,
        sample_rate,
        np,
    )
    dc_offset = float(np.mean(clean))
    active_rms_dbfs = _dbfs(float(np.sqrt(np.mean(np.square(clean)))))

    frame_length = max(32, int(round(sample_rate * 0.040)))
    hop_length = max(16, int(round(sample_rate * 0.020)))
    frames, starts = _frame_signal(clean, frame_length, hop_length, np)
    frame_count = len(frames)
    f0_reference = _frame_numeric_reference(
        expected_f0_hz,
        sample_count,
        starts,
        frame_length,
        frame_count,
        np,
    )
    voiced_reference = _frame_mask_reference(
        expected_voiced_mask,
        sample_count,
        starts,
        frame_length,
        frame_count,
        np,
    )
    if voiced_reference is None and f0_reference is not None:
        voiced_reference = f0_reference > 0.0
    silence_reference = _frame_mask_reference(
        expected_silence_mask,
        sample_count,
        starts,
        frame_length,
        frame_count,
        np,
    )
    # Non-pitched frames include consonants, breaths, and note transitions.
    # Only an explicit score-silence mask may label them as digital silence.

    detected_f0: list[float] = []
    frame_rms_db: list[float] = []
    flatness: list[float] = []
    high_frequency_ratio: list[float] = []
    normalized_spectra: list[Any | None] = []
    frequencies = np.fft.rfftfreq(frame_length, 1.0 / sample_rate)
    high_frequency_cutoff = min(8_000.0, sample_rate * 0.45)
    spectral_window = np.hanning(frame_length)
    for frame in frames:
        rms = float(np.sqrt(np.mean(np.square(frame))))
        rms_db = _dbfs(rms)
        frame_rms_db.append(rms_db)
        detected_f0.append(_estimate_f0(frame, sample_rate, rms_db, np))
        spectrum = np.abs(np.fft.rfft((frame - np.mean(frame)) * spectral_window))
        power = np.square(spectrum)
        if rms_db > -60.0 and float(np.sum(power)) > 0.0:
            positive = power + 1e-18
            flatness.append(
                float(np.exp(np.mean(np.log(positive))) / np.mean(positive))
            )
            denominator = float(np.sum(power[frequencies >= 300.0]))
            numerator = float(
                np.sum(power[frequencies >= high_frequency_cutoff])
            )
            high_frequency_ratio.append(numerator / denominator if denominator else 0.0)
            norm = float(np.linalg.norm(spectrum))
            normalized_spectra.append(spectrum / norm if norm else spectrum)
        else:
            # Preserve frame adjacency so silence cannot join unrelated spectra.
            normalized_spectra.append(None)

    spectral_flux: list[float] = []
    for previous, current in zip(normalized_spectra, normalized_spectra[1:], strict=False):
        if previous is not None and current is not None:
            # L2-normalized spectra have a maximum distance of sqrt(2), so this
            # scale is comparable across sample rates and FFT sizes.
            spectral_flux.append(
                min(1.0, float(np.linalg.norm(current - previous) / math.sqrt(2.0)))
            )

    f0_array = np.asarray(detected_f0, dtype=np.float64)
    detected_voiced = f0_array > 0.0
    expected_voiced_frames = (
        int(np.count_nonzero(voiced_reference))
        if voiced_reference is not None
        else 0
    )
    expected_silence_frames = (
        int(np.count_nonzero(silence_reference))
        if silence_reference is not None
        else 0
    )
    dropout_rate: float | None = None
    voiced_recall: float | None = None
    if expected_voiced_frames:
        dropout_rate = float(np.mean(~detected_voiced[voiced_reference]))
        voiced_recall = 1.0 - dropout_rate
        active_rms_dbfs = float(
            np.median(np.asarray(frame_rms_db)[voiced_reference])
        )
    false_voicing_rate: float | None = None
    false_voicing_run: float | None = None
    silence_rms: float | None = None
    if expected_silence_frames:
        false_flags = detected_voiced & silence_reference
        false_voicing_rate = float(np.mean(detected_voiced[silence_reference]))
        false_voicing_run = _longest_true_run(false_flags) * hop_length / sample_rate
        silence_rms = float(np.median(np.asarray(frame_rms_db)[silence_reference]))

    pitch_errors: Any = np.asarray([], dtype=np.float64)
    if f0_reference is not None:
        comparable = (f0_reference > 0.0) & detected_voiced
        if np.any(comparable):
            pitch_errors = np.abs(
                1200.0 * np.log2(f0_array[comparable] / f0_reference[comparable])
            )
    pitch_median = float(np.median(pitch_errors)) if pitch_errors.size else None
    pitch_p90 = float(np.quantile(pitch_errors, 0.9)) if pitch_errors.size else None
    gross_rate = float(np.mean(pitch_errors > 200.0)) if pitch_errors.size else None
    octave_rate = float(np.mean(pitch_errors > 600.0)) if pitch_errors.size else None
    return WaveformMetrics(
        sample_count=sample_count,
        expected_samples=expected_samples,
        length_error_samples=abs(sample_count - expected_samples),
        nonfinite_count=nonfinite_count,
        peak=peak,
        required_attenuation_db=required_attenuation_db,
        clipped_fraction=clipped_fraction,
        flat_top_max_run_samples=flat_top_max_run_samples,
        dc_offset=dc_offset,
        active_rms_dbfs=active_rms_dbfs,
        expected_voiced_frames=expected_voiced_frames,
        expected_silence_frames=expected_silence_frames,
        voiced_recall=voiced_recall,
        dropout_rate=dropout_rate,
        false_voicing_rate=false_voicing_rate,
        false_voicing_max_run_seconds=false_voicing_run,
        silence_rms_dbfs=silence_rms,
        pitch_median_abs_cents=pitch_median,
        pitch_p90_abs_cents=pitch_p90,
        gross_pitch_error_rate=gross_rate,
        octave_error_rate=octave_rate,
        spectral_flatness_p95=_percentile_or_zero(flatness, 0.95, np),
        high_frequency_ratio_p95=_percentile_or_zero(
            high_frequency_ratio,
            0.95,
            np,
        ),
        spectral_flux_p95=_percentile_or_zero(spectral_flux, 0.95, np),
    )


def assess_waveform(
    metrics: WaveformMetrics,
    *,
    sample_rate: int,
    thresholds: QualityThresholds = DEFAULT_QUALITY_THRESHOLDS,
) -> QualityAssessment:
    """Apply calibrated structural gates and retain acoustic ranking evidence.

    The deterministic autocorrelation and spectrum metrics are useful for
    comparing candidates from the same score, but they have not been calibrated
    as universal acceptance oracles for real singing.  Pitch, voicing, silence,
    and spectral evidence therefore contributes only to ``local_loss``.  Hard
    rejection is limited to container-independent structural defects that are
    safe to diagnose for real vocals: non-finite samples, duration mismatch,
    clipping/peak overflow, DC bias, and effectively empty voiced output.
    """

    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    violations: list[str] = []
    max_length_error = int(round(sample_rate * thresholds.max_length_error_seconds))
    checks = (
        (metrics.nonfinite_count > 0, "nonfinite"),
        (metrics.length_error_samples > max_length_error, "duration"),
        # Unclamped model floats may exceed full scale before PCM publication.
        # Reject only when the single bounded final gain cannot create headroom.
        (
            metrics.required_attenuation_db
            > _MAX_AUTOMATIC_ATTENUATION_DB + _HEADROOM_DB_TOLERANCE,
            "headroom",
        ),
        # A qualifying near-peak plateau is direct flat-top evidence regardless
        # of song length; a fraction-only gate would miss short rails inside a
        # long segment because the denominator dilutes the defect.
        (metrics.flat_top_max_run_samples >= 4, "clipping"),
        (abs(metrics.dc_offset) > thresholds.max_abs_dc_offset, "dc_offset"),
        (
            metrics.expected_voiced_frames > 0
            and metrics.active_rms_dbfs < thresholds.min_active_rms_dbfs,
            "empty_audio",
        ),
    )
    for failed, code in checks:
        if failed:
            violations.append(code)
    loss_terms = (
        0.15
        * _monotonic_ratio_or_zero(
            metrics.pitch_p90_abs_cents,
            thresholds.max_pitch_p90_cents,
        ),
        0.05
        * _monotonic_ratio_or_zero(
            metrics.gross_pitch_error_rate,
            thresholds.max_gross_pitch_error_rate,
        ),
        0.05
        * _monotonic_ratio_or_zero(
            metrics.octave_error_rate,
            thresholds.max_octave_error_rate,
        ),
        0.15
        * _ratio_or_zero(metrics.dropout_rate, thresholds.max_dropout_rate),
        0.15
        * _ratio_or_zero(
            metrics.false_voicing_rate,
            thresholds.max_false_voicing_rate,
        ),
        0.15
        * _bounded_ratio(
            metrics.spectral_flatness_p95,
            thresholds.max_spectral_flatness_p95,
        ),
        0.10
        * _bounded_ratio(
            metrics.high_frequency_ratio_p95,
            thresholds.max_high_frequency_ratio_p95,
        ),
        0.05
        * _bounded_ratio(
            metrics.spectral_flux_p95,
            thresholds.max_spectral_flux_p95,
        ),
        0.10
        * _bounded_ratio(
            metrics.clipped_fraction,
            thresholds.max_clipped_fraction,
        ),
        0.05
        * _bounded_ratio(abs(metrics.dc_offset), thresholds.max_abs_dc_offset),
    )
    return QualityAssessment(
        passed=not violations,
        violations=tuple(violations),
        local_loss=round(sum(loss_terms), 6),
    )


@dataclass(frozen=True, slots=True, eq=False)
class SegmentCandidate:
    """Bind one scored segment waveform to its absolute output interval."""

    candidate_id: str
    prompt_id: str
    segment_index: int
    start_sample: int
    end_sample: int
    waveform: object
    assessment: QualityAssessment
    required_attenuation_db: float = 0.0

    def __post_init__(self) -> None:
        """Validate identifiers and timeline bounds without importing NumPy."""

        if not self.candidate_id or len(self.candidate_id) > 128:
            raise ValueError("Candidate id must be bounded text.")
        if _PROMPT_ID_PATTERN.fullmatch(self.prompt_id) is None:
            raise ValueError("Candidate prompt id has an invalid shape.")
        if type(self.segment_index) is not int or self.segment_index < 0:
            raise ValueError("Candidate segment index must be non-negative.")
        if (
            type(self.start_sample) is not int
            or type(self.end_sample) is not int
            or not 0 <= self.start_sample < self.end_sample
        ):
            raise ValueError("Candidate interval is invalid.")
        if (
            not math.isfinite(self.required_attenuation_db)
            or self.required_attenuation_db < 0.0
        ):
            raise ValueError(
                "Candidate headroom requirement must be finite and non-negative."
            )


_CandidatePathState = tuple[
    float,
    float,
    tuple[str, ...],
    tuple[SegmentCandidate, ...],
]


def _pareto_candidate_prefixes(
    options: Sequence[_CandidatePathState],
) -> tuple[_CandidatePathState, ...]:
    """Keep equal-cost prefixes that trade headroom for lexical stability.

    Once paths end on the same candidate, every future acoustic cost is
    identical, so a larger current cost is permanently dominated.  Headroom is
    a path maximum, however: a future louder candidate can erase an earlier
    headroom difference and make candidate ids the deciding tie-break.  The
    frontier must therefore retain each equal-cost prefix whose ids improve as
    headroom rises instead of retaining only today's minimum-headroom path.
    """

    if not options:
        return ()
    minimum_cost = min(option[0] for option in options)
    equal_cost = sorted(
        (option for option in options if option[0] == minimum_cost),
        key=lambda option: (option[1], option[2]),
    )
    frontier: list[_CandidatePathState] = []
    best_ids: tuple[str, ...] | None = None
    for option in equal_cost:
        if best_ids is not None and best_ids <= option[2]:
            continue
        frontier.append(option)
        best_ids = option[2]
    return tuple(frontier)


def select_candidate_path(
    candidate_groups: Sequence[Sequence[SegmentCandidate]],
    *,
    sample_rate: int,
) -> tuple[SegmentCandidate, ...]:
    """Choose the minimum-loss hard-safe path with bounded transition costs."""

    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    if not candidate_groups:
        raise QualitySelectionError("No candidate segments were supplied.")
    filtered: list[list[SegmentCandidate]] = []
    candidate_ids: set[str] = set()
    previous_index = -1
    for group in candidate_groups:
        if not group:
            raise QualitySelectionError("A target segment has no candidates.")
        indexes = {candidate.segment_index for candidate in group}
        if len(indexes) != 1:
            raise ValueError("Candidate group mixes target segment indexes.")
        intervals = {
            (candidate.start_sample, candidate.end_sample) for candidate in group
        }
        if len(intervals) != 1:
            raise ValueError("Candidates for one target must share an interval.")
        for candidate in group:
            if candidate.candidate_id in candidate_ids:
                raise ValueError("Candidate ids must be globally unique.")
            candidate_ids.add(candidate.candidate_id)
        index = next(iter(indexes))
        if index <= previous_index:
            raise ValueError("Candidate groups must be in increasing segment order.")
        previous_index = index
        accepted = sorted(
            (candidate for candidate in group if candidate.assessment.passed),
            key=lambda candidate: candidate.candidate_id,
        )
        if not accepted:
            raise QualitySelectionError("A target segment has no hard-safe candidate.")
        filtered.append(accepted)

    states: dict[str, tuple[_CandidatePathState, ...]] = {}
    for candidate in filtered[0]:
        duration = (candidate.end_sample - candidate.start_sample) / sample_rate
        states[candidate.candidate_id] = (
            (
                candidate.assessment.local_loss * duration,
                candidate.required_attenuation_db,
                (candidate.candidate_id,),
                (candidate,),
            ),
        )
    for group in filtered[1:]:
        next_states: dict[str, tuple[_CandidatePathState, ...]] = {}
        for candidate in group:
            options: list[_CandidatePathState] = []
            duration = (candidate.end_sample - candidate.start_sample) / sample_rate
            node_cost = candidate.assessment.local_loss * duration
            for prefixes in states.values():
                for (
                    previous_cost,
                    previous_headroom,
                    previous_ids,
                    previous_path,
                ) in prefixes:
                    transition = _transition_cost(
                        previous_path[-1],
                        candidate,
                        sample_rate,
                    )
                    if not math.isfinite(transition):
                        continue
                    options.append(
                        (
                            round(previous_cost + node_cost + transition, 9),
                            max(
                                previous_headroom,
                                candidate.required_attenuation_db,
                            ),
                            previous_ids + (candidate.candidate_id,),
                            previous_path + (candidate,),
                        )
                    )
            frontier = _pareto_candidate_prefixes(options)
            if frontier:
                next_states[candidate.candidate_id] = frontier
        if not next_states:
            raise QualitySelectionError("No boundary-safe candidate path exists.")
        states = next_states
    return min(
        (state for prefixes in states.values() for state in prefixes),
        key=lambda state: state[:3],
    )[3]


def stitch_candidate_path(
    path: Sequence[SegmentCandidate],
    *,
    total_samples: int,
    sample_rate: int,
    global_gain_db: float = 0.0,
) -> object:
    """Place candidates at absolute offsets and apply exactly one global gain.

    The function never overlaps, stretches, per-segment normalizes, or
    crossfades segments.  Gaps remain exact digital silence.  Five-millisecond
    edge ramps meet gaps and independently rendered contiguous boundaries at
    zero, preventing hard cuts without overlap or timestamp movement.  The
    requested attenuation is reduced further only when
    needed to create headroom, and the resulting single global multiplication
    may not attenuate by more than six dB.  This preserves relative dynamics
    while distinguishing correctable model-float overflow from hard clipping.
    """

    np = _import_numpy()
    if type(total_samples) is not int or total_samples <= 0:
        raise ValueError("Total sample count must be positive.")
    if type(sample_rate) is not int or sample_rate <= 0:
        raise ValueError("Sample rate must be a positive integer.")
    if not math.isfinite(global_gain_db) or not -6.0 <= global_gain_db <= 0.0:
        raise ValueError("Global attenuation must lie between minus six and zero dB.")
    if not path:
        raise QualitySelectionError("At least one selected candidate is required.")
    output = np.zeros(total_samples, dtype=np.float32)
    previous_end = 0
    fade_samples = max(1, int(round(sample_rate * 0.005)))
    for position, candidate in enumerate(path):
        if not candidate.assessment.passed:
            raise QualitySelectionError("A rejected candidate cannot be stitched.")
        if candidate.start_sample < previous_end or candidate.end_sample > total_samples:
            raise ValueError("Candidate intervals overlap or exceed the output timeline.")
        samples = np.asarray(candidate.waveform, dtype=np.float32)
        expected = candidate.end_sample - candidate.start_sample
        if samples.ndim != 1 or len(samples) != expected:
            raise ValueError("Candidate waveform does not match its exact interval.")
        if not np.all(np.isfinite(samples)):
            raise ValueError("Candidate waveform contains non-finite samples.")
        leading_boundary = position > 0 or candidate.start_sample > 0
        trailing_boundary = (
            position + 1 < len(path) or candidate.end_sample < total_samples
        )
        if leading_boundary or trailing_boundary:
            # Work on a private copy so DP candidates remain immutable and can
            # still be compared deterministically across alternative paths.
            samples = samples.copy()
            if leading_boundary and trailing_boundary and len(samples) < 3:
                raise ValueError("A doubly bounded candidate is too short to ramp safely.")
            maximum_edge = max(1, (len(samples) - 1) // 2)
            edge = min(fade_samples, maximum_edge)
            if leading_boundary:
                if edge == 1:
                    samples[0] = 0.0
                else:
                    samples[:edge] *= np.linspace(
                        0.0,
                        1.0,
                        edge,
                        dtype=np.float32,
                    )
            if trailing_boundary:
                if edge == 1:
                    samples[-1] = 0.0
                else:
                    samples[-edge:] *= np.linspace(
                        1.0,
                        0.0,
                        edge,
                        dtype=np.float32,
                    )
        output[candidate.start_sample : candidate.end_sample] = samples
        previous_end = candidate.end_sample
    raw_peak = float(np.max(np.abs(output)))
    safe_gain_db = global_gain_db
    if raw_peak > 0.0:
        headroom_gain_db = -_required_attenuation_db(raw_peak)
        safe_gain_db = min(safe_gain_db, headroom_gain_db)
    # Float32 peak measurement can place the analytical six-dB boundary a few
    # ten-millionths of a dB outside the exact decimal value.  Use the same
    # tolerance as candidate assessment, clamp within it, and let the final
    # 0.995 peak gate remain the publication authority.
    if safe_gain_db < -_MAX_AUTOMATIC_ATTENUATION_DB - _HEADROOM_DB_TOLERANCE:
        raise ValueError("Selected path exceeds the automatic headroom capacity.")
    safe_gain_db = max(-_MAX_AUTOMATIC_ATTENUATION_DB, safe_gain_db)
    gain = 10.0 ** (safe_gain_db / 20.0)
    output *= gain
    if float(np.max(np.abs(output))) > DEFAULT_QUALITY_THRESHOLDS.max_peak + 1e-6:
        raise ValueError("Global gain could not protect the selected path headroom.")
    return output


def _optional_label(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError(f"{label} must be null or bounded text.")
    return unicodedata.normalize("NFC", value.strip())


def _quantile(ordered: Sequence[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float((ordered[lower] * (1.0 - weight)) + (ordered[upper] * weight))


def _portable_seed_value(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Canonical metadata keys must be text.")
            normalized_key = unicodedata.normalize("NFC", key)
            folded = re.sub(r"[^a-z0-9]", "", normalized_key.casefold())
            if (
                folded in _VOLATILE_SEED_KEYS
                or folded.endswith("path")
                or folded.endswith("uuid")
            ):
                continue
            result[normalized_key] = _portable_seed_value(child)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_portable_seed_value(child) for child in value]
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        # A UUID may arrive under a vendor-specific key; its value is never
        # semantic score content and therefore must not perturb replay seeds.
        return "<uuid>" if _UUID_PATTERN.fullmatch(normalized) else normalized
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Canonical metadata numbers must be finite.")
        return value
    raise ValueError("Canonical metadata contains an unsupported value.")


def _import_numpy() -> Any:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("NumPy is required for singing quality analysis.") from error
    return np


def _dbfs(rms: float) -> float:
    if rms <= 0.0:
        return -120.0
    return max(-120.0, 20.0 * math.log10(rms))


def _frame_signal(
    samples: Any,
    frame_length: int,
    hop_length: int,
    np: Any,
) -> tuple[Any, list[int]]:
    starts = list(range(0, len(samples), hop_length))
    required = starts[-1] + frame_length
    # A strided view keeps analysis memory linear in audio duration.  Materializing
    # every overlapping 40 ms frame separately would retain roughly twice the
    # full-song PCM size again for a 20 ms hop and becomes costly at 12 minutes.
    padded = np.zeros(required, dtype=np.float32)
    padded[: len(samples)] = samples
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(len(starts), frame_length),
        strides=(padded.strides[0] * hop_length, padded.strides[0]),
        writeable=False,
    )
    return frames, starts


def _frame_numeric_reference(
    value: object | None,
    sample_count: int,
    starts: Sequence[int],
    frame_length: int,
    frame_count: int,
    np: Any,
) -> Any | None:
    if value is None:
        return None
    candidate = np.asarray(value, dtype=np.float64)
    if candidate.ndim == 0:
        return np.full(frame_count, float(candidate), dtype=np.float64)
    if candidate.ndim != 1:
        raise ValueError("Frame reference must be a scalar or one-dimensional vector.")
    if len(candidate) == frame_count:
        return candidate.copy()
    if len(candidate) != sample_count:
        raise ValueError("Frame reference length does not match samples or frames.")
    framed: list[float] = []
    for start in starts:
        values = candidate[start : min(sample_count, start + frame_length)]
        positive = values[values > 0.0]
        framed.append(float(np.median(positive)) if positive.size else 0.0)
    return np.asarray(framed, dtype=np.float64)


def _frame_mask_reference(
    value: object | None,
    sample_count: int,
    starts: Sequence[int],
    frame_length: int,
    frame_count: int,
    np: Any,
) -> Any | None:
    if value is None:
        return None
    candidate = np.asarray(value)
    if candidate.ndim == 0:
        return np.full(frame_count, bool(candidate), dtype=bool)
    if candidate.ndim != 1:
        raise ValueError("Frame mask must be a scalar or one-dimensional vector.")
    if len(candidate) == frame_count:
        return candidate.astype(bool)
    if len(candidate) != sample_count:
        raise ValueError("Frame mask length does not match samples or frames.")
    framed = [
        bool(np.mean(candidate[start : min(sample_count, start + frame_length)]) >= 0.5)
        for start in starts
    ]
    return np.asarray(framed, dtype=bool)


def _estimate_f0(frame: Any, sample_rate: int, rms_db: float, np: Any) -> float:
    if rms_db <= -60.0:
        return 0.0
    centered = (frame - np.mean(frame)) * np.hanning(len(frame))
    fft_size = 1 << (2 * len(centered) - 1).bit_length()
    spectrum = np.fft.rfft(centered, n=fft_size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum), n=fft_size)[: len(frame)]
    zero = float(correlation[0])
    if zero <= 1e-12:
        return 0.0
    minimum_lag = max(1, int(sample_rate / 1_100.0))
    maximum_lag = min(len(frame) - 2, int(sample_rate / 50.0))
    if minimum_lag >= maximum_lag:
        return 0.0
    region = correlation[minimum_lag : maximum_lag + 1]
    relative = int(np.argmax(region))
    lag = minimum_lag + relative
    strength = float(correlation[lag] / zero)
    if strength < 0.30:
        return 0.0
    left = float(correlation[lag - 1])
    center = float(correlation[lag])
    right = float(correlation[lag + 1])
    denominator = left - (2.0 * center) + right
    refined_lag = float(lag)
    if abs(denominator) > 1e-12:
        refined_lag += 0.5 * (left - right) / denominator
    return float(sample_rate / refined_lag) if refined_lag > 0.0 else 0.0


def _longest_true_run(flags: Any) -> int:
    longest = 0
    current = 0
    for value in flags:
        if bool(value):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _required_attenuation_db(peak: float) -> float:
    """Return the global attenuation needed to reach the design headroom peak."""

    if peak <= _AUTOMATIC_HEADROOM_TARGET_PEAK:
        return 0.0
    return 20.0 * math.log10(peak / _AUTOMATIC_HEADROOM_TARGET_PEAK)


def _flat_top_evidence(
    samples: Any,
    sample_rate: int,
    np: Any,
) -> tuple[float, int]:
    """Measure near-peak rails without mistaking FP16 extrema for clipping.

    SoulX's production FP16 path can repeat a few samples around a smooth low
    note's apex after its half-precision output is converted back to float32.
    The longest plausible quantization dwell is derived from the local FP16 bin
    width and a conservative 20 Hz lower signal frequency.  Near a sinusoid's
    apex, ``A - A*cos(w*t)`` grows quadratically; allowing a full quantization
    bin gives a total dwell of ``sqrt(2*step/A)/(pi*f)``.  A plateau is clipping
    evidence only when it outlasts that bound, or when a shorter plateau has a
    shoulder jump spanning more than four FP16 bins.  The latter retains
    detection of abrupt four-sample rails while ignoring an ordinary one-bin
    quantization stair.

    A candidate level must be within two percent of the global peak or at the
    unit full-scale rail.  The second anchor prevents one unrelated over-scale
    impulse from hiding a clipped ``+/-1`` plateau, while the first prevents a
    harmless lower-amplitude constant region from being blamed.  Quantization
    bounds are computed from each run's level rather than the global peak so an
    impulse cannot make a smooth FP16 apex look artificially abrupt.  Short
    smooth coincidences and digital silence do not count.
    """

    if len(samples) < 4:
        return 0.0, 0
    absolute = np.abs(samples)
    peak = float(np.max(absolute))
    if peak <= 1e-8:
        return 0.0, 0
    high = 0.98 * peak
    tolerance = max(
        1e-8,
        2.0 * float(np.finfo(np.float32).eps) * max(peak, 1.0),
    )
    at_full_scale = np.abs(absolute - 1.0) <= tolerance
    candidate_level = (absolute >= high) | at_full_scale
    pairs = (
        candidate_level[:-1]
        & candidate_level[1:]
        & (np.signbit(samples[:-1]) == np.signbit(samples[1:]))
        & (np.abs(samples[:-1] - samples[1:]) <= tolerance)
    )
    plateau_samples = 0
    max_run_samples = 0
    run_start: int | None = None
    for pair_index, is_flat in enumerate(pairs):
        if bool(is_flat):
            if run_start is None:
                run_start = pair_index
            continue
        if run_start is None:
            continue
        run_samples = pair_index - run_start + 1
        run_end = run_start + run_samples
        if _is_clipping_plateau(
            samples,
            run_start,
            run_end,
            sample_rate,
            np,
        ):
            plateau_samples += run_samples
            max_run_samples = max(max_run_samples, run_samples)
        run_start = None
    if run_start is not None:
        run_samples = len(samples) - run_start
        if _is_clipping_plateau(
            samples,
            run_start,
            len(samples),
            sample_rate,
            np,
        ):
            plateau_samples += run_samples
            max_run_samples = max(max_run_samples, run_samples)
    return plateau_samples / len(samples), max_run_samples


def _is_clipping_plateau(
    samples: Any,
    run_start: int,
    run_end: int,
    sample_rate: int,
    np: Any,
) -> bool:
    """Distinguish an abrupt or sustained rail from a smooth FP16 apex."""

    run_samples = run_end - run_start
    if run_samples < 4:
        return False
    level = float(np.max(np.abs(samples[run_start:run_end])))
    if level <= 1e-8:
        return False
    half_precision_step = max(
        2.0**-24,
        math.ldexp(1.0, math.floor(math.log2(level)) - 10),
    )
    quantized_apex_seconds = (
        math.sqrt(2.0 * half_precision_step / level) / (math.pi * 20.0)
    )
    # An interval of this duration can include floor(duration * rate) + 1
    # samples.  The next sample is the first duration-only clipping evidence.
    sustained_samples = max(
        4,
        int(math.floor(sample_rate * quantized_apex_seconds)) + 2,
    )
    shoulder_jumps = []
    if run_start > 0:
        shoulder_jumps.append(
            abs(float(samples[run_start] - samples[run_start - 1]))
        )
    if run_end < len(samples):
        shoulder_jumps.append(abs(float(samples[run_end - 1] - samples[run_end])))
    return (
        run_samples >= sustained_samples
        or max(shoulder_jumps, default=0.0) > 4.0 * half_precision_step
    )


def _percentile_or_zero(values: Sequence[float], fraction: float, np: Any) -> float:
    return float(np.quantile(np.asarray(values), fraction)) if values else 0.0


def _bounded_ratio(value: float, limit: float) -> float:
    if limit <= 0.0:
        return 0.0 if value <= 0.0 else 2.0
    return min(2.0, max(0.0, value / limit))


def _ratio_or_zero(value: float | None, limit: float) -> float:
    return 0.0 if value is None else _bounded_ratio(value, limit)


def _monotonic_ratio_or_zero(value: float | None, limit: float) -> float:
    """Scale evidence toward two without flattening distinct severe errors."""

    if value is None or value <= 0.0:
        return 0.0
    if limit <= 0.0 or math.isinf(value):
        return 2.0
    return 2.0 * value / (value + limit)


def _transition_cost(
    previous: SegmentCandidate,
    current: SegmentCandidate,
    sample_rate: int,
) -> float:
    if current.start_sample < previous.end_sample:
        return math.inf
    gap = current.start_sample - previous.end_sample
    switch_cost = 0.02 if previous.prompt_id != current.prompt_id else 0.0
    # A fractional threshold sample is still short of the full 80 ms contract.
    long_gap_samples = math.ceil(sample_rate * 0.080)
    if gap >= long_gap_samples:
        # Silence removes waveform-boundary evidence, but changing the reference
        # voice still carries a consistency cost across the same song.
        return switch_cost
    np = _import_numpy()
    prior = np.asarray(previous.waveform, dtype=np.float64)
    following = np.asarray(current.waveform, dtype=np.float64)
    if prior.ndim != 1 or following.ndim != 1 or not len(prior) or not len(following):
        return math.inf
    window = max(1, int(round(sample_rate * 0.020)))
    prior_edge = prior[-window:]
    following_edge = following[:window]
    jump = abs(float(prior[-1]) - float(following[0])) if gap == 0 else 0.0
    local_differences = np.concatenate(
        [np.abs(np.diff(prior_edge)), np.abs(np.diff(following_edge))]
    )
    typical = float(np.median(local_differences)) if local_differences.size else 0.0
    jump_limit = max(0.02, 8.0 * typical)
    prior_rms = _dbfs(float(np.sqrt(np.mean(np.square(prior_edge)))))
    following_rms = _dbfs(float(np.sqrt(np.mean(np.square(following_edge)))))
    loudness_delta = abs(prior_rms - following_rms)
    # Independently rendered contiguous segments rarely share phase or edge
    # loudness.  Stitching deterministically ramps both sides to zero, so these
    # measurements rank smoother alternatives but cannot make every path fail.
    jump_cost = min(0.06, jump / max(jump_limit, 1e-9) * 0.06)
    loudness_cost = min(0.07, loudness_delta / 12.0 * 0.07)
    return round(min(0.15, switch_cost + jump_cost + loudness_cost), 9)
