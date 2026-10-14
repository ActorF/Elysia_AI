"""Verify ownership of one exact Windows IPv4 loopback TCP listener.

An HTTP health check can prove that a service answers on a port, but it cannot
prove that the answering process is the child Elysia launched.  This module
queries Windows' kernel-maintained TCP owner table and accepts only a LISTEN
row whose address is exactly ``127.0.0.1``, whose port matches the requested
port, and whose owning PID matches the managed child.

The check deliberately fails closed.  Unsupported platforms, unavailable
native APIs, inconsistent table data, and native query failures all raise the
same stable ``RuntimeError`` instead of being confused with a negative owner
match.  Callers can therefore translate infrastructure failure into their own
unavailable state while treating an ordinary ``False`` as a definitive
listener mismatch.
"""

from __future__ import annotations

import ctypes
import os
import socket
from typing import Any


_IS_WINDOWS = os.name == "nt"
_AF_INET = 2
_TCP_TABLE_OWNER_PID_LISTENER = 3
_MIB_TCP_STATE_LISTEN = 2
_NO_ERROR = 0
_ERROR_INSUFFICIENT_BUFFER = 122
_IPV4_LOOPBACK_HOST_ORDER = 0x7F000001
_MAX_PORT = 65_535
_MAX_PROCESS_ID = 0xFFFFFFFF
_MAX_TCP_TABLE_BYTES = 64 * 1024 * 1024
_MAX_TABLE_QUERY_ATTEMPTS = 3
_UNAVAILABLE_MESSAGE = (
    "Windows IPv4 loopback listener ownership verification is unavailable."
)

_DWORD = ctypes.c_uint32


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    """Mirror one fixed-width row returned by ``GetExtendedTcpTable``."""

    _fields_ = [
        ("dwState", _DWORD),
        ("dwLocalAddr", _DWORD),
        ("dwLocalPort", _DWORD),
        ("dwRemoteAddr", _DWORD),
        ("dwRemotePort", _DWORD),
        ("dwOwningPid", _DWORD),
    ]


_iphlpapi: Any = None
_get_extended_tcp_table: Any = None
if _IS_WINDOWS:
    try:
        _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        _get_extended_tcp_table = _iphlpapi.GetExtendedTcpTable
        _get_extended_tcp_table.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_DWORD),
            ctypes.c_int32,
            _DWORD,
            ctypes.c_int32,
            _DWORD,
        ]
        _get_extended_tcp_table.restype = _DWORD
    except (AttributeError, OSError):
        # Import must remain safe when a restricted Windows environment omits
        # the API.  The public call below converts this state into one stable,
        # fail-closed error rather than leaving a partially initialized binding.
        _iphlpapi = None
        _get_extended_tcp_table = None


def _validate_port(port: object) -> int:
    """Return one canonical TCP port or reject ambiguous Python values."""

    if type(port) is not int:
        raise TypeError("port must be an integer.")
    if not 1 <= port <= _MAX_PORT:
        raise ValueError("port must be between 1 and 65535.")
    return port


def _validate_process_id(process_id: object) -> int:
    """Return one positive DWORD PID suitable for native identity matching."""

    if type(process_id) is not int:
        raise TypeError("process_id must be an integer.")
    if not 1 <= process_id <= _MAX_PROCESS_ID:
        raise ValueError("process_id must be a positive 32-bit integer.")
    return process_id


def _checked_table_size(size: int, *, minimum: int) -> int:
    """Validate a native table size before allocating or reading its buffer."""

    if size < minimum or size > _MAX_TCP_TABLE_BYTES:
        raise RuntimeError(_UNAVAILABLE_MESSAGE)
    return size


