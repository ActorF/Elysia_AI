"""Test the closed offline SoulX SVS runtime without loading private models."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
from typing import Iterator
import wave

import pytest

from scripts import song_svs_runtime


def _write_pcm_wave(path: Path, *, duration_seconds: float = 1.25) -> None:
    """Write one deterministic mono PCM fixture accepted by the worker."""

    sample_rate = 16_000
    frame_count = int(round(sample_rate * duration_seconds))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frame_count)


def _assert_failure_code(
    expected_code: str,
    callback: Callable[[], object],
) -> None:
    """Assert that one fail-closed boundary exposes only its stable code."""

    with pytest.raises(song_svs_runtime._SvsRuntimeFailure) as captured:
        callback()
    assert captured.value.code == expected_code


def _create_managed_job(runtime_root: Path, token: str) -> Path:
    """Create one UUID-scoped managed job directory for an orchestration test."""

    jobs_root = runtime_root / "jobs"
    jobs_root.mkdir(parents=True)
    job_root = jobs_root / token
    job_root.mkdir()
    _write_pcm_wave(job_root / "target_vocal.wav")
    (job_root / "lyrics.lrc").write_text(
        "[00:00.00]无瑕的歌谣\n",
        encoding="utf-8",
    )
    return job_root


def test_probe_wave_accepts_bounded_pcm_and_reports_duration(tmp_path: Path) -> None:
    """Read duration from a supported fixed-size PCM fixture without FFmpeg."""

    vocal = tmp_path / "vocal.wav"
    _write_pcm_wave(vocal, duration_seconds=1.5)

    assert song_svs_runtime._probe_wave(vocal, label="Test vocal") == pytest.approx(
        1.5
    )


def test_probe_wave_rejects_non_wave_and_hardlinked_input(tmp_path: Path) -> None:
    """Reject malformed or multiply linked input before a decoder sees it."""

    malformed = tmp_path / "malformed.wav"
    malformed.write_bytes(b"not a wave file")
    _assert_failure_code(
        "invalid_vocal",
        lambda: song_svs_runtime._probe_wave(malformed, label="Test vocal"),
    )

    vocal = tmp_path / "linked.wav"
    _write_pcm_wave(vocal)
    os.link(vocal, tmp_path / "second-name.wav")
    _assert_failure_code(
        "unsafe_path",
        lambda: song_svs_runtime._probe_wave(vocal, label="Test vocal"),
    )


def test_validate_job_root_accepts_only_direct_uuid_v4_children(tmp_path: Path) -> None:
    """Keep renderer paths within one direct private UUIDv4 job directory."""

    runtime_root = tmp_path / "runtime"
    jobs_root = runtime_root / "jobs"
    jobs_root.mkdir(parents=True)
    valid = jobs_root / "123e4567-e89b-42d3-a456-426614174000"
    valid.mkdir()

    job_root, token = song_svs_runtime._validate_job_root(valid, runtime_root)
    assert job_root == valid
    assert token == valid.name

    nested = valid / "123e4567-e89b-42d3-a456-426614174001"
    nested.mkdir()
    _assert_failure_code(
        "invalid_job",
        lambda: song_svs_runtime._validate_job_root(nested, runtime_root),
    )

    invalid = jobs_root / "not-a-managed-job"
    invalid.mkdir()
    _assert_failure_code(
        "invalid_job",
        lambda: song_svs_runtime._validate_job_root(invalid, runtime_root),
    )


def test_asset_verification_rejects_digest_or_size_changes(tmp_path: Path) -> None:
    """Pin every private asset by both reviewed length and SHA256 digest."""

    asset = tmp_path / "model.pt"
    asset.write_bytes(b"reviewed-model")
    spec = song_svs_runtime._AssetSpec(
        "model.pt",
        len(b"reviewed-model"),
        "80837ffc9506e991f5ca5ae6ba7a57947fb5606128d6a2574c13b9b010db1e23",
    )

    assert song_svs_runtime._require_asset(tmp_path, spec) == asset
    asset.write_bytes(b"modified-model")
    _assert_failure_code(
        "runtime_integrity_failed",
        lambda: song_svs_runtime._require_asset(tmp_path, spec),
    )


def test_offline_environment_has_no_inherited_proxy_or_network_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construct a minimal offline child environment instead of inheriting secrets."""

    runtime_root = tmp_path / "one" / "two" / "three" / "soulx"
    source_root = runtime_root / "source"
    monkeypatch.setenv("HTTPS_PROXY", "http://secret.invalid")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-a-child")

    environment = song_svs_runtime._offline_environment(
        runtime_root,
        source_root,
        job_token="123e4567-e89b-42d3-a456-426614174000",
    )

    assert "HTTPS_PROXY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] == "1"
    assert environment["NO_PROXY"] == "*"
    assert environment[song_svs_runtime._INTERNAL_JOB_TOKEN_ENV].endswith("4000")


