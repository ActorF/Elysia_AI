"""Repeat one privacy-preserving real Song Cover smoke against private runtimes.

The command performs a read-only input preflight by default.  Loading the
private CUDA/WSL singing runtime requires the explicit
``--run-private-runtime`` flag.  A real run stages only bounded lyric assets in
an OS-temporary UUID job, invokes the production ``song_svs_worker.py`` rather
than copying its pipeline, confirms the worker's WSL cleanup-only protocol, and
then validates the final WAV/MP3 pair.  Standard output contains hashes and
media facts only; it never contains lyrics, native paths, or private runtime
diagnostics.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Final, NoReturn
import uuid
import wave


if __package__ in (None, ""):
    # Direct execution exposes only ``scripts`` on sys.path.  Anchor imports to
    # the reviewed repository instead of accepting a caller-controlled module
    # search path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import song_cover_worker as _production_audio
from scripts.song_lyrics_alignment import LyricsAlignmentError, parse_synced_lrc


_PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
_SMOKE_DIRECTORY_NAME: Final = "elysia-song-cover-smoke"
_WORKER_EVENT_PREFIX: Final = b"ELYSIA_SONG_COVER "
_CLEANUP_ACK: Final = b'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}\n'
_MAX_PROCESS_STDOUT_BYTES: Final = 256 * 1024
_MAX_LYRICS_BYTES: Final = 1024 * 1024
_MAX_MANIFEST_BYTES: Final = 64 * 1024
_WORKER_TIMEOUT_SECONDS: Final = 2 * 60 * 60
_CLEANUP_TIMEOUT_SECONDS: Final = 60
_PROBE_TIMEOUT_SECONDS: Final = 30
_OUTPUT_DURATION_TOLERANCE_SECONDS: Final = 1.0
_MAX_OUTPUT_BYTES: Final = 140 * 1024 * 1024
_UUID_PATTERN: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_INPUT_CONTAINER_BY_SUFFIX: Final[Mapping[str, frozenset[str]]] = {
    ".aac": frozenset({"aac"}),
    ".flac": frozenset({"flac"}),
    ".m4a": frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
    ".mp3": frozenset({"mp3"}),
    ".ogg": frozenset({"ogg"}),
    ".opus": frozenset({"ogg"}),
    ".wav": frozenset({"wav"}),
}
_EXPECTED_WORKER_STAGES: Final = (
    ("validating", 5),
    ("separating", 15),
    ("transcribing", 35),
    ("aligning", 52),
    ("synthesizing", 58),
    ("mixing", 90),
    ("complete", 100),
)
_SMOKE_MANIFEST: Final = {
    "hasPlainLyrics": True,
    "hasSyncedLyrics": True,
    "schemaVersion": 1,
    "source": "explicit-local-smoke",
}


class _SmokeFailure(Exception):
    """Carry one closed CLI error code without retaining a private diagnostic."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject malformed arguments without echoing caller-supplied path values."""

    def error(self, message: str) -> NoReturn:
        """Collapse every argparse diagnostic to one path-free request error."""

        del message
        raise _SmokeFailure("invalid_request")


@dataclass(frozen=True, slots=True)
class _RuntimePaths:
    """Hold the fixed source-development runtime locations used by Main."""

    python: Path
    worker: Path
    ffmpeg: Path
    ffprobe: Path
    demucs_site: Path
    torch_home: Path


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    """Contain one bounded child result after stderr has been discarded."""

    return_code: int
    stdout: bytes


@dataclass(frozen=True, slots=True)
class _AudioSummary:
    """Describe one probed audio stream without retaining its native path."""

    container: str
    codec: str
    sample_rate: int
    channels: int
    duration_seconds: float
    byte_count: int
    sha256: str
    bit_rate: int | None


@dataclass(frozen=True, slots=True)
class _TextSummary:
    """Describe one private UTF-8 lyric input without retaining its text."""

    text_format: str
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _Preflight:
    """Keep validated summaries plus in-memory lyric bytes for private staging."""

    vocals_path: Path
    accompaniment_path: Path
    lyrics_lrc: bytes
    plain_lyrics: bytes
    vocals: _AudioSummary
    accompaniment: _AudioSummary
    lrc: _TextSummary
    plain: _TextSummary


