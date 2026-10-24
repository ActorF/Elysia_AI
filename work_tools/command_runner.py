"""Run one authorized CMD request in a managed Windows Job and return its receipt.

The fixed NDJSON helper receives no model-selected executable or environment.
Its caller owns permission and conversation identity. CMD has the current
Windows user's file and network access: the Job only contains ordinary child
processes, not work delegated to Windows services or a filesystem sandbox.
Closing the private input channel cancels the request. No final receipt is
published before the Job has been confirmed empty and both output pipes drain.
"""

from __future__ import annotations

import codecs
from io import BufferedReader, RawIOBase
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Any, BinaryIO, cast

from voice._windows_managed_process import (
    WindowsManagedProcess, launch_windows_managed_process,
)

MAX_OUTPUT_BYTES = 16_384
MAX_REQUEST_BYTES = 65_536


def _text(value: object, maximum: int, *, nonempty: bool = False) -> str:
    if (not isinstance(value, str) or "\0" in value
            or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
            or len(value.encode("utf-8")) > maximum or nonempty and not value.strip()):
        raise ValueError("Invalid command text.")
    return value


def _absolute(value: str) -> bool:
    return re.match(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))", value) is not None


def _request(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"command", "cwd", "timeoutMs"}:
        raise ValueError("Invalid command request.")
    command = _text(value["command"], 8192, nonempty=True)
    # CMD /c silently ignores text after a newline. Reject multiline proposals
    # before execution; use explicit &, &&, parentheses, or a reviewed script.
    if "\r" in command or "\n" in command:
        raise ValueError("CMD requires a single command line.")
    cwd = _text(value["cwd"], 2048, nonempty=True)
    timeout = value["timeoutMs"]
    # CMD accepts CreateProcess's UNC cwd but silently falls back to Windows.
    # Requiring a drive-qualified directory prevents success in the wrong place;
    # an authorized command may still use UNC paths as explicit file arguments.
    if re.match(r"^[A-Za-z]:[\\/]", cwd) is None or type(timeout) is not int or not 1000 <= timeout <= 120_000:
        raise ValueError("Invalid command context.")
    return {"command": command, "cwd": cwd, "timeoutMs": timeout}


