"""Benchmark the private Windows voice pipeline without retaining user data.

The command measures the production resource shape rather than changing it:
Ollama streaming and managed GPT-SoVITS synthesis overlap, then CPU
Faster-Whisper consumes the synthesized WAV while both GPU services remain
resident. Only bounded timing and global GPU metadata reach JSON output. Test
text, generated audio, transcripts, filesystem paths, and exception details
remain process-local and are discarded before exit.
"""

from __future__ import annotations

import argparse
from array import array
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass, replace
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Event, Lock, Thread, Timer
from time import monotonic
from typing import Any, Final, Protocol, cast
from urllib.parse import urlsplit
import wave

import requests
from requests import Response, Session
from requests.exceptions import RequestException
from urllib3.util import Timeout as Urllib3Timeout

# A direct script invocation exposes only ``scripts`` on sys.path. Anchoring
# the repository root makes the documented CMD command independent of cwd.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SETTINGS
from desktop_speech import DesktopSpeechConfig
from voice import (
    FasterWhisperConfig,
    FasterWhisperTranscriber,
    JsonVoiceProfileCatalog,
    ManagedGptSovitsConfig,
    ManagedGptSovitsLease,
    ManagedGptSovitsRuntime,
    SynthesisResult,
    TranscriptionRequest,
    VoiceCapture,
)


_BENCHMARK_PROMPT: Final = (
    "请用一句简短中文说明本地语音管线正在进行资源基准测试。"
)
_SYNTHESIS_TEXT: Final = "这是一段只在内存中使用的本地语音管线基准音频。"
_DEFAULT_CYCLES: Final = 3
_MAX_CYCLES: Final = 50
_DEFAULT_SAMPLE_INTERVAL_MS: Final = 250
_MIN_SAMPLE_INTERVAL_MS: Final = 100
_MAX_SAMPLE_INTERVAL_MS: Final = 2_000
_DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 180.0
_MAX_STREAM_FRAMES: Final = 16_384
_MAX_STREAM_FRAME_BYTES: Final = 1_048_576
_MAX_STREAM_BYTES: Final = 8 * 1_048_576
_MAX_PROCESS_RESPONSE_BYTES: Final = 4 * 1_048_576
_GPU_QUERY_TIMEOUT_SECONDS: Final = 5.0
_TARGET_SAMPLE_RATE_HZ: Final = 16_000
_CAPTURE_FRAME_SAMPLES: Final = 320
_CAPTURE_MIN_SAMPLES: Final = 3_200
_CAPTURE_MAX_SAMPLES: Final = 480_000


class _BenchmarkFailure(Exception):
    """Carry one closed failure code without retaining private diagnostics."""

    def __init__(self, code: str) -> None:
        """Store a stable code suitable for JSON stderr output."""

        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _BenchmarkConfig:
    """Freeze bounded command options before local services are touched."""

    cycles: int
    sample_interval_seconds: float
    request_timeout_seconds: float

    def __post_init__(self) -> None:
        """Reject values that could create an unbounded local workload."""

        if type(self.cycles) is not int or not 1 <= self.cycles <= _MAX_CYCLES:
            raise ValueError("cycles is outside the benchmark range")
        if (
            type(self.sample_interval_seconds) is not float
            or not _MIN_SAMPLE_INTERVAL_MS / 1_000.0
            <= self.sample_interval_seconds
            <= _MAX_SAMPLE_INTERVAL_MS / 1_000.0
        ):
            raise ValueError("sample interval is outside the benchmark range")
        if (
            type(self.request_timeout_seconds) is not float
            or not 1.0 <= self.request_timeout_seconds <= 300.0
        ):
            raise ValueError("request timeout is outside the benchmark range")


@dataclass(frozen=True, slots=True)
class _GpuDeviceSample:
    """Hold one global nvidia-smi observation for a physical GPU."""

    name: str
    total_mib: int
    used_mib: int
    utilization_percent: int


@dataclass(frozen=True, slots=True)
class _OllamaMetrics:
    """Hold safe local and terminal-frame timing for one streamed reply."""

    ttft_seconds: float
    elapsed_seconds: float
    server_total_seconds: float
    load_seconds: float
    prompt_eval_seconds: float
    eval_seconds: float
    prompt_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class _SynthesisMetrics:
    """Hold timing and duration while keeping WAV bytes out of reports."""

    audio: bytes
    elapsed_seconds: float
    audio_seconds: float


@dataclass(frozen=True, slots=True)
class _TranscriptionMetrics:
    """Hold only CPU transcription latency after discarding its text."""

    elapsed_seconds: float


class _GpuMonitor(Protocol):
    """Describe the global GPU sampler used by the benchmark transaction."""

    def start(self) -> None:
        """Capture a baseline and begin periodic sampling."""

    def stop(self) -> dict[str, object]:
        """Stop sampling and return a sanitized aggregate."""


