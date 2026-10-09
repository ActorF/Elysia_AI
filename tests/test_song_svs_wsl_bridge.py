"""Test the mounted-Windows to private-WSL singing-job bridge."""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scripts import song_svs_wsl_bridge as bridge


_TOKEN = "12345678-1234-4234-9234-123456789abc"


def _event(
    stage: str,
    progress: int,
    message: str,
    *,
    error_code: str | None = None,
) -> str:
    """Encode one canonical runtime protocol event for bridge tests."""

    payload: dict[str, object] = {
        "message": message,
        "progressPercent": progress,
        "stage": stage,
    }
    if error_code is not None:
        payload["errorCode"] = error_code
    return bridge._EVENT_PREFIX + json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _source_job(tmp_path: Path, *, plain_lyrics: bool = True) -> Path:
    """Create one mounted-job-shaped fixture with fixed allowlisted inputs."""

    mounted_root = tmp_path / "mounted"
    mounted_root.mkdir()
    job_root = mounted_root / _TOKEN
    job_root.mkdir()
    (job_root / "target_vocal.wav").write_bytes(b"fixed-vocal-input")
    (job_root / "lyrics.lrc").write_text("[00:00.00]我爱你", encoding="utf-8")
    if plain_lyrics:
        (job_root / "lyrics.txt").write_text("我爱你", encoding="utf-8")
    (job_root / "must-not-copy.secret").write_text("private", encoding="utf-8")
    return job_root


def _private_jobs(tmp_path: Path) -> Path:
    """Create one private-job-parent-shaped fixture."""

    jobs_root = tmp_path / "private" / "jobs"
    jobs_root.mkdir(parents=True)
    return jobs_root


def _patch_roots(
    monkeypatch: pytest.MonkeyPatch,
    source_job: Path,
    jobs_root: Path,
) -> None:
    """Route mount and private-root checks to isolated test directories."""

    monkeypatch.setattr(
        bridge,
        "_find_windows_mount",
        lambda candidate: source_job.parent if candidate == source_job else None,
    )
    monkeypatch.setattr(bridge, "_private_jobs_root", lambda: jobs_root)
    monkeypatch.setattr(
        bridge,
        "_terminate_private_job_processes",
        lambda _private_job: None,
    )


class _ReadyObserver(StringIO):
    """Assert that readiness is emitted only after the fixed output exists."""

    def __init__(self, output_path: Path) -> None:
        """Bind readiness observation to one mounted output path."""

        super().__init__()
        self._output_path = output_path
        self.ready_saw_output = False

    def write(self, value: str) -> int:
        """Record text and verify output visibility at the ready event."""

        if '"stage":"ready"' in value:
            self.ready_saw_output = self._output_path.is_file()
        return super().write(value)


