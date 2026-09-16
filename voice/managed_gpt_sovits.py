"""Own one challenge-bound, fail-closed local GPT-SoVITS worker lease.

This parent-side boundary deliberately remains separate from the legacy HTTP
adapter.  It holds Windows no-write/no-delete guards for every selected asset
and runtime anchor, launches the isolated worker inside a kill-on-close Job
Object, and accepts audio only after an exact challenge and binding handshake.

The extracted GPT-SoVITS distribution and processes already running as the
same Windows user are trusted inputs at lease start.  The measured runtime
anchors prove parent/worker consistency, not complete dependency provenance;
the roughly sixty-thousand-file third-party runtime is neither fully manifested
nor made immutable here.  The low-level lease therefore never claims complete
binding provenance and never enables synthesis caching.  The queue's separate
``binding_verified`` bit means only that its private factory owns this Elysia
lease; it must not be interpreted as third-party supply-chain attestation.

Construction and status inspection are pure in-memory operations.  Filesystem
inspection, hashing, handle acquisition, and process creation happen only in
``acquire_lease``.  The module imports safely on non-Windows hosts, where the
runtime reports a stable unavailable state without probing the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import ntpath
import os
from pathlib import Path, PurePosixPath
from queue import Empty, Queue
import re
import secrets
import stat
from threading import Condition, Lock, RLock, Thread
from time import monotonic
from typing import Any, BinaryIO, Callable, Final, Literal, Protocol, TypeAlias, cast

from scripts.gpt_sovits_protocol import (
    FrameKind,
    MANAGED_RUNTIME_IMPORT_RELATIVE_ENTRIES,
    MANAGED_RUNTIME_MANIFEST_RELATIVE_FILES,
    PROTOCOL_MAX_PAYLOAD_BYTES,
    ProtocolFrame,
    read_frame,
    write_frame,
)
from scripts.gpt_sovits_worker import compute_runtime_manifest_digest

from ._windows_managed_process import launch_windows_managed_process
from . import profiles as _profiles_module
from .profiles import LocalVoiceAssetDeclaration, _LocalVoiceSelection
from .synthesis import (
    SynthesisRequest,
    SynthesisResult,
    SynthesisValidationError,
)


ManagedGptSovitsDevice: TypeAlias = Literal["cpu", "cuda"]
ManagedGptSovitsState: TypeAlias = Literal[
    "idle",
    "starting",
    "ready",
    "poisoned",
    "closed",
    "unavailable",
]

MANAGED_GPT_SOVITS_MAX_TIMEOUT_SECONDS: Final = 600.0
MANAGED_GPT_SOVITS_MAX_REQUESTS_PER_LEASE: Final = 65_535
MANAGED_GPT_SOVITS_MAX_CANCEL_TOMBSTONES: Final = 4_096

_SCHEMA_VERSION: Final = 1
_BINDING_DOMAIN: Final = b"ELYTTS-BINDING-V1\0"
_RUNTIME_MANIFEST_DOMAIN: Final = b"ELYTTS-RUNTIME-MANIFEST-V1\0"
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")
_VOLUME_GUID_PATH_PATTERN: Final = re.compile(
    r"\\\\\?\\(Volume\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\})"
    r"(\\.*)?\Z"
)
_NATIVE_FIXED_VOLUME_PATTERN: Final = re.compile(
    r"\\Device\\HarddiskVolume[1-9][0-9]*\Z", re.IGNORECASE
)
_MAPPED_DRIVE_PATH_PATTERN: Final = re.compile(r"[D-Z]:\\.+\Z", re.IGNORECASE)
_ERROR_CODES: Final = frozenset(
    {
        "protocol_invalid",
        "binding_failed",
        "engine_failed",
        "synthesis_failed",
    }
)
_READY_FIELDS: Final = frozenset(
    {"binding_sha256", "challenge", "schema"}
)
_AUDIO_FIELDS: Final = frozenset(
    {"challenge", "format", "sample_rate", "schema"}
)
_STOPPED_FIELDS: Final = frozenset({"challenge", "schema"})
_ERROR_FIELDS: Final = frozenset({"code"})
_RUNTIME_MANIFEST_RELATIVE_FILES: Final = (
    MANAGED_RUNTIME_MANIFEST_RELATIVE_FILES
)
_BOOTSTRAP_COPY_ROLES: Final = (
    ("runtime-python", "python.exe"),
    ("runtime-python-dll", "python39.dll"),
    ("runtime-python-abi-dll", "python3.dll"),
    ("runtime-vcruntime", "vcruntime140.dll"),
    ("runtime-vcruntime-1", "vcruntime140_1.dll"),
    ("runtime-libffi", "libffi-7.dll"),
    ("runtime-libcrypto", "libcrypto-1_1.dll"),
    ("runtime-libssl", "libssl-1_1.dll"),
    ("runtime-sqlite", "sqlite3.dll"),
    ("runtime-tcl", "tcl86t.dll"),
    ("runtime-tk", "tk86t.dll"),
    ("runtime-hashlib-extension", "_hashlib.pyd"),
    ("runtime-ssl-extension", "_ssl.pyd"),
    ("runtime-sqlite-extension", "_sqlite3.pyd"),
    ("runtime-ctypes-extension", "_ctypes.pyd"),
    ("runtime-socket-extension", "_socket.pyd"),
    ("runtime-select-extension", "select.pyd"),
    ("runtime-unicode-extension", "unicodedata.pyd"),
)
_BOOTSTRAP_SYS_PATH_RELATIVE_ENTRIES: Final = (
    MANAGED_RUNTIME_IMPORT_RELATIVE_ENTRIES
)
_HASH_CHUNK_BYTES: Final = 1024 * 1024
_MAX_CANDIDATE_ANCHOR_BYTES: Final = 8 * 1024 * 1024 * 1024
_MIN_SAMPLE_RATE: Final = 8_000
_MAX_SAMPLE_RATE: Final = 192_000
_BUILTIN_PATH_TYPE: Final = type(Path())
_CANONICAL_WORKER_SCRIPT: Final = Path(
    compute_runtime_manifest_digest.__globals__["_SCRIPT_PATH"]
)
_CATALOG_SELECTION_PROVENANCE: Final = _profiles_module._SELECTION_PROVENANCE
_LEASE_ISSUER_SEAL: Final = object()
_TEST_DEPENDENCY_SEAL: Final = object()
_QUARANTINED_GUARDS: list[_FileGuard] = []
_QUARANTINED_GUARDS_LOCK = Lock()
_DOS_DEVICE_LOCK = RLock()
_GLOBAL_CLEANUP_POISONED = False
_GLOBAL_CLEANUP_PENDING = 0
_EMERGENCY_QUARANTINE_GUARD: _FileGuard | None = None
_EMERGENCY_QUARANTINE_ERROR_HEAD: BaseException | None = None
_EMERGENCY_CLEANUP_OWNER_HEAD: _ProcessTransportGuardOwner | None = None

_CONFIG_INVALID_MESSAGE = "Managed GPT-SoVITS configuration is invalid."
_REQUEST_INVALID_MESSAGE = "Managed GPT-SoVITS synthesis input is invalid."
_UNAVAILABLE_MESSAGE = "Managed GPT-SoVITS is unavailable."
_PROTOCOL_MESSAGE = "The managed GPT-SoVITS worker response is invalid."
_STARTUP_MESSAGE = "The managed GPT-SoVITS worker could not start safely."
_SYNTHESIS_MESSAGE = "Managed GPT-SoVITS synthesis failed."
_CANCELLED_MESSAGE = "Managed GPT-SoVITS synthesis was cancelled before start."


class ManagedGptSovitsError(Exception):
    """Base class for stable, secret-safe managed-runtime failures."""


class ManagedGptSovitsValidationError(ManagedGptSovitsError):
    """Report invalid public configuration or synthesis input."""


class ManagedGptSovitsUnavailableError(ManagedGptSovitsError):
    """Report a closed, poisoned, unsupported, or occupied runtime."""


class ManagedGptSovitsProtocolError(ManagedGptSovitsError):
    """Report a response that failed the private worker contract."""


class ManagedGptSovitsStartupError(ManagedGptSovitsError):
    """Report a failed guarded launch or READY handshake."""


class ManagedGptSovitsSynthesisError(ManagedGptSovitsError):
    """Report failure after one valid synthesis request was accepted."""


class ManagedGptSovitsCancelledError(ManagedGptSovitsError):
    """Report a pre-start operation that its exact token already cancelled."""


class _DeadlineExpired(Exception):
    """Mark one internal operation that exceeded its shared deadline."""


class _ManagedProcess(Protocol):
    """Describe the sanitized lifecycle surface returned by the launcher."""

    @property
    def pid(self) -> int:
        """Return the root child identifier for post-launch image attestation."""

    def terminate(self, timeout_seconds: float = 5.0) -> bool:
        """Kill the complete process tree and release native ownership."""


class _FileGuard(Protocol):
    """Describe the opaque held-file guard needed by this boundary."""

    def close(self) -> None:
        """Release file handles after the worker tree has terminated."""


class _Transport(Protocol):
    """Describe parent streams and the three child launch handles."""

    request_stream: BinaryIO
    response_stream: BinaryIO
    stdin_handle: int
    stdout_handle: int
    stderr_handle: int

    def close_child_ends(self) -> None:
        """Release the parent's copies of handles duplicated into the child."""

    def close(self) -> None:
        """Close every parent and not-yet-released child endpoint."""


@dataclass(frozen=True, slots=True, repr=False)
class ManagedGptSovitsConfig:
    """Configure one fixed managed runtime without touching the filesystem."""

    runtime_root: Path
    worker_script: Path
    startup_timeout_seconds: float = 300.0
    synthesis_timeout_seconds: float = 180.0
    stop_timeout_seconds: float = 5.0
    device: ManagedGptSovitsDevice = "cuda"
    seed: int = 42

    def __post_init__(self) -> None:
        """Reject mutable, relative, normalized-away, or unbounded values."""

        if not _is_lexical_absolute_path(self.runtime_root):
            raise ManagedGptSovitsValidationError(_CONFIG_INVALID_MESSAGE)
        if (
            not _is_lexical_absolute_path(self.worker_script)
            or self.worker_script.suffix.casefold() != ".py"
        ):
            raise ManagedGptSovitsValidationError(_CONFIG_INVALID_MESSAGE)
        for timeout in (
            self.startup_timeout_seconds,
            self.synthesis_timeout_seconds,
            self.stop_timeout_seconds,
        ):
            _require_timeout(timeout)
        if type(self.device) is not str or self.device not in ("cpu", "cuda"):
            raise ManagedGptSovitsValidationError(_CONFIG_INVALID_MESSAGE)
        if (
            type(self.seed) is not int
            or not 0 <= self.seed <= 2_147_483_647
        ):
            raise ManagedGptSovitsValidationError(_CONFIG_INVALID_MESSAGE)
        # Snapshot every accepted value into exact built-in immutable types.
        # Later launch decisions therefore never re-enter a Path/number/string
        # subclass with dynamic conversion or comparison behavior.
        object.__setattr__(self, "runtime_root", Path(str(self.runtime_root)))
        object.__setattr__(self, "worker_script", Path(str(self.worker_script)))
        object.__setattr__(
            self,
            "startup_timeout_seconds",
            float(self.startup_timeout_seconds),
        )
        object.__setattr__(
            self,
            "synthesis_timeout_seconds",
            float(self.synthesis_timeout_seconds),
        )
        object.__setattr__(
            self,
            "stop_timeout_seconds",
            float(self.stop_timeout_seconds),
        )
        object.__setattr__(self, "device", str(self.device))
        object.__setattr__(self, "seed", int(self.seed))

    def __repr__(self) -> str:
        """Suppress deployment paths while exposing bounded policy choices."""

        return (
            f"{type(self).__name__}(device={self.device!r}, "
            f"startup_timeout_seconds={self.startup_timeout_seconds!r}, "
            f"synthesis_timeout_seconds={self.synthesis_timeout_seconds!r}, "
            f"stop_timeout_seconds={self.stop_timeout_seconds!r})"
        )


@dataclass(frozen=True, slots=True)
class ManagedGptSovitsStatus:
    """Expose only an in-memory lifecycle snapshot and current availability."""

    state: ManagedGptSovitsState
    available: bool
    cache_eligible: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class _SelectionAssetSnapshot:
    """Freeze one catalog asset into exact module-owned primitive values."""

    candidate_path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class _SelectionSnapshot:
    """Detach every selection field from its caller-retained source object."""

    profile_id: str
    emotion: str
    base_url: str
    gpt_weights: _SelectionAssetSnapshot
    sovits_weights: _SelectionAssetSnapshot
    reference_audio: _SelectionAssetSnapshot
    prompt_text: str
    prompt_language: str
    speed_factor: float
    audio_format: str