def _parser() -> argparse.ArgumentParser:
    """Build the closed smoke-test command without arbitrary runtime paths."""

    parser = _SafeArgumentParser(
        description=(
            "Preflight explicit Song Cover stems and lyrics; add "
            "--run-private-runtime to invoke the production lyrics-SVS worker."
        )
    )
    parser.add_argument("--vocals", required=True, type=Path)
    parser.add_argument("--accompaniment", required=True, type=Path)
    parser.add_argument("--lyrics-lrc", required=True, type=Path)
    parser.add_argument("--plain-lyrics", required=True, type=Path)
    parser.add_argument(
        "--key-shift-semitones",
        choices=(-2, -1, 0, 1, 2),
        default=0,
        type=int,
    )
    parser.add_argument(
        "--run-private-runtime",
        action="store_true",
        help="Explicitly load the ignored private CUDA/WSL singing runtime.",
    )
    parser.add_argument(
        "--keep-output",
        action="store_true",
        help=(
            "Keep a successful result under the fixed temporary smoke root; "
            "stdout reports only its UUID."
        ),
    )
    return parser


def _runtime_paths() -> _RuntimePaths:
    """Return the same fixed private runtime layout used by Electron Main."""

    gpt_runtime = _PROJECT_ROOT / "models" / "cache" / "GPT-SoVITS-v2-240821"
    singing_runtime = _PROJECT_ROOT / "models" / "cache" / "singing-runtime"
    return _RuntimePaths(
        python=gpt_runtime / "runtime" / "python.exe",
        worker=_PROJECT_ROOT / "scripts" / "song_svs_worker.py",
        ffmpeg=gpt_runtime / "ffmpeg.exe",
        ffprobe=gpt_runtime / "ffprobe.exe",
        demucs_site=singing_runtime / "site-packages",
        torch_home=singing_runtime / "torch",
    )


def _smoke_root() -> Path:
    """Return the fixed OS-temporary root that may contain private smoke jobs."""

    return Path(tempfile.gettempdir()) / _SMOKE_DIRECTORY_NAME