def test_success_copies_only_fixed_inputs_cleans_private_job_and_delays_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish atomically after private cleanup without copying unrelated files."""

    source_job = _source_job(tmp_path)
    jobs_root = _private_jobs(tmp_path)
    _patch_roots(monkeypatch, source_job, jobs_root)
    inspected: dict[str, object] = {}

    def fake_runtime(arguments: list[str]) -> int:
        """Verify the private job and create one deterministic generated vocal."""

        private_job = Path(arguments[0])
        inspected["names"] = sorted(path.name for path in private_job.iterdir())
        inspected["private_job"] = private_job
        if os.name == "posix":
            inspected["job_mode"] = stat.S_IMODE(private_job.stat().st_mode)
            inspected["file_modes"] = {
                path.name: stat.S_IMODE(path.stat().st_mode)
                for path in private_job.iterdir()
            }
        print(_event("validating", 4, "Validating the private SoulX runtime."))
        (private_job / bridge._OUTPUT_NAME).write_bytes(b"generated-vocal")
        print(_event("ready", 100, "The Elysia vocal is ready for the reviewed mix stage."))
        return 0

    monkeypatch.setattr(bridge, "_RUNTIME_MODULE", SimpleNamespace(main=fake_runtime))
    destination = source_job / bridge._OUTPUT_NAME
    output = _ReadyObserver(destination)

    assert bridge._run_bridge(str(source_job), output) == 0
    assert destination.read_bytes() == b"generated-vocal"
    assert output.ready_saw_output is True
    assert output.getvalue().splitlines() == [
        _event("validating", 4, "Validating the private SoulX runtime."),
        _event("ready", 100, "The Elysia vocal is ready for the reviewed mix stage."),
    ]
    assert inspected["names"] == ["lyrics.lrc", "lyrics.txt", "target_vocal.wav"]
    assert not Path(str(inspected["private_job"])).exists()
    assert not (source_job / bridge._OUTPUT_TEMP_NAME).exists()
    if os.name == "posix":
        assert inspected["job_mode"] == 0o700
        file_modes = inspected["file_modes"]
        assert isinstance(file_modes, dict)
        assert set(file_modes.values()) == {0o600}


def test_optional_plain_lyrics_is_not_invented_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy no lyrics.txt file when the managed Windows job omits it."""

    source_job = _source_job(tmp_path, plain_lyrics=False)
    jobs_root = _private_jobs(tmp_path)
    _patch_roots(monkeypatch, source_job, jobs_root)

    def fake_runtime(arguments: list[str]) -> int:
        """Observe the allowlist and finish one successful fake runtime."""

        private_job = Path(arguments[0])
        assert sorted(path.name for path in private_job.iterdir()) == [
            "lyrics.lrc",
            "target_vocal.wav",
        ]
        (private_job / bridge._OUTPUT_NAME).write_bytes(b"generated")
        print(_event("ready", 100, "The Elysia vocal is ready for the reviewed mix stage."))
        return 0

    monkeypatch.setattr(bridge, "_RUNTIME_MODULE", SimpleNamespace(main=fake_runtime))

    assert bridge._run_bridge(str(source_job), StringIO()) == 0


def test_runtime_failure_is_relayed_and_private_job_is_always_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve a bounded runtime error while deleting copied private material."""

    source_job = _source_job(tmp_path)
    jobs_root = _private_jobs(tmp_path)
    _patch_roots(monkeypatch, source_job, jobs_root)

    def fake_runtime(_arguments: list[str]) -> int:
        """Return one correctly framed private-runtime failure."""

        print(
            _event(
                "error",
                0,
                "The synchronized lyrics could not be aligned safely.",
                error_code="lyrics_alignment_failed",
            )
        )
        return 1

    monkeypatch.setattr(bridge, "_RUNTIME_MODULE", SimpleNamespace(main=fake_runtime))
    output = StringIO()

    assert bridge._run_bridge(str(source_job), output) == 1
    assert not (jobs_root / _TOKEN).exists()
    assert not (source_job / bridge._OUTPUT_NAME).exists()
    assert output.getvalue().splitlines() == [
        _event(
            "error",
            0,
            "The synchronized lyrics could not be aligned safely.",
            error_code="lyrics_alignment_failed",
        )
    ]


def test_invalid_runtime_output_cannot_leak_a_private_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Collapse path-bearing runtime output to one safe bridge error."""

    source_job = _source_job(tmp_path)
    jobs_root = _private_jobs(tmp_path)
    _patch_roots(monkeypatch, source_job, jobs_root)
    secret = "D:\\Users\\Actor\\private.wav"

    def fake_runtime(_arguments: list[str]) -> int:
        """Attempt to place a private native path inside an otherwise valid event."""

        print(_event("validating", 4, secret))
        return 0

    monkeypatch.setattr(bridge, "_RUNTIME_MODULE", SimpleNamespace(main=fake_runtime))

    assert bridge.main([str(source_job)]) == 1
    captured = capsys.readouterr().out
    assert secret not in captured
    assert str(source_job) not in captured
    lines = captured.splitlines()
    assert len(lines) == 1 and lines[0].startswith(bridge._EVENT_PREFIX)
    assert json.loads(lines[0][len(bridge._EVENT_PREFIX) :]) == {
        "errorCode": "invalid_protocol",
        "message": "The private singing runtime emitted invalid output.",
        "progressPercent": 0,
        "stage": "error",
    }
    assert not (jobs_root / _TOKEN).exists()