def _query_ipv4_listener_table() -> tuple[ctypes.Array[Any], int]:
    """Return one bounded snapshot of the IPv4 owner-PID listener table.

    TCP listeners may appear between the size probe and the data query, so
    Windows can report ``ERROR_INSUFFICIENT_BUFFER`` more than once.  A small
    bounded retry absorbs that normal race without permitting an unbounded
    allocation loop.  The hard byte ceiling also prevents corrupt native size
    data from turning this security check into process-wide memory exhaustion.
    """

    if not _IS_WINDOWS or _get_extended_tcp_table is None:
        raise RuntimeError(_UNAVAILABLE_MESSAGE)

    required_size = _DWORD(0)
    status = int(
        _get_extended_tcp_table(
            None,
            ctypes.byref(required_size),
            False,
            _AF_INET,
            _TCP_TABLE_OWNER_PID_LISTENER,
            0,
        )
    )
    if status not in {_NO_ERROR, _ERROR_INSUFFICIENT_BUFFER}:
        raise RuntimeError(_UNAVAILABLE_MESSAGE)

    next_size = _checked_table_size(
        int(required_size.value), minimum=ctypes.sizeof(_DWORD)
    )
    for _attempt in range(_MAX_TABLE_QUERY_ATTEMPTS):
        capacity = next_size
        buffer = (ctypes.c_ubyte * capacity)()
        reported_size = _DWORD(capacity)
        status = int(
            _get_extended_tcp_table(
                ctypes.cast(buffer, ctypes.c_void_p),
                ctypes.byref(reported_size),
                False,
                _AF_INET,
                _TCP_TABLE_OWNER_PID_LISTENER,
                0,
            )
        )
        if status == _NO_ERROR:
            table_size = _checked_table_size(
                int(reported_size.value), minimum=ctypes.sizeof(_DWORD)
            )
            if table_size > capacity:
                raise RuntimeError(_UNAVAILABLE_MESSAGE)
            return buffer, table_size
        if status != _ERROR_INSUFFICIENT_BUFFER:
            raise RuntimeError(_UNAVAILABLE_MESSAGE)

        # A genuine table-growth race must ask for more space than the buffer
        # just rejected.  Equal or shrinking sizes are an inconsistent native
        # response and cannot safely be retried as though they were trusted.
        candidate_size = _checked_table_size(
            int(reported_size.value), minimum=ctypes.sizeof(_DWORD)
        )
        if candidate_size <= capacity:
            raise RuntimeError(_UNAVAILABLE_MESSAGE)
        next_size = candidate_size

    raise RuntimeError(_UNAVAILABLE_MESSAGE)


def _table_contains_owner(
    buffer: ctypes.Array[Any],
    table_size: int,
    *,
    port: int,
    process_id: int,
) -> bool:
    """Match a validated endpoint and PID against one bounded table snapshot."""

    header_size = ctypes.sizeof(_DWORD)
    row_size = ctypes.sizeof(_MIB_TCPROW_OWNER_PID)
    entry_count = int(_DWORD.from_buffer_copy(buffer, 0).value)
    required_size = header_size + entry_count * row_size
    if required_size > table_size:
        raise RuntimeError(_UNAVAILABLE_MESSAGE)

    for index in range(entry_count):
        row = _MIB_TCPROW_OWNER_PID.from_buffer_copy(
            buffer, header_size + index * row_size
        )
        if (
            int(row.dwState) == _MIB_TCP_STATE_LISTEN
            and socket.ntohl(int(row.dwLocalAddr))
            == _IPV4_LOOPBACK_HOST_ORDER
            and socket.ntohs(int(row.dwLocalPort) & 0xFFFF) == port
            and int(row.dwOwningPid) == process_id
        ):
            return True
    return False


def windows_ipv4_loopback_listener_is_owned(
    port: int,
    process_id: int,
) -> bool:
    """Verify that ``process_id`` owns the exact loopback listener ``port``.

    ``True`` means a Windows IPv4 TCP owner table snapshot contained a LISTEN
    row for exactly ``127.0.0.1:port`` with the requested owning PID.  ``False``
    means the snapshot completed successfully but contained no such row.

    ``port`` must be an integer from 1 through 65535, and ``process_id`` must be
    a positive 32-bit integer; invalid values raise ``TypeError`` or
    ``ValueError``.  Unsupported platforms and all native verification
    failures raise one stable ``RuntimeError`` so callers cannot accidentally
    authorize a child after an incomplete ownership check.
    """

    validated_port = _validate_port(port)
    validated_process_id = _validate_process_id(process_id)
    try:
        buffer, table_size = _query_ipv4_listener_table()
        return _table_contains_owner(
            buffer,
            table_size,
            port=validated_port,
            process_id=validated_process_id,
        )
    except Exception:
        # Native bindings and injected test doubles can fail in many ways.  Do
        # not expose platform diagnostics or let an unexpected exception form
        # a third, potentially misinterpreted ownership result.
        raise RuntimeError(_UNAVAILABLE_MESSAGE) from None


__all__ = ["windows_ipv4_loopback_listener_is_owned"]