def _snapshot_selection_asset(value: object) -> tuple[
    LocalVoiceAssetDeclaration, _SelectionAssetSnapshot
]:
    """Clone and revalidate one exact catalog asset without retaining aliases."""

    if type(value) is not LocalVoiceAssetDeclaration:
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    raw_root = value.asset_root
    raw_relative = value.relative_path
    raw_size = value.size_bytes
    raw_sha256 = value.sha256
    if (
        type(raw_root) is not _BUILTIN_PATH_TYPE
        or type(raw_relative) is not PurePosixPath
        or type(raw_size) is not int
        or type(raw_sha256) is not str
    ):
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    try:
        clone = LocalVoiceAssetDeclaration(
            asset_root=Path(str(raw_root)),
            relative_path=PurePosixPath(str(raw_relative)),
            size_bytes=int(raw_size),
            sha256=str(raw_sha256),
        )
    except (TypeError, ValueError):
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE) from None
    if (
        value.asset_root != raw_root
        or value.relative_path != raw_relative
        or value.size_bytes != raw_size
        or value.sha256 != raw_sha256
    ):
        # Deliberate ``object.__setattr__`` can bypass a frozen dataclass. A
        # second read detects ordinary concurrent mutation during the snapshot;
        # no caller-owned object is consulted again after this point.
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    return clone, _SelectionAssetSnapshot(
        candidate_path=Path(str(clone.candidate_path)),
        size_bytes=int(clone.size_bytes),
        sha256=str(clone.sha256),
    )


def _snapshot_selection(value: object) -> _SelectionSnapshot:
    """Validate provenance then take one private immutable selection snapshot."""

    if (
        type(value) is not _LocalVoiceSelection
        or value._provenance is not _CATALOG_SELECTION_PROVENANCE
    ):
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    raw_profile_id = value.profile_id
    raw_emotion = value.emotion
    raw_base_url = value.base_url
    raw_gpt = value.gpt_weights
    raw_sovits = value.sovits_weights
    raw_reference = value.reference_audio
    raw_prompt = value.prompt_text
    raw_language = value.prompt_language
    raw_speed = value.speed_factor
    raw_format = value.audio_format
    if (
        any(
            type(item) is not str
            for item in (
                raw_profile_id,
                raw_emotion,
                raw_base_url,
                raw_prompt,
                raw_language,
                raw_format,
            )
        )
        or type(raw_speed) is not float
    ):
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    gpt_clone, gpt_snapshot = _snapshot_selection_asset(raw_gpt)
    sovits_clone, sovits_snapshot = _snapshot_selection_asset(raw_sovits)
    reference_clone, reference_snapshot = _snapshot_selection_asset(raw_reference)
    try:
        clone = _LocalVoiceSelection(
            profile_id=str(raw_profile_id),
            emotion=str(raw_emotion),
            base_url=str(raw_base_url),
            gpt_weights=gpt_clone,
            sovits_weights=sovits_clone,
            reference_audio=reference_clone,
            prompt_text=str(raw_prompt),
            prompt_language=cast(Any, str(raw_language)),
            speed_factor=float(raw_speed),
            audio_format=cast(Any, str(raw_format)),
            _provenance=_CATALOG_SELECTION_PROVENANCE,
        )
    except (TypeError, ValueError):
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE) from None
    if (
        value._provenance is not _CATALOG_SELECTION_PROVENANCE
        or value.profile_id != raw_profile_id
        or value.emotion != raw_emotion
        or value.base_url != raw_base_url
        or value.gpt_weights is not raw_gpt
        or value.sovits_weights is not raw_sovits
        or value.reference_audio is not raw_reference
        or value.prompt_text != raw_prompt
        or value.prompt_language != raw_language
        or value.speed_factor != raw_speed
        or value.audio_format != raw_format
    ):
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    return _SelectionSnapshot(
        profile_id=str(clone.profile_id),
        emotion=str(clone.emotion),
        base_url=str(clone.base_url),
        gpt_weights=gpt_snapshot,
        sovits_weights=sovits_snapshot,
        reference_audio=reference_snapshot,
        prompt_text=str(clone.prompt_text),
        prompt_language=str(clone.prompt_language),
        speed_factor=float(clone.speed_factor),
        audio_format=str(clone.audio_format),
    )


def _is_lexical_absolute_path(value: object) -> bool:
    """Validate one normalized absolute ``Path`` without filesystem access."""

    if type(value) is not _BUILTIN_PATH_TYPE:
        return False
    rendered = str(value)
    return (
        bool(rendered)
        and "\x00" not in rendered
        and len(rendered) <= 32_767
        and value.is_absolute()
        and os.path.normpath(rendered) == rendered
    )


def _require_timeout(value: object) -> float:
    """Return one finite positive timeout within the shared process ceiling."""

    if type(value) not in (int, float):
        raise ManagedGptSovitsValidationError(_CONFIG_INVALID_MESSAGE)
    numeric = float(cast(int | float, value))
    if (
        not math.isfinite(numeric)
        or not 0.01 <= numeric <= MANAGED_GPT_SOVITS_MAX_TIMEOUT_SECONDS
    ):
        raise ManagedGptSovitsValidationError(_CONFIG_INVALID_MESSAGE)
    return numeric


def _remaining(deadline: float) -> float:
    """Return time left in one aggregate operation or raise its sentinel."""

    value = deadline - monotonic()
    if value <= 0.0:
        raise _DeadlineExpired
    return value


def _call_with_deadline(
    operation: Callable[[], Any], timeout_seconds: float
) -> Any:
    """Run potentially blocking pipe I/O behind one strict daemon deadline.

    Python cannot portably cancel a thread blocked in a binary stream.  The
    owner therefore poisons and transfers the complete resource generation when
    this helper expires. Process-first cleanup terminates the Job so child-handle
    closure wakes OS-backed reads before parent pipes and guards are released;
    the daemon fallback preserves caller progress for a hostile injected stream.
    """

    results: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def invoke() -> None:
        """Capture a value or exception without rendering it in diagnostics."""

        try:
            results.put((True, operation()))
        except BaseException as error:
            results.put((False, error))

    Thread(target=invoke, name="elysia-managed-tts-io", daemon=True).start()
    try:
        succeeded, value = results.get(timeout=timeout_seconds)
    except Empty:
        raise _DeadlineExpired from None
    if succeeded:
        return value
    raise value


def _write_with_deadline(
    stream: BinaryIO,
    frame: ProtocolFrame,
    timeout_seconds: float,
    writer: Callable[[BinaryIO, ProtocolFrame], None],
) -> None:
    """Write and flush one complete frame inside the caller's deadline."""

    def emit() -> None:
        """Keep the frame contiguous and visible before reporting success."""

        writer(stream, frame)
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()

    _call_with_deadline(emit, timeout_seconds)


def _read_with_deadline(
    stream: BinaryIO,
    timeout_seconds: float,
    reader: Callable[[BinaryIO], ProtocolFrame],
) -> ProtocolFrame:
    """Read exactly one protocol frame without allowing an unbounded wait."""

    value = _call_with_deadline(lambda: reader(stream), timeout_seconds)
    if type(value) is not ProtocolFrame:
        raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
    return value


def _hash_candidate_file(path: Path) -> tuple[int, str]:
    """Measure one untrusted runtime-anchor candidate before guard acquisition.

    These values are deliberately called candidates: pathname inspection and
    hashing alone cannot prevent a replacement race.  No binding claim is made
    until the file-guard layer reopens, verifies, and holds the complete set.
    """

    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise OSError
        digest = hashlib.sha256()
        total = 0
        with path.open("rb", buffering=0) as stream:
            while True:
                chunk = stream.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_CANDIDATE_ANCHOR_BYTES:
                    raise OSError
                digest.update(chunk)
        after = os.lstat(path)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            getattr(before, "st_mtime_ns", 0),
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            getattr(after, "st_mtime_ns", 0),
        )
        if total <= 0 or total != before.st_size or identity_before != identity_after:
            raise OSError
        return total, digest.hexdigest()
    except (OSError, RuntimeError, ValueError):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None


def _default_guard_acquirer(declarations: tuple[Any, ...]) -> _FileGuard:
    """Acquire the Windows guard lazily so non-Windows imports stay harmless."""

    from ._windows_file_guard import WindowsReadOnlyFileGuardSet

    return WindowsReadOnlyFileGuardSet.acquire(declarations)


def _default_declaration_factory(
    path: Path, size_bytes: int, sha256: str
) -> Any:
    """Construct a guard declaration without exposing it from this module."""

    from ._windows_file_guard import GuardedFileDeclaration

    return GuardedFileDeclaration(path, size_bytes, sha256)


def _default_guarded_paths_getter(guard: _FileGuard) -> tuple[str, ...]:
    """Read stable Volume-GUID paths through the guard's private sealed API."""

    from ._windows_file_guard import (
        _GUARDED_VOLUME_PATH_ACCESS_SEAL,
        _get_guarded_volume_paths,
    )

    return _get_guarded_volume_paths(
        guard,  # type: ignore[arg-type]
        access_seal=_GUARDED_VOLUME_PATH_ACCESS_SEAL,
    )


def _default_manifest_digest_factory(
    runtime_root: Path,
    worker_path: Path,
    protocol_path: Path,
) -> str:
    """Hash runtime anchors only through their guarded stable path spellings."""

    return compute_runtime_manifest_digest(
        runtime_root,
        worker_path=worker_path,
        protocol_path=protocol_path,
    )


class _PipeTransport:
    """Own two private pipes plus one NUL handle during Windows launch."""

    __slots__ = (
        "_child_fds",
        "_closed",
        "request_stream",
        "response_stream",
        "stderr_handle",
        "stdin_handle",
        "stdout_handle",
    )

    def __init__(self) -> None:
        """Create non-inherited descriptors and translate child ends to HANDLEs."""

        import msvcrt

        request_read = request_write = response_read = response_write = -1
        stderr_fd = -1
        try:
            request_read, request_write = os.pipe()
            response_read, response_write = os.pipe()
            stderr_fd = os.open(
                os.devnull,
                os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
            self.request_stream: BinaryIO = os.fdopen(
                request_write, "wb", buffering=0
            )
            request_write = -1
            self.response_stream: BinaryIO = os.fdopen(
                response_read, "rb", buffering=0
            )
            response_read = -1
            self.stdin_handle = int(msvcrt.get_osfhandle(request_read))
            self.stdout_handle = int(msvcrt.get_osfhandle(response_write))
            self.stderr_handle = int(msvcrt.get_osfhandle(stderr_fd))
            self._child_fds = [request_read, response_write, stderr_fd]
            self._closed = False
        except BaseException:
            for descriptor in (
                request_read,
                request_write,
                response_read,
                response_write,
                stderr_fd,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None

    def close_child_ends(self) -> None:
        """Close descriptors whose handles the launcher already duplicated."""

        remaining: list[int] = []
        for descriptor in self._child_fds:
            try:
                os.close(descriptor)
            except OSError:
                # Retain failed descriptors so teardown can retry ownership;
                # clearing first would silently turn a native leak permanent.
                remaining.append(descriptor)
        self._child_fds = remaining
        if remaining:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)

    def close(self) -> None:
        """Idempotently close every parent and remaining child endpoint."""

        if self._closed:
            return
        # A failed child-end close is ambiguous: in particular, retaining the
        # response writer means another thread can still be blocked in the
        # parent reader. Do not call FileIO.close on that reader until a retry
        # has released every child endpoint, because Windows may wait forever
        # for the in-flight read instead of interrupting it.
        self.close_child_ends()
        failed = False
        for stream in (self.request_stream, self.response_stream):
            try:
                stream.close()
            except BaseException:
                failed = True
        if failed:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._closed = True


class _ProcessGuardOwner:
    """Retain a process and its immutable-file guard as one cleanup unit.

    An ambiguous Job termination means both owners remain security-relevant:
    the process may still execute from files that the guard must keep immutable.
    Quarantine therefore holds this composite instead of preserving only the
    guard and silently abandoning the process handle.
    """

    __slots__ = ("_closed", "_guard", "_lock", "_process", "_timeout_seconds")

    def __init__(
        self,
        process: _ManagedProcess,
        guard: _FileGuard,
        timeout_seconds: float,
    ) -> None:
        """Adopt both owners and the bounded timeout used by explicit retries."""

        self._process = process
        self._guard = guard
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._closed = False

    def close(self) -> None:
        """Release the guard only after a retry proves complete Job teardown."""

        with self._lock:
            if self._closed:
                return
            try:
                stopped = (
                    self._process.terminate(
                        timeout_seconds=self._timeout_seconds
                    )
                    is True
                )
            except BaseException:
                stopped = False
            if not stopped:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            try:
                self._guard.close()
            except BaseException:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            self._closed = True


class _ProcessTransportGuardOwner:
    """Retain a process, its pipes, and file guards after ambiguous teardown.

    Pipe streams may be blocked in another Python thread on Windows.  This
    owner therefore proves Job termination first, lets child-handle closure
    wake those reads, and only then closes the parent transport and guards.
    """

    __slots__ = (
        "_closed",
        "_guard",
        "_lock",
        "_process",
        "_quarantine_next",
        "_timeout_seconds",
        "_transport",
    )

    def __init__(
        self,
        process: _ManagedProcess | None,
        transport: _Transport,
        guard: _FileGuard,
        timeout_seconds: float,
    ) -> None:
        """Preallocate cleanup state and optionally adopt a launched process.

        Production creates this holder before ``CreateProcessW``. Persistent
        lock-allocation failure therefore happens while no child exists and all
        ordinary transaction owners are still available to the caller.
        """

        self._process = process
        self._transport = transport
        self._guard = guard
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._closed = False
        # This intrusive link is reserved before launch. If retaining an
        # ambiguous owner in the ordinary list later fails from allocation,
        # the module can still root an unbounded chain without allocating.
        self._quarantine_next: _ProcessTransportGuardOwner | None = None

    def close(self) -> None:
        """Release pipes and guards only after any adopted Job terminates."""

        with self._lock:
            if self._closed:
                return
            process = self._process
            if process is not None:
                try:
                    stopped = (
                        process.terminate(
                            timeout_seconds=self._timeout_seconds
                        )
                        is True
                    )
                except BaseException:
                    stopped = False
                if not stopped:
                    raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            failed = False
            try:
                self._transport.close()
            except BaseException:
                failed = True
            try:
                self._guard.close()
            except BaseException:
                failed = True
            if failed:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            self._closed = True

    def adopt_process(self, process: _ManagedProcess) -> None:
        """Attach the just-launched Job without allocating teardown state.

        The runtime calls this exactly once while holding its unpublished launch
        transaction lock. Plain field assignment is intentional: no fallible
        lock or holder construction may occur after the child starts.
        """

        if self._closed or self._process is not None:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._process = process

    def adopt_process_for_cleanup(self, process: _ManagedProcess) -> None:
        """Rescue a launcher result when normal ownership transfer is interrupted.

        This idempotent assignment uses only state allocated before launch. It
        exists for the exception path around ``adopt_process``: an injected
        ``BaseException`` must never let transport or file guards close before
        the just-created Job has been terminated.
        """

        if self._process is None:
            self._process = process
        elif self._process is not process:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)

    def set_cleanup_timeout(self, timeout_seconds: float) -> None:
        """Set the one caller-owned stop budget before cleanup begins.

        The composite owner is constructed before launch so teardown never
        needs to allocate a new holder after resources have been detached. Only
        the thread that wins ``_OwnedResources._claim_cleanup`` calls this method.
        """

        with self._lock:
            if self._closed:
                return
            self._timeout_seconds = timeout_seconds


