"""Test bounded SoulX candidate orchestration without private model assets."""

from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import sys
from typing import Callable, Mapping

import numpy as np
import pytest

import scripts.song_svs_inference as svs_inference
from scripts.song_svs_inference import (
    InferenceDependencies,
    LoadedSoulXModel,
    SAMPLE_RATE,
    SvsInferenceError,
    SvsInferenceRequest,
    infer_score_expression,
    run_svs_inference,
    seed_all_randomness,
    write_pcm16_wave,
)
from scripts.song_svs_quality import (
    PromptRecord,
    QualitySelectionError,
    TargetFeatures,
    select_prompts,
)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64


def _prompt(
    prompt_id: str,
    root: Path,
    *,
    median: float = 57.0,
    emotion: str | None = None,
    style: str = "neutral",
    rank: int = 0,
    audio_digest: str = _DIGEST_A,
    metadata_digest: str = _DIGEST_B,
    phonemes: tuple[str, ...] = ("zh_a",),
) -> PromptRecord:
    entry: dict[str, object] = {
        "id": prompt_id,
        "role": "emotion",
        "selection_rank": rank,
        "style_proxy": style,
        "emotion": emotion,
        "audio": {
            "path": f"audio/{prompt_id}.wav",
            "bytes": 100,
            "sha256": audio_digest,
        },
        "metadata": {
            "path": f"metadata/{prompt_id}.json",
            "bytes": 100,
            "sha256": metadata_digest,
        },
        "profile": {
            "duration_seconds": 6.0,
            "note_median": median,
            "note_p10": median,
            "note_p90": median,
            "note_span": 0.0,
            "syllables_per_second": 5.0,
            "phonemes": list(phonemes),
        },
    }
    return PromptRecord.from_manifest_entry(
        entry,
        validated_audio_path=root / f"{prompt_id}.wav",
        validated_metadata_path=root / f"{prompt_id}.json",
    )


def _segment(
    index: int,
    start_ms: int,
    *,
    duration_ms: int = 200,
    text: str = "普通歌词",
) -> dict[str, object]:
    sample_count = int(round(duration_ms * SAMPLE_RATE / 1_000))
    f0_frame_count = max(1, round(sample_count / 480))
    return {
        "segment_index": index,
        "time": [start_ms, start_ms + duration_ms],
        "text": text,
        "duration": f"{duration_ms / 1000.0:.3f}",
        "phoneme": "zh_a",
        "note_pitch": "57",
        "note_type": "2",
        "f0": " ".join("220.0" for _ in range(f0_frame_count)),
    }


