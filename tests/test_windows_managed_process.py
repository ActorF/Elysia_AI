"""Test the isolated Windows child-process and Job Object security boundary."""

from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Dict, Tuple

import pytest

import voice._windows_managed_process as managed_process_module
from voice._windows_managed_process import (
    WindowsManagedProcess,
    WindowsManagedProcessControlError,
    WindowsManagedProcessError,
    WindowsManagedProcessValidationError,
    launch_windows_managed_process,
)


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows Job Objects are only available on Windows."
)

if os.name == "nt":
    import msvcrt


_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_SYNCHRONIZE = 0x00100000
_HANDLE_FLAG_INHERIT = 0x00000001
_PYTHON_EXECUTABLE = str(
    Path(getattr(sys, "_base_executable", sys.executable)).resolve()
)


def _native_handle(file_descriptor: int) -> int:
    """Return a Windows HANDLE for one test-owned file descriptor."""

    return int(msvcrt.get_osfhandle(file_descriptor))


def _open_standard_handles() -> Tuple[int, int, int, int]:
    """Create NUL input/error handles plus a readable stdout pipe."""

    stdin_fd = os.open(os.devnull, os.O_RDONLY | os.O_BINARY)
    stderr_fd = os.open(os.devnull, os.O_WRONLY | os.O_BINARY)
    stdout_read_fd, stdout_write_fd = os.pipe()
    return stdin_fd, stdout_read_fd, stdout_write_fd, stderr_fd


def _launch_python(
    script: str,
    *arguments: str,
    environment: Dict[str, str] | None = None,
) -> Tuple[WindowsManagedProcess, int]:
    """Launch an isolated Python snippet and return its process and stdout fd."""

    stdin_fd, stdout_read_fd, stdout_write_fd, stderr_fd = (
        _open_standard_handles()
    )
    try:
        process = launch_windows_managed_process(
            [_PYTHON_EXECUTABLE, "-I", "-u", "-c", script, *arguments],
            stdin_handle=_native_handle(stdin_fd),
            stdout_handle=_native_handle(stdout_write_fd),
            stderr_handle=_native_handle(stderr_fd),
            cwd=str(Path.cwd().resolve()),
            environment=environment,
        )
    except BaseException:
        os.close(stdout_read_fd)
        raise
    finally:
        os.close(stdin_fd)
        os.close(stdout_write_fd)
        os.close(stderr_fd)
    return process, stdout_read_fd


def _read_all(file_descriptor: int) -> bytes:
    """Consume a child pipe after the process has closed every writer."""

    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(file_descriptor, 4_096)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(file_descriptor)


def _read_line_with_deadline(
    file_descriptor: int,
    timeout_seconds: float,
) -> bytes:
    """Read one short pipe line without letting a failed child hang pytest."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.PeekNamedPipe.restype = ctypes.c_int
    deadline = monotonic() + timeout_seconds
    collected = bytearray()
    while monotonic() < deadline:
        available = ctypes.c_uint32()
        if not kernel32.PeekNamedPipe(
            ctypes.c_void_p(_native_handle(file_descriptor)),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            break
        if available.value:
            collected.extend(os.read(file_descriptor, available.value))
            if b"\n" in collected:
                return bytes(collected.split(b"\n", 1)[0])
        sleep(0.01)
    raise AssertionError("managed child did not publish its test line in time")


def _kernel_process_handle(process_id: int) -> int:
    """Open a synchronization-only handle to pin one test process identity."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, process_id)
    assert handle
    return int(handle)


