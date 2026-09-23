"""Test the privacy-preserving three-component voice benchmark."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event, Lock
from typing import cast
import wave

import pytest
from requests import Response, Session

from scripts import benchmark_voice_pipeline as benchmark


def _wav_bytes(sample_rate: int = 32_000, sample_count: int = 6_400) -> bytes:
    """Create deterministic non-silent mono PCM entirely in memory."""

    output = BytesIO()
    samples = b"".join(
        int(1_000 if index % 8 < 4 else -1_000).to_bytes(
            2,
            "little",
            signed=True,
        )
        for index in range(sample_count)
    )
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples)
    return output.getvalue()


class _FakeMonitor:
    """Record lifecycle calls and return deterministic global GPU metadata."""

    def __init__(self) -> None:
        """Begin as a sampler with no lifecycle calls."""

        self.started = False
        self.stopped = False

    def start(self) -> None:
        """Record baseline sampling admission."""

        self.started = True

    def stop(self) -> dict[str, object]:
        """Record cleanup and return one safe global aggregate."""

        self.stopped = True
        return {
            "scope": "global",
            "sample_count": 4,
            "devices": [
                {
                    "index": 0,
                    "name": "Test GPU",
                    "total_mib": 12_000,
                    "baseline_used_mib": 1_000,
                    "peak_used_mib": 8_000,
                    "final_used_mib": 1_100,
                    "peak_utilization_percent": 90,
                }
            ],
        }


class _FakePipeline:
    """Exercise real benchmark scheduling without local model dependencies."""

    def __init__(self, cycles: int) -> None:
        """Create one two-party barrier per expected overlap cycle."""

        self._barriers = [Barrier(2) for _ in range(cycles)]
        self._ollama_cycle = 0
        self._tts_cycle = 0
        self._lock = Lock()
        self.closed = False
        self.transcribed_cycles: list[int] = []

    def prepare(self) -> float:
        """Return a deterministic managed-runtime cold-start duration."""

        return 2.5

    def stream_ollama(self) -> benchmark._OllamaMetrics:
        """Meet TTS at a barrier to prove the operations overlap."""

        with self._lock:
            cycle = self._ollama_cycle
            self._ollama_cycle += 1
        self._barriers[cycle].wait(timeout=2.0)
        return benchmark._OllamaMetrics(
            ttft_seconds=0.1 + cycle,
            elapsed_seconds=0.5 + cycle,
            server_total_seconds=0.4 + cycle,
            load_seconds=0.01,
            prompt_eval_seconds=0.02,
            eval_seconds=0.3,
            prompt_tokens=12,
            output_tokens=8,
        )

    def synthesize(self, cycle: int) -> benchmark._SynthesisMetrics:
        """Meet Ollama at a barrier and return private in-memory bytes."""

        with self._lock:
            barrier_index = self._tts_cycle
            self._tts_cycle += 1
        self._barriers[barrier_index].wait(timeout=2.0)
        return benchmark._SynthesisMetrics(
            audio=b"private-audio-not-for-json",
            elapsed_seconds=0.7 + cycle,
            audio_seconds=1.25,
        )

    def ollama_size_vram_bytes(self) -> int:
        """Return deterministic model-specific residency."""

        return 6_000_000_000

    def transcribe(
        self,
        synthesis: benchmark._SynthesisMetrics,
        cycle: int,
    ) -> benchmark._TranscriptionMetrics:
        """Assert audio remains in memory and return text-free timing."""

        assert synthesis.audio == b"private-audio-not-for-json"
        self.transcribed_cycles.append(cycle)
        return benchmark._TranscriptionMetrics(elapsed_seconds=0.2 + cycle)

    def close(self) -> None:
        """Record deterministic pipeline cleanup."""

        self.closed = True


class _FailingPipeline(_FakePipeline):
    """Fail during Ollama streaming to verify transaction cleanup."""

    def stream_ollama(self) -> benchmark._OllamaMetrics:
        """Let concurrent TTS start, then raise one closed failure."""

        with self._lock:
            cycle = self._ollama_cycle
            self._ollama_cycle += 1
        self._barriers[cycle].wait(timeout=2.0)
        raise benchmark._BenchmarkFailure("ollama_unavailable")


class _FakeResponse:
    """Provide the minimal requests response surface used by the client."""

    def __init__(
        self,
        *,
        lines: tuple[bytes, ...] = (),
        chunks: tuple[bytes, ...] | None = None,
        document: object = None,
        status_code: int = 200,
    ) -> None:
        """Store deterministic decoded chunks, NDJSON lines, or one document."""

        self._lines = lines
        self._chunks = chunks
        self._document = document
        self.status_code = status_code

    def __enter__(self) -> "_FakeResponse":
        """Return this context-managed fake response."""

        return self

    def __exit__(self, *_ignored: object) -> None:
        """Model requests response cleanup without external resources."""

    def iter_lines(self) -> tuple[bytes, ...]:
        """Return configured streaming frames."""

        return self._lines

    def iter_content(self, *, chunk_size: int) -> tuple[bytes, ...]:
        """Return fixed decoded chunks matching Requests' streaming surface."""

        assert chunk_size == 65_536
        if self._chunks is not None:
            return self._chunks
        if self._lines:
            return tuple(line + b"\n" for line in self._lines)
        return (json.dumps(self._document).encode("utf-8"),)

    def json(self) -> object:
        """Return the configured decoded document."""

        return self._document

    def close(self) -> None:
        """Allow the wall-deadline timer to close a fake response safely."""