def _fake_score_tensors(
    metadata: Mapping[str, object],
    frame_count: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mirror pinned SoulX note expansion for contract-level processor fakes."""

    phonemes = [
        token.replace("<AP>", "<SP>")
        for token in str(metadata["phoneme"]).split()
    ]
    pitches = [int(token) for token in str(metadata["note_pitch"]).split()]
    note_types = [int(token) for token in str(metadata["note_type"]).split()]
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
    processed_pitch = [0]
    processed_type = [1]
    for phoneme, pitch, note_type in merged:
        content_count = len(phoneme[3:].split("-")) + 1 if phoneme[:3] == "en_" else 1
        processed_pitch.extend([pitch] * (content_count + 2))
        processed_type.extend([note_type] * (content_count + 2))
    expanded_count = len(processed_pitch) - 1
    assert frame_count >= expanded_count
    frame_positions = np.arange(frame_count, dtype=np.float64)
    mel2note = (
        np.floor(frame_positions * expanded_count / frame_count).astype(np.int64) + 1
    )
    return (
        mel2note.reshape(1, -1),
        np.asarray([processed_pitch], dtype=np.int64),
        np.asarray([processed_type], dtype=np.int64),
    )


class _FakeProcessor:
    def __init__(self) -> None:
        self.prompt_calls: list[str] = []
        self.target_calls: list[int] = []

    def process(self, metadata: dict[str, object], wav_path: str | None) -> object:
        """Return score tensors while deliberately mutating the received copy."""

        metadata["processor_mutation"] = True
        if wav_path is not None:
            prompt_id = Path(wav_path).stem
            self.prompt_calls.append(prompt_id)
            return {"prompt_id": prompt_id, "waveform": np.ones((1, 32))}
        index = int(metadata["segment_index"])
        self.target_calls.append(index)
        start_ms, end_ms = metadata["time"]  # type: ignore[misc]
        sample_count = int(round((end_ms - start_ms) * SAMPLE_RATE / 1_000))
        frame_count = sample_count // 480
        mel2note, note_pitch, note_type = _fake_score_tensors(metadata, frame_count)
        return {
            "segment_index": index,
            "sample_count": sample_count,
            "mel2note": mel2note,
            "note_pitch": note_pitch,
            "note_type": note_type,
        }


class _QuantizedProcessor(_FakeProcessor):
    def process(self, metadata: dict[str, object], wav_path: str | None) -> object:
        """Reproduce the pinned vendor's 6.7-second float-floor frame count."""

        if wav_path is not None:
            return super().process(metadata, wav_path)
        metadata["processor_mutation"] = True
        index = int(metadata["segment_index"])
        self.target_calls.append(index)
        frame_count = 334
        mel2note, note_pitch, note_type = _fake_score_tensors(metadata, frame_count)
        return {
            "segment_index": index,
            "sample_count": frame_count * 480,
            "mel2note": mel2note,
            "note_pitch": note_pitch,
            "note_type": note_type,
        }


class _MutatingTargetProcessor(_FakeProcessor):
    def __init__(self, mutation: Callable[[dict[str, object]], None]) -> None:
        super().__init__()
        self._mutation = mutation

    def process(self, metadata: dict[str, object], wav_path: str | None) -> object:
        """Apply one test-only corruption after building a valid fake target."""

        processed = super().process(metadata, wav_path)
        if wav_path is None:
            assert isinstance(processed, dict)
            self._mutation(processed)
        return processed


class _PromptCacheProbe:
    def __init__(self, cache: dict[str, object]) -> None:
        self._cache = cache
        self.resident_counts_at_allocation: list[int] = []

    def process(self, metadata: dict[str, object], wav_path: str | None) -> object:
        """Record resident payload count when one new fake GPU payload is made."""

        assert wav_path is not None
        self.resident_counts_at_allocation.append(len(self._cache) + 1)
        return {"prompt_id": Path(wav_path).stem, "payload": object()}


class _FakeModel:
    def __init__(
        self,
        behavior: Callable[[int, str, int, int], np.ndarray] | None = None,
    ) -> None:
        self.calls: list[tuple[int, str, dict[str, object]]] = []
        self._counts: Counter[int] = Counter()
        self._behavior = behavior

    def infer(self, data: Mapping[str, object], **options: object) -> np.ndarray:
        """Capture strict SoulX options and return a deterministic fake waveform."""

        target = data["target"]
        prompt = data["prompt"]
        assert isinstance(target, Mapping)
        assert isinstance(prompt, Mapping)
        index = int(target["segment_index"])
        prompt_id = str(prompt["prompt_id"])
        ordinal = self._counts[index]
        self._counts[index] += 1
        self.calls.append((index, prompt_id, dict(options)))
        sample_count = int(target["sample_count"])
        if self._behavior is not None:
            return self._behavior(index, prompt_id, ordinal, sample_count)
        return _clean_wave(sample_count)


class _Harness:
    def __init__(
        self,
        prompt_payloads: Mapping[Path, object],
        *,
        behavior: Callable[[int, str, int, int], np.ndarray] | None = None,
        processor: _FakeProcessor | None = None,
    ) -> None:
        self.model = _FakeModel(behavior)
        self.processor = processor if processor is not None else _FakeProcessor()
        self.prompt_payloads = dict(prompt_payloads)
        self.model_loads = 0
        self.processor_loads = 0
        self.seeds: list[int] = []
        self.writes: list[tuple[Path, np.ndarray, int]] = []

    def _load_model(self, model_path: Path, config_path: Path) -> LoadedSoulXModel:
        self.model_loads += 1
        config = SimpleNamespace(
            audio=SimpleNamespace(sample_rate=SAMPLE_RATE, hop_size=480),
            infer=SimpleNamespace(n_steps=32, cfg=3.0),
        )
        return LoadedSoulXModel(self.model, config)

    def _load_processor(self, config: object, phoneset_path: Path) -> object:
        self.processor_loads += 1
        return self.processor

    def _load_prompt_session(self, prompt: PromptRecord) -> object:
        return nullcontext(
            (self.prompt_payloads[prompt.metadata.path], str(prompt.audio.path))
        )

    def _set_seed(self, seed: int) -> None:
        self.seeds.append(seed)

    def _write(self, path: Path, waveform: object, sample_rate: int) -> None:
        self.writes.append((path, np.asarray(waveform).copy(), sample_rate))

    def dependencies(self) -> InferenceDependencies:
        """Expose this harness through the production dependency contract."""

        return InferenceDependencies(
            model_loader=self._load_model,
            processor_loader=self._load_processor,
            prompt_session_loader=self._load_prompt_session,  # type: ignore[arg-type]
            seed_setter=self._set_seed,
            inference_context=nullcontext,
            audio_writer=self._write,
        )


def _clean_wave(sample_count: int) -> np.ndarray:
    timeline = np.arange(sample_count, dtype=np.float64) / SAMPLE_RATE
    return (0.2 * np.sin(2.0 * np.pi * 220.0 * timeline)).reshape(1, -1)


def _prompt_payload() -> list[dict[str, object]]:
    return [
        {
            "duration": "0.200",
            "phoneme": "zh_a",
            "note_pitch": "57",
            "note_type": "2",
        }
    ]


def _write_authenticated_prompt(tmp_path: Path) -> tuple[PromptRecord, bytes, bytes]:
    metadata_bytes = json.dumps(
        _prompt_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    audio_bytes = b"RIFF" + bytes(range(64))
    prompt = _prompt(
        "authenticated",
        tmp_path,
        audio_digest=hashlib.sha256(audio_bytes).hexdigest(),
        metadata_digest=hashlib.sha256(metadata_bytes).hexdigest(),
    )
    prompt.audio.path.write_bytes(audio_bytes)
    prompt.metadata.path.write_bytes(metadata_bytes)
    prompt.audio.path.chmod(0o600)
    prompt.metadata.path.chmod(0o600)
    prompt = replace(
        prompt,
        audio=replace(prompt.audio, byte_count=len(audio_bytes)),
        metadata=replace(prompt.metadata, byte_count=len(metadata_bytes)),
    )
    return prompt, metadata_bytes, audio_bytes


def _request(
    tmp_path: Path,
    metadata: list[dict[str, object]],
    prompts: list[PromptRecord],
    *,
    total_samples: int | None = None,
) -> SvsInferenceRequest:
    return SvsInferenceRequest(
        model_path=tmp_path / "model.pt",
        config_path=tmp_path / "config.yaml",
        phoneset_path=tmp_path / "phones.json",
        output_directory=tmp_path,
        target_audio_sha256=_DIGEST_D,
        target_metadata=metadata,
        prompts=prompts,
        total_samples=total_samples,
    )


def test_default_dependencies_require_authenticated_prompt_sessions() -> None:
    """Prevent production wiring from regressing to ordinary mutable paths."""

    dependencies = svs_inference.make_default_dependencies()

    assert dependencies.prompt_session_loader is svs_inference._authenticated_prompt_session


@pytest.mark.parametrize(
    ("mode", "link_count", "uid", "byte_count", "expected"),
    [
        (stat.S_IFREG | 0o600, 1, 42, 100, True),
        (stat.S_IFREG | 0o640, 1, 42, 100, False),
        (stat.S_IFREG | 0o600, 2, 42, 100, False),
        (stat.S_IFREG | 0o600, 1, 7, 100, False),
        (stat.S_IFREG | 0o600, 1, 42, 99, False),
        (stat.S_IFDIR | 0o600, 1, 42, 100, False),
    ],
    ids=[
        "private-file",
        "group-readable",
        "hard-linked",
        "foreign-owner",
        "wrong-size",
        "wrong-kind",
    ],
)
def test_authenticated_prompt_descriptor_policy_is_exact(
    mode: int,
    link_count: int,
    uid: int,
    byte_count: int,
    expected: bool,
) -> None:
    """Exercise private descriptor metadata checks on every test platform."""

    info = SimpleNamespace(
        st_mode=mode,
        st_nlink=link_count,
        st_uid=uid,
        st_size=byte_count,
    )

    assert svs_inference._prompt_asset_metadata_matches(
        info,  # type: ignore[arg-type]
        expected_uid=42,
        expected_bytes=100,
    ) is expected


@pytest.mark.skipif(os.name != "posix", reason="Descriptor paths require POSIX /proc.")
def test_authenticated_prompt_session_survives_path_replacement(tmp_path: Path) -> None:
    """Keep vendor input bound to verified descriptors after pathname replacement."""

    prompt, metadata_bytes, audio_bytes = _write_authenticated_prompt(tmp_path)
    replacement_audio = tmp_path / "replacement.wav"
    replacement_metadata = tmp_path / "replacement.json"
    replacement_audio.write_bytes(b"X" * len(audio_bytes))
    replacement_metadata.write_bytes(b"{}")
    replacement_audio.chmod(0o600)
    replacement_metadata.chmod(0o600)

    with svs_inference._authenticated_prompt_session(prompt) as (metadata, audio_path):
        os.replace(replacement_audio, prompt.audio.path)
        os.replace(replacement_metadata, prompt.metadata.path)

        assert metadata == json.loads(metadata_bytes.decode("utf-8"))
        assert Path(audio_path).read_bytes() == audio_bytes

    assert not Path(audio_path).exists()


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "memfd_create"),
    reason="Sealed prompt copies require Linux memfd support.",
)
def test_authenticated_prompt_session_freezes_bytes_after_digest(
    tmp_path: Path,
) -> None:
    """Keep vendor bytes immutable after same-inode source modification."""

    prompt, _metadata_bytes, audio_bytes = _write_authenticated_prompt(tmp_path)
    fcntl_module = importlib.import_module("fcntl")
    required_seals = (
        getattr(fcntl_module, "F_SEAL_WRITE", 0x0008)
        | getattr(fcntl_module, "F_SEAL_GROW", 0x0004)
        | getattr(fcntl_module, "F_SEAL_SHRINK", 0x0002)
        | getattr(fcntl_module, "F_SEAL_SEAL", 0x0001)
    )

    with svs_inference._authenticated_prompt_session(prompt) as (_metadata, audio_path):
        with prompt.audio.path.open("r+b") as mutable_source:
            mutable_source.seek(0)
            mutable_source.write(b"X" * len(audio_bytes))
            mutable_source.flush()
            os.fsync(mutable_source.fileno())

        sealed_descriptor = int(Path(audio_path).name)
        applied = fcntl_module.fcntl(
            sealed_descriptor,
            getattr(fcntl_module, "F_GET_SEALS", 1034),
        )
        assert applied & required_seals == required_seals
        assert Path(audio_path).read_bytes() == audio_bytes
        with pytest.raises(OSError):
            os.write(sealed_descriptor, b"tamper")

    assert not Path(audio_path).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes are enforced in WSL.")
