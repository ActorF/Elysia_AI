"""Exercise the private Python GPU broker client without inference or CUDA.

Socket pairs preserve real blocking reads, short IPC ownership messages, late
grants, and EOF semantics while keeping these concurrency checks deterministic.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import socket
from threading import Event, Thread
from typing import Any, BinaryIO, cast

import pytest

import gpu_admission as gpu


@pytest.fixture
def broker() -> Iterator[tuple[gpu.GpuAdmissionClient, Any, socket.socket]]:
    """Own both private endpoints and unblock the reader before final cleanup."""

    client_socket, native_socket = socket.socketpair()
    native_socket.settimeout(2)
    native_stream = native_socket.makefile("rwb", buffering=0)
    client = gpu.GpuAdmissionClient(cast(BinaryIO, client_socket.makefile("rwb", buffering=0)))
    try:
        yield client, native_stream, native_socket
    finally:
        native_socket.shutdown(socket.SHUT_RDWR)
        native_stream.close()
        native_socket.close()
        client.close()
        client_socket.close()
        client._reader.join(2)
        gpu.configure_gpu_client(None)


def _start(client: gpu.GpuAdmissionClient, **kwargs: Any) -> tuple[Thread, list[Any]]:
    result: list[Any] = []

    def execute() -> None:
        try:
            result.append(client.acquire("image-generation", **kwargs))
        except Exception as error:
            result.append(error)

    thread = Thread(target=execute)
    thread.start()
    return thread, result


def _message(stream: Any) -> dict[str, Any]:
    return json.loads(stream.readline(513))


def _reply(stream: Any, identity: str, status: str = "granted") -> None:
    stream.write((json.dumps({"id": identity, "status": status}) + "\n").encode())


def test_grant_retained_until_exact_lease_release(broker: Any) -> None:
    """A grant permits one worker and releases exactly once after explicit cleanup."""

    client, native, _ = broker
    thread, result = _start(client)
    request = _message(native)
    assert request == {"action": "acquire", "owner": "image-generation", "id": request["id"]}
    _reply(native, request["id"])
    thread.join(2)
    assert isinstance(result[0], gpu.GpuLease)
    result[0].release()
    result[0].release()
    assert _message(native) == {"action": "release", "id": request["id"]}
    assert not client.closed


def test_cancelled_wait_releases_only_its_late_grant(broker: Any) -> None:
    """Cancellation before grant retains a tombstone until a possible late grant."""

    client, native, _ = broker
    cancelled = Event()
    thread, result = _start(client, cancel_requested=cancelled.is_set)
    request = _message(native)
    cancelled.set()
    assert _message(native) == {"action": "cancel", "id": request["id"]}
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionCancelledError)
    _reply(native, request["id"])
    assert _message(native) == {"action": "release", "id": request["id"]}
    assert not client.closed


def test_cancel_after_grant_has_one_release_claim(broker: Any) -> None:
    """The cancellation path shares ownership with the grant reader under one lock."""

    client, native, _ = broker
    probe_entered, probe_return = Event(), Event()

    def cancelled() -> bool:
        probe_entered.set()
        assert probe_return.wait(2)
        return True

    thread, result = _start(client, cancel_requested=cancelled)
    request = _message(native)
    assert probe_entered.wait(2)
    with client._lock:
        pending = client._pending[request["id"]]
    _reply(native, request["id"])
    assert pending.ready.wait(2)
    probe_return.set()
    assert _message(native)["action"] == "cancel"
    assert _message(native)["action"] == "release"
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionCancelledError)
    assert pending.release_claimed
    assert not client.closed


def test_probe_failure_cancels_and_releases_late_grant(broker: Any) -> None:
    """A broken cancellation probe must not strand a request that can still win."""

    client, native, _ = broker

    def broken() -> bool:
        raise RuntimeError("private cancellation details")

    thread, result = _start(client, cancel_requested=broken)
    request = _message(native)
    assert _message(native)["action"] == "cancel"
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionError)
    assert "private" not in str(result[0])
    _reply(native, request["id"])
    assert _message(native)["action"] == "release"


def test_timeout_cancels_bounded_wait(broker: Any) -> None:
    """The local deadline ends waiting without pretending an unseen grant vanished."""

    client, native, _ = broker
    thread, result = _start(client, timeout_seconds=0.02)
    request = _message(native)
    assert _message(native)["action"] == "cancel"
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionError)
    _reply(native, request["id"], "cancelled")


def test_poisoned_lease_never_releases_on_scope_exit(broker: Any) -> None:
    """Unproved native cleanup quarantines capacity instead of freeing it in finally."""

    client, native, _ = broker
    thread, result = _start(client)
    request = _message(native)
    _reply(native, request["id"])
    thread.join(2)
    lease = result[0]
    lease.poison()
    lease.release()
    lease.poison()
    assert _message(native) == {"action": "poison", "id": request["id"]}
    assert lease._poisoned and not lease._released


@pytest.mark.parametrize("status", ["denied", "cancelled"])
def test_native_refusal_does_not_start_work(broker: Any, status: str) -> None:
    """A native denial cannot be converted into a local GPU capability."""

    client, native, _ = broker
    thread, result = _start(client)
    request = _message(native)
    _reply(native, request["id"], status)
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionError)


@pytest.mark.parametrize("payload", [
    b"{}\n", b"x" * 513 + b"\n", b'{"id":"bad","status":"granted"}\n',
    b'{"id":"00000000000000000000000000000000","status":"granted"}\n',
    b'{"id":"bad","id":"bad","status":"denied"}\n',
])
def test_invalid_native_frame_closes_admission(broker: Any, payload: bytes) -> None:
    """Malformed, duplicate, oversized, and unsolicited frames fail the whole session."""

    client, native, _ = broker
    thread, result = _start(client)
    _message(native)
    native.write(payload)
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionError)
    assert client.closed


def test_eof_rejects_waiter_without_fake_release(broker: Any) -> None:
    """Main pipe loss wakes admission while retaining physical cleanup responsibility."""

    client, native, native_socket = broker
    thread, result = _start(client)
    _message(native)
    native_socket.shutdown(socket.SHUT_WR)
    thread.join(2)
    assert isinstance(result[0], gpu.GpuAdmissionError)
    assert client.closed


def test_console_scope_preserves_existing_behavior() -> None:
    """Library use without Desktop admission remains usable and exception transparent."""

    gpu.configure_gpu_client(None)
    assert not gpu.using_desktop_gpu()
    with pytest.raises(ValueError, match="original"):
        with gpu.gpu_lease("ollama") as lease:
            assert not lease.cancelled
            raise ValueError("original")