class _FakeSession:
    """Route fixed chat and process-list calls to fake responses."""

    def __init__(
        self,
        stream_response: _FakeResponse,
        process_response: _FakeResponse,
    ) -> None:
        """Store the two endpoint responses and lifecycle state."""

        self._stream_response = stream_response
        self._process_response = process_response
        self.closed = False
        self.post_kwargs: dict[str, object] | None = None
        self.get_kwargs: dict[str, object] | None = None

    def post(self, *_args: object, **kwargs: object) -> _FakeResponse:
        """Return the fixed Ollama stream response."""

        self.post_kwargs = kwargs
        return self._stream_response

    def get(self, *_args: object, **kwargs: object) -> _FakeResponse:
        """Return the fixed Ollama process response."""

        self.get_kwargs = kwargs
        return self._process_response

    def close(self) -> None:
        """Record session cleanup."""

        self.closed = True


def _dependencies(
    monitor: _FakeMonitor,
    pipeline: _FakePipeline,
) -> benchmark._BenchmarkDependencies:
    """Build deterministic Windows dependencies for core benchmark tests."""

    return benchmark._BenchmarkDependencies(
        platform_name="nt",
        monitor_factory=lambda _interval: monitor,
        pipeline_factory=lambda: pipeline,
    )


def _config(cycles: int) -> benchmark._BenchmarkConfig:
    """Build one valid test configuration."""

    return benchmark._BenchmarkConfig(
        cycles=cycles,
        sample_interval_seconds=0.25,
        request_timeout_seconds=30.0,
    )


def test_nvidia_csv_parser_accepts_multiple_global_devices() -> None:
    """Preserve every GPU while parsing only the four fixed query fields."""

    samples = benchmark._parse_nvidia_csv(
        "NVIDIA Test One, 12282, 2048, 75\nNVIDIA Test Two, 8192, 1024, 10\n"
    )

    assert samples == (
        benchmark._GpuDeviceSample("NVIDIA Test One", 12_282, 2_048, 75),
        benchmark._GpuDeviceSample("NVIDIA Test Two", 8_192, 1_024, 10),
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "GPU, 100, 101, 5\n",
        "GPU, 100, 10, 101\n",
        "GPU, not-a-number, 10, 5\n",
    ],
)
def test_nvidia_csv_parser_fails_closed(value: str) -> None:
    """Reject missing, inconsistent, or non-numeric global samples."""

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        benchmark._parse_nvidia_csv(value)

    assert raised.value.code == "gpu_sampler_unavailable"


def test_managed_wav_becomes_frame_aligned_cpu_capture() -> None:
    """Downsample 32 kHz WAV in memory to the exact VoiceCapture contract."""

    capture, duration = benchmark._capture_from_wav(_wav_bytes(), 3)

    assert capture.session_id == "voice_benchmark_3"
    assert capture.sample_rate_hz == 16_000
    assert capture.sample_count == 3_200
    assert len(capture.pcm_s16le) == 6_400
    assert duration == 0.2