def _terminate_then_release_or_quarantine(
    process: _ManagedProcess,
    guard: _FileGuard,
    timeout_seconds: float,
) -> bool:
    """Release a guard only after proven termination and cleanup success."""

    stopped = False
    try:
        stopped = process.terminate(timeout_seconds=timeout_seconds) is True
    except BaseException:
        pass
    if stopped:
        try:
            guard.close()
        except BaseException:
            _quarantine_guard(guard)
            return False
        return True
    # An ambiguous Job outcome cannot authorize making verified inputs mutable
    # again or abandon the native process owner. One permanently poisoned
    # runtime contributes one composite owner, so this ledger remains bounded
    # per runtime failure without relying on an outer test/runtime reference.
    _quarantine_guard(_ProcessGuardOwner(process, guard, timeout_seconds))
    return False


def _begin_cleanup_barrier() -> None:
    """Linearize one cleanup before any later production launch is admitted."""

    global _GLOBAL_CLEANUP_PENDING
    with _QUARANTINED_GUARDS_LOCK:
        _GLOBAL_CLEANUP_PENDING += 1


def _complete_cleanup_barrier() -> bool:
    """Release one successful reservation, returning false on broken state."""

    global _GLOBAL_CLEANUP_PENDING
    global _GLOBAL_CLEANUP_POISONED
    with _QUARANTINED_GUARDS_LOCK:
        if _GLOBAL_CLEANUP_PENDING <= 0:
            # An impossible accounting mismatch must not reopen production.
            _GLOBAL_CLEANUP_POISONED = True
            return False
        _GLOBAL_CLEANUP_PENDING -= 1
        return True


def _quarantine_cleanup_owner(
    owner: _ProcessTransportGuardOwner,
) -> None:
    """Atomically poison, finish one barrier, and retain a composite owner.

    The intrusive emergency link is assigned before the ordinary list append.
    It therefore preserves the process, pipes, and guards even if list growth
    raises ``MemoryError`` or another ``BaseException``. A successful append
    removes the temporary link so tests and diagnostics keep one canonical
    ledger entry.
    """

    global _EMERGENCY_CLEANUP_OWNER_HEAD
    global _GLOBAL_CLEANUP_PENDING
    global _GLOBAL_CLEANUP_POISONED
    with _QUARANTINED_GUARDS_LOCK:
        _GLOBAL_CLEANUP_POISONED = True
        if _GLOBAL_CLEANUP_PENDING > 0:
            _GLOBAL_CLEANUP_PENDING -= 1
        previous = _EMERGENCY_CLEANUP_OWNER_HEAD
        owner._quarantine_next = previous
        _EMERGENCY_CLEANUP_OWNER_HEAD = owner
        try:
            if not any(existing is owner for existing in _QUARANTINED_GUARDS):
                _QUARANTINED_GUARDS.append(owner)
        except BaseException:
            # The emergency head already owns this node and its predecessor.
            return
        _EMERGENCY_CLEANUP_OWNER_HEAD = previous
        owner._quarantine_next = None


def _quarantine_guard(guard: _FileGuard) -> None:
    """Retain one ambiguous owner and latch production globally fail-closed."""

    global _EMERGENCY_QUARANTINE_ERROR_HEAD
    global _EMERGENCY_QUARANTINE_GUARD
    global _GLOBAL_CLEANUP_POISONED
    with _QUARANTINED_GUARDS_LOCK:
        # Poison must publish before any fallible list scan or growth. One
        # direct fallback roots the first failure without allocating.
        _GLOBAL_CLEANUP_POISONED = True
        if _EMERGENCY_QUARANTINE_GUARD is None:
            _EMERGENCY_QUARANTINE_GUARD = guard
        try:
            if not any(existing is guard for existing in _QUARANTINED_GUARDS):
                _QUARANTINED_GUARDS.append(guard)
        except BaseException as error:
            # The exception already owns a traceback frame whose local ``guard``
            # is this exact resource. Chain those existing exception objects so
            # repeated allocation failures retain every independent owner
            # without allocating another list/node in the failing path. This
            # intentionally lives for the rest of the permanently poisoned
            # process and is never surfaced through public diagnostics.
            previous = _EMERGENCY_QUARANTINE_ERROR_HEAD
            if error is not previous:
                try:
                    error.__context__ = previous
                except BaseException:
                    pass
                else:
                    _EMERGENCY_QUARANTINE_ERROR_HEAD = error
            return
        if _EMERGENCY_QUARANTINE_GUARD is guard:
            _EMERGENCY_QUARANTINE_GUARD = None


def _close_or_quarantine(guard: _FileGuard) -> None:
    """Close an unlaunched guard or retain it when close is ambiguous."""

    try:
        guard.close()
    except BaseException:
        # A failed close may mean native handles are still active.  Retaining
        # the owner is safer than allowing garbage collection to obscure that
        # fail-closed state.
        _quarantine_guard(guard)


class _OwnedResources:
    """Keep pipes, process, and guards alive in the required cleanup order."""

    __slots__ = (
        "_cleanup_condition",
        "_cleanup_owner",
        "_cleanup_result",
        "_cleanup_runner_started",
        "_cleanup_state",
        "_global_barrier_owned",
        "guard",
        "process",
        "transport",
    )

    def __init__(
        self,
        transport: _Transport,
        process: _ManagedProcess | None,
        guard: _FileGuard,
    ) -> None:
        """Preallocate cleanup ownership before optionally adopting a child."""

        self.transport = transport
        self.process = process
        self.guard = guard
        # Build the only composite owner while the successful launch still has
        # an outer transaction owner. A teardown-time allocation failure would
        # otherwise detach live resources before they can be quarantined.
        self._cleanup_owner = _ProcessTransportGuardOwner(
            process,
            transport,
            guard,
            0.0,
        )
        self._cleanup_condition = Condition(RLock())
        self._cleanup_state = "open"
        self._cleanup_result = False
        self._cleanup_runner_started = False
        self._global_barrier_owned = False

    def adopt_process(self, process: _ManagedProcess) -> None:
        """Attach one new child to the already allocated cleanup owner."""

        self._cleanup_owner.adopt_process(process)
        self.process = process

    def adopt_process_for_cleanup(self, process: _ManagedProcess) -> None:
        """Idempotently rescue a live child after interrupted normal adoption."""

        # The composite owner is authoritative and must receive the process
        # first. If interruption occurs before the diagnostic mirror below,
        # process-first teardown is already guaranteed.
        self._cleanup_owner.adopt_process_for_cleanup(process)
        self.process = process

    def reserve_production_owner_under_gate(self) -> None:
        """Reserve the global production slot while its gate lock is held.

        Production keeps this reservation from immediately before CreateProcess
        through final cleanup. Besides making the worker globally single-owner,
        it removes every error-to-cleanup window in which a second worker could
        launch before the first owner's termination outcome becomes known.
        """

        global _GLOBAL_CLEANUP_PENDING
        if self._global_barrier_owned or self._cleanup_state != "open":
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        _GLOBAL_CLEANUP_PENDING += 1
        self._global_barrier_owned = True

    def __del__(self) -> None:
        """Best-effort preserve process-before-guard order after abandonment."""

        try:
            self.close(0.0)
        except BaseException:
            pass

    def _claim_cleanup(self) -> bool:
        """Claim cleanup and publish its production-launch exclusion barrier."""

        with self._cleanup_condition:
            if self._cleanup_state != "open":
                return False
            # The global reservation is the cleanup linearization point. A
            # production acquire that wins the same lock first is earlier;
            # every acquire after this point fails closed until cleanup proves
            # success or transitions the gate to permanent poison.
            if not self._global_barrier_owned:
                _begin_cleanup_barrier()
                self._global_barrier_owned = True
            self._cleanup_state = "closing"
            return True

    def _finish_cleanup(self, timeout_seconds: float) -> bool:
        """Terminate the Job before closing pipes, guards, files, and mapping."""

        owner = self._cleanup_owner
        try:
            owner.set_cleanup_timeout(timeout_seconds)
            owner.close()
        except BaseException:
            # One composite owner preserves the exact process-first retry
            # order. A live child can otherwise keep another thread's pipe
            # read blocked while an eager FileIO.close waits forever.
            _quarantine_cleanup_owner(owner)
            result = False
        else:
            result = _complete_cleanup_barrier()
        with self._cleanup_condition:
            self._cleanup_result = result
            self._global_barrier_owned = False
            self._cleanup_state = "closed"
            self._cleanup_condition.notify_all()
        return result

    def _finish_cleanup_once(self, timeout_seconds: float) -> bool | None:
        """Let exactly one runner consume the already claimed cleanup barrier.

        A non-conforming or fault-injected ``Thread.start`` can begin its target
        and then raise. The caller's synchronous fallback must not run cleanup a
        second time, decrement the global pending count twice, or publish false
        permanent poison.
        """

        with self._cleanup_condition:
            if self._cleanup_runner_started:
                return None
            self._cleanup_runner_started = True
        return self._finish_cleanup(timeout_seconds)

    def close(self, timeout_seconds: float) -> bool:
        """Own cleanup or join another owner for at most the supplied timeout.

        The timeout bounds native Job waiting and concurrent joiners. The first
        owner performs post-termination stream and guard release synchronously;
        those close methods are expected to return but are not a wall-clock
        deadline supplied by Python's I/O APIs.
        """

        if self._claim_cleanup():
            result = self._finish_cleanup_once(timeout_seconds)
            return result is True
        deadline = monotonic() + max(0.0, timeout_seconds)
        with self._cleanup_condition:
            while self._cleanup_state == "closing":
                remaining = deadline - monotonic()
                if remaining <= 0.0:
                    return False
                self._cleanup_condition.wait(remaining)
            return self._cleanup_result

    def close_async(self, timeout_seconds: float) -> None:
        """Start process-first Job, pipe, and guard cleanup off-thread.

        Cancellation is on the latency-sensitive queue path. On Windows,
        closing a ``FileIO`` while another thread blocks in ``read`` can itself
        block, so the caller only transfers ownership. The cleanup thread first
        terminates the Job, whose child-handle closure wakes the reader, and
        then closes parent pipes and guards. It is deliberately non-daemon so a
        short-lived backend cannot exit between termination and bootstrap-file
        cleanup; native Job waiting remains bounded by ``timeout_seconds``.
        """

        if not self._claim_cleanup():
            return

        def finish() -> None:
            """Retain this owner until Job termination resolves guard release."""

            self._finish_cleanup_once(timeout_seconds)

        try:
            cleanup = Thread(
                target=finish,
                name="elysia-managed-tts-cleanup",
                daemon=False,
            )
            cleanup.start()
        except BaseException:
            # Thread allocation or start failure cannot leave state at
            # ``closing`` or abandon a live process. Finish synchronously.
            finish()


def _default_transport_factory() -> _Transport:
    """Create the production pipe transport on a supported Windows host."""

    return _PipeTransport()


@dataclass(frozen=True, slots=True, repr=False)
class _DeclaredIdentity:
    """Retain one guard-verified size and digest without its candidate path."""

    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class _GuardedLayout:
    """Freeze only stable guarded paths needed after drive-letter validation."""

    gpt_path: Path
    sovits_path: Path
    reference_path: Path
    worker_path: Path
    protocol_path: Path
    runtime_root: Path
    candidate_runtime_root: Path
    runtime_anchor_paths: tuple[Path, ...]
    runtime_anchor_identities: tuple[_DeclaredIdentity, ...]


