"""Test the Windows source-SVC and lyrics-SVS orchestrator without loading audio models."""

from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import wave
from types import SimpleNamespace

import pytest

from scripts import song_svs_worker as worker


_TOKEN = "12345678-1234-4234-9234-123456789abc"


def _write_svc_wave(path: Path, frames: int, *, width: int = 3) -> None:
    """Create a complete canonical SVC output without loading audio libraries."""

    with wave.open(str(path), "wb") as output:
        output.setparams((1, width, 24_000, frames, "NONE", "not compressed"))
        output.writeframes(bytes(width * frames))


def test_source_svc_protocol_uses_only_conversion_stages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Map source inference to native conversion without lyric-only stages."""

    protocol = b"".join([
        _svs_event("validating", 4), _svs_event("synthesizing", 58),
        _svs_event("ready", 100),
    ])
    worker._relay_svs_protocol(BytesIO(protocol), engine="source-svc")
    output = capsys.readouterr().out
    assert '"stage":"converting"' in output
    assert '"progressPercent":55' in output
    assert '"stage":"transcribing"' not in output
    with pytest.raises(worker._SvsWorkerFailure):
        worker._relay_svs_protocol(BytesIO(_successful_protocol()), engine="source-svc")


def test_source_progress_is_monotonic_bounded_and_drops_private_text(capsys):
    """Relay repeated synthesis progress as fixed native conversion messages."""
    protocol = _svs_event("validating", 4)
    protocol += b"".join(_svs_event("synthesizing", value, "private words")
                         for value in [58, 59, 60, 61, 62, 64, 72, 88])
    protocol += _svs_event("ready", 100)
    assert worker._relay_svs_protocol(BytesIO(protocol), engine="source-svc").ready
    output = capsys.readouterr().out
    assert "private words" not in output
    records = [json.loads(line.removeprefix(worker._audio._EVENT_PREFIX)) for line in output.splitlines()]
    assert [record["progressPercent"] for record in records] == [55, 56, 57, 58, 59, 61, 70, 88]
    assert all(record["stage"] == "converting" for record in records)
    assert records[3]["message"] == "Checking the vocal pitch"


@pytest.mark.parametrize("values", [[59], [58, 58], [58, 70, 69], [58, 89]])
def test_source_progress_rejects_invalid_first_duplicate_regression_and_overflow(values):
    """Progress never weakens the existing ordered lifecycle or upper bound."""
    protocol = _svs_event("validating", 4)
    protocol += b"".join(_svs_event("synthesizing", value) for value in values)
    protocol += _svs_event("ready", 100)
    with pytest.raises(worker._SvsWorkerFailure):
        worker._relay_svs_protocol(BytesIO(protocol), engine="source-svc")


@pytest.mark.parametrize("payload", [
    '{"schema_version":1,"reference":"fairy","key_shift_semitones":12}',
    '{"schema_version":1,"reference":"elysia","key_shift_semitones":12}',
])
def test_source_svc_manifest_accepts_only_reviewed_references(tmp_path: Path, payload: str) -> None:
    """Admit fixed profile identities bound to the native launch key."""

    (tmp_path / "singing-request.json").write_text(payload, encoding="utf-8")
    worker._validate_svc_request(tmp_path, 12)


@pytest.mark.parametrize("payload", [
    '{"schema_version":true,"reference":"fairy","key_shift_semitones":12}',
    '{"schema_version":1,"reference":"fairy","key_shift_semitones":0}',
    '{"schema_version":1,"reference":"fairy","key_shift_semitones":true}',
    '{"schema_version":1,"reference":"fairy","key_shift_semitones":12,"cfg":3}',
    '{"schema_version":1,"reference":"fairy","reference":"elysia","key_shift_semitones":12}',
    '{"schema_version":1,"reference":"D:/private.wav","key_shift_semitones":12}',
])
def test_source_svc_manifest_rejects_ambiguous_or_unbound_requests(tmp_path: Path, payload: str) -> None:
    """Reject duplicate fields, custom profiles, and disagreement with native IPC."""

    (tmp_path / "singing-request.json").write_text(payload, encoding="utf-8")
    with pytest.raises(worker._SvsWorkerFailure):
        worker._validate_svc_request(tmp_path, 12)


def test_source_svc_output_requires_exact_pcm24_payload(tmp_path: Path) -> None:
    """Reject frame loss and truncated data before the shared mixer can pad it."""

    output = tmp_path / "output.wav"
    target_samples = 44_117
    expected = round(target_samples * 24_000 / 44_100)
    _write_svc_wave(output, expected)
    worker._require_svc_output_duration(output, target_samples=target_samples)
    for frames, width in [(expected - 1, 3), (expected + 1, 3), (expected, 2)]:
        _write_svc_wave(output, frames, width=width)
        with pytest.raises(worker._SvsWorkerFailure):
            worker._require_svc_output_duration(output, target_samples=target_samples)
    _write_svc_wave(output, expected)
    output.write_bytes(output.read_bytes()[:-3])
    with pytest.raises(worker._SvsWorkerFailure, match="incomplete"):
        worker._require_svc_output_duration(output, target_samples=target_samples)


def test_source_svc_rejects_simultaneous_lyrics_before_runtime(tmp_path: Path) -> None:
    """Keep mode selection exclusive before starting native or WSL tooling."""

    job_root = tmp_path / _TOKEN
    job_root.mkdir()
    (job_root / "singing-request.json").write_text("{}", encoding="utf-8")
    (job_root / "lyrics-manifest.json").write_text("{}", encoding="utf-8")
    arguments = _arguments(job_root)
    arguments.engine = "source-svc"
    with pytest.raises(worker._SvsWorkerFailure, match="unexpected entry"):
        worker._run_job(arguments)


def _svs_event(
    stage: str,
    progress: int,
    message: str = "bounded status",
    *,
    error_code: str | None = None,
) -> bytes:
    """Encode one private bridge event for protocol-boundary tests."""

    payload: dict[str, object] = {
        "message": message,
        "progressPercent": progress,
        "stage": stage,
    }
    if error_code is not None:
        payload["errorCode"] = error_code
    return (
        worker._SVS_EVENT_PREFIX
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _successful_protocol() -> bytes:
    """Return the exact private stage sequence expected from the WSL bridge."""

    return b"".join(
        [
            _svs_event("validating", 4),
            _svs_event("transcribing", 18),
            _svs_event("aligning", 52),
            _svs_event("synthesizing", 58),
            _svs_event("ready", 100),
        ]
    )


def _arguments(job_root: Path) -> argparse.Namespace:
    """Create one manager-shaped stems request for an isolated job fixture."""

    return argparse.Namespace(
        engine="lyrics-svs",
        source_mode="stems",
        input=None,
        vocal_input=job_root.parent / "vocal.wav",
        accompaniment_input=job_root.parent / "instrumental.wav",
        key_shift_semitones=0,
        shift_accompaniment=True,
        job_dir=job_root,
        ffmpeg=Path("ffmpeg.exe"),
        ffprobe=Path("ffprobe.exe"),
        demucs_site=Path("demucs"),
        torch_home=Path("torch"),
        job_token=_TOKEN,
    )


def test_parser_matches_the_electron_lyrics_svs_launch_contract() -> None:
    """Accept every Main-owned argument without exposing legacy SVC controls."""

    parsed = worker._parser().parse_args(
        [
            "--source-mode", "stems",
            "--vocal-input", "vocal.wav",
            "--accompaniment-input", "instrumental.wav",
            "--key-shift-semitones", "12",
            "--shift-accompaniment", "false",
            "--job-dir", "job",
            "--ffmpeg", "ffmpeg.exe",
            "--ffprobe", "ffprobe.exe",
            "--demucs-site", "demucs",
            "--torch-home", "torch",
            "--job-token", _TOKEN,
        ]
    )

    assert parsed.source_mode == "stems"
    assert parsed.key_shift_semitones == 12
    assert parsed.shift_accompaniment is False
    assert not hasattr(parsed, "svc_root")
    assert not hasattr(parsed, "svc_model_root")


@pytest.mark.parametrize("engine", ["lyrics-svs", "source-svc"])
@pytest.mark.parametrize("source_mode", ["song", "stems"])
@pytest.mark.parametrize("shift_accompaniment", [False, True])
def test_prepare_sources_shifts_only_lyrics_vocal_and_optional_accompaniment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_mode: str,
    shift_accompaniment: bool,
    engine: str,
) -> None:
    """Use F0-only transposition for source SVC while independently gating music."""

    job_root = tmp_path / _TOKEN
    job_root.mkdir()
    arguments = _arguments(job_root)
    arguments.source_mode = source_mode
    arguments.engine = engine
    arguments.input = tmp_path / "song.wav"
    arguments.key_shift_semitones = 4
    arguments.shift_accompaniment = shift_accompaniment
    transpose_calls: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        worker._audio,
        "_validate_source",
        lambda source, _ffprobe: (Path(source), 2.0),
    )
    monkeypatch.setattr(worker._audio, "_target_samples", lambda _duration: 88_200)

    def fake_decode(
        _ffmpeg: Path,
        _source: Path,
        _duration: float,
        output: Path,
    ) -> None:
        """Materialize each canonical decode requested by the source mode."""

        output.write_bytes(b"decoded")

    def fake_separate(
        _song: Path,
        separation_root: Path,
        _demucs_site: Path,
        _torch_home: Path,
    ) -> tuple[Path, Path]:
        """Create representative stems at the shared post-separation boundary."""

        separation_root.mkdir()
        vocals = separation_root / "vocals.wav"
        accompaniment = separation_root / "no_vocals.wav"
        vocals.write_bytes(b"vocals")
        accompaniment.write_bytes(b"accompaniment")
        return vocals, accompaniment

    def fake_prepare(
        _ffmpeg: Path,
        _source: Path,
        output: Path,
        **_options: object,
    ) -> None:
        """Materialize one mono vocal preparation without decoding audio."""

        output.write_bytes(b"prepared")

    def fake_transpose(
        _ffmpeg: Path,
        source: Path,
        output: Path,
        **_options: object,
    ) -> None:
        """Record which stream is transposed and create its expected output."""

        transpose_calls.append((source, output))
        output.write_bytes(b"shifted")

    monkeypatch.setattr(worker._audio, "_decode_song", fake_decode)
    monkeypatch.setattr(worker._audio, "_separate_vocals", fake_separate)
    monkeypatch.setattr(worker._audio, "_prepare_mono_vocals", fake_prepare)
    monkeypatch.setattr(worker._audio, "_transpose_audio", fake_transpose)

    result = worker._prepare_sources(
        arguments,
        job_root=job_root,
        ffmpeg=Path("ffmpeg.exe"),
        ffprobe=Path("ffprobe.exe"),
        demucs_site=Path("demucs") if source_mode == "song" else None,
        torch_home=Path("torch") if source_mode == "song" else None,
    )

    returned_accompaniment = result[3]
    scratch_files = result[5]
    vocal_calls = int(engine == "lyrics-svs")
    if vocal_calls:
        assert transpose_calls[0][0].name == "target-vocal-unshifted.wav"
    if shift_accompaniment:
        assert len(transpose_calls) == vocal_calls + 1
        assert transpose_calls[vocal_calls][0].name in {
            "accompaniment.wav",
            "no_vocals.wav",
        }
        assert returned_accompaniment.name == "accompaniment-key-shifted.wav"
        assert any(path == returned_accompaniment for path, _label in scratch_files)
    else:
        assert len(transpose_calls) == vocal_calls
        assert returned_accompaniment.name in {"accompaniment.wav", "no_vocals.wav"}
        assert all(
            path.name != "accompaniment-key-shifted.wav"
            for path, _label in scratch_files
        )


def test_path_translation_uses_absolute_wslpath_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep native path conversion on one exact executable/argument boundary."""

    observed: dict[str, object] = {}

    def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        """Capture the fixed conversion command and return one mounted path."""

        observed["command"] = command
        observed["options"] = options
        return subprocess.CompletedProcess(command, 0, b"/mnt/d/Elysia_AI\n", b"")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    wsl = Path(r"C:\Windows\System32\wsl.exe")

    assert worker._translate_windows_path(wsl, Path(r"D:\Elysia_AI")) == (
        "/mnt/d/Elysia_AI"
    )
    assert observed["command"] == [
        str(wsl),
        "--exec",
        "/usr/bin/wslpath",
        "-a",
        "-u",
        r"D:\Elysia_AI",
    ]
    options = observed["options"]
    assert isinstance(options, dict)
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL


def test_wsl_runtime_home_uses_fixed_pwd_query_and_reviewed_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the current uid home without a username, shell, or caller path."""

    observed: dict[str, object] = {}

    def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        """Return a POSIX home while capturing the closed lookup command."""

        observed["command"] = command
        observed["options"] = options
        return subprocess.CompletedProcess(command, 0, b"/home/current-user", b"")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    wsl = Path(r"C:\Windows\System32\wsl.exe")

    assert worker._resolve_wsl_prep_python(wsl) == (
        "/home/current-user/.local/share/elysia-ai/soulx/prep-env/bin/python"
    )
    assert observed["command"] == [
        str(wsl),
        "--exec",
        "/usr/bin/python3",
        "-I",
        "-c",
        worker._WSL_HOME_QUERY,
    ]
    options = observed["options"]
    assert isinstance(options, dict)
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize(
    "home",
    [b"relative/home", b"/home/user\n/escape", b"/home/../root", b"/home//user"],
)
def test_wsl_runtime_home_rejects_unsafe_pwd_results(
    home: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a malformed pwd home before it can select an interpreter."""

    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda command, **_options: subprocess.CompletedProcess(
            command,
            0,
            home,
            b"",
        ),
    )

    with pytest.raises(worker._SvsWorkerFailure, match="runtime location"):
        worker._resolve_wsl_prep_python(Path("wsl.exe"))


