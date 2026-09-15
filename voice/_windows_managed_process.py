"""Launch one Windows child inside a private kill-on-close Job Object.

The managed local-speech runtime needs a stronger process boundary than the
ordinary :mod:`subprocess` convenience API exposes.  This module creates the
child suspended, restricts inheritance to private duplicates of its three
standard handles, assigns it to a kill-on-close Job Object, and only then lets
its primary thread run.  Closing the container therefore terminates the whole
descendant tree, including helpers such as FFmpeg.

Command arguments, environment values, paths, and native handle values may
contain deployment secrets.  Public failures and representations intentionally
use a small stable vocabulary and never retain or render those launch inputs.
The module imports safely on non-Windows hosts so cross-platform tooling can
inspect it; attempting to launch there fails with a stable unavailable error.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import ntpath
import os
import subprocess
from threading import Condition, Lock, RLock
from time import monotonic
from typing import Any, Dict, Sequence, Tuple


WINDOWS_MANAGED_PROCESS_MAX_WAIT_SECONDS = 600.0
"""Largest blocking interval accepted by lifecycle operations."""

_IS_WINDOWS = os.name == "nt"
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_DUPLICATE_SAME_ACCESS = 0x00000002
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_MAX_WINDOWS_COMMAND_LINE_CODE_UNITS = 32_767
_MAX_WINDOWS_ENVIRONMENT_CODE_UNITS = 32_767
_TERMINATE_EXIT_CODE = 1

_HANDLE = wintypes.HANDLE
_SIZE_T = ctypes.c_size_t
_ULONG_PTR = ctypes.c_size_t
_LPBYTE = ctypes.POINTER(wintypes.BYTE)

# PROC_THREAD_ATTRIBUTE_HANDLE_LIST still requires every selected handle to be
# marked inheritable.  Serialize that short window so another launcher using
# this same boundary cannot run legacy all-handle inheritance before the
# temporary duplicates are closed.  Any future same-process launcher that uses
# broad inheritance must coordinate on this lock for the guarantee to hold.
_PROCESS_CREATION_LOCK = Lock()


class WindowsManagedProcessError(Exception):
    """Base class for stable managed-process boundary failures."""


class WindowsManagedProcessUnavailableError(WindowsManagedProcessError):
    """Report that the required Windows process primitives are unavailable."""


class WindowsManagedProcessValidationError(WindowsManagedProcessError):
    """Report malformed launch or lifecycle inputs without echoing them."""


class WindowsManagedProcessLaunchError(WindowsManagedProcessError):
    """Report a sanitized failure before a managed child becomes runnable."""


class WindowsManagedProcessControlError(WindowsManagedProcessError):
    """Report a sanitized native wait or teardown failure."""


class _STARTUPINFOW(ctypes.Structure):
    """Mirror the fixed prefix of the Windows STARTUPINFOEXW structure."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", _LPBYTE),
        ("hStdInput", _HANDLE),
        ("hStdOutput", _HANDLE),
        ("hStdError", _HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    """Mirror STARTUPINFOEXW while keeping its attribute storage external."""

    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    """Receive the two owned handles returned by CreateProcessW."""

    _fields_ = [
        ("hProcess", _HANDLE),
        ("hThread", _HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """Mirror the basic limits embedded in extended Job Object settings."""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", _SIZE_T),
        ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", _ULONG_PTR),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    """Mirror the accounting fields required by extended Job Object limits."""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """Mirror the Job Object payload used to enable kill-on-close."""

    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", _SIZE_T),
        ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T),
        ("PeakJobMemoryUsed", _SIZE_T),
    ]


_kernel32: Any
if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = _HANDLE
    _kernel32.DuplicateHandle.argtypes = [
        _HANDLE,
        _HANDLE,
        _HANDLE,
        ctypes.POINTER(_HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _kernel32.DuplicateHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [_HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SIZE_T),
    ]
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        _SIZE_T,
        ctypes.c_void_p,
        _SIZE_T,
        ctypes.c_void_p,
        ctypes.POINTER(_SIZE_T),
    ]
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _kernel32.DeleteProcThreadAttributeList.restype = None
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = _HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        _HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.ResumeThread.argtypes = [_HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.TerminateProcess.argtypes = [_HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [_HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [_HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [
        _HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
else:
    _kernel32 = None


def _require_windows() -> None:
    """Fail before validating sensitive caller input on unsupported hosts."""

    if not _IS_WINDOWS or _kernel32 is None:
        raise WindowsManagedProcessUnavailableError(
            "Managed Windows processes are unavailable on this platform."
        )


def _validate_timeout(timeout_seconds: object) -> float:
    """Return a finite bounded wait accepted by Win32 millisecond timers."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or (isinstance(timeout_seconds, float) and not math.isfinite(timeout_seconds))
        or timeout_seconds < 0
        or timeout_seconds > WINDOWS_MANAGED_PROCESS_MAX_WAIT_SECONDS
    ):
        raise WindowsManagedProcessValidationError(
            "timeout_seconds must be finite and between zero and 600."
        )
    return float(timeout_seconds)


def _utf16_code_units(value: str) -> int:
    """Count Win32 UTF-16 code units while rejecting unpaired surrogates."""

    try:
        encoded = value.encode("utf-16-le")
    except UnicodeEncodeError:
        raise WindowsManagedProcessValidationError(
            "A managed-process string is not valid UTF-16."
        ) from None
    return len(encoded) // 2


def _wait_milliseconds(timeout_seconds: float) -> int:
    """Round positive fractional milliseconds up so waits never end early."""

    if timeout_seconds == 0.0:
        return 0
    return max(1, math.ceil(timeout_seconds * 1_000.0))


def _validate_native_handle(value: object) -> int:
    """Accept one non-null pointer-sized handle without rendering its value."""

    pointer_max = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
    invalid_handle = ctypes.c_void_p(-1).value
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > pointer_max
        or value == invalid_handle
    ):
        raise WindowsManagedProcessValidationError(
            "A standard-stream handle is invalid."
        )
    return value


def _validate_launch_inputs(
    argv: object,
    cwd: object,
    environment: object,
) -> Tuple[Tuple[str, ...], str, str, str]:
    """Freeze and encode the closed command, directory, and environment.

    CreateProcessW consumes mutable buffers.  Freezing the caller's collections
    before any native work prevents another thread from changing the security
    decision between validation and process creation.
    """

    if not isinstance(argv, (list, tuple)) or not argv:
        raise WindowsManagedProcessValidationError(
            "argv must be a non-empty closed list of strings."
        )
    frozen_argv = tuple(argv)
    if any(not isinstance(value, str) or "\x00" in value for value in frozen_argv):
        raise WindowsManagedProcessValidationError(
            "argv must contain only NUL-free strings."
        )
    if not frozen_argv[0] or not ntpath.isabs(frozen_argv[0]):
        raise WindowsManagedProcessValidationError(
            "The executable path must be absolute."
        )
    if not isinstance(cwd, str) or "\x00" in cwd or not ntpath.isabs(cwd):
        raise WindowsManagedProcessValidationError(
            "cwd must be an absolute NUL-free path."
        )
    if not isinstance(environment, dict):
        raise WindowsManagedProcessValidationError(
            "environment must be a closed dictionary of strings."
        )

    frozen_environment: Dict[str, str] = {}
    folded_keys = set()
    for key, value in environment.items():
        if (
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\x00" in key
            or not isinstance(value, str)
            or "\x00" in value
        ):
            raise WindowsManagedProcessValidationError(
                "environment contains an invalid key or value."
            )
        folded = key.casefold()
        if folded in folded_keys:
            # Windows variable lookup is case-insensitive.  Reject aliases so
            # sorting cannot silently choose a different value than the caller.
            raise WindowsManagedProcessValidationError(
                "environment contains duplicate case-insensitive keys."
            )
        folded_keys.add(folded)
        frozen_environment[key] = value

    command_line = subprocess.list2cmdline(frozen_argv)
    if (
        _utf16_code_units(command_line) + 1
        > _MAX_WINDOWS_COMMAND_LINE_CODE_UNITS
    ):
        raise WindowsManagedProcessValidationError(
            "The managed-process command line is too long."
        )
    entries = [
        f"{key}={frozen_environment[key]}"
        for key in sorted(frozen_environment, key=lambda item: (item.casefold(), item))
    ]
    # A Unicode environment block ends with an empty entry.  Supplying an
    # explicit block even when it is empty prevents CreateProcessW from
    # inheriting the parent's environment by default.
    environment_block = "\x00".join(entries) + "\x00"
    if not entries:
        environment_block = "\x00"
    if (
        _utf16_code_units(environment_block) + 1
        > _MAX_WINDOWS_ENVIRONMENT_CODE_UNITS
    ):
        raise WindowsManagedProcessValidationError(
            "The managed-process environment is too large."
        )
    return frozen_argv, cwd, command_line, environment_block


def _handle_value(handle: object) -> int:
    """Convert a non-null ctypes HANDLE to its integer storage value."""

    if isinstance(handle, int):
        return handle
    value = getattr(handle, "value", None)
    if value is None:
        return 0
    return int(value)


def _close_handle(handle: int) -> bool:
    """Close one owned handle and report success without raising native detail."""

    if handle == 0 or _kernel32 is None:
        return True
    return bool(_kernel32.CloseHandle(_HANDLE(handle)))


def _duplicate_handle(handle: int, *, inheritable: bool) -> int:
    """Create a private duplicate with an explicit inheritance bit."""

    _require_windows()
    assert _kernel32 is not None
    current_process = _kernel32.GetCurrentProcess()
    duplicate = _HANDLE()
    succeeded = _kernel32.DuplicateHandle(
        current_process,
        _HANDLE(handle),
        current_process,
        ctypes.byref(duplicate),
        0,
        wintypes.BOOL(inheritable),
        _DUPLICATE_SAME_ACCESS,
    )
    if not succeeded:
        raise WindowsManagedProcessLaunchError(
            "The managed process could not duplicate a stream handle."
        ) from None
    return _handle_value(duplicate)


def _observe_process(handle: int, timeout_seconds: float) -> Tuple[bool, int | None]:
    """Wait on one owned or duplicated process handle for a bounded interval."""

    _require_windows()
    assert _kernel32 is not None
    result = int(
        _kernel32.WaitForSingleObject(
            _HANDLE(handle),
            wintypes.DWORD(_wait_milliseconds(timeout_seconds)),
        )
    )
    if result == _WAIT_TIMEOUT:
        return False, None
    if result != _WAIT_OBJECT_0:
        raise WindowsManagedProcessControlError(
            "The managed process could not be observed."
        ) from None
    exit_code = wintypes.DWORD()
    if not _kernel32.GetExitCodeProcess(_HANDLE(handle), ctypes.byref(exit_code)):
        raise WindowsManagedProcessControlError(
            "The managed process exit status is unavailable."
        ) from None
    return True, int(exit_code.value)


class _TeardownAttempt:
    """Hold one immutable eventual result shared by concurrent teardown callers."""

    __slots__ = ("completed", "failed", "result")

    def __init__(self) -> None:
        """Create one pending attempt whose outcome is published under a lock."""

        self.completed = False
        self.failed = False
        self.result = False


class _TeardownResult:
    """Return cleanup outcome plus any native handles that remain retryable."""

    __slots__ = (
        "exit_code",
        "exited",
        "failed",
        "job_handle",
        "process_handle",
        "thread_handle",
    )

    def __init__(
        self,
        *,
        exited: bool,
        exit_code: int | None,
        failed: bool,
        process_handle: int,
        thread_handle: int,
        job_handle: int,
    ) -> None:
        """Capture an attempt without retaining any sensitive launch input."""

        self.exited = exited
        self.exit_code = exit_code
        self.failed = failed
        self.process_handle = process_handle
        self.thread_handle = thread_handle
        self.job_handle = job_handle


class WindowsManagedProcess:
    """Own a child process, primary thread, and kill-on-close Job Object.

    Instances are created only by :func:`launch_windows_managed_process`.
    ``close`` and ``terminate`` are the same terminal transaction: one caller
    becomes its owner while concurrent callers wait on a shared attempt.  The
    public ``closed`` state is published only after Job termination and native
    handle cleanup finish, so no caller can report success while another still
    owns a live descendant tree.
    """

    __slots__ = (
        "_condition",
        "_exit_code",
        "_final_failed",
        "_final_result",
        "_job_handle",
        "_pid",
        "_process_handle",
        "_state",
        "_teardown_attempt",
        "_thread_handle",
    )

    def __init__(
        self,
        *,
        process_handle: int,
        thread_handle: int,
        job_handle: int,
        process_id: int,
    ) -> None:
        """Adopt native handles returned by the private launch transaction."""

        self._condition = Condition(RLock())
        self._process_handle = process_handle
        self._thread_handle = thread_handle
        self._job_handle = job_handle
        self._pid = process_id
        self._exit_code: int | None = None
        self._state = "active"
        self._teardown_attempt: _TeardownAttempt | None = None
        self._final_result = False
        self._final_failed = False

    @property
    def pid(self) -> int:
        """Return the operating-system identifier of the root child process."""

        return self._pid

    @property
    def closed(self) -> bool:
        """Return whether Job termination and all handle cleanup have finished."""

        with self._condition:
            return self._state == "closed"

    def __repr__(self) -> str:
        """Render lifecycle state without command, path, environment, or handles."""

        with self._condition:
            state = self._state
            if state == "active" and self._exit_code is not None:
                state = "exited"
        return f"{type(self).__name__}(state={state!r})"

    def __enter__(self) -> "WindowsManagedProcess":
        """Return this owned container for deterministic context management."""

        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Kill descendants and release handles when leaving a context."""

        self.close()

    def __del__(self) -> None:
        """Best-effort kill the Job if ownership was abandoned unexpectedly."""

        try:
            self.close(timeout_seconds=0.0)
        except BaseException:
            # Destructors must not turn interpreter shutdown into an unraisable
            # exception.  A concurrent teardown owner still retains the object
            # and its Job until that transaction publishes its outcome.
            pass

    def _record_exit(self, exit_code: int | None) -> None:
        """Cache an observed status without overwriting a prior observation."""

        if exit_code is None:
            return
        with self._condition:
            if self._exit_code is None:
                self._exit_code = exit_code

    @staticmethod
    def _raise_control_failure() -> None:
        """Raise a fresh sanitized error for a shared failed teardown attempt."""

        raise WindowsManagedProcessControlError(
            "The managed process could not be closed safely."
        ) from None

    def _wait_for_attempt_locked(
        self,
        attempt: _TeardownAttempt,
        timeout_seconds: float,
    ) -> bool:
        """Wait boundedly for exactly the attempt observed by this caller.

        The attempt object remains immutable after publication.  A later retry
        therefore cannot replace the result underneath a waiter from a failed
        earlier attempt.
        """

        deadline = monotonic() + timeout_seconds
        while not attempt.completed:
            remaining = deadline - monotonic()
            if remaining <= 0.0:
                return False
            self._condition.wait(remaining)
        if attempt.failed:
            self._raise_control_failure()
        return attempt.result

    def _perform_teardown(
        self,
        timeout_seconds: float,
        *,
        process_handle: int,
        thread_handle: int,
        job_handle: int,
    ) -> _TeardownResult:
        """Terminate and close one owned handle set without propagating errors.

        Each successfully closed handle is removed from the returned ownership
        set immediately.  A rare native failure therefore leaves the exact
        remaining handle available for a later explicit retry instead of either
        double-closing it or falsely publishing a completed teardown.
        """

        failed = False
        remaining_job = job_handle
        remaining_thread = thread_handle
        remaining_process = process_handle
        if job_handle:
            try:
                assert _kernel32 is not None
                # Closing the configured Job is the tree-kill guarantee;
                # TerminateJobObject merely asks Windows to begin it promptly.
                _kernel32.TerminateJobObject(
                    _HANDLE(job_handle), wintypes.UINT(_TERMINATE_EXIT_CODE)
                )
                if _close_handle(job_handle):
                    remaining_job = 0
                else:
                    failed = True
            except BaseException:
                failed = True
        if thread_handle:
            try:
                if _close_handle(thread_handle):
                    remaining_thread = 0
                else:
                    failed = True
            except BaseException:
                failed = True

        exited = process_handle == 0
        exit_code: int | None = None
        if process_handle:
            try:
                exited, exit_code = _observe_process(
                    process_handle, timeout_seconds
                )
            except BaseException:
                failed = True
            try:
                if _close_handle(process_handle):
                    remaining_process = 0
                else:
                    failed = True
            except BaseException:
                failed = True
        return _TeardownResult(
            exited=exited,
            exit_code=exit_code,
            failed=failed,
            process_handle=remaining_process,
            thread_handle=remaining_thread,
            job_handle=remaining_job,
        )

    def _teardown(self, timeout_seconds: object) -> bool:
        """Own or join the single synchronized terminal lifecycle transaction."""

        timeout = _validate_timeout(timeout_seconds)
        with self._condition:
            if self._state == "closed":
                if self._final_failed:
                    self._raise_control_failure()
                return self._final_result
            if self._state == "closing":
                attempt = self._teardown_attempt
                assert attempt is not None
                return self._wait_for_attempt_locked(attempt, timeout)

            attempt = _TeardownAttempt()
            self._teardown_attempt = attempt
            self._state = "closing"
            process_handle = self._process_handle
            thread_handle = self._thread_handle
            job_handle = self._job_handle

        try:
            outcome = self._perform_teardown(
                timeout,
                process_handle=process_handle,
                thread_handle=thread_handle,
                job_handle=job_handle,
            )
        except BaseException:
            # The concrete implementation contains its own per-handle guards.
            # This final boundary also keeps an injected or future helper defect
            # from publishing success or silently transferring ownership.
            outcome = _TeardownResult(
                exited=False,
                exit_code=None,
                failed=True,
                process_handle=process_handle,
                thread_handle=thread_handle,
                job_handle=job_handle,
            )

        with self._condition:
            self._process_handle = outcome.process_handle
            self._thread_handle = outcome.thread_handle
            self._job_handle = outcome.job_handle
            if outcome.exit_code is not None and self._exit_code is None:
                self._exit_code = outcome.exit_code
            all_handles_released = not (
                self._process_handle
                or self._thread_handle
                or self._job_handle
            )
            attempt.failed = outcome.failed
            attempt.result = outcome.exited and not outcome.failed
            attempt.completed = True
            if all_handles_released:
                self._state = "closed"
                self._final_result = attempt.result
                self._final_failed = attempt.failed
            else:
                # A close failure keeps ownership retryable.  Waiters retain a
                # reference to this completed attempt even if another caller
                # begins the next generation immediately after notification.
                self._state = "active"
            self._teardown_attempt = None
            self._condition.notify_all()
            if attempt.failed:
                self._raise_control_failure()
            return attempt.result

    def wait(self, timeout_seconds: float = 5.0) -> int | None:
        """Return the root exit code, or ``None`` when the bounded wait expires.

        Waiting for the root does not release the Job Object because a root can
        exit while one of its descendants remains alive.  A wait overlapping
        teardown joins its synchronization result before consulting exit state.
        """

        timeout = _validate_timeout(timeout_seconds)
        deadline = monotonic() + timeout
        with self._condition:
            if self._exit_code is not None:
                return self._exit_code
            if self._state == "closed":
                if self._final_failed:
                    self._raise_control_failure()
                return None
            if self._state == "closing":
                attempt = self._teardown_attempt
                assert attempt is not None
                self._wait_for_attempt_locked(attempt, timeout)
                if self._exit_code is not None:
                    return self._exit_code
                return None
            if self._process_handle == 0:
                return None
            try:
                wait_handle = _duplicate_handle(
                    self._process_handle, inheritable=False
                )
            except WindowsManagedProcessError:
                raise WindowsManagedProcessControlError(
                    "The managed process could not be observed."
                ) from None

        remaining = max(0.0, deadline - monotonic())
        try:
            exited, exit_code = _observe_process(wait_handle, remaining)
        finally:
            _close_handle(wait_handle)
        if not exited:
            return None
        self._record_exit(exit_code)
        return exit_code

    def is_alive(self) -> bool:
        """Return a conservative immediate root-process liveness snapshot."""

        with self._condition:
            if self._state == "closed" or self._exit_code is not None:
                return False
            if self._state == "closing":
                # Teardown may have signalled the process but has not yet
                # published cleanup.  Reporting alive avoids a premature
                # success claim while the Job remains owned by another thread.
                return True
            if self._process_handle == 0:
                return False
            try:
                wait_handle = _duplicate_handle(
                    self._process_handle, inheritable=False
                )
            except WindowsManagedProcessError:
                raise WindowsManagedProcessControlError(
                    "The managed process could not be observed."
                ) from None
        try:
            exited, exit_code = _observe_process(wait_handle, 0.0)
        finally:
            _close_handle(wait_handle)
        self._record_exit(exit_code)
        return not exited

    def terminate(self, timeout_seconds: float = 5.0) -> bool:
        """Kill the complete Job tree and release every handle within a bound.

        ``terminate`` is intentionally terminal and has the same ownership
        semantics as :meth:`close`.  Concurrent calls join one teardown attempt
        rather than detaching the Job twice.
        """

        return self._teardown(timeout_seconds)

    def close(self, timeout_seconds: float = 5.0) -> bool:
        """Kill the complete Job tree and release every handle within a bound.

        Exactly one concurrent caller performs native teardown.  Other callers
        wait up to their own bound for that same immutable result, returning
        ``False`` only when their wait expires and never publishing ``closed``
        ahead of the owner.
        """

        return self._teardown(timeout_seconds)


def launch_windows_managed_process(
    argv: Sequence[str],
    *,
    stdin_handle: int,
    stdout_handle: int,
    stderr_handle: int,
    cwd: str,
    environment: dict[str, str] | None = None,
) -> WindowsManagedProcess:
    """Launch one suspended child and bind it to a kill-on-close Job Object.

    ``argv[0]`` and ``cwd`` must be absolute.  ``environment`` is a complete
    closed set rather than additions to the parent environment; ``None`` means
    an empty environment.  The caller retains its stream handles.  Private
    inheritable duplicates are the only handles placed in the child attribute
    list and are closed in the parent before this function returns.

    Any failure before ``ResumeThread`` terminates the suspended process and
    releases every temporary handle, so no uncontained executable can run.
    """

    _require_windows()
    assert _kernel32 is not None
    frozen_argv, frozen_cwd, command_line, environment_block = (
        _validate_launch_inputs(
            argv,
            cwd,
            {} if environment is None else environment,
        )
    )
    source_handles = (
        _validate_native_handle(stdin_handle),
        _validate_native_handle(stdout_handle),
        _validate_native_handle(stderr_handle),
    )

    inherited_handles: list[int] = []
    attribute_list: ctypes.c_void_p | None = None
    attribute_initialized = False
    job_handle = 0
    process_information = _PROCESS_INFORMATION()
    child_created = False
    child_assigned = False
    _PROCESS_CREATION_LOCK.acquire()
    try:
        for source_handle in source_handles:
            inherited_handles.append(
                _duplicate_handle(source_handle, inheritable=True)
            )

        attribute_size = _SIZE_T()
        _kernel32.InitializeProcThreadAttributeList(
            None, 1, 0, ctypes.byref(attribute_size)
        )
        if attribute_size.value == 0:
            raise WindowsManagedProcessLaunchError(
                "The managed process handle boundary could not be prepared."
            )
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
        if not _kernel32.InitializeProcThreadAttributeList(
            attribute_list, 1, 0, ctypes.byref(attribute_size)
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process handle boundary could not be prepared."
            )
        attribute_initialized = True

        handle_array_type = _HANDLE * len(inherited_handles)
        handle_array = handle_array_type(
            *(_HANDLE(value) for value in inherited_handles)
        )
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_array, ctypes.c_void_p),
            ctypes.sizeof(handle_array),
            None,
            None,
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process handle boundary could not be prepared."
            )

        job = _kernel32.CreateJobObjectW(None, None)
        job_handle = _handle_value(job)
        if job_handle == 0:
            raise WindowsManagedProcessLaunchError(
                "The managed process Job could not be created."
            )
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not _kernel32.SetInformationJobObject(
            _HANDLE(job_handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process Job could not be configured."
            )

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = _HANDLE(inherited_handles[0])
        startup.StartupInfo.hStdOutput = _HANDLE(inherited_handles[1])
        startup.StartupInfo.hStdError = _HANDLE(inherited_handles[2])
        startup.lpAttributeList = attribute_list
        command_buffer = ctypes.create_unicode_buffer(command_line)
        environment_buffer = ctypes.create_unicode_buffer(environment_block)
        creation_flags = (
            _CREATE_SUSPENDED
            | _CREATE_UNICODE_ENVIRONMENT
            | _EXTENDED_STARTUPINFO_PRESENT
            | _CREATE_NO_WINDOW
        )
        executable = frozen_argv[0]
        if not _kernel32.CreateProcessW(
            executable,
            command_buffer,
            None,
            None,
            True,
            creation_flags,
            ctypes.cast(environment_buffer, ctypes.c_void_p),
            frozen_cwd,
            ctypes.cast(
                ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW)
            ),
            ctypes.byref(process_information),
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process could not be created."
            )
        child_created = True
        if not _kernel32.AssignProcessToJobObject(
            _HANDLE(job_handle), process_information.hProcess
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process could not be contained."
            )
        child_assigned = True
        if _kernel32.ResumeThread(process_information.hThread) == 0xFFFFFFFF:
            raise WindowsManagedProcessLaunchError(
                "The managed process could not be started."
            )

        managed = WindowsManagedProcess(
            process_handle=_handle_value(process_information.hProcess),
            thread_handle=_handle_value(process_information.hThread),
            job_handle=job_handle,
            process_id=int(process_information.dwProcessId),
        )
        process_information.hProcess = None
        process_information.hThread = None
        job_handle = 0
        return managed
    except WindowsManagedProcessError:
        raise
    except BaseException:
        raise WindowsManagedProcessLaunchError(
            "The managed process launch transaction failed."
        ) from None
    finally:
        try:
            if child_created:
                if child_assigned and job_handle:
                    _kernel32.TerminateJobObject(
                        _HANDLE(job_handle), wintypes.UINT(_TERMINATE_EXIT_CODE)
                    )
                elif process_information.hProcess:
                    _kernel32.TerminateProcess(
                        process_information.hProcess,
                        wintypes.UINT(_TERMINATE_EXIT_CODE),
                    )
                if process_information.hProcess:
                    _kernel32.WaitForSingleObject(
                        process_information.hProcess, wintypes.DWORD(5_000)
                    )
            if process_information.hThread:
                _close_handle(_handle_value(process_information.hThread))
            if process_information.hProcess:
                _close_handle(_handle_value(process_information.hProcess))
            if job_handle:
                _close_handle(job_handle)
            if attribute_initialized and attribute_list is not None:
                _kernel32.DeleteProcThreadAttributeList(attribute_list)
            for inherited_handle in inherited_handles:
                _close_handle(inherited_handle)
        finally:
            _PROCESS_CREATION_LOCK.release()
