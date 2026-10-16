"""Own one isolated ComfyUI child process for each image-generation job.

The raw HTTP adapter intentionally cannot enter the trusted generation core.
This supervisor supplies the missing ownership proof: it verifies pinned local
runtime files, starts ComfyUI inside a kill-on-close Windows Job Object, checks
that the randomly selected loopback listener belongs to that exact child, and
removes the whole per-job directory before a result can be published.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import socket
import stat
from threading import Lock, RLock
from time import monotonic, sleep
from typing import Protocol

from voice._windows_file_guard import (
    GuardedFileDeclaration,
    WindowsReadOnlyFileGuardSet,
    _GUARDED_VOLUME_PATH_ACCESS_SEAL,
    _get_guarded_volume_paths,
)
from voice._windows_managed_process import (
    WindowsManagedProcess,
    launch_windows_managed_process,
)

from ._private_run_directory import (
    PrivateRunDirectory,
    create_private_run_directory,
    remove_private_run_directory,
)
from ._windows_loopback_owner import (
    windows_ipv4_loopback_listener_is_owned,
)
from .comfyui import (
    ComfyOutputDescriptor,
    ComfyPromptHandle,
    ComfyUiConfig,
    HttpComfyUiClient,
)
from .exceptions import (
    ImageGenerationProtocolError,
    ImageGenerationUnavailableError,
)
from .png import CanonicalPng
from .profiles import ModelProfile
from .service import _MANAGED_RUNTIME_CAPABILITY
from .workflow import CompiledWorkflow


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_DRIVE_FIXED = 3
_TEST_DEPENDENCY_SEAL = object()
_UNAVAILABLE_MESSAGE = "The managed local image runtime is unavailable."
_CLEANUP_MESSAGE = "The managed local image runtime could not be cleaned safely."


class _ManagedProcess(Protocol):
    """Describe the owned process operations used by this supervisor."""

    @property
    def pid(self) -> int:
        """Return the root child process identifier."""

    @property
    def closed(self) -> bool:
        """Return whether process-tree teardown and handle cleanup finished."""

    def is_alive(self) -> bool:
        """Return a conservative immediate root-process liveness snapshot."""

    def terminate(self, timeout_seconds: float = 5.0) -> bool:
        """Terminate the complete owned process tree within a time bound."""


class _FileGuard(Protocol):
    """Describe the immutable-file guard lifetime used by one child."""

    @property
    def closed(self) -> bool:
        """Return whether every guarded native handle has been released."""

    def close(self) -> None:
        """Release every guarded native handle or raise a stable failure."""


class _RawClient(Protocol):
    """Describe the reviewed raw ComfyUI adapter behind the supervisor."""

    def ensure_ready(self, profile: ModelProfile) -> None:
        """Probe required built-in nodes and the selected checkpoint key."""

    def probe_ready(self) -> None:
        """Confirm that the private server finished API route initialization."""

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Mint a correlated prompt handle without submitting native work."""

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> None:
        """Submit one fixed graph under its pre-issued correlation handle."""

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Wait for one correlated output or a stable terminal failure."""

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Fetch and validate one opaque output descriptor."""

    def cancel(self, handle: ComfyPromptHandle) -> bool:
        """Best-effort cancel one prompt on this exclusive instance."""