def _sha256_file(path: Path) -> str:
    """Hash one already-validated file with bounded-memory sequential reads."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_input_file(
    path: Path,
    *,
    label: str,
    allowed_suffixes: frozenset[str],
    maximum_bytes: int,
) -> tuple[Path, int]:
    """Validate one bounded, non-linked input without returning its path publicly."""

    try:
        candidate = _production_audio._require_file(path, label)
        info = os.lstat(candidate)
    except (OSError, RuntimeError, ValueError, _production_audio._SongCoverFailure):
        raise _SmokeFailure("invalid_input") from None
    if (
        candidate.suffix.lower() not in allowed_suffixes
        or not stat.S_ISREG(info.st_mode)
        or not 1 <= info.st_size <= maximum_bytes
    ):
        raise _SmokeFailure("invalid_input")
    return candidate, info.st_size


def _verify_fixed_file(path: Path, *, expected_bytes: int, expected_sha256: str) -> None:
    """Authenticate a fixed executable before the smoke command launches it."""

    try:
        candidate = _production_audio._require_file(path, "Song Cover runtime")
        if candidate.stat().st_size != expected_bytes:
            raise _SmokeFailure("runtime_unavailable")
        if _sha256_file(candidate).lower() != expected_sha256.lower():
            raise _SmokeFailure("runtime_unavailable")
    except _SmokeFailure:
        raise
    except (OSError, RuntimeError, ValueError, _production_audio._SongCoverFailure):
        raise _SmokeFailure("runtime_unavailable") from None


def _verify_runtime_tools(runtime: _RuntimePaths, *, for_run: bool) -> None:
    """Verify FFprobe for preflight and every native launcher before real work."""

    _verify_fixed_file(
        runtime.ffprobe,
        expected_bytes=_production_audio._EXPECTED_FFPROBE_BYTES,
        expected_sha256=_production_audio._EXPECTED_FFPROBE_SHA256,
    )
    if not for_run:
        return
    _verify_fixed_file(
        runtime.python,
        expected_bytes=_production_audio._EXPECTED_PYTHON_BYTES,
        expected_sha256=_production_audio._EXPECTED_PYTHON_SHA256,
    )
    _verify_fixed_file(
        runtime.ffmpeg,
        expected_bytes=_production_audio._EXPECTED_FFMPEG_BYTES,
        expected_sha256=_production_audio._EXPECTED_FFMPEG_SHA256,
    )
    try:
        _production_audio._require_file(runtime.worker, "Song Cover worker")
        _production_audio._require_directory(
            runtime.demucs_site,
            "Song Cover runtime",
        )
        _production_audio._require_directory(
            runtime.torch_home,
            "Song Cover runtime",
        )
    except _production_audio._SongCoverFailure:
        raise _SmokeFailure("runtime_unavailable") from None


def _closed_environment(runtime: _RuntimePaths, private_root: Path) -> dict[str, str]:
    """Build the same allowlisted offline environment used by Electron Main."""

    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    search_path = [str(runtime.python.parent), str(runtime.ffmpeg.parent)]
    if system_root:
        search_path.append(str(Path(system_root) / "System32"))
    environment = {
        "HF_HUB_OFFLINE": "1",
        "NUMBA_CACHE_DIR": str(private_root / "numba-cache"),
        "PATH": os.pathsep.join(search_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "TEMP": str(private_root),
        "TMP": str(private_root),
        "TORCH_HOME": str(runtime.torch_home),
        "TRANSFORMERS_OFFLINE": "1",
    }
    for name in ("CUDA_VISIBLE_DEVICES", "SystemDrive", "SystemRoot", "WINDIR"):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


def _run_closed_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> _ProcessResult:
    """Run one no-shell command while bounding retained output and diagnostics."""

    try:
        with tempfile.TemporaryFile() as stdout_file:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            try:
                return_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise _SmokeFailure("process_timeout") from error
            stdout_file.seek(0)
            stdout = stdout_file.read(_MAX_PROCESS_STDOUT_BYTES + 1)
    except _SmokeFailure:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise _SmokeFailure("runtime_unavailable") from error
    if len(stdout) > _MAX_PROCESS_STDOUT_BYTES:
        raise _SmokeFailure("invalid_protocol")
    return _ProcessResult(return_code, stdout)


def _parse_probe_payload(
    payload: bytes,
    *,
    suffix: str,
    byte_count: int,
    digest: str,
) -> _AudioSummary:
    """Parse one bounded FFprobe response and enforce the selected container."""

    try:
        document = json.loads(payload.decode("utf-8", errors="strict"))
        if (
            not isinstance(document, dict)
            or not {"format", "streams"}.issubset(document)
            or not set(document).issubset(
                {"format", "programs", "stream_groups", "streams"}
            )
            or document.get("programs", []) != []
            or document.get("stream_groups", []) != []
        ):
            raise ValueError
        streams = document["streams"]
        format_value = document["format"]
        if (
            not isinstance(streams, list)
            or len(streams) != 1
            or not isinstance(streams[0], dict)
            or not isinstance(format_value, dict)
        ):
            raise ValueError
        stream = streams[0]
        codec = stream["codec_name"]
        sample_rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
        format_names = str(format_value["format_name"]).split(",")
        raw_duration = stream.get("duration", format_value.get("duration"))
        if raw_duration is None:
            raise ValueError
        duration = float(raw_duration)
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _SmokeFailure("invalid_input") from None
    bit_rate: int | None = None
    for raw_bit_rate in (stream.get("bit_rate"), format_value.get("bit_rate")):
        if isinstance(raw_bit_rate, bool) or not isinstance(raw_bit_rate, (str, int)):
            continue
        try:
            candidate_bit_rate = int(raw_bit_rate)
        except (TypeError, ValueError):
            continue
        if candidate_bit_rate > 0:
            bit_rate = candidate_bit_rate
            break
    accepted_containers = _INPUT_CONTAINER_BY_SUFFIX.get(suffix)
    if (
        stream.get("codec_type") != "audio"
        or not isinstance(codec, str)
        or not codec
        or accepted_containers is None
        or not accepted_containers.intersection(format_names)
        or sample_rate <= 0
        or channels <= 0
        or not math.isfinite(duration)
        or not _production_audio._MIN_DURATION_SECONDS
        <= duration
        <= _production_audio._MAX_DURATION_SECONDS
    ):
        raise _SmokeFailure("invalid_input")
    return _AudioSummary(
        container=next(name for name in format_names if name in accepted_containers),
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=round(duration, 6),
        byte_count=byte_count,
        sha256=digest,
        bit_rate=bit_rate,
    )


def _probe_audio(path: Path, runtime: _RuntimePaths) -> _AudioSummary:
    """Hash and probe one explicit input without publishing its native path."""

    candidate, byte_count = _require_input_file(
        path,
        label="Smoke audio input",
        allowed_suffixes=frozenset(_INPUT_CONTAINER_BY_SUFFIX),
        maximum_bytes=_production_audio._MAX_SOURCE_BYTES,
    )
    digest = _sha256_file(candidate)
    result = _run_closed_process(
        [
            str(runtime.ffprobe),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels,duration,bit_rate:format=format_name,duration,bit_rate",
            "-of",
            "json",
            str(candidate),
        ],
        cwd=_PROJECT_ROOT,
        environment=_closed_environment(runtime, Path(tempfile.gettempdir())),
        timeout_seconds=_PROBE_TIMEOUT_SECONDS,
    )
    if result.return_code != 0:
        raise _SmokeFailure("invalid_input")
    return _parse_probe_payload(
        result.stdout,
        suffix=candidate.suffix.lower(),
        byte_count=byte_count,
        digest=digest,
    )


def _read_lyrics(path: Path, *, suffix: str, text_format: str) -> tuple[bytes, _TextSummary]:
    """Read one bounded strict-UTF-8 lyric input without returning its text."""

    candidate, byte_count = _require_input_file(
        path,
        label="Smoke lyric input",
        allowed_suffixes=frozenset({suffix}),
        maximum_bytes=_MAX_LYRICS_BYTES,
    )
    try:
        content = candidate.read_bytes()
        decoded = content.decode("utf-8", errors="strict")
    except (OSError, UnicodeError):
        raise _SmokeFailure("invalid_input") from None
    if not decoded.strip() or "\x00" in decoded:
        raise _SmokeFailure("invalid_input")
    return content, _TextSummary(
        text_format=text_format,
        byte_count=byte_count,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _preflight(arguments: argparse.Namespace, runtime: _RuntimePaths) -> _Preflight:
    """Validate and summarize all four inputs without loading a singing model."""

    _verify_runtime_tools(runtime, for_run=bool(arguments.run_private_runtime))
    vocals = _probe_audio(arguments.vocals, runtime)
    accompaniment = _probe_audio(arguments.accompaniment, runtime)
    try:
        if os.path.samefile(arguments.vocals, arguments.accompaniment):
            raise _SmokeFailure("invalid_input")
    except OSError:
        raise _SmokeFailure("invalid_input") from None
    lyrics_lrc, lrc_summary = _read_lyrics(
        arguments.lyrics_lrc,
        suffix=".lrc",
        text_format="lrc-utf8",
    )
    plain_lyrics, plain_summary = _read_lyrics(
        arguments.plain_lyrics,
        suffix=".txt",
        text_format="text-utf8",
    )
    track_duration_ms = round(
        max(vocals.duration_seconds, accompaniment.duration_seconds) * 1000
    )
    try:
        parse_synced_lrc(
            lyrics_lrc.decode("utf-8", errors="strict"),
            track_duration_ms,
        )
    except (LyricsAlignmentError, UnicodeError):
        raise _SmokeFailure("invalid_input") from None
    return _Preflight(
        vocals_path=Path(os.path.abspath(str(arguments.vocals))),
        accompaniment_path=Path(os.path.abspath(str(arguments.accompaniment))),
        lyrics_lrc=lyrics_lrc,
        plain_lyrics=plain_lyrics,
        vocals=vocals,
        accompaniment=accompaniment,
        lrc=lrc_summary,
        plain=plain_summary,
    )


def _write_private_file(job_root: Path, name: str, content: bytes) -> None:
    """Create one fixed-name mode-0600 job asset without replacing a file."""

    destination = job_root / name
    try:
        with destination.open("xb") as stream:
            os.chmod(destination, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise _SmokeFailure("job_setup_failed") from error


def _verify_inputs_unchanged(preflight: _Preflight) -> None:
    """Detect any source mutation between preflight and completed synthesis."""

    checks = (
        (
            preflight.vocals_path,
            frozenset(_INPUT_CONTAINER_BY_SUFFIX),
            _production_audio._MAX_SOURCE_BYTES,
            preflight.vocals.byte_count,
            preflight.vocals.sha256,
        ),
        (
            preflight.accompaniment_path,
            frozenset(_INPUT_CONTAINER_BY_SUFFIX),
            _production_audio._MAX_SOURCE_BYTES,
            preflight.accompaniment.byte_count,
            preflight.accompaniment.sha256,
        ),
    )
    for path, suffixes, maximum, byte_count, digest in checks:
        try:
            candidate, current_bytes = _require_input_file(
                path,
                label="Smoke audio input",
                allowed_suffixes=suffixes,
                maximum_bytes=maximum,
            )
            if current_bytes != byte_count or _sha256_file(candidate) != digest:
                raise _SmokeFailure("input_changed")
        except _SmokeFailure as error:
            if error.code == "input_changed":
                raise
            raise _SmokeFailure("input_changed") from None


def _create_job(preflight: _Preflight) -> tuple[Path, str]:
    """Create one external temporary UUID job with the fixed four-field manifest."""

    root = Path(os.path.abspath(str(_smoke_root())))
    project_root = Path(os.path.abspath(str(_PROJECT_ROOT)))
    try:
        if os.path.commonpath((str(root), str(project_root))) == str(project_root):
            raise _SmokeFailure("unsafe_temp_root")
    except ValueError:
        pass
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = _production_audio._require_directory(
            root,
            "Song Cover smoke root",
        )
        job_id = str(uuid.uuid4())
        job_root = root / job_id
        job_root.mkdir(mode=0o700)
        job_root = _production_audio._require_directory(
            job_root,
            "Song Cover smoke job",
        )
    except (OSError, _production_audio._SongCoverFailure) as error:
        raise _SmokeFailure("job_setup_failed") from error
    manifest = json.dumps(
        _SMOKE_MANIFEST,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(manifest) > _MAX_MANIFEST_BYTES:
        raise _SmokeFailure("job_setup_failed")
    try:
        _write_private_file(job_root, "lyrics.lrc", preflight.lyrics_lrc)
        _write_private_file(job_root, "lyrics.txt", preflight.plain_lyrics)
        _write_private_file(job_root, "lyrics-manifest.json", manifest)
    except BaseException:
        _remove_job(job_root)
        raise
    return job_root, job_id


def _worker_command(
    runtime: _RuntimePaths,
    preflight: _Preflight,
    job_root: Path,
    job_id: str,
    key_shift: int,
) -> list[str]:
    """Build the production stems-mode command from fixed options only."""

    return [
        str(runtime.python),
        str(runtime.worker),
        "--source-mode",
        "stems",
        "--vocal-input",
        str(preflight.vocals_path),
        "--accompaniment-input",
        str(preflight.accompaniment_path),
        "--key-shift-semitones",
        str(key_shift),
        "--job-dir",
        str(job_root),
        "--ffmpeg",
        str(runtime.ffmpeg),
        "--ffprobe",
        str(runtime.ffprobe),
        "--demucs-site",
        str(runtime.demucs_site),
        "--torch-home",
        str(runtime.torch_home),
        "--job-token",
        job_id,
    ]


def _parse_worker_events(stdout: bytes, *, require_complete: bool) -> tuple[str, ...]:
    """Accept only the production worker's exact ordered, bounded progress stream."""

    events: list[str] = []
    lines = stdout.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise _SmokeFailure("invalid_protocol")
    for index, raw_line in enumerate(lines):
        if not raw_line.startswith(_WORKER_EVENT_PREFIX):
            raise _SmokeFailure("invalid_protocol")
        try:
            payload = json.loads(
                raw_line[len(_WORKER_EVENT_PREFIX) : -1].decode(
                    "utf-8",
                    errors="strict",
                )
            )
        except (UnicodeError, json.JSONDecodeError):
            raise _SmokeFailure("invalid_protocol") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"message", "progressPercent", "stage"}
            or index >= len(_EXPECTED_WORKER_STAGES)
        ):
            raise _SmokeFailure("invalid_protocol")
        expected_stage, expected_progress = _EXPECTED_WORKER_STAGES[index]
        message = payload.get("message")
        if (
            payload.get("stage") != expected_stage
            or payload.get("progressPercent") != expected_progress
            or not isinstance(message, str)
            or not 1 <= len(message) <= 160
            or any(ord(character) < 0x20 for character in message)
        ):
            raise _SmokeFailure("invalid_protocol")
        events.append(expected_stage)
    if require_complete and len(events) != len(_EXPECTED_WORKER_STAGES):
        raise _SmokeFailure("invalid_protocol")
    if not require_complete and len(events) >= len(_EXPECTED_WORKER_STAGES):
        raise _SmokeFailure("invalid_protocol")
    return tuple(events)


