"""Preserve source-time ownership when assembling the pinned RVC v2 segments.

The private, digest-verified ``infer/vc/pipeline.py`` chooses quiet cut points
on a 10 ms grid but its HuBERT features advance in 20 ms steps. Its symmetric
``[left_pad:-right_pad]`` crop consequently drops one output frame from some
segments and a fractional tail from the final segment. Concatenation then
moves later syllables earlier; padding the completed song cannot repair that.

This maintained adapter reproduces only that fixed pipeline's orchestration:
the same filter, quiet cuts, padded inference inputs, global F0, generator,
loudness matching and PCM quantization. Each segment owns an explicit absolute
source interval. Its missing core samples are already present in generated
right context and are selected by an explicit end index. No silence, waveform
repetition, crossfade, resynthesis or time stretching is used to repair length.
The final output length comes from the prepared input WAV, allowing only the
few samples explained by its upstream 16 kHz resampling. Vendor files and
classes remain unchanged; one converter instance is bound for one request.

Orchestration adapted from RVC-Project/Retrieval-based-Voice-Conversion-WebUI,
``infer/vc/pipeline.py``, revision
``81eed5e8f68b6bed1789f682fe78cdd324495afc``. The production snapshot is additionally
authenticated by the parent worker's fixed source-tree digest. Original notice:

MIT License

Copyright (c) 2023 liujing04
Copyright (c) 2023 源文雨
Copyright (c) 2023 Ftps

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import wave


_INPUT_RATE = 16_000
_OUTPUT_RATE = 40_000
_FRAME_SAMPLES = 160
_MAX_OUTPUT_SAMPLES = _OUTPUT_RATE * 15 * 60
_MAX_RESAMPLE_ROUNDING_SAMPLES = 3


class _PipelineContractError(RuntimeError):
    """Reject an incompatible pipeline or missing generated context."""


def _output_position(input_position: int) -> int:
    """Map one absolute 16 kHz sample boundary to the nearest 40 kHz sample."""

    return round(input_position * _OUTPUT_RATE / _INPUT_RATE)


def _wav_output_samples(input_audio: Path) -> int:
    """Read the prepared PCM timeline without decoding or normalizing audio."""

    try:
        with wave.open(str(input_audio), "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
            if source.getcomptype() != "NONE" or rate < 1 or frames < 1:
                raise _PipelineContractError("Invalid prepared input timeline.")
            samples = round(frames * _OUTPUT_RATE / rate)
    except (OSError, EOFError, wave.Error) as error:
        raise _PipelineContractError("Invalid prepared input timeline.") from error
    if not 1 <= samples <= _MAX_OUTPUT_SAMPLES:
        raise _PipelineContractError("Invalid prepared input timeline.")
    return samples


def _require_geometry(pipeline: Any) -> None:
    """Reject changes to the reviewed frame and context geometry."""

    values = {
        name: getattr(pipeline, name, None)
        for name in (
            "sr", "window", "t_pad", "t_pad2", "t_pad_tgt",
            "t_query", "t_center", "t_max",
        )
    }
    if (
        any(type(value) is not int or value < 1 for value in values.values())
        or values["sr"] != _INPUT_RATE
        or values["window"] != _FRAME_SAMPLES
        or values["t_pad2"] != 2 * values["t_pad"]
        or values["t_pad"] % _FRAME_SAMPLES != 0
        or values["t_pad_tgt"] != _output_position(values["t_pad"])
        or values["t_query"] >= values["t_center"]
        or not callable(getattr(pipeline, "get_f0", None))
        or not callable(getattr(pipeline, "vc", None))
    ):
        raise _PipelineContractError("Unsupported RVC pipeline geometry.")


def _quiet_cuts(audio: Any, pipeline: Any, np: Any) -> list[int]:
    """Retain the upstream quiet-point search and its original 10 ms grid."""

    audio_pad = np.pad(
        audio, (pipeline.window // 2, pipeline.window // 2), mode="reflect",
    )
    cuts: list[int] = []
    if audio_pad.shape[0] > pipeline.t_max:
        audio_sum = np.zeros_like(audio)
        for offset in range(pipeline.window):
            audio_sum += np.abs(audio_pad[offset : offset - pipeline.window])
        for center in range(pipeline.t_center, audio.shape[0], pipeline.t_center):
            search = audio_sum[
                center - pipeline.t_query : center + pipeline.t_query
            ]
            quiet = center - pipeline.t_query + int(np.where(search == search.min())[0][0])
            cut = quiet // pipeline.window * pipeline.window
            if cut <= (cuts[-1] if cuts else 0) or cut >= len(audio):
                raise _PipelineContractError("Invalid RVC source segment interval.")
            cuts.append(cut)
    return cuts


def _core_audio(
    generated: Any,
    *,
    left_pad: int,
    output_start: int,
    output_end: int,
    np: Any,
) -> Any:
    """Select one exact generated core, rejecting absent right-context samples."""

    samples = np.asarray(generated)
    expected = output_end - output_start
    stop = left_pad + expected
    if (
        samples.ndim != 1
        or left_pad < 0
        or output_start < 0
        or expected < 1
        or stop > len(samples)
    ):
        raise _PipelineContractError("RVC did not generate the complete source core.")
    # A copied core releases the much larger left/right context after each GPU
    # call. Ownership is exclusive, so a view must not retain all song contexts.
    return samples[left_pad:stop].copy()


def _run_pipeline(
    pipeline: Any,
    vendor: Any,
    target_samples: int,
    model: Any,
    net_g: Any,
    sid: int,
    audio: Any,
    times: Any,
    f0_up_key: int,
    f0_method: str,
    file_index: str,
    index_rate: float,
    if_f0: int,
    tgt_sr: int,
    resample_sr: int,
    rms_mix_rate: float,
    version: str,
    protect: float,
) -> Any:
    """Run only the fixed 40 kHz/F0 v2 profile with exact core ownership."""

    _require_geometry(pipeline)
    if (
        type(sid) is not int or sid != 0
        or type(f0_up_key) is not int or not -12 <= f0_up_key <= 12
        or f0_method != "rmvpe" or not isinstance(file_index, str)
        or index_rate != 0.0 or if_f0 != 1 or tgt_sr != _OUTPUT_RATE
        or resample_sr != 0 or rms_mix_rate != 0.25
        or version != "v2" or protect != 0.33
        or not 1 <= target_samples <= _MAX_OUTPUT_SAMPLES
    ):
        raise _PipelineContractError("Unsupported RVC inference profile.")
    np, torch = vendor.np, vendor.torch
    source = np.asarray(audio)
    if (
        source.ndim != 1 or len(source) < _FRAME_SAMPLES
        or not bool(np.all(np.isfinite(source)))
        or abs(_output_position(len(source)) - target_samples)
        > _MAX_RESAMPLE_ROUNDING_SAMPLES
    ):
        raise _PipelineContractError("Decoded input differs from the prepared timeline.")

    # Everything before generation intentionally follows the reviewed vendor
    # order, including filtering before cut selection and one global F0 pass.
    source = vendor.signal.filtfilt(vendor.bh, vendor.ah, source)
    cuts = _quiet_cuts(source, pipeline, np)
    padded = np.pad(source, (pipeline.t_pad, pipeline.t_pad), mode="reflect")
    frame_count = padded.shape[0] // pipeline.window
    started = vendor.ttime()
    speaker = torch.tensor(sid, device=pipeline.device).unsqueeze(0).long()
    pitch, pitchf = pipeline.get_f0(padded, frame_count, f0_up_key, f0_method)
    if len(pitch) < frame_count or len(pitchf) < frame_count:
        raise _PipelineContractError("RVC pitch does not cover the padded input.")
    pitch = torch.tensor(pitch[:frame_count], device=pipeline.device).unsqueeze(0).long()
    pitchf = torch.tensor(
        pitchf[:frame_count].astype(np.float32), device=pipeline.device,
    ).unsqueeze(0).float()
    times[1] += vendor.ttime() - started

    parts = []
    start = 0
    for end in [*cuts, len(source)]:
        final = end == len(source)
        # Preserve the upstream extra 10 ms of middle-segment HuBERT context;
        # its pitch slice deliberately ends one frame earlier. Altering either
        # input would change inference instead of repairing output ownership.
        audio_end = None if final else end + pipeline.t_pad2 + pipeline.window
        pitch_end = None if final else (end + pipeline.t_pad2) // pipeline.window
        generated = pipeline.vc(
            model, net_g, speaker,
            padded[start:audio_end],
            pitch[:, start // pipeline.window : pitch_end],
            pitchf[:, start // pipeline.window : pitch_end],
            times, None, None, index_rate, version, protect,
        )
        parts.append(_core_audio(
            generated,
            left_pad=pipeline.t_pad_tgt,
            output_start=_output_position(start),
            output_end=target_samples if final else _output_position(end),
            np=np,
        ))
        del generated
        start = end
    output = np.concatenate(parts)
    del parts
    if len(output) != target_samples or not bool(np.all(np.isfinite(output))):
        raise _PipelineContractError("Invalid assembled RVC waveform.")

    # Retain the vendor's whole-song envelope blend and normalization exactly.
    # In particular there is no per-segment gain, seam ramp, limiter or pad.
    output = vendor.change_rms(source, _INPUT_RATE, output, tgt_sr, rms_mix_rate)
    if len(output) != target_samples or not bool(np.all(np.isfinite(output))):
        raise _PipelineContractError("Invalid RVC loudness result.")
    audio_max = np.abs(output).max() / 0.99
    max_int16 = 32768
    if audio_max > 1:
        max_int16 /= audio_max
    output = (output * max_int16).astype(np.int16)
    del pitch, pitchf, speaker
    if torch.cuda.is_available() and not vendor.cuda_graph_enabled(pipeline.device):
        torch.cuda.empty_cache()
    return output


def bind_exact_timing_pipeline(
    converter: Any,
    vendor: Any,
    input_audio: Path,
) -> Callable[[], None]:
    """Bind one verified converter's pipeline and return its idempotent restore.

    ``vendor`` must be the caller-authenticated ``infer.vc.pipeline`` module.
    The prepared PCM WAV supplies the final sample count. No vendor file or
    class is changed, and the returned cleanup restores this instance even if
    inference fails. The caller remains responsible for model/resource release.
    """

    pipeline = getattr(converter, "pipeline", None)
    if type(pipeline) is not vendor.Pipeline:
        raise _PipelineContractError("Unexpected RVC pipeline instance.")
    _require_geometry(pipeline)
    original = pipeline.pipeline
    target_samples = _wav_output_samples(input_audio)

    def _bound(*arguments: Any) -> Any:
        return _run_pipeline(pipeline, vendor, target_samples, *arguments)

    pipeline.pipeline = _bound

    def _restore() -> None:
        if pipeline.pipeline is _bound:
            pipeline.pipeline = original

    return _restore


__all__ = ["bind_exact_timing_pipeline"]
