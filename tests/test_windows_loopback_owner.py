"""Test exact Windows IPv4 loopback listener ownership verification."""

from __future__ import annotations

import ctypes
from ipaddress import IPv4Address
import os
import socket
from typing import Iterable

import pytest

import image_generation._windows_loopback_owner as owner_module
from image_generation._windows_loopback_owner import (
    windows_ipv4_loopback_listener_is_owned,
)


_UNAVAILABLE_MESSAGE = (
    "Windows IPv4 loopback listener ownership verification is unavailable."
)


def _row(
    *,
    address: str = "127.0.0.1",
    port: int = 48_123,
    state: int = 2,
    process_id: int = 4_242,
) -> owner_module._MIB_TCPROW_OWNER_PID:
    """Build one native-layout owner row from readable endpoint values."""

    value = owner_module._MIB_TCPROW_OWNER_PID()
    value.dwState = state
    value.dwLocalAddr = socket.htonl(int(IPv4Address(address)))
    value.dwLocalPort = socket.htons(port)
    value.dwRemoteAddr = 0
    value.dwRemotePort = 0
    value.dwOwningPid = process_id
    return value


def _table_payload(
    rows: Iterable[owner_module._MIB_TCPROW_OWNER_PID],
    *,
    declared_count: int | None = None,
) -> bytes:
    """Serialize rows with the count prefix used by the Windows table API."""

    frozen_rows = tuple(rows)
    count = owner_module._DWORD(
        len(frozen_rows) if declared_count is None else declared_count
    )
    parts = [
        ctypes.string_at(ctypes.byref(count), ctypes.sizeof(count)),
    ]
    parts.extend(
        ctypes.string_at(ctypes.byref(row), ctypes.sizeof(row))
        for row in frozen_rows
    )
    return b"".join(parts)