def test_authenticated_prompt_session_rejects_mode_and_digest_changes(
    tmp_path: Path,
) -> None:
    """Reject group-readable or same-size modified prompt assets before use."""

    prompt, _metadata_bytes, audio_bytes = _write_authenticated_prompt(tmp_path)
    prompt.audio.path.chmod(0o640)
    with pytest.raises(SvsInferenceError, match="unavailable or unsafe"):
        with svs_inference._authenticated_prompt_session(prompt):
            pytest.fail("An unsafe private prompt must never be yielded.")

    prompt.audio.path.chmod(0o600)
    prompt.audio.path.write_bytes(b"X" * len(audio_bytes))
    with pytest.raises(SvsInferenceError, match="reviewed prompt bank"):
        with svs_inference._authenticated_prompt_session(prompt):
            pytest.fail("A modified private prompt must never be yielded.")


def test_single_load_prompt_cache_strict_options_and_exact_timeline(tmp_path: Path) -> None:
    """Load runtime objects once and preserve gaps and trailing timeline silence."""

    first = _prompt("primary", tmp_path)
    second = _prompt(
        "secondary",
        tmp_path,
        rank=1,
        audio_digest=_DIGEST_C,
        metadata_digest=_DIGEST_D,
    )
    payloads = {
        first.metadata.path: _prompt_payload(),
        second.metadata.path: _prompt_payload(),
    }
    harness = _Harness(payloads)
    metadata = [_segment(0, 0), _segment(1, 400)]
    result = run_svs_inference(
        _request(
            tmp_path,
            metadata,
            [first, second],
            total_samples=SAMPLE_RATE,
        ),
        dependencies=harness.dependencies(),
    )
    assert harness.model_loads == harness.processor_loads == 1
    assert harness.processor.prompt_calls == ["primary"]
    assert harness.processor.target_calls == [0, 1]
    assert len(harness.model.calls) == 2
    for _index, _prompt_id, options in harness.model.calls:
        assert options == {
            "auto_shift": False,
            "pitch_shift": 0,
            "n_steps": 32,
            "cfg": 3.0,
            "control": "score",
            "use_fp16": True,
        }
    assert len(harness.seeds) == 2 and harness.seeds[0] != harness.seeds[1]
    assert result.output_path == tmp_path / "generated.wav"
    assert result.sample_count == SAMPLE_RATE
    assert result.generated_candidate_count == 2
    assert len(harness.writes) == 1
    written_path, written, written_rate = harness.writes[0]
    assert written_path == result.output_path and written_rate == SAMPLE_RATE
    assert written.shape == (SAMPLE_RATE,)
    np.testing.assert_array_equal(written[4_800:9_600], np.zeros(4_800))
    np.testing.assert_array_equal(written[14_400:], np.zeros(9_600))
    assert all("processor_mutation" not in item for item in metadata)
    assert all(
        "processor_mutation" not in payloads[path][0]  # type: ignore[index]
        for path in payloads
    )