def _guarded_layout(
    paths: object,
    declarations: tuple[Any, ...],
    candidate_runtime_root: Path,
) -> _GuardedLayout:
    """Validate the accessor's ordered Volume-GUID path layout.

    The file guard pins objects, but a drive letter can be remapped between
    verification and process creation.  Only the guard-exported Volume-GUID
    spellings are therefore admitted to INIT, binding, cwd, or argv.
    """

    expected_count = 5 + len(_RUNTIME_MANIFEST_RELATIVE_FILES)
    if (
        type(paths) is not tuple
        or len(paths) != expected_count
        or any(type(value) is not str for value in paths)
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    snapshots = tuple(Path(value) for value in paths)
    if any(
        not _is_lexical_absolute_path(path)
        or not str(path).casefold().startswith("\\\\?\\volume{")
        for path in snapshots
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    folded = tuple(os.path.normcase(str(path)) for path in snapshots)
    if len(set(folded)) != len(folded):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)

    gpt_path, sovits_path, reference_path, worker_path, protocol_path = snapshots[:5]
    runtime_anchors = snapshots[5:]
    runtime_declarations = declarations[5:]
    if len(runtime_declarations) != len(runtime_anchors):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    identities: list[_DeclaredIdentity] = []
    for declaration in runtime_declarations:
        try:
            size_bytes = declaration.size_bytes
            sha256 = declaration.sha256
        except BaseException:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
        if (
            type(size_bytes) is not int
            or size_bytes <= 0
            or type(sha256) is not str
            or _SHA256_PATTERN.fullmatch(sha256) is None
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        identities.append(_DeclaredIdentity(size_bytes, sha256))
    python_relative = Path(_RUNTIME_MANIFEST_RELATIVE_FILES[0][1])
    runtime_root = runtime_anchors[0]
    for _part in python_relative.parts:
        runtime_root = runtime_root.parent
    if worker_path.with_name("gpt_sovits_protocol.py") != protocol_path:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    for anchor_path, (_role, relative) in zip(
        runtime_anchors, _RUNTIME_MANIFEST_RELATIVE_FILES
    ):
        if runtime_root / Path(relative) != anchor_path:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    return _GuardedLayout(
        gpt_path,
        sovits_path,
        reference_path,
        worker_path,
        protocol_path,
        runtime_root,
        candidate_runtime_root,
        runtime_anchors,
        tuple(identities),
    )


def _runtime_role_path(layout: _GuardedLayout, role: str) -> Path:
    """Return one stable guarded runtime path by its closed manifest role."""

    for path, (candidate_role, _relative) in zip(
        layout.runtime_anchor_paths, _RUNTIME_MANIFEST_RELATIVE_FILES
    ):
        if candidate_role == role:
            return path
    raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)


def _runtime_role_identity(
    layout: _GuardedLayout, role: str
) -> _DeclaredIdentity:
    """Return guard-verified metadata for one closed manifest role."""

    for identity, (candidate_role, _relative) in zip(
        layout.runtime_anchor_identities, _RUNTIME_MANIFEST_RELATIVE_FILES
    ):
        if candidate_role == role:
            return identity
    raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)


def _query_dos_device(name: str) -> tuple[str, ...]:
    """Return one DOS-device target stack, using empty only for not-found."""

    if os.name != "nt":
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.QueryDosDeviceW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.QueryDosDeviceW.restype = wintypes.DWORD
    capacity = 32_768
    buffer = ctypes.create_unicode_buffer(capacity)
    ctypes.set_last_error(0)
    count = int(kernel32.QueryDosDeviceW(name, buffer, capacity))
    if count == 0:
        if ctypes.get_last_error() == 2:
            return ()
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    if count >= capacity:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    values = tuple(
        entry for entry in "".join(buffer[:count]).split("\x00") if entry
    )
    if not values or any("\x00" in entry for entry in values):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    return values


def _logical_drive_mask() -> int:
    """Return Windows' authoritative occupied-drive bit mask."""

    if os.name != "nt":
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetLogicalDrives.argtypes = []
    kernel32.GetLogicalDrives.restype = wintypes.DWORD
    ctypes.set_last_error(0)
    value = int(kernel32.GetLogicalDrives())
    if value == 0 and ctypes.get_last_error() != 0:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    return value


def _define_dos_device(flags: int, name: str, target: str) -> bool:
    """Apply one raw DOS-device definition without exposing native errors."""

    if os.name != "nt":
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.DefineDosDeviceW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    ]
    kernel32.DefineDosDeviceW.restype = wintypes.BOOL
    return bool(kernel32.DefineDosDeviceW(flags, name, target))


def _query_volume_name(root: str) -> str:
    """Return the Volume-GUID root reached through one mapped drive."""

    if os.name != "nt":
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    capacity = 64
    buffer = ctypes.create_unicode_buffer(capacity)
    if not kernel32.GetVolumeNameForVolumeMountPointW(root, buffer, capacity):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    return buffer.value


def _candidate_dos_device_names() -> tuple[str, ...]:
    """Randomize every non-system drive candidate without repetition."""

    values = [f"{letter}:" for letter in "DEFGHIJKLMNOPQRSTUVWXYZ"]
    # Fisher-Yates gives every free letter a fair first chance while still
    # exhausting the bounded set when another application owns a candidate.
    for upper in range(len(values) - 1, 0, -1):
        selected = secrets.randbelow(upper + 1)
        values[upper], values[selected] = values[selected], values[upper]
    return tuple(values)


def _volume_mapping_identity(path: Path) -> tuple[str, str]:
    """Bind a guarded Volume-GUID spelling to its sole fixed native target."""

    match = _VOLUME_GUID_PATH_PATTERN.fullmatch(str(path))
    if match is None:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    volume_name = match.group(1)
    targets = _query_dos_device(volume_name)
    if (
        len(targets) != 1
        or _NATIVE_FIXED_VOLUME_PATTERN.fullmatch(targets[0]) is None
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    return volume_name, targets[0]


class _DosDeviceMapping:
    """Own one verified temporary drive alias for a guarded fixed volume."""

    __slots__ = ("_active", "_drive", "_native_target", "_volume_root")

    def __init__(
        self, drive: str, native_target: str, volume_root: str
    ) -> None:
        """Adopt a mapping only after exact target and volume verification."""

        self._drive = drive
        self._native_target = native_target
        self._volume_root = volume_root
        self._active = True

    @property
    def drive(self) -> str:
        """Return the temporary drive name without any private path suffix."""

        return self._drive

    def path_for(self, path: Path) -> str:
        """Translate only a path on this mapping's guarded Volume-GUID root."""

        match = _VOLUME_GUID_PATH_PATTERN.fullmatch(str(path))
        if (
            match is None
            or ("\\\\?\\" + match.group(1) + "\\").casefold()
            != self._volume_root.casefold()
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        suffix = match.group(2) or "\\"
        result = self._drive + suffix
        if not ntpath.isabs(result) or ntpath.normpath(result) != result:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        return result

    def native_path_for(self, path: Path) -> str:
        """Return the fixed kernel path corresponding to one guarded path."""

        match = _VOLUME_GUID_PATH_PATTERN.fullmatch(str(path))
        if match is None:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        expected_root = "\\\\?\\" + match.group(1) + "\\"
        if expected_root.casefold() != self._volume_root.casefold():
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        suffix = match.group(2) or "\\"
        return self._native_target + suffix

    def verify(self) -> None:
        """Recheck both namespace target and reached volume before a launch."""

        with _DOS_DEVICE_LOCK:
            if (
                not self._active
                or _query_dos_device(self._drive) != (self._native_target,)
                or _query_volume_name(self._drive + "\\").casefold()
                != self._volume_root.casefold()
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)

    def close(self) -> None:
        """Remove only this exact raw target and prove the name became empty."""

        remove_flags = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008
        with _DOS_DEVICE_LOCK:
            if not self._active:
                return
            current = _query_dos_device(self._drive)
            if self._native_target not in current:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            removed = _define_dos_device(
                remove_flags, self._drive, self._native_target
            )
            remaining = _query_dos_device(self._drive)
            if not removed or remaining:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            self._active = False


def _create_dos_device_mapping(path: Path) -> _DosDeviceMapping:
    """Create one collision-free verified drive alias to a guarded volume."""

    volume_name, native_target = _volume_mapping_identity(path)
    expected_root = "\\\\?\\" + volume_name + "\\"
    create_flags = 0x00000001 | 0x00000008
    with _DOS_DEVICE_LOCK:
        mask = _logical_drive_mask()
        for drive in _candidate_dos_device_names():
            bit = 1 << (ord(drive[0].upper()) - ord("A"))
            if mask & bit or _query_dos_device(drive):
                continue
            if not _define_dos_device(create_flags, drive, native_target):
                if _query_dos_device(drive):
                    continue
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            mapping = _DosDeviceMapping(drive, native_target, expected_root)
            try:
                mapping.verify()
            except BaseException:
                _close_or_quarantine(mapping)
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            return mapping
    raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)


def _create_restricted_bootstrap_directory(path: Path) -> None:
    """Exclusively create one Windows directory with an owner/SYSTEM-only DACL."""

    if os.name != "nt":
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    class _SecurityAttributes(ctypes.Structure):
        """Mirror SECURITY_ATTRIBUTES for one non-inherited directory ACL."""

        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    kernel32.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SecurityAttributes),
    ]
    kernel32.CreateDirectoryW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    # OWNER RIGHTS refers to the token owner chosen during creation. The
    # protected DACL prevents inherited broad access while SYSTEM retains
    # recovery access; no handle is inheritable by the worker.
    sddl = "D:P(A;;FA;;;SY)(A;;FA;;;OW)"
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    try:
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False
        )
        if not kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    finally:
        kernel32.LocalFree(descriptor)