def _install_static_table(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> list[tuple[bool, bool, int, int, int]]:
    """Install a deterministic two-call table API and return its call ledger."""

    calls: list[tuple[bool, bool, int, int, int]] = []

    def fake_get_extended_tcp_table(
        table_pointer: object,
        size_pointer: object,
        ordered: object,
        address_family: object,
        table_class: object,
        reserved: object,
    ) -> int:
        size = ctypes.cast(
            size_pointer, ctypes.POINTER(owner_module._DWORD)
        ).contents
        calls.append(
            (
                bool(table_pointer),
                bool(ordered),
                int(address_family),
                int(table_class),
                int(reserved),
            )
        )
        if not table_pointer:
            size.value = len(payload)
            return owner_module._ERROR_INSUFFICIENT_BUFFER
        if int(size.value) < len(payload):
            size.value = len(payload)
            return owner_module._ERROR_INSUFFICIENT_BUFFER
        ctypes.memmove(table_pointer, payload, len(payload))
        size.value = len(payload)
        return owner_module._NO_ERROR

    monkeypatch.setattr(owner_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        owner_module,
        "_get_extended_tcp_table",
        fake_get_extended_tcp_table,
    )
    return calls


@pytest.mark.parametrize("port", [True, False, 1.0, "8188", None])
def test_rejects_non_integer_ports(port: object) -> None:
    """Reject coercible and Boolean ports before consulting native state."""

    with pytest.raises(TypeError, match=r"^port must be an integer\.$"):
        windows_ipv4_loopback_listener_is_owned(port, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("port", [-1, 0, 65_536])
def test_rejects_ports_outside_the_tcp_range(port: int) -> None:
    """Reject integers that cannot identify a TCP listener."""

    with pytest.raises(ValueError, match=r"^port must be between 1 and 65535\.$"):
        windows_ipv4_loopback_listener_is_owned(port, 1)


@pytest.mark.parametrize("process_id", [True, False, 1.0, "42", None])
def test_rejects_non_integer_process_ids(process_id: object) -> None:
    """Reject coercible and Boolean process identifiers before native access."""

    with pytest.raises(TypeError, match=r"^process_id must be an integer\.$"):
        windows_ipv4_loopback_listener_is_owned(1, process_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("process_id", [-1, 0, 0x1_0000_0000])
def test_rejects_process_ids_outside_a_positive_dword(process_id: int) -> None:
    """Reject PIDs that cannot equal the native unsigned owner field."""

    with pytest.raises(
        ValueError,
        match=r"^process_id must be a positive 32-bit integer\.$",
    ):
        windows_ipv4_loopback_listener_is_owned(1, process_id)


def test_non_windows_platform_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a stable unavailable result instead of guessing off Windows."""

    monkeypatch.setattr(owner_module, "_IS_WINDOWS", False)

    with pytest.raises(RuntimeError) as raised:
        windows_ipv4_loopback_listener_is_owned(8_188, 42)

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert raised.value.__cause__ is None


def test_exact_listener_owner_matches_and_uses_owner_pid_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept an exact endpoint/PID row and pin every native query selector."""

    calls = _install_static_table(
        monkeypatch,
        _table_payload(
            (
                _row(address="0.0.0.0"),
                _row(process_id=4_243),
                _row(),
            )
        ),
    )

    assert windows_ipv4_loopback_listener_is_owned(48_123, 4_242) is True
    assert calls == [
        (False, False, 2, 3, 0),
        (True, False, 2, 3, 0),
    ]


@pytest.mark.parametrize(
    ("address", "port", "state", "process_id"),
    [
        ("0.0.0.0", 48_123, 2, 4_242),
        ("127.0.0.2", 48_123, 2, 4_242),
        ("127.0.0.1", 48_124, 2, 4_242),
        ("127.0.0.1", 48_123, 5, 4_242),
        ("127.0.0.1", 48_123, 2, 4_243),
    ],
)
def test_rejects_every_non_exact_listener_row(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
    port: int,
    state: int,
    process_id: int,
) -> None:
    """Require address, port, LISTEN state, and owner PID to match together."""

    _install_static_table(
        monkeypatch,
        _table_payload(
            (
                _row(
                    address=address,
                    port=port,
                    state=state,
                    process_id=process_id,
                ),
            )
        ),
    )

    assert windows_ipv4_loopback_listener_is_owned(48_123, 4_242) is False


def test_empty_listener_table_is_a_definitive_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return False when a successful owner-table snapshot has no listeners."""

    _install_static_table(monkeypatch, _table_payload(()))

    assert windows_ipv4_loopback_listener_is_owned(48_123, 4_242) is False


def test_retries_when_the_listener_table_grows_during_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover from one bounded size race without weakening the final match."""

    payload = _table_payload((_row(),))
    calls = 0

    def fake_get_extended_tcp_table(
        table_pointer: object,
        size_pointer: object,
        _ordered: object,
        _address_family: object,
        _table_class: object,
        _reserved: object,
    ) -> int:
        nonlocal calls
        calls += 1
        size = ctypes.cast(
            size_pointer, ctypes.POINTER(owner_module._DWORD)
        ).contents
        if calls == 1:
            size.value = ctypes.sizeof(owner_module._DWORD)
            return owner_module._ERROR_INSUFFICIENT_BUFFER
        if calls == 2:
            size.value = len(payload)
            return owner_module._ERROR_INSUFFICIENT_BUFFER
        ctypes.memmove(table_pointer, payload, len(payload))
        size.value = len(payload)
        return owner_module._NO_ERROR

    monkeypatch.setattr(owner_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        owner_module,
        "_get_extended_tcp_table",
        fake_get_extended_tcp_table,
    )

    assert windows_ipv4_loopback_listener_is_owned(48_123, 4_242) is True
    assert calls == 3


@pytest.mark.parametrize("failure_mode", ["status", "exception", "missing"])
def test_native_query_failures_use_one_stable_error(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    """Collapse unavailable bindings and native failures into one safe result."""

    def failing_get_extended_tcp_table(*_arguments: object) -> int:
        if failure_mode == "exception":
            raise OSError("sensitive native diagnostic")
        return 5

    monkeypatch.setattr(owner_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        owner_module,
        "_get_extended_tcp_table",
        None if failure_mode == "missing" else failing_get_extended_tcp_table,
    )

    with pytest.raises(RuntimeError) as raised:
        windows_ipv4_loopback_listener_is_owned(48_123, 4_242)

    assert str(raised.value) == _UNAVAILABLE_MESSAGE
    assert raised.value.__cause__ is None
    assert "sensitive" not in str(raised.value)


def test_truncated_native_table_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a row count that would read beyond the returned table bytes."""

    payload = _table_payload((_row(),), declared_count=2)
    _install_static_table(monkeypatch, payload)

    with pytest.raises(RuntimeError) as raised:
        windows_ipv4_loopback_listener_is_owned(48_123, 4_242)

    assert str(raised.value) == _UNAVAILABLE_MESSAGE


@pytest.mark.skipif(
    os.name != "nt",
    reason="The integration check requires the native Windows TCP owner table.",
)
def test_live_loopback_listener_reports_its_real_process_owner() -> None:
    """Verify the structure layout and byte order against a live TCP socket."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        process_id = os.getpid()
        different_process_id = (
            process_id + 1 if process_id < 0xFFFFFFFF else process_id - 1
        )

        assert windows_ipv4_loopback_listener_is_owned(port, process_id) is True
        assert (
            windows_ipv4_loopback_listener_is_owned(port, different_process_id)
            is False
        )
