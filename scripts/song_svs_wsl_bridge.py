"""Bridge one mounted Windows SVS job into the private WSL runtime.

The bridge accepts only a mounted Windows directory with a canonical UUIDv4
name and fixed input filenames. Inputs are copied through no-follow file
descriptors into a mode-0700 Linux job, where :mod:`scripts.song_svs_runtime`
performs the reviewed offline pipeline. The generated vocal is staged back on
the mounted filesystem and atomically published only after the private Linux
job has been removed. Before either normal or recovery cleanup deletes that
directory, the runtime marks the exact UUID cancelled, terminates its leased
Linux session, and verifies that no live session members remain.

Standard output remains a closed ``ELYSIA_SONG_SVS`` JSON-line protocol. The
runtime's final ``ready`` event is deliberately delayed until the mounted
output exists, preventing a native caller from observing completion before it
can open the result. Paths and vendor diagnostics never enter the protocol.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from collections.abc import Callable
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from types import ModuleType
from typing import Final, NoReturn, Sequence, TextIO, cast


_EVENT_PREFIX: Final = "ELYSIA_SONG_SVS "
_CLEANUP_EVENT_LINE: Final = (
    'ELYSIA_SONG_SVS_CLEANUP {"status":"complete"}'
)
_CLEANUP_ARGUMENT: Final = "--cleanup-private-job"
_JOB_TOKEN_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_VOCAL_BYTES: Final = 256 * 1024 * 1024
_MAX_LYRICS_BYTES: Final = 1024 * 1024
_MAX_PROTOCOL_LINE_BYTES: Final = 2_048
_MAX_PROTOCOL_EVENTS: Final = 128
_MAX_MOUNTINFO_BYTES: Final = 4 * 1024 * 1024
_MAX_MOUNTINFO_LINES: Final = 20_000
_OUTPUT_NAME: Final = "generated_vocal.wav"
_OUTPUT_TEMP_NAME: Final = ".generated_vocal.wav.elysia-tmp"
_ALLOWED_EVENT_STAGES: Final = frozenset(
    {"validating", "transcribing", "aligning", "synthesizing", "ready", "error"}
)
_RUNTIME_MODULE: ModuleType | None = None


class _BridgeFailure(RuntimeError):
    """Carry one stable path-free bridge error code and safe explanation."""

    def __init__(self, code: str, message: str) -> None:
        """Create one failure suitable for the bounded stdout protocol."""

        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _SourceJob:
    """Hold authenticated fixed inputs from one mounted Windows job."""

    root: Path
    token: str
    target_vocal: Path
    synchronized_lyrics: Path
    plain_lyrics: Path | None


class _ProtocolRelay:
    """Validate runtime events and withhold readiness until publication."""

    def __init__(self, destination: TextIO) -> None:
        """Create a bounded line buffer targeting the original stdout stream."""

        self._destination = destination
        self._buffer = ""
        self._event_count = 0
        self._ready_line: str | None = None
        self._saw_error = False

    @property
    def has_ready(self) -> bool:
        """Return whether exactly one valid runtime readiness event was held."""

        return self._ready_line is not None

    @property
    def saw_error(self) -> bool:
        """Return whether the runtime emitted a validated error event."""

        return self._saw_error

    def write(self, value: str) -> int:
        """Accept print fragments and relay only complete validated event lines."""

        if not isinstance(value, str):
            raise TypeError("Protocol output must be text.")
        self._buffer += value
        if len(self._buffer.encode("utf-8")) > _MAX_PROTOCOL_LINE_BYTES:
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._accept_line(line.rstrip("\r"))
        return len(value)

    def flush(self) -> None:
        """Flush already validated events without releasing partial text."""

        self._destination.flush()

    def finish(self) -> None:
        """Require the runtime to end on a complete protocol line."""

        if self._buffer:
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")

    def publish_ready(self) -> None:
        """Release the held ready event after mounted output publication."""

        if self._ready_line is None:
            _fail("invalid_protocol", "The private singing runtime omitted completion.")
        self._destination.write(self._ready_line + "\n")
        self._destination.flush()
        self._ready_line = None

    def _accept_line(self, line: str) -> None:
        """Validate one closed protocol object before forwarding or holding it."""

        if not line or not line.startswith(_EVENT_PREFIX):
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
        try:
            payload = json.loads(line[len(_EVENT_PREFIX) :])
        except (json.JSONDecodeError, UnicodeError) as error:
            raise _BridgeFailure(
                "invalid_protocol",
                "The private singing runtime emitted invalid output.",
            ) from error
        if not isinstance(payload, dict):
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
        allowed_keys = {"message", "progressPercent", "stage", "errorCode"}
        if set(payload) - allowed_keys or not {
            "message",
            "progressPercent",
            "stage",
        }.issubset(payload):
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
        stage = payload.get("stage")
        message = payload.get("message")
        progress = payload.get("progressPercent")
        error_code = payload.get("errorCode")
        if (
            not isinstance(stage, str)
            or stage not in _ALLOWED_EVENT_STAGES
            or not isinstance(message, str)
            or len(message) > 240
            or any(ord(character) < 0x20 for character in message)
            or "/" in message
            or "\\" in message
            or type(progress) is not int
            or not 0 <= progress <= 100
            or (
                error_code is not None
                and (
                    not isinstance(error_code, str)
                    or not 1 <= len(error_code) <= 64
                    or re.fullmatch(r"[a-z0-9_]+", error_code) is None
                )
            )
            or (stage == "error") != (error_code is not None)
        ):
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
        self._event_count += 1
        if self._event_count > _MAX_PROTOCOL_EVENTS:
            _fail("invalid_protocol", "The private singing runtime emitted too many events.")
        canonical_line = _EVENT_PREFIX + json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if stage == "ready":
            if self._ready_line is not None or self._saw_error:
                _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
            self._ready_line = canonical_line
            return
        if self._ready_line is not None or self._saw_error:
            _fail("invalid_protocol", "The private singing runtime emitted invalid output.")
        if stage == "error":
            self._saw_error = True
        self._destination.write(canonical_line + "\n")
        self._destination.flush()


def _fail(code: str, message: str) -> NoReturn:
    """Stop bridge work with one renderer-safe error and no private path."""

    raise _BridgeFailure(code, message)


def _emit_error(code: str, message: str) -> None:
    """Emit one bounded bridge-owned failure in the shared closed protocol."""

    payload = {
        "errorCode": code[:64],
        "message": message[:240],
        "progressPercent": 0,
        "stage": "error",
    }
    print(
        _EVENT_PREFIX
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        flush=True,
    )


def _load_runtime() -> ModuleType:
    """Import the reviewed runtime lazily so bridge failures remain framed."""

    global _RUNTIME_MODULE
    if _RUNTIME_MODULE is not None:
        return _RUNTIME_MODULE
    try:
        loaded = importlib.import_module("scripts.song_svs_runtime")
    except Exception as error:
        raise _BridgeFailure(
            "runtime_unavailable",
            "The private singing runtime is unavailable.",
        ) from error
    _RUNTIME_MODULE = loaded
    return loaded


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows reparse metadata in cross-platform unit tests."""

    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _absolute_without_resolution(path: Path) -> Path:
    """Normalize dot segments without following any filesystem link."""

    if not path.is_absolute():
        _fail("invalid_job", "The mounted singing job path must be absolute.")
    return Path(os.path.abspath(str(path)))


