"""Convert source singing in the authenticated offline SoulX inference child.

The complete source timeline is partitioned into disjoint cores with two
seconds of context. Short model calls bypass the vendor's long-song silence
filter, and raw vocoder frames are inspected before any tail padding could
conceal missing audio. Pitch correction acts on the source contour, followed
by the user's explicit key choice; words, timing, and waveforms are not
transposed or reconstructed from lyrics. These integrity checks do not prove
that generated singing is free of audible artifacts.

Heavy packages are imported only inside the private WSL worker. The caller
owns source/runtime authentication, the exclusive process lease, cancellation,
and private workspace cleanup. No evaluation harness is imported here.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import gc
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import socket
import sys
from typing import Callable, Iterator

_RATE = 24_000
_HOP = 480
_MAX_SECONDS = 720
_CONTEXT_SAMPLES = 2 * _RATE
_JOIN_RADIUS = 1_200


@dataclass(frozen=True)
class SvcInferenceRequest:
    """Describe one leased conversion using verified runtime and source paths."""

    runtime_root: Path
    source_root: Path
    target_vocal: Path
    output_directory: Path
    reference_id: str
    key_shift_semitones: int

    def __post_init__(self) -> None:
        """Reject unsupported controls before importing model dependencies."""
        for value in (self.runtime_root, self.source_root, self.target_vocal, self.output_directory):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError("SVC paths must be absolute paths authenticated by the runtime.")
        if self.reference_id not in {"fairy", "elysia"}:
            raise ValueError("Unsupported singing reference.")
        if type(self.key_shift_semitones) is not int or not -12 <= self.key_shift_semitones <= 12:
            raise ValueError("Singing key shift must be an integer between -12 and 12.")


@dataclass(frozen=True)
class _Core:
    """Assign absolute source samples and contextual pitch frames to one call."""

    start: int
    end: int
    context_start: int
    context_end: int


def _validate_audio(audio: object) -> None:
    import numpy as np

    if not isinstance(audio, np.ndarray) or audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Expected nonempty finite mono singing audio.")


def _validate_pitch(contour: object, samples: int) -> None:
    import numpy as np

    if (not isinstance(contour, np.ndarray) or contour.ndim != 1
            or len(contour) != (samples + _HOP - 1) // _HOP
            or not np.isfinite(contour).all() or np.any(contour < 0)):
        raise ValueError("Singing pitch must cover the complete 50 Hz source grid.")


def _read_source(path: Path) -> tuple[object, dict[str, object]]:
    """Resample once, allowing only librosa's documented ceil-to-round excess.

    The output length is the nearest integer duration at 24 kHz. A short
    resampler result is rejected; padding it would disguise lost source audio.
    """
    import numpy as np
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32", always_2d=False)
    _validate_audio(audio)
    if type(rate) is not int or rate <= 0 or not 1 <= len(audio) / rate <= _MAX_SECONDS:
        raise ValueError("Singing source duration is outside the supported range.")
    original_samples = len(audio)
    expected = round(original_samples * _RATE / rate)
    if rate != _RATE:
        import librosa

        audio = librosa.resample(audio, orig_sr=rate, target_sr=_RATE)
    excess = len(audio) - expected
    if excess not in (0, 1):
        raise ValueError("Source resampling returned an unexpected or short timeline.")
    audio = np.ascontiguousarray(audio[:expected], dtype=np.float32)
    _validate_audio(audio)
    return audio, {"source_rate": rate, "source_samples": original_samples,
                   "samples_24k": expected, "resample_tail_cropped_samples": excess}


def _plan(audio: object) -> list[_Core]:
    """Choose quiet frame boundaries while assigning every sample exactly once.

    A 100 ms energy minimum between 16 and 20 seconds chooses each next cut.
    Equal minima prefer 18 seconds. Both sides receive two seconds of context;
    a final core up to 22 seconds keeps every call below the vendor's 30-second
    long-song branch. Silence and unvoiced regions retain normal core ownership.
    """
    import numpy as np

    _validate_audio(audio)
    total = len(audio)
    energy_prefix = np.concatenate(([0.0], np.cumsum(audio.astype(np.float64) ** 2)))
    cores = []
    start = 0
    while start < total:
        if total - start <= 22 * _RATE:
            end = total
        else:
            frames = np.arange(start // _HOP + 800, start // _HOP + 1001)
            positions = frames * _HOP
            left, right = positions - 1200, positions + 1200
            energy = (energy_prefix[right] - energy_prefix[left]) / (right - left)
            ordering = np.lexsort((np.abs(frames - (start // _HOP + 900)), energy))
            end = int(positions[ordering[0]])
        cores.append(_Core(start, end, max(0, start - _CONTEXT_SAMPLES),
                           min(total, end + _CONTEXT_SAMPLES)))
        start = end
    _validate_plan(cores, total)
    return cores


def _validate_plan(cores: list[_Core], total: int) -> None:
    cursor = 0
    for core in cores:
        if not 0 <= core.context_start <= core.start == cursor < core.end <= core.context_end <= total:
            raise ValueError("Singing cores have a hole, overlap, or invalid context.")
        if (core.start % _HOP or core.context_start % _HOP
                or (core.end != total and core.end % _HOP)
                or (core.context_end != total and core.context_end % _HOP)):
            raise ValueError("Singing boundaries must follow the absolute pitch frame grid.")
        if core.context_end - core.context_start >= 30 * _RATE:
            raise ValueError("Singing context exceeds the short inference contract.")
        cursor = core.end
    if cursor != total or not cores:
        raise ValueError("Singing cores do not own the complete source timeline.")


@contextmanager
def _offline_scope(source_root: Path, output_directory: Path) -> Iterator[None]:
    """Disable downloads and socket connections for this exclusively leased child.

    Environment and loader patches are process-global, so this function belongs
    only in the runtime's isolated child, never in the threaded desktop process.
    Local HF files remain readable with explicit local_files_only loaders.
    """
    environment = dict(os.environ)
    search_path = list(sys.path)
    bytecode = sys.dont_write_bytecode
    previous = socket.create_connection, socket.socket.connect, socket.socket.connect_ex

    def _denied(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Network access is disabled during local singing inference.")

    try:
        for key in tuple(os.environ):
            if key.lower().endswith("_proxy"):
                os.environ.pop(key)
        os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                           "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                           "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
                           "NUMBA_CACHE_DIR": str(output_directory / "numba-cache")})
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(source_root))
        socket.create_connection = _denied
        socket.socket.connect = _denied
        socket.socket.connect_ex = _denied
        yield
    finally:
        socket.create_connection, socket.socket.connect, socket.socket.connect_ex = previous
        sys.path[:] = search_path
        sys.dont_write_bytecode = bytecode
        os.environ.clear()
        os.environ.update(environment)


def _extract_pitch(audio: object, source_root: Path, rmvpe_path: Path, workspace: Path) -> object:
    """Use the validated native extractor without filling its unvoiced gaps.

    A temporary float source preserves the resampled PCM exactly for the same
    path-based extractor used in listening comparisons. Its duration cap covers
    the complete song; any rounding mismatch in the returned grid is rejected.
    Both the temporary source and RMVPE are released before SVC construction.
    """
    import soundfile as sf
    import torch

    spec = importlib.util.spec_from_file_location(
        "elysia_svc_native_f0", source_root / "preprocess/tools/f0_extraction.py")
    if spec is None or spec.loader is None:
        raise ValueError("Verified singing pitch extractor is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_load = torch.load

    def _safe_load(*args: object, **kwargs: object) -> object:
        kwargs["weights_only"] = True
        return original_load(*args, **kwargs)

    extractor = None
    prepared = workspace / "pitch-source-24k.wav"
    with prepared.open("xb") as stream:
        sf.write(stream, audio, _RATE, format="WAV", subtype="FLOAT")
    try:
        torch.load = _safe_load
        extractor = module.F0Extractor(str(rmvpe_path), device="cuda", target_sr=_RATE,
                                       hop_size=_HOP, max_duration=math.ceil(len(audio) / _RATE) + 1,
                                       verbose=False)
        with torch.inference_mode():
            pitch = extractor.process(str(prepared))
        _validate_pitch(pitch, len(audio))
        return pitch
    finally:
        torch.load = original_load
        del extractor
        prepared.unlink(missing_ok=True)
        gc.collect()
        torch.cuda.empty_cache()


def _load_model(source_root: Path, assets: dict[str, Path]) -> object:
    """Load pinned tensors and external Whisper strictly from local files."""
    import torch
    from transformers import WhisperFeatureExtractor, WhisperModel
    from soulxsinger.models.modules import whisper_encoder
    from soulxsinger.models.soulxsinger_svc import SoulXSingerSVC
    from soulxsinger.utils.file_utils import load_config

    previous = whisper_encoder.WhisperEncoder.__init__

    def _local_whisper(self: object, device: object = None) -> None:
        self.fe = WhisperFeatureExtractor.from_pretrained(str(assets["whisper"]), local_files_only=True)
        self.model = WhisperModel.from_pretrained(str(assets["whisper"]), local_files_only=True,
                                                  use_safetensors=True).to("cpu").eval()

    try:
        # WhisperEncoder is not a child nn.Module. Its own constructor must
        # bypass the upstream remote model ID; parent.half() leaves it fp32.
        whisper_encoder.WhisperEncoder.__init__ = _local_whisper
        model = SoulXSingerSVC(load_config(str(source_root / "soulxsinger/config/soulxsinger.yaml")))
    finally:
        whisper_encoder.WhisperEncoder.__init__ = previous
    # Torch 2.2's mmap loader requires a string filename, unlike ordinary load.
    checkpoint = torch.load(str(assets["model"]), map_location="cpu", weights_only=True, mmap=True)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    del checkpoint
    model.half()
    model.mel.float()
    model.eval().to("cuda")
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    gc.collect()
    return model


def _audit_raw(raw: object, generated: object, samples: int) -> int:
    """Reject hidden padding, changed PCM, and unexpected raw frame lengths."""
    import numpy as np

    _validate_audio(raw)
    _validate_audio(generated)
    expected = ((samples + _HOP - 1) // _HOP) * _HOP
    if len(raw) != expected or len(generated) != samples or len(raw) < samples:
        raise ValueError("Singing vocoder returned a short or unexpected timeline.")
    if not np.array_equal(generated, raw[:samples]):
        raise ValueError("Singing output changed beyond its permitted partial-frame tail crop.")
    return len(raw) - samples


@contextmanager
def _sampling_progress(model: object, cfg: float,
                       callback: Callable[[int], None] | None) -> Iterator[None]:
    """Observe returned estimator forwards without changing tensors or sampling.

    The pinned vendor performs conditional and unconditional forwards in each
    of its 32 steps when CFG is positive. The hook returns None and never reads
    tensor values, synchronizes CUDA, or consumes randomness. Older test models
    without this module still receive the assembly's completed-segment updates.
    """
    estimator = getattr(getattr(getattr(model, "cfm_decoder", None), "model", None),
                        "diff_estimator", None)
    if callback is None or estimator is None:
        yield
        return
    calls = 0
    per_step = 2 if cfg > 0 else 1

    def _observed(_module: object, _inputs: object, _output: object) -> None:
        nonlocal calls
        calls += 1
        if calls % per_step == 0:
            callback(min(32, calls // per_step))

    hook = estimator.register_forward_hook(_observed)
    try:
        yield
    finally:
        hook.remove()


def _render_core(model: object, source: object, pitch: object, prompt: object,
                 prompt_pitch: object, key: int, cfg: float,
                 progress_step: Callable[[int], None] | None = None) -> tuple[object, int]:
    """Capture the raw vocoder before upstream length repair can hide a defect."""
    import numpy as np
    import torch

    target = torch.from_numpy(source).unsqueeze(0).to("cuda")
    target_pitch = torch.from_numpy(pitch).float().unsqueeze(0).to("cuda")
    captured = []

    def _capture(_module: object, _inputs: object, value: object) -> None:
        captured.append(value.squeeze().float().detach().cpu().numpy().copy())

    hook = model.vocoder.register_forward_hook(_capture)
    torch.manual_seed(0)
    np.random.seed(0)
    random.seed(0)
    try:
        with torch.inference_mode(), _sampling_progress(model, cfg, progress_step):
            generated, shift = model.infer(pt_wav=prompt, gt_wav=target, pt_f0=prompt_pitch,
                gt_f0=target_pitch, auto_shift=False, pitch_shift=key,
                n_steps=32, cfg=cfg, use_fp16=True)
        if len(captured) != 1 or shift != key:
            raise ValueError("Singing model changed key or invoked its long-song assembler.")
        samples = generated.squeeze().float().cpu().numpy().copy()
        tail = _audit_raw(captured[0], samples, len(source))
        return samples, tail
    finally:
        hook.remove()


def _select_join(previous: object, current: object, nominal: int,
                 previous_start: int, current_start: int) -> int:
    """Choose a compatible near-zero join within 50 ms on the absolute timeline.

    Independent contexts can generate opposite phases. Crossfading such waves
    can cancel a sustained note, while joining arbitrary values can click. We
    compare existing samples' value jump, adjacent-slope mismatch, and edge
    magnitude, then choose a low-discontinuity sample with no waveform mixing.
    Equal scores prefer the nominal cut. This cannot eliminate every possible
    phase cusp or model artifact; it only bounds a specific join discontinuity.
    """
    import numpy as np

    low = max(nominal - _JOIN_RADIUS, previous_start + 2, current_start)
    high = min(nominal + _JOIN_RADIUS, previous_start + len(previous) - 1,
               current_start + len(current) - 2)
    if not low <= nominal <= high:
        raise ValueError("Generated singing contexts do not share the nominal join.")
    positions = np.arange(low, high + 1)
    left = previous[positions - previous_start - 1]
    right = current[positions - current_start]
    left_slope = left - previous[positions - previous_start - 2]
    right_slope = current[positions - current_start + 1] - right
    score = (np.abs(right - left) + .5 * np.abs(right_slope - left_slope)
             + .1 * (np.abs(left) + np.abs(right)))
    order = np.lexsort((np.abs(positions - nominal), score))
    return int(positions[order[0]])


def _assemble(model: object, audio: object, pitch: object, prompt: object,
              prompt_pitch: object, cores: list[_Core], key: int, cfg: float,
              progress: Callable[[int], None] | None = None) -> tuple[object, list[dict]]:
    """Assign exact sample ownership using at most two generated contexts.

    A pending context is committed only when the next context supplies a
    compatible join. Moving that ownership boundary changes neither source
    positions nor the original inference/F0 windows. Every destination sample
    is copied from that same absolute position in exactly one context.
    """
    import numpy as np

    _validate_plan(cores, len(audio))
    _validate_pitch(pitch, len(audio))
    assembled = np.empty(len(audio), dtype=np.float32)
    receipts = []
    pending = None
    pending_core = None
    pending_record = None
    actual_start = 0
    for index, core in enumerate(cores):
        window = audio[core.context_start:core.context_end]
        contour = pitch[core.context_start // _HOP:(core.context_end + _HOP - 1) // _HOP]
        _validate_pitch(contour, len(window))
        def _step(completed: int) -> None:
            # Integer quantization bounds the complete job to 30 updates. The
            # final percent is reserved for the actual serialized output.
            progress(64 + 23 * (index * 32 + completed) // (len(cores) * 32))

        if progress is None:
            generated, tail = _render_core(model, window, contour, prompt, prompt_pitch, key, cfg)
        else:
            generated, tail = _render_core(model, window, contour, prompt, prompt_pitch, key, cfg,
                                           progress_step=_step)
        _validate_audio(generated)
        if len(generated) != len(window):
            raise ValueError("Singing context omitted source-owned samples.")
        if pending is not None:
            join = _select_join(pending, generated, core.start,
                                pending_core.context_start, core.context_start)
            if not actual_start < join < core.end:
                raise ValueError("Adjusted singing join changed core ordering.")
            owned = pending[actual_start - pending_core.context_start:join - pending_core.context_start]
            if len(owned) != join - actual_start:
                raise ValueError("Adjusted singing join lost source-owned samples.")
            assembled[actual_start:join] = owned
            del owned
            pending_record.update(actual_start=actual_start, actual_end=join)
            receipts.append(pending_record)
            actual_start = join
        # No older contextual waveform remains referenced after this handoff.
        # The output array owns committed samples independently of each view.
        pending, pending_core = generated, core
        pending_record = {**asdict(core), "nominal_start": core.start,
                          "frame_tail_cropped_samples": tail,
                          "zero_f0_frames_retained": int(np.sum(contour == 0))}
        if progress is not None:
            _step(32)
    owned = pending[actual_start - pending_core.context_start:len(audio) - pending_core.context_start]
    if len(owned) != len(audio) - actual_start:
        raise ValueError("Final singing context omitted source-owned samples.")
    assembled[actual_start:] = owned
    pending_record.update(actual_start=actual_start, actual_end=len(audio))
    receipts.append(pending_record)
    _validate_audio(assembled)
    return assembled, receipts


def _attenuate(audio: object) -> tuple[object, float]:
    """Apply at most one constant attenuation, retaining relative dynamics."""
    import numpy as np

    _validate_audio(audio)
    peak = float(np.max(np.abs(audio)))
    gain = min(1.0, .98 / max(peak, 1e-8))
    return audio * gain, gain


def run_svc_inference(request: SvcInferenceRequest, *,
                      progress: Callable[[int], None] | None = None) -> Path:
    """Return an exact-duration mono 24 kHz PCM24 generated.wav for a leased job.

    Assets are checked before loading. Native RMVPE and bounded consensus
    repair preserve the complete frame grid; model calls preserve every source
    sample. A private JSON receipt records rounding, corrections, cores, seams,
    and constant output gain, without publishing model or private input paths.
    Existing outputs are never overwritten. The enclosing runtime removes the
    private job workspace after success, failure, or cancellation.
    An optional callback observes strictly increasing integer milestones in
    59..88; percentages reflect completed setup/model work, never elapsed time.
    """
    from scripts.song_svc_assets import verify_svc_assets
    from scripts.song_svc_pitch import repair_pitch

    last_progress = 58

    def _report(percent: int) -> None:
        nonlocal last_progress
        if percent > last_progress:
            last_progress = percent
            if progress is not None:
                progress(percent)

    output = request.output_directory / "generated.wav"
    receipt_path = request.output_directory / "svc-receipt.json"
    if not request.output_directory.is_dir() or output.exists() or receipt_path.exists():
        raise ValueError("Singing output workspace is unavailable or already used.")
    assets = verify_svc_assets(request.runtime_root)
    _report(59)
    with _offline_scope(request.source_root, request.output_directory):
        import numpy as np
        import soundfile as sf
        import torch

        audio, source_receipt = _read_source(request.target_vocal)
        cores = _plan(audio)
        _report(60)
        native = _extract_pitch(audio, request.source_root, assets["rmvpe"], request.output_directory)
        _report(61)
        # Keep the native input independent so a helper cannot silently mutate
        # the baseline used to count edits and verify the exact frame contract.
        pitch = repair_pitch(audio, native.copy())
        _validate_pitch(pitch, len(audio))
        if pitch.dtype != native.dtype:
            raise ValueError("Pitch repair changed the native contour dtype.")
        _report(62)
        prompt, rate = sf.read(str(assets[f"reference_{request.reference_id}"]), dtype="float32")
        _validate_audio(prompt)
        if rate != _RATE or not 1 <= len(prompt) / _RATE <= 20:
            raise ValueError("Singing reference format is invalid.")
        prompt_pitch = np.load(str(assets[f"reference_f0_{request.reference_id}"]), allow_pickle=False)
        _validate_pitch(prompt_pitch, len(prompt))
        model = _load_model(request.source_root, assets)
        try:
            _report(64)
            prompt_tensor = torch.from_numpy(prompt).unsqueeze(0).to("cuda")
            prompt_contour = torch.from_numpy(prompt_pitch).float().unsqueeze(0).to("cuda")
            cfg = 1.0 if request.reference_id == "fairy" else 3.0
            generated, segments = _assemble(model, audio, pitch, prompt_tensor, prompt_contour,
                                             cores, request.key_shift_semitones, cfg,
                                             progress=_report if progress is not None else None)
        finally:
            del model
            gc.collect()
            torch.cuda.empty_cache()
        bounded, gain = _attenuate(generated)
        seams = [{"nominal_sample": segment["nominal_start"], "sample": segment["actual_start"],
                  "source_adjacent_jump": float(abs(audio[segment["actual_start"]] - audio[segment["actual_start"] - 1])),
                  "output_adjacent_jump": float(abs(bounded[segment["actual_start"]] - bounded[segment["actual_start"] - 1]))}
                 for segment in segments[1:]]
        # The caller supplies a private directory. Exclusive opens additionally
        # prevent accidental retries from replacing earlier job evidence.
        with output.open("xb") as stream:
            sf.write(stream, bounded, _RATE, format="WAV", subtype="PCM_24")
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, **source_receipt, "reference": request.reference_id,
                       "key_shift_semitones": request.key_shift_semitones, "cfg": cfg, "steps": 32,
                       "changed_pitch_frames": int(np.sum(native != pitch)),
                       "native_zero_frames": int(np.sum(native == 0)), "global_gain": gain,
                       "raw_peak": float(np.max(np.abs(generated))),
                       "segments": segments, "seams": seams, "tail_padding_samples": 0,
                       "seam_processing": "absolute joins within 50 ms; no mixing or padding"}, stream, allow_nan=False)
        _report(88)
    return output