class _VoicePipeline(Protocol):
    """Describe the co-resident production services behind the benchmark."""

    def prepare(self) -> float:
        """Start managed TTS and return its cold acquisition time."""

    def stream_ollama(self) -> _OllamaMetrics:
        """Consume one fixed streamed reply without retaining its content."""

    def synthesize(self, cycle: int) -> _SynthesisMetrics:
        """Synthesize one fixed sentence entirely in memory."""

    def ollama_size_vram_bytes(self) -> int:
        """Return configured-model residency reported by Ollama ``/api/ps``."""

    def transcribe(
        self,
        synthesis: _SynthesisMetrics,
        cycle: int,
    ) -> _TranscriptionMetrics:
        """Transcribe generated PCM on CPU and immediately discard text."""

    def close(self) -> None:
        """Release every owned service even after partial initialization."""


@dataclass(frozen=True, slots=True)
class _BenchmarkDependencies:
    """Bundle injectable platform, sampler, pipeline, and monotonic clock."""

    platform_name: str
    monitor_factory: Callable[[float], _GpuMonitor]
    pipeline_factory: Callable[[], _VoicePipeline]
    clock: Callable[[], float] = monotonic


def _bounded_cycles(value: str) -> int:
    """Parse a finite cycle count before composing expensive dependencies."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("cycles must be an integer") from error
    if not 1 <= parsed <= _MAX_CYCLES:
        raise argparse.ArgumentTypeError(
            f"cycles must be between 1 and {_MAX_CYCLES}"
        )
    return parsed


def _bounded_interval_ms(value: str) -> int:
    """Parse a GPU interval that is responsive without polling excessively."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "sample interval must be an integer"
        ) from error
    if not _MIN_SAMPLE_INTERVAL_MS <= parsed <= _MAX_SAMPLE_INTERVAL_MS:
        raise argparse.ArgumentTypeError(
            "sample interval is outside the supported range"
        )
    return parsed


def _bounded_timeout(value: str) -> float:
    """Parse a finite per-request timeout accepted by local dependencies."""

    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be numeric") from error
    if not 1.0 <= parsed <= 300.0:
        raise argparse.ArgumentTypeError(
            "timeout must be between 1 and 300 seconds"
        )
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    """Create the bounded CLI without accepting text, audio, or path inputs."""

    parser = argparse.ArgumentParser(
        description=(
            "Benchmark co-resident local Ollama, GPT-SoVITS, and CPU "
            "Faster-Whisper without writing test content or audio to disk."
        )
    )
    parser.add_argument(
        "--cycles",
        type=_bounded_cycles,
        default=_DEFAULT_CYCLES,
        help=f"Measured cycles, 1-{_MAX_CYCLES} (default: {_DEFAULT_CYCLES}).",
    )
    parser.add_argument(
        "--sample-interval-ms",
        type=_bounded_interval_ms,
        default=_DEFAULT_SAMPLE_INTERVAL_MS,
        help="Global nvidia-smi sampling interval in milliseconds.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=_bounded_timeout,
        default=_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        help="Bound for each local Ollama or GPT-SoVITS request.",
    )
    return parser


def _system_directory() -> Path:
    """Resolve the authoritative Windows system directory through kernel32."""

    if os.name != "nt":
        raise _BenchmarkFailure("platform_unsupported")
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(  # type: ignore[attr-defined]
            buffer,
            len(buffer),
        )
    except (AttributeError, OSError, ValueError):
        raise _BenchmarkFailure("gpu_sampler_unavailable") from None
    if type(length) is not int or length <= 0 or length >= len(buffer):
        raise _BenchmarkFailure("gpu_sampler_unavailable")
    return Path(buffer.value)


def _parse_nvidia_csv(value: str) -> tuple[_GpuDeviceSample, ...]:
    """Parse one bounded all-device CSV snapshot from fixed nvidia-smi fields."""

    if not isinstance(value, str) or len(value) > 65_536:
        raise _BenchmarkFailure("gpu_sampler_unavailable")
    try:
        rows = tuple(csv.reader(StringIO(value)))
        if not 1 <= len(rows) <= 16:
            raise ValueError("unexpected GPU count")
        samples: list[_GpuDeviceSample] = []
        for row in rows:
            if len(row) != 4:
                raise ValueError("unexpected GPU field count")
            name = row[0].strip()
            total_mib = int(row[1].strip())
            used_mib = int(row[2].strip())
            utilization = int(row[3].strip())
            if (
                not name
                or len(name) > 256
                or not 1 <= total_mib <= 1_048_576
                or not 0 <= used_mib <= total_mib
                or not 0 <= utilization <= 100
            ):
                raise ValueError("invalid GPU values")
            samples.append(
                _GpuDeviceSample(
                    name=name,
                    total_mib=total_mib,
                    used_mib=used_mib,
                    utilization_percent=utilization,
                )
            )
        return tuple(samples)
    except (csv.Error, TypeError, ValueError):
        raise _BenchmarkFailure("gpu_sampler_unavailable") from None