@pytest.mark.parametrize(
    "value",
    ["relative/path", "/mnt/d/../secret", "/mnt/d//job", "/mnt/d/job\nleak"],
)
def test_wsl_path_validation_rejects_noncanonical_output(value: str) -> None:
    """Refuse path traversal, control data, relative paths, and empty segments."""

    with pytest.raises(worker._SvsWorkerFailure, match="translated"):
        worker._validate_wsl_path(value)


def test_protocol_mapping_emits_only_the_manager_stage_sequence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Drop private messages while mapping only transcription/alignment/synthesis."""

    private_lyric = "不应出现在外层输出"
    payload = b"".join(
        [
            _svs_event("validating", 4, private_lyric),
            _svs_event("transcribing", 18, private_lyric),
            _svs_event("aligning", 52, private_lyric),
            _svs_event("synthesizing", 58, private_lyric),
            _svs_event("ready", 100, private_lyric),
        ]
    )

    outcome = worker._relay_svs_protocol(BytesIO(payload))
    output = capsys.readouterr().out

    assert outcome == worker._BridgeOutcome(True, None)
    assert private_lyric not in output
    records = [
        json.loads(line.removeprefix(worker._audio._EVENT_PREFIX))
        for line in output.splitlines()
    ]
    assert [(item["stage"], item["progressPercent"]) for item in records] == [
        ("transcribing", 35),
        ("aligning", 52),
        ("synthesizing", 58),
    ]


def test_protocol_error_is_terminal_and_never_forwarded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose only the failed alignment stage and its closed error code."""

    private_text = "歌词对齐失败但不能公开原文"
    outcome = worker._relay_svs_protocol(
        BytesIO(
            _svs_event("validating", 4)
            + _svs_event("transcribing", 18)
            + _svs_event(
                "error",
                0,
                private_text,
                error_code="lyrics_alignment_failed",
            )
        )
    )

    assert outcome == worker._BridgeOutcome(False, "lyrics_alignment_failed")
    records = [
        json.loads(line.removeprefix(worker._audio._EVENT_PREFIX))
        for line in capsys.readouterr().out.splitlines()
    ]
    assert [(item["stage"], item["progressPercent"]) for item in records] == [
        ("transcribing", 35),
        ("aligning", 52),
    ]
    assert private_text not in json.dumps(records, ensure_ascii=False)