def test_protocol_relay_holds_ready_and_rejects_partial_or_post_terminal_data() -> None:
    """Enforce complete bounded events and exactly one terminal state."""

    destination = StringIO()
    relay = bridge._ProtocolRelay(destination)
    progress = _event("validating", 4, "Validating the private SoulX runtime.")
    ready = _event("ready", 100, "The Elysia vocal is ready for the reviewed mix stage.")
    relay.write(progress + "\n")
    relay.write(ready + "\n")

    assert destination.getvalue().splitlines() == [progress]
    with pytest.raises(bridge._BridgeFailure, match="invalid output"):
        relay.write(progress + "\n")

    partial = bridge._ProtocolRelay(StringIO())
    partial.write(progress)
    with pytest.raises(bridge._BridgeFailure, match="invalid output"):
        partial.finish()


def test_job_identity_mount_and_single_link_inputs_are_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject non-UUID jobs, unmounted jobs, and hard-linked managed inputs."""

    source_job = _source_job(tmp_path)
    monkeypatch.setattr(bridge, "_find_windows_mount", lambda candidate: candidate.parent)
    assert bridge._validate_mounted_source_job(str(source_job)).token == _TOKEN

    invalid_root = source_job.parent / "not-a-uuid"
    invalid_root.mkdir()
    (invalid_root / "target_vocal.wav").write_bytes(b"vocal")
    (invalid_root / "lyrics.lrc").write_bytes(b"lyrics")
    with pytest.raises(bridge._BridgeFailure) as invalid_uuid:
        bridge._validate_mounted_source_job(str(invalid_root))
    assert invalid_uuid.value.code == "invalid_job"

    monkeypatch.setattr(bridge, "_find_windows_mount", lambda _candidate: None)
    with pytest.raises(bridge._BridgeFailure) as unmounted:
        bridge._validate_mounted_source_job(str(source_job))
    assert unmounted.value.code == "invalid_job"

    monkeypatch.setattr(bridge, "_find_windows_mount", lambda candidate: candidate.parent)
    hard_link = source_job / "lyrics-copy.lrc"
    os.link(source_job / "lyrics.lrc", hard_link)
    with pytest.raises(bridge._BridgeFailure) as linked:
        bridge._validate_mounted_source_job(str(source_job))
    assert linked.value.code == "unsafe_path"


def test_private_job_collision_is_not_deleted_or_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave an unexpected existing UUID job untouched instead of merging data."""

    source_job = _source_job(tmp_path)
    jobs_root = _private_jobs(tmp_path)
    _patch_roots(monkeypatch, source_job, jobs_root)
    collision = jobs_root / _TOKEN
    collision.mkdir()
    marker = collision / "existing"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(bridge._BridgeFailure) as captured:
        bridge._run_bridge(str(source_job), StringIO())

    assert captured.value.code == "private_job_failed"
    assert marker.read_text(encoding="utf-8") == "keep"