def test_prompt_lru_evicts_before_allocating_a_third_payload(tmp_path: Path) -> None:
    """Keep the live GPU-payload peak at the configured two-entry limit."""

    prompts = [
        _prompt(
            f"prompt-{index}",
            tmp_path,
            rank=index,
            audio_digest=f"{index + 1:x}" * 64,
            metadata_digest=f"{index + 4:x}" * 64,
        )
        for index in range(3)
    ]
    payloads = {prompt.metadata.path: _prompt_payload() for prompt in prompts}
    harness = _Harness(payloads)
    cache: dict[str, object] = {}
    probe = _PromptCacheProbe(cache)

    for prompt in prompts[:2]:
        svs_inference._processed_prompt(
            prompt,
            cache=cache,
            processor=probe,
            dependencies=harness.dependencies(),
        )
    # Refresh the first entry so the second becomes the LRU victim.
    svs_inference._processed_prompt(
        prompts[0],
        cache=cache,
        processor=probe,
        dependencies=harness.dependencies(),
    )
    svs_inference._processed_prompt(
        prompts[2],
        cache=cache,
        processor=probe,
        dependencies=harness.dependencies(),
    )

    assert probe.resident_counts_at_allocation == [1, 2, 2]
    assert list(cache) == [prompts[0].prompt_id, prompts[2].prompt_id]


