"""Exercise real CMD quoting, bounded receipts, and managed cancellation on Windows."""

from __future__ import annotations

import ctypes
import codecs
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from work_tools.command_runner import run_command


pytestmark = pytest.mark.skipif(os.name != "nt", reason="The CMD runner requires Windows.")
_ROOT = Path(__file__).resolve().parents[1]


def _request(command: str, cwd: Path, timeout: int = 5000) -> dict[str, object]:
    return {"command": command, "cwd": str(cwd), "timeoutMs": timeout}


def _run(command: str, cwd: Path, timeout: int = 5000) -> dict[str, object]:
    return run_command(_request(command, cwd, timeout))


@pytest.mark.parametrize(("command", "expected"), [
    ("ver", "Windows"), ("echo 中文输出", "中文输出"),
    ('echo "two words"', '"two words"'),
    ("echo %SystemRoot%", os.environ.get("SystemRoot", "C:\\Windows")),
    ("echo first & echo second", "second"), ("echo piped | findstr piped", "piped"),
    ("echo ^& literal ! bang", "& literal ! bang"),
])
def test_real_cmd_language(command: str, expected: str, tmp_path: Path) -> None:
    """Preserve real CMD parsing and Unicode instead of CRT backslash escaping."""
    result = _run(command, tmp_path)
    assert result["status"] == "succeeded"
    assert expected in result["command"]["stdout"]
    assert result["command"]["exitCode"] == 0


@pytest.mark.parametrize("message", ["ASCII output", "中文测试", "中文🙂 ASCII"])
def test_cmd_unicode_mode_preserves_bomless_utf16_output(tmp_path: Path, message: str) -> None:
    """Read explicit CMD /u output as UTF-16 even though the console uses UTF-8."""
    result = _run(f"cmd /d /u /c echo {message}", tmp_path)
    assert result["status"] == "succeeded"
    assert result["command"]["stdout"].strip() == message


def test_unicode_cmd_preserves_stderr_and_actual_nonzero_exit(tmp_path: Path) -> None:
    """Decode independently redirected CMD /u streams without masking failure."""
    result = _run('cmd /d /u /c "echo 中文stdout & echo 中文stderr 1>&2 & exit /b 7"', tmp_path)
    assert result["status"] == "failed"
    assert result["command"]["exitCode"] == 7
    assert result["command"]["stdout"].strip() == "中文stdout"
    assert result["command"]["stderr"].strip() == "中文stderr"


def test_mixed_cmd_unicode_lines_preserve_order_and_each_encoding(tmp_path: Path) -> None:
    """Combine normal CMD and cmd /u output without decoding one child incorrectly."""
    result = _run('echo 开始 & cmd /d /u /c echo 中文🙂 & echo 结束', tmp_path)
    assert result["status"] == "succeeded"
    assert [line.strip() for line in result["command"]["stdout"].splitlines()] == ["开始", "中文🙂", "结束"]


def test_unaligned_mixed_bytes_cannot_break_the_receipt_decoder(tmp_path: Path) -> None:
    """Keep an ambiguous binary/text mixture bounded without recursive decoding."""
    data = b"a" + "中文\r\n".encode("utf-16-le") + b"\r\n"
    source = f"import sys;sys.stdout.buffer.write(bytes.fromhex('{data.hex()}'))"
    result = _run(f'"{sys.executable}" -c "{source}"', tmp_path)
    assert result["status"] == "succeeded"
    assert result["command"]["stdout"].startswith("a")
    assert len(result["command"]["stdout"].encode("utf-8")) <= 16384


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be", "utf-32", "utf-32-be"])
def test_bom_marked_tool_output_decodes_independently_for_both_pipes(tmp_path: Path, encoding: str) -> None:
    """Preserve BOM-marked native output without leaking byte order marks into answers."""
    message = "工具输出🙂"
    data = message.encode(encoding)
    if encoding == "utf-16-be":
        data = codecs.BOM_UTF16_BE + data
    elif encoding == "utf-32-be":
        data = codecs.BOM_UTF32_BE + data
    source = f"import sys;sys.stdout.buffer.write(bytes.fromhex('{data.hex()}'));sys.stderr.buffer.write(bytes.fromhex('{data.hex()}'))"
    result = _run(f'"{sys.executable}" -c "{source}"', tmp_path)
    assert result["status"] == "succeeded"
    assert result["command"]["stdout"] == message
    assert result["command"]["stderr"] == message


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_truncated_unicode_output_ends_at_a_complete_character(tmp_path: Path, encoding: str) -> None:
    """Truncation marks omitted text without fabricating a broken final character."""
    source = f"import sys;sys.stdout.buffer.write(('界'*20000).encode('{encoding}'))"
    result = _run(f'"{sys.executable}" -c "{source}"', tmp_path)
    receipt = result["command"]
    assert result["status"] == "succeeded"
    assert receipt["truncated"] is True
    assert receipt["stdout"] and set(receipt["stdout"]) == {"界"}
    assert len(receipt["stdout"].encode("utf-8")) <= 16384