class _NullStandardHandles:
    """Own three NUL descriptors long enough for the launcher to duplicate."""

    __slots__ = ("_descriptors", "stderr_handle", "stdin_handle", "stdout_handle")

    def __init__(self) -> None:
        """Open non-inherited NUL descriptors and expose their Windows handles."""

        if os.name != "nt":
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
        import msvcrt

        descriptors: list[int] = []
        try:
            stdin_fd = os.open(
                os.devnull,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            descriptors.append(stdin_fd)
            stdout_fd = os.open(
                os.devnull,
                os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
            descriptors.append(stdout_fd)
            stderr_fd = os.open(
                os.devnull,
                os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
            descriptors.append(stderr_fd)
            self.stdin_handle = int(msvcrt.get_osfhandle(stdin_fd))
            self.stdout_handle = int(msvcrt.get_osfhandle(stdout_fd))
            self.stderr_handle = int(msvcrt.get_osfhandle(stderr_fd))
            self._descriptors = descriptors
        except BaseException:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from None

    def close(self) -> None:
        """Close each caller-owned descriptor exactly once."""

        remaining: list[int] = []
        for descriptor in self._descriptors:
            try:
                os.close(descriptor)
            except OSError:
                remaining.append(descriptor)
        self._descriptors = remaining
        if remaining:
            raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE)


class _StandardHandles(Protocol):
    """Describe three launch handles and their deterministic owner."""

    stdin_handle: int
    stdout_handle: int
    stderr_handle: int

    def close(self) -> None:
        """Release every caller-owned launch handle."""


@dataclass(frozen=True, slots=True)
class ManagedComfyUiConfig:
    """Configure one trusted local ComfyUI installation and private state root."""

    runtime_root: Path
    python_executable: Path
    models_root: Path
    private_state_root: Path
    python_sha256: str
    comfyui_main_sha256: str
    startup_timeout_seconds: float = 90.0
    request_timeout_seconds: float = 30.0
    job_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 0.25
    stop_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        """Normalize lexical paths and reject unbounded or ambiguous policy."""

        for field_name in (
            "runtime_root",
            "python_executable",
            "models_root",
            "private_state_root",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Path):
                raise TypeError(f"{field_name} must be a Path.")
            normalized = Path(os.path.normpath(str(value)))
            if not normalized.is_absolute() or "\x00" in str(normalized):
                raise ValueError(f"{field_name} must be an absolute normalized path.")
            object.__setattr__(self, field_name, normalized)
        private_root = self.private_state_root
        for protected_path in (
            self.runtime_root,
            self.models_root,
            self.python_executable,
        ):
            if (
                private_root == protected_path
                or private_root in protected_path.parents
                or protected_path in private_root.parents
            ):
                # Whole-run deletion is the privacy boundary.  It must never
                # be able to reach the runtime, interpreter, or model store.
                raise ValueError(
                    "private_state_root must not overlap trusted runtime inputs."
                )
        for field_name in ("python_sha256", "comfyui_main_sha256"):
            value = getattr(self, field_name)
            if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest.")
        for field_name, maximum in (
            ("startup_timeout_seconds", 600.0),
            ("request_timeout_seconds", 120.0),
            ("job_timeout_seconds", 3_600.0),
            ("poll_interval_seconds", 5.0),
            ("stop_timeout_seconds", 600.0),
        ):
            value = getattr(self, field_name)
            if (
                type(value) is not float
                or not math.isfinite(value)
                or value <= 0.0
                or value > maximum
            ):
                raise ValueError(f"{field_name} is outside its supported bound.")

    @property
    def main_script(self) -> Path:
        """Return the expected ComfyUI entry point below the trusted runtime."""

        return self.runtime_root / "main.py"


@dataclass(frozen=True, slots=True)
class _Preflight:
    """Remember a side-effect-free verified profile and file declarations."""

    profile: ModelProfile
    declarations: tuple[GuardedFileDeclaration, ...]


@dataclass(slots=True)
class _ActiveSession:
    """Own all resources and correlation state for one private child."""

    run: PrivateRunDirectory
    guard: _FileGuard
    process: _ManagedProcess
    raw: _RawClient
    port: int
    handle: ComfyPromptHandle
    workflow_digest: str
    cancel_attempted: bool = False
    process_closed: bool = False
    guard_closed: bool = False
    run_removed: bool = False
    submitted: bool = False
    output: ComfyOutputDescriptor | None = None


@dataclass(frozen=True, slots=True)
class _Dependencies:
    """Collect sealed production seams used by deterministic unit tests."""

    launcher: Callable[..., _ManagedProcess]
    guard_acquirer: Callable[[tuple[GuardedFileDeclaration, ...]], _FileGuard]
    guarded_paths_getter: Callable[[_FileGuard], tuple[str, ...]]
    raw_factory: Callable[[ComfyUiConfig], _RawClient]
    run_factory: Callable[[Path], PrivateRunDirectory]
    run_remover: Callable[[PrivateRunDirectory], None]
    port_allocator: Callable[[], int]
    owner_verifier: Callable[[int, int], bool]
    transport_factory: Callable[[], _StandardHandles]
    environment_factory: Callable[[PrivateRunDirectory], dict[str, str]]
    path_volume_verifier: Callable[[Path], None]
    monotonic_clock: Callable[[], float]
    sleeper: Callable[[float], None]
    platform_name: str


def _allocate_loopback_port() -> int:
    """Reserve and release one ephemeral IPv4 loopback port for immediate use."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = int(candidate.getsockname()[1])
    if not 1 <= port <= 65_535:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    return port


def _is_reparse(stat_result: os.stat_result) -> bool:
    """Return whether a Windows stat snapshot identifies a reparse point."""

    return bool(
        int(getattr(stat_result, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _require_safe_existing_path(path: Path, *, directory: bool) -> os.stat_result:
    """Reject links, reparse points, aliases, and unexpected filesystem types."""

    current = path
    chain: list[Path] = []
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for component in reversed(chain):
        try:
            snapshot = os.lstat(component)
        except OSError as error:
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
        if stat.S_ISLNK(snapshot.st_mode) or _is_reparse(snapshot):
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    snapshot = os.lstat(path)
    expected = stat.S_ISDIR(snapshot.st_mode) if directory else stat.S_ISREG(snapshot.st_mode)
    if not expected:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    return snapshot


def _require_private_state_ownership(
    trusted_root: Path,
    *,
    expected_run: Path | None,
) -> None:
    """Require an empty root or the caller's single exact run directory.

    A leftover UUID directory can contain prompts or generated images from a
    previous hard crash, while an unexpected sibling can belong to another
    live Elysia process.  Neither case is safe to adopt or delete
    automatically.  Rechecking after allocation also turns a concurrent
    launcher race into a fail-closed rollback of only this caller's run.
    """

    _require_safe_existing_path(trusted_root, directory=True)
    if expected_run is not None and expected_run.parent != trusted_root:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    try:
        with os.scandir(trusted_root) as entries:
            entry_names = {entry.name for entry in entries}
    except OSError as error:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
    expected_names = set() if expected_run is None else {expected_run.name}
    if entry_names != expected_names:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)


def _require_missing_path(path: Path) -> None:
    """Accept only an actually absent path without swallowing access errors."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
    raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)


def _require_fixed_local_volume(path: Path) -> None:
    """Reject remote, removable, optical, and namespace-ambiguous storage."""

    if os.name != "nt":
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    import ctypes
    from ctypes import wintypes

    anchor = path.anchor
    if len(anchor) != 3 or anchor[1:] != ":\\":
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetDriveTypeW
    function.argtypes = [wintypes.LPCWSTR]
    function.restype = wintypes.UINT
    if int(function(anchor)) != _DRIVE_FIXED:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)