def test_vendor_float_floor_uses_model_frames_then_absolute_placement(
    tmp_path: Path,
) -> None:
    """Accept the pinned Mandarin example's 1.5-hop timeline quantization drift."""

    prompt = _prompt("primary", tmp_path)
    processor = _QuantizedProcessor()
    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=processor,
    )
    metadata = _segment(0, 0, duration_ms=6_710)
    metadata["duration"] = "6.700"
    result = run_svs_inference(
        _request(tmp_path, [metadata], [prompt]),
        dependencies=harness.dependencies(),
    )
    assert result.sample_count == 161_040
    assert result.generated_candidate_count == 1
    written = harness.writes[0][1]
    assert written.dtype == np.float32
    np.testing.assert_array_equal(written[-720:], np.zeros(720, dtype=np.float32))
    assert written[-721] == 0.0


def test_long_score_accepts_bounded_cumulative_centisecond_rounding(
    tmp_path: Path,
) -> None:
    """Accept three-hop drift when many independently rounded notes explain it."""

    prompt = _prompt("primary", tmp_path)
    processor = _QuantizedProcessor()
    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=processor,
    )
    metadata = _segment(0, 0, duration_ms=6_740)
    metadata["duration"] = " ".join("0.337" for _ in range(20))
    metadata["phoneme"] = " ".join("zh_a" for _ in range(20))
    metadata["note_pitch"] = " ".join("57" for _ in range(20))
    metadata["note_type"] = " ".join("2" for _ in range(20))
    result = run_svs_inference(
        _request(tmp_path, [metadata], [prompt]),
        dependencies=harness.dependencies(),
    )
    assert result.sample_count == 161_760
    assert result.generated_candidate_count == 1
    np.testing.assert_array_equal(
        harness.writes[0][1][-1_440:],
        np.zeros(1_440, dtype=np.float32),
    )


@pytest.mark.parametrize(
    ("duration", "frame_count", "message"),
    [
        ("0.100", 10, "absolute interval"),
        ("0.163", 12, "DataProcessor frames"),
    ],
    ids=["absolute-interval", "model-frames"],
)
def test_score_duration_sum_must_match_both_timing_contracts(
    tmp_path: Path,
    duration: str,
    frame_count: int,
    message: str,
) -> None:
    """Reject duration totals that disagree with either independent timeline."""

    prompt = _prompt("primary", tmp_path)

    def change_frame_count(processed: dict[str, object]) -> None:
        if frame_count == 10:
            return
        metadata = _segment(0, 0)
        metadata["duration"] = duration
        mel2note, note_pitch, note_type = _fake_score_tensors(metadata, frame_count)
        processed.update(
            sample_count=frame_count * 480,
            mel2note=mel2note,
            note_pitch=note_pitch,
            note_type=note_type,
        )

    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=_MutatingTargetProcessor(change_frame_count),
    )
    metadata = _segment(0, 0)
    metadata["duration"] = duration
    with pytest.raises(SvsInferenceError, match=message):
        run_svs_inference(
            _request(tmp_path, [metadata], [prompt]),
            dependencies=harness.dependencies(),
        )
    assert not harness.model.calls


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("sequence", "note evidence contradicts"),
        ("coverage", "does not cover every score note"),
    ],
)
def test_processed_notes_must_preserve_and_cover_repeated_score_slots(
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    """Detect an omitted repeated-pitch note by sequence and frame coverage."""

    prompt = _prompt("primary", tmp_path)

    def corrupt_notes(processed: dict[str, object]) -> None:
        if corruption == "sequence":
            processed["note_pitch"] = np.asarray([[0, 57, 57, 57]], dtype=np.int64)
            processed["note_type"] = np.asarray([[1, 2, 2, 2]], dtype=np.int64)
        else:
            processed["mel2note"] = np.minimum(
                np.asarray(processed["mel2note"]),
                3,
            )

    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=_MutatingTargetProcessor(corrupt_notes),
    )
    metadata = _segment(0, 0)
    metadata.update(
        duration="0.100 0.100",
        phoneme="zh_a zh_a",
        note_pitch="57 57",
        note_type="2 2",
    )
    with pytest.raises(SvsInferenceError, match=message):
        run_svs_inference(
            _request(tmp_path, [metadata], [prompt]),
            dependencies=harness.dependencies(),
        )
    assert not harness.model.calls


