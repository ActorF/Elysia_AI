"""Create one lyrics-driven Elysia cover through the private WSL SVS runtime.

Electron Main creates a UUID job directory containing fixed-name synchronized
lyrics, then starts this short-lived Windows worker with the same native audio
contract as the legacy cover worker.  This module reuses the reviewed native
validation, Demucs separation, pitch-shift, consonant-preservation, and mix
helpers from :mod:`scripts.song_cover_worker`.  Only a prepared vocal and the
fixed lyric assets cross into WSL; lyric text never appears in an argument,
progress event, diagnostic, or renderer-visible value.

The WSL boundary has no shell.  Windows paths are translated by the absolute
System32 ``wsl.exe`` and a fixed ``wslpath`` executable, after which the fixed
private prep interpreter runs :mod:`scripts.song_svs_wsl_bridge`.  Its bounded
``ELYSIA_SONG_SVS`` protocol is validated and mapped to the exact
``ELYSIA_SONG_COVER`` stage sequence expected by Electron Main.  Successful
jobs retain only the final WAV and MP3; every lyric and scratch artifact is
removed before completion is reported.  Recovery cleanup is internally
bounded to 30 seconds, below Electron Main's 60-second cleanup deadline, so
the native manager remains the outer authority without racing equal timers.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
from types import ModuleType
from typing import BinaryIO, NoReturn, Optional, Sequence, cast

if __package__:
    from scripts import song_cover_worker as _audio
else:  # pragma: no cover - exercised by Electron's direct script launch.
    def _load_sibling_audio_worker() -> ModuleType:
        """Load the fixed sibling helper without trusting ``sys.path``.

        The bundled embedded Python intentionally has an isolated ``._pth``
        file, so direct script execution does not place the repository's
        ``scripts`` directory on the import path.  Loading this one reviewed
        sibling by its path preserves that isolation and avoids making any
        current directory or environment-controlled path importable.
        """

        module_path = Path(__file__).resolve().with_name("song_cover_worker.py")
        specification = importlib.util.spec_from_file_location(
            "elysia_song_cover_worker",
            module_path,
        )
        if specification is None or specification.loader is None:
            raise RuntimeError("The native song-cover helper is unavailable.")
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        try:
            specification.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(specification.name, None)
            raise
        return module

    _audio = _load_sibling_audio_worker()


_SVS_EVENT_PREFIX = "ELYSIA_SONG_SVS "
_BRIDGE_CLEANUP_EVENT = b'ELYSIA_SONG_SVS_CLEANUP {"status":"complete"}\n'
_WORKER_CLEANUP_EVENT = 'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}'
_CLEANUP_ARGUMENT = "--cleanup-private-job"
_WSL_PATH_EXECUTABLE = "/usr/bin/wslpath"
_WSL_SYSTEM_PYTHON = "/usr/bin/python3"
_WSL_HOME_QUERY = (
    "import os,pwd,sys;"
    "sys.stdout.write(pwd.getpwuid(os.getuid()).pw_dir)"
)
_WSL_PREP_PYTHON_SUFFIX = ".local/share/elysia-ai/soulx/prep-env/bin/python"
_WSL_BRIDGE_MODULE = "scripts.song_svs_wsl_bridge"
_MAX_WSL_PATH_BYTES = 8 * 1024
_MAX_SVS_LINE_BYTES = 2 * 1024
_MAX_SVS_OUTPUT_BYTES = 256 * 1024
_MAX_SVS_EVENTS = 128
_WSL_PATH_TIMEOUT_SECONDS = 30
_WSL_CLEANUP_TIMEOUT_SECONDS = 30
_MAX_LYRICS_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_GENERATED_VOCAL_NAME = "generated_vocal.wav"
_LYRICS_NAMES = ("lyrics.lrc", "lyrics.txt", "lyrics-manifest.json")
_INITIAL_JOB_FILES = frozenset(_LYRICS_NAMES)
_REQUIRED_INITIAL_JOB_FILES = frozenset({"lyrics.lrc", "lyrics-manifest.json"})
_FINAL_JOB_FILES = frozenset({"elysia-cover.wav", "elysia-cover.mp3"})
_SVS_STAGE_SEQUENCE = (
    "validating",
    "transcribing",
    "aligning",
    "synthesizing",
    "ready",
)
_SVS_STAGE_PROGRESS = {
    "validating": 4,
    "transcribing": 18,
    "aligning": 52,
    "synthesizing": 58,
    "ready": 100,
}
_SVS_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")


class _SvsWorkerFailure(RuntimeError):
    """Represent one path-free terminal failure in the Windows SVS layer."""


@dataclass(frozen=True)
class _SvsEvent:
    """Hold one validated private bridge event without exposing its message."""

    stage: str
    progress_percent: int
    error_code: Optional[str]


@dataclass(frozen=True)
class _BridgeOutcome:
    """Describe the single terminal state accepted from the WSL bridge."""

    ready: bool
    error_code: Optional[str]


def _fail(message: str) -> NoReturn:
    """Stop the worker with one stable explanation that contains no path."""

    raise _SvsWorkerFailure(message)


def _emit(stage: str, progress_percent: int, message: str) -> None:
    """Publish one canonical event accepted by Electron's closed protocol."""

    payload = {
        "message": message,
        "progressPercent": progress_percent,
        "stage": stage,
    }
    print(
        f"{_audio._EVENT_PREFIX}"
        f"{json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}",
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    """Build the exact lyrics-SVS command line emitted by Electron Main."""

    parser = argparse.ArgumentParser(
        description="Create one local lyrics-driven Elysia song cover."
    )
    parser.add_argument(
        "--source-mode",
        choices=("song", "stems"),
        default="song",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--vocal-input", type=Path)
    parser.add_argument("--accompaniment-input", type=Path)
    parser.add_argument(
        "--key-shift-semitones",
        choices=tuple(sorted(_audio._ALLOWED_KEY_SHIFTS)),
        default=0,
        type=int,
    )
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    parser.add_argument("--demucs-site", required=True, type=Path)
    parser.add_argument("--torch-home", required=True, type=Path)
    parser.add_argument("--job-token", required=True)
    return parser


def _require_single_link_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> Path:
    """Require one bounded regular input that has no hard-link alias."""

    candidate = _audio._require_file(path, label)
    try:
        info = os.lstat(candidate)
    except OSError as error:
        raise _SvsWorkerFailure(f"{label} could not be verified.") from error
    if info.st_nlink != 1 or not 1 <= info.st_size <= maximum_bytes:
        _fail(f"{label} is unavailable or unsafe.")
    return candidate


def _require_optional_single_link_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> Optional[Path]:
    """Validate one optional managed file while rejecting unsafe aliases."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _SvsWorkerFailure(f"{label} could not be verified.") from error
    return _require_single_link_file(
        path,
        label=label,
        maximum_bytes=maximum_bytes,
    )


def _require_absent(path: Path, *, label: str) -> None:
    """Reject a collision instead of replacing an unowned job artifact."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _SvsWorkerFailure(f"{label} could not be verified.") from error
    _fail(f"{label} already exists in the managed job.")


def _require_exact_job_layout(
    job_root: Path,
    *,
    allowed_files: frozenset[str],
    required_files: frozenset[str],
    label: str,
) -> None:
    """Require an exact flat allowlist without links, directories, or extras.

    Main creates the initial lyric assets before this process starts.  An
    allowlist prevents a raced directory entry from being mistaken for worker
    scratch or surviving success.  The same check is repeated after cleanup;
    a late insertion therefore fails the job and lets Main remove its entire
    UUID directory instead of publishing a partially cleaned result.
    """

    names: set[str] = set()
    for candidate, is_directory in _audio._iter_safe_directory(
        job_root,
        label=label,
    ):
        if is_directory or candidate.name not in allowed_files:
            _fail(f"{label} contains an unexpected entry.")
        names.add(candidate.name)
    if not required_files.issubset(names):
        _fail(f"{label} is incomplete.")


def _system_wsl_executable() -> Path:
    """Return the absolute native WSL launcher below the real system root."""

    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        _fail("The Windows WSL launcher is unavailable.")
    root = Path(os.path.abspath(system_root))
    wsl = _audio._require_file(root / "System32" / "wsl.exe", "WSL launcher")
    if wsl.name.casefold() != "wsl.exe" or wsl.parent.name.casefold() != "system32":
        _fail("The Windows WSL launcher is unavailable.")
    return wsl


def _validate_wsl_path(value: str) -> str:
    """Accept one absolute normalized Linux path returned by fixed ``wslpath``."""

    if (
        not value.startswith("/")
        or len(value.encode("utf-8")) > _MAX_WSL_PATH_BYTES
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        _fail("A native path could not be translated for WSL safely.")
    parts = value.split("/")[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _fail("A native path could not be translated for WSL safely.")
    return value


def _translate_windows_path(wsl: Path, native_path: Path) -> str:
    """Translate one authenticated native path without invoking a shell."""

    try:
        completed = subprocess.run(
            [
                str(wsl),
                "--exec",
                _WSL_PATH_EXECUTABLE,
                "-a",
                "-u",
                str(native_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_WSL_PATH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _SvsWorkerFailure(
            "A native path could not be translated for WSL safely."
        ) from error
    if completed.returncode != 0 or len(completed.stdout) > _MAX_WSL_PATH_BYTES:
        _fail("A native path could not be translated for WSL safely.")
    try:
        decoded = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _SvsWorkerFailure(
            "A native path could not be translated for WSL safely."
        ) from error
    if decoded.endswith("\r\n"):
        value = decoded[:-2]
    elif decoded.endswith("\n"):
        value = decoded[:-1]
    else:
        _fail("A native path could not be translated for WSL safely.")
    if "\r" in value or "\n" in value:
        _fail("A native path could not be translated for WSL safely.")
    return _validate_wsl_path(value)


def _resolve_wsl_prep_python(wsl: Path) -> str:
    """Build the fixed private interpreter path from the current WSL uid home.

    The home directory comes from POSIX ``pwd`` inside the selected WSL
    distribution, not a Windows environment variable or hard-coded username.
    The query is a fixed isolated Python command and never invokes a shell.
    Only the reviewed runtime suffix is appended after strict path validation.
    """

    try:
        completed = subprocess.run(
            [
                str(wsl),
                "--exec",
                _WSL_SYSTEM_PYTHON,
                "-I",
                "-c",
                _WSL_HOME_QUERY,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_WSL_PATH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _SvsWorkerFailure(
            "The private WSL runtime location could not be resolved safely."
        ) from error
    if completed.returncode != 0 or len(completed.stdout) > _MAX_WSL_PATH_BYTES:
        _fail("The private WSL runtime location could not be resolved safely.")
    try:
        home = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _SvsWorkerFailure(
            "The private WSL runtime location could not be resolved safely."
        ) from error
    try:
        validated_home = _validate_wsl_path(home)
        return _validate_wsl_path(
            f"{validated_home}/{_WSL_PREP_PYTHON_SUFFIX}"
        )
    except _SvsWorkerFailure as error:
        raise _SvsWorkerFailure(
            "The private WSL runtime location could not be resolved safely."
        ) from error


def _parse_svs_event(line: bytes) -> _SvsEvent:
    """Validate one bounded bridge event while deliberately discarding text."""

    try:
        decoded = line.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise _SvsWorkerFailure(
            "The private singing runtime returned an invalid response."
        ) from error
    if not decoded.startswith(_SVS_EVENT_PREFIX):
        _fail("The private singing runtime returned an invalid response.")
    try:
        payload = json.loads(decoded[len(_SVS_EVENT_PREFIX) :])
    except json.JSONDecodeError as error:
        raise _SvsWorkerFailure(
            "The private singing runtime returned an invalid response."
        ) from error
    if not isinstance(payload, dict):
        _fail("The private singing runtime returned an invalid response.")
    allowed = {"message", "progressPercent", "stage", "errorCode"}
    required = {"message", "progressPercent", "stage"}
    if set(payload) - allowed or not required.issubset(payload):
        _fail("The private singing runtime returned an invalid response.")
    stage = payload.get("stage")
    progress = payload.get("progressPercent")
    message = payload.get("message")
    error_code = payload.get("errorCode")
    if (
        not isinstance(stage, str)
        or stage not in {*_SVS_STAGE_SEQUENCE, "error"}
        or type(progress) is not int
        or not 0 <= progress <= 100
        or not isinstance(message, str)
        or not 1 <= len(message) <= 240
        or any(ord(character) < 0x20 for character in message)
        or "/" in message
        or "\\" in message
        or (
            error_code is not None
            and (
                not isinstance(error_code, str)
                or _SVS_ERROR_CODE_PATTERN.fullmatch(error_code) is None
            )
        )
        or (stage == "error") != (error_code is not None)
    ):
        _fail("The private singing runtime returned an invalid response.")
    return _SvsEvent(stage, progress, error_code)


def _relay_svs_protocol(stream: BinaryIO) -> _BridgeOutcome:
    """Map one complete, ordered SVS stream to Electron's cover lifecycle.

    Vendor and bridge messages are intentionally never forwarded.  The native
    manager already owns fixed presentation strings, while dropping the
    private text here prevents an upstream diagnostic from becoming a path or
    lyric disclosure even if a future bridge validation regresses.
    """

    total_bytes = 0
    event_count = 0
    expected_index = 0
    terminal_error: Optional[str] = None
    while True:
        raw_line = stream.readline(_MAX_SVS_LINE_BYTES + 1)
        if not raw_line:
            break
        total_bytes += len(raw_line)
        event_count += 1
        if (
            len(raw_line) > _MAX_SVS_LINE_BYTES
            or total_bytes > _MAX_SVS_OUTPUT_BYTES
            or event_count > _MAX_SVS_EVENTS
            or not raw_line.endswith(b"\n")
        ):
            _fail("The private singing runtime returned an invalid response.")
        line = raw_line[:-1]
        if line.endswith(b"\r"):
            line = line[:-1]
        event = _parse_svs_event(line)
        if terminal_error is not None:
            _fail("The private singing runtime returned an invalid response.")
        if event.stage == "error":
            if event.progress_percent != 0:
                _fail("The private singing runtime returned an invalid response.")
            terminal_error = event.error_code
            continue
        if (
            expected_index >= len(_SVS_STAGE_SEQUENCE)
            or event.stage != _SVS_STAGE_SEQUENCE[expected_index]
            or event.progress_percent != _SVS_STAGE_PROGRESS[event.stage]
        ):
            _fail("The private singing runtime returned an invalid response.")
        expected_index += 1
        if event.stage == "transcribing":
            _emit(
                "transcribing",
                35,
                "Transcribing the source melody and syllables",
            )
        elif event.stage == "aligning":
            _emit("aligning", 52, "Aligning verified lyrics to the detected notes")
        elif event.stage == "synthesizing":
            _emit("synthesizing", 58, "Singing the verified lyrics with Elysia voice")

    if terminal_error == "lyrics_alignment_failed" and expected_index == 2:
        # Transcription and alignment share one isolated prep process, so the
        # private runtime cannot emit its normal alignment event until that
        # process succeeds.  A typed alignment failure nevertheless proves
        # that execution entered that phase; publishing the closed stage here
        # lets Electron classify it accurately without exposing diagnostics.
        _emit("aligning", 52, "Aligning verified lyrics to the detected notes")
    if terminal_error is not None:
        return _BridgeOutcome(False, terminal_error)
    return _BridgeOutcome(
        expected_index == len(_SVS_STAGE_SEQUENCE),
        None,
    )


def _run_wsl_bridge(wsl: Path, project_root: Path, job_root: Path) -> None:
    """Run the fixed WSL bridge and accept only its closed event protocol."""

    mounted_project = _translate_windows_path(wsl, project_root)
    mounted_job = _translate_windows_path(wsl, job_root)
    prep_python = _resolve_wsl_prep_python(wsl)
    command = [
        str(wsl),
        "--cd",
        mounted_project,
        "--exec",
        prep_python,
        "-B",
        "-m",
        _WSL_BRIDGE_MODULE,
        mounted_job,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as error:
        raise _SvsWorkerFailure(
            "The private singing runtime could not be started."
        ) from error
    if process.stdout is None:
        process.kill()
        process.wait()
        _fail("The private singing runtime could not be started.")
    try:
        outcome = _relay_svs_protocol(cast(BinaryIO, process.stdout))
    except BaseException:
        process.kill()
        process.wait()
        raise
    return_code = process.wait()
    if return_code != 0 or outcome.error_code is not None:
        _fail("The private lyrics-driven singing stage did not complete.")
    if not outcome.ready:
        _fail("The private singing runtime returned an invalid response.")


def _run_wsl_private_cleanup(
    wsl: Path,
    project_root: Path,
    job_token: str,
) -> None:
    """Ask the fixed bridge to delete only one canonical private UUID job.

    This recovery process is intentionally separate from synthesis because
    Electron can force-kill the original worker before its bridge reaches a
    Python ``finally`` block.  The command contains no mounted job path or
    lyric data: only the reviewed module, a fixed mode flag, and the UUID that
    Main already assigned to the terminated transaction.  The bounded reader
    stops this inner helper before Electron Main's longer cleanup watchdog.
    """

    if _audio._JOB_TOKEN_PATTERN.fullmatch(job_token) is None:
        _fail("Song-cover cleanup identity is invalid.")
    mounted_project = _translate_windows_path(wsl, project_root)
    prep_python = _resolve_wsl_prep_python(wsl)
    command = [
        str(wsl),
        "--cd",
        mounted_project,
        "--exec",
        prep_python,
        "-B",
        "-m",
        _WSL_BRIDGE_MODULE,
        _CLEANUP_ARGUMENT,
        job_token,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as error:
        raise _SvsWorkerFailure(
            "The private singing job could not be cleaned up."
        ) from error
    if process.stdout is None:
        process.kill()
        process.wait()
        _fail("The private singing job could not be cleaned up.")
    output_stream = cast(BinaryIO, process.stdout)
    response: list[bytes] = []
    read_error: list[BaseException] = []

    def _read_bounded_response() -> None:
        """Read at most one byte beyond the exact cleanup acknowledgement."""

        try:
            response.append(output_stream.read(len(_BRIDGE_CLEANUP_EVENT) + 1))
        except BaseException as error:
            read_error.append(error)

    reader = threading.Thread(target=_read_bounded_response, daemon=True)
    reader.start()
    reader.join(_WSL_CLEANUP_TIMEOUT_SECONDS)
    if reader.is_alive():
        process.kill()
        process.wait()
        _fail("The private singing job could not be cleaned up.")
    if read_error:
        process.kill()
        process.wait()
        raise read_error[0]
    return_code = process.wait()
    if (
        return_code != 0
        or response != [_BRIDGE_CLEANUP_EVENT]
    ):
        _fail("The private singing job could not be cleaned up.")


def _cleanup_private_job(job_token: str) -> None:
    """Run the exact WSL cleanup transaction for one Main-issued UUID."""

    if _audio._JOB_TOKEN_PATTERN.fullmatch(job_token) is None:
        _fail("Song-cover cleanup identity is invalid.")
    _audio._require_asset(
        Path(sys.executable),
        label="Python runtime",
        expected_bytes=_audio._EXPECTED_PYTHON_BYTES,
        expected_sha256=_audio._EXPECTED_PYTHON_SHA256,
    )
    project_root = _audio._require_directory(
        Path(__file__).resolve().parents[1],
        "Elysia project runtime",
    )
    _run_wsl_private_cleanup(
        _system_wsl_executable(),
        project_root,
        job_token,
    )


def _validate_runtime_assets(
    arguments: argparse.Namespace,
) -> tuple[Path, Path, Optional[Path], Optional[Path]]:
    """Authenticate native tools and conditional Demucs assets before work."""

    _audio._require_asset(
        Path(sys.executable),
        label="Python runtime",
        expected_bytes=_audio._EXPECTED_PYTHON_BYTES,
        expected_sha256=_audio._EXPECTED_PYTHON_SHA256,
    )
    ffmpeg = _audio._require_asset(
        arguments.ffmpeg,
        label="FFmpeg runtime",
        expected_bytes=_audio._EXPECTED_FFMPEG_BYTES,
        expected_sha256=_audio._EXPECTED_FFMPEG_SHA256,
    )
    ffprobe = _audio._require_asset(
        arguments.ffprobe,
        label="FFprobe runtime",
        expected_bytes=_audio._EXPECTED_FFPROBE_BYTES,
        expected_sha256=_audio._EXPECTED_FFPROBE_SHA256,
    )
    if arguments.source_mode != "song":
        return ffmpeg, ffprobe, None, None
    demucs_site = _audio._require_directory(
        arguments.demucs_site,
        "Song-separation package",
    )
    _audio._remove_bytecode_caches(
        demucs_site,
        label="Song-separation package",
    )
    _audio._require_singing_runtime(demucs_site)
    torch_home = _audio._require_directory(
        arguments.torch_home,
        "Demucs model cache",
    )
    _audio._require_asset(
        torch_home / "hub" / "checkpoints" / "955717e8-8726e21a.th",
        label="Demucs separation model",
        expected_bytes=_audio._EXPECTED_DEMUCS_MODEL_BYTES,
        expected_sha256=_audio._EXPECTED_DEMUCS_MODEL_SHA256,
    )
    return ffmpeg, ffprobe, demucs_site, torch_home


def _prepare_sources(
    arguments: argparse.Namespace,
    *,
    job_root: Path,
    ffmpeg: Path,
    ffprobe: Path,
    demucs_site: Optional[Path],
    torch_home: Optional[Path],
) -> tuple[float, int, Path, Path, list[tuple[Path, str]], list[tuple[Path, str]]]:
    """Validate, separate, and canonically prepare target vocal/accompaniment."""

    scratch_trees: list[tuple[Path, str]] = []
    scratch_files: list[tuple[Path, str]] = []
    if arguments.source_mode == "song":
        source, duration = _audio._validate_source(arguments.input, ffprobe)
        target_samples = _audio._target_samples(duration)
        decoded_song = job_root / "source.wav"
        _require_absent(decoded_song, label="Decoded-song scratch")
        _audio._decode_song(ffmpeg, source, duration, decoded_song)
        scratch_files.append((decoded_song, "Decoded-song scratch"))
        _emit("separating", 15, "Preparing vocals and accompaniment")
        if demucs_site is None or torch_home is None:
            _fail("The song-separation runtime is unavailable.")
        separation_root = job_root / "separated"
        _require_absent(separation_root, label="Separated-audio scratch")
        vocals, accompaniment = _audio._separate_vocals(
            decoded_song,
            separation_root,
            demucs_site,
            torch_home,
        )
        scratch_trees.append((separation_root, "Separated-audio scratch"))
    else:
        vocal_source, vocal_duration = _audio._validate_source(
            arguments.vocal_input,
            ffprobe,
        )
        accompaniment_source, accompaniment_duration = _audio._validate_source(
            arguments.accompaniment_input,
            ffprobe,
        )
        duration = max(vocal_duration, accompaniment_duration)
        target_samples = _audio._target_samples(duration)
        _emit("separating", 15, "Preparing vocals and accompaniment")
        provided_root = job_root / "provided-stems"
        _require_absent(provided_root, label="Provided-stem scratch")
        provided_root = _audio._require_or_create_directory(
            provided_root,
            label="Provided-stem scratch",
        )
        scratch_trees.append((provided_root, "Provided-stem scratch"))
        vocals = provided_root / "vocals.wav"
        accompaniment = provided_root / "accompaniment.wav"
        _audio._decode_song(ffmpeg, vocal_source, duration, vocals)
        _audio._decode_song(
            ffmpeg,
            accompaniment_source,
            duration,
            accompaniment,
        )

    target_vocal = job_root / "target_vocal.wav"
    _require_absent(target_vocal, label="Prepared target vocal")
    if arguments.key_shift_semitones == 0:
        _audio._prepare_mono_vocals(
            ffmpeg,
            vocals,
            target_vocal,
            target_samples=target_samples,
        )
    else:
        unshifted_vocal = job_root / "target-vocal-unshifted.wav"
        shifted_vocal = job_root / "target-vocal-shifted.wav"
        shifted_accompaniment = job_root / "accompaniment-key-shifted.wav"
        for path, label in (
            (unshifted_vocal, "Unshifted target-vocal scratch"),
            (shifted_vocal, "Shifted target-vocal scratch"),
            (shifted_accompaniment, "Key-shifted accompaniment scratch"),
        ):
            _require_absent(path, label=label)
            scratch_files.append((path, label))
        _audio._prepare_mono_vocals(
            ffmpeg,
            vocals,
            unshifted_vocal,
            target_samples=target_samples,
        )
        _audio._transpose_audio(
            ffmpeg,
            unshifted_vocal,
            shifted_vocal,
            key_shift_semitones=arguments.key_shift_semitones,
            target_samples=target_samples,
        )
        _audio._prepare_mono_vocals(
            ffmpeg,
            shifted_vocal,
            target_vocal,
            target_samples=target_samples,
        )
        _audio._transpose_audio(
            ffmpeg,
            accompaniment,
            shifted_accompaniment,
            key_shift_semitones=arguments.key_shift_semitones,
            target_samples=target_samples,
        )
        accompaniment = shifted_accompaniment
    scratch_files.append((target_vocal, "Prepared target vocal"))
    return (
        duration,
        target_samples,
        target_vocal,
        accompaniment,
        scratch_trees,
        scratch_files,
    )


def _cleanup_success(
    job_root: Path,
    *,
    scratch_trees: Sequence[tuple[Path, str]],
    scratch_files: Sequence[tuple[Path, str]],
) -> None:
    """Remove every private lyric and intermediate after final outputs exist."""

    for scratch_root, label in scratch_trees:
        try:
            os.lstat(scratch_root)
        except FileNotFoundError:
            continue
        _audio._remove_safe_tree(scratch_root, label=label)
    for scratch_file, label in scratch_files:
        _audio._unlink_safe_file(scratch_file, label=label)
    for name in _LYRICS_NAMES:
        _audio._unlink_safe_file(
            job_root / name,
            label="Private lyrics asset",
        )
    numba_cache = job_root / "numba-cache"
    try:
        os.lstat(numba_cache)
    except FileNotFoundError:
        pass
    else:
        _audio._remove_safe_tree(numba_cache, label="Numba cache scratch")
    _require_exact_job_layout(
        job_root,
        allowed_files=_FINAL_JOB_FILES,
        required_files=_FINAL_JOB_FILES,
        label="Completed song-cover job",
    )


def _run_job(arguments: argparse.Namespace) -> None:
    """Execute one native-to-WSL lyrics-driven cover transaction."""

    if _audio._JOB_TOKEN_PATTERN.fullmatch(arguments.job_token) is None:
        _fail("Song-cover job identity is invalid.")
    _audio._validate_source_arguments(arguments)
    job_root = Path(os.path.abspath(str(arguments.job_dir)))
    if job_root.name != arguments.job_token:
        _fail("Song-cover job directory is invalid.")
    job_root = _audio._require_directory(job_root, "Song-cover job directory")
    _require_exact_job_layout(
        job_root,
        allowed_files=_INITIAL_JOB_FILES,
        required_files=_REQUIRED_INITIAL_JOB_FILES,
        label="Prepared song-cover job",
    )
    _require_single_link_file(
        job_root / "lyrics.lrc",
        label="Synchronized lyrics",
        maximum_bytes=_MAX_LYRICS_BYTES,
    )
    _require_optional_single_link_file(
        job_root / "lyrics.txt",
        label="Plain lyrics",
        maximum_bytes=_MAX_LYRICS_BYTES,
    )
    _require_single_link_file(
        job_root / "lyrics-manifest.json",
        label="Lyrics provenance",
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )
    for name in (_GENERATED_VOCAL_NAME, "elysia-cover.wav", "elysia-cover.mp3"):
        _require_absent(job_root / name, label="Song-cover output")

    _emit("validating", 5, "Reading the selected audio")
    ffmpeg, ffprobe, demucs_site, torch_home = _validate_runtime_assets(arguments)
    (
        duration,
        target_samples,
        target_vocal,
        accompaniment,
        scratch_trees,
        scratch_files,
    ) = _prepare_sources(
        arguments,
        job_root=job_root,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        demucs_site=demucs_site,
        torch_home=torch_home,
    )
    consonant_layer = job_root / "consonant-layer.wav"
    _require_absent(consonant_layer, label="Consonant-layer scratch")
    _audio._create_consonant_layer(
        ffmpeg,
        target_vocal,
        consonant_layer,
        target_samples=target_samples,
    )
    scratch_files.append((consonant_layer, "Consonant-layer scratch"))

    _audio._release_gpu_cache()
    project_root = _audio._require_directory(
        Path(__file__).resolve().parents[1],
        "Elysia project runtime",
    )
    wsl = _system_wsl_executable()
    _run_wsl_bridge(wsl, project_root, job_root)
    generated_vocal = _require_single_link_file(
        job_root / _GENERATED_VOCAL_NAME,
        label="Generated Elysia vocal",
        maximum_bytes=_audio._MAX_DECODED_BYTES,
    )
    scratch_files.append((generated_vocal, "Generated Elysia vocal"))

    _audio._release_gpu_cache()
    _emit("mixing", 90, "Mixing Elysia with the accompaniment")
    wav_output = job_root / "elysia-cover.wav"
    playback_output = job_root / "elysia-cover.mp3"
    _audio._mix_cover(
        ffmpeg,
        generated_vocal,
        consonant_layer,
        accompaniment,
        wav_output,
        playback_output,
        target_samples=target_samples,
    )
    _cleanup_success(
        job_root,
        scratch_trees=scratch_trees,
        scratch_files=scratch_files,
    )
    _emit("complete", 100, f"{duration:.3f}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one bounded local lyrics-driven cover and return process status."""

    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    cleanup_request = (
        len(raw_arguments) == 2
        and raw_arguments[0] == _CLEANUP_ARGUMENT
    )
    # Keep argparse outside the failure-collapsing boundary so its built-in
    # --help success retains exit status 0 during runtime compatibility checks.
    arguments = None if cleanup_request else _parser().parse_args(raw_arguments)
    try:
        if cleanup_request:
            _cleanup_private_job(raw_arguments[1])
            print(_WORKER_CLEANUP_EVENT, flush=True)
            return 0
        if arguments is None:
            _fail("Lyrics-driven singing request is invalid.")
        _run_job(arguments)
        return 0
    except (_SvsWorkerFailure, _audio._SongCoverFailure) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("Lyrics-driven singing was cancelled.", file=sys.stderr, flush=True)
        return 130
    except BaseException:
        # Vendor exceptions can include paths or transcribed lyric fragments.
        # Collapse them at the process boundary rather than printing traceback.
        print(
            "Lyrics-driven singing stopped unexpectedly.",
            file=sys.stderr,
            flush=True,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