def _hash_stable_file(path: Path) -> tuple[int, str]:
    """Hash one regular non-reparse file and reject mutation during the read."""

    before = _require_safe_existing_path(path, directory=False)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as error:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
    after = os.lstat(path)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or _is_reparse(after):
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    return int(after.st_size), digest.hexdigest()


def _checkpoint_path(models_root: Path, checkpoint_name: str) -> Path:
    """Resolve a validated ComfyUI checkpoint key below models/checkpoints."""

    parts = checkpoint_name.replace("\\", "/").split("/")
    candidate = models_root / "checkpoints"
    for part in parts:
        candidate = candidate / part
    return candidate


def _authoritative_windows_directory(function_name: str) -> str:
    """Read an operating-system directory without trusting parent variables."""

    if os.name != "nt" or function_name not in {
        "GetSystemWindowsDirectoryW",
        "GetSystemDirectoryW",
    }:
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
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
    if length <= 0 or length >= capacity or length != len(value):
        raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
    return value


def _closed_environment(run: PrivateRunDirectory) -> dict[str, str]:
    """Build a minimal child environment without inherited secrets or proxies."""

    system_root = _authoritative_windows_directory(
        "GetSystemWindowsDirectoryW"
    )
    system32 = _authoritative_windows_directory("GetSystemDirectoryW")
    return {
        "APPDATA": str(run.cache),
        "CUDA_CACHE_PATH": str(run.cache),
        "HF_HOME": str(run.cache),
        "HOME": str(run.user),
        "HUGGINGFACE_HUB_CACHE": str(run.cache),
        "LANGUAGE": "en_US",
        "LOCALAPPDATA": str(run.cache),
        "MPLCONFIGDIR": str(run.cache),
        "NUMBA_CACHE_DIR": str(run.cache),
        "PATH": system32,
        "PIP_CACHE_DIR": str(run.cache),
        "SystemRoot": system_root,
        "TEMP": str(run.temp),
        "TORCH_HOME": str(run.cache),
        "TRITON_CACHE_DIR": str(run.cache),
        "TMP": str(run.temp),
        "USERPROFILE": str(run.user),
        "XDG_CACHE_HOME": str(run.cache),
    }