def _seal_restricted_bootstrap_directory(path: Path) -> None:
    """Remove owner write access after populating the private bootstrap."""

    if os.name != "nt":
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    advapi32.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    # SYSTEM retains recovery access. The owner keeps read/execute/delete so
    # CPython can load and deterministic teardown can remove the directory, but
    # ordinary create/write operations cannot add a shadow module or DLL.
    sddl = "D:P(A;;FA;;;SY)(A;;GRGXSD;;;OW)"
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    try:
        security_information = 0x00000004 | 0x80000000
        if not advapi32.SetFileSecurityW(
            str(path), security_information, descriptor
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    finally:
        kernel32.LocalFree(descriptor)


class _DescriptorOwner:
    """Retain one CRT descriptor until its close result is unambiguous."""

    __slots__ = ("_descriptor", "_lock")

    def __init__(self) -> None:
        """Create an empty owner before any descriptor can be acquired."""

        self._descriptor = -1
        self._lock = Lock()

    def adopt(self, descriptor: int) -> None:
        """Adopt one fresh descriptor without an allocation-time ownership gap."""

        if type(descriptor) is not int or descriptor < 0 or self._descriptor >= 0:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._descriptor = descriptor

    def close(self) -> None:
        """Close the descriptor once or retain its exact value for quarantine."""

        with self._lock:
            if self._descriptor < 0:
                return
            try:
                os.close(self._descriptor)
            except BaseException:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            self._descriptor = -1


class _BootstrapDirectoryOwner:
    """Hold a directory handle that conflicts with file-add-capable peers."""

    __slots__ = ("_handle", "_lock")

    def __init__(self) -> None:
        """Create an empty owner before the native directory handle is opened."""

        self._handle = 0
        self._lock = Lock()

    def acquire(self, path: Path) -> None:
        """Open the sealed directory without ``FILE_SHARE_WRITE``.

        Sharing checks include data-access handles opened before the DACL was
        sealed, so a pre-existing ``FILE_ADD_FILE`` capability is detected.
        They do not cover a pre-opened ``WRITE_DAC``-only handle; excluding a
        malicious process under the same user is an explicit module-level trust
        assumption.  The retained handle and sealed DACL still reject ordinary
        later file-add opens until the managed process has terminated.
        """

        if os.name != "nt" or self._handle != 0:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(
            str(path),
            0x00000001,  # FILE_LIST_DIRECTORY participates in share checks.
            0x00000001,  # FILE_SHARE_READ; write/delete sharing stay denied.
            None,
            3,  # OPEN_EXISTING
            0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            None,
        )
        value = ctypes.cast(handle, ctypes.c_void_p).value
        invalid_handle = ctypes.c_void_p(-1).value
        if value is None or value == invalid_handle:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._handle = int(value)

    def close(self) -> None:
        """Close the exact directory handle or retain it when close is ambiguous."""

        with self._lock:
            if self._handle == 0:
                return
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            if not kernel32.CloseHandle(wintypes.HANDLE(self._handle)):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            self._handle = 0


class _BootstrapCleanupOwner:
    """Own only paths created or potentially created by one bootstrap attempt."""

    __slots__ = ("_directory", "_directory_created", "_files", "_lock")

    def __init__(self, directory: Path) -> None:
        """Prepare bookkeeping before the exclusive directory creation call."""

        self._directory = directory
        self._directory_created = False
        self._files: list[Path] = []
        self._lock = Lock()

    def mark_directory_created(self) -> None:
        """Record that this transaction, and no prior owner, created the directory."""

        if self._directory_created:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._directory_created = True

    def plan_file(self, path: Path) -> None:
        """Adopt a destination before an exclusive helper can create it partially."""

        if not self._directory_created or path.parent != self._directory:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._files.append(path)

    def close(self) -> None:
        """Restore this transaction's directory to absence, retaining failures."""

        with self._lock:
            if not self._directory_created:
                return
            if not _cleanup_bootstrap_paths(
                self._directory, tuple(self._files)
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            self._directory_created = False


def _finish_descriptor_owners(*owners: _DescriptorOwner) -> bool:
    """Close temporary descriptors and quarantine every ambiguous owner."""

    succeeded = True
    for owner in owners:
        try:
            owner.close()
        except BaseException:
            _quarantine_guard(owner)
            succeeded = False
    return succeeded


def _write_exclusive_file(path: Path, content: bytes) -> tuple[int, str]:
    """Create one bootstrap file only after its write handle closes safely."""

    owner = _DescriptorOwner()
    result: tuple[int, str] | None = None
    failed = False
    try:
        owner.adopt(
            os.open(
                str(path),
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
        )
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(owner._descriptor, view[written:])
            if count <= 0:
                raise OSError
            written += count
        os.fsync(owner._descriptor)
        result = written, hashlib.sha256(content).hexdigest()
    except BaseException:
        failed = True
    closed = _finish_descriptor_owners(owner)
    if failed or not closed or result is None:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
    return result


def _copy_exclusive_file(source: Path, destination: Path) -> tuple[int, str]:
    """Copy one held source and prove both temporary handles were released."""

    source_owner = _DescriptorOwner()
    destination_owner = _DescriptorOwner()
    digest = hashlib.sha256()
    total = 0
    failed = False
    try:
        source_owner.adopt(
            os.open(str(source), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        )
        destination_owner.adopt(
            os.open(
                str(destination),
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
        )
        while True:
            chunk = os.read(source_owner._descriptor, _HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_CANDIDATE_ANCHOR_BYTES:
                raise OSError
            digest.update(chunk)
            offset = 0
            while offset < len(chunk):
                count = os.write(destination_owner._descriptor, chunk[offset:])
                if count <= 0:
                    raise OSError
                offset += count
        if total <= 0:
            raise OSError
        os.fsync(destination_owner._descriptor)
    except BaseException:
        failed = True
    # Close the write-capable destination first.  Neither result may be
    # discarded because an ambiguous descriptor would outlive later guards.
    closed = _finish_descriptor_owners(destination_owner, source_owner)
    if failed or not closed or total <= 0:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
    return total, digest.hexdigest()


def _bootstrap_path_file(
    layout: _GuardedLayout,
    mapping: _DosDeviceMapping,
) -> bytes:
    """Encode the closed absolute sys.path used by the isolated Python copy."""

    # Python 3.9 LoadLibrary rejects Volume-GUID and GLOBALROOT spellings.  One
    # verified temporary DOS-device alias is therefore shared by every entry;
    # the mapping and all file guards outlive the worker process.  The copied
    # executable directory is deliberately absent: it contains no importable
    # application code and admitting it would permit an added shadow module.
    values: list[str] = []
    for relative in _BOOTSTRAP_SYS_PATH_RELATIVE_ENTRIES:
        path = layout.runtime_root / Path(relative)
        rendered = mapping.path_for(path)
        if (
            not rendered.casefold().startswith(mapping.drive.casefold() + "\\")
            or "\r" in rendered
            or "\n" in rendered
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        values.append(rendered)
    if len(set(value.casefold() for value in values)) != len(values):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    # No ``import site``, relative entry, blank line, or executable .pth code is
    # admitted. This prevents legacy hooks from becoming pre-worker code-loading
    # surfaces before the worker validates and replaces ``sys.path`` itself.
    return ("\r\n".join(values) + "\r\n").encode("utf-8", errors="strict")


def _require_exact_bootstrap_directory(
    directory: Path, files: tuple[Path, ...]
) -> None:
    """Reject any undeclared entry before or after the directory becomes guarded."""

    expected = tuple(path.name for path in files)
    try:
        with os.scandir(str(directory)) as entries:
            observed = tuple(entry.name for entry in entries)
    except BaseException:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
    if (
        len(observed) != len(expected)
        or {name.casefold() for name in observed}
        != {name.casefold() for name in expected}
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)


def _cleanup_bootstrap_paths(directory: Path, files: tuple[Path, ...]) -> bool:
    """Remove exact bootstrap paths and report whether nothing remains."""

    succeeded = True
    for path in reversed(files):
        try:
            os.unlink(str(path))
        except FileNotFoundError:
            # A helper can fail before creating its planned destination, and a
            # retry can revisit paths already removed by an earlier attempt.
            pass
        except OSError:
            succeeded = False
    try:
        os.rmdir(str(directory))
    except FileNotFoundError:
        pass
    except OSError:
        succeeded = False
    return succeeded


class _BootstrapGuard:
    """Hold file and directory guards through process-lifetime cleanup."""

    __slots__ = (
        "_cleanup_owner",
        "_closed",
        "_directory_owner",
        "_guard",
        "_lock",
        "_mapping",
    )

    def __init__(
        self,
        guard: _FileGuard,
        directory_owner: _BootstrapDirectoryOwner,
        cleanup_owner: _BootstrapCleanupOwner,
        mapping: _DosDeviceMapping,
    ) -> None:
        """Adopt a fully guarded bootstrap directory transaction."""

        self._guard = guard
        self._directory_owner = directory_owner
        self._cleanup_owner = cleanup_owner
        self._mapping = mapping
        self._lock = Lock()
        self._closed = False

    def close(self) -> None:
        """Release handles, then remove only this transaction's known files."""

        with self._lock:
            if self._closed:
                return
            try:
                self._guard.close()
            except BaseException:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            try:
                self._directory_owner.close()
            except BaseException:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            cleanup_failed = False
            try:
                self._cleanup_owner.close()
            except BaseException:
                cleanup_failed = True
            try:
                self._mapping.close()
            except BaseException:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            if cleanup_failed:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            self._closed = True


class _CombinedGuard:
    """Release bootstrap and source guards only after their common child exits."""

    __slots__ = ("_bootstrap", "_closed", "_lock", "_source")

    def __init__(self, source: _FileGuard, bootstrap: _FileGuard) -> None:
        """Adopt both guards as one process-lifetime resource."""

        self._source = source
        self._bootstrap = bootstrap
        self._lock = Lock()
        self._closed = False

    def close(self) -> None:
        """Close both guards once without letting one failure abandon the other."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            failed = False
            for guard in (self._bootstrap, self._source):
                try:
                    guard.close()
                except BaseException:
                    _quarantine_guard(guard)
                    failed = True
            if failed:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)


@dataclass(frozen=True, slots=True, repr=False)
class _BootstrapLaunch:
    """Return a verified mapped executable, import root, and held guard."""

    executable: str
    import_root: str
    stable_executable: Path
    executable_identity: _DeclaredIdentity
    guard: _FileGuard


def _default_bootstrap_factory(layout: _GuardedLayout) -> _BootstrapLaunch:
    r"""Create and guard a CPython bootstrap that preserves stable sys.path.

    CPython 3.9 cannot load compiled extensions through Volume-GUID,
    ``GLOBALROOT``, or ``\\.\Volume`` paths. A private guarded executable copy,
    closed ``python39._pth``, and verified temporary DOS-device alias bridge
    that compatibility gap. The exact mapping remains owned until Job death.
    """

    token = secrets.token_hex(32)
    directory = layout.runtime_root / f".elysia-managed-{token}"
    candidate_directory = (
        layout.candidate_runtime_root / f".elysia-managed-{token}"
    )
    created_files: list[Path] = []
    cleanup_owner: _BootstrapCleanupOwner | None = _BootstrapCleanupOwner(
        directory
    )
    directory_owner: _BootstrapDirectoryOwner | None = (
        _BootstrapDirectoryOwner()
    )
    bootstrap_guard: _FileGuard | None = None
    mapping: _DosDeviceMapping | None = None
    owned_guard: _BootstrapGuard | None = None
    try:
        assert cleanup_owner is not None
        assert directory_owner is not None
        mapping = _create_dos_device_mapping(layout.runtime_root)
        _create_restricted_bootstrap_directory(directory)
        cleanup_owner.mark_directory_created()
        declarations: list[Any] = []
        for role, filename in _BOOTSTRAP_COPY_ROLES:
            destination = directory / filename
            candidate_destination = candidate_directory / filename
            # Plan before opening: an exclusive helper can create a partial
            # destination and then fail before it returns to this bookkeeping.
            cleanup_owner.plan_file(destination)
            size_bytes, sha256 = _copy_exclusive_file(
                _runtime_role_path(layout, role), destination
            )
            source_identity = _runtime_role_identity(layout, role)
            if (
                size_bytes != source_identity.size_bytes
                or not hmac.compare_digest(sha256, source_identity.sha256)
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            created_files.append(destination)
            declarations.append(
                _default_declaration_factory(
                    candidate_destination, size_bytes, sha256
                )
            )
        path_file = directory / "python39._pth"
        candidate_path_file = candidate_directory / "python39._pth"
        path_content = _bootstrap_path_file(layout, mapping)
        cleanup_owner.plan_file(path_file)
        size_bytes, sha256 = _write_exclusive_file(path_file, path_content)
        created_files.append(path_file)
        declarations.append(
            _default_declaration_factory(
                candidate_path_file, size_bytes, sha256
            )
        )
        _seal_restricted_bootstrap_directory(directory)
        directory_owner.acquire(directory)
        _require_exact_bootstrap_directory(directory, tuple(created_files))
        bootstrap_guard = _default_guard_acquirer(tuple(declarations))
        # The directory owner detects incompatible file-add handles around both
        # exact enumerations. The module-level trust model excludes a same-user
        # peer that retained WRITE_DAC before sealing; leaf guards still supply
        # identity and no-write/no-delete ownership for every declared file.
        _require_exact_bootstrap_directory(directory, tuple(created_files))
        stable_paths = _default_guarded_paths_getter(bootstrap_guard)
        if (
            len(stable_paths) != len(created_files)
            or any(type(value) is not str for value in stable_paths)
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        stable = tuple(Path(value) for value in stable_paths)
        if stable != tuple(created_files):
            # The second guard entered through the fixed drive spelling only
            # because its public boundary intentionally rejects device paths.
            # Exact Volume-GUID comparison proves no drive remap redirected it
            # to different objects between stable creation and acquisition.
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        stable_directory = stable[0].parent
        if any(path.parent != stable_directory for path in stable):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        python_path = stable[0]
        if python_path.name.casefold() != "python.exe":
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        owned_guard = _BootstrapGuard(
            bootstrap_guard,
            directory_owner,
            cleanup_owner,
            mapping,
        )
        bootstrap_guard = None
        directory_owner = None
        cleanup_owner = None
        mapping = None
        executable = owned_guard._mapping.path_for(python_path)
        import_root = owned_guard._mapping.path_for(layout.runtime_root)
        owned_guard._mapping.verify()
        launch = _BootstrapLaunch(
            executable,
            import_root,
            python_path,
            _runtime_role_identity(layout, "runtime-python"),
            owned_guard,
        )
        owned_guard = None
        return launch
    except BaseException as error:
        if owned_guard is not None:
            # Once constructed, this object is the sole owner of leaf guards,
            # private files, and the DOS mapping. Never run split cleanup too.
            _close_or_quarantine(owned_guard)
            if isinstance(error, ManagedGptSovitsError):
                raise
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
        if bootstrap_guard is not None:
            try:
                bootstrap_guard.close()
            except BaseException:
                _quarantine_guard(bootstrap_guard)
        if directory_owner is not None:
            _close_or_quarantine(directory_owner)
        if cleanup_owner is not None:
            _close_or_quarantine(cleanup_owner)
        if mapping is not None:
            _close_or_quarantine(mapping)
        if isinstance(error, ManagedGptSovitsError):
            raise
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None


def _verify_production_bootstrap(bootstrap: _BootstrapLaunch) -> None:
    """Revalidate the guarded executable through its live drive mapping."""

    guard = bootstrap.guard
    if type(guard) is not _BootstrapGuard:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    if (
        type(guard._directory_owner) is not _BootstrapDirectoryOwner
        or guard._directory_owner._handle == 0
        or type(guard._cleanup_owner) is not _BootstrapCleanupOwner
        or not guard._cleanup_owner._directory_created
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    mapping = guard._mapping
    mapping.verify()
    if mapping.path_for(bootstrap.stable_executable) != bootstrap.executable:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    if mapping.path_for(
        bootstrap.stable_executable.parent.parent
    ) != bootstrap.import_root:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    size_bytes, sha256 = _hash_candidate_file(Path(bootstrap.executable))
    if (
        size_bytes != bootstrap.executable_identity.size_bytes
        or not hmac.compare_digest(
            sha256, bootstrap.executable_identity.sha256
        )
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)


class _NativeHandleGuard:
    """Retain one Win32 query handle until CloseHandle is proven successful."""

    __slots__ = ("_handle",)

    def __init__(self, handle: int) -> None:
        """Adopt one nonzero process-query handle."""

        self._handle = handle

    def close(self) -> None:
        """Close the exact native handle or retain it for quarantine retry."""

        if self._handle == 0:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(wintypes.HANDLE(self._handle)):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        self._handle = 0


def _verify_launched_process_image(
    process: _ManagedProcess, bootstrap: _BootstrapLaunch
) -> None:
    """Prove the new root process image is the guarded bootstrap executable."""

    if os.name != "nt" or type(process.pid) is not int or process.pid <= 0:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    guard = bootstrap.guard
    if type(guard) is not _BootstrapGuard:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    expected = guard._mapping.native_path_for(bootstrap.stable_executable)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessImageFileNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.K32GetProcessImageFileNameW.restype = wintypes.DWORD
    raw_handle = kernel32.OpenProcess(0x00001000, False, process.pid)
    handle_value = int(getattr(raw_handle, "value", raw_handle) or 0)
    if handle_value <= 0:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    owner = _NativeHandleGuard(handle_value)
    failure: BaseException | None = None
    try:
        capacity = 32_768
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(
            kernel32.K32GetProcessImageFileNameW(
                raw_handle, buffer, capacity
            )
        )
        if (
            length <= 0
            or length >= capacity
            or buffer.value.casefold() != expected.casefold()
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    except BaseException as error:
        failure = error
    try:
        owner.close()
    except BaseException:
        _quarantine_guard(owner)
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
    if failure is not None:
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None


def _guarded_runtime_manifest_digest(declarations: tuple[Any, ...]) -> str:
    """Hash the exact guard-verified runtime declarations by stable role."""

    runtime_declarations = declarations[3:]
    roles = ("elysia-worker", "elysia-protocol") + tuple(
        role for role, _relative in _RUNTIME_MANIFEST_RELATIVE_FILES
    )
    if len(runtime_declarations) != len(roles):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    entries: list[dict[str, object]] = []
    for role, declaration in zip(roles, runtime_declarations):
        try:
            size_bytes = declaration.size_bytes
            sha256 = declaration.sha256
        except BaseException:
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
        if (
            type(size_bytes) is not int
            or size_bytes <= 0
            or type(sha256) is not str
            or _SHA256_PATTERN.fullmatch(sha256) is None
        ):
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
        entries.append({"bytes": size_bytes, "id": role, "sha256": sha256})
    canonical = json.dumps(
        {"files": entries, "schema": _SCHEMA_VERSION},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(_RUNTIME_MANIFEST_DOMAIN + canonical).hexdigest()


def _canonical_binding(
    *,
    runtime_root: Path,
    gpt_path: Path,
    gpt_size: int,
    gpt_sha256: str,
    sovits_path: Path,
    sovits_size: int,
    sovits_sha256: str,
    reference_path: Path,
    reference_size: int,
    reference_sha256: str,
    prompt_text: str,
    prompt_language: str,
    speed_milli: int,
    seed: int,
    device: str,
    runtime_manifest_digest: str,
) -> str:
    """Compute the exact configuration digest independently of the challenge."""

    value = {
        "device": device,
        "gpt_weights": {
            "bytes": gpt_size,
            "path": str(gpt_path),
            "sha256": gpt_sha256,
        },
        "prompt_language": prompt_language,
        "prompt_text": prompt_text,
        "reference_audio": {
            "bytes": reference_size,
            "path": str(reference_path),
            "sha256": reference_sha256,
        },
        "runtime_manifest_digest": runtime_manifest_digest,
        "runtime_root": str(runtime_root),
        "schema": _SCHEMA_VERSION,
        "seed": seed,
        "sovits_weights": {
            "bytes": sovits_size,
            "path": str(sovits_path),
            "sha256": sovits_sha256,
        },
        "speed_milli": speed_milli,
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(_BINDING_DOMAIN + encoded).hexdigest()


def _exact_metadata(frame: ProtocolFrame, fields: frozenset[str]) -> dict[str, object]:
    """Return frame metadata only when its plain-object field set is exact."""

    metadata = frame.metadata
    if type(metadata) is not dict or set(metadata) != fields:
        raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
    return metadata


def _require_error_frame(frame: ProtocolFrame, request_id: int) -> None:
    """Validate the worker's code-only ERROR without reflecting its code."""

    if frame.kind is not FrameKind.ERROR or frame.request_id != request_id:
        raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
    metadata = _exact_metadata(frame, _ERROR_FIELDS)
    if type(metadata["code"]) is not str or metadata["code"] not in _ERROR_CODES:
        raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)


def _require_managed_wav(audio: bytes, sample_rate: int) -> None:
    """Revalidate the worker's exact mono signed-16 PCM WAV representation."""

    if (
        type(audio) is not bytes
        or not 46 <= len(audio) <= PROTOCOL_MAX_PAYLOAD_BYTES
        or len(audio) % 2 != 0
        or audio[:4] != b"RIFF"
        or int.from_bytes(audio[4:8], "little") + 8 != len(audio)
        or audio[8:16] != b"WAVEfmt "
        or int.from_bytes(audio[16:20], "little") != 16
        or int.from_bytes(audio[20:22], "little") != 1
        or int.from_bytes(audio[22:24], "little") != 1
        or int.from_bytes(audio[24:28], "little") != sample_rate
        or int.from_bytes(audio[28:32], "little") != sample_rate * 2
        or int.from_bytes(audio[32:34], "little") != 2
        or int.from_bytes(audio[34:36], "little") != 16
        or audio[36:40] != b"data"
        or int.from_bytes(audio[40:44], "little") != len(audio) - 44
        or not any(audio[44:])
    ):
        raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)


class ManagedGptSovitsLease:
    """Own one guarded worker bound to one private catalog selection.

    Instances cannot be constructed by application callers; the runtime keeps
    a private provenance token and issues one only after READY passes. This is
    a low-level runtime lease, not the speech queue's Elysia-ownership marker.
    In particular it intentionally exposes ``cache_eligible=False`` and makes
    no complete-provenance claim while the runtime manifest remains a partial
    consistency set rather than an approved supply-chain manifest. The queue's
    ``binding_verified`` property only proves its private factory issued the
    binding around this exact lease; it does not strengthen that threat model.
    """

    _active_token: str | None
    _cancelled_tokens: set[str]
    _challenge: str
    _io_lock: Lock
    _next_request_id: int
    _on_finished: Callable[["ManagedGptSovitsLease", bool], None]
    _provenance: object
    _reader: Callable[[BinaryIO], ProtocolFrame]
    _resources: _OwnedResources | None
    _speed_factor: float
    _state: str
    _state_lock: Condition
    _stop_timeout: float
    _synthesis_timeout: float
    _used_tokens: set[str]
    _writer: Callable[[BinaryIO, ProtocolFrame], None]

    __slots__ = (
        "_active_token",
        "_cancelled_tokens",
        "_challenge",
        "_io_lock",
        "_next_request_id",
        "_on_finished",
        "_provenance",
        "_resources",
        "_speed_factor",
        "_state",
        "_state_lock",
        "_stop_timeout",
        "_synthesis_timeout",
        "_used_tokens",
        "_reader",
        "_writer",
    )

    def __init__(self) -> None:
        """Reject direct construction; only a completed runtime may issue one."""

        raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)

    @classmethod
    def _issue(
        cls,
        *,
        issuer_seal: object,
        resources: _OwnedResources,
        challenge: str,
        speed_factor: float,
        synthesis_timeout: float,
        stop_timeout: float,
        reader: Callable[[BinaryIO], ProtocolFrame],
        writer: Callable[[BinaryIO, ProtocolFrame], None],
        on_finished: Callable[["ManagedGptSovitsLease", bool], None],
    ) -> "ManagedGptSovitsLease":
        """Issue one exact-class lease only with the module-private seal.

        Both the class identity and seal are checked here so subclass factory
        dispatch cannot replace the concrete lifecycle implementation after a
        successful READY attestation.
        """

        if cls is not ManagedGptSovitsLease or issuer_seal is not _LEASE_ISSUER_SEAL:
            raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
        self = object.__new__(cls)
        self._provenance = issuer_seal
        self._resources = resources
        self._challenge = challenge
        self._speed_factor = speed_factor
        self._synthesis_timeout = synthesis_timeout
        self._stop_timeout = stop_timeout
        self._reader = reader
        self._writer = writer
        self._on_finished = on_finished
        self._state_lock = Condition(RLock())
        self._io_lock = Lock()
        self._state = "ready"
        self._active_token = None
        self._cancelled_tokens = set()
        self._next_request_id = 1
        self._used_tokens = set()
        return self

    @property
    def cache_eligible(self) -> bool:
        """Return false until a complete approved runtime manifest exists."""

        return False

    @property
    def closed(self) -> bool:
        """Return whether this lease can no longer accept synthesis work."""

        with self._state_lock:
            return self._state in ("closed", "poisoned")

    @property
    def poisoned(self) -> bool:
        """Return whether a failure permanently invalidated this lease."""

        with self._state_lock:
            return self._state == "poisoned"

    def __repr__(self) -> str:
        """Render lifecycle/cache state without selection or challenge data."""

        with self._state_lock:
            state = self._state
        return (
            f"{type(self).__name__}(state={state!r}, cache_eligible=False)"
        )

    def __enter__(self) -> "ManagedGptSovitsLease":
        """Return the already-ready lease for deterministic ownership."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Close the worker when leaving a context."""

        self.close()

    def _poison(self) -> None:
        """Atomically detach one generation and start fail-closed cleanup."""

        resources: _OwnedResources | None
        with self._state_lock:
            if self._state in ("closed", "poisoned"):
                return
            self._state = "poisoned"
            self._active_token = None
            resources, self._resources = self._resources, None
            self._state_lock.notify_all()
        if resources is not None:
            resources.close_async(self._stop_timeout)
        self._on_finished(self, True)

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Return one challenge-bound WAV for a unique queue operation token.

        Request IDs increase monotonically and tokens may never be reused on a
        lease.  Token uniqueness is what prevents an old delayed cancellation
        from matching a later sentence that happens to run on the same child.
        Any I/O, ERROR, timeout, metadata, or WAV defect permanently poisons the
        lease and starts Job teardown; there is no retry or HTTP fallback.
        """

        try:
            request = SynthesisRequest(text=text, language=language)  # type: ignore[arg-type]
        except (SynthesisValidationError, TypeError, ValueError):
            raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE) from None
        token = _require_operation_token(operation_token)
        with self._io_lock:
            with self._state_lock:
                if self._state != "ready" or self._resources is None:
                    raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
                if token in self._cancelled_tokens:
                    # Move the consumed tombstone into the permanent used-token
                    # ledger before allocating an ID. The same token can never
                    # be replayed after its cancelled submission returns.
                    self._cancelled_tokens.remove(token)
                    self._used_tokens.add(token)
                    raise ManagedGptSovitsCancelledError(_CANCELLED_MESSAGE)
                if (
                    token in self._used_tokens
                    or len(self._used_tokens)
                    >= MANAGED_GPT_SOVITS_MAX_REQUESTS_PER_LEASE
                ):
                    raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
                request_id = self._next_request_id
                self._next_request_id += 1
                self._used_tokens.add(token)
                self._active_token = token
                resources = self._resources

            deadline = monotonic() + self._synthesis_timeout
            try:
                _write_with_deadline(
                    resources.transport.request_stream,
                    ProtocolFrame(
                        FrameKind.SYNTHESIZE,
                        request_id,
                        {
                            "challenge": self._challenge,
                            "schema": _SCHEMA_VERSION,
                            "text": request.text,
                            "text_language": request.language,
                        },
                    ),
                    _remaining(deadline),
                    self._writer,
                )
                frame = _read_with_deadline(
                    resources.transport.response_stream,
                    _remaining(deadline),
                    self._reader,
                )
                if frame.kind is FrameKind.ERROR:
                    _require_error_frame(frame, request_id)
                    raise ManagedGptSovitsSynthesisError(_SYNTHESIS_MESSAGE)
                if frame.kind is not FrameKind.AUDIO or frame.request_id != request_id:
                    raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
                metadata = _exact_metadata(frame, _AUDIO_FIELDS)
                sample_rate = metadata["sample_rate"]
                if (
                    type(metadata["schema"]) is not int
                    or metadata["schema"] != _SCHEMA_VERSION
                    or type(metadata["challenge"]) is not str
                    or not hmac.compare_digest(metadata["challenge"], self._challenge)
                    or metadata["format"] != "wav"
                    or type(sample_rate) is not int
                    or not _MIN_SAMPLE_RATE <= sample_rate <= _MAX_SAMPLE_RATE
                ):
                    raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
                _require_managed_wav(frame.payload, sample_rate)
                try:
                    result = SynthesisResult(
                        audio=frame.payload,
                        audio_format="wav",
                        speed_factor=self._speed_factor,
                    )
                except SynthesisValidationError:
                    raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE) from None
                with self._state_lock:
                    if (
                        self._state != "ready"
                        or self._active_token is None
                        or not hmac.compare_digest(self._active_token, token)
                    ):
                        # A matching abort owns the terminal transition.  Audio
                        # that raced with its acknowledgement must not escape to
                        # playback even if the pipe read itself completed.
                        raise ManagedGptSovitsSynthesisError(_SYNTHESIS_MESSAGE)
                    self._active_token = None
            except ManagedGptSovitsValidationError:
                raise
            except ManagedGptSovitsSynthesisError:
                self._poison()
                raise
            except BaseException:
                self._poison()
                raise ManagedGptSovitsSynthesisError(_SYNTHESIS_MESSAGE) from None
            finally:
                with self._state_lock:
                    if self._active_token == token:
                        self._active_token = None
            return result

    def abort(self, operation_token: str) -> bool:
        """Cancel only the exact canonical queue operation token.

        A never-seen token records a bounded pre-start tombstone so an abort that
        wins the scheduling race prevents later pipe I/O. A completed or
        duplicate token is a stale no-op. On an active match, resource ownership
        transfers immediately to a bounded non-daemon cleanup thread. It first
        terminates the Job so child-handle closure wakes protocol I/O, then closes
        parent streams and guards; process exit still waits for that owned cleanup.
        """

        token = _require_operation_token(operation_token)
        resources: _OwnedResources | None = None
        capacity_failed = False
        with self._state_lock:
            if (
                self._state == "ready"
                and self._active_token is not None
                and hmac.compare_digest(self._active_token, token)
            ):
                self._state = "poisoned"
                self._active_token = None
                resources, self._resources = self._resources, None
            elif self._state != "ready":
                return False
            elif token in self._used_tokens or token in self._cancelled_tokens:
                # Completed and duplicate-cancel tokens are stale no-ops.
                return False
            elif (
                len(self._cancelled_tokens)
                >= MANAGED_GPT_SOVITS_MAX_CANCEL_TOMBSTONES
                or len(self._used_tokens) + len(self._cancelled_tokens)
                >= MANAGED_GPT_SOVITS_MAX_REQUESTS_PER_LEASE
            ):
                # Eviction would allow a previously acknowledged cancellation
                # to start later. Exhaustion therefore poisons the whole child.
                self._state = "poisoned"
                self._active_token = None
                resources, self._resources = self._resources, None
                capacity_failed = True
            else:
                self._cancelled_tokens.add(token)
                return True
            self._state_lock.notify_all()
        if resources is not None:
            resources.close_async(self._stop_timeout)
            self._on_finished(self, True)
        if capacity_failed:
            raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
        return resources is not None

    def close(self) -> None:
        """Gracefully stop an idle worker or kill a busy/invalid one directly."""

        resources: _OwnedResources | None
        join_deadline = monotonic() + self._stop_timeout
        with self._state_lock:
            if self._state in ("closed", "poisoned"):
                return
            if self._state == "closing":
                # Exactly one close owner writes STOP and releases resources.
                # Joiners observe that immutable transaction result rather than
                # issuing a second STOP or turning a clean close into poison.
                while self._state == "closing":
                    remaining = join_deadline - monotonic()
                    if remaining <= 0.0:
                        return
                    self._state_lock.wait(remaining)
                return
            if self._active_token is not None:
                self._state = "poisoned"
                self._active_token = None
                resources, self._resources = self._resources, None
                poisoned = True
            else:
                self._state = "closing"
                resources = self._resources
                poisoned = False
            self._state_lock.notify_all()
        if resources is None:
            self._on_finished(self, poisoned)
            return
        if poisoned:
            resources.close_async(self._stop_timeout)
            self._on_finished(self, True)
            return

        graceful = False
        with self._io_lock:
            deadline = monotonic() + self._stop_timeout
            try:
                _write_with_deadline(
                    resources.transport.request_stream,
                    ProtocolFrame(
                        FrameKind.STOP,
                        0,
                        {"challenge": self._challenge, "schema": _SCHEMA_VERSION},
                    ),
                    _remaining(deadline),
                    self._writer,
                )
                frame = _read_with_deadline(
                    resources.transport.response_stream,
                    _remaining(deadline),
                    self._reader,
                )
                if frame.kind is FrameKind.ERROR:
                    _require_error_frame(frame, 0)
                    raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
                metadata = _exact_metadata(frame, _STOPPED_FIELDS)
                graceful = (
                    frame.kind is FrameKind.STOPPED
                    and frame.request_id == 0
                    and type(metadata["schema"]) is int
                    and metadata["schema"] == _SCHEMA_VERSION
                    and type(metadata["challenge"]) is str
                    and hmac.compare_digest(metadata["challenge"], self._challenge)
                )
            except BaseException:
                graceful = False
        try:
            cleanup_succeeded = resources.close(
                max(0.0, deadline - monotonic())
            )
            graceful = graceful and cleanup_succeeded
        finally:
            with self._state_lock:
                self._resources = None
                self._state = "closed" if graceful else "poisoned"
            try:
                self._on_finished(self, not graceful)
            finally:
                # Joiners wake only after both lease and owning runtime have
                # published the same terminal outcome.
                with self._state_lock:
                    self._state_lock.notify_all()

    def _terminate_from_runtime(self) -> None:
        """Detach and finish bounded cleanup during explicit runtime shutdown."""

        resources: _OwnedResources | None
        with self._state_lock:
            if self._state in ("closed", "poisoned"):
                return
            self._state = "closed"
            self._active_token = None
            resources, self._resources = self._resources, None
            self._state_lock.notify_all()
        if resources is not None:
            resources.close(self._stop_timeout)


def _require_operation_token(value: object) -> str:
    """Accept only the queue's canonical 256-bit lowercase-hex identifier."""

    if type(value) is not str or _OPERATION_TOKEN_PATTERN.fullmatch(value) is None:
        raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
    return value


@dataclass(frozen=True, slots=True, repr=False)
class _RuntimeDependencies:
    """Freeze production or sealed-test boundary implementations together."""

    launcher: Callable[..., _ManagedProcess]
    guard_acquirer: Callable[[tuple[Any, ...]], _FileGuard]
    guarded_paths_getter: Callable[[_FileGuard], tuple[str, ...]]
    declaration_factory: Callable[[Path, int, str], Any]
    manifest_digest_factory: Callable[[Path, Path, Path], str]
    candidate_hash_factory: Callable[[Path], tuple[int, str]]
    transport_factory: Callable[[], _Transport]
    challenge_factory: Callable[[], str]
    reader: Callable[[BinaryIO], ProtocolFrame]
    writer: Callable[[BinaryIO, ProtocolFrame], None]
    bootstrap_factory: Callable[[_GuardedLayout], _BootstrapLaunch]
    environment_factory: Callable[[], dict[str, str]]
    platform_name: str


def _launch_into_preallocated_owner(
    launcher: Callable[..., _ManagedProcess],
    argv: list[str],
    resources: _OwnedResources,
    *,
    cwd: str,
    environment: dict[str, str],
) -> _ManagedProcess:
    """Launch once and transfer any returned Job before an exception escapes.

    The cleanup owner and all of its synchronization state already exist. The
    rescue path is idempotent so an interruption at the ordinary adoption call
    cannot expose transport or guards without first attaching the live Job.
    """

    process: _ManagedProcess | None = None
    try:
        process = launcher(
            argv,
            stdin_handle=resources.transport.stdin_handle,
            stdout_handle=resources.transport.stdout_handle,
            stderr_handle=resources.transport.stderr_handle,
            cwd=cwd,
            environment=environment,
        )
        resources.adopt_process(process)
    except BaseException:
        if process is not None:
            resources.adopt_process_for_cleanup(process)
        raise
    return process


class ManagedGptSovitsRuntime:
    """Lazily create and own at most one guarded local worker lease."""

    __slots__ = (
        "_challenge_factory",
        "_candidate_hash_factory",
        "_bootstrap_factory",
        "_config",
        "_declaration_factory",
        "_environment_factory",
        "_guard_acquirer",
        "_guarded_paths_getter",
        "_launcher",
        "_lease",
        "_lock",
        "_manifest_digest_factory",
        "_platform_name",
        "_reader",
        "_starting_resources",
        "_state",
        "_transport_factory",
        "_writer",
    )

    def __init__(
        self,
        config: ManagedGptSovitsConfig,
    ) -> None:
        """Store immutable policy and fixed production boundaries without probing.

        The public constructor has no dependency-injection surface.  This
        prevents application callers from replacing the guard or launcher and
        receiving a genuine lease object around an unguarded fake process.
        """

        dependencies = _RuntimeDependencies(
            launcher=launch_windows_managed_process,
            guard_acquirer=_default_guard_acquirer,
            guarded_paths_getter=_default_guarded_paths_getter,
            declaration_factory=_default_declaration_factory,
            manifest_digest_factory=_default_manifest_digest_factory,
            candidate_hash_factory=_hash_candidate_file,
            transport_factory=_default_transport_factory,
            challenge_factory=lambda: secrets.token_hex(32),
            reader=read_frame,
            writer=write_frame,
            bootstrap_factory=_default_bootstrap_factory,
            environment_factory=_minimal_environment,
            platform_name=os.name,
        )
        self._initialize(config, dependencies)

    @classmethod
    def _create_for_testing(
        cls,
        config: ManagedGptSovitsConfig,
        *,
        dependency_seal: object,
        dependencies: _RuntimeDependencies,
    ) -> "ManagedGptSovitsRuntime":
        """Create an exact runtime through the module-private test boundary.

        This is intentionally absent from ``__all__`` and requires a private
        identity seal.  Public production construction cannot carry injected
        dependencies across the trust boundary.
        """

        if (
            cls is not ManagedGptSovitsRuntime
            or dependency_seal is not _TEST_DEPENDENCY_SEAL
            or type(dependencies) is not _RuntimeDependencies
        ):
            raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
        runtime = object.__new__(cls)
        runtime._initialize(config, dependencies)
        return runtime

    def _initialize(
        self,
        config: ManagedGptSovitsConfig,
        dependencies: _RuntimeDependencies,
    ) -> None:
        """Initialize common state after public or sealed-test construction."""

        if type(config) is not ManagedGptSovitsConfig:
            raise TypeError("config must be a ManagedGptSovitsConfig.")
        # Retain a second exact snapshot rather than the caller's object. Even
        # frozen dataclasses can be mutated deliberately via ``object`` APIs;
        # such later mutation must not alter a pending security decision.
        self._config = ManagedGptSovitsConfig(
            runtime_root=config.runtime_root,
            worker_script=config.worker_script,
            startup_timeout_seconds=config.startup_timeout_seconds,
            synthesis_timeout_seconds=config.synthesis_timeout_seconds,
            stop_timeout_seconds=config.stop_timeout_seconds,
            device=config.device,
            seed=config.seed,
        )
        self._launcher = dependencies.launcher
        self._guard_acquirer = dependencies.guard_acquirer
        self._guarded_paths_getter = dependencies.guarded_paths_getter
        self._declaration_factory = dependencies.declaration_factory
        self._manifest_digest_factory = dependencies.manifest_digest_factory
        self._transport_factory = dependencies.transport_factory
        self._challenge_factory = dependencies.challenge_factory
        self._candidate_hash_factory = dependencies.candidate_hash_factory
        self._bootstrap_factory = dependencies.bootstrap_factory
        self._environment_factory = dependencies.environment_factory
        self._reader = dependencies.reader
        self._writer = dependencies.writer
        self._platform_name = dependencies.platform_name
        self._lock = RLock()
        self._lease: ManagedGptSovitsLease | None = None
        self._starting_resources: _OwnedResources | None = None
        self._state: ManagedGptSovitsState = (
            "idle" if dependencies.platform_name == "nt" else "unavailable"
        )

    def __repr__(self) -> str:
        """Render only safe in-memory lifecycle state."""

        return f"{type(self).__name__}(state={self.get_status().state!r})"

    def __del__(self) -> None:
        """Best-effort initiate Job teardown if explicit ownership was omitted."""

        try:
            self.shutdown()
        except BaseException:
            pass

    def get_status(self) -> ManagedGptSovitsStatus:
        """Return a no-probe snapshot; this method never starts the worker."""

        with self._lock:
            state = self._state
        return ManagedGptSovitsStatus(
            state=state,
            available=state in ("idle", "ready"),
            cache_eligible=False,
        )

    def _build_guard_declarations(self, selection: _SelectionSnapshot) -> tuple[Any, ...]:
        """Build three declared assets plus all measured runtime candidates."""

        declarations = [
            self._declaration_factory(
                selection.gpt_weights.candidate_path,
                selection.gpt_weights.size_bytes,
                selection.gpt_weights.sha256,
            ),
            self._declaration_factory(
                selection.sovits_weights.candidate_path,
                selection.sovits_weights.size_bytes,
                selection.sovits_weights.sha256,
            ),
            self._declaration_factory(
                selection.reference_audio.candidate_path,
                selection.reference_audio.size_bytes,
                selection.reference_audio.sha256,
            ),
        ]
        anchor_paths = (
            self._config.worker_script,
            self._config.worker_script.with_name("gpt_sovits_protocol.py"),
            *(
                self._config.runtime_root / Path(relative)
                for _role, relative in _RUNTIME_MANIFEST_RELATIVE_FILES
            ),
        )
        for path in anchor_paths:
            size_bytes, sha256 = self._candidate_hash_factory(path)
            declarations.append(
                self._declaration_factory(path, size_bytes, sha256)
            )
        return tuple(declarations)

    def acquire_lease(self, selection: object) -> ManagedGptSovitsLease:
        """Guard one private catalog selection and complete an exact READY.

        The acquisition performs one attempt only.  Guard, manifest, launch,
        ERROR, timeout, challenge, or digest failure poisons this runtime and
        tears down all acquired resources; callers must create a new runtime
        explicitly rather than receiving an implicit retry or HTTP fallback.
        """

        snapshot = _snapshot_selection(selection)
        if snapshot.audio_format != "wav":
            raise ManagedGptSovitsValidationError(_REQUEST_INVALID_MESSAGE)
        with self._lock:
            if self._state != "idle":
                raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
            self._state = "starting"

        guard: _FileGuard | None = None
        bootstrap_guard: _FileGuard | None = None
        transport: _Transport | None = None
        process: _ManagedProcess | None = None
        resources: _OwnedResources | None = None
        try:
            if self._platform_name != "nt":
                raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
            if self._bootstrap_factory is _default_bootstrap_factory:
                with _QUARANTINED_GUARDS_LOCK:
                    if (
                        _GLOBAL_CLEANUP_POISONED
                        or _GLOBAL_CLEANUP_PENDING > 0
                    ):
                        raise ManagedGptSovitsUnavailableError(
                            _UNAVAILABLE_MESSAGE
                        )
            if (
                self._guarded_paths_getter is _default_guarded_paths_getter
                and os.path.normcase(str(self._config.worker_script))
                != os.path.normcase(str(_CANONICAL_WORKER_SCRIPT))
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            declarations = self._build_guard_declarations(snapshot)
            guard = self._guard_acquirer(declarations)
            layout = _guarded_layout(
                self._guarded_paths_getter(guard),
                declarations,
                self._config.runtime_root,
            )
            declared_manifest_digest = _guarded_runtime_manifest_digest(
                declarations
            )
            manifest_digest = self._manifest_digest_factory(
                layout.runtime_root,
                layout.worker_path,
                layout.protocol_path,
            )
            if (
                type(manifest_digest) is not str
                or _SHA256_PATTERN.fullmatch(manifest_digest) is None
                or not hmac.compare_digest(
                    manifest_digest, declared_manifest_digest
                )
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            challenge = self._challenge_factory()
            if (
                type(challenge) is not str
                or _SHA256_PATTERN.fullmatch(challenge) is None
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            speed_milli = int(round(snapshot.speed_factor * 1_000.0))
            gpt_path = layout.gpt_path
            sovits_path = layout.sovits_path
            reference_path = layout.reference_path
            binding_sha256 = _canonical_binding(
                runtime_root=layout.runtime_root,
                gpt_path=gpt_path,
                gpt_size=snapshot.gpt_weights.size_bytes,
                gpt_sha256=snapshot.gpt_weights.sha256,
                sovits_path=sovits_path,
                sovits_size=snapshot.sovits_weights.size_bytes,
                sovits_sha256=snapshot.sovits_weights.sha256,
                reference_path=reference_path,
                reference_size=snapshot.reference_audio.size_bytes,
                reference_sha256=snapshot.reference_audio.sha256,
                prompt_text=snapshot.prompt_text,
                prompt_language=snapshot.prompt_language,
                speed_milli=speed_milli,
                seed=self._config.seed,
                device=self._config.device,
                runtime_manifest_digest=manifest_digest,
            )
            init_metadata = {
                "challenge": challenge,
                "device": self._config.device,
                "gpt_weights": {
                    "bytes": snapshot.gpt_weights.size_bytes,
                    "path": str(gpt_path),
                    "sha256": snapshot.gpt_weights.sha256,
                },
                "prompt_language": snapshot.prompt_language,
                "prompt_text": snapshot.prompt_text,
                "reference_audio": {
                    "bytes": snapshot.reference_audio.size_bytes,
                    "path": str(reference_path),
                    "sha256": snapshot.reference_audio.sha256,
                },
                "runtime_manifest_digest": manifest_digest,
                "runtime_root": str(layout.runtime_root),
                "schema": _SCHEMA_VERSION,
                "seed": self._config.seed,
                "sovits_weights": {
                    "bytes": snapshot.sovits_weights.size_bytes,
                    "path": str(sovits_path),
                    "sha256": snapshot.sovits_weights.sha256,
                },
                "speed_milli": speed_milli,
            }
            bootstrap = self._bootstrap_factory(layout)
            if type(bootstrap) is not _BootstrapLaunch:
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            bootstrap_guard = bootstrap.guard
            if (
                type(bootstrap.executable) is not str
                or _MAPPED_DRIVE_PATH_PATTERN.fullmatch(
                    bootstrap.executable
                ) is None
                or "\x00" in bootstrap.executable
                or len(bootstrap.executable) > 32_767
                or not ntpath.isabs(bootstrap.executable)
                or ntpath.normpath(bootstrap.executable)
                != bootstrap.executable
                or type(bootstrap.import_root) is not str
                or _MAPPED_DRIVE_PATH_PATTERN.fullmatch(
                    bootstrap.import_root
                ) is None
                or bootstrap.import_root[:2].casefold()
                != bootstrap.executable[:2].casefold()
                or type(bootstrap.stable_executable) is not _BUILTIN_PATH_TYPE
                or _VOLUME_GUID_PATH_PATTERN.fullmatch(
                    str(bootstrap.stable_executable)
                ) is None
                or type(bootstrap.executable_identity)
                is not _DeclaredIdentity
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            combined_guard = _CombinedGuard(guard, bootstrap_guard)
            guard = combined_guard
            bootstrap_guard = None
            environment = self._environment_factory()
            if (
                type(environment) is not dict
                or any(type(key) is not str or type(value) is not str for key, value in environment.items())
                or set(environment)
                != {"LANGUAGE", "PATH", "SystemRoot", "USERPROFILE"}
            ):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            transport = self._transport_factory()
            # Allocate every cleanup lock/condition before CreateProcessW. If
            # allocation fails persistently, no child exists and the outer
            # transaction can still close the ordinary transport and guards.
            resources = _OwnedResources(transport, None, guard)
            guard = None
            transport = None
            with self._lock:
                if self._state != "starting":
                    raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
                if self._bootstrap_factory is _default_bootstrap_factory:
                    # Recheck at the final pre-CreateProcess boundary. The
                    # executable remains file-guarded and its temporary alias
                    # must still name the exact fixed volume and bytes.
                    _verify_production_bootstrap(bootstrap)
                    # The final check and CreateProcess linearize under the
                    # same gate used by cleanup reservations. Cleanup that wins
                    # first blocks this launch; a launch that wins first has its
                    # Job adopted before cleanup can publish a pending barrier.
                    with _QUARANTINED_GUARDS_LOCK:
                        if (
                            _GLOBAL_CLEANUP_POISONED
                            or _GLOBAL_CLEANUP_PENDING > 0
                        ):
                            raise ManagedGptSovitsUnavailableError(
                                _UNAVAILABLE_MESSAGE
                            )
                        resources.reserve_production_owner_under_gate()
                        process = _launch_into_preallocated_owner(
                            self._launcher,
                            [
                                bootstrap.executable,
                                "-I",
                                "-B",
                                "-u",
                                str(layout.worker_path),
                                bootstrap.import_root,
                            ],
                            resources,
                            cwd=str(layout.runtime_root),
                            environment=environment,
                        )
                else:
                    process = _launch_into_preallocated_owner(
                        self._launcher,
                        [
                            bootstrap.executable,
                            "-I",
                            "-B",
                            "-u",
                            str(layout.worker_path),
                            bootstrap.import_root,
                        ],
                        resources,
                        cwd=str(layout.runtime_root),
                        environment=environment,
                    )
                if self._bootstrap_factory is _default_bootstrap_factory:
                    _verify_launched_process_image(process, bootstrap)
                self._starting_resources = resources
                process = None
                resources.transport.close_child_ends()

            deadline = monotonic() + self._config.startup_timeout_seconds
            _write_with_deadline(
                resources.transport.request_stream,
                ProtocolFrame(FrameKind.INIT, 0, init_metadata),
                _remaining(deadline),
                self._writer,
            )
            frame = _read_with_deadline(
                resources.transport.response_stream,
                _remaining(deadline),
                self._reader,
            )
            if frame.kind is FrameKind.ERROR:
                _require_error_frame(frame, 0)
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
            if frame.kind is not FrameKind.READY or frame.request_id != 0:
                raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
            metadata = _exact_metadata(frame, _READY_FIELDS)
            if (
                type(metadata["schema"]) is not int
                or metadata["schema"] != _SCHEMA_VERSION
                or type(metadata["challenge"]) is not str
                or not hmac.compare_digest(metadata["challenge"], challenge)
                or type(metadata["binding_sha256"]) is not str
                or not hmac.compare_digest(
                    metadata["binding_sha256"], binding_sha256
                )
            ):
                raise ManagedGptSovitsProtocolError(_PROTOCOL_MESSAGE)
            lease = ManagedGptSovitsLease._issue(
                issuer_seal=_LEASE_ISSUER_SEAL,
                resources=resources,
                challenge=challenge,
                speed_factor=snapshot.speed_factor,
                synthesis_timeout=self._config.synthesis_timeout_seconds,
                stop_timeout=self._config.stop_timeout_seconds,
                reader=self._reader,
                writer=self._writer,
                on_finished=self._lease_finished,
            )
            with self._lock:
                if self._state != "starting" or self._starting_resources is not resources:
                    raise ManagedGptSovitsUnavailableError(_UNAVAILABLE_MESSAGE)
                self._starting_resources = None
                self._lease = lease
                self._state = "ready"
            resources = None
            return lease
        except ManagedGptSovitsValidationError:
            raise
        except BaseException as error:
            if resources is not None:
                if process is not None:
                    # Retry the allocation-free rescue if an asynchronous
                    # exception interrupted the inner exception handler itself.
                    resources.adopt_process_for_cleanup(process)
                    process = None
                resources.close(self._config.stop_timeout_seconds)
            elif process is not None and guard is not None:
                _terminate_then_release_or_quarantine(
                    process,
                    guard,
                    self._config.stop_timeout_seconds,
                )
                process = None
                guard = None
            elif process is not None:
                try:
                    process.terminate(self._config.stop_timeout_seconds)
                except BaseException:
                    pass
            if transport is not None:
                # A transport can fail while closing a native descriptor. It
                # must not interrupt guard/mapping cleanup or leave lifecycle
                # state at ``starting``; retain its ownership and latch future
                # production launches just like any other ambiguous resource.
                _close_or_quarantine(transport)
            if bootstrap_guard is not None:
                _close_or_quarantine(bootstrap_guard)
            if guard is not None:
                _close_or_quarantine(guard)
            with self._lock:
                self._starting_resources = None
                if self._state != "closed":
                    self._state = "poisoned"
            if isinstance(error, ManagedGptSovitsUnavailableError):
                raise
            if isinstance(error, ManagedGptSovitsProtocolError):
                raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None
            raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE) from None

    def _lease_finished(
        self, lease: ManagedGptSovitsLease, poisoned: bool
    ) -> None:
        """Accept one terminal callback only from the currently owned lease."""

        with self._lock:
            if self._lease is not lease:
                return
            self._lease = None
            if self._state == "closed":
                return
            self._state = "poisoned" if poisoned else "idle"

    def shutdown(self) -> None:
        """Permanently close and own or join process-first active cleanup.

        Native Job waiting and concurrent joining use the configured timeout;
        post-termination stream and guard close calls have no Python-level
        wall-clock deadline and may finish synchronously in the owning caller.
        """

        lease: ManagedGptSovitsLease | None
        resources: _OwnedResources | None
        with self._lock:
            if self._state == "closed":
                return
            self._state = "closed"
            lease, self._lease = self._lease, None
            resources, self._starting_resources = self._starting_resources, None
        if lease is not None:
            lease._terminate_from_runtime()
        if resources is not None:
            resources.close(self._config.stop_timeout_seconds)

    def close(self) -> None:
        """Alias :meth:`shutdown` for ordinary context-style ownership."""

        self.shutdown()

    def __enter__(self) -> "ManagedGptSovitsRuntime":
        """Return this lazily initialized runtime owner."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Shut down the runtime when leaving a context."""

        self.shutdown()


def _authoritative_windows_directory(function_name: str) -> str:
    """Read one OS directory from kernel32 instead of attacker-controlled env."""

    if os.name != "nt" or function_name not in (
        "GetSystemWindowsDirectoryW",
        "GetSystemDirectoryW",
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = getattr(kernel32, function_name)
    function.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    function.restype = wintypes.UINT
    capacity = 32_768
    buffer = ctypes.create_unicode_buffer(capacity)
    length = int(function(buffer, capacity))
    value = buffer.value
    if (
        length <= 0
        or length >= capacity
        or length != len(value)
        or not value
        or "\x00" in value
        or ";" in value
        or not ntpath.isabs(value)
        or ntpath.normpath(value) != value
    ):
        raise ManagedGptSovitsStartupError(_STARTUP_MESSAGE)
    return value


def _minimal_environment() -> dict[str, str]:
    """Build a closed child environment from authoritative Windows APIs only.

    The runtime itself is never placed on ``PATH``: guarded FFmpeg is invoked
    by its absolute stable device path.  Keeping only the kernel-reported
    System32 directory prevents inherited secrets and a forged ``SystemRoot``
    from redirecting any unavoidable operating-system helper lookup. PyTorch
    computes a default crash-report path with :meth:`pathlib.Path.home` during
    import, so ``USERPROFILE`` deliberately names the same authoritative,
    non-user-specific Windows directory instead of inheriting a real profile.
    """

    system_root = _authoritative_windows_directory(
        "GetSystemWindowsDirectoryW"
    )
    system_directory = _authoritative_windows_directory("GetSystemDirectoryW")
    return {
        "LANGUAGE": "en_US",
        "PATH": system_directory,
        "SystemRoot": system_root,
        "USERPROFILE": system_root,
    }


__all__ = [
    "MANAGED_GPT_SOVITS_MAX_CANCEL_TOMBSTONES",
    "MANAGED_GPT_SOVITS_MAX_REQUESTS_PER_LEASE",
    "MANAGED_GPT_SOVITS_MAX_TIMEOUT_SECONDS",
    "ManagedGptSovitsConfig",
    "ManagedGptSovitsCancelledError",
    "ManagedGptSovitsDevice",
    "ManagedGptSovitsError",
    "ManagedGptSovitsLease",
    "ManagedGptSovitsProtocolError",
    "ManagedGptSovitsRuntime",
    "ManagedGptSovitsStartupError",
    "ManagedGptSovitsState",
    "ManagedGptSovitsStatus",
    "ManagedGptSovitsSynthesisError",
    "ManagedGptSovitsUnavailableError",
    "ManagedGptSovitsValidationError",
]