def test_multicycle_core_overlaps_gpu_work_and_redacts_payloads() -> None:
    """Measure all cycles while excluding audio, prompts, transcripts, and paths."""

    monitor = _FakeMonitor()
    pipeline = _FakePipeline(cycles=3)

    result = benchmark._run_benchmark(
        _config(3),
        _dependencies(monitor, pipeline),
    )

    serialized = json.dumps(result, allow_nan=False)
    assert result["cycles"] == 3
    assert result["policy"] == {
        "stt_device": "cpu",
        "ollama_tts_overlap": True,
        "stt_after_gpu_inference": True,
        "gpu_sampling_scope": "global",
        "audio_persisted": False,
        "text_persisted": False,
    }
    assert cast(dict[str, object], result["ollama"])["size_vram_bytes"] == (
        6_000_000_000
    )
    assert pipeline.transcribed_cycles == [1, 2, 3]
    assert pipeline.closed is True
    assert monitor.started is True
    assert monitor.stopped is True
    assert "private-audio-not-for-json" not in serialized
    assert benchmark._BENCHMARK_PROMPT not in serialized
    assert benchmark._SYNTHESIS_TEXT not in serialized
    assert "transcript" not in serialized.casefold()
    assert "path" not in serialized.casefold()


def test_failure_still_closes_pipeline_and_gpu_sampler() -> None:
    """Clean every owner when either member of concurrent GPU work fails."""

    monitor = _FakeMonitor()
    pipeline = _FailingPipeline(cycles=1)

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        benchmark._run_benchmark(
            _config(1),
            _dependencies(monitor, pipeline),
        )

    assert raised.value.code == "ollama_unavailable"
    assert pipeline.closed is True
    assert monitor.stopped is True


def test_ollama_client_reads_stream_metrics_and_process_residency() -> None:
    """Use terminal stream fields and ``/api/ps`` without retaining reply text."""

    final_frame = {
        "message": {"content": "private generated reply"},
        "done": True,
        "total_duration": 900_000_000,
        "load_duration": 100_000_000,
        "prompt_eval_duration": 200_000_000,
        "eval_duration": 500_000_000,
        "prompt_eval_count": 20,
        "eval_count": 10,
    }
    fake_session = _FakeSession(
        _FakeResponse(lines=(json.dumps(final_frame).encode("utf-8"),)),
        _FakeResponse(
            document={
                "models": [
                    {"name": "local-model", "size_vram": 6_543_210_000}
                ]
            }
        ),
    )
    clock_values = iter(
        (10.0, 10.1, 10.2, 10.8, 11.0, 11.1, 11.2, 11.3, 11.4)
    )
    client = benchmark._OllamaBenchmarkClient(
        "http://127.0.0.1:11434",
        "local-model",
        30.0,
        clock=lambda: next(clock_values),
    )
    client._session.close()
    client._session = cast(Session, fake_session)

    metrics = client.stream()
    residency = client.size_vram_bytes()
    client.close()

    assert metrics == benchmark._OllamaMetrics(
        ttft_seconds=0.2,
        elapsed_seconds=0.8,
        server_total_seconds=0.9,
        load_seconds=0.1,
        prompt_eval_seconds=0.2,
        eval_seconds=0.5,
        prompt_tokens=20,
        output_tokens=10,
    )
    assert residency == 6_543_210_000
    assert fake_session.closed is True
    assert fake_session.post_kwargs is not None
    assert fake_session.post_kwargs["stream"] is True
    assert fake_session.post_kwargs["allow_redirects"] is False
    assert fake_session.get_kwargs is not None
    assert fake_session.get_kwargs["stream"] is True
    assert fake_session.get_kwargs["allow_redirects"] is False
    assert "private generated reply" not in repr(metrics)


def test_ollama_trickle_stream_obeys_total_wall_deadline() -> None:
    """Stop a stream whose individually timely frames exceed the total bound."""

    frames = (
        json.dumps({"message": {"content": "a"}, "done": False}).encode(),
        json.dumps({"message": {"content": "b"}, "done": False}).encode(),
    )
    fake_session = _FakeSession(
        _FakeResponse(lines=frames),
        _FakeResponse(document={"models": []}),
    )
    clock_values = iter((10.0, 10.1, 10.5, 11.1))
    client = benchmark._OllamaBenchmarkClient(
        "http://127.0.0.1:11434",
        "local-model",
        1.0,
        clock=lambda: next(clock_values),
    )
    client._session.close()
    client._session = cast(Session, fake_session)

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        client.stream()
    client.close()

    assert raised.value.code == "ollama_timeout"
    assert fake_session.closed is True