def _confirm_private_cleanup(
    runtime: _RuntimePaths,
    job_root: Path,
    job_id: str,
) -> None:
    """Require the production worker's exact idempotent WSL cleanup acknowledgement."""

    result = _run_closed_process(
        [
            str(runtime.python),
            str(runtime.worker),
            "--cleanup-private-job",
            job_id,
        ],
        cwd=_PROJECT_ROOT,
        environment=_closed_environment(runtime, job_root),
        timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
    )
    if result.return_code != 0 or result.stdout not in {
        _CLEANUP_ACK,
        _CLEANUP_ACK.replace(b"\n", b"\r\n"),
    }:
        raise _SmokeFailure("private_cleanup_failed")


def _validate_wav_output(path: Path, *, expected_duration: float) -> _AudioSummary:
    """Require the production lossless output's exact PCM encoding and duration."""

    candidate, byte_count = _require_input_file(
        path,
        label="Song Cover WAV output",
        allowed_suffixes=frozenset({".wav"}),
        maximum_bytes=_MAX_OUTPUT_BYTES,
    )
    try:
        with wave.open(str(candidate), "rb") as source:
            channels = source.getnchannels()
            sample_rate = source.getframerate()
            sample_width = source.getsampwidth()
            frame_count = source.getnframes()
    except (EOFError, OSError, wave.Error):
        raise _SmokeFailure("invalid_output") from None
    duration = frame_count / sample_rate if sample_rate > 0 else 0.0
    if (
        channels != 2
        or sample_rate != 44_100
        or sample_width != 2
        or frame_count <= 0
        or abs(duration - expected_duration) > _OUTPUT_DURATION_TOLERANCE_SECONDS
    ):
        raise _SmokeFailure("invalid_output")
    return _AudioSummary(
        container="wav",
        codec="pcm_s16le",
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=round(duration, 6),
        byte_count=byte_count,
        sha256=_sha256_file(candidate),
        bit_rate=None,
    )