def _launch_production(
    argv: list[str],
    *,
    stdin_handle: int,
    stdout_handle: int,
    stderr_handle: int,
    cwd: str,
    environment: dict[str, str],
) -> WindowsManagedProcess:
    """Adapt the reviewed Windows launcher to the dependency protocol."""

    return launch_windows_managed_process(
        argv,
        stdin_handle=stdin_handle,
        stdout_handle=stdout_handle,
        stderr_handle=stderr_handle,
        cwd=cwd,
        environment=environment,
    )


def _production_dependencies() -> _Dependencies:
    """Return the sealed production implementations for every privileged seam."""

    return _Dependencies(
        launcher=_launch_production,
        guard_acquirer=WindowsReadOnlyFileGuardSet.acquire,
        guarded_paths_getter=lambda guard: _get_guarded_volume_paths(
            guard,
            access_seal=_GUARDED_VOLUME_PATH_ACCESS_SEAL,
        ),
        raw_factory=lambda config: HttpComfyUiClient(config),
        run_factory=create_private_run_directory,
        run_remover=remove_private_run_directory,
        port_allocator=_allocate_loopback_port,
        owner_verifier=windows_ipv4_loopback_listener_is_owned,
        transport_factory=_NullStandardHandles,
        environment_factory=_closed_environment,
        path_volume_verifier=_require_fixed_local_volume,
        monotonic_clock=monotonic,
        sleeper=sleep,
        platform_name=os.name,
    )