def _environment() -> dict[str, str]:
    # Preserve ordinary application discovery without forwarding credentials,
    # Python import hooks, or Electron's private session/broker variables.
    parent = {key.upper(): value for key, value in os.environ.items()}
    root = parent.get("SYSTEMROOT", "")
    if not _absolute(root) or "\0" in root:
        raise ValueError("Windows is unavailable.")
    directories = [ntpath.join(root, "System32")]
    seen = {directories[0].casefold()}
    for entry in parent.get("PATH", "").split(";"):
        entry = entry.strip().strip('"')
        if (not entry or not _absolute(entry) or any(char in entry for char in "\0\r\n")
                or entry.casefold() in seen):
            continue
        seen.add(entry.casefold())
        directories.append(entry)
    result = {
        "SystemRoot": root, "WINDIR": root, "PATH": ";".join(directories),
        "ComSpec": ntpath.join(root, "System32", "cmd.exe"),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD", "NoDefaultCurrentDirectoryInExePath": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
    }
    for name in ("TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                 "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        value = parent.get(name, "")
        if value and _absolute(value) and not any(char in value for char in "\0\r\n"):
            result[name] = value
    for name in ("COMPUTERNAME", "USERNAME", "USERDOMAIN", "USERDNSDOMAIN", "HOMEDRIVE", "HOMEPATH",
                 "SYSTEMDRIVE", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS", "OS"):
        value = parent.get(name, "")
        if value and len(value) <= 2048 and not any(char in value for char in "\0\r\n"):
            result[name] = value
    return result


def _decode_output(data: bytes, *, truncated: bool, split_lines: bool = True) -> str:
    """Decode text pipes while recognizing Windows' explicit Unicode output.

    CMD normally inherits our UTF-8 console, but ``cmd /u`` deliberately emits
    BOM-less UTF-16 and tools may write BOM-marked text. A BOM is authoritative;
    otherwise require UTF-16 ASCII/null alignment or its CRLF structure. Never
    infer UTF-16 merely because an unrelated tool emitted invalid UTF-8 bytes.
    """
    encoding = "utf-8"
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif split_lines and b"\r\n" in data and (b"\r\0\n\0" in data or b"\0\r\0\n" in data):
        # A command sequence can combine ordinary CMD and cmd /u children in
        # the same pipe. Their complete CRLF lines carry their own encoding
        # structure; decoding the entire pipe as either format corrupts one
        # child. Preserve each line in order, including its original newline.
        values: list[str] = []
        start = 0
        for newline in re.finditer(b"(?=(\r\0\n\0|\0\r\0\n|\r\n))", data):
            delimiter = newline.group(1)
            # UTF-16 CRLF byte patterns overlap by one byte across endianness.
            # Only a code-unit-aligned delimiter belongs to the current line.
            if newline.start() < start or len(delimiter) == 4 and (newline.start() - start) % 2:
                continue
            end = newline.start() + len(delimiter)
            values.append(_decode_output(data[start:end], truncated=False, split_lines=False))
            start = end
        if start < len(data):
            values.append(_decode_output(data[start:], truncated=truncated, split_lines=False))
        return "".join(values)
    elif len(data) >= 4 and b"\r\n" not in data:
        pairs = len(data) // 2
        low_zeros, high_zeros = data[0::2].count(0), data[1::2].count(0)
        little_newline = any(match.start() % 2 == 0 for match in re.finditer(b"\r\0\n\0", data))
        big_newline = any(match.start() % 2 == 0 for match in re.finditer(b"\0\r\0\n", data))
        if little_newline or (high_zeros >= 2 and high_zeros * 2 >= pairs and high_zeros > low_zeros * 2):
            encoding = "utf-16-le"
        elif big_newline or (low_zeros >= 2 and low_zeros * 2 >= pairs and low_zeros > high_zeros * 2):
            encoding = "utf-16-be"
    # A retained prefix can end inside a UTF-8 character or UTF-16 surrogate.
    # Leave that unfinished suffix out instead of inventing a replacement
    # character; invalid bytes inside otherwise complete output remain visible.
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    return decoder.decode(data, final=not truncated).replace("\0", "\ufffd")


class _Output:
    """Drain both pipes while retaining one shared bounded byte budget."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.data: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        self.size = 0
        self.truncated = False
        self.truncated_streams: set[str] = set()

    def drain(self, descriptor: int, name: str) -> None:
        """Consume to EOF even after the retained budget is full to avoid deadlock."""
        try:
            while chunk := os.read(descriptor, 4096):
                with self.lock:
                    retained = chunk[:max(0, MAX_OUTPUT_BYTES - self.size)]
                    self.data[name].extend(retained)
                    self.size += len(retained)
                    self.truncated |= len(retained) != len(chunk)
                    if len(retained) != len(chunk):
                        self.truncated_streams.add(name)
        finally:
            os.close(descriptor)

    def strings(self) -> tuple[str, str, bool]:
        """Normalize invalid external encodings without exceeding the UTF-8 wire cap."""
        remaining = MAX_OUTPUT_BYTES
        values: list[str] = []
        truncated = self.truncated
        for name in ("stdout", "stderr"):
            encoded = _decode_output(bytes(self.data[name]), truncated=name in self.truncated_streams).encode("utf-8")
            value = encoded[:remaining].decode("utf-8", errors="ignore")
            truncated |= len(encoded) > remaining
            remaining -= len(value.encode("utf-8"))
            values.append(value)
        return values[0], values[1], truncated


def _receipt(request: dict[str, Any], output: _Output, exit_code: int | None, termination: str) -> dict[str, Any]:
    stdout, stderr, truncated = output.strings()
    success = termination == "exit" and exit_code == 0
    status = "succeeded" if success else "cancelled" if termination == "cancelled" else "failed"
    error = None if success else "timeout" if termination == "timeout" else "cancelled" if termination == "cancelled" else "failed"
    summary = {"exit": f"CMD exited with code {exit_code}.", "timeout": "CMD timed out and its process tree was stopped.",
               "cancelled": "CMD was cancelled and its process tree was stopped.", "spawn_failed": "CMD could not start."}[termination]
    if truncated:
        summary += " Output was truncated."
    return {"status": status, "text": summary, "error": error, "command": {
        "command": request["command"], "cwd": request["cwd"], "stdout": stdout, "stderr": stderr,
        "exitCode": exit_code, "termination": termination, "truncated": truncated,
    }}


def run_command(value: object, cancelled: Event | None = None) -> dict[str, Any]:
    """Execute one validated request, retaining the Job until every ordinary child ends.

    Callers must obtain authorization before invoking this function. Returned
    output is untrusted evidence, never another instruction or permission.
    """
    request = _request(value)
    cancelled = cancelled if cancelled is not None else Event()
    output = _Output()
    if cancelled.is_set():
        return _receipt(request, output, None, "cancelled")
    process: WindowsManagedProcess | None = None
    descriptors: list[int] = []
    readers: list[Thread] = []
    exit_code: int | None = None
    termination = "spawn_failed"
    try:
        if os.name != "nt" or not Path(request["cwd"]).is_dir():
            raise ValueError("Command directory is unavailable.")
        import msvcrt

        environment = _environment()
        executable = environment["ComSpec"]
        # Redirected CMD output chooses its encoding when the interpreter
        # starts. Bootstrap a private UTF-8 console before starting the actual
        # interpreter. Delayed expansion inserts the command only after the
        # bootstrap's metacharacter/percent parsing, so pipes, quotes, carets,
        # percent variables and literal exclamation marks reach CMD unchanged.
        # The actual user command keeps normal delayed expansion disabled.
        environment["ELYSIA_WORK_COMMAND"] = request["command"]
        command_line = (subprocess.list2cmdline([executable])
                        + ' /d /q /v:on /s /c "chcp 65001>nul & "' + executable
                        + '" /d /q /v:off /s /c "!ELYSIA_WORK_COMMAND!""')
        stdin_fd = os.open(os.devnull, os.O_RDONLY | os.O_BINARY)
        descriptors.append(stdin_fd)
        stdout_read, stdout_write = os.pipe()
        descriptors.extend((stdout_read, stdout_write))
        stderr_read, stderr_write = os.pipe()
        descriptors.extend((stderr_read, stderr_write))
        if cancelled.is_set():
            return _receipt(request, output, None, "cancelled")
        process = launch_windows_managed_process(
            [executable], raw_command_line=command_line,
            stdin_handle=int(msvcrt.get_osfhandle(stdin_fd)),
            stdout_handle=int(msvcrt.get_osfhandle(stdout_write)),
            stderr_handle=int(msvcrt.get_osfhandle(stderr_write)),
            cwd=request["cwd"], environment=environment,
        )
        for descriptor in (stdin_fd, stdout_write, stderr_write):
            os.close(descriptor)
            descriptors.remove(descriptor)
        for descriptor, name in ((stdout_read, "stdout"), (stderr_read, "stderr")):
            thread = Thread(target=output.drain, args=(descriptor, name), daemon=True)
            thread.start()
            readers.append(thread)
            descriptors.remove(descriptor)
        deadline = monotonic() + request["timeoutMs"] / 1000
        while True:
            if cancelled.is_set():
                termination = "cancelled"
                break
            exit_code = process.wait(0.02)
            if exit_code is not None:
                termination = "exit"
                break
            if monotonic() >= deadline:
                termination = "timeout"
                break
    except Exception:
        # Native errors may contain secrets and paths. Actual stdout/stderr are
        # separate intentional receipts; diagnostics use only closed messages.
        termination = "cancelled" if cancelled.is_set() else "spawn_failed"
    finally:
        if process is not None:
            # Root exit does not imply descendant exit. A failed teardown must
            # keep this helper/lease alive; Main's watchdog can kill the helper,
            # which closes its private kill-on-close Job handle as a last resort.
            while not process.closed:
                try:
                    if process.close(1.0):
                        break
                except Exception:
                    pass
                sleep(0.02)
        for descriptor in descriptors:
            os.close(descriptor)
        for thread in readers:
            thread.join()
    return _receipt(request, output, exit_code if termination == "exit" else None, termination)


def _watch_input(stream: BinaryIO, cancelled: Event) -> None:
    try:
        # Exactly one request is permitted. EOF, an extra message, or a broken
        # private channel all revoke the current command rather than queue work.
        # Raw fd I/O avoids holding BufferedReader's lock during interpreter
        # shutdown when a successful command leaves this daemon waiting on Main.
        os.read(stream.fileno(), 1)
    finally:
        cancelled.set()


def main() -> int:
    """Accept one bounded NDJSON request and emit one final UTF-8 receipt."""
    try:
        # Use the unbuffered stream: BufferedReader.readline may prefetch a
        # second message that the raw cancellation watcher would never see.
        raw_stdin = cast(RawIOBase, cast(BufferedReader, sys.stdin.buffer).raw)
        raw = raw_stdin.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            return 2
        request = _request(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeError):
        return 2
    cancelled = Event()
    Thread(target=_watch_input, args=(sys.stdin.buffer, cancelled), daemon=True).start()
    result = run_command(request, cancelled)
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