@pytest.mark.parametrize(
    "payload",
    [
        _svs_event("transcribing", 18),
        _svs_event("validating", 4) + _svs_event("validating", 4),
        _svs_event("validating", 4).rstrip(b"\n"),
        b"unframed vendor output\n",
    ],
)
def test_protocol_rejects_missing_duplicate_partial_or_unframed_events(
    payload: bytes,
) -> None:
    """Fail closed instead of guessing when the private stage order changes."""

    with pytest.raises(worker._SvsWorkerFailure, match="invalid response"):
        worker._relay_svs_protocol(BytesIO(payload))


def test_wsl_bridge_uses_fixed_interpreter_module_and_no_lyric_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launch the reviewed module directly and keep lyric contents off argv."""

    observed: dict[str, object] = {}

    class FakeProcess:
        """Provide one successful bounded process for launch-contract inspection."""

        def __init__(self, command: list[str], **options: object) -> None:
            """Capture the child command and expose the successful protocol."""

            observed["command"] = command
            observed["options"] = options
            self.stdout = BytesIO(_successful_protocol())
            self.killed = False

        def wait(self) -> int:
            """Report successful bridge completion."""

            return 0

        def kill(self) -> None:
            """Record an unexpected protocol-abort request."""

            self.killed = True

    translations = {
        Path("project"): "/mnt/d/project",
        Path("job") / _TOKEN: f"/mnt/d/jobs/{_TOKEN}",
    }
    monkeypatch.setattr(
        worker,
        "_translate_windows_path",
        lambda _wsl, value: translations[value],
    )
    monkeypatch.setattr(
        worker,
        "_resolve_wsl_prep_python",
        lambda _wsl: (
            "/home/current-user/.local/share/elysia-ai/"
            "soulx/prep-env/bin/python"
        ),
    )
    monkeypatch.setattr(worker.subprocess, "Popen", FakeProcess)

    worker._run_wsl_bridge(
        Path(r"C:\Windows\System32\wsl.exe"),
        Path("project"),
        Path("job") / _TOKEN,
    )

    command = observed["command"]
    assert isinstance(command, list)
    assert command == [
        r"C:\Windows\System32\wsl.exe",
        "--cd",
        "/mnt/d/project",
        "--exec",
        "/home/current-user/.local/share/elysia-ai/soulx/prep-env/bin/python",
        "-B",
        "-m",
        "scripts.song_svs_wsl_bridge",
        f"/mnt/d/jobs/{_TOKEN}",
    ]
    assert not any("lyric text" in argument for argument in command)
    options = observed["options"]
    assert isinstance(options, dict) and options["shell"] is False


def test_wsl_cleanup_uses_only_fixed_module_flag_and_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover an interrupted private job without passing a caller path."""

    observed: dict[str, object] = {}

    class FakeProcess:
        """Expose the one exact cleanup acknowledgement from the bridge."""

        def __init__(self, command: list[str], **options: object) -> None:
            """Capture the no-shell cleanup boundary."""

            observed["command"] = command
            observed["options"] = options
            self.stdout = BytesIO(worker._BRIDGE_CLEANUP_EVENT)

        def wait(self) -> int:
            """Report one successful bridge cleanup."""

            return 0

        def kill(self) -> None:
            """Fail if the valid bounded response is rejected."""

            raise AssertionError("cleanup process should not be killed")

    monkeypatch.setattr(
        worker,
        "_translate_windows_path",
        lambda _wsl, _value: "/mnt/d/project",
    )
    monkeypatch.setattr(
        worker,
        "_resolve_wsl_prep_python",
        lambda _wsl: "/home/user/.local/share/elysia-ai/soulx/prep-env/bin/python",
    )
    monkeypatch.setattr(worker.subprocess, "Popen", FakeProcess)

    worker._run_wsl_private_cleanup(
        Path(r"C:\Windows\System32\wsl.exe"),
        Path("project"),
        _TOKEN,
    )

    assert observed["command"] == [
        r"C:\Windows\System32\wsl.exe",
        "--cd",
        "/mnt/d/project",
        "--exec",
        "/home/user/.local/share/elysia-ai/soulx/prep-env/bin/python",
        "-B",
        "-m",
        "scripts.song_svs_wsl_bridge",
        "--cleanup-private-job",
        _TOKEN,
    ]
    options = observed["options"]
    assert isinstance(options, dict) and options["shell"] is False
    assert worker._WSL_CLEANUP_TIMEOUT_SECONDS < 60


