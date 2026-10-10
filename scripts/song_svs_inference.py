"""Orchestrate deterministic, quality-gated SoulX segment inference.

This module connects the private SoulX model to :mod:`song_svs_quality` while
owning the final prompt-consumption trust boundary.  Its caller supplies paths
that the WSL runtime already authenticated, corrected score metadata, and
parsed prompt records; immediately before vendor code consumes each prompt,
the orchestrator reopens and authenticates both assets from stable file
descriptors.  It loads one model and one ``DataProcessor``, caches processed
prompts, generates one baseline candidate per target segment, spends a fixed
rescue budget, selects a globally coherent hard-safe path, and writes one exact
24 kHz mono PCM16 file named ``generated.wav``.

Production dependencies are imported lazily because the reviewed SoulX and
CUDA packages exist only inside the private inference environment.  Explicit
dependency injection lets unit tests exercise the complete control flow with
small local fakes and no private model, prompt audio, or GPU.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
import copy
from dataclasses import dataclass, replace
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import random
import stat
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
import unicodedata

from scripts.song_svs_quality import (
    CandidateBudget,
    DEFAULT_CANDIDATE_BUDGET,
    DEFAULT_QUALITY_THRESHOLDS,
    PromptAsset,
    PromptRecord,
    QualityAssessment,
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


SAMPLE_RATE = 24_000
_OUTPUT_NAME = "generated.wav"
_CONTROL_MODE = "score"
_RESCUE_LOSS_THRESHOLD = 0.55
_MAX_PROMPT_CACHE_ENTRIES = 2
_BOUNDARY_FADE_SECONDS = 0.005


class SvsInferenceError(RuntimeError):
    """Report a deterministic orchestration or SoulX contract failure."""


class _SoulXDataProcessor(Protocol):
    """Describe the small vendor processor contract used by this orchestrator.

    SoulX itself is private and untyped, but this boundary needs only its
    deterministic ``process`` operation.  Keeping that contract explicit
    prevents untyped vendor objects from spreading through score validation.
    """

    def process(self, metadata: dict[str, object], wav_path: str | None) -> object:
        """Convert copied score or prompt metadata into vendor tensors."""


@dataclass(frozen=True, slots=True)
class LoadedSoulXModel:
    """Pair the single loaded SoulX model with its immutable configuration."""

    model: object
    config: object


@dataclass(frozen=True, slots=True)
class InferenceDependencies:
    """Provide lazy production adapters or model-free test doubles.

    Each loader is called exactly once per :func:`run_svs_inference` call.
    Prompt metadata and processed prompt tensors are cached separately by the
    orchestrator, so adapters do not need hidden caches of their own.
    """

    model_loader: Callable[[Path, Path], LoadedSoulXModel]
    processor_loader: Callable[[object, Path], _SoulXDataProcessor]
    prompt_session_loader: Callable[
        [PromptRecord], AbstractContextManager[tuple[object, str]]
    ]
    seed_setter: Callable[[int], None]
    inference_context: Callable[[], AbstractContextManager[object]]
    audio_writer: Callable[[Path, object, int], None]


@dataclass(frozen=True, slots=True)
class SvsInferenceRequest:
    """Describe one already authenticated private SoulX inference job."""

    model_path: Path
    config_path: Path
    phoneset_path: Path
    output_directory: Path
    target_audio_sha256: str
    target_metadata: Sequence[Mapping[str, object]]
    prompts: Sequence[PromptRecord]
    total_samples: int | None = None
    global_gain_db: float = 0.0
    rescue_loss_threshold: float = _RESCUE_LOSS_THRESHOLD
    budget: CandidateBudget = DEFAULT_CANDIDATE_BUDGET

    def __post_init__(self) -> None:
        """Reject invalid public bounds before loading a private model."""

        for value, label in (
            (self.model_path, "Model path"),
            (self.config_path, "Config path"),
            (self.phoneset_path, "Phoneset path"),
            (self.output_directory, "Output directory"),
        ):
            if not isinstance(value, Path):
                raise TypeError(f"{label} must be a Path validated by the runtime.")
        if not self.target_metadata:
            raise ValueError("Target metadata must contain at least one segment.")
        if not self.prompts:
            raise ValueError("At least one authenticated prompt is required.")
        if self.total_samples is not None and (
            type(self.total_samples) is not int or self.total_samples <= 0
        ):
            raise ValueError("Total sample count must be positive when supplied.")
        if not math.isfinite(self.global_gain_db) or not (
            -6.0 <= self.global_gain_db <= 0.0
        ):
            raise ValueError("Global attenuation must lie between minus six and zero dB.")
        if not math.isfinite(self.rescue_loss_threshold) or not (
            0.0 <= self.rescue_loss_threshold <= 2.0
        ):
            raise ValueError("Rescue loss threshold must lie between zero and two.")


@dataclass(frozen=True, slots=True)
class CandidateAudit:
    """Expose deterministic candidate evidence without private audio samples."""

    candidate_id: str
    segment_index: int
    prompt_id: str
    candidate_kind: str
    seed: int
    passed: bool
    violations: tuple[str, ...]
    local_loss: float


@dataclass(frozen=True, slots=True)
class SvsInferenceResult:
    """Summarize the selected full-timeline render and bounded search."""

    output_path: Path
    sample_count: int
    generated_candidate_count: int
    rescue_candidate_count: int
    selected_candidate_ids: tuple[str, ...]
    audits: tuple[CandidateAudit, ...]


@dataclass(frozen=True, slots=True)
class ScoreExpressionLabels:
    """Hold conservative closed-set emotion and score-style tie-break labels."""

    emotion: str | None
    style_proxy: str


@dataclass(frozen=True, slots=True)
class _SegmentPlan:
    metadata: Mapping[str, object]
    segment_index: int
    start_sample: int
    end_sample: int
    prompt_ranking: tuple[PromptRecord, ...]
    features: TargetFeatures


@dataclass(frozen=True, slots=True)
class _ScoreFrameEvidence:
    f0_hz: object
    voiced: object
    silence: object
    hop_size: int
    model_samples: int


def _expression_lyric_text(metadata: Mapping[str, object]) -> str:
    """Collapse score slots into the lyric text used by conservative cues.

    Production metadata separates every note slot with whitespace and repeats
    the onset token in type-3 sustain slots.  Keeping only type-2 onsets avoids
    both whitespace breaking a multi-character lexicon cue and a melisma
    inventing extra lyric characters.  Malformed or legacy metadata falls back
    to whitespace removal because expression is only a prompt tie-breaker.
    """

    raw_text = metadata.get("text", "")
    if not isinstance(raw_text, str):
        return ""
    tokens = raw_text.split()
    raw_note_type = metadata.get("note_type")
    if isinstance(raw_note_type, str):
        try:
            note_types = tuple(int(token) for token in raw_note_type.split())
        except ValueError:
            note_types = ()
        if len(note_types) == len(tokens) and all(
            note_type in {1, 2, 3} for note_type in note_types
        ):
            return unicodedata.normalize(
                "NFC",
                "".join(
                    token
                    for token, note_type in zip(tokens, note_types, strict=True)
                    if note_type == 2
                ),
            )
    return unicodedata.normalize(
        "NFC",
        "".join(token for token in tokens if token not in {"<SP>", "<AP>"}),
    )


def seed_all_randomness(seed: int) -> None:
    """Reset Python, NumPy, PyTorch, and every CUDA generator to one seed.

    SoulX runs in a one-shot private process, so enabling deterministic CUDA
    behavior here cannot perturb unrelated application workloads.  The seed is
    bounded to 63 bits by :func:`canonical_candidate_seed`.
    """

    if type(seed) is not int or not 0 <= seed < 1 << 63:
        raise ValueError("Inference seed must be a non-negative 63-bit integer.")
    import numpy as np  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]

    random.seed(seed)
    np.random.seed(seed % (1 << 32))
    torch.manual_seed(seed)
    if hasattr(torch, "cuda"):
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch, "use_deterministic_algorithms"):
        # Reproducibility is part of the production contract.  Continuing after
        # an unsupported CUDA operation would create an apparently successful
        # but non-replayable private render, so deterministic failures surface.
        torch.use_deterministic_algorithms(True, warn_only=False)
    cudnn = getattr(getattr(torch, "backends", None), "cudnn", None)
    if cudnn is not None:
        cudnn.benchmark = False
        cudnn.deterministic = True


def write_pcm16_wave(path: Path, waveform: object, sample_rate: int) -> None:
    """Write one finite mono 24 kHz waveform through SoundFile as PCM16 WAV."""

    import numpy as np  # type: ignore[import-not-found]
    import soundfile as sf  # type: ignore[import-not-found]

    if not isinstance(path, Path) or path.name != _OUTPUT_NAME:
        raise ValueError(f"SoulX output must be named {_OUTPUT_NAME!r}.")
    if sample_rate != SAMPLE_RATE:
        raise ValueError("SoulX output must use the reviewed 24 kHz sample rate.")
    samples = np.asarray(waveform, dtype=np.float32)
    if samples.ndim != 1 or not samples.size or not np.all(np.isfinite(samples)):
        raise ValueError("SoulX output must be a finite non-empty mono waveform.")
    if float(np.max(np.abs(samples))) > 0.999:
        raise ValueError("SoulX output would clip when encoded as PCM16.")
    sf.write(str(path), samples, sample_rate, format="WAV", subtype="PCM_16")


def make_default_dependencies() -> InferenceDependencies:
    """Create lazy adapters for the reviewed private SoulX environment."""

    return InferenceDependencies(
        model_loader=_load_soulx_model,
        processor_loader=_load_data_processor,
        prompt_session_loader=_authenticated_prompt_session,
        seed_setter=seed_all_randomness,
        inference_context=_torch_no_grad,
        audio_writer=write_pcm16_wave,
    )


def infer_score_expression(
    metadata: Mapping[str, object],
    features: TargetFeatures,
) -> ScoreExpressionLabels:
    """Infer deterministic lyrics/score labels without claiming ML semantics.

    This deliberately small rule set recognizes only unambiguous Simplified
    Chinese cues.  A tied or absent lyric cue yields ``None`` rather than an
    invented emotion.  Punctuation, note range, syllable rate, and optional
    score dynamics choose one of four style proxies; prompt selection still
    ranks acoustic distance and phoneme coverage before either label.
    """

    text = _expression_lyric_text(metadata)
    lexicon: Mapping[str, tuple[str, ...]] = {
        "neutral": ("好的", "明白了", "知道了"),
        "happy": ("开心", "高兴", "快乐", "喜悦", "太好了"),
        "sad": ("难过", "悲伤", "哭泣", "失去", "离别", "遗憾", "痛苦"),
        "caring": ("小心", "保重", "休息", "陪着你", "担心你", "没事的"),
        "moved": ("谢谢你", "感动", "珍惜", "难忘", "记得我"),
        "playful": ("哈哈", "嘿嘿", "猜猜", "好玩", "游戏", "开玩笑"),
        "affectionate": ("喜欢你", "爱你", "想你", "亲爱的", "最喜欢"),
        "teasing": ("笨蛋", "骗你的", "逗你", "真可爱", "小傻瓜"),
        "serious": ("必须", "危险", "重要", "认真", "注意", "绝不"),
        "surprised": ("竟然", "没想到", "怎么会", "不会吧", "真的？", "什么？"),
    }
    scores = {
        label: sum(1 for phrase in phrases if phrase in text)
        for label, phrases in lexicon.items()
    }
    best_score = max(scores.values(), default=0)
    winners = [label for label, score in scores.items() if score == best_score and score]
    emotion = winners[0] if len(winners) == 1 else None

    punctuation_count = sum(text.count(mark) for mark in ("!", "！", "?", "？"))
    dynamics = _score_dynamics(metadata)
    dynamics_span = max(dynamics) - min(dynamics) if dynamics else 0.0
    if punctuation_count >= 2 or (
        features.note_span >= 15.0 and features.syllables_per_second >= 4.5
    ):
        style = "emphatic"
    elif (
        emotion not in {None, "neutral"}
        or features.note_span >= 9.0
        or dynamics_span >= 20.0
        or (punctuation_count and features.syllables_per_second >= 3.0)
    ):
        style = "expressive"
    elif (
        punctuation_count == 0
        and features.note_span <= 5.0
        and features.syllables_per_second <= 2.2
        and dynamics_span <= 10.0
    ):
        style = "calm"
    else:
        style = "neutral"
    return ScoreExpressionLabels(emotion=emotion, style_proxy=style)


def run_svs_inference(
    request: SvsInferenceRequest,
    *,
    dependencies: InferenceDependencies | None = None,
) -> SvsInferenceResult:
    """Generate, gate, globally select, and publish one SoulX vocal timeline.

    Candidate C0 always uses the highest-ranked acoustic prompt and seed zero.
    A fixed rescue plan may add C1 with the same prompt and seed one, followed
    by C2 with the second-ranked prompt and that prompt's seed zero.  Rescue
    results never trigger another search round, preventing runaway GPU work.
    """

    deps = dependencies if dependencies is not None else make_default_dependencies()
    target_metadata = copy.deepcopy(list(request.target_metadata))
    plans = _plan_segments(target_metadata, request.prompts)
    timeline_samples = _resolve_timeline_length(plans, request.total_samples)

    loaded = deps.model_loader(request.model_path, request.config_path)
    sample_rate = _integer_config(loaded.config, "audio", "sample_rate")
    hop_size = _integer_config(loaded.config, "audio", "hop_size")
    n_steps = _integer_config(loaded.config, "infer", "n_steps")
    cfg = _number_config(loaded.config, "infer", "cfg")
    if sample_rate != SAMPLE_RATE:
        raise SvsInferenceError("SoulX configuration must use 24 kHz audio.")
    processor = deps.processor_loader(loaded.config, request.phoneset_path)

    prompt_cache: dict[str, object] = {}
    evidence_cache: dict[int, _ScoreFrameEvidence] = {}
    candidate_groups: dict[int, list[SegmentCandidate]] = {
        plan.segment_index: [] for plan in plans
    }
    audits: list[CandidateAudit] = []

    for plan in plans:
        target_data = processor.process(copy.deepcopy(dict(plan.metadata)), None)
        evidence_cache[plan.segment_index] = _build_score_evidence(
            plan,
            target_data,
            hop_size,
        )
        candidate, audit = _generate_candidate(
            request=request,
            plan=plan,
            prompt=plan.prompt_ranking[0],
            candidate_kind="c0",
            seed_ordinal=0,
            model=loaded.model,
            target_data=target_data,
            evidence=evidence_cache[plan.segment_index],
            prompt_cache=prompt_cache,
            processor=processor,
            dependencies=deps,
            n_steps=n_steps,
            cfg=cfg,
        )
        candidate_groups[plan.segment_index].append(candidate)
        audits.append(audit)
        # Target tensors can live on CUDA.  Rescue targets are rebuilt only for
        # the bounded segments that need them rather than retaining the whole
        # song's target tensors in VRAM.
        del target_data

    rescue_requests = _rescue_requests(
        plans,
        candidate_groups,
        request.rescue_loss_threshold,
    )
    rescue_plan = plan_rescue_candidates(
        rescue_requests,
        total_voiced_seconds=sum(_voiced_seconds(plan.metadata) for plan in plans),
        budget=request.budget,
    )
    plan_by_index = {plan.segment_index: plan for plan in plans}
    rescue_count = 0
    for allocation in rescue_plan:
        plan = plan_by_index[allocation.segment_index]
        target_data = processor.process(copy.deepcopy(dict(plan.metadata)), None)
        for ordinal in allocation.candidate_ordinals:
            if ordinal == 1:
                prompt = plan.prompt_ranking[0]
                candidate_kind = "c1"
                seed_ordinal = 1
            elif ordinal == 2 and len(plan.prompt_ranking) >= 2:
                prompt = plan.prompt_ranking[1]
                candidate_kind = "c2"
                seed_ordinal = 0
            else:
                continue
            candidate, audit = _generate_candidate(
                request=request,
                plan=plan,
                prompt=prompt,
                candidate_kind=candidate_kind,
                seed_ordinal=seed_ordinal,
                model=loaded.model,
                target_data=target_data,
                evidence=evidence_cache[plan.segment_index],
                prompt_cache=prompt_cache,
                processor=processor,
                dependencies=deps,
                n_steps=n_steps,
                cfg=cfg,
            )
            candidate_groups[plan.segment_index].append(candidate)
            audits.append(audit)
            rescue_count += 1
        del target_data

    ordered_groups = [candidate_groups[plan.segment_index] for plan in plans]
    selected = select_candidate_path(ordered_groups, sample_rate=SAMPLE_RATE)
    rendered = stitch_candidate_path(
        selected,
        total_samples=timeline_samples,
        sample_rate=SAMPLE_RATE,
        global_gain_db=request.global_gain_db,
    )
    _validate_stitched_waveform(rendered, timeline_samples)
    output_path = request.output_directory / _OUTPUT_NAME
    deps.audio_writer(output_path, rendered, SAMPLE_RATE)
    return SvsInferenceResult(
        output_path=output_path,
        sample_count=timeline_samples,
        generated_candidate_count=len(audits),
        rescue_candidate_count=rescue_count,
        selected_candidate_ids=tuple(item.candidate_id for item in selected),
        audits=tuple(audits),
    )


def _load_soulx_model(model_path: Path, config_path: Path) -> LoadedSoulXModel:
    from cli.inference import build_model  # type: ignore[import-not-found]
    from soulxsinger.utils.file_utils import load_config  # type: ignore[import-not-found]

    config = load_config(str(config_path))
    model = build_model(
        model_path=str(model_path),
        config=config,
        device="cuda",
        use_fp16=True,
    )
    return LoadedSoulXModel(model=model, config=config)


def _load_data_processor(config: object, phoneset_path: Path) -> _SoulXDataProcessor:
    from soulxsinger.utils.data_processor import (  # type: ignore[import-not-found]
        DataProcessor,
    )

    return DataProcessor(
        hop_size=_integer_config(config, "audio", "hop_size"),
        sample_rate=_integer_config(config, "audio", "sample_rate"),
        phoneset_path=str(phoneset_path),
        device="cuda",
    )


def _prompt_asset_metadata_matches(
    info: os.stat_result,
    *,
    expected_uid: int,
    expected_bytes: int,
) -> bool:
    """Return whether one descriptor has the closed private-asset metadata."""

    return bool(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == expected_uid
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_size == expected_bytes
    )


def _open_authenticated_prompt_asset(asset: PromptAsset, label: str) -> int:
    """Open one private prompt asset without following its final path component.

    The runtime verifies the complete bank before inference starts, but a path
    can otherwise be renamed between that verification and vendor consumption.
    Keeping this descriptor open lets the caller hash and consume one stable
    inode.  Exact owner-only permissions make the privacy claim enforceable and
    exclude replacement by another local account.
    """

    if os.name != "posix" or not hasattr(os, "getuid"):
        raise SvsInferenceError("Private prompts require the reviewed POSIX runtime.")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    close_on_exec = getattr(os, "O_CLOEXEC", None)
    if no_follow is None or close_on_exec is None:
        raise SvsInferenceError("Private prompt descriptor safeguards are unavailable.")
    descriptor = -1
    try:
        descriptor = os.open(asset.path, os.O_RDONLY | no_follow | close_on_exec)
        info = os.fstat(descriptor)
        if not _prompt_asset_metadata_matches(
            info,
            expected_uid=os.getuid(),
            expected_bytes=asset.byte_count,
        ):
            raise OSError
    except (OSError, TypeError, ValueError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise SvsInferenceError(f"{label} is unavailable or unsafe.") from error
    return descriptor


def _read_authenticated_prompt_asset(
    descriptor: int,
    asset: PromptAsset,
    label: str,
    *,
    capture: bool,
) -> bytes:
    """Hash bytes from an already opened inode and optionally retain them.

    Reading and hashing through the same descriptor avoids the classic
    validate-by-path/use-by-path race.  The descriptor is rewound only after a
    complete identity check so an audio decoder starts at the canonical header.
    """

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    byte_count = 0
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, 1024 * 1024):
            byte_count += len(chunk)
            digest.update(chunk)
            if capture:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as error:
        raise SvsInferenceError(f"{label} could not be authenticated.") from error
    if (
        byte_count != asset.byte_count
        or after.st_size != asset.byte_count
        or digest.hexdigest() != asset.sha256
    ):
        raise SvsInferenceError(f"{label} does not match the reviewed prompt bank.")
    return b"".join(chunks)


def _sealed_authenticated_prompt_copy(
    source_descriptor: int,
    asset: PromptAsset,
    label: str,
) -> int:
    """Copy, authenticate, and seal audio before a vendor reopens it.

    Holding the source descriptor prevents pathname replacement, but it does
    not make that inode immutable: another process under the same UID could
    overwrite it after the digest check.  Linux ``memfd`` seals close that
    final check/use gap.  The digest is calculated while copying, all mutation
    seals are applied only after it matches the reviewed manifest, and vendor
    code receives only the resulting read-only descriptor view.
    """

    memfd_create = getattr(os, "memfd_create", None)
    allow_sealing = getattr(os, "MFD_ALLOW_SEALING", None)
    close_on_exec = getattr(os, "MFD_CLOEXEC", None)
    if not callable(memfd_create) or allow_sealing is None or close_on_exec is None:
        raise SvsInferenceError("Private prompt sealing is unavailable.")
    try:
        fcntl_module = importlib.import_module("fcntl")
        # CPython 3.10 exposes ``fcntl()`` but not these Linux constants.  Their
        # UAPI values are architecture-independent; the read-back check below
        # still fails closed if the running kernel does not implement them.
        add_seals = getattr(fcntl_module, "F_ADD_SEALS", 1033)
        get_seals = getattr(fcntl_module, "F_GET_SEALS", 1034)
        required_seals = (
            getattr(fcntl_module, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl_module, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl_module, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl_module, "F_SEAL_SEAL", 0x0001)
        )
    except ImportError as error:
        raise SvsInferenceError("Private prompt sealing is unavailable.") from error

    sealed_descriptor = -1
    digest = hashlib.sha256()
    byte_count = 0
    try:
        sealed_descriptor = memfd_create(
            "elysia-private-prompt",
            allow_sealing | close_on_exec,
        )
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        while chunk := os.read(source_descriptor, 1024 * 1024):
            byte_count += len(chunk)
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(sealed_descriptor, remaining)
                if written <= 0:
                    raise OSError
                remaining = remaining[written:]
        source_after = os.fstat(source_descriptor)
        if (
            byte_count != asset.byte_count
            or source_after.st_size != asset.byte_count
            or digest.hexdigest() != asset.sha256
        ):
            raise SvsInferenceError(
                f"{label} does not match the reviewed prompt bank."
            )
        os.fchmod(sealed_descriptor, 0o400)
        fcntl_module.fcntl(sealed_descriptor, add_seals, required_seals)
        applied_seals = fcntl_module.fcntl(sealed_descriptor, get_seals)
        if applied_seals & required_seals != required_seals:
            raise OSError
        os.lseek(sealed_descriptor, 0, os.SEEK_SET)
    except SvsInferenceError:
        if sealed_descriptor >= 0:
            os.close(sealed_descriptor)
        raise
    except (OSError, TypeError, ValueError) as error:
        if sealed_descriptor >= 0:
            os.close(sealed_descriptor)
        raise SvsInferenceError(f"{label} could not be authenticated.") from error
    return sealed_descriptor


@contextmanager
def _authenticated_prompt_session(
    prompt: PromptRecord,
) -> Iterator[tuple[object, str]]:
    """Yield decoded metadata and an fd-backed path for one authenticated prompt.

    ``DataProcessor`` accepts only an audio pathname.  Linux's ``/proc/self/fd``
    view lets it reopen a sealed, authenticated in-memory copy without returning
    to either the mutable bank pathname or its mutable source inode.  Every
    descriptor is closed on both success and failure paths.
    """

    metadata_descriptor = -1
    audio_source_descriptor = -1
    sealed_audio_descriptor = -1
    try:
        metadata_descriptor = _open_authenticated_prompt_asset(
            prompt.metadata,
            "Private prompt metadata",
        )
        encoded_metadata = _read_authenticated_prompt_asset(
            metadata_descriptor,
            prompt.metadata,
            "Private prompt metadata",
            capture=True,
        )
        try:
            metadata = json.loads(encoded_metadata.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SvsInferenceError("Prompt metadata could not be decoded.") from error

        audio_source_descriptor = _open_authenticated_prompt_asset(
            prompt.audio,
            "Private prompt audio",
        )
        sealed_audio_descriptor = _sealed_authenticated_prompt_copy(
            audio_source_descriptor,
            prompt.audio,
            "Private prompt audio",
        )
        os.close(audio_source_descriptor)
        audio_source_descriptor = -1
        descriptor_path = Path("/proc/self/fd") / str(sealed_audio_descriptor)
        if not descriptor_path.exists():
            raise SvsInferenceError("The private prompt descriptor view is unavailable.")
        yield metadata, str(descriptor_path)
    finally:
        for descriptor in (
            sealed_audio_descriptor,
            audio_source_descriptor,
            metadata_descriptor,
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _torch_no_grad() -> AbstractContextManager[object]:
    import torch  # type: ignore[import-not-found]

    return torch.no_grad()


def _plan_segments(
    metadata: Sequence[Mapping[str, object]],
    prompts: Sequence[PromptRecord],
) -> tuple[_SegmentPlan, ...]:
    plans: list[_SegmentPlan] = []
    previous_end = 0
    for index, segment in enumerate(metadata):
        if not isinstance(segment, Mapping):
            raise ValueError("Every target segment must be an object.")
        start_sample, end_sample = _segment_interval(segment)
        if start_sample < previous_end:
            raise ValueError("Target segment intervals must not overlap.")
        previous_end = end_sample
        acoustic_features = TargetFeatures.from_metadata([segment])
        labels = infer_score_expression(segment, acoustic_features)
        features = TargetFeatures(
            duration_seconds=acoustic_features.duration_seconds,
            note_median=acoustic_features.note_median,
            note_p10=acoustic_features.note_p10,
            note_p90=acoustic_features.note_p90,
            note_span=acoustic_features.note_span,
            syllables_per_second=acoustic_features.syllables_per_second,
            phonemes=acoustic_features.phonemes,
            style_proxy=labels.style_proxy,
            emotion=labels.emotion,
        )
        ranking = select_prompts(prompts, features, limit=min(2, len(prompts)))
        plans.append(
            _SegmentPlan(
                metadata=segment,
                segment_index=index,
                start_sample=start_sample,
                end_sample=end_sample,
                prompt_ranking=tuple(item.prompt for item in ranking),
                features=features,
            )
        )
    return tuple(plans)


def _segment_interval(metadata: Mapping[str, object]) -> tuple[int, int]:
    raw_time = metadata.get("time")
    if (
        isinstance(raw_time, (str, bytes))
        or not isinstance(raw_time, Sequence)
        or len(raw_time) != 2
    ):
        raise ValueError("Target segment time must contain start and end milliseconds.")
    start_ms, end_ms = raw_time
    if (
        isinstance(start_ms, bool)
        or isinstance(end_ms, bool)
        or not isinstance(start_ms, (int, float))
        or not isinstance(end_ms, (int, float))
    ):
        raise ValueError("Target segment times must be numeric milliseconds.")
    start_value = float(start_ms)
    end_value = float(end_ms)
    if (
        not math.isfinite(start_value)
        or not math.isfinite(end_value)
        or start_value < 0.0
        or end_value <= start_value
    ):
        raise ValueError("Target segment interval is invalid.")
    start_sample = int(round(start_value * SAMPLE_RATE / 1_000.0))
    end_sample = int(round(end_value * SAMPLE_RATE / 1_000.0))
    if end_sample <= start_sample:
        raise ValueError("Target segment interval is shorter than one sample.")
    return start_sample, end_sample


def _resolve_timeline_length(
    plans: Sequence[_SegmentPlan],
    requested_samples: int | None,
) -> int:
    required = max(plan.end_sample for plan in plans)
    if requested_samples is not None and requested_samples < required:
        raise ValueError("Total sample count truncates a target segment.")
    return required if requested_samples is None else requested_samples


def _config_member(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        if name not in value:
            raise SvsInferenceError(f"SoulX configuration is missing {name!r}.")
        return value[name]
    if not hasattr(value, name):
        raise SvsInferenceError(f"SoulX configuration is missing {name!r}.")
    return getattr(value, name)


def _integer_config(config: object, section: str, name: str) -> int:
    raw = _config_member(_config_member(config, section), name)
    if type(raw) is not int or raw <= 0:
        raise SvsInferenceError(f"SoulX configuration {section}.{name} is invalid.")
    return raw


def _number_config(config: object, section: str, name: str) -> float:
    raw = _config_member(_config_member(config, section), name)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SvsInferenceError(f"SoulX configuration {section}.{name} is invalid.")
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise SvsInferenceError(f"SoulX configuration {section}.{name} is invalid.")
    return value


def _prompt_metadata_record(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 1
        and isinstance(value[0], Mapping)
    ):
        return value[0]
    raise SvsInferenceError("Prompt metadata must contain exactly one segment.")


def _processed_prompt(
    prompt: PromptRecord,
    *,
    cache: dict[str, object],
    processor: _SoulXDataProcessor,
    dependencies: InferenceDependencies,
) -> object:
    cached = cache.pop(prompt.prompt_id, None)
    if cached is not None:
        # Plain dictionaries preserve insertion order, so reinsertion provides
        # a tiny dependency-free LRU suitable for GPU-backed prompt tensors.
        cache[prompt.prompt_id] = cached
        return cached
    if len(cache) >= _MAX_PROMPT_CACHE_ENTRIES:
        # Prompt preprocessing allocates the new payload directly on CUDA.  An
        # old LRU entry must therefore lose its last cache reference before the
        # processor starts, otherwise loading entry three briefly retains three
        # full GPU payloads despite the nominal two-entry cache limit.
        del cache[next(iter(cache))]
    with dependencies.prompt_session_loader(prompt) as (raw, audio_path):
        metadata = copy.deepcopy(dict(_prompt_metadata_record(raw)))
        processed = processor.process(metadata, audio_path)
    cache[prompt.prompt_id] = processed
    return processed


def _to_numpy_vector(value: object, label: str) -> Any:
    import numpy as np  # type: ignore[import-not-found]

    candidate = value
    for method_name in ("detach", "cpu", "numpy"):
        method = getattr(candidate, method_name, None)
        if callable(method):
            candidate = method()
    result = np.asarray(candidate)
    result = np.squeeze(result)
    if result.ndim != 1 or not result.size:
        raise SvsInferenceError(f"{label} must be a non-empty vector.")
    return result


def _expected_processed_notes(
    metadata: Mapping[str, object],
) -> tuple[
    tuple[int, ...],
    tuple[int, ...],
    tuple[tuple[int, int], ...],
    int,
]:
    """Reproduce the pinned DataProcessor's score-note expansion contract.

    SoulX inserts a fixed index-zero ``<PAD>`` note, then expands every source
    slot to ``<BOW>``, its phoneme token(s), and ``<EOW>`` while repeating the
    source pitch and type.  It also merges only adjacent equivalent silence
    slots.  Comparing that complete ordered sequence detects omitted repeated
    pitches that a set-based comparison cannot see.
    """

    raw_pitch = metadata.get("note_pitch")
    raw_type = metadata.get("note_type")
    raw_phoneme = metadata.get("phoneme")
    if not (
        isinstance(raw_pitch, str)
        and isinstance(raw_type, str)
        and isinstance(raw_phoneme, str)
    ):
        raise SvsInferenceError("Target score pitch evidence is missing.")
    try:
        pitches = [int(value) for value in raw_pitch.split()]
        note_types = [int(value) for value in raw_type.split()]
    except ValueError as error:
        raise SvsInferenceError("Target score pitch evidence is malformed.") from error
    phonemes = [str(value).replace("<AP>", "<SP>") for value in raw_phoneme.split()]
    if not pitches or not len(pitches) == len(note_types) == len(phonemes):
        raise SvsInferenceError("Target score pitch evidence has inconsistent lengths.")
    if any(not 0 <= pitch <= 127 for pitch in pitches) or any(
        note_type not in {1, 2, 3} for note_type in note_types
    ):
        raise SvsInferenceError("Target score pitch evidence is invalid.")

    merged: list[tuple[str, int, int]] = []
    for phoneme, pitch, note_type in zip(
        phonemes,
        pitches,
        note_types,
        strict=True,
    ):
        effective_type = 1 if phoneme == "<SP>" else note_type
        if (
            merged
            and phoneme == merged[-1][0] == "<SP>"
            and pitch == merged[-1][1]
            and effective_type == merged[-1][2]
        ):
            continue
        merged.append((phoneme, pitch, effective_type))

    expected_pitch = [0]
    expected_type = [1]
    note_index_ranges: list[tuple[int, int]] = []
    for phoneme, pitch, note_type in merged:
        # English compound phones expand to one index per component plus SEP;
        # every other token in the Mandarin-only product path uses one index.
        content_count = len(phoneme[3:].split("-")) + 1 if phoneme[:3] == "en_" else 1
        expansion_count = content_count + 2
        range_start = len(expected_pitch)
        expected_pitch.extend([pitch] * expansion_count)
        expected_type.extend([note_type] * expansion_count)
        note_index_ranges.append((range_start, range_start + expansion_count))
    return (
        tuple(expected_pitch),
        tuple(expected_type),
        tuple(note_index_ranges),
        len(pitches),
    )


def _finite_integer_vector(value: object, label: str) -> Any:
    """Return a numeric vector only when every processed value is an exact integer."""

    import numpy as np  # type: ignore[import-not-found]

    raw = np.asarray(value)
    if raw.dtype.kind == "b":
        raise SvsInferenceError(f"DataProcessor {label} evidence is invalid.")
    try:
        numeric = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise SvsInferenceError(
            f"DataProcessor {label} evidence is invalid."
        ) from error
    if not np.all(np.isfinite(numeric)) or not np.array_equal(
        numeric,
        np.rint(numeric),
    ):
        raise SvsInferenceError(f"DataProcessor {label} evidence is invalid.")
    return np.rint(numeric).astype(np.int64)


def _score_dynamics(metadata: Mapping[str, object]) -> tuple[float, ...]:
    raw = metadata.get("dynamics", metadata.get("velocity"))
    if isinstance(raw, str):
        tokens: Sequence[object] = raw.split()
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        tokens = raw
    else:
        return ()
    values: list[float] = []
    for token in tokens:
        if isinstance(token, bool) or not isinstance(token, (str, int, float)):
            return ()
        try:
            value = float(token)
        except (TypeError, ValueError):
            return ()
        if not math.isfinite(value) or not 0.0 <= value <= 127.0:
            return ()
        values.append(value)
    return tuple(values)


def _metadata_numeric_tokens(
    metadata: Mapping[str, object],
    field: str,
) -> tuple[float, ...]:
    """Parse one required numeric score vector without accepting booleans or NaNs."""

    raw = metadata.get(field)
    if isinstance(raw, str):
        tokens: Sequence[object] = raw.split()
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        tokens = raw
    else:
        raise SvsInferenceError(f"Score {field} evidence is missing.")
    values: list[float] = []
    for token in tokens:
        if isinstance(token, bool) or not isinstance(token, (str, int, float)):
            raise SvsInferenceError(f"Score {field} evidence is invalid.")
        try:
            value = float(token)
        except (TypeError, ValueError) as error:
            raise SvsInferenceError(f"Score {field} evidence is invalid.") from error
        if not math.isfinite(value):
            raise SvsInferenceError(f"Score {field} evidence is invalid.")
        values.append(value)
    if not values:
        raise SvsInferenceError(f"Score {field} evidence is empty.")
    return tuple(values)


def _model_timing_tolerance_samples(
    metadata: Mapping[str, object],
    expected_samples: int,
    hop_size: int,
) -> int:
    """Bound vendor duration drift caused by centisecond note serialization.

    SoulX's pinned MIDI parser rounds every note duration independently to two
    decimal places before ``DataProcessor`` sums and floors the result to model
    frames.  That cumulative loss grows with note count, so a fixed two-hop
    allowance incorrectly rejects valid long songs.  The mathematical rounding
    bound is also capped at one percent of the absolute interval (with a
    three-hop minimum) so malformed metadata cannot claim an arbitrarily large
    discrepancy merely by supplying many duration tokens.
    """

    durations = _metadata_numeric_tokens(metadata, "duration")
    if any(value <= 0.0 for value in durations):
        raise SvsInferenceError("Score duration evidence is invalid.")
    serialized_rounding = math.ceil(len(durations) * 0.005 * SAMPLE_RATE)
    # Two hops cover the final floating-point floor plus millisecond endpoint
    # quantization; the per-note term covers the only cumulative loss source.
    mathematical_bound = serialized_rounding + (2 * hop_size)
    policy_cap = max(3 * hop_size, math.ceil(expected_samples * 0.01))
    return min(mathematical_bound, policy_cap)


def _score_duration_samples(
    metadata: Mapping[str, object],
    expected_note_count: int,
) -> int:
    """Return the finite positive score-duration sum in output samples."""

    durations = _metadata_numeric_tokens(metadata, "duration")
    if len(durations) != expected_note_count or any(
        value <= 0.0 for value in durations
    ):
        raise SvsInferenceError("Score duration evidence is invalid.")
    return int(round(math.fsum(durations) * SAMPLE_RATE))


def _build_score_evidence(
    plan: _SegmentPlan,
    processed_target: object,
    hop_size: int,
) -> _ScoreFrameEvidence:
    import numpy as np  # type: ignore[import-not-found]

    if not isinstance(processed_target, Mapping):
        raise SvsInferenceError("DataProcessor target output must be an object.")
    try:
        mel2note = _to_numpy_vector(processed_target["mel2note"], "mel2note")
        note_pitch = _to_numpy_vector(processed_target["note_pitch"], "note_pitch")
        note_type = _to_numpy_vector(processed_target["note_type"], "note_type")
    except KeyError as error:
        raise SvsInferenceError("DataProcessor target evidence is incomplete.") from error
    if not np.all(np.isfinite(mel2note)) or not np.allclose(mel2note, np.rint(mel2note)):
        raise SvsInferenceError("DataProcessor mel2note evidence is invalid.")
    indexes = np.rint(mel2note).astype(np.int64)
    if len(note_pitch) != len(note_type):
        raise SvsInferenceError("DataProcessor note evidence has inconsistent lengths.")
    processed_pitch = _finite_integer_vector(note_pitch, "note pitch")
    processed_type = _finite_integer_vector(note_type, "note type")
    (
        expected_pitch,
        expected_type,
        note_index_ranges,
        source_note_count,
    ) = _expected_processed_notes(plan.metadata)
    if tuple(processed_pitch.tolist()) != expected_pitch or tuple(
        processed_type.tolist()
    ) != expected_type:
        raise SvsInferenceError("DataProcessor note evidence contradicts the score.")
    if np.any(indexes < 0) or np.any(indexes >= len(note_pitch)):
        raise SvsInferenceError("DataProcessor mel2note indexes are out of range.")
    mapped_indexes = set(indexes.tolist())
    # The pinned DataProcessor can write BOW, content, and EOW onto the same
    # one- or two-frame short note, so later writes legitimately replace some
    # expanded token indexes.  Requiring every expanded index rejects real
    # fast lyrics.  Requiring at least one index from every merged source-note
    # range still proves repeated equal-pitch notes were not silently omitted.
    if any(
        not any(index in mapped_indexes for index in range(start, end))
        for start, end in note_index_ranges
    ):
        raise SvsInferenceError(
            "DataProcessor mel2note does not cover every score note."
        )
    frame_pitch = processed_pitch[indexes].astype(np.float64)
    frame_type = processed_type[indexes]
    voiced = (indexes > 0) & (frame_type != 1) & (frame_pitch > 0.0)
    silence = (indexes > 0) & (frame_type == 1)
    f0_hz = np.zeros(len(indexes), dtype=np.float32)
    f0_hz[voiced] = 440.0 * np.power(2.0, (frame_pitch[voiced] - 69.0) / 12.0)
    expected_samples = plan.end_sample - plan.start_sample
    model_samples = len(indexes) * hop_size
    duration_samples = _score_duration_samples(plan.metadata, source_note_count)
    # Raw score durations are not vendor-quantized yet, so their sum must
    # independently authenticate the absolute interval within only endpoint
    # and final-frame quantization.  Note count cannot expand this allowance.
    if abs(duration_samples - expected_samples) >= 2 * hop_size:
        raise SvsInferenceError(
            "Score duration timing evidence contradicts the absolute interval."
        )
    metadata_f0 = _metadata_numeric_tokens(plan.metadata, "f0")
    if any(value < 0.0 for value in metadata_f0):
        raise SvsInferenceError("Score f0 evidence is invalid.")
    f0_samples = len(metadata_f0) * hop_size
    # F0 is emitted from the unrounded audio timeline, so it independently
    # authenticates the absolute interval.  Unlike rounded note durations its
    # discrepancy must never accumulate with song length.
    if abs(f0_samples - expected_samples) >= 2 * hop_size:
        raise SvsInferenceError("Score F0 timing evidence contradicts the score.")
    timing_tolerance = _model_timing_tolerance_samples(
        plan.metadata,
        expected_samples,
        hop_size,
    )
    # Model frames remain authoritative for inference length; the validated
    # absolute interval remains authoritative for placement and final padding.
    if abs(model_samples - expected_samples) > timing_tolerance:
        raise SvsInferenceError("DataProcessor timing evidence contradicts the score.")
    if abs(model_samples - duration_samples) > timing_tolerance:
        raise SvsInferenceError(
            "Score duration timing evidence contradicts DataProcessor frames."
        )
    return _ScoreFrameEvidence(
        f0_hz=f0_hz,
        voiced=voiced,
        silence=silence,
        hop_size=hop_size,
        model_samples=model_samples,
    )


def _sample_evidence(
    evidence: _ScoreFrameEvidence,
    sample_count: int,
) -> tuple[object, object, object]:
    import numpy as np  # type: ignore[import-not-found]

    # Quality analysis uses the same fixed 20 ms hop as the pinned SoulX model,
    # so frame evidence can stay compact instead of expanding to millions of
    # per-sample values and then immediately reducing it back to frames.
    frame_count = math.ceil(sample_count / evidence.hop_size)
    f0 = np.asarray(evidence.f0_hz, dtype=np.float32)
    voiced = np.asarray(evidence.voiced, dtype=bool)
    silence = np.asarray(evidence.silence, dtype=bool)
    if len(f0) < frame_count:
        missing = frame_count - len(f0)
        f0 = np.pad(f0, (0, missing), constant_values=0.0)
        voiced = np.pad(voiced, (0, missing), constant_values=False)
        # A bounded absolute-time remainder follows all model score frames and
        # is intentionally filled with silence during interval reconciliation.
        silence = np.pad(silence, (0, missing), constant_values=True)
    else:
        f0 = f0[:frame_count]
        voiced = voiced[:frame_count]
        silence = silence[:frame_count]
    return f0, voiced, silence


def _waveform_array(value: object) -> Any:
    import numpy as np  # type: ignore[import-not-found]

    candidate = value
    detach = getattr(candidate, "detach", None)
    if callable(detach):
        candidate = detach()
    squeeze = getattr(candidate, "squeeze", None)
    if callable(squeeze):
        candidate = squeeze()
    cpu = getattr(candidate, "cpu", None)
    if callable(cpu):
        candidate = cpu()
    numpy_method = getattr(candidate, "numpy", None)
    if callable(numpy_method):
        candidate = numpy_method()
    result = np.asarray(candidate, dtype=np.float32)
    if result.ndim != 1 or not result.size:
        raise SvsInferenceError("SoulX infer must return one non-empty mono waveform.")
    return result


def _exact_interval_waveform(
    waveform: object,
    expected_samples: int,
    *,
    sample_rate: int,
) -> object:
    """Reconcile bounded model-frame drift to an absolute placement interval.

    SoulX emits the DataProcessor frame length, while score metadata places the
    segment on an independent millisecond timeline.  A short cosine-like edge
    ramp reaches zero before deterministic truncation or zero padding, avoiding
    the click that a non-zero final sample would otherwise create.
    """

    import numpy as np  # type: ignore[import-not-found]

    samples = np.asarray(waveform, dtype=np.float32)
    result = np.zeros(expected_samples, dtype=np.float32)
    copied = min(expected_samples, len(samples))
    result[:copied] = samples[:copied]
    if len(samples) != expected_samples and copied:
        fade = min(copied, max(1, int(round(sample_rate * _BOUNDARY_FADE_SECONDS))))
        if fade == 1:
            result[copied - 1] = 0.0
        else:
            result[copied - fade : copied] *= np.linspace(
                1.0,
                0.0,
                fade,
                dtype=np.float32,
            )
    return result


def _validate_stitched_waveform(waveform: object, expected_samples: int) -> None:
    """Recheck the exact post-fade, post-gain waveform before publication."""

    import numpy as np  # type: ignore[import-not-found]

    samples = np.asarray(waveform)
    thresholds = DEFAULT_QUALITY_THRESHOLDS
    failed = (
        samples.ndim != 1
        or len(samples) != expected_samples
        or not np.all(np.isfinite(samples))
    )
    if not failed:
        absolute = np.abs(samples)
        failed = (
            float(np.max(absolute)) > thresholds.max_peak
            or abs(float(np.mean(samples))) > thresholds.max_abs_dc_offset
        )
    if failed:
        raise QualitySelectionError("Stitched output failed structural quality review.")


def _generate_candidate(
    *,
    request: SvsInferenceRequest,
    plan: _SegmentPlan,
    prompt: PromptRecord,
    candidate_kind: str,
    seed_ordinal: int,
    model: object,
    target_data: object,
    evidence: _ScoreFrameEvidence,
    prompt_cache: dict[str, object],
    processor: _SoulXDataProcessor,
    dependencies: InferenceDependencies,
    n_steps: int,
    cfg: float,
) -> tuple[SegmentCandidate, CandidateAudit]:
    prompt_data = _processed_prompt(
        prompt,
        cache=prompt_cache,
        processor=processor,
        dependencies=dependencies,
    )
    seed = canonical_candidate_seed(
        target_audio_sha256=request.target_audio_sha256,
        segment_metadata=plan.metadata,
        prompt_sha256=prompt.audio.sha256,
        candidate_ordinal=seed_ordinal,
    )
    dependencies.seed_setter(seed)
    infer = getattr(model, "infer", None)
    if not callable(infer):
        raise SvsInferenceError("Loaded SoulX model does not expose infer().")
    with dependencies.inference_context():
        generated = infer(
            {"prompt": prompt_data, "target": target_data},
            auto_shift=False,
            pitch_shift=0,
            n_steps=n_steps,
            cfg=cfg,
            control=_CONTROL_MODE,
            use_fp16=True,
        )
    waveform = _waveform_array(generated)
    raw_sample_count = len(waveform)
    expected_samples = plan.end_sample - plan.start_sample
    placed_waveform = _exact_interval_waveform(
        waveform,
        expected_samples,
        sample_rate=SAMPLE_RATE,
    )
    del generated, waveform
    expected_f0, expected_voiced, expected_silence = _sample_evidence(
        evidence,
        expected_samples,
    )
    metrics = analyze_waveform(
        placed_waveform,
        sample_rate=SAMPLE_RATE,
        expected_samples=expected_samples,
        expected_f0_hz=expected_f0,
        expected_voiced_mask=expected_voiced,
        expected_silence_mask=expected_silence,
    )
    # Duration quality refers to the model-frame contract, not the independent
    # absolute placement interval reconciled above.
    metrics = replace(
        metrics,
        expected_samples=evidence.model_samples,
        length_error_samples=abs(raw_sample_count - evidence.model_samples),
    )
    assessment = assess_waveform(metrics, sample_rate=SAMPLE_RATE)
    candidate_id = f"s{plan.segment_index:05d}-{candidate_kind}-{prompt.prompt_id}"
    candidate = SegmentCandidate(
        candidate_id=candidate_id,
        prompt_id=prompt.prompt_id,
        segment_index=plan.segment_index,
        start_sample=plan.start_sample,
        end_sample=plan.end_sample,
        waveform=placed_waveform,
        assessment=assessment,
        required_attenuation_db=metrics.required_attenuation_db,
    )
    return candidate, CandidateAudit(
        candidate_id=candidate_id,
        segment_index=plan.segment_index,
        prompt_id=prompt.prompt_id,
        candidate_kind=candidate_kind,
        seed=seed,
        passed=assessment.passed,
        violations=assessment.violations,
        local_loss=assessment.local_loss,
    )


def _rescue_requests(
    plans: Sequence[_SegmentPlan],
    candidate_groups: Mapping[int, Sequence[SegmentCandidate]],
    loss_threshold: float,
) -> tuple[RescueRequest, ...]:
    requests: list[RescueRequest] = []
    for plan in plans:
        baseline = candidate_groups[plan.segment_index][0]
        assessment: QualityAssessment = baseline.assessment
        if assessment.passed and assessment.local_loss < loss_threshold:
            continue
        severity = assessment.local_loss
        if not assessment.passed:
            severity += 100.0 + (0.01 * len(assessment.violations))
        requests.append(
            RescueRequest(
                segment_index=plan.segment_index,
                duration_seconds=(plan.end_sample - plan.start_sample) / SAMPLE_RATE,
                severity=severity,
                candidate_ordinals=(1, 2) if len(plan.prompt_ranking) >= 2 else (1,),
            )
        )
    return tuple(requests)


def _voiced_seconds(metadata: Mapping[str, object]) -> float:
    raw_duration = metadata.get("duration")
    raw_pitch = metadata.get("note_pitch")
    raw_type = metadata.get("note_type")
    if not (
        isinstance(raw_duration, str)
        and isinstance(raw_pitch, str)
        and isinstance(raw_type, str)
    ):
        raise SvsInferenceError("Target score duration evidence is missing.")
    try:
        durations = [float(value) for value in raw_duration.split()]
        pitches = [float(value) for value in raw_pitch.split()]
        note_types = [int(value) for value in raw_type.split()]
    except ValueError as error:
        raise SvsInferenceError("Target score duration evidence is malformed.") from error
    if not durations or not len(durations) == len(pitches) == len(note_types):
        raise SvsInferenceError("Target score duration evidence is inconsistent.")
    return sum(
        duration
        for duration, pitch, note_type in zip(
            durations,
            pitches,
            note_types,
            strict=True,
        )
        if note_type != 1 and pitch > 0.0
    )