def test_short_notes_need_one_mapped_index_per_source_slot(tmp_path: Path) -> None:
    """Accept valid short notes whose BOW/content indexes are overwritten by EOW."""

    prompt = _prompt("primary", tmp_path)

    def retain_one_index_per_note(processed: dict[str, object]) -> None:
        processed["mel2note"] = np.asarray(
            [[1, 1, 1, 1, 1, 4, 4, 4, 4, 4]],
            dtype=np.int64,
        )

    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=_MutatingTargetProcessor(retain_one_index_per_note),
    )
    metadata = _segment(0, 0)
    metadata.update(
        duration="0.100 0.100",
        phoneme="zh_a zh_a",
        note_pitch="57 57",
        note_type="2 2",
    )
    result = run_svs_inference(
        _request(tmp_path, [metadata], [prompt]),
        dependencies=harness.dependencies(),
    )

    assert result.generated_candidate_count == 1
    assert len(harness.model.calls) == 1


def test_processed_note_pitch_rejects_fractional_values(tmp_path: Path) -> None:
    """Reject fractional processed MIDI values instead of truncating them."""

    prompt = _prompt("primary", tmp_path)

    def fractional_pitch(processed: dict[str, object]) -> None:
        note_pitch = np.asarray(processed["note_pitch"], dtype=np.float64)
        note_pitch[0, 1] = 57.9
        processed["note_pitch"] = note_pitch

    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=_MutatingTargetProcessor(fractional_pitch),
    )
    with pytest.raises(SvsInferenceError, match="note pitch evidence is invalid"):
        run_svs_inference(
            _request(tmp_path, [_segment(0, 0)], [prompt]),
            dependencies=harness.dependencies(),
        )
    assert not harness.model.calls


def test_f0_timeline_must_independently_match_absolute_interval(tmp_path: Path) -> None:
    """Reject metadata whose F0 timeline contradicts otherwise plausible durations."""

    prompt = _prompt("primary", tmp_path)
    harness = _Harness(
        {prompt.metadata.path: _prompt_payload()},
        processor=_QuantizedProcessor(),
    )
    metadata = _segment(0, 0, duration_ms=6_710)
    metadata["duration"] = "6.700"
    metadata["f0"] = "220.0"
    with pytest.raises(SvsInferenceError, match="F0 timing evidence"):
        run_svs_inference(
            _request(tmp_path, [metadata], [prompt]),
            dependencies=harness.dependencies(),
        )
    assert not harness.model.calls