def _validate_mp3_output(
    path: Path,
    runtime: _RuntimePaths,
    *,
    expected_duration: float,
) -> _AudioSummary:
    """Require one stereo 44.1-kHz MP3 whose duration matches the input stems."""

    candidate, byte_count = _require_input_file(
        path,
        label="Song Cover MP3 output",
        allowed_suffixes=frozenset({".mp3"}),
        maximum_bytes=_MAX_OUTPUT_BYTES,
    )
    digest = _sha256_file(candidate)
    result = _run_closed_process(
        [
            str(runtime.ffprobe),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels,duration,bit_rate:format=format_name,duration,bit_rate",
            "-of",
            "json",
            str(candidate),
        ],
        cwd=_PROJECT_ROOT,
        environment=_closed_environment(runtime, candidate.parent),
        timeout_seconds=_PROBE_TIMEOUT_SECONDS,
    )
    if result.return_code != 0:
        raise _SmokeFailure("invalid_output")
    try:
        summary = _parse_probe_payload(
            result.stdout,
            suffix=".mp3",
            byte_count=byte_count,
            digest=digest,
        )
    except _SmokeFailure:
        raise _SmokeFailure("invalid_output") from None
    if (
        summary.codec != "mp3"
        or summary.sample_rate != 44_100
        or summary.channels != 2
        or summary.bit_rate != 320_000
        or abs(summary.duration_seconds - expected_duration)
        > _OUTPUT_DURATION_TOLERANCE_SECONDS
    ):
        raise _SmokeFailure("invalid_output")
    return summary