def test_wsl_cleanup_timeout_kills_launcher_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound a silent cleanup bridge below Electron's outer cleanup deadline."""

    observed: dict[str, object] = {}

    class _SilentProcess:
        """Represent a WSL cleanup launcher that never closes stdout."""

        def __init__(self, _command: list[str], **_options: object) -> None:
            """Expose a non-null stream required by the worker contract."""

            self.stdout = BytesIO()

        def kill(self) -> None:
            """Record bounded escalation of the stuck launcher."""

            observed["killed"] = True

        def wait(self) -> int:
            """Report completion after the synthetic kill."""

            observed["waited"] = True
            return -9

    class _StuckReader:
        """Model a bounded reader thread that remains blocked at deadline."""

        def __init__(self, **options: object) -> None:
            """Capture daemon configuration without invoking its target."""

            observed["thread_options"] = options

        def start(self) -> None:
            """Record reader startup."""

            observed["started"] = True

        def join(self, timeout: float | None = None) -> None:
            """Capture the private cleanup timeout budget."""

            observed["timeout"] = timeout

        def is_alive(self) -> bool:
            """Keep the synthetic read blocked until worker escalation."""

            return True

    monkeypatch.setattr(worker, "_translate_windows_path", lambda *_args: "/mnt/d/project")
    monkeypatch.setattr(
        worker,
        "_resolve_wsl_prep_python",
        lambda _wsl: "/home/user/private/python",
    )
    monkeypatch.setattr(worker.subprocess, "Popen", _SilentProcess)
    monkeypatch.setattr(worker.threading, "Thread", _StuckReader)

    with pytest.raises(worker._SvsWorkerFailure):
        worker._run_wsl_private_cleanup(
            Path(r"C:\Windows\System32\wsl.exe"),
            Path("project"),
            _TOKEN,
        )

    assert observed["timeout"] == worker._WSL_CLEANUP_TIMEOUT_SECONDS
    assert observed["killed"] is True
    assert observed["waited"] is True