def test_cleanup_mode_removes_only_the_exact_owned_uuid_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Recover one interrupted job without scanning or deleting its sibling."""

    jobs_root = _private_jobs(tmp_path)
    target = jobs_root / _TOKEN
    sibling = jobs_root / "87654321-4321-4321-8321-cba987654321"
    target.mkdir(mode=0o700)
    sibling.mkdir(mode=0o700)
    if os.name == "posix":
        target.chmod(0o700)
        sibling.chmod(0o700)
    (target / "private-lyrics.txt").write_text("秘密", encoding="utf-8")
    marker = sibling / "keep"
    marker.write_text("owned by another job", encoding="utf-8")
    terminated: list[Path] = []
    monkeypatch.setattr(bridge, "_private_jobs_root", lambda: jobs_root)
    monkeypatch.setattr(
        bridge,
        "_terminate_private_job_processes",
        lambda private_job: terminated.append(private_job),
    )

    assert bridge.main([bridge._CLEANUP_ARGUMENT, _TOKEN]) == 0

    assert terminated == [target]
    assert not target.exists()
    assert marker.read_text(encoding="utf-8") == "owned by another job"
    assert capsys.readouterr().out == bridge._CLEANUP_EVENT_LINE + "\n"


def test_cleanup_refuses_to_delete_job_when_session_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve private files when exact-session termination cannot be proven."""

    jobs_root = _private_jobs(tmp_path)
    target = jobs_root / _TOKEN
    target.mkdir(mode=0o700)
    marker = target / "private-lyrics.txt"
    marker.write_text("秘密", encoding="utf-8")

    def _reject_cleanup(_private_job: Path) -> None:
        """Model an authenticated session that remains alive after escalation."""

        raise RuntimeError("session still active")

    monkeypatch.setattr(
        bridge,
        "_RUNTIME_MODULE",
        SimpleNamespace(_terminate_job_processes_for_cleanup=_reject_cleanup),
    )

    with pytest.raises(bridge._BridgeFailure) as captured:
        bridge._remove_private_job(target, jobs_root)

    assert captured.value.code == "cleanup_failed"
    assert marker.read_text(encoding="utf-8") == "秘密"


def test_cleanup_mode_rejects_noncanonical_identity_without_deleting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never reinterpret an arbitrary path or partial token as a cleanup job."""

    jobs_root = _private_jobs(tmp_path)
    target = jobs_root / _TOKEN
    target.mkdir(mode=0o700)
    if os.name == "posix":
        target.chmod(0o700)
    marker = target / "keep"
    marker.write_text("private", encoding="utf-8")
    monkeypatch.setattr(bridge, "_private_jobs_root", lambda: jobs_root)

    assert bridge.main([bridge._CLEANUP_ARGUMENT, "../jobs"]) == 1

    assert marker.read_text(encoding="utf-8") == "private"
    payload = capsys.readouterr().out
    assert "../jobs" not in payload
    assert '"errorCode":"invalid_job"' in payload


def test_unsafe_existing_output_prevents_publish_but_not_private_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a hard-linked destination and remove staged and private outputs."""

    source_job = _source_job(tmp_path)
    jobs_root = _private_jobs(tmp_path)
    _patch_roots(monkeypatch, source_job, jobs_root)
    destination = source_job / bridge._OUTPUT_NAME
    destination.write_bytes(b"old")
    os.link(destination, source_job / "linked-old-output.wav")

    def fake_runtime(arguments: list[str]) -> int:
        """Produce a valid private output before mounted publication fails."""

        private_job = Path(arguments[0])
        (private_job / bridge._OUTPUT_NAME).write_bytes(b"new")
        print(_event("ready", 100, "The Elysia vocal is ready for the reviewed mix stage."))
        return 0

    monkeypatch.setattr(bridge, "_RUNTIME_MODULE", SimpleNamespace(main=fake_runtime))

    with pytest.raises(bridge._BridgeFailure) as captured:
        bridge._run_bridge(str(source_job), StringIO())

    assert captured.value.code == "publish_failed"
    assert destination.read_bytes() == b"old"
    assert not (jobs_root / _TOKEN).exists()
    assert not (source_job / bridge._OUTPUT_TEMP_NAME).exists()


def test_main_rejects_argument_count_with_only_bounded_protocol_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Accept exactly one path and never let argparse-style prose reach stdout."""

    assert bridge.main([]) == 2
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert len(lines) == 1 and lines[0].startswith(bridge._EVENT_PREFIX)
    assert json.loads(lines[0][len(bridge._EVENT_PREFIX) :]) == {
        "errorCode": "invalid_request",
        "message": "Exactly one mounted singing job is required.",
        "progressPercent": 0,
        "stage": "error",
    }
