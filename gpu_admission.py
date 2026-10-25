"""Coordinate Python GPU users with Electron's single admission authority.

The private duplex fd5 carries only bounded ownership messages, independently
of user NDJSON, speech, and PNG pipes. A lease remains held through physical
cleanup. Unproven cleanup poisons admission until Main observes process exit.
Library/console use without an installed Desktop broker preserves its existing
execution policy; production Desktop installs this client before model work.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
import re
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from typing import BinaryIO, Literal
from uuid import uuid4

GpuOwner = Literal["ollama", "gpt-sovits", "image-generation"]
_ID = re.compile(r"^[0-9a-f]{32}$")
_CLIENT: GpuAdmissionClient | None = None


class GpuAdmissionError(Exception):
    """Report unavailable, closed, exhausted, or invalid GPU admission safely."""


class GpuAdmissionCancelledError(GpuAdmissionError):
    """Report cancellation before physical GPU execution began."""


@dataclass
class _Pending:
    ready: Event = field(default_factory=Event)
    status: str | None = None
    cancelled: bool = False
    release_claimed: bool = False


class GpuLease:
    """Retain one exact Main grant through model execution and physical cleanup."""

    def __init__(self, client: GpuAdmissionClient | None, identity: str,
                 cancel_requested: Callable[[], bool] | None = None) -> None:
        """Bind a private grant and optional cooperative cancellation probe."""

        self._client = client
        self._id = identity
        self._cancel = cancel_requested
        self._poisoned = False
        self._released = False
        self._lock = Lock()

    @property
    def cancelled(self) -> bool:
        """Report cooperative stop without treating it as physical release."""

        return (self._client is not None and self._client.closed) or (
            self._cancel is not None and self._cancel()
        )

    def poison(self) -> None:
        """Quarantine a grant when its native process cleanup cannot be proved."""

        with self._lock:
            if self._poisoned or self._released:
                return
            self._poisoned = True
        if self._client is not None:
            self._client.send("poison", self._id)

    def release(self) -> None:
        """Release once after cleanup, retaining poisoned grants for process exit."""

        with self._lock:
            if self._released or self._poisoned:
                return
            self._released = True
        if self._client is not None:
            self._client.send("release", self._id)


class GpuAdmissionClient:
    """Own one bounded session of request/grant exchanges on the private pipe."""

    def __init__(self, stream: BinaryIO, *, write_stream: BinaryIO | None = None,
                 _interrupt_reader: Callable[[Thread], None] | None = None,
                 _available_bytes: Callable[[], int] | None = None) -> None:
        """Own private streams, with an independent writer for inherited CRT pipes.

        Socket test/library streams can provide their own duplex implementation.
        The fixed Desktop pipe uses separate descriptors and available-byte
        polling: Windows serializes synchronous I/O on a shared pipe file
        object, even across duplicated descriptors. Its reader must never wait
        inside ReadFile for bytes that Main has not sent yet.
        """

        self._stream = stream
        self._write_stream = stream if write_stream is None else write_stream
        self._interrupt_reader = _interrupt_reader
        self._available_bytes = _available_bytes
        self._read_buffer = bytearray()
        self._stop = Event()
        self._lock = Lock()
        self._write_lock = Lock()
        self._reader_done = Event()
        self._pending: dict[str, _Pending] = {}
        self._closed = False
        self._reader = Thread(target=self._read, name="elysia-gpu-grants", daemon=True)
        self._reader.start()

    @classmethod
    def from_inherited_fd5(cls) -> GpuAdmissionClient:
        """Own Electron's fixed IPC pipe, leaving unrelated descriptors untouched."""

        stream: BinaryIO | None = None
        write_stream: BinaryIO | None = None
        read_fd: int | None = None
        owns_descriptor = False
        try:
            # The image pipe helper performs the same native handle-kind check;
            # fd5 is checked independently rather than accepting a caller path.
            import stat
            import ctypes
            import importlib

            if os.name == "nt":
                native = getattr(importlib.import_module("msvcrt"), "get_osfhandle")(5)
                file_type = ctypes.WinDLL("kernel32", use_last_error=True).GetFileType
                file_type.argtypes = [ctypes.c_void_p]
                file_type.restype = ctypes.c_uint32
                valid = file_type(ctypes.c_void_p(native)) == 3
            else:
                mode = os.fstat(5).st_mode
                valid = stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)
            if not valid:
                raise ValueError
            # Missing inherited descriptors can be reused by import-time log
            # files. Ownership begins only after the OS confirms an IPC pipe;
            # rejecting another handle must not change or close that resource.
            owns_descriptor = True
            os.set_inheritable(5, False)
            # Duplicate before starting either I/O operation. Distinct CRT
            # descriptors avoid the Windows read/write lock inversion while
            # preserving one private duplex connection and fixed fd ownership.
            read_fd = os.dup(5)
            os.set_inheritable(read_fd, False)
            reader_descriptor = read_fd
            read_native = (getattr(importlib.import_module("msvcrt"), "get_osfhandle")(read_fd)
                           if os.name == "nt" else None)
            stream = os.fdopen(read_fd, "rb", buffering=0, closefd=True)
            read_fd = None
            write_stream = os.fdopen(5, "wb", buffering=0, closefd=True)
            return cls(stream, write_stream=write_stream,
                       _interrupt_reader=_interrupt_windows_reader if os.name == "nt" else None,
                       _available_bytes=(lambda: _windows_available_bytes(read_native))
                       if read_native is not None else lambda: _posix_read_ready(reader_descriptor))
        except Exception:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            if read_fd is not None:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
            if write_stream is not None:
                try:
                    write_stream.close()
                except OSError:
                    pass
            elif owns_descriptor:
                try:
                    os.close(5)
                except OSError:
                    pass
        raise GpuAdmissionError("Desktop GPU admission is unavailable.")

    @property
    def closed(self) -> bool:
        """Read whether the broker can still grant physical execution."""

        with self._lock:
            return self._closed

    def acquire(self, owner: GpuOwner, *, cancel_requested: Callable[[], bool] | None = None,
                timeout_seconds: float = 120.0) -> GpuLease:
        """Wait boundedly for one exact grant, preserving late-cancel cleanup."""

        if owner not in {"ollama", "gpt-sovits", "image-generation"}:
            raise GpuAdmissionError("GPU owner is invalid.")
        if not 0.0 < timeout_seconds <= 120.0:
            raise GpuAdmissionError("GPU wait limit is invalid.")
        identity = uuid4().hex
        pending = _Pending()
        with self._lock:
            if self._closed or len(self._pending) >= 8:
                raise GpuAdmissionError("GPU admission is unavailable or full.")
            self._pending[identity] = pending
        self.send("acquire", identity, owner)
        deadline = monotonic() + timeout_seconds
        while True:
            probe_failed = False
            try:
                cancelled = cancel_requested is not None and cancel_requested()
            except Exception:
                cancelled = True
                probe_failed = True
            expired = monotonic() >= deadline
            if cancelled or expired:
                with self._lock:
                    pending.cancelled = True
                    granted = pending.status == "granted" and not pending.release_claimed
                    if granted:
                        pending.release_claimed = True
                self.send("cancel", identity)
                if granted:
                    self.send("release", identity)
                if probe_failed:
                    raise GpuAdmissionError("GPU cancellation state is unavailable.")
                if cancelled:
                    raise GpuAdmissionCancelledError("GPU wait was cancelled.")
                raise GpuAdmissionError("GPU admission wait timed out.")
            if pending.ready.wait(min(0.05, max(0.0, deadline - monotonic()))):
                if pending.status != "granted" or self.closed:
                    raise GpuAdmissionError("GPU admission was refused.")
                return GpuLease(self, identity, cancel_requested)

    def send(self, action: str, identity: str, owner: GpuOwner | None = None) -> None:
        """Write one fixed private ownership message without native diagnostics."""

        if action not in {"acquire", "release", "cancel", "poison"} or type(identity) is not str or _ID.fullmatch(identity) is None:
            raise GpuAdmissionError("GPU ownership message is invalid.")
        if (action == "acquire" and owner not in {"ollama", "gpt-sovits", "image-generation"}) or (
            action != "acquire" and owner is not None
        ):
            raise GpuAdmissionError("GPU ownership message is invalid.")
        message = {"action": action, "id": identity}
        if owner is not None:
            message["owner"] = owner
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("ascii")
        failed = False
        try:
            with self._write_lock:
                view = memoryview(payload)
                while view:
                    count = self._write_stream.write(view)
                    if type(count) is not int or not 0 < count <= len(view):
                        raise OSError
                    view = view[count:]
                self._write_stream.flush()
        except Exception:
            failed = True
        if failed:
            self.close()
            raise GpuAdmissionError("Desktop GPU admission I/O failed.")

    def close(self) -> None:
        """Reject waiters and interrupt owned reads without releasing GPU grants.

        Only the reader closes its streams after leaving the native read. On
        Windows, closing a raw CRT stream from another thread can deadlock on
        its descriptor lock. Bounded cancellation also covers the short race
        between the reader's closed check and entering its next native read.
        If native cancellation fails, the reader retains the handles until it
        exits; the descriptor numbers cannot be reused under an active read.
        """

        with self._lock:
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
        for request in pending:
            request.ready.set()
        self._stop.set()
        if current_thread() is self._reader:
            return
        deadline = monotonic() + 1.0
        while not self._reader_done.is_set():
            if self._interrupt_reader is None and self._available_bytes is None:
                # Generic streams own their own interruption policy. Never
                # close a descriptor while an unknown implementation reads it.
                break
            try:
                if self._interrupt_reader is not None:
                    self._interrupt_reader(self._reader)
            except Exception:
                pass
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            self._reader.join(min(0.05, remaining))
        if self._reader_done.is_set():
            self._reader.join(max(0.0, deadline - monotonic()))

    def _read(self) -> None:
        """Resolve bounded grants and release a cancelled request that won late."""

        try:
            while not self.closed:
                payload = self._read_payload()
                if not payload:
                    break
                if len(payload) > 512 or not payload.endswith(b"\n"):
                    raise ValueError
                value = json.loads(payload.decode("ascii"), object_pairs_hook=_unique_fields)
                if not isinstance(value, dict) or set(value) != {"id", "status"}:
                    raise ValueError
                identity, status = value["id"], value["status"]
                if type(identity) is not str or _ID.fullmatch(identity) is None or status not in {
                    "granted", "denied", "cancelled",
                }:
                    raise ValueError
                with self._lock:
                    pending = self._pending.pop(identity, None)
                    if pending is None:
                        raise ValueError
                    pending.status = status
                    release = pending.cancelled and status == "granted" and not pending.release_claimed
                    if release:
                        # Acquire cancellation and the reader can race after a
                        # grant. Claim under the same lock so only one releases.
                        pending.release_claimed = True
                    pending.ready.set()
                if release:
                    # No native operation started for this already cancelled
                    # waiter. Release the late grant so the FIFO can continue.
                    self.send("release", identity)
        except Exception:
            pass
        finally:
            self.close()
            try:
                with self._write_lock:
                    if self._write_stream is not self._stream:
                        try:
                            self._write_stream.close()
                        finally:
                            self._stream.close()
                    else:
                        self._stream.close()
            except Exception:
                pass
            finally:
                self._reader_done.set()

    def _read_payload(self) -> bytes:
        if self._available_bytes is None:
            return self._stream.readline(513)
        # A single reader consumes only bytes already reported by PeekNamedPipe.
        # Partial JSON frames remain bounded in Python rather than causing a
        # synchronous native read to wait for a delimiter and block all writes.
        while not self._stop.is_set():
            newline = self._read_buffer.find(b"\n")
            if newline >= 0:
                payload = bytes(self._read_buffer[:newline + 1])
                del self._read_buffer[:newline + 1]
                return payload
            if len(self._read_buffer) >= 513:
                raise ValueError
            available = self._available_bytes()
            if available:
                chunk = self._stream.read(min(available, 513 - len(self._read_buffer)))
                if not chunk:
                    return b""
                self._read_buffer.extend(chunk)
            else:
                self._stop.wait(0.01)
        return b""


