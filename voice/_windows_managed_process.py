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
from time import monotonic, sleep
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
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_ACCOUNTING_POLL_INTERVAL_SECONDS = 0.01
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


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    """Describe cumulative and active process counts for one Job object."""

    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
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
    _kernel32.QueryInformationJobObject.argtypes = [
        _HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
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


def _query_active_job_processes(handle: int) -> int:
    """Return the Job's authoritative active-process count.

    Root-process signalling alone cannot prove that descendants created inside
    the Job have terminated.  Accounting information is therefore the release
    gate for the Job handle that enforces kill-on-close containment.
    """

    _require_windows()
    assert _kernel32 is not None
    accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned_length = wintypes.DWORD()
    expected_length = ctypes.sizeof(accounting)
    if not _kernel32.QueryInformationJobObject(
        _HANDLE(handle),
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(accounting),
        expected_length,
        ctypes.byref(returned_length),
    ):
        raise WindowsManagedProcessControlError(
            "The managed process Job could not be observed."
        ) from None
    active_processes = int(accounting.ActiveProcesses)
    total_processes = int(accounting.TotalProcesses)
    if (
        int(returned_length.value) != expected_length
        or active_processes > total_processes
    ):
        # An incomplete or internally inconsistent native snapshot cannot be
        # used to prove that closing the containment handle is safe.
        raise WindowsManagedProcessControlError(
            "The managed process Job returned invalid accounting information."
        ) from None
    return active_processes


def _wait_for_job_empty(handle: int, deadline: float) -> bool:
    """Poll Job accounting until no process remains or ``deadline`` expires."""

    while True:
        if _query_active_job_processes(handle) == 0:
            return True
        remaining = deadline - monotonic()
        if remaining <= 0.0:
            return False
        sleep(min(_JOB_ACCOUNTING_POLL_INTERVAL_SECONDS, remaining))
        if monotonic() >= deadline:
            # Return conservatively rather than issuing a native query after
            # the caller's teardown deadline has expired.
            return False


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
        double-closing it or falsely publishing a completed teardown.  The Job
        remains owned until accounting proves that the entire process tree has
        no active process; a signalled root alone is not a tree-termination
        guarantee. External process-object handles may receive their signalled
        state just after the Job's authoritative active count reaches zero.
        """

        deadline = monotonic() + timeout_seconds
        failed = False
        remaining_job = job_handle
        remaining_thread = thread_handle
        remaining_process = process_handle
        termination_requested = job_handle == 0
        job_empty = job_handle == 0
        root_exited = process_handle == 0
        exit_code: int | None = None

        if job_handle:
            try:
                assert _kernel32 is not None
                termination_requested = bool(
                    _kernel32.TerminateJobObject(
                        _HANDLE(job_handle),
                        wintypes.UINT(_TERMINATE_EXIT_CODE),
                    )
                )
                if not termination_requested:
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

        if job_handle and termination_requested:
            try:
                job_empty = _wait_for_job_empty(job_handle, deadline)
            except BaseException:
                failed = True

        if process_handle and job_empty:
            try:
                root_exited, exit_code = _observe_process(
                    process_handle,
                    max(0.0, deadline - monotonic()),
                )
            except BaseException:
                failed = True

        if process_handle and root_exited:
            try:
                if _close_handle(process_handle):
                    remaining_process = 0
                else:
                    failed = True
            except BaseException:
                failed = True

        if job_handle and job_empty and root_exited and not failed:
            try:
                # The Job is the last handle released: its kill-on-close policy
                # remains the containment backstop for every earlier ambiguity.
                if _close_handle(job_handle):
                    remaining_job = 0
                else:
                    failed = True
            except BaseException:
                failed = True

        return _TeardownResult(
            exited=job_empty and root_exited,
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
            observation_owner, cleanup_quarantine = (
                _duplicate_observation_handle(self._process_handle)
            )

        remaining = max(0.0, deadline - monotonic())
        try:
            exited, exit_code = _observe_process(
                observation_owner._handle, remaining
            )
        finally:
            _release_observation_handle(
                observation_owner, cleanup_quarantine
            )
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
            observation_owner, cleanup_quarantine = (
                _duplicate_observation_handle(self._process_handle)
            )
        try:
            exited, exit_code = _observe_process(
                observation_owner._handle, 0.0
            )
        finally:
            _release_observation_handle(
                observation_owner, cleanup_quarantine
            )
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


class _SingleHandleOwner:
    """Keep one temporary duplicate reachable until native close succeeds."""

    __slots__ = ("_handle", "_lock")

    def __init__(self) -> None:
        """Preallocate an empty slot before native duplication can succeed."""

        self._handle = 0
        self._lock = Lock()

    def _close(self) -> bool:
        """Release the handle once, retaining it after any ambiguous result."""

        with self._lock:
            if self._handle == 0:
                return True
            try:
                closed = _close_handle(self._handle)
            except BaseException:
                return False
            if closed:
                self._handle = 0
            return closed


class _LaunchCleanupOwner:
    """Own every native resource until a launch transaction commits.

    A child remains suspended while its parent-side inherited duplicates are
    closed.  On any earlier failure this owner terminates either the assigned
    Job tree or the still-uncontained root and only releases core handles after
    native observations prove that the corresponding processes have exited.
    """

    __slots__ = (
        "_assigned",
        "_inherited_handles",
        "_job_handle",
        "_lock",
        "_managed_process",
        "_process_handle",
        "_process_information",
        "_thread_handle",
    )

    def __init__(self) -> None:
        """Create an empty owner before the first fallible native operation."""

        self._assigned = False
        # Fixed slots avoid allocating list growth after DuplicateHandle has
        # returned a new native resource but before ownership is published.
        self._inherited_handles: list[int] = [0, 0, 0]
        self._job_handle = 0
        self._lock = Lock()
        self._managed_process: WindowsManagedProcess | None = None
        self._process_handle = 0
        self._process_information: _PROCESS_INFORMATION | None = None
        self._thread_handle = 0

    def _mark_assigned(self) -> None:
        """Record that the Job, rather than the root alone, owns termination."""

        self._assigned = True

    def _transfer_to_managed(self, process_id: int) -> WindowsManagedProcess:
        """Construct and adopt the managed owner before the child may run."""

        managed = WindowsManagedProcess(
            process_handle=self._process_handle,
            thread_handle=self._thread_handle,
            job_handle=self._job_handle,
            process_id=process_id,
        )
        self._managed_process = managed

        self._process_handle = 0
        self._thread_handle = 0
        self._job_handle = 0
        return managed

    def _close_inherited_locked(self) -> bool:
        """Close each inherited duplicate while preserving every failed slot."""

        all_closed = True
        for index, handle in enumerate(self._inherited_handles):
            if handle == 0:
                continue
            try:
                closed = _close_handle(handle)
            except BaseException:
                closed = False
            if closed:
                self._inherited_handles[index] = 0
            else:
                all_closed = False
        return all_closed

    def _close_inherited(self) -> bool:
        """Close parent duplicates before the suspended child may run."""

        with self._lock:
            return self._close_inherited_locked()

    @staticmethod
    def _close_one(handle: int) -> bool:
        """Close one handle while converting exceptions into retained ownership."""

        try:
            return _close_handle(handle)
        except BaseException:
            return False

    def _salvage_process_information_locked(self) -> None:
        """Adopt CreateProcess outputs retained across an interrupted transfer."""

        process_information = self._process_information
        if process_information is None:
            return
        if process_information.hProcess:
            process_handle = _handle_value(process_information.hProcess)
            if self._process_handle == 0:
                self._process_handle = process_handle
            elif self._process_handle != process_handle:
                raise WindowsManagedProcessControlError(
                    "The managed process returned ambiguous process ownership."
                ) from None
            process_information.hProcess = None
        if process_information.hThread:
            thread_handle = _handle_value(process_information.hThread)
            if self._thread_handle == 0:
                self._thread_handle = thread_handle
            elif self._thread_handle != thread_handle:
                raise WindowsManagedProcessControlError(
                    "The managed process returned ambiguous thread ownership."
                ) from None
            process_information.hThread = None
        if not process_information.hProcess and not process_information.hThread:
            self._process_information = None

    def _close(self) -> bool:
        """Terminate owned work and retain each resource lacking a safe proof."""

        with self._lock:
            deadline = monotonic() + 5.0
            try:
                self._salvage_process_information_locked()
            except BaseException:
                # Keeping the ctypes result structure is itself exact ownership;
                # a later retry may complete the ordinary integer conversion.
                pass
            if self._managed_process is not None:
                try:
                    managed_closed = self._managed_process.close(
                        max(0.0, deadline - monotonic())
                    )
                except BaseException:
                    managed_closed = False
                if managed_closed:
                    self._managed_process = None

            root_exited = self._process_handle == 0
            job_empty = not self._assigned

            if self._process_handle:
                if self._assigned and self._job_handle:
                    try:
                        assert _kernel32 is not None
                        _kernel32.TerminateJobObject(
                            _HANDLE(self._job_handle),
                            wintypes.UINT(_TERMINATE_EXIT_CODE),
                        )
                    except BaseException:
                        pass
                elif not self._assigned:
                    try:
                        assert _kernel32 is not None
                        _kernel32.TerminateProcess(
                            _HANDLE(self._process_handle),
                            wintypes.UINT(_TERMINATE_EXIT_CODE),
                        )
                    except BaseException:
                        pass

            if self._assigned and self._job_handle:
                try:
                    job_empty = _wait_for_job_empty(
                        self._job_handle, deadline
                    )
                except BaseException:
                    job_empty = False

            if self._process_handle and (
                not self._assigned or job_empty
            ):
                try:
                    root_exited, _exit_code = _observe_process(
                        self._process_handle,
                        max(0.0, deadline - monotonic()),
                    )
                except BaseException:
                    root_exited = False

            # An uncontained suspended root must keep both of its core handles
            # until death is proven; otherwise no exact owner could retry the
            # failed termination transaction.
            if root_exited and self._thread_handle:
                if self._close_one(self._thread_handle):
                    self._thread_handle = 0
            if root_exited and self._process_handle:
                if self._close_one(self._process_handle):
                    self._process_handle = 0

            if self._job_handle:
                may_release_job = not self._assigned or (
                    job_empty and root_exited
                )
                if may_release_job and self._close_one(self._job_handle):
                    self._job_handle = 0

            self._close_inherited_locked()
            return not (
                self._managed_process is not None
                or self._process_information is not None
                or self._process_handle
                or self._thread_handle
                or self._job_handle
                or any(self._inherited_handles)
            )


class _CleanupQuarantine:
    """Strongly retain ambiguous native owners and permanently stop new work."""

    __slots__ = ("_lock", "_owners", "_poisoned")

    def __init__(self) -> None:
        """Create one initially healthy process-wide cleanup boundary."""

        self._lock = RLock()
        self._owners: list[_SingleHandleOwner | _LaunchCleanupOwner] = []
        self._poisoned = False

    def _retain(
        self, owner: _SingleHandleOwner | _LaunchCleanupOwner
    ) -> None:
        """Latch failure and keep the exact owner alive for safe later retry."""

        with self._lock:
            self._owners.append(owner)
            self._poisoned = True

    def _retry(self) -> bool:
        """Retry retained cleanup without clearing the production safety latch."""

        with self._lock:
            remaining: list[_SingleHandleOwner | _LaunchCleanupOwner] = []
            for owner in self._owners:
                try:
                    if not owner._close():
                        remaining.append(owner)
                except BaseException:
                    remaining.append(owner)
            self._owners = remaining
            return not remaining


_CLEANUP_QUARANTINE = _CleanupQuarantine()


def _duplicate_observation_handle(
    source_handle: int,
) -> Tuple[_SingleHandleOwner, _CleanupQuarantine]:
    """Duplicate a process handle only while the cleanup boundary is healthy."""

    cleanup_quarantine = _CLEANUP_QUARANTINE
    owner = _SingleHandleOwner()
    with cleanup_quarantine._lock:
        if cleanup_quarantine._poisoned:
            raise WindowsManagedProcessControlError(
                "The managed process observation boundary is unavailable."
            ) from None
        try:
            duplicate = _duplicate_handle(source_handle, inheritable=False)
        except WindowsManagedProcessError:
            raise WindowsManagedProcessControlError(
                "The managed process could not be observed."
            ) from None
        owner._handle = duplicate
        return owner, cleanup_quarantine


def _release_observation_handle(
    owner: _SingleHandleOwner,
    cleanup_quarantine: _CleanupQuarantine,
) -> None:
    """Close an observation duplicate or quarantine its exact owner."""

    if owner._close():
        return
    cleanup_quarantine._retain(owner)
    raise WindowsManagedProcessControlError(
        "The managed process observation handle could not be closed safely."
    ) from None


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

    Every parent-side inheritable duplicate is confirmed closed before
    ``ResumeThread``.  A cleanup ambiguity retains its exact native owner and
    permanently fails this process boundary closed, so no uncontained child or
    unowned handle can be accumulated by later launch attempts.
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

    creation_owner = _LaunchCleanupOwner()
    attribute_list: ctypes.c_void_p | None = None
    attribute_initialized = False
    pending_inherited_handle = 0
    pending_job_handle = 0
    process_information = _PROCESS_INFORMATION()
    cleanup_quarantine = _CLEANUP_QUARANTINE
    managed_ready_for_return = False
    _PROCESS_CREATION_LOCK.acquire()
    try:
        cleanup_quarantine._lock.acquire()
    except BaseException:
        # Do not strand the process-wide creation gate if lock acquisition is
        # interrupted before the native launch transaction has begun.
        _PROCESS_CREATION_LOCK.release()
        raise
    try:
        if cleanup_quarantine._poisoned:
            raise WindowsManagedProcessLaunchError(
                "The managed process cleanup boundary is unavailable."
            ) from None

        for index, source_handle in enumerate(source_handles):
            pending_inherited_handle = _duplicate_handle(
                source_handle, inheritable=True
            )
            creation_owner._inherited_handles[index] = (
                pending_inherited_handle
            )
            pending_inherited_handle = 0

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

        handle_array_type = _HANDLE * len(
            creation_owner._inherited_handles
        )
        handle_array = handle_array_type(
            *(
                _HANDLE(value)
                for value in creation_owner._inherited_handles
            )
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
        pending_job_handle = _handle_value(job)
        if pending_job_handle == 0:
            raise WindowsManagedProcessLaunchError(
                "The managed process Job could not be created."
            )
        creation_owner._job_handle = pending_job_handle
        pending_job_handle = 0
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not _kernel32.SetInformationJobObject(
            _HANDLE(creation_owner._job_handle),
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
        startup.StartupInfo.hStdInput = _HANDLE(
            creation_owner._inherited_handles[0]
        )
        startup.StartupInfo.hStdOutput = _HANDLE(
            creation_owner._inherited_handles[1]
        )
        startup.StartupInfo.hStdError = _HANDLE(
            creation_owner._inherited_handles[2]
        )
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
        creation_owner._process_information = process_information
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
        creation_owner._process_handle = _handle_value(
            process_information.hProcess
        )
        creation_owner._thread_handle = _handle_value(
            process_information.hThread
        )
        process_information.hProcess = None
        process_information.hThread = None
        creation_owner._process_information = None
        if not _kernel32.AssignProcessToJobObject(
            _HANDLE(creation_owner._job_handle),
            _HANDLE(creation_owner._process_handle),
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process could not be contained."
            )
        creation_owner._mark_assigned()

        # The attribute list and its inheritable duplicates are parent-only
        # launch machinery.  Releasing them while the child is still suspended
        # prevents both an EOF-extending pipe leak and a later inheritance race.
        if attribute_initialized and attribute_list is not None:
            _kernel32.DeleteProcThreadAttributeList(attribute_list)
            attribute_initialized = False
            attribute_list = None
        if not creation_owner._close_inherited():
            raise WindowsManagedProcessLaunchError(
                "The managed process parent handles could not be closed safely."
            )

        thread_handle = creation_owner._thread_handle
        managed = creation_owner._transfer_to_managed(
            int(process_information.dwProcessId)
        )
        if (
            _kernel32.ResumeThread(_HANDLE(thread_handle))
            == 0xFFFFFFFF
        ):
            raise WindowsManagedProcessLaunchError(
                "The managed process could not be started."
            )
        # Keep the launch owner as an additional strong reference until the
        # return path has released both global gates in ``finally``.  No
        # fallible cleanup helper may run after the child becomes runnable.
        managed_ready_for_return = True
        return managed
    except WindowsManagedProcessError:
        raise
    except BaseException:
        raise WindowsManagedProcessLaunchError(
            "The managed process launch transaction failed."
        ) from None
    finally:
        cleanup_ambiguous = False
        try:
            if not managed_ready_for_return:
                # Preserve native results if an asynchronous exception lands
                # between acquisition and its ordinary Python assignment.
                if pending_inherited_handle:
                    if (
                        pending_inherited_handle
                        in creation_owner._inherited_handles
                    ):
                        pending_inherited_handle = 0
                    else:
                        for index, handle in enumerate(
                            creation_owner._inherited_handles
                        ):
                            if handle == 0:
                                creation_owner._inherited_handles[index] = (
                                    pending_inherited_handle
                                )
                                pending_inherited_handle = 0
                                break
                if pending_inherited_handle:
                    pending_owner = _SingleHandleOwner()
                    pending_owner._handle = pending_inherited_handle
                    cleanup_quarantine._retain(pending_owner)
                    cleanup_ambiguous = True
                    pending_inherited_handle = 0
                if pending_job_handle:
                    if creation_owner._job_handle == 0:
                        creation_owner._job_handle = pending_job_handle
                    elif creation_owner._job_handle != pending_job_handle:
                        pending_owner = _SingleHandleOwner()
                        pending_owner._handle = pending_job_handle
                        cleanup_quarantine._retain(pending_owner)
                        cleanup_ambiguous = True
                    pending_job_handle = 0
                if attribute_initialized and attribute_list is not None:
                    try:
                        _kernel32.DeleteProcThreadAttributeList(attribute_list)
                    except BaseException:
                        cleanup_ambiguous = True
                try:
                    owner_closed = creation_owner._close()
                except BaseException:
                    owner_closed = False
                if not owner_closed:
                    cleanup_quarantine._retain(creation_owner)
                    cleanup_ambiguous = True
        finally:
            cleanup_quarantine._lock.release()
            _PROCESS_CREATION_LOCK.release()
        if cleanup_ambiguous:
            raise WindowsManagedProcessLaunchError(
                "The managed process launch transaction could not be cleaned safely."
            ) from None