def test_command_reads_selected_cwd_and_quoted_space_path(tmp_path: Path) -> None:
    """Resolve a reviewed directory and a quoted executable containing spaces."""
    directory = tmp_path / "space folder"
    directory.mkdir()
    file = directory / "read only.txt"
    file.write_text("fixture body", encoding="utf-8")
    result = _run(f'type "{file}"', directory)
    assert result["command"]["stdout"] == "fixture body"
    result = _run("cd", directory)
    assert str(directory) in result["command"]["stdout"]


def test_quoted_executable_and_redirected_unicode_file(tmp_path: Path) -> None:
    """Keep executable-path quotes and redirection functional in an isolated fixture."""
    executable = tmp_path / "quoted executable.exe"
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", executable)
    result = _run(f'"{executable}" /d /c echo quoted-executable', tmp_path)
    assert result["status"] == "succeeded"
    assert "quoted-executable" in result["command"]["stdout"]
    result = _run('echo 中文文件>"output with spaces.txt" & type "output with spaces.txt"', tmp_path)
    assert result["status"] == "succeeded"
    assert "中文文件" in result["command"]["stdout"]
    assert "中文文件" in (tmp_path / "output with spaces.txt").read_text(encoding="utf-8")


def test_standard_machine_identity_environment_is_available(tmp_path: Path) -> None:
    """Answer ordinary identity queries while the surrounding environment remains closed."""
    result = _run("echo %COMPUTERNAME% & echo %USERNAME%", tmp_path)
    assert result["status"] == "succeeded"
    assert os.environ["COMPUTERNAME"] in result["command"]["stdout"]
    assert os.environ["USERNAME"] in result["command"]["stdout"]


def test_real_output_separates_stderr_and_nonzero_exit(tmp_path: Path) -> None:
    """Expose actual command failure and both streams without fabricating success."""
    result = _run("echo stdout & echo stderr 1>&2 & exit /b 7", tmp_path)
    assert result["status"] == "failed"
    assert result["command"]["termination"] == "exit"
    assert result["command"]["exitCode"] == 7
    assert result["command"]["stdout"].strip() == "stdout"
    assert result["command"]["stderr"].strip() == "stderr"


def test_both_pipes_drain_after_shared_output_budget(tmp_path: Path) -> None:
    """Avoid deadlock when stdout and stderr both exceed the retained wire limit."""
    result = _run('for /l %i in (1,1,3000) do @(echo abcdefghijklmnopqrstuvwxyz & echo stderr-abcdefghijklmnopqrstuvwxyz 1>&2)', tmp_path)
    receipt = result["command"]
    assert result["status"] == "succeeded"
    assert receipt["truncated"] is True
    assert len((receipt["stdout"] + receipt["stderr"]).encode("utf-8")) <= 16384