def _require_safe_existing(
    path: Path,
    *,
    label: str,
    require_directory: bool,
    require_single_link: bool = False,
) -> tuple[Path, os.stat_result]:
    """Authenticate every component with lstat and reject path redirections."""

    candidate = _absolute_without_resolution(path)
    try:
        current = Path(candidate.anchor)
        parts = candidate.parts[1:] if candidate.anchor else candidate.parts
        if not parts:
            raise OSError
        final_info: os.stat_result | None = None
        for index, part in enumerate(parts):
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise OSError
            final = index == len(parts) - 1
            if not final:
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError
                continue
            final_info = info
            if require_directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError
            elif not stat.S_ISREG(info.st_mode):
                raise OSError
            if require_single_link and info.st_nlink != 1:
                raise OSError
        if final_info is None:
            raise OSError
    except (OSError, RuntimeError, ValueError) as error:
        raise _BridgeFailure(
            "unsafe_path",
            f"{label} is unavailable or unsafe.",
        ) from error
    return candidate, final_info


def _decode_mountinfo_path(value: str) -> str:
    """Decode the octal escapes used for whitespace and backslashes in mountinfo."""

    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _find_windows_mount(candidate: Path) -> Path | None:
    """Return the enclosing kernel-verified DrvFs mount, if one exists."""

    if os.name != "posix":
        return None
    mountinfo_path = Path("/proc/self/mountinfo")
    try:
        if mountinfo_path.stat().st_size > _MAX_MOUNTINFO_BYTES:
            return None
        mountinfo = mountinfo_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if len(mountinfo.encode("utf-8")) > _MAX_MOUNTINFO_BYTES:
        return None
    lines = mountinfo.splitlines()
    if len(lines) > _MAX_MOUNTINFO_LINES:
        return None
    matches: list[Path] = []
    for line in lines:
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 6 or len(right_fields) < 3:
            continue
        filesystem_type = right_fields[0].casefold()
        descriptor = line.casefold()
        is_drvfs = filesystem_type in {"drvfs", "fuse.drvfs"} or (
            filesystem_type == "9p"
            and ("aname=drvfs" in descriptor or right_fields[1].casefold() == "drvfs")
        )
        if not is_drvfs:
            continue
        mount_point = Path(_decode_mountinfo_path(left_fields[4]))
        try:
            candidate.relative_to(mount_point)
        except ValueError:
            continue
        matches.append(mount_point)
    return max(matches, key=lambda path: len(path.parts), default=None)