def test_deterministic_torch_mode_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request strict deterministic algorithms instead of warning and continuing."""

    calls: list[tuple[bool, bool]] = []
    fake_torch = SimpleNamespace(
        manual_seed=lambda _seed: None,
        cuda=SimpleNamespace(manual_seed_all=lambda _seed: None),
        use_deterministic_algorithms=lambda enabled, warn_only: calls.append(
            (enabled, warn_only)
        ),
        backends=SimpleNamespace(
            cudnn=SimpleNamespace(benchmark=True, deterministic=False)
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    seed_all_randomness(123)
    assert calls == [(True, False)]
    assert fake_torch.backends.cudnn.benchmark is False
    assert fake_torch.backends.cudnn.deterministic is True


def test_expression_proxy_changes_only_acoustically_tied_prompt_order(
    tmp_path: Path,
) -> None:
    """Use lyric emotion after acoustic cost, never instead of acoustic fit."""

    metadata = _segment(0, 0, text="今天真的很开心！")
    acoustic = TargetFeatures.from_metadata([metadata])
    labels = infer_score_expression(metadata, acoustic)
    assert labels.emotion == "happy"
    assert labels.style_proxy == "expressive"
    target = replace(
        acoustic,
        emotion=labels.emotion,
        style_proxy=labels.style_proxy,
    )
    neutral_tie = _prompt(
        "neutral-tie",
        tmp_path,
        emotion="neutral",
        style="expressive",
    )
    happy_tie = _prompt(
        "happy-tie",
        tmp_path,
        emotion="happy",
        style="expressive",
        rank=50,
        audio_digest=_DIGEST_C,
    )
    happy_wrong_register = _prompt(
        "happy-far",
        tmp_path,
        median=75.0,
        emotion="happy",
        style="expressive",
        audio_digest=_DIGEST_D,
    )
    happy_wrong_lyrics = _prompt(
        "happy-missing-phone",
        tmp_path,
        emotion="happy",
        style="expressive",
        phonemes=("zh_b",),
        audio_digest="e" * 64,
    )
    ranking = select_prompts(
        [neutral_tie, happy_wrong_register, happy_wrong_lyrics, happy_tie],
        target,
        limit=4,
    )
    assert [item.prompt.prompt_id for item in ranking] == [
        "happy-tie",
        "neutral-tie",
        "happy-far",
        "happy-missing-phone",
    ]


def test_expression_proxy_collapses_score_spacing_and_sustain_tokens() -> None:
    """Recognize one cue from production note slots without duplicating melismas."""

    acoustic = TargetFeatures.from_metadata([_segment(0, 0)])
    labels = infer_score_expression(
        {
            "text": "太 好 好 了",
            "note_type": "2 2 3 2",
        },
        acoustic,
    )
    assert labels.emotion == "happy"
    assert labels.style_proxy == "expressive"


def test_candidate_seed_is_stable_across_job_paths_and_uuids(tmp_path: Path) -> None:
    """Keep inference seeds unchanged when only orchestration identity changes."""

    prompt = _prompt("primary", tmp_path)
    payloads = {prompt.metadata.path: _prompt_payload()}
    first_metadata = _segment(0, 0)
    first_metadata.update(
        {"job_id": "123e4567-e89b-42d3-a456-426614174000", "path": "C:/one"}
    )
    second_metadata = _segment(0, 0)
    second_metadata.update(
        {"job_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "path": "/mnt/d/two"}
    )
    first_harness = _Harness(payloads)
    second_harness = _Harness(payloads)
    run_svs_inference(
        _request(tmp_path, [first_metadata], [prompt]),
        dependencies=first_harness.dependencies(),
    )
    other_output = tmp_path / "other"
    other_output.mkdir()
    second_request = replace(
        _request(tmp_path, [second_metadata], [prompt]),
        output_directory=other_output,
    )
    run_svs_inference(
        second_request,
        dependencies=second_harness.dependencies(),
    )
    assert first_harness.seeds == second_harness.seeds


def test_bounded_rescue_uses_same_prompt_then_second_prompt(tmp_path: Path) -> None:
    """Generate C1 and C2 once each and cache both processed prompts."""

    primary = _prompt("primary", tmp_path)
    secondary = _prompt(
        "secondary",
        tmp_path,
        rank=1,
        audio_digest=_DIGEST_C,
        metadata_digest=_DIGEST_D,
    )

    def _behavior(index: int, prompt_id: str, ordinal: int, count: int) -> np.ndarray:
        if index == 0 and ordinal < 2:
            return np.zeros((1, count), dtype=np.float64)
        return _clean_wave(count)

    payloads = {
        primary.metadata.path: _prompt_payload(),
        secondary.metadata.path: _prompt_payload(),
    }
    harness = _Harness(payloads, behavior=_behavior)
    metadata = [_segment(index, index * 200) for index in range(8)]
    result = run_svs_inference(
        _request(tmp_path, metadata, [primary, secondary]),
        dependencies=harness.dependencies(),
    )
    first_segment_calls = [call for call in harness.model.calls if call[0] == 0]
    assert [call[1] for call in first_segment_calls] == [
        "primary",
        "primary",
        "secondary",
    ]
    assert [audit.candidate_kind for audit in result.audits if audit.segment_index == 0] == [
        "c0",
        "c1",
        "c2",
    ]
    assert result.generated_candidate_count == 10
    assert result.rescue_candidate_count == 2
    assert result.selected_candidate_ids[0].endswith("c2-secondary")
    assert harness.processor.prompt_calls == ["primary", "secondary"]
    assert harness.processor.target_calls == list(range(8)) + [0]
    assert harness.model_loads == harness.processor_loads == 1


def test_single_prompt_rescue_does_not_budget_or_generate_c2(tmp_path: Path) -> None:
    """Retry a one-segment song once without charging a missing second prompt."""

    primary = _prompt("primary", tmp_path)

    def _behavior(index: int, prompt_id: str, ordinal: int, count: int) -> np.ndarray:
        return np.zeros((1, count)) if ordinal == 0 else _clean_wave(count)

    harness = _Harness(
        {primary.metadata.path: _prompt_payload()},
        behavior=_behavior,
    )
    result = run_svs_inference(
        _request(tmp_path, [_segment(0, 0)], [primary]),
        dependencies=harness.dependencies(),
    )
    assert [call[1] for call in harness.model.calls] == ["primary", "primary"]
    assert [audit.candidate_kind for audit in result.audits] == ["c0", "c1"]
    assert result.rescue_candidate_count == 1
    assert harness.processor.target_calls == [0, 0]


def test_correctable_float_overflow_does_not_trigger_rescue(tmp_path: Path) -> None:
    """Repair one clean over-unity C0 globally instead of regenerating it."""

    primary = _prompt("primary", tmp_path)

    def _over_unity(
        index: int,
        prompt_id: str,
        ordinal: int,
        count: int,
    ) -> np.ndarray:
        return 7.5 * _clean_wave(count)

    harness = _Harness(
        {primary.metadata.path: _prompt_payload()},
        behavior=_over_unity,
    )
    result = run_svs_inference(
        _request(tmp_path, [_segment(0, 0)], [primary]),
        dependencies=harness.dependencies(),
    )
    assert result.generated_candidate_count == 1
    assert result.rescue_candidate_count == 0
    assert len(harness.model.calls) == 1
    assert float(np.max(np.abs(harness.writes[0][1]))) == pytest.approx(
        0.994,
        abs=1e-6,
    )


def test_flat_top_c0_triggers_bounded_clean_rescue(tmp_path: Path) -> None:
    """Regenerate irreversible clipping even when its peak could be attenuated."""

    primary = _prompt("primary", tmp_path)

    def _clipped_then_clean(
        index: int,
        prompt_id: str,
        ordinal: int,
        count: int,
    ) -> np.ndarray:
        if ordinal == 0:
            return np.clip(7.5 * _clean_wave(count), -1.0, 1.0)
        return _clean_wave(count)

    harness = _Harness(
        {primary.metadata.path: _prompt_payload()},
        behavior=_clipped_then_clean,
    )
    result = run_svs_inference(
        _request(tmp_path, [_segment(0, 0)], [primary]),
        dependencies=harness.dependencies(),
    )
    assert result.generated_candidate_count == 2
    assert result.rescue_candidate_count == 1
    assert [audit.passed for audit in result.audits] == [False, True]
    assert "clipping" in result.audits[0].violations
    assert result.selected_candidate_ids[0].endswith("c1-primary")


def test_post_stitch_structural_recheck_blocks_corrupted_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject defects introduced after per-candidate assessment and selection."""

    primary = _prompt("primary", tmp_path)
    harness = _Harness({primary.metadata.path: _prompt_payload()})
    monkeypatch.setattr(
        svs_inference,
        "stitch_candidate_path",
        lambda *_args, **_kwargs: np.full(4_800, np.nan, dtype=np.float32),
    )
    with pytest.raises(QualitySelectionError, match="Stitched output"):
        run_svs_inference(
            _request(tmp_path, [_segment(0, 0)], [primary]),
            dependencies=harness.dependencies(),
        )
    assert not harness.writes