def _wait_and_close_native_process(
    process_handle: int,
    timeout_milliseconds: int,
) -> int:
    """Wait for a pinned test process and release its synchronization handle."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    try:
        return int(
            kernel32.WaitForSingleObject(
                ctypes.c_void_p(process_handle), timeout_milliseconds
            )
        )
    finally:
        assert kernel32.CloseHandle(ctypes.c_void_p(process_handle))


def test_launch_passes_quoted_arguments_and_only_the_closed_environment() -> None:
    """Preserve list2cmdline quoting without inheriting a parent secret."""

    secret_name = "ELYSIA_PARENT_ONLY_SECRET"
    old_secret = os.environ.get(secret_name)
    os.environ[secret_name] = "must-not-cross-boundary"
    script = (
        "import json, os, sys; "
        "print(json.dumps({'argv':sys.argv[1:],"
        "'env':dict(os.environ)},sort_keys=True),flush=True)"
    )
    try:
        process, output_fd = _launch_python(
            script,
            "plain",
            "space value",
            'quote"value',
            "trailing\\",
            environment={"ELYSIA_ALLOWED": "yes"},
        )
        assert process.wait(5.0) == 0
        output = json.loads(_read_all(output_fd).decode("utf-8"))
    finally:
        if old_secret is None:
            os.environ.pop(secret_name, None)
        else:
            os.environ[secret_name] = old_secret

    assert output["argv"] == [
        "plain",
        "space value",
        'quote"value',
        "trailing\\",
    ]
    assert output["env"] == {"ELYSIA_ALLOWED": "yes"}
    assert process.is_alive() is False
    assert process.close() is True
    assert process.close() is True


def test_handle_list_excludes_an_unrelated_inheritable_handle() -> None:
    """Prove bInheritHandles cannot bypass the explicit attribute allowlist."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.SetHandleInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    kernel32.SetHandleInformation.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    event_handle = int(kernel32.CreateEventW(None, True, False, None))
    assert event_handle
    assert kernel32.SetHandleInformation(
        ctypes.c_void_p(event_handle),
        _HANDLE_FLAG_INHERIT,
        _HANDLE_FLAG_INHERIT,
    )
    script = (
        "import ctypes, sys; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "k.SetEvent.argtypes=[ctypes.c_void_p]; k.SetEvent.restype=ctypes.c_int; "
        "print(bool(k.SetEvent(ctypes.c_void_p(int(sys.argv[1])))),flush=True)"
    )
    try:
        process, output_fd = _launch_python(script, str(event_handle))
        assert process.wait(5.0) == 0
        assert _read_all(output_fd).strip() == b"False"
        assert (
            kernel32.WaitForSingleObject(ctypes.c_void_p(event_handle), 0)
            == _WAIT_TIMEOUT
        )
        assert process.close() is True
    finally:
        assert kernel32.CloseHandle(ctypes.c_void_p(event_handle))


def test_terminate_kills_the_root_and_its_grandchild() -> None:
    """Close the Job as one unit so helper descendants cannot survive."""

    script = (
        "import os, subprocess, sys, time; "
        "g=subprocess.Popen([sys.executable,'-I','-c',"
        "'import time; time.sleep(60)']); "
        "print(os.getpid(),g.pid,flush=True); time.sleep(60)"
    )
    process, output_fd = _launch_python(script)
    root_id, grandchild_id = (
        int(value)
        for value in _read_line_with_deadline(output_fd, 5.0).split()
    )
    assert root_id == process.pid
    root_handle = _kernel_process_handle(root_id)
    grandchild_handle = _kernel_process_handle(grandchild_id)

    try:
        assert process.terminate(5.0) is True
        assert _wait_and_close_native_process(root_handle, 5_000) == _WAIT_OBJECT_0
        root_handle = 0
        assert (
            _wait_and_close_native_process(grandchild_handle, 5_000)
            == _WAIT_OBJECT_0
        )
        grandchild_handle = 0
        assert process.is_alive() is False
        assert process.close() is True
    finally:
        os.close(output_fd)
        if root_handle:
            _wait_and_close_native_process(root_handle, 0)
        if grandchild_handle:
            _wait_and_close_native_process(grandchild_handle, 0)
        process.close(timeout_seconds=0.0)