class ManagedComfyUiRuntime:
    """Supervise one-at-a-time private ComfyUI jobs with fail-closed cleanup."""

    def __init__(self, config: ManagedComfyUiConfig) -> None:
        """Capture trusted configuration without starting a child process."""

        self._initialize(config, _production_dependencies())

    @classmethod
    def _create_for_testing(
        cls,
        config: ManagedComfyUiConfig,
        dependencies: _Dependencies,
        *,
        dependency_seal: object,
    ) -> "ManagedComfyUiRuntime":
        """Create a deterministic fixture only through the module-private seal."""

        if cls is not ManagedComfyUiRuntime or dependency_seal is not _TEST_DEPENDENCY_SEAL:
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
        instance = object.__new__(cls)
        instance._initialize(config, dependencies)
        return instance

    def _initialize(
        self,
        config: ManagedComfyUiConfig,
        dependencies: _Dependencies,
    ) -> None:
        """Copy immutable inputs and initialize the lifecycle state machine."""

        if type(config) is not ManagedComfyUiConfig:
            raise TypeError("config must be ManagedComfyUiConfig.")
        if type(dependencies) is not _Dependencies:
            raise TypeError("dependencies must be the sealed dependency set.")
        self._config = ManagedComfyUiConfig(
            runtime_root=config.runtime_root,
            python_executable=config.python_executable,
            models_root=config.models_root,
            private_state_root=config.private_state_root,
            python_sha256=config.python_sha256,
            comfyui_main_sha256=config.comfyui_main_sha256,
            startup_timeout_seconds=config.startup_timeout_seconds,
            request_timeout_seconds=config.request_timeout_seconds,
            job_timeout_seconds=config.job_timeout_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
            stop_timeout_seconds=config.stop_timeout_seconds,
        )
        self._dependencies = dependencies
        self._lock = RLock()
        # Terminal operations include slow native teardown outside `_lock`.
        # Serialize the complete select/cleanup/publish sequence so concurrent
        # close/finalize callers cannot delete the same run twice or reopen a
        # runtime after another caller permanently closed it.
        self._terminal_lock = Lock()
        self._state = "idle"
        self._preflight: _Preflight | None = None
        self._active: _ActiveSession | None = None
        self._last_finalized_handle: ComfyPromptHandle | None = None

    @property
    def is_managed_private_runtime(self) -> bool:
        """Identify this exact supervisor as an owned private runtime."""

        return True

    @property
    def gpu_cleanup_complete(self) -> bool:
        """Prove no private native process or uncertain rollback retains GPU use.

        ``finalize`` transitions to idle only after process termination, file
        guards, and run-directory cleanup all succeed. A poisoned startup may
        have no correlated active handle, so absence of that handle alone is
        insufficient evidence for releasing a global GPU grant.
        """

        with self._lock:
            return self._active is None and self._state in {"idle", "preflighted", "closed"}

    @property
    def managed_runtime_capability(self) -> object:
        """Return the composition capability accepted by the trusted core."""

        return _MANAGED_RUNTIME_CAPABILITY

    def ensure_ready(self, profile: ModelProfile) -> None:
        """Verify trusted files without allocating runtime resources.

        The core checks cancellation immediately after this call without a
        matching finalizer.  Consequently this phase may retain only immutable
        in-memory facts; process, port, native guard, and directory allocation
        all begin later inside :meth:`prepare`.
        """

        if type(profile) is not ModelProfile:
            raise TypeError("profile must be ModelProfile.")
        with self._lock:
            if self._state not in {"idle", "preflighted"}:
                raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
            # Revalidation replaces, rather than layers on, an earlier
            # profile.  A failed second check must never leave a stale profile
            # eligible for later prepare().
            self._preflight = None
            self._state = "checking"
        try:
            if self._dependencies.platform_name != "nt":
                raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
            for path in (
                self._config.runtime_root,
                self._config.models_root,
                self._config.models_root / "checkpoints",
                self._config.private_state_root,
            ):
                _require_safe_existing_path(path, directory=True)
                self._dependencies.path_volume_verifier(path)
            self._dependencies.path_volume_verifier(
                self._config.python_executable
            )
            _require_private_state_ownership(
                self._config.private_state_root,
                expected_run=None,
            )
            # ComfyUI automatically expands this file into additional model
            # roots, which would defeat the single reviewed models directory.
            _require_missing_path(
                self._config.runtime_root / "extra_model_paths.yaml"
            )
            checkpoint = _checkpoint_path(
                self._config.models_root,
                profile.checkpoint_name,
            )
            python_size, python_digest = _hash_stable_file(
                self._config.python_executable
            )
            main_size, main_digest = _hash_stable_file(self._config.main_script)
            checkpoint_size, checkpoint_digest = _hash_stable_file(checkpoint)
            if (
                python_digest != self._config.python_sha256
                or main_digest != self._config.comfyui_main_sha256
                or checkpoint_digest != profile.checkpoint_sha256
            ):
                raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
            declarations = (
                GuardedFileDeclaration(
                    self._config.python_executable,
                    python_size,
                    python_digest,
                ),
                GuardedFileDeclaration(
                    self._config.main_script,
                    main_size,
                    main_digest,
                ),
                GuardedFileDeclaration(
                    checkpoint,
                    checkpoint_size,
                    checkpoint_digest,
                ),
            )
        except ImageGenerationUnavailableError:
            with self._lock:
                if self._state == "checking":
                    self._state = "idle"
            raise
        except BaseException as error:
            with self._lock:
                if self._state == "checking":
                    self._state = "idle"
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
        with self._lock:
            if self._state != "checking":
                raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
            self._preflight = _Preflight(profile, declarations)
            self._state = "preflighted"

    def _stable_launch_paths(
        self,
        guard: _FileGuard,
        profile: ModelProfile,
    ) -> tuple[Path, Path, Path]:
        """Derive interpreter, entry-point, and model roots from guarded handles.

        File guards deny replacement and expose volume-GUID paths so a drive
        letter cannot be remapped between verification and child startup.  The
        checkpoint's reviewed relative key lets this method recover the stable
        models root without trusting the original drive-letter spelling.
        """

        try:
            values = self._dependencies.guarded_paths_getter(guard)
        except BaseException as error:
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
        if (
            type(values) is not tuple
            or len(values) != 3
            or any(type(value) is not str or not value for value in values)
        ):
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
        python_path, main_path, checkpoint_path = (
            Path(value) for value in values
        )
        checkpoint_suffix = (
            "checkpoints",
            *profile.checkpoint_name.replace("\\", "/").split("/"),
        )
        actual_suffix = checkpoint_path.parts[-len(checkpoint_suffix):]
        if (
            python_path.name.casefold()
            != self._config.python_executable.name.casefold()
            or main_path.name.casefold() != self._config.main_script.name.casefold()
            or tuple(part.casefold() for part in actual_suffix)
            != tuple(part.casefold() for part in checkpoint_suffix)
        ):
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
        models_root = checkpoint_path
        for _part in checkpoint_suffix:
            models_root = models_root.parent
        return python_path, main_path, models_root

    def _command(
        self,
        run: PrivateRunDirectory,
        port: int,
        *,
        python_executable: Path,
        main_script: Path,
        models_root: Path,
    ) -> list[str]:
        """Build the fixed private-runtime command from guarded stable paths."""

        return [
            str(python_executable),
            # Isolated mode also removes the script directory from sys.path,
            # which prevents ComfyUI from importing its sibling packages.
            # A closed child environment plus -E/-s blocks inherited Python
            # configuration while preserving the application directory.
            "-E",
            "-s",
            "-B",
            "-u",
            "-X",
            "utf8",
            str(main_script),
            "--listen",
            "127.0.0.1",
            "--port",
            str(port),
            "--base-directory",
            str(run.root),
            "--models-directory",
            str(models_root),
            "--output-directory",
            str(run.output),
            "--input-directory",
            str(run.input),
            "--temp-directory",
            str(run.temp),
            "--user-directory",
            str(run.user),
            "--front-end-root",
            str(run.frontend),
            "--disable-auto-launch",
            "--disable-metadata",
            "--disable-all-custom-nodes",
            "--offline",
            "--disable-assets",
            "--disable-partner-nodes",
            "--preview-method",
            "none",
        ]

    def _raw_config(self, port: int) -> ComfyUiConfig:
        """Bind the raw adapter to one exact private loopback listener."""

        return ComfyUiConfig(
            base_url=f"http://127.0.0.1:{port}",
            probe_timeout_seconds=min(
                2.0,
                self._config.request_timeout_seconds,
            ),
            request_timeout_seconds=self._config.request_timeout_seconds,
            job_timeout_seconds=self._config.job_timeout_seconds,
            poll_interval_seconds=self._config.poll_interval_seconds,
            exclusive_instance=True,
        )

    def _rollback_startup(
        self,
        process: _ManagedProcess | None,
        guard: _FileGuard | None,
        run: PrivateRunDirectory | None,
    ) -> bool:
        """Release a failed pre-return allocation in process-first order."""

        clean = True
        process_closed = process is None
        if process is not None:
            try:
                process_closed = bool(
                    process.terminate(self._config.stop_timeout_seconds)
                ) and process.closed
            except BaseException:
                process_closed = False
            clean = clean and process_closed
        if not process_closed:
            return False
        if guard is not None:
            try:
                guard.close()
                clean = clean and guard.closed
            except BaseException:
                clean = False
        if run is not None:
            try:
                self._dependencies.run_remover(
                    run,
                )
                _require_missing_path(run.root)
            except BaseException:
                clean = False
        return clean

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Start, authenticate by PID ownership, and prepare one private job."""

        with self._terminal_lock:
            return self._prepare_exclusive(workflow)

    def _prepare_exclusive(
        self,
        workflow: CompiledWorkflow,
    ) -> ComfyPromptHandle:
        """Allocate one job while close/finalize are excluded from startup."""

        if type(workflow) is not CompiledWorkflow:
            raise TypeError("workflow must be CompiledWorkflow.")
        workflow.validate_invariants()
        with self._lock:
            preflight = self._preflight
            if self._state != "preflighted" or preflight is None:
                raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
            if (
                workflow.profile_id != preflight.profile.profile_id
                or workflow.profile_digest != preflight.profile.profile_digest
            ):
                raise ImageGenerationProtocolError(
                    "Compiled workflow does not match the verified model profile."
                )
            self._state = "starting"

        run: PrivateRunDirectory | None = None
        guard: _FileGuard | None = None
        process: _ManagedProcess | None = None
        transport: _StandardHandles | None = None
        try:
            run = self._dependencies.run_factory(self._config.private_state_root)
            _require_private_state_ownership(
                self._config.private_state_root,
                expected_run=run.root,
            )
            guard = self._dependencies.guard_acquirer(preflight.declarations)
            python_path, main_path, models_root = self._stable_launch_paths(
                guard,
                preflight.profile,
            )
            # Repeat the default-config absence check after directory/file
            # guards are active and immediately before starting the child.
            _require_missing_path(main_path.parent / "extra_model_paths.yaml")
            port = self._dependencies.port_allocator()
            if type(port) is not int or not 1 <= port <= 65_535:
                raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
            transport = self._dependencies.transport_factory()
            process = self._dependencies.launcher(
                self._command(
                    run,
                    port,
                    python_executable=python_path,
                    main_script=main_path,
                    models_root=models_root,
                ),
                stdin_handle=transport.stdin_handle,
                stdout_handle=transport.stdout_handle,
                stderr_handle=transport.stderr_handle,
                cwd=str(main_path.parent),
                environment=self._dependencies.environment_factory(run),
            )
            transport.close()
            transport = None
            deadline = (
                self._dependencies.monotonic_clock()
                + self._config.startup_timeout_seconds
            )
            raw: _RawClient | None = None
            while True:
                if not process.is_alive():
                    raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
                if self._dependencies.owner_verifier(port, process.pid):
                    candidate = self._dependencies.raw_factory(
                        self._raw_config(port)
                    )
                    try:
                        candidate.probe_ready()
                    except ImageGenerationUnavailableError:
                        pass
                    else:
                        # `/system_stats` is registered only after ComfyUI has
                        # initialized routes and nodes.  A missing built-in node
                        # or checkpoint after that point is a stable install
                        # error, not a startup condition worth retrying for 90s.
                        candidate.ensure_ready(preflight.profile)
                        raw = candidate
                        break
                remaining = deadline - self._dependencies.monotonic_clock()
                if remaining <= 0.0:
                    raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
                self._dependencies.sleeper(min(0.05, remaining))
            handle = raw.prepare(workflow)
            if type(handle) is not ComfyPromptHandle:
                raise ImageGenerationProtocolError(
                    "Managed image runtime returned an invalid prompt handle."
                )
            active = _ActiveSession(
                run=run,
                guard=guard,
                process=process,
                raw=raw,
                port=port,
                handle=handle,
                workflow_digest=workflow.workflow_digest,
            )
            with self._lock:
                if self._state != "starting":
                    raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
                self._active = active
                self._preflight = None
                self._state = "prepared"
            run = None
            guard = None
            process = None
            return handle
        except (ImageGenerationProtocolError, ImageGenerationUnavailableError) as error:
            clean = self._rollback_startup(process, guard, run)
            with self._lock:
                self._preflight = None
                if not clean:
                    self._state = "poisoned"
                elif self._state == "starting":
                    self._state = "idle"
            if not clean:
                raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE) from None
            if isinstance(error, ImageGenerationProtocolError):
                raise
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from None
        except BaseException:
            clean = self._rollback_startup(process, guard, run)
            with self._lock:
                self._preflight = None
                if not clean:
                    self._state = "poisoned"
                elif self._state == "starting":
                    self._state = "idle"
            if not clean:
                raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE) from None
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from None
        finally:
            if transport is not None:
                try:
                    transport.close()
                except BaseException:
                    with self._lock:
                        self._state = "poisoned"

    def _owned_active(self, handle: ComfyPromptHandle) -> _ActiveSession:
        """Return the exact active session after correlation and PID checks."""

        if type(handle) is not ComfyPromptHandle:
            raise TypeError("handle must be ComfyPromptHandle.")
        with self._lock:
            active = self._active
            state = self._state
        if active is None or handle != active.handle or state not in {
            "prepared",
            "submitted",
            "output-observed",
        }:
            raise ImageGenerationProtocolError(
                "Prompt handle does not belong to the active private runtime."
            )
        try:
            owned = active.process.is_alive() and self._dependencies.owner_verifier(
                active.port,
                active.process.pid,
            )
        except BaseException as error:
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE) from error
        if not owned:
            raise ImageGenerationUnavailableError(_UNAVAILABLE_MESSAGE)
        return active

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> None:
        """Submit the exact compiled graph once to the owned private listener."""

        if type(workflow) is not CompiledWorkflow:
            raise TypeError("workflow must be CompiledWorkflow.")
        workflow.validate_invariants()
        active = self._owned_active(handle)
        if active.submitted or workflow.workflow_digest != active.workflow_digest:
            raise ImageGenerationProtocolError(
                "Prepared prompt does not match the active private workflow."
            )
        active.raw.submit_prepared(handle, workflow)
        active.submitted = True
        with self._lock:
            if self._active is active:
                self._state = "submitted"

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Wait for one output while retaining exact process ownership."""

        active = self._owned_active(handle)
        if not active.submitted:
            raise ImageGenerationProtocolError(
                "The active private workflow has not been submitted."
            )
        descriptor = active.raw.wait_for_output(
            handle,
            expected_output_node_id,
            cancel_requested=cancel_requested,
        )
        if type(descriptor) is not ComfyOutputDescriptor:
            raise ImageGenerationProtocolError(
                "Managed image runtime returned an invalid output descriptor."
            )
        active.output = descriptor
        with self._lock:
            if self._active is active:
                self._state = "output-observed"
        return descriptor

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Fetch only the descriptor observed for the current private job."""

        if type(descriptor) is not ComfyOutputDescriptor:
            raise TypeError("descriptor must be ComfyOutputDescriptor.")
        with self._lock:
            active = self._active
        if active is None or active.output != descriptor:
            raise ImageGenerationProtocolError(
                "Output descriptor does not belong to the active private runtime."
            )
        self._owned_active(active.handle)
        return active.raw.fetch_png(
            descriptor,
            expected_width=expected_width,
            expected_height=expected_height,
        )

    def _cleanup_active(self, active: _ActiveSession) -> bool:
        """Converge all terminal paths on process-first, exact-root cleanup."""

        clean = True
        if not active.cancel_attempted:
            active.cancel_attempted = True
            try:
                active.raw.cancel(active.handle)
            except BaseException:
                # HTTP cancellation is advisory for this exclusive child.  A
                # buggy adapter must never prevent the authoritative process
                # tree termination and exact-directory cleanup below.
                pass
        if not active.process_closed:
            try:
                active.process_closed = bool(
                    active.process.terminate(self._config.stop_timeout_seconds)
                ) and active.process.closed
            except BaseException:
                active.process_closed = False
            clean = clean and active.process_closed
        if not active.process_closed:
            return False
        if not active.guard_closed:
            try:
                active.guard.close()
                active.guard_closed = active.guard.closed
            except BaseException:
                active.guard_closed = False
            clean = clean and active.guard_closed
        if not active.run_removed:
            try:
                if active.run.root.parent != self._config.private_state_root:
                    raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE)
                self._dependencies.run_remover(
                    active.run,
                )
                _require_missing_path(active.run.root)
                active.run_removed = True
            except BaseException:
                active.run_removed = False
            clean = clean and active.run_removed
        return clean and all((
            active.process_closed,
            active.guard_closed,
            active.run_removed,
        ))

    def finalize(self, handle: ComfyPromptHandle) -> None:
        """Stop the child and erase all private state before result publication."""

        if type(handle) is not ComfyPromptHandle:
            raise TypeError("handle must be ComfyPromptHandle.")
        with self._terminal_lock:
            with self._lock:
                active = self._active
                if active is None:
                    if handle == self._last_finalized_handle:
                        return
                    raise ImageGenerationProtocolError(
                        "Prompt handle does not belong to the private runtime."
                    )
                if handle != active.handle:
                    raise ImageGenerationProtocolError(
                        "Prompt handle does not belong to the private runtime."
                    )
                self._state = "finalizing"
            clean = self._cleanup_active(active)
            with self._lock:
                if clean:
                    self._active = None
                    self._last_finalized_handle = handle
                    self._state = "idle"
                else:
                    self._state = "poisoned"
            if not clean:
                raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE)

    def close(self) -> None:
        """Permanently close this owner and terminate any active private job."""

        with self._terminal_lock:
            with self._lock:
                if self._state == "closed":
                    return
                active = self._active
                if active is None:
                    if self._state == "poisoned":
                        # A failed startup rollback has no trustworthy prompt
                        # handle to adopt.  Do not relabel unknown native/file
                        # ownership as safely closed.
                        raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE)
                    self._preflight = None
                    self._state = "closed"
                    return
                self._state = "finalizing"
            clean = self._cleanup_active(active)
            with self._lock:
                if clean:
                    self._active = None
                    self._preflight = None
                    self._last_finalized_handle = active.handle
                    self._state = "closed"
                else:
                    self._state = "poisoned"
            if not clean:
                raise ImageGenerationUnavailableError(_CLEANUP_MESSAGE)

    def __enter__(self) -> "ManagedComfyUiRuntime":
        """Return this lazily started private runtime owner."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Close the private runtime when leaving a context."""

        self.close()


__all__ = ["ManagedComfyUiConfig", "ManagedComfyUiRuntime"]