def test_all_failed_candidates_stop_after_fixed_rescue_budget(tmp_path: Path) -> None:
    """Converge after the reviewed rescue cap when no hard-safe path exists."""

    primary = _prompt("primary", tmp_path)
    secondary = _prompt(
        "secondary",
        tmp_path,
        rank=1,
        audio_digest=_DIGEST_C,
    )

    def _silence(index: int, prompt_id: str, ordinal: int, count: int) -> np.ndarray:
        return np.zeros((1, count), dtype=np.float64)

    payloads = {
        primary.metadata.path: _prompt_payload(),
        secondary.metadata.path: _prompt_payload(),
    }
    harness = _Harness(payloads, behavior=_silence)
    metadata = [_segment(index, index * 200) for index in range(20)]
    with pytest.raises(QualitySelectionError):
        run_svs_inference(
            _request(tmp_path, metadata, [primary, secondary]),
            dependencies=harness.dependencies(),
        )
    assert len(harness.model.calls) == 25
    assert harness.model_loads == harness.processor_loads == 1
    assert not harness.writes


def test_pcm16_writer_uses_explicit_soundfile_container_and_subtype(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the published vocal to mono 24 kHz WAV with PCM16 encoding."""

    captured: dict[str, object] = {}

    def _write(path: str, samples: np.ndarray, rate: int, **options: object) -> None:
        captured.update(
            path=path,
            samples=np.asarray(samples).copy(),
            rate=rate,
            options=options,
        )

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=_write))
    output = tmp_path / "generated.wav"
    write_pcm16_wave(output, np.zeros(100, dtype=np.float32), SAMPLE_RATE)
    assert captured["path"] == str(output)
    assert captured["rate"] == SAMPLE_RATE
    assert captured["options"] == {"format": "WAV", "subtype": "PCM_16"}