def test_cleanup_main_emits_only_the_fixed_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep Main's recovery contract separate from synthesis progress."""

    observed: list[str] = []
    monkeypatch.setattr(
        worker,
        "_cleanup_private_job",
        lambda token: observed.append(token),
    )

    assert worker.main([worker._CLEANUP_ARGUMENT, _TOKEN]) == 0
    assert observed == [_TOKEN]
    assert capsys.readouterr().out == worker._WORKER_CLEANUP_EVENT + "\n"


def test_direct_isolated_launch_loads_only_the_fixed_sibling_helper() -> None:
    """Support Electron's embedded-Python script launch without ``sys.path``."""

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(worker.__file__).resolve()),
            "--help",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0
    assert b"ModuleNotFoundError" not in combined
    assert b"--job-token" in completed.stdout


def test_initial_job_layout_allows_only_main_owned_lyrics(tmp_path: Path) -> None:
    """Reject unknown files and directories before any model work begins."""

    job_root = tmp_path / _TOKEN
    job_root.mkdir()
    (job_root / "lyrics.lrc").write_text("[00:00.00]歌词", encoding="utf-8")
    (job_root / "lyrics-manifest.json").write_text("{}", encoding="utf-8")
    worker._require_exact_job_layout(
        job_root,
        allowed_files=worker._INITIAL_JOB_FILES,
        required_files=worker._REQUIRED_INITIAL_JOB_FILES,
        label="Prepared song-cover job",
    )

    (job_root / "injected.secret").write_text("private", encoding="utf-8")
    with pytest.raises(worker._SvsWorkerFailure, match="unexpected entry"):
        worker._require_exact_job_layout(
            job_root,
            allowed_files=worker._INITIAL_JOB_FILES,
            required_files=worker._REQUIRED_INITIAL_JOB_FILES,
            label="Prepared song-cover job",
        )
    (job_root / "injected.secret").unlink()
    (job_root / "injected-directory").mkdir()
    with pytest.raises(worker._SvsWorkerFailure, match="unexpected entry"):
        worker._require_exact_job_layout(
            job_root,
            allowed_files=worker._INITIAL_JOB_FILES,
            required_files=worker._REQUIRED_INITIAL_JOB_FILES,
            label="Prepared song-cover job",
        )


def test_initial_job_layout_requires_the_provenance_manifest(tmp_path: Path) -> None:
    """Refuse a lyric job that did not complete Main's atomic preparation."""

    job_root = tmp_path / _TOKEN
    job_root.mkdir()
    (job_root / "lyrics.lrc").write_text("[00:00.00]歌词", encoding="utf-8")

    with pytest.raises(worker._SvsWorkerFailure, match="incomplete"):
        worker._require_exact_job_layout(
            job_root,
            allowed_files=worker._INITIAL_JOB_FILES,
            required_files=worker._REQUIRED_INITIAL_JOB_FILES,
            label="Prepared song-cover job",
        )