class _NvidiaSmiMonitor:
    """Collect global GPU usage because WDDM lacks reliable process VRAM."""

    def __init__(self, interval_seconds: float) -> None:
        """Store the bounded interval without starting a subprocess."""

        self._interval_seconds = interval_seconds
        self._executable = _system_directory() / "nvidia-smi.exe"
        self._stop_event = Event()
        self._lock = Lock()
        self._snapshots: list[tuple[_GpuDeviceSample, ...]] = []
        self._failure = False
        self._thread: Thread | None = None
        self._stopped = False

    def _query(self) -> tuple[_GpuDeviceSample, ...]:
        """Run one fixed no-shell nvidia-smi query with a hard timeout."""

        try:
            completed = subprocess.run(
                [
                    str(self._executable),
                    "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=_GPU_QUERY_TIMEOUT_SECONDS,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise _BenchmarkFailure("gpu_sampler_unavailable") from None
        if completed.returncode != 0 or completed.stderr.strip():
            raise _BenchmarkFailure("gpu_sampler_unavailable")
        return _parse_nvidia_csv(completed.stdout)

    def start(self) -> None:
        """Capture a synchronous baseline, then start one owned sampler thread."""

        if self._thread is not None or self._stopped:
            raise _BenchmarkFailure("gpu_sampler_unavailable")
        baseline = self._query()
        self._snapshots.append(baseline)
        self._thread = Thread(
            target=self._sample_until_stopped,
            name="elysia-voice-benchmark-gpu",
            daemon=False,
        )
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            raise _BenchmarkFailure("gpu_sampler_unavailable") from None

    def _sample_until_stopped(self) -> None:
        """Append periodic snapshots until stop or a sampler failure wins."""

        while not self._stop_event.wait(self._interval_seconds):
            try:
                snapshot = self._query()
            except _BenchmarkFailure:
                with self._lock:
                    self._failure = True
                self._stop_event.set()
                return
            with self._lock:
                self._snapshots.append(snapshot)

    def stop(self) -> dict[str, object]:
        """Join the sampler and aggregate baseline, peak, and final usage."""

        if self._stopped:
            raise _BenchmarkFailure("gpu_sampler_unavailable")
        self._stopped = True
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(_GPU_QUERY_TIMEOUT_SECONDS + self._interval_seconds + 1.0)
            if thread.is_alive():
                raise _BenchmarkFailure("gpu_sampler_unavailable")
        try:
            final_snapshot = self._query()
        except _BenchmarkFailure:
            final_snapshot = ()
            with self._lock:
                self._failure = True
        with self._lock:
            if final_snapshot:
                self._snapshots.append(final_snapshot)
            snapshots = tuple(self._snapshots)
            failed = self._failure
        if failed or not snapshots:
            raise _BenchmarkFailure("gpu_sampler_unavailable")
        device_count = len(snapshots[0])
        if any(len(snapshot) != device_count for snapshot in snapshots):
            raise _BenchmarkFailure("gpu_sampler_unavailable")
        devices: list[dict[str, object]] = []
        for index in range(device_count):
            observations = tuple(snapshot[index] for snapshot in snapshots)
            first = observations[0]
            if any(
                observation.name != first.name
                or observation.total_mib != first.total_mib
                for observation in observations
            ):
                raise _BenchmarkFailure("gpu_sampler_unavailable")
            devices.append(
                {
                    "index": index,
                    "name": first.name,
                    "total_mib": first.total_mib,
                    "baseline_used_mib": first.used_mib,
                    "peak_used_mib": max(item.used_mib for item in observations),
                    "final_used_mib": observations[-1].used_mib,
                    "peak_utilization_percent": max(
                        item.utilization_percent for item in observations
                    ),
                }
            )
        return {
            "scope": "global",
            "sample_count": len(snapshots),
            "devices": devices,
        }


def _loopback_origin(value: str) -> str:
    """Require a credential-free HTTP loopback origin for fixed test text."""

    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        raise _BenchmarkFailure("ollama_unavailable") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise _BenchmarkFailure("ollama_unavailable")
    host = parsed.hostname
    if host == "::1":
        host = "[::1]"
    return f"http://{host}{f':{port}' if port is not None else ''}"


def _nonnegative_int(value: object) -> int:
    """Require one exact non-negative integer from an Ollama metric frame."""

    if type(value) is not int or value < 0:
        raise _BenchmarkFailure("ollama_invalid_response")
    return value


def _nanoseconds(value: object) -> float:
    """Convert one exact non-negative Ollama duration to rounded seconds."""

    return round(_nonnegative_int(value) / 1_000_000_000.0, 6)


def _total_http_timeout(total_seconds: float) -> Urllib3Timeout:
    """Apply one wall budget across connection and response-header phases."""

    return Urllib3Timeout(
        total=total_seconds,
        connect=min(5.0, total_seconds),
        read=total_seconds,
    )


def _iter_bounded_ndjson(
    response: Response,
    *,
    expired: Event,
    deadline: float,
    clock: Callable[[], float],
) -> Iterator[tuple[bytes, float]]:
    """Yield bounded decoded-body NDJSON lines without hidden line buffering.

    ``requests.Response.iter_lines`` buffers until a delimiter appears, so an
    untrusted loopback peer could send one unbounded line before the caller's
    checks run. Reading fixed decoded chunks here caps both the total body and
    the still-unframed line before JSON parsing.
    """

    pending = bytearray()
    total_bytes = 0
    frame_count = 0
    for chunk in response.iter_content(chunk_size=65_536):
        observed_at = clock()
        if expired.is_set() or observed_at >= deadline:
            raise _BenchmarkFailure("ollama_timeout")
        if not isinstance(chunk, bytes):
            raise _BenchmarkFailure("ollama_invalid_response")
        if not chunk:
            continue
        if len(chunk) > _MAX_STREAM_BYTES - total_bytes:
            raise _BenchmarkFailure("ollama_invalid_response")
        total_bytes += len(chunk)
        pending.extend(chunk)
        while True:
            delimiter = pending.find(b"\n")
            if delimiter < 0:
                break
            raw_line = bytes(pending[:delimiter])
            del pending[: delimiter + 1]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            frame_count += 1
            if (
                frame_count > _MAX_STREAM_FRAMES
                or len(raw_line) > _MAX_STREAM_FRAME_BYTES
            ):
                raise _BenchmarkFailure("ollama_invalid_response")
            yield raw_line, observed_at
        if len(pending) > _MAX_STREAM_FRAME_BYTES:
            raise _BenchmarkFailure("ollama_invalid_response")

    if pending:
        observed_at = clock()
        if expired.is_set() or observed_at >= deadline:
            raise _BenchmarkFailure("ollama_timeout")
        frame_count += 1
        if frame_count > _MAX_STREAM_FRAMES:
            raise _BenchmarkFailure("ollama_invalid_response")
        yield bytes(pending), observed_at


def _read_bounded_response_body(
    response: Response,
    *,
    max_bytes: int,
    expired: Event,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    """Read one streamed response under decoded-size and wall-clock bounds."""

    body = bytearray()
    for chunk in response.iter_content(chunk_size=65_536):
        observed_at = clock()
        if expired.is_set() or observed_at >= deadline:
            raise _BenchmarkFailure("ollama_timeout")
        if not isinstance(chunk, bytes):
            raise _BenchmarkFailure("ollama_invalid_response")
        if not chunk:
            continue
        if len(chunk) > max_bytes - len(body):
            raise _BenchmarkFailure("ollama_invalid_response")
        body.extend(chunk)
    # Closing a timed-out Requests response may end iteration normally. Check
    # again after EOF so a legal final chunk cannot conceal a deadline that
    # expired while the peer delayed stream termination.
    finished_at = clock()
    if expired.is_set() or finished_at >= deadline:
        raise _BenchmarkFailure("ollama_timeout")
    return bytes(body)


class _OllamaBenchmarkClient:
    """Collect stream and residency metrics without retaining generated text."""

    def __init__(
        self,
        origin: str,
        model_name: str,
        timeout_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Validate the logical model and create a proxy-free local session."""

        model = model_name.strip()
        if not model or len(model) > 256:
            raise _BenchmarkFailure("ollama_unavailable")
        self._origin = _loopback_origin(origin)
        self._model_name = model
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._session = Session()
        # Local fixed test text must never follow ambient HTTP proxy settings.
        self._session.trust_env = False

    def _require_success(self, response: Response) -> None:
        """Map every non-success response to one detail-free failure code."""

        if not 200 <= response.status_code < 300:
            raise _BenchmarkFailure("ollama_request_failed")

    def stream(self) -> _OllamaMetrics:
        """Consume a fixed NDJSON chat stream and retain only numeric metrics."""

        started = self._clock()
        deadline = started + self._timeout_seconds
        first_token_at: float | None = None
        final_frame: dict[str, object] | None = None
        saw_content = False
        expired = Event()
        response_holder: list[Response | None] = [None]

        def _expire() -> None:
            """Close an attached response when the hard wall deadline wins."""

            expired.set()
            response = response_holder[0]
            if response is not None:
                try:
                    response.close()
                except BaseException:
                    # The owning request path reports the closed timeout code;
                    # timer-thread cleanup must never print private transport
                    # diagnostics to stderr.
                    pass

        deadline_timer = Timer(self._timeout_seconds, _expire)
        deadline_timer.daemon = False
        deadline_timer.start()
        payload = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": _BENCHMARK_PROMPT}],
            "stream": True,
            "think": False,
            "keep_alive": "10m",
        }
        try:
            with self._session.post(
                f"{self._origin}/api/chat",
                json=cast(Any, payload),
                stream=True,
                # The validated loopback origin is the entire network trust
                # boundary. Following a local redirect could exfiltrate the
                # fixed prompt or turn the diagnostic into arbitrary I/O.
                allow_redirects=False,
                timeout=cast(Any, _total_http_timeout(self._timeout_seconds)),
            ) as response:
                response_holder[0] = response
                if expired.is_set() or self._clock() >= deadline:
                    raise _BenchmarkFailure("ollama_timeout")
                self._require_success(response)
                for raw_line, observed_at in _iter_bounded_ndjson(
                    response,
                    expired=expired,
                    deadline=deadline,
                    clock=self._clock,
                ):
                    if not raw_line:
                        continue
                    try:
                        decoded: object = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeError, ValueError, TypeError):
                        raise _BenchmarkFailure(
                            "ollama_invalid_response"
                        ) from None
                    if not isinstance(decoded, dict):
                        raise _BenchmarkFailure("ollama_invalid_response")
                    frame = cast(dict[str, object], decoded)
                    if "error" in frame:
                        raise _BenchmarkFailure("ollama_request_failed")
                    message = frame.get("message")
                    if not isinstance(message, dict):
                        raise _BenchmarkFailure("ollama_invalid_response")
                    content = message.get("content")
                    if not isinstance(content, str):
                        raise _BenchmarkFailure("ollama_invalid_response")
                    if content:
                        saw_content = True
                        if first_token_at is None:
                            first_token_at = observed_at
                    if frame.get("done") is True:
                        final_frame = frame
                        break
        except _BenchmarkFailure:
            raise
        except RequestException:
            code = "ollama_timeout" if expired.is_set() else "ollama_unavailable"
            raise _BenchmarkFailure(code) from None
        finally:
            deadline_timer.cancel()
            deadline_timer.join()
            response_holder[0] = None
        finished = self._clock()
        if expired.is_set() or finished >= deadline:
            raise _BenchmarkFailure("ollama_timeout")
        if final_frame is None or first_token_at is None or not saw_content:
            raise _BenchmarkFailure("ollama_invalid_response")
        return _OllamaMetrics(
            ttft_seconds=round(first_token_at - started, 6),
            elapsed_seconds=round(finished - started, 6),
            server_total_seconds=_nanoseconds(final_frame.get("total_duration")),
            load_seconds=_nanoseconds(final_frame.get("load_duration")),
            prompt_eval_seconds=_nanoseconds(
                final_frame.get("prompt_eval_duration")
            ),
            eval_seconds=_nanoseconds(final_frame.get("eval_duration")),
            prompt_tokens=_nonnegative_int(final_frame.get("prompt_eval_count")),
            output_tokens=_nonnegative_int(final_frame.get("eval_count")),
        )

    def size_vram_bytes(self) -> int:
        """Read configured-model VRAM residency from Ollama ``/api/ps``."""

        started = self._clock()
        deadline = started + self._timeout_seconds
        expired = Event()
        response_holder: list[Response | None] = [None]

        def _expire() -> None:
            """Close an attached process-list response at the wall deadline."""

            expired.set()
            response = response_holder[0]
            if response is not None:
                try:
                    response.close()
                except BaseException:
                    pass

        deadline_timer = Timer(self._timeout_seconds, _expire)
        deadline_timer.daemon = False
        deadline_timer.start()
        try:
            with self._session.get(
                f"{self._origin}/api/ps",
                stream=True,
                # Keep residency inspection on the same validated loopback
                # origin instead of inheriting Requests' redirect behavior.
                allow_redirects=False,
                timeout=cast(Any, _total_http_timeout(self._timeout_seconds)),
            ) as response:
                response_holder[0] = response
                if expired.is_set() or self._clock() >= deadline:
                    raise _BenchmarkFailure("ollama_timeout")
                self._require_success(response)
                body = _read_bounded_response_body(
                    response,
                    max_bytes=_MAX_PROCESS_RESPONSE_BYTES,
                    expired=expired,
                    deadline=deadline,
                    clock=self._clock,
                )
                decoded: object = json.loads(body)
        except _BenchmarkFailure:
            raise
        except (RequestException, ValueError):
            code = "ollama_timeout" if expired.is_set() else "ollama_unavailable"
            raise _BenchmarkFailure(code) from None
        finally:
            deadline_timer.cancel()
            deadline_timer.join()
            response_holder[0] = None
        if not isinstance(decoded, dict):
            raise _BenchmarkFailure("ollama_invalid_response")
        models = decoded.get("models")
        if not isinstance(models, list) or len(models) > 1_024:
            raise _BenchmarkFailure("ollama_invalid_response")
        residency: int | None = None
        for candidate in models:
            if not isinstance(candidate, dict):
                continue
            if self._model_name not in (
                candidate.get("name"),
                candidate.get("model"),
            ):
                continue
            residency = _nonnegative_int(candidate.get("size_vram"))
            break
        # Parsing and bounded model matching are part of the request's hard
        # wall-clock budget, even though the HTTP body already reached EOF.
        finished_at = self._clock()
        if expired.is_set() or finished_at >= deadline:
            raise _BenchmarkFailure("ollama_timeout")
        if residency is None:
            raise _BenchmarkFailure("ollama_model_not_resident")
        return residency

    def close(self) -> None:
        """Close pooled loopback connections without unloading shared Ollama."""

        self._session.close()


def _pcm_array(pcm_s16le: bytes) -> array[int]:
    """Decode immutable signed little-endian PCM into native integer samples."""

    samples = array("h")
    samples.frombytes(pcm_s16le)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _resample_mono_s16le(
    pcm_s16le: bytes,
    source_rate_hz: int,
) -> bytes:
    """Box-resample mono PCM to 16 kHz without creating a temporary file.

    Each target sample averages the source interval that maps to it. This
    simple bounded filter is sufficient for a synthesized acceptance phrase
    and avoids adding a second native audio dependency to the diagnostic.
    """

    if (
        type(source_rate_hz) is not int
        or not 8_000 <= source_rate_hz <= 192_000
        or len(pcm_s16le) % 2 != 0
        or not pcm_s16le
    ):
        raise _BenchmarkFailure("tts_invalid_audio")
    source = _pcm_array(pcm_s16le)
    if source_rate_hz == _TARGET_SAMPLE_RATE_HZ:
        output = array("h", source)
    else:
        target_count = (
            len(source) * _TARGET_SAMPLE_RATE_HZ // source_rate_hz
        )
        if target_count <= 0:
            raise _BenchmarkFailure("tts_invalid_audio")
        output = array("h")
        for index in range(target_count):
            start = index * source_rate_hz // _TARGET_SAMPLE_RATE_HZ
            end = (index + 1) * source_rate_hz // _TARGET_SAMPLE_RATE_HZ
            if end <= start:
                # Upsampling maps some target points to an empty interval; use
                # the nearest available source point instead of inventing I/O.
                output.append(source[min(start, len(source) - 1)])
            else:
                bounded_end = min(end, len(source))
                window = source[start:bounded_end]
                output.append(round(sum(window) / len(window)))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def _capture_from_wav(audio: bytes, cycle: int) -> tuple[VoiceCapture, float]:
    """Convert a managed mono PCM WAV to one bounded in-memory STT capture."""

    try:
        with wave.open(BytesIO(audio), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getcomptype() != "NONE"
            ):
                raise _BenchmarkFailure("tts_invalid_audio")
            source_rate = wav_file.getframerate()
            source_frames = wav_file.getnframes()
            pcm = wav_file.readframes(source_frames)
            if wav_file.readframes(1) != b"":
                raise _BenchmarkFailure("tts_invalid_audio")
    except _BenchmarkFailure:
        raise
    except (EOFError, wave.Error):
        raise _BenchmarkFailure("tts_invalid_audio") from None
    if source_frames <= 0 or source_rate <= 0 or len(pcm) != source_frames * 2:
        raise _BenchmarkFailure("tts_invalid_audio")
    converted = _resample_mono_s16le(pcm, source_rate)
    sample_count = min(len(converted) // 2, _CAPTURE_MAX_SAMPLES)
    # VoiceCapture requires exact 20 ms boundaries. Truncating the final
    # partial frame is deterministic and avoids padding speech-duration data.
    sample_count -= sample_count % _CAPTURE_FRAME_SAMPLES
    if sample_count < _CAPTURE_MIN_SAMPLES:
        raise _BenchmarkFailure("tts_invalid_audio")
    converted = converted[: sample_count * 2]
    if not any(converted):
        raise _BenchmarkFailure("tts_invalid_audio")
    capture = VoiceCapture(
        session_id=f"voice_benchmark_{cycle}",
        pcm_s16le=converted,
        sample_rate_hz=_TARGET_SAMPLE_RATE_HZ,
        sample_count=sample_count,
        speech_start_sample=0,
        speech_end_sample=sample_count,
    )
    return capture, round(source_frames / source_rate, 6)


class _ProductionVoicePipeline:
    """Own one managed TTS lease, CPU STT adapter, and local Ollama client."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Compose lightweight owners without loading any model weights."""

        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._speech_config = DesktopSpeechConfig.from_app_settings(SETTINGS)
        self._ollama = _OllamaBenchmarkClient(
            SETTINGS.ollama_host,
            SETTINGS.model_name,
            timeout_seconds,
            clock=clock,
        )
        model_path = (
            SETTINGS.base_dir.resolve()
            / "models"
            / "weights"
            / "faster-whisper"
            / SETTINGS.transcription_model
        )
        self._transcriber = FasterWhisperTranscriber(
            FasterWhisperConfig(model_path=model_path, device="cpu")
        )
        self._runtime: ManagedGptSovitsRuntime | None = None
        self._lease: ManagedGptSovitsLease | None = None

    def prepare(self) -> float:
        """Acquire the rights-gated managed GPU voice lease exactly once."""

        if not self._speech_config.allow_local_evaluation:
            raise _BenchmarkFailure("tts_not_authorized")
        status = self._transcriber.get_status()
        if status.state == "unavailable" or status.device != "cpu":
            raise _BenchmarkFailure("stt_unavailable")
        try:
            catalog = JsonVoiceProfileCatalog.load(
                self._speech_config.catalog_path,
                self._speech_config.asset_root,
                allow_local_evaluation=True,
            )
            selection = catalog.resolve_selection(
                self._speech_config.voice_profile_id,
                "neutral",
            )
            selection = replace(
                selection,
                speed_factor=self._speech_config.speech_rate_percent / 100.0,
            )
            runtime = ManagedGptSovitsRuntime(
                ManagedGptSovitsConfig(
                    runtime_root=self._speech_config.runtime_root,
                    worker_script=self._speech_config.worker_script,
                    startup_timeout_seconds=min(
                        300.0,
                        max(1.0, self._timeout_seconds),
                    ),
                    synthesis_timeout_seconds=self._timeout_seconds,
                    device="cuda",
                    seed=self._speech_config.deterministic_seed,
                )
            )
            self._runtime = runtime
            started = self._clock()
            self._lease = runtime.acquire_lease(selection)
            return round(self._clock() - started, 6)
        except _BenchmarkFailure:
            raise
        except Exception:
            raise _BenchmarkFailure("tts_unavailable") from None

    def stream_ollama(self) -> _OllamaMetrics:
        """Delegate one fixed streaming request to the no-retention client."""

        return self._ollama.stream()

    def synthesize(self, cycle: int) -> _SynthesisMetrics:
        """Generate one WAV in memory with a unique managed operation token."""

        lease = self._lease
        if lease is None:
            raise _BenchmarkFailure("tts_unavailable")
        started = self._clock()
        try:
            result: SynthesisResult = lease.synthesize(
                _SYNTHESIS_TEXT,
                "zh",
                secrets.token_hex(32),
            )
            capture, duration = _capture_from_wav(result.audio, cycle)
        except _BenchmarkFailure:
            raise
        except Exception:
            raise _BenchmarkFailure("tts_failed") from None
        # The temporary capture validates that the WAV can enter STT, but only
        # the original in-memory audio continues to the measured STT call.
        del capture
        return _SynthesisMetrics(
            audio=result.audio,
            elapsed_seconds=round(self._clock() - started, 6),
            audio_seconds=duration,
        )

    def ollama_size_vram_bytes(self) -> int:
        """Return Ollama's model-specific VRAM value while it remains loaded."""

        return self._ollama.size_vram_bytes()

    def transcribe(
        self,
        synthesis: _SynthesisMetrics,
        cycle: int,
    ) -> _TranscriptionMetrics:
        """Run CPU STT on synthesized PCM and discard the result immediately."""

        capture, _duration = _capture_from_wav(synthesis.audio, cycle)
        started = self._clock()
        try:
            result = self._transcriber.transcribe(
                TranscriptionRequest(capture=capture, language="zh")
            )
        except Exception:
            raise _BenchmarkFailure("stt_failed") from None
        elapsed = round(self._clock() - started, 6)
        # TranscriptionResult validation already proves non-blank text; the
        # benchmark deliberately records neither that text nor a digest of it.
        del result
        return _TranscriptionMetrics(elapsed_seconds=elapsed)

    def close(self) -> None:
        """Release local HTTP and managed-worker owners independently."""

        failed = False
        lease, self._lease = self._lease, None
        runtime, self._runtime = self._runtime, None
        if lease is not None:
            try:
                lease.close()
            except BaseException:
                failed = True
        if runtime is not None:
            try:
                runtime.shutdown()
            except BaseException:
                failed = True
        try:
            self._ollama.close()
        except BaseException:
            failed = True
        if failed:
            raise _BenchmarkFailure("cleanup_failed")


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Return a deterministic nearest-rank percentile for a small sample."""

    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    rank = max(1, round(percentile * len(ordered) + 0.499999))
    return round(ordered[min(rank - 1, len(ordered) - 1)], 6)


def _timing_summary(values: Sequence[float]) -> dict[str, object]:
    """Summarize bounded timings without exposing test content."""

    if not values:
        raise ValueError("values must not be empty")
    return {
        "count": len(values),
        "min_seconds": round(min(values), 6),
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": round(max(values), 6),
    }


def _measurement_document(
    cycle: int,
    ollama: _OllamaMetrics,
    synthesis: _SynthesisMetrics,
    transcription: _TranscriptionMetrics,
) -> dict[str, object]:
    """Build one text-free, audio-free per-cycle measurement object."""

    return {
        "cycle": cycle,
        "ollama": {
            "ttft_seconds": ollama.ttft_seconds,
            "elapsed_seconds": ollama.elapsed_seconds,
            "server_total_seconds": ollama.server_total_seconds,
            "load_seconds": ollama.load_seconds,
            "prompt_eval_seconds": ollama.prompt_eval_seconds,
            "eval_seconds": ollama.eval_seconds,
            "prompt_tokens": ollama.prompt_tokens,
            "output_tokens": ollama.output_tokens,
        },
        "tts": {
            "elapsed_seconds": synthesis.elapsed_seconds,
            "audio_seconds": synthesis.audio_seconds,
        },
        "stt": {
            "elapsed_seconds": transcription.elapsed_seconds,
            "device": "cpu",
        },
    }


def _run_benchmark(
    config: _BenchmarkConfig,
    dependencies: _BenchmarkDependencies,
) -> dict[str, object]:
    """Run one cleanup-safe, multi-cycle co-residency transaction."""

    if dependencies.platform_name != "nt":
        raise _BenchmarkFailure("platform_unsupported")
    monitor = dependencies.monitor_factory(config.sample_interval_seconds)
    pipeline = dependencies.pipeline_factory()
    monitor_started = False
    gpu_summary: dict[str, object] | None = None
    measurements: list[dict[str, object]] = []
    ollama_metrics: list[_OllamaMetrics] = []
    synthesis_metrics: list[_SynthesisMetrics] = []
    transcription_metrics: list[_TranscriptionMetrics] = []
    residency_values: list[int] = []
    startup_seconds = 0.0
    primary_error: BaseException | None = None
    try:
        monitor.start()
        monitor_started = True
        startup_seconds = pipeline.prepare()
        # One executor is retained across cycles so thread creation does not
        # contaminate per-cycle latency. Production intentionally overlaps only
        # Ollama and TTS; STT follows under the Backend's existing mutual
        # exclusion policy while both GPU models remain resident.
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="elysia-voice-benchmark",
        ) as executor:
            for cycle in range(1, config.cycles + 1):
                ollama_future = executor.submit(pipeline.stream_ollama)
                synthesis_future = executor.submit(pipeline.synthesize, cycle)
                ollama = ollama_future.result()
                synthesis = synthesis_future.result()
                residency_values.append(pipeline.ollama_size_vram_bytes())
                transcription = pipeline.transcribe(synthesis, cycle)
                measurements.append(
                    _measurement_document(
                        cycle,
                        ollama,
                        synthesis,
                        transcription,
                    )
                )
                ollama_metrics.append(ollama)
                synthesis_metrics.append(synthesis)
                transcription_metrics.append(transcription)
                # Do not retain generated WAV bytes beyond their measured cycle.
                synthesis_metrics[-1] = replace(synthesis, audio=b"")
                del synthesis, synthesis_future
    except BaseException as error:
        primary_error = error

    cleanup_started = dependencies.clock()
    cleanup_failed = False
    try:
        pipeline.close()
    except BaseException:
        cleanup_failed = True
    if monitor_started:
        try:
            gpu_summary = monitor.stop()
        except BaseException:
            cleanup_failed = True
    cleanup_seconds = round(dependencies.clock() - cleanup_started, 6)

    if primary_error is not None:
        raise primary_error
    if cleanup_failed or gpu_summary is None:
        raise _BenchmarkFailure("cleanup_failed")
    if not (
        measurements
        and len(ollama_metrics) == config.cycles
        and len(synthesis_metrics) == config.cycles
        and len(transcription_metrics) == config.cycles
        and len(residency_values) == config.cycles
    ):
        raise _BenchmarkFailure("internal_error")

    return {
        "schema_version": 1,
        "platform": "windows",
        "cycles": config.cycles,
        "policy": {
            "stt_device": "cpu",
            "ollama_tts_overlap": True,
            "stt_after_gpu_inference": True,
            "gpu_sampling_scope": "global",
            "audio_persisted": False,
            "text_persisted": False,
        },
        "gpu": gpu_summary,
        "ollama": {
            "size_vram_bytes": max(residency_values),
            "ttft": _timing_summary(
                [item.ttft_seconds for item in ollama_metrics]
            ),
            "elapsed": _timing_summary(
                [item.elapsed_seconds for item in ollama_metrics]
            ),
            "server_total": _timing_summary(
                [item.server_total_seconds for item in ollama_metrics]
            ),
        },
        "tts": {
            "startup_seconds": round(startup_seconds, 6),
            "elapsed": _timing_summary(
                [item.elapsed_seconds for item in synthesis_metrics]
            ),
        },
        "stt": {
            "device": "cpu",
            "elapsed": _timing_summary(
                [item.elapsed_seconds for item in transcription_metrics]
            ),
        },
        "cleanup_seconds": cleanup_seconds,
        "measurements": measurements,
    }


def _production_dependencies(config: _BenchmarkConfig) -> _BenchmarkDependencies:
    """Compose real Windows adapters after CLI validation succeeds."""

    return _BenchmarkDependencies(
        platform_name=os.name,
        monitor_factory=lambda interval: _NvidiaSmiMonitor(interval),
        pipeline_factory=lambda: _ProductionVoicePipeline(
            config.request_timeout_seconds
        ),
    )


def _write_safe_error(code: str) -> None:
    """Write one closed JSON error code without exception or path details."""

    print(json.dumps({"error": code}, separators=(",", ":")), file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Windows benchmark and emit exactly one sanitized JSON result."""

    arguments = _build_parser().parse_args(argv)
    try:
        config = _BenchmarkConfig(
            cycles=arguments.cycles,
            sample_interval_seconds=arguments.sample_interval_ms / 1_000.0,
            request_timeout_seconds=float(arguments.request_timeout_seconds),
        )
        result = _run_benchmark(config, _production_dependencies(config))
    except _BenchmarkFailure as error:
        _write_safe_error(error.code)
        return 2
    except Exception:
        _write_safe_error("internal_error")
        return 1
    print(
        json.dumps(
            result,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