def test_context_manager_closes_a_sleeping_child_and_is_idempotent() -> None:
    """Make normal ownership syntax terminate work even after user exceptions."""

    process, output_fd = _launch_python("import time; time.sleep(60)")
    assert process.wait(0.0) is None
    with pytest.raises(RuntimeError, match="test interruption"):
        with process:
            assert process.is_alive()
            raise RuntimeError("test interruption")

    os.close(output_fd)
    assert process.closed is True
    assert process.is_alive() is False
    assert process.close() is True
    assert process.terminate() is True


def test_concurrent_teardown_callers_share_one_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not publish success while the first teardown owner is paused."""

    process, output_fd = _launch_python("import time; time.sleep(60)")
    entered_teardown = Event()
    release_teardown = Event()
    second_started = Event()
    second_finished = Event()
    result_lock = Lock()
    results: dict[str, bool] = {}
    errors: list[BaseException] = []
    original_teardown = WindowsManagedProcess._perform_teardown

    def paused_teardown(
        selected: WindowsManagedProcess,
        timeout_seconds: float,
        *,
        process_handle: int,
        thread_handle: int,
        job_handle: int,
    ) -> object:
        """Pause after ownership publication but before any native cleanup."""

        entered_teardown.set()
        assert release_teardown.wait(5.0)
        return original_teardown(
            selected,
            timeout_seconds,
            process_handle=process_handle,
            thread_handle=thread_handle,
            job_handle=job_handle,
        )

    def close_first() -> None:
        """Become the teardown owner and record its final result."""

        try:
            result = process.close(2.0)
            with result_lock:
                results["close"] = result
        except BaseException as error:
            errors.append(error)

    def terminate_second() -> None:
        """Join the owner's immutable attempt instead of detaching the Job."""

        second_started.set()
        try:
            result = process.terminate(2.0)
            with result_lock:
                results["terminate"] = result
        except BaseException as error:
            errors.append(error)
        finally:
            second_finished.set()

    monkeypatch.setattr(
        WindowsManagedProcess, "_perform_teardown", paused_teardown
    )
    first_thread = Thread(target=close_first, daemon=True)
    second_thread = Thread(target=terminate_second, daemon=True)
    try:
        first_thread.start()
        assert entered_teardown.wait(2.0)
        assert process.closed is False
        assert "closing" in repr(process)
        second_thread.start()
        assert second_started.wait(2.0)
        assert second_finished.wait(0.1) is False
        assert process.closed is False

        release_teardown.set()
        first_thread.join(5.0)
        second_thread.join(5.0)
        assert first_thread.is_alive() is False
        assert second_thread.is_alive() is False
        assert errors == []
        assert results == {"close": True, "terminate": True}
        assert process.closed is True
    finally:
        release_teardown.set()
        first_thread.join(1.0)
        second_thread.join(1.0)
        process.close(timeout_seconds=0.0)
        os.close(output_fd)