def _validate_outputs(
    job_root: Path,
    runtime: _RuntimePaths,
    *,
    expected_duration: float,
) -> Mapping[str, _AudioSummary]:
    """Validate the final two-file job layout and both production encodings."""

    try:
        names = {entry.name for entry in os.scandir(job_root)}
    except OSError:
        raise _SmokeFailure("invalid_output") from None
    if names != {"elysia-cover.wav", "elysia-cover.mp3"}:
        raise _SmokeFailure("invalid_output")
    return {
        "wav": _validate_wav_output(
            job_root / "elysia-cover.wav",
            expected_duration=expected_duration,
        ),
        "mp3": _validate_mp3_output(
            job_root / "elysia-cover.mp3",
            runtime,
            expected_duration=expected_duration,
        ),
    }


def _remove_job(job_root: Path) -> None:
    """Delete only one verified UUID child below the fixed external smoke root."""

    root = Path(os.path.abspath(str(_smoke_root())))
    candidate = Path(os.path.abspath(str(job_root)))
    if candidate.parent != root or _UUID_PATTERN.fullmatch(candidate.name) is None:
        raise _SmokeFailure("job_cleanup_failed")
    try:
        root = _production_audio._require_directory(
            root,
            "Song Cover smoke root",
        )
        info = os.lstat(candidate)
    except FileNotFoundError:
        return
    except (OSError, _production_audio._SongCoverFailure) as error:
        raise _SmokeFailure("job_cleanup_failed") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _production_audio._is_reparse(info)
    ):
        raise _SmokeFailure("job_cleanup_failed")
    try:
        shutil.rmtree(candidate)
        root.rmdir()
    except OSError:
        # Other retained smoke jobs legitimately keep the shared root nonempty.
        if candidate.exists():
            raise _SmokeFailure("job_cleanup_failed") from None