def _require_source_file(
    root: Path,
    name: str,
    *,
    maximum_bytes: int,
) -> Path:
    """Require one bounded single-link regular input with a fixed filename."""

    candidate, info = _require_safe_existing(
        root / name,
        label="Managed singing input",
        require_directory=False,
        require_single_link=True,
    )
    if not 1 <= info.st_size <= maximum_bytes:
        _fail("invalid_input", "A managed singing input has an unsupported size.")
    return candidate


def _optional_source_file(
    root: Path,
    name: str,
    *,
    maximum_bytes: int,
) -> Path | None:
    """Return one optional fixed input while rejecting dangling or unsafe entries."""

    candidate = root / name
    try:
        os.lstat(candidate)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _BridgeFailure(
            "unsafe_path",
            "An optional managed singing input is unavailable.",
        ) from error
    return _require_source_file(root, name, maximum_bytes=maximum_bytes)


def _validate_mounted_source_job(argument: str) -> _SourceJob:
    """Authenticate one canonical UUIDv4 job on a mounted Windows filesystem."""

    if not isinstance(argument, str) or not argument:
        _fail("invalid_job", "The mounted singing job is invalid.")
    root, _info = _require_safe_existing(
        Path(argument),
        label="Mounted singing job",
        require_directory=True,
    )
    if _JOB_TOKEN_PATTERN.fullmatch(root.name) is None:
        _fail("invalid_job", "The mounted singing job identifier is invalid.")
    mount_point = _find_windows_mount(root)
    if mount_point is None or root == mount_point:
        _fail("invalid_job", "The singing job is not on a mounted Windows filesystem.")
    return _SourceJob(
        root=root,
        token=root.name,
        target_vocal=_require_source_file(
            root,
            "target_vocal.wav",
            maximum_bytes=_MAX_VOCAL_BYTES,
        ),
        synchronized_lyrics=_require_source_file(
            root,
            "lyrics.lrc",
            maximum_bytes=_MAX_LYRICS_BYTES,
        ),
        plain_lyrics=_optional_source_file(
            root,
            "lyrics.txt",
            maximum_bytes=_MAX_LYRICS_BYTES,
        ),
    )