def test_ollama_stream_rejects_unterminated_oversized_frame() -> None:
    """Bound one line before Requests or JSON can retain an unlimited body."""

    oversized = b"x" * (benchmark._MAX_STREAM_FRAME_BYTES + 1)
    fake_session = _FakeSession(
        _FakeResponse(
            chunks=(
                oversized[:600_000],
                oversized[600_000:],
            )
        ),
        _FakeResponse(document={"models": []}),
    )
    client = benchmark._OllamaBenchmarkClient(
        "http://127.0.0.1:11434",
        "local-model",
        30.0,
    )
    client._session.close()
    client._session = cast(Session, fake_session)

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        client.stream()
    client.close()

    assert raised.value.code == "ollama_invalid_response"


def test_ollama_process_list_is_streamed_and_size_bounded() -> None:
    """Reject an oversized process list before decoding it as one JSON object."""

    fake_session = _FakeSession(
        _FakeResponse(lines=()),
        _FakeResponse(
            chunks=(
                b"x" * benchmark._MAX_PROCESS_RESPONSE_BYTES,
                b"x",
            )
        ),
    )
    client = benchmark._OllamaBenchmarkClient(
        "http://127.0.0.1:11434",
        "local-model",
        30.0,
    )
    client._session.close()
    client._session = cast(Session, fake_session)

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        client.size_vram_bytes()
    client.close()

    assert raised.value.code == "ollama_invalid_response"
    assert fake_session.get_kwargs is not None
    assert fake_session.get_kwargs["stream"] is True


def test_process_list_rechecks_deadline_after_normal_stream_end() -> None:
    """Reject timeout closure that appears as EOF after one legal JSON chunk."""

    expired = Event()
    clock_calls = 0

    def _clock() -> float:
        """Set the timer flag only after the final chunk has been consumed."""

        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 2:
            expired.set()
        return 10.0

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        benchmark._read_bounded_response_body(
            cast(Response, _FakeResponse(document={"models": []})),
            max_bytes=benchmark._MAX_PROCESS_RESPONSE_BYTES,
            expired=expired,
            deadline=11.0,
            clock=_clock,
        )

    assert raised.value.code == "ollama_timeout"
    assert clock_calls == 2


def test_process_list_includes_json_validation_in_wall_deadline() -> None:
    """Reject a valid residency document whose parsing crosses the deadline."""

    fake_session = _FakeSession(
        _FakeResponse(lines=()),
        _FakeResponse(
            document={
                "models": [
                    {"name": "local-model", "size_vram": 6_543_210_000}
                ]
            }
        ),
    )
    clock_values = iter((10.0, 10.1, 10.2, 10.3, 11.0))
    client = benchmark._OllamaBenchmarkClient(
        "http://127.0.0.1:11434",
        "local-model",
        1.0,
        clock=lambda: next(clock_values),
    )
    client._session.close()
    client._session = cast(Session, fake_session)

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        client.size_vram_bytes()
    client.close()

    assert raised.value.code == "ollama_timeout"
    assert fake_session.closed is True


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1:11434",
        "http://example.com:11434",
        "http://user:password@127.0.0.1:11434",
        "http://127.0.0.1:11434/private",
    ],
)
def test_ollama_client_rejects_non_loopback_or_credentialed_origins(
    origin: str,
) -> None:
    """Prevent the fixed benchmark prompt from leaving a plain local origin."""

    with pytest.raises(benchmark._BenchmarkFailure) as raised:
        benchmark._OllamaBenchmarkClient(origin, "model", 30.0)

    assert raised.value.code == "ollama_unavailable"


def test_main_emits_one_sanitized_json_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep successful CLI output machine-readable and payload-free."""

    monitor = _FakeMonitor()
    pipeline = _FakePipeline(cycles=1)
    monkeypatch.setattr(
        benchmark,
        "_production_dependencies",
        lambda _settings: _dependencies(monitor, pipeline),
    )

    assert benchmark.main(["--cycles", "1"]) == 0

    output = capsys.readouterr()
    document = json.loads(output.out)
    assert output.err == ""
    assert document["schema_version"] == 1
    assert "private-audio-not-for-json" not in output.out
    assert "D:\\" not in output.out


def test_script_help_works_outside_repository(tmp_path: Path) -> None:
    """Support direct CMD execution without relying on the current directory."""

    script_path = Path(benchmark.__file__).resolve()
    completed = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--cycles" in completed.stdout
    assert "--sample-interval-ms" in completed.stdout
    assert completed.stderr == ""