def test_parent_credentials_and_import_hooks_are_not_forwarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ordinary PATH discovery while stripping secret and import variables."""
    monkeypatch.setenv("ELYSIA_TEST_SECRET", "do-not-leak")
    monkeypatch.setenv("ELYSIA_DESKTOP_SESSION_TOKEN", "private-session")
    monkeypatch.setenv("PYTHONPATH", "private-import-hook")
    monkeypatch.setenv("PATH", ".;relative;" + os.environ["PATH"])
    result = _run("set", tmp_path)
    output = result["command"]["stdout"]
    assert "do-not-leak" not in output and "private-session" not in output and "private-import-hook" not in output
    assert "NoDefaultCurrentDirectoryInExePath=1" in output
    assert "PATH=.;" not in output and ";relative;" not in output


@pytest.mark.parametrize("changes", [
    {"command": ""}, {"command": "x\0"}, {"command": "\ud800"}, {"command": "x" * 8193},
    {"command": "echo alpha\necho beta"}, {"command": "echo alpha\r\necho beta"},
    {"cwd": "C:relative"}, {"cwd": "\\root-relative"}, {"cwd": r"\\localhost\D$\Elysia_AI"}, {"timeoutMs": True},
    {"timeoutMs": 999}, {"timeoutMs": 120001}, {"extra": "ignored"},
])
def test_invalid_request_never_reaches_cmd(changes: dict[str, object], tmp_path: Path) -> None:
    """Reject malformed fields, ambiguous paths, and unsupported timeout values."""
    with pytest.raises(ValueError):
        run_command({**_request("echo safe", tmp_path), **changes})


def test_missing_directory_has_truthful_spawn_failure(tmp_path: Path) -> None:
    """Reject unavailable cwd without silently running in the install directory."""
    result = _run("echo never", tmp_path / "absent")
    assert result["status"] == "failed"
    assert result["command"]["termination"] == "spawn_failed"
    assert result["command"]["exitCode"] is None


def _tree_command(tmp_path: Path) -> tuple[str, Path]:
    # Only isolated test children are created. The marker lets the test pin a
    # live process handle before cancellation so PID reuse cannot hide a leak.
    marker = tmp_path / "child.pid"
    script = tmp_path / "tree.py"
    script.write_text(
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"
        "print('child alive',flush=True)\n"
        "time.sleep(120)\n", encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}" "{marker}"', marker


def _pin_child(marker: Path) -> tuple[object, int]:
    deadline = monotonic() + 10
    while not marker.exists() and monotonic() < deadline:
        sleep(0.01)
    assert marker.exists(), "synthetic command did not start its child"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, int(marker.read_text()))
    assert handle
    return kernel, handle


def _assert_stopped(kernel: object, handle: int) -> None:
    try:
        assert kernel.WaitForSingleObject(handle, 1000) == 0
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancel_terminates_descendants(tmp_path: Path, cancel: bool) -> None:
    """Return only after a pinned grandchild is dead, including explicit cancellation."""
    command, marker = _tree_command(tmp_path)
    stopped = Event()
    results: list[dict[str, object]] = []
    thread = Thread(target=lambda: results.append(run_command(_request(command, tmp_path, 5000 if cancel else 1500), stopped)))
    thread.start()
    kernel, handle = _pin_child(marker)
    if cancel:
        stopped.set()
    thread.join(10)
    assert not thread.is_alive()
    _assert_stopped(kernel, handle)
    assert results[0]["command"]["termination"] == ("cancelled" if cancel else "timeout")
    assert "child alive" in results[0]["command"]["stdout"]


def test_helper_eof_cancels_and_returns_one_receipt(tmp_path: Path) -> None:
    """Closing Main's private stdin stops the Job before the helper publishes its result."""
    command, marker = _tree_command(tmp_path)
    helper = subprocess.Popen([sys.executable, "-B", "-u", "-s", "-m", "work_tools.command_runner"],
                              cwd=_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        helper.stdin.write((json.dumps(_request(command, tmp_path, 120000)) + "\n").encode())
        helper.stdin.flush()
        kernel, handle = _pin_child(marker)
        helper.stdin.close()
        assert helper.wait(10) == 0
        _assert_stopped(kernel, handle)
        lines = helper.stdout.read().decode("utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["command"]["termination"] == "cancelled"
        assert helper.stderr.read() == b""
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(10)


def test_second_message_in_same_write_cancels_without_eof(tmp_path: Path) -> None:
    """Do not lose prefetched extra input or wait indefinitely for a later EOF."""
    helper = subprocess.Popen([sys.executable, "-B", "-u", "-s", "-m", "work_tools.command_runner"],
                              cwd=_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        request = _request('ping -n 30 127.0.0.1 >nul', tmp_path, 120000)
        helper.stdin.write((json.dumps(request) + '\n{}\n').encode())
        helper.stdin.flush()
        assert helper.wait(10) == 0
        result = json.loads(helper.stdout.read().decode("utf-8"))
        assert result["command"]["termination"] == "cancelled"
        assert helper.stderr.read() == b""
    finally:
        helper.stdin.close()
        if helper.poll() is None:
            helper.kill()
            helper.wait(10)