def _audio_json(summary: _AudioSummary) -> dict[str, object]:
    """Serialize one audio summary without adding filenames or native paths."""

    return {
        "bit_rate": summary.bit_rate,
        "bytes": summary.byte_count,
        "channels": summary.channels,
        "codec": summary.codec,
        "container": summary.container,
        "duration_seconds": summary.duration_seconds,
        "sample_rate": summary.sample_rate,
        "sha256": summary.sha256,
    }


def _text_json(summary: _TextSummary) -> dict[str, object]:
    """Serialize one lyric hash without serializing lyric text or a filename."""

    return {
        "bytes": summary.byte_count,
        "format": summary.text_format,
        "sha256": summary.sha256,
    }


def _input_json(preflight: _Preflight) -> dict[str, object]:
    """Return the stable read-only preflight report shared by both modes."""

    return {
        "accompaniment": _audio_json(preflight.accompaniment),
        "lyrics_lrc": _text_json(preflight.lrc),
        "plain_lyrics": _text_json(preflight.plain),
        "vocals": _audio_json(preflight.vocals),
    }


def _run_real_smoke(
    arguments: argparse.Namespace,
    runtime: _RuntimePaths,
    preflight: _Preflight,
) -> dict[str, object]:
    """Invoke production SVS, confirm WSL cleanup, validate outputs, and clean up."""

    job_root, job_id = _create_job(preflight)
    keep_success = False
    worker_failure: BaseException | None = None
    result: _ProcessResult | None = None
    try:
        try:
            result = _run_closed_process(
                _worker_command(
                    runtime,
                    preflight,
                    job_root,
                    job_id,
                    arguments.key_shift_semitones,
                ),
                cwd=_PROJECT_ROOT,
                environment=_closed_environment(runtime, job_root),
                timeout_seconds=_WORKER_TIMEOUT_SECONDS,
            )
        except BaseException as error:
            worker_failure = error

        # Cleanup-only is idempotent.  Running it after success as well as
        # failure turns absence of a WSL private job into an explicit smoke
        # assertion instead of trusting the synthesis return code.
        _confirm_private_cleanup(runtime, job_root, job_id)
        if worker_failure is not None:
            raise worker_failure
        if result is None or result.return_code != 0:
            if result is not None and result.stdout:
                _parse_worker_events(result.stdout, require_complete=False)
            raise _SmokeFailure("worker_failed")
        stages = _parse_worker_events(result.stdout, require_complete=True)
        _verify_inputs_unchanged(preflight)
        # The run may last hours. Re-authenticate native launchers before the
        # final MP3 probe so the reported result cannot rely on a replaced tool.
        _verify_runtime_tools(runtime, for_run=True)
        expected_duration = max(
            preflight.vocals.duration_seconds,
            preflight.accompaniment.duration_seconds,
        )
        outputs = _validate_outputs(
            job_root,
            runtime,
            expected_duration=expected_duration,
        )
        keep_success = bool(arguments.keep_output)
        return {
            "inputs": _input_json(preflight),
            "job_id": job_id,
            "key_shift_semitones": arguments.key_shift_semitones,
            "outputs": {
                name: _audio_json(summary)
                for name, summary in outputs.items()
            },
            "private_cleanup": "confirmed",
            "retained": keep_success,
            "stages": list(stages),
            "status": "complete",
        }
    finally:
        if not keep_success:
            _remove_job(job_root)