def test_failed_handle_close_keeps_exact_ownership_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish a stable failure and retain the Job until a later retry closes it."""

    process, output_fd = _launch_python("import time; time.sleep(60)")
    job_handle = process._job_handle
    original_close_handle = managed_process_module._close_handle
    failed_once = False

    def fail_first_job_close(handle: int) -> bool:
        """Simulate one native Job close failure without closing that handle."""

        nonlocal failed_once
        if handle == job_handle and not failed_once:
            failed_once = True
            return False
        return original_close_handle(handle)

    monkeypatch.setattr(
        managed_process_module, "_close_handle", fail_first_job_close
    )
    try:
        with pytest.raises(WindowsManagedProcessControlError):
            process.close(2.0)
        assert process.closed is False
        assert process._job_handle == job_handle
        assert process.close(2.0) is True
        assert process.closed is True
    finally:
        process.close(timeout_seconds=0.0)
        os.close(output_fd)


def test_managed_launches_serialize_the_temporary_inheritable_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent a second launch from overlapping private inheritable duplicates."""

    first_duplicate_entered = Event()
    release_first_duplicate = Event()
    second_launch_started = Event()
    second_launch_duplicate = Event()
    call_lock = Lock()
    duplicate_calls = 0
    launched: list[Tuple[WindowsManagedProcess, int]] = []
    errors: list[BaseException] = []
    original_duplicate = managed_process_module._duplicate_handle

    def paused_duplicate(handle: int, *, inheritable: bool) -> int:
        """Hold the first launch inside the module-level creation lock."""

        nonlocal duplicate_calls
        with call_lock:
            duplicate_calls += 1
            call_number = duplicate_calls
        if call_number == 1:
            first_duplicate_entered.set()
            assert release_first_duplicate.wait(5.0)
        elif call_number == 4:
            second_launch_duplicate.set()
        return original_duplicate(handle, inheritable=inheritable)

    def launch_sleeping_child() -> None:
        """Launch one lightweight child and retain it for deterministic cleanup."""

        try:
            launched.append(
                _launch_python("import time; time.sleep(60)")
            )
        except BaseException as error:
            errors.append(error)

    def launch_second_child() -> None:
        """Signal scheduling before asking the serialized launcher to proceed."""

        second_launch_started.set()
        launch_sleeping_child()

    monkeypatch.setattr(
        managed_process_module, "_duplicate_handle", paused_duplicate
    )
    first_thread = Thread(target=launch_sleeping_child, daemon=True)
    second_thread = Thread(target=launch_second_child, daemon=True)
    try:
        first_thread.start()
        assert first_duplicate_entered.wait(2.0)
        second_thread.start()
        assert second_launch_started.wait(2.0)
        assert second_launch_duplicate.wait(0.1) is False

        release_first_duplicate.set()
        first_thread.join(5.0)
        second_thread.join(5.0)
        assert first_thread.is_alive() is False
        assert second_thread.is_alive() is False
        assert errors == []
        assert len(launched) == 2
        assert second_launch_duplicate.is_set()
    finally:
        release_first_duplicate.set()
        first_thread.join(1.0)
        second_thread.join(1.0)
        for child, output_fd in launched:
            child.close(timeout_seconds=1.0)
            os.close(output_fd)


@pytest.mark.parametrize("bad_handle", [0, -1, True, 1 << 80])
def test_invalid_stream_handles_are_rejected_without_native_creation(
    bad_handle: object,
) -> None:
    """Reject null, sentinel, Boolean, and non-pointer-sized stream handles."""

    stdin_fd, stdout_read_fd, stdout_write_fd, stderr_fd = (
        _open_standard_handles()
    )
    try:
        with pytest.raises(WindowsManagedProcessValidationError):
            launch_windows_managed_process(
                [_PYTHON_EXECUTABLE, "-I", "-c", "pass"],
                stdin_handle=bad_handle,  # type: ignore[arg-type]
                stdout_handle=_native_handle(stdout_write_fd),
                stderr_handle=_native_handle(stderr_fd),
                cwd=str(Path.cwd().resolve()),
            )
    finally:
        os.close(stdin_fd)
        os.close(stdout_read_fd)
        os.close(stdout_write_fd)
        os.close(stderr_fd)


@pytest.mark.parametrize(
    ("arguments", "environment"),
    [
        (("\U0001f600" * 20_000,), {}),
        ((), {"NON_BMP": "\U0001f600" * 20_000}),
    ],
)
def test_command_and_environment_limits_count_utf16_code_units(
    arguments: Tuple[str, ...],
    environment: Dict[str, str],
) -> None:
    """Reject astral text that fits Python length but exceeds Win32 UTF-16 bounds."""

    stdin_fd, stdout_read_fd, stdout_write_fd, stderr_fd = (
        _open_standard_handles()
    )
    try:
        with pytest.raises(WindowsManagedProcessValidationError):
            launch_windows_managed_process(
                [_PYTHON_EXECUTABLE, *arguments],
                stdin_handle=_native_handle(stdin_fd),
                stdout_handle=_native_handle(stdout_write_fd),
                stderr_handle=_native_handle(stderr_fd),
                cwd=str(Path.cwd().resolve()),
                environment=environment,
            )
    finally:
        os.close(stdin_fd)
        os.close(stdout_read_fd)
        os.close(stdout_write_fd)
        os.close(stderr_fd)