def _private_jobs_root() -> Path:
    """Create and authenticate the fixed private Linux singing-job parent."""

    runtime_root_provider = getattr(_load_runtime(), "_runtime_root", None)
    if not callable(runtime_root_provider):
        _fail("runtime_unavailable", "The private singing runtime is unavailable.")
    runtime_root = Path(runtime_root_provider())
    jobs_root = runtime_root / "jobs"
    try:
        _require_safe_existing(
            runtime_root,
            label="Private SoulX runtime",
            require_directory=True,
        )
        jobs_root.mkdir(mode=0o700, parents=False, exist_ok=True)
        _jobs_path, info = _require_safe_existing(
            jobs_root,
            label="Private singing job store",
            require_directory=True,
        )
        getuid = cast(Callable[[], int] | None, getattr(os, "getuid", None))
        if os.name == "posix" and (getuid is None or info.st_uid != getuid()):
            raise OSError
        os.chmod(jobs_root, 0o700)
    except (OSError, RuntimeError) as error:
        raise _BridgeFailure(
            "private_job_failed",
            "The private singing job store is unavailable.",
        ) from error
    return jobs_root


def _create_private_job(jobs_root: Path, token: str) -> Path:
    """Create one collision-resistant mode-0700 direct child job directory."""

    if _JOB_TOKEN_PATTERN.fullmatch(token) is None:
        _fail("invalid_job", "The mounted singing job identifier is invalid.")
    job_root = jobs_root / token
    try:
        _require_safe_existing(
            jobs_root,
            label="Private singing job store",
            require_directory=True,
        )
        os.mkdir(job_root, mode=0o700)
        os.chmod(job_root, 0o700)
        info = os.lstat(job_root)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise OSError
        getuid = cast(Callable[[], int] | None, getattr(os, "getuid", None))
        if os.name == "posix" and (
            getuid is None
            or info.st_uid != getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise OSError
    except (OSError, RuntimeError) as error:
        raise _BridgeFailure(
            "private_job_failed",
            "The private singing job could not be created.",
        ) from error
    return job_root


def _copy_exclusive(
    source: Path,
    destination: Path,
    *,
    maximum_bytes: int,
) -> int:
    """Copy one bounded file through no-follow descriptors into a new mode-0600 file.

    Source lstat identity is compared with the opened descriptor to close the
    validation/open race. The copy also enforces its byte bound while reading,
    so a concurrently growing mounted file cannot overrun the private job.
    """

    source_path, source_info = _require_safe_existing(
        source,
        label="Managed singing file",
        require_directory=False,
        require_single_link=True,
    )
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    source_flags |= getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    destination_flags |= getattr(os, "O_BINARY", 0)
    destination_flags |= getattr(os, "O_NOFOLLOW", 0)
    source_fd = -1
    destination_fd = -1
    destination_created = False
    total_bytes = 0
    try:
        source_fd = os.open(source_path, source_flags)
        opened_info = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_info.st_mode)
            or opened_info.st_nlink != 1
            or (opened_info.st_dev, opened_info.st_ino)
            != (source_info.st_dev, source_info.st_ino)
            or not 1 <= opened_info.st_size <= maximum_bytes
        ):
            raise OSError
        destination_fd = os.open(destination, destination_flags, 0o600)
        destination_created = True
        os.fchmod(destination_fd, 0o600)
        while True:
            chunk = os.read(source_fd, min(1024 * 1024, maximum_bytes + 1 - total_bytes))
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > maximum_bytes:
                raise OSError
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise OSError
                view = view[written:]
        if total_bytes != opened_info.st_size:
            raise OSError
        os.fsync(destination_fd)
    except OSError as error:
        if destination_fd >= 0:
            os.close(destination_fd)
            destination_fd = -1
        if destination_created:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise _BridgeFailure(
            "copy_failed",
            "A managed singing file could not be copied safely.",
        ) from error
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)
    return total_bytes