def _write_safe_error(code: str) -> None:
    """Emit one closed code without paths, lyrics, commands, or exception text."""

    print(
        json.dumps({"error": code}, separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run a model-free preflight or an explicitly authorized private smoke."""

    try:
        arguments = _parser().parse_args(argv)
        if arguments.keep_output and not arguments.run_private_runtime:
            raise _SmokeFailure("invalid_request")
        runtime = _runtime_paths()
        preflight = _preflight(arguments, runtime)
        if arguments.run_private_runtime:
            report = _run_real_smoke(arguments, runtime, preflight)
        else:
            report = {
                "inputs": _input_json(preflight),
                "key_shift_semitones": arguments.key_shift_semitones,
                "run_private_runtime": False,
                "status": "preflight",
            }
    except _SmokeFailure as error:
        _write_safe_error(error.code)
        return 2
    except SystemExit as error:
        # argparse uses SystemExit only for the path-free ``--help`` response;
        # malformed arguments are collapsed by _SafeArgumentParser.error.
        return int(error.code or 0)
    except KeyboardInterrupt:
        _write_safe_error("cancelled")
        return 130
    except BaseException:
        # Private paths, lyrics, subprocess commands, and vendor diagnostics can
        # all appear in unexpected exceptions; collapse them at the CLI edge.
        _write_safe_error("internal_error")
        return 1
    print(
        json.dumps(
            report,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