@pytest.mark.parametrize(
    ("argv", "cwd", "environment"),
    [
        ([], str(Path.cwd().resolve()), {}),
        (["relative.exe"], str(Path.cwd().resolve()), {}),
        ([sys.executable, "bad\x00argument"], str(Path.cwd().resolve()), {}),
        ([sys.executable], "relative-directory", {}),
        ([sys.executable], str(Path.cwd().resolve()), {"BAD=KEY": "x"}),
        ([sys.executable], str(Path.cwd().resolve()), {"A": "x\x00y"}),
        (
            [sys.executable],
            str(Path.cwd().resolve()),
            {"Mixed": "one", "MIXED": "two"},
        ),
    ],
)
def test_invalid_launch_inputs_fail_before_native_creation(
    argv: object,
    cwd: object,
    environment: object,
) -> None:
    """Reject ambiguous commands, paths, and environment blocks up front."""

    stdin_fd, stdout_read_fd, stdout_write_fd, stderr_fd = (
        _open_standard_handles()
    )
    try:
        with pytest.raises(WindowsManagedProcessValidationError):
            launch_windows_managed_process(
                argv,  # type: ignore[arg-type]
                stdin_handle=_native_handle(stdin_fd),
                stdout_handle=_native_handle(stdout_write_fd),
                stderr_handle=_native_handle(stderr_fd),
                cwd=cwd,  # type: ignore[arg-type]
                environment=environment,  # type: ignore[arg-type]
            )
    finally:
        os.close(stdin_fd)
        os.close(stdout_read_fd)
        os.close(stdout_write_fd)
        os.close(stderr_fd)


@pytest.mark.parametrize(
    "timeout_seconds",
    [-1, math.inf, math.nan, 601, True, "5"],
)
def test_lifecycle_waits_reject_unbounded_or_ambiguous_values(
    timeout_seconds: object,
) -> None:
    """Keep caller-controlled waits finite, numeric, and operationally bounded."""

    process, output_fd = _launch_python("import time; time.sleep(60)")
    try:
        with pytest.raises(WindowsManagedProcessValidationError):
            process.wait(timeout_seconds)  # type: ignore[arg-type]
        with pytest.raises(WindowsManagedProcessValidationError):
            process.terminate(timeout_seconds)  # type: ignore[arg-type]
        with pytest.raises(WindowsManagedProcessValidationError):
            process.close(timeout_seconds)  # type: ignore[arg-type]
    finally:
        process.close(timeout_seconds=1.0)
        os.close(output_fd)


def test_failures_and_repr_do_not_disclose_sensitive_launch_inputs() -> None:
    """Keep executable, cwd, environment, and raw handles out of diagnostics."""

    secret = "DO-NOT-RENDER-THIS-SECRET"
    stdin_fd, stdout_read_fd, stdout_write_fd, stderr_fd = (
        _open_standard_handles()
    )
    try:
        with pytest.raises(WindowsManagedProcessError) as raised:
            launch_windows_managed_process(
                [f"C:\\missing\\{secret}\\python.exe", secret],
                stdin_handle=_native_handle(stdin_fd),
                stdout_handle=_native_handle(stdout_write_fd),
                stderr_handle=_native_handle(stderr_fd),
                cwd=str(Path.cwd().resolve()),
                environment={"PRIVATE": secret},
            )
        rendered_error = f"{raised.value!s} {raised.value!r}"
        assert secret not in rendered_error
        assert str(_native_handle(stdout_write_fd)) not in rendered_error
    finally:
        os.close(stdin_fd)
        os.close(stdout_read_fd)
        os.close(stdout_write_fd)
        os.close(stderr_fd)

    process, output_fd = _launch_python("print('ok',flush=True)")
    rendered_process = repr(process)
    assert sys.executable not in rendered_process
    assert str(process.pid) not in rendered_process
    assert "handle" not in rendered_process.casefold()
    assert process.wait(5.0) == 0
    assert _read_all(output_fd).strip() == b"ok"
    assert process.close() is True