def _copy_inputs(source: _SourceJob, private_job: Path) -> None:
    """Copy only the three allowlisted input names into the private Linux job."""

    copied: list[Path] = []
    try:
        pairs = [
            (source.target_vocal, private_job / "target_vocal.wav", _MAX_VOCAL_BYTES),
            (source.synchronized_lyrics, private_job / "lyrics.lrc", _MAX_LYRICS_BYTES),
        ]
        if source.plain_lyrics is not None:
            pairs.append((source.plain_lyrics, private_job / "lyrics.txt", _MAX_LYRICS_BYTES))
        for origin, destination, maximum_bytes in pairs:
            _copy_exclusive(origin, destination, maximum_bytes=maximum_bytes)
            copied.append(destination)
    except Exception:
        for path in copied:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _invoke_runtime(private_job: Path, destination: TextIO) -> tuple[int, _ProtocolRelay]:
    """Run the reviewed runtime while streaming only validated bounded events."""

    relay = _ProtocolRelay(destination)
    runtime_main = getattr(_load_runtime(), "main", None)
    if not callable(runtime_main):
        _fail("runtime_unavailable", "The private singing runtime is unavailable.")
    with redirect_stdout(relay):
        status = runtime_main([str(private_job)])
    relay.finish()
    if type(status) is not int or status not in {0, 1, 2, 130}:
        _fail("runtime_failed", "The private singing runtime returned invalid status.")
    if status == 0 and (not relay.has_ready or relay.saw_error):
        _fail("invalid_protocol", "The private singing runtime omitted completion.")
    if status != 0 and relay.has_ready:
        _fail("invalid_protocol", "The private singing runtime reported false completion.")
    if status != 0 and not relay.saw_error:
        _fail("invalid_protocol", "The private singing runtime omitted its failure event.")
    return status, relay


def _terminate_private_job_processes(private_job: Path) -> None:
    """Stop only the authenticated session leased by one private UUID job."""

    terminator = getattr(_load_runtime(), "_terminate_job_processes_for_cleanup", None)
    if not callable(terminator):
        _fail("cleanup_failed", "The private singing cleanup runtime is unavailable.")
    try:
        terminator(private_job)
    except BaseException as error:
        raise _BridgeFailure(
            "cleanup_failed",
            "The private singing job could not be stopped safely.",
        ) from error


def _remove_private_job(private_job: Path, jobs_root: Path) -> None:
    """Stop and delete only the exact UUID child created by this bridge."""

    if private_job.parent != jobs_root or _JOB_TOKEN_PATTERN.fullmatch(private_job.name) is None:
        _fail("cleanup_failed", "The private singing job could not be cleaned up.")
    try:
        info = os.lstat(private_job)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _BridgeFailure(
            "cleanup_failed",
            "The private singing job could not be cleaned up.",
        ) from error
    getuid = cast(Callable[[], int] | None, getattr(os, "getuid", None))
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (
            os.name == "posix"
            and (
                getuid is None
                or info.st_uid != getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            )
        )
    ):
        _fail("cleanup_failed", "The private singing job could not be cleaned up.")
    _terminate_private_job_processes(private_job)
    try:
        shutil.rmtree(private_job)
    except OSError as error:
        raise _BridgeFailure(
            "cleanup_failed",
            "The private singing job could not be cleaned up.",
        ) from error


def _cleanup_private_job_by_token(token: str) -> None:
    """Remove one Main-owned private job by canonical UUID and nothing else.

    Electron may have to force-kill the Windows worker and its WSL launcher,
    which prevents the normal bridge transaction from reaching its cleanup
    block.  Recovery therefore reconstructs only the fixed direct child from
    the authenticated runtime root and UUID, terminates its registered Linux
    session, and verifies that no live member remains before deletion.  No
    caller path, process name, glob, or shell command participates.
    """

    if _JOB_TOKEN_PATTERN.fullmatch(token) is None:
        _fail("invalid_job", "The private singing job identifier is invalid.")
    jobs_root = _private_jobs_root()
    _remove_private_job(jobs_root / token, jobs_root)


