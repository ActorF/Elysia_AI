"""Verify the opt-in Song Cover smoke CLI without loading private models."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Sequence
import wave

import pytest

from scripts import smoke_song_cover as smoke


def _write_inputs(root: Path) -> dict[str, Path]:
    """Create four harmless fixture inputs accepted by the read-only preflight."""

    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "vocals": root / "private-vocals.wav",
        "accompaniment": root / "private-accompaniment.wav",
        "lrc": root / "private-lyrics.lrc",
        "plain": root / "private-lyrics.txt",
    }
    paths["vocals"].write_bytes(b"fixture vocals")
    paths["accompaniment"].write_bytes(b"fixture accompaniment")
    paths["lrc"].write_text(
        "[00:00.00]\u4f60\n[00:01.00]\u597d\n",
        encoding="utf-8",
    )
    paths["plain"].write_text("\u4f60\u597d\n", encoding="utf-8")
    return paths


def _arguments(paths: dict[str, Path], *extra: str) -> list[str]:
    """Build one CLI request from explicit private fixture paths."""

    return [
        "--vocals",
        str(paths["vocals"]),
        "--accompaniment",
        str(paths["accompaniment"]),
        "--lyrics-lrc",
        str(paths["lrc"]),
        "--plain-lyrics",
        str(paths["plain"]),
        *extra,
    ]


def _probe_payload(*, mp3: bool = False, duration: float = 2.0) -> bytes:
    """Return one strict fake FFprobe document for an input or final preview."""

    return json.dumps(
        {
            "format": {
                "bit_rate": "320000" if mp3 else "705600",
                "duration": str(duration),
                "format_name": "mp3" if mp3 else "wav",
            },
            "streams": [
                {
                    "channels": 2 if mp3 else 1,
                    "bit_rate": "320000" if mp3 else "705600",
                    "codec_name": "mp3" if mp3 else "pcm_s16le",
                    "codec_type": "audio",
                    "duration": str(duration),
                    "sample_rate": "44100",
                }
            ],
            # The pinned Windows FFprobe emits an empty programs collection
            # even when show_entries requests only stream and format fields.
            "programs": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _worker_events(*, complete: bool = True) -> bytes:
    """Encode the production worker's closed progress sequence."""

    stages: Sequence[tuple[str, int]] = smoke._EXPECTED_WORKER_STAGES
    if not complete:
        stages = stages[:1]
    return b"".join(
        smoke._WORKER_EVENT_PREFIX
        + json.dumps(
            {
                "message": "bounded fixture progress",
                "progressPercent": progress,
                "stage": stage,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for stage, progress in stages
    )


def _write_valid_outputs(job_root: Path) -> None:
    """Replace private job inputs with one valid model-free WAV/MP3 pair."""

    for child in job_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    with wave.open(str(job_root / "elysia-cover.wav"), "wb") as destination:
        destination.setnchannels(2)
        destination.setsampwidth(2)
        destination.setframerate(44_100)
        destination.writeframes(b"\x00\x00\x00\x00" * (44_100 * 2))
    (job_root / "elysia-cover.mp3").write_bytes(b"ID3\x04fixture-mp3")


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    temporary_root: Path,
    *,
    worker_mode: str = "success",
    probe_wrong_format: bool = False,
) -> dict[str, Any]:
    """Replace every native process with a deterministic model-free dispatcher."""

    runtime = smoke._RuntimePaths(
        python=temporary_root / "runtime" / "python.exe",
        worker=smoke._PROJECT_ROOT / "scripts" / "song_svs_worker.py",
        ffmpeg=temporary_root / "runtime" / "ffmpeg.exe",
        ffprobe=temporary_root / "runtime" / "ffprobe.exe",
        demucs_site=temporary_root / "runtime" / "site-packages",
        torch_home=temporary_root / "runtime" / "torch",
    )
    smoke_root = temporary_root / "smoke-root"
    observed: dict[str, Any] = {
        "commands": [],
        "manifest": None,
        "runtime": runtime,
        "smoke_root": smoke_root,
    }

    monkeypatch.setattr(smoke, "_runtime_paths", lambda: runtime)
    monkeypatch.setattr(smoke, "_smoke_root", lambda: smoke_root)
    monkeypatch.setattr(
        smoke,
        "_verify_runtime_tools",
        lambda _runtime, *, for_run: observed.setdefault(
            "verified_for_run",
            for_run,
        ),
    )

    def fake_process(
        command: list[str],
        **options: object,
    ) -> smoke._ProcessResult:
        """Dispatch fixed FFprobe, production-worker, and cleanup commands."""

        observed["commands"].append((list(command), dict(options)))
        if command[0] == str(runtime.ffprobe):
            target = Path(command[-1])
            if target.name == "elysia-cover.mp3":
                return smoke._ProcessResult(0, _probe_payload(mp3=True))
            if probe_wrong_format:
                return smoke._ProcessResult(0, _probe_payload(mp3=True))
            return smoke._ProcessResult(0, _probe_payload())
        if command[:2] != [str(runtime.python), str(runtime.worker)]:
            raise AssertionError("Smoke launched an unreviewed command.")
        if command[2:] == ["--cleanup-private-job", command[-1]]:
            if worker_mode == "cleanup-failure":
                return smoke._ProcessResult(1, b"")
            return smoke._ProcessResult(0, smoke._CLEANUP_ACK)
        if worker_mode == "exception":
            raise RuntimeError(
                "SECRET lyric text at D:/private/artist/song.wav"
            )
        job_root = Path(command[command.index("--job-dir") + 1])
        manifest_path = job_root / "lyrics-manifest.json"
        observed["manifest"] = json.loads(manifest_path.read_text("utf-8"))
        observed["job_root"] = job_root
        if worker_mode == "failure":
            return smoke._ProcessResult(2, _worker_events(complete=False))
        _write_valid_outputs(job_root)
        if worker_mode == "input-changed":
            Path(command[command.index("--vocal-input") + 1]).write_bytes(
                b"mutated during synthesis"
            )
        if worker_mode == "invalid-output":
            with wave.open(str(job_root / "elysia-cover.wav"), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\x00\x00" * (44_100 * 2))
        return smoke._ProcessResult(0, _worker_events())

    monkeypatch.setattr(smoke, "_run_closed_process", fake_process)
    return observed


def test_default_mode_preflights_hashes_without_loading_private_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep the default path model-free while reporting reproducible input facts."""

    paths = _write_inputs(tmp_path / "private-inputs")
    observed = _install_fake_runtime(monkeypatch, tmp_path / "runtime-fixture")

    assert smoke.main(_arguments(paths)) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert report["status"] == "preflight"
    assert report["run_private_runtime"] is False
    assert report["inputs"]["vocals"]["sha256"] == hashlib.sha256(
        paths["vocals"].read_bytes()
    ).hexdigest()
    commands = observed["commands"]
    assert len(commands) == 2
    assert all(command[0][0] == str(observed["runtime"].ffprobe) for command in commands)
    assert not observed["smoke_root"].exists()


def test_real_smoke_uses_closed_worker_command_and_removes_successful_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invoke production worker options, verify cleanup, and delete output by default."""

    paths = _write_inputs(tmp_path / "private-inputs")
    observed = _install_fake_runtime(monkeypatch, tmp_path / "runtime-fixture")

    assert smoke.main(
        _arguments(
            paths,
            "--run-private-runtime",
            "--key-shift-semitones",
            "-1",
        )
    ) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert report["status"] == "complete"
    assert report["key_shift_semitones"] == -1
    assert report["private_cleanup"] == "confirmed"
    assert report["retained"] is False
    assert report["stages"] == [stage for stage, _ in smoke._EXPECTED_WORKER_STAGES]
    assert report["outputs"]["wav"]["codec"] == "pcm_s16le"
    assert report["outputs"]["mp3"]["codec"] == "mp3"
    assert observed["manifest"] == smoke._SMOKE_MANIFEST
    assert len(observed["manifest"]) == 4
    worker_commands = [
        command
        for command, _options in observed["commands"]
        if command[:2]
        == [str(observed["runtime"].python), str(observed["runtime"].worker)]
    ]
    assert len(worker_commands) == 2
    launch = worker_commands[0]
    assert launch[2:4] == ["--source-mode", "stems"]
    assert launch[2::2] == [
        "--source-mode",
        "--vocal-input",
        "--accompaniment-input",
        "--key-shift-semitones",
        "--job-dir",
        "--ffmpeg",
        "--ffprobe",
        "--demucs-site",
        "--torch-home",
        "--job-token",
    ]
    assert launch[launch.index("--key-shift-semitones") + 1] == "-1"
    assert "--input" not in launch
    assert worker_commands[1][2:] == ["--cleanup-private-job", report["job_id"]]
    assert not observed["smoke_root"].exists()


def test_keep_output_retains_only_validated_outputs_under_reported_uuid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep only the final pair when the caller explicitly requests retention."""

    paths = _write_inputs(tmp_path / "private-inputs")
    observed = _install_fake_runtime(monkeypatch, tmp_path / "runtime-fixture")

    assert smoke.main(
        _arguments(paths, "--run-private-runtime", "--keep-output")
    ) == 0

    report = json.loads(capsys.readouterr().out)
    retained = observed["smoke_root"] / report["job_id"]
    assert report["retained"] is True
    assert {entry.name for entry in retained.iterdir()} == {
        "elysia-cover.mp3",
        "elysia-cover.wav",
    }


@pytest.mark.parametrize(
    ("worker_mode", "expected_error"),
    [
        ("failure", "worker_failed"),
        ("cleanup-failure", "private_cleanup_failed"),
        ("input-changed", "input_changed"),
        ("invalid-output", "invalid_output"),
    ],
)
def test_failures_run_cleanup_and_remove_local_private_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    worker_mode: str,
    expected_error: str,
) -> None:
    """Fail closed after cleanup and leave no lyrics or generated local output."""

    paths = _write_inputs(tmp_path / "private-inputs")
    observed = _install_fake_runtime(
        monkeypatch,
        tmp_path / "runtime-fixture",
        worker_mode=worker_mode,
    )

    assert smoke.main(_arguments(paths, "--run-private-runtime")) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": expected_error}
    assert not observed["smoke_root"].exists()
    worker_commands = [
        command
        for command, _options in observed["commands"]
        if command and command[0] == str(observed["runtime"].python)
    ]
    assert any("--cleanup-private-job" in command for command in worker_commands)


def test_audio_container_mismatch_is_rejected_before_job_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject an extension/container mismatch before copying any private lyric."""

    paths = _write_inputs(tmp_path / "private-inputs")
    observed = _install_fake_runtime(
        monkeypatch,
        tmp_path / "runtime-fixture",
        probe_wrong_format=True,
    )

    assert smoke.main(_arguments(paths)) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "invalid_input"}
    assert not observed["smoke_root"].exists()


def test_unexpected_private_diagnostic_is_collapsed_and_cleanup_still_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never print a lyric, native path, command, or unexpected exception detail."""

    paths = _write_inputs(tmp_path / "private-inputs")
    observed = _install_fake_runtime(
        monkeypatch,
        tmp_path / "runtime-fixture",
        worker_mode="exception",
    )

    assert smoke.main(_arguments(paths, "--run-private-runtime")) == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert json.loads(captured.err) == {"error": "internal_error"}
    assert "SECRET" not in combined
    assert "private-vocals" not in combined
    assert "\u4f60\u597d" not in combined
    assert not observed["smoke_root"].exists()
    assert any(
        "--cleanup-private-job" in command
        for command, _options in observed["commands"]
    )


def test_keep_output_requires_explicit_private_runtime_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse a retention request that did not authorize model execution."""

    paths = _write_inputs(tmp_path / "private-inputs")
    _install_fake_runtime(monkeypatch, tmp_path / "runtime-fixture")

    assert smoke.main(_arguments(paths, "--keep-output")) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "invalid_request"}


def test_help_is_path_free_and_does_not_report_an_internal_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preserve ordinary CLI help without entering preflight or printing paths."""

    assert smoke.main(["--help"]) == 0

    captured = capsys.readouterr()
    assert "--run-private-runtime" in captured.out
    assert captured.err == ""
    assert "internal_error" not in captured.out