def _windows_available_bytes(handle: int) -> int:
    """Inspect bounded pipe availability without waiting for a complete frame."""

    import ctypes

    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    peek.restype = ctypes.c_int
    available = ctypes.c_uint32()
    if not peek(handle, None, 0, None, ctypes.byref(available), None):
        raise OSError
    return available.value


def _posix_read_ready(descriptor: int) -> int:
    """Allow a bounded POSIX raw read only after readiness or EOF is observable."""

    import select

    # POSIX pipe/socket reads return available bytes without filling the whole
    # requested buffer. Unlike Windows, readiness therefore permits a capped
    # read directly, including the empty read that proves peer EOF.
    return 513 if select.select([descriptor], [], [], 0)[0] else 0


def _interrupt_windows_reader(reader: Thread) -> None:
    """Cancel this client's synchronous read without closing a live CRT handle."""

    import ctypes

    identity = reader.native_id
    if identity is None or not reader.is_alive():
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_thread = kernel.OpenThread
    open_thread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_thread.restype = ctypes.c_void_p
    cancel = kernel.CancelSynchronousIo
    cancel.argtypes = [ctypes.c_void_p]
    cancel.restype = ctypes.c_int
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_thread(0x0001, False, identity)  # THREAD_TERMINATE is required by CancelSynchronousIo.
    if handle:
        try:
            cancel(handle)
        finally:
            close_handle(handle)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def configure_gpu_client(client: GpuAdmissionClient | None) -> None:
    """Install the process-owned Desktop broker before constructing model services."""

    global _CLIENT
    _CLIENT = client


def using_desktop_gpu() -> bool:
    """Return whether model adapters must use Main's global admission boundary."""

    return _CLIENT is not None


@contextmanager
def gpu_lease(owner: GpuOwner, *, cancel_requested: Callable[[], bool] | None = None) -> Iterator[GpuLease]:
    """Hold one global grant until an operation and its cleanup both leave scope."""

    client = _CLIENT
    lease = GpuLease(None, "") if client is None else client.acquire(owner, cancel_requested=cancel_requested)
    try:
        yield lease
    finally:
        lease.release()