def _remove_staged_output(path: Path) -> None:
    """Remove only a regular bridge-owned temporary output if it exists."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _BridgeFailure(
            "publish_failed",
            "The generated vocal could not be staged.",
        ) from error
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _fail("publish_failed", "The generated vocal staging path is unsafe.")
    try:
        path.unlink()
    except OSError as error:
        raise _BridgeFailure(
            "publish_failed",
            "The generated vocal staging path could not be cleared.",
        ) from error


def _stage_output(private_job: Path, mounted_job: Path) -> Path:
    """Copy the validated private result to a non-visible mounted temp file."""

    generated, _info = _require_safe_existing(
        private_job / _OUTPUT_NAME,
        label="Generated singing vocal",
        require_directory=False,
        require_single_link=True,
    )
    staged = mounted_job / _OUTPUT_TEMP_NAME
    _remove_staged_output(staged)
    _copy_exclusive(generated, staged, maximum_bytes=_MAX_VOCAL_BYTES)
    return staged


def _publish_staged_output(staged: Path, destination: Path) -> None:
    """Atomically replace only a safe fixed output after private cleanup."""

    _staged_path, staged_info = _require_safe_existing(
        staged,
        label="Staged singing vocal",
        require_directory=False,
        require_single_link=True,
    )
    try:
        existing = os.lstat(destination)
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise _BridgeFailure(
            "publish_failed",
            "The generated vocal destination is unavailable.",
        ) from error
    if existing is not None and (
        not stat.S_ISREG(existing.st_mode)
        or stat.S_ISLNK(existing.st_mode)
        or existing.st_nlink != 1
    ):
        _fail("publish_failed", "The generated vocal destination is unsafe.")
    try:
        os.replace(staged, destination)
    except OSError as error:
        raise _BridgeFailure(
            "publish_failed",
            "The generated vocal could not be published.",
        ) from error
    _destination_path, destination_info = _require_safe_existing(
        destination,
        label="Generated singing vocal",
        require_directory=False,
        require_single_link=True,
    )
    if destination_info.st_size != staged_info.st_size:
        _fail("publish_failed", "The generated vocal could not be verified.")


def _run_bridge(argument: str, destination: TextIO) -> int:
    """Stage, run, publish, and unconditionally clean one mounted singing job."""

    source = _validate_mounted_source_job(argument)
    jobs_root = _private_jobs_root()
    private_job = _create_private_job(jobs_root, source.token)
    staged_output: Path | None = None
    runtime_status: int | None = None
    relay: _ProtocolRelay | None = None
    pending_error: BaseException | None = None
    try:
        _copy_inputs(source, private_job)
        runtime_status, relay = _invoke_runtime(private_job, destination)
        if runtime_status == 0:
            staged_output = _stage_output(private_job, source.root)
    except BaseException as error:
        pending_error = error
    try:
        _remove_private_job(private_job, jobs_root)
    except BaseException as cleanup_error:
        pending_error = cleanup_error

    if pending_error is not None:
        if staged_output is not None:
            try:
                _remove_staged_output(staged_output)
            except _BridgeFailure:
                pass
        raise pending_error
    if runtime_status is None or relay is None:
        _fail("runtime_failed", "The private singing runtime did not start.")
    if runtime_status != 0:
        return runtime_status
    if staged_output is None:
        _fail("publish_failed", "The generated vocal was not staged.")
    try:
        _publish_staged_output(staged_output, source.root / _OUTPUT_NAME)
    except BaseException:
        try:
            _remove_staged_output(staged_output)
        except _BridgeFailure:
            pass
        raise
    relay.publish_ready()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one mounted-to-private WSL bridge and return a process status code."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    cleanup_request = (
        len(arguments) == 2
        and arguments[0] == _CLEANUP_ARGUMENT
    )
    if not cleanup_request and len(arguments) != 1:
        _emit_error("invalid_request", "Exactly one mounted singing job is required.")
        return 2
    try:
        if cleanup_request:
            _cleanup_private_job_by_token(arguments[1])
            print(_CLEANUP_EVENT_LINE, flush=True)
            return 0
        return _run_bridge(arguments[0], sys.stdout)
    except _BridgeFailure as error:
        _emit_error(error.code, str(error))
        return 1
    except KeyboardInterrupt:
        _emit_error("cancelled", "Singing synthesis was cancelled.")
        return 130
    except BaseException:
        # Filesystem and runtime exceptions can contain private paths. Collapse
        # every unclassified failure before it reaches the renderer protocol.
        _emit_error("bridge_failed", "The private singing bridge stopped unexpectedly.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