def test_mandarin_hotword_wrapper_biases_every_funasr_generate_call() -> None:
    """Inject validated lyrics into vendor ASR without editing vendor source."""

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Model:
        def generate(self, *args: object, **kwargs: object) -> str:
            calls.append((args, kwargs))
            return "recognized"

    class _ZhModel:
        model = _Model()

    class _Transcriber:
        zh_model = _ZhModel()

    class _Pipeline:
        lyric_transcriber = _Transcriber()

    pipeline = _Pipeline()
    song_svs_runtime._install_mandarin_hotword(pipeline, "无 瑕 歌 谣")

    result = pipeline.lyric_transcriber.zh_model.model.generate(
        "segment.wav",
        output_timestamp=True,
        hotword="untrusted override",
    )

    assert result == "recognized"
    assert calls == [
        (
            ("segment.wav",),
            {"output_timestamp": True, "hotword": "无 瑕 歌 谣"},
        )
    ]


def test_quiet_child_uses_argv_no_shell_and_discards_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launch reviewed child argv without a shell or path-bearing diagnostics."""

    captured: dict[str, object] = {"lease": None}
    waits: list[float | None] = []
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = tmp_path / token
    job_root.mkdir()

    class _Process:
        """Model one successful session leader without launching a process."""

        pid = 4312

        def __init__(self, command: list[str], **kwargs: object) -> None:
            """Capture the closed launch contract."""

            captured["command"] = command
            captured.update(kwargs)

        def wait(self, timeout: float | None = None) -> int:
            """Return successful completion for both waits."""

            waits.append(timeout)
            return 0

    lease = song_svs_runtime._StageLease(
        version=1,
        job_token=token,
        stage="preprocess",
        stage_id="a" * 32,
        pid=_Process.pid,
        process_group_id=_Process.pid,
        session_id=_Process.pid,
        start_time_ticks=99,
    )

    @contextmanager
    def _lock(_job_root: Path) -> Iterator[None]:
        """Replace the Linux advisory lock in this cross-platform unit test."""

        yield

    monkeypatch.setattr(song_svs_runtime.subprocess, "Popen", _Process)
    monkeypatch.setattr(song_svs_runtime, "_stage_control_lock", _lock)
    monkeypatch.setattr(song_svs_runtime, "_raise_if_cancelled", lambda _root: None)
    monkeypatch.setattr(
        song_svs_runtime,
        "_capture_stage_lease",
        lambda *_args, **_kwargs: lease,
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_write_stage_lease",
        lambda _root, value: captured.__setitem__("lease", value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_stage_lease",
        lambda _root: captured["lease"],
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_terminate_stage_session",
        lambda value: captured.__setitem__("terminated", value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_clear_stage_lease",
        lambda _root, _stage_id: captured.__setitem__("lease", None),
    )
    command = ["/fixed/python", "-B", "-c", "pass"]

    result = song_svs_runtime._run_quiet_process(
        command,
        job_root=job_root,
        job_token=token,
        stage="preprocess",
        cwd=tmp_path,
        environment={"SAFE": "1"},
        timeout_seconds=9,
    )

    assert result == 0
    assert captured["command"] == command
    assert captured["cwd"] == str(tmp_path)
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["SAFE"] == "1"
    assert environment[song_svs_runtime._INTERNAL_JOB_TOKEN_ENV] == token
    assert song_svs_runtime._STAGE_ID_PATTERN.fullmatch(
        environment[song_svs_runtime._INTERNAL_STAGE_ID_ENV]
    )
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["shell"] is False
    assert captured["start_new_session"] is True
    assert captured["terminated"] == lease
    assert captured["lease"] is None


def test_cleanup_terminates_registered_session_before_clearing_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind cleanup to one UUID lease and prevent any subsequent stage launch."""

    runtime_root = tmp_path / "runtime"
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = _create_managed_job(runtime_root, token)
    lease = song_svs_runtime._StageLease(
        version=song_svs_runtime._STAGE_LEASE_VERSION,
        job_token=token,
        stage="inference",
        stage_id="b" * 32,
        pid=8123,
        process_group_id=8123,
        session_id=8123,
        start_time_ticks=444,
    )
    song_svs_runtime._write_stage_lease(job_root, lease)
    terminated: list[song_svs_runtime._StageLease] = []

    @contextmanager
    def _lock(_job_root: Path) -> Iterator[None]:
        """Replace Linux flock while preserving cleanup ordering."""

        yield

    monkeypatch.setattr(song_svs_runtime, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(song_svs_runtime, "_stage_control_lock", _lock)
    monkeypatch.setattr(
        song_svs_runtime,
        "_terminate_stage_session",
        lambda value: terminated.append(value),
    )
    monkeypatch.setattr(song_svs_runtime, "_stage_session_members", lambda _value: [])

    song_svs_runtime._terminate_job_processes_for_cleanup(job_root)

    assert terminated == [lease]
    assert not (job_root / song_svs_runtime._STAGE_LEASE_NAME).exists()
    assert (job_root / song_svs_runtime._CANCEL_REQUEST_NAME).is_file()
    _assert_failure_code(
        "cancelled",
        lambda: song_svs_runtime._raise_if_cancelled(job_root),
    )


def test_stage_timeout_terminates_exact_lease_before_reporting_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalate a timed-out stage through its recorded session identity."""

    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = tmp_path / token
    job_root.mkdir()
    lease = song_svs_runtime._StageLease(
        version=1,
        job_token=token,
        stage="inference",
        stage_id="c" * 32,
        pid=9224,
        process_group_id=9224,
        session_id=9224,
        start_time_ticks=555,
    )
    state: dict[str, song_svs_runtime._StageLease | None] = {"lease": None}
    terminated: list[song_svs_runtime._StageLease] = []

    class _TimedOutProcess:
        """Model a child that exceeds its stage budget and is then reaped."""

        pid = lease.pid

        def __init__(self, _command: list[str], **_kwargs: object) -> None:
            """Create the deterministic fake process."""

            self.wait_count = 0

        def wait(self, timeout: float | None = None) -> int:
            """Time out once, then report signal termination during reap."""

            self.wait_count += 1
            if self.wait_count == 1:
                raise subprocess.TimeoutExpired(
                    "stage",
                    0.0 if timeout is None else timeout,
                )
            return -9

    @contextmanager
    def _lock(_job_root: Path) -> Iterator[None]:
        """Replace Linux flock for a deterministic timeout test."""

        yield

    monkeypatch.setattr(song_svs_runtime.subprocess, "Popen", _TimedOutProcess)
    monkeypatch.setattr(song_svs_runtime, "_stage_control_lock", _lock)
    monkeypatch.setattr(song_svs_runtime, "_raise_if_cancelled", lambda _root: None)
    monkeypatch.setattr(
        song_svs_runtime,
        "_capture_stage_lease",
        lambda *_args, **_kwargs: lease,
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_write_stage_lease",
        lambda _root, value: state.__setitem__("lease", value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_stage_lease",
        lambda _root: state["lease"],
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_terminate_stage_session",
        lambda value: terminated.append(value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_clear_stage_lease",
        lambda _root, _stage_id: state.__setitem__("lease", None),
    )

    _assert_failure_code(
        "stage_timeout",
        lambda: song_svs_runtime._run_quiet_process(
            ["/fixed/python", "-c", "pass"],
            job_root=job_root,
            job_token=token,
            stage="inference",
            cwd=tmp_path,
            environment={},
            timeout_seconds=7,
        ),
    )

    assert terminated == [lease]
    assert state["lease"] is None


def test_shared_runtime_budget_caps_each_stage_and_expires_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep stage limits subordinate to one documented WSL wall-clock budget."""

    now = 500.0
    monkeypatch.setattr(song_svs_runtime.time, "monotonic", lambda: now)

    assert song_svs_runtime._bounded_stage_timeout(
        now + 120,
        300,
    ) == 120
    assert song_svs_runtime._bounded_stage_timeout(
        now + 300,
        120,
    ) == 120
    _assert_failure_code(
        "stage_timeout",
        lambda: song_svs_runtime._bounded_stage_timeout(now, 120),
    )
    assert (
        song_svs_runtime._PREPROCESS_TIMEOUT_SECONDS
        + song_svs_runtime._INFERENCE_TIMEOUT_SECONDS
        == song_svs_runtime._TOTAL_RUNTIME_TIMEOUT_SECONDS
    )


def test_run_job_publishes_fixed_vocal_and_cleans_intermediates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orchestrate mocked prep and inference through the closed file contract."""

    runtime_root = tmp_path / "runtime"
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = _create_managed_job(runtime_root, token)
    source_root = runtime_root / "source"
    source_root.mkdir()
    events: list[tuple[str, int]] = []

    monkeypatch.setattr(song_svs_runtime, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(song_svs_runtime, "_verify_runtime_assets", lambda _root: None)
    monkeypatch.setattr(
        song_svs_runtime,
        "_verify_source_checkout",
        lambda _root: source_root,
    )

    def _preprocess(
        actual_token: str,
        _runtime_root: Path,
        _source_root: Path,
        *,
        timeout_seconds: int,
    ) -> None:
        assert actual_token == token
        assert 1 <= timeout_seconds <= song_svs_runtime._PREPROCESS_TIMEOUT_SECONDS
        corrected = job_root / ".svs-work" / "target.corrected.json"
        corrected.write_text('[{"text":"无 瑕"}]', encoding="utf-8")

    def _infer(
        actual_job: Path,
        _runtime_root: Path,
        _source_root: Path,
        *,
        timeout_seconds: int,
    ) -> Path:
        assert actual_job == job_root
        assert 1 <= timeout_seconds <= song_svs_runtime._INFERENCE_TIMEOUT_SECONDS
        generated = actual_job / ".svs-work" / "generated" / "generated.wav"
        generated.parent.mkdir()
        _write_pcm_wave(generated)
        return generated

    monkeypatch.setattr(song_svs_runtime, "_invoke_preprocess", _preprocess)
    monkeypatch.setattr(song_svs_runtime, "_invoke_inference", _infer)
    monkeypatch.setattr(
        song_svs_runtime,
        "_emit",
        lambda stage, progress, _message, **_kwargs: events.append((stage, progress)),
    )

    song_svs_runtime._run_job(str(job_root))

    output = job_root / "generated_vocal.wav"
    assert output.is_file()
    assert song_svs_runtime._probe_wave(output, label="Published vocal") == pytest.approx(
        1.25
    )
    assert not (job_root / ".svs-work").exists()
    assert events == [
        ("validating", 4),
        ("transcribing", 18),
        ("aligning", 52),
        ("synthesizing", 58),
        ("ready", 100),
    ]


def test_main_collapses_unexpected_errors_without_leaking_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hide vendor exception details behind one bounded structured error event."""

    secret = "/home/private/models/secret-model.pt"

    def _raise(_job_root: str) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(song_svs_runtime, "_run_job", _raise)

    assert song_svs_runtime.main(["/managed/job"]) == 1
    output = capsys.readouterr().out.strip()
    assert output.startswith(song_svs_runtime._EVENT_PREFIX)
    assert secret not in output
    payload = json.loads(output.removeprefix(song_svs_runtime._EVENT_PREFIX))
    assert payload["stage"] == "error"
    assert payload["errorCode"] == "runtime_failed"


def test_main_rejects_flags_and_missing_job_root(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose exactly one positional managed-root argument and no override flags."""

    assert song_svs_runtime.main([]) == 2
    first = capsys.readouterr().out
    assert '"errorCode":"invalid_request"' in first

    assert song_svs_runtime.main(["--model", "untrusted.pt"]) == 2
    second = capsys.readouterr().out
    assert '"errorCode":"invalid_request"' in second