@pytest.mark.parametrize("engine", ["lyrics-svs", "source-svc"])
def test_successful_job_removes_lyrics_and_scratch_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
) -> None:
    """Retain only final media after the private bridge and reviewed mix succeed."""

    job_root = tmp_path / _TOKEN
    job_root.mkdir()
    (job_root / "lyrics.lrc").write_text("[00:00.00]爱莉希雅", encoding="utf-8")
    (job_root / "lyrics.txt").write_text("爱莉希雅", encoding="utf-8")
    (job_root / "lyrics-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "vocal.wav").write_bytes(b"vocal")
    (tmp_path / "instrumental.wav").write_bytes(b"instrumental")
    arguments = _arguments(job_root)
    arguments.engine = engine
    if engine == "source-svc":
        for name in ("lyrics.lrc", "lyrics.txt", "lyrics-manifest.json"):
            (job_root / name).unlink()
        (job_root / "singing-request.json").write_text(json.dumps({
            "schema_version": 1, "reference": "fairy", "key_shift_semitones": 0,
        }), encoding="utf-8")

    monkeypatch.setattr(
        worker,
        "_validate_runtime_assets",
        lambda _arguments: (Path("ffmpeg"), Path("ffprobe"), None, None),
    )

    def fake_prepare(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        """Create representative fixed and nested scratch outputs."""

        target = job_root / "target_vocal.wav"
        target.write_bytes(b"target")
        accompaniment = job_root / "accompaniment.wav"
        accompaniment.write_bytes(b"accompaniment")
        scratch_tree = job_root / "provided-stems"
        scratch_tree.mkdir()
        (scratch_tree / "nested.wav").write_bytes(b"nested")
        return (
            10.0,
            441_000,
            target,
            accompaniment,
            [(scratch_tree, "Provided-stem scratch")],
            [
                (target, "Prepared target vocal"),
                (accompaniment, "Accompaniment scratch"),
            ],
        )

    monkeypatch.setattr(worker, "_prepare_sources", fake_prepare)
    monkeypatch.setattr(worker._audio, "_release_gpu_cache", lambda: None)
    monkeypatch.setattr(
        worker._audio,
        "_create_consonant_layer",
        lambda _ffmpeg, _source, output, **_options: output.write_bytes(b"c"),
    )
    monkeypatch.setattr(worker, "_system_wsl_executable", lambda: Path("wsl.exe"))

    def fake_bridge(_wsl: Path, _project: Path, root: Path, **options: object) -> None:
        """Publish the one fixed bridge output without running WSL."""

        if engine == "source-svc":
            assert options == {"engine": "source-svc"}
            _write_svc_wave(root / worker._GENERATED_VOCAL_NAME, 240_000)
        else:
            (root / worker._GENERATED_VOCAL_NAME).write_bytes(b"generated")

    monkeypatch.setattr(worker, "_run_wsl_bridge", fake_bridge)

    def fake_mix(
        _ffmpeg: Path,
        _vocal: Path,
        _consonants: Path | None,
        _accompaniment: Path,
        wav_output: Path,
        mp3_output: Path,
        **_options: object,
    ) -> None:
        """Create final outputs for cleanup-order verification."""

        assert (_consonants is None) == (engine == "source-svc")
        assert (job_root / "consonant-layer.wav").exists() == (engine == "lyrics-svs")
        wav_output.write_bytes(b"wav")
        mp3_output.write_bytes(b"mp3")

    monkeypatch.setattr(worker._audio, "_mix_cover", fake_mix)

    worker._run_job(arguments)

    assert sorted(path.name for path in job_root.iterdir()) == [
        "elysia-cover.mp3",
        "elysia-cover.wav",
    ]
    output = capsys.readouterr().out
    assert "爱莉希雅" not in output
    assert '"stage":"complete"' in output


def test_main_collapses_unexpected_failures_without_a_traceback_or_lyric(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep private transcript fragments out of the final diagnostic boundary."""

    private_lyric = "这是不能出现在日志里的歌词"
    monkeypatch.setattr(worker, "_run_job", lambda _arguments: (_ for _ in ()).throw(
        RuntimeError(private_lyric)
    ))

    status = worker.main(
        [
            "--input", "song.wav",
            "--shift-accompaniment", "true",
            "--job-dir", _TOKEN,
            "--ffmpeg", "ffmpeg.exe",
            "--ffprobe", "ffprobe.exe",
            "--demucs-site", "demucs",
            "--torch-home", "torch",
            "--job-token", _TOKEN,
        ]
    )
    captured = capsys.readouterr()

    assert status == 3
    assert private_lyric not in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
