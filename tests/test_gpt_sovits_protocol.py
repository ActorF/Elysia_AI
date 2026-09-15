"""Test the private binary protocol shared with a managed GPT-SoVITS worker."""

from __future__ import annotations

import ast
from io import BytesIO
from pathlib import Path
import struct
from typing import List, Tuple

import pytest

from scripts.gpt_sovits_protocol import (
    FrameKind,
    PROTOCOL_HEADER_SIZE,
    PROTOCOL_MAGIC,
    PROTOCOL_MAX_JSON_DEPTH,
    PROTOCOL_MAX_JSON_INTEGER,
    PROTOCOL_MAX_METADATA_BYTES,
    PROTOCOL_MAX_PAYLOAD_BYTES,
    PROTOCOL_MAX_REQUEST_ID,
    PROTOCOL_VERSION,
    ProtocolFrame,
    ProtocolIOError,
    ProtocolTruncatedError,
    ProtocolValidationError,
    read_frame,
    write_frame,
)


_HEADER = struct.Struct(">8sBBHIIQ")


def _header(
    *,
    magic: bytes = PROTOCOL_MAGIC,
    version: int = PROTOCOL_VERSION,
    kind: int = int(FrameKind.READY),
    flags: int = 0,
    metadata_length: int = 2,
    payload_length: int = 0,
    request_id: int = 0,
) -> bytes:
    """Build one raw header so receiver-only failures can be isolated."""

    return _HEADER.pack(
        magic,
        version,
        kind,
        flags,
        metadata_length,
        payload_length,
        request_id,
    )


def _wire(
    metadata: bytes,
    *,
    kind: FrameKind = FrameKind.READY,
    payload: bytes = b"",
    request_id: int = 0,
) -> bytes:
    """Build a complete raw frame without invoking sender validation."""

    return _header(
        kind=int(kind),
        metadata_length=len(metadata),
        payload_length=len(payload),
        request_id=request_id,
    ) + metadata + payload


class _PartialReader:
    """Limit each read to exercise exact-read framing loops."""

    def __init__(self, data: bytes, chunk_size: int) -> None:
        """Store immutable input and one positive artificial read limit."""

        self._stream = BytesIO(data)
        self._chunk_size = chunk_size

    def read(self, size: int = -1) -> bytes:
        """Return no more than the configured partial-read size."""

        selected = self._chunk_size if size < 0 else min(size, self._chunk_size)
        return self._stream.read(selected)


class _PartialWriter:
    """Limit each write to exercise write-all framing loops."""

    def __init__(self, chunk_size: int) -> None:
        """Create an inspectable output stream and partial-write limit."""

        self.output = BytesIO()
        self._chunk_size = chunk_size

    def write(self, data: object) -> int:
        """Write only one bounded prefix and report its exact length."""

        view = memoryview(data)  # type: ignore[arg-type]
        written = min(len(view), self._chunk_size)
        self.output.write(view[:written])
        return written


class _ExplodingReader:
    """Raise a sensitive native read error for sanitization coverage."""

    def read(self, _size: int = -1) -> bytes:
        """Raise instead of exposing any protocol bytes."""

        raise OSError("private pipe and local path")


class _StalledWriter:
    """Model a writer that makes no forward progress."""

    def write(self, _data: object) -> int:
        """Report a zero-length write that the protocol must reject."""

        return 0


@pytest.mark.parametrize(
    ("kind", "request_id", "metadata", "payload"),
    [
        (FrameKind.INIT, 0, {"nonce": "init"}, b""),
        (FrameKind.READY, 0, {"runtime": "managed"}, b""),
        (FrameKind.SYNTHESIZE, 1, {"text": "你好"}, b""),
        (FrameKind.AUDIO, 1, {"format": "wav"}, b"RIFFraw-audio"),
        (FrameKind.ERROR, 0, {"code": "startup_failed"}, b""),
        (FrameKind.ERROR, 9, {"code": "synthesis_failed"}, b""),
        (FrameKind.STOP, 0, {}, b""),
        (FrameKind.STOPPED, 0, {}, b""),
    ],
)
def test_every_closed_kind_round_trips(
    kind: FrameKind,
    request_id: int,
    metadata: object,
    payload: bytes,
) -> None:
    """Round-trip every permitted kind with its canonical request-ID shape."""

    frame = ProtocolFrame(kind, request_id, metadata, payload)
    stream = BytesIO()

    write_frame(stream, frame)
    stream.seek(0)

    assert read_frame(stream) == frame
    assert stream.read() == b""


def test_header_is_fixed_width_and_big_endian() -> None:
    """Pin field offsets and integer byte order independently of struct unpack."""

    request_id = 0x0102030405060708
    stream = BytesIO()
    write_frame(
        stream,
        ProtocolFrame(FrameKind.SYNTHESIZE, request_id, {"a": 1}),
    )
    encoded = stream.getvalue()

    assert PROTOCOL_HEADER_SIZE == 28
    assert encoded[:8] == PROTOCOL_MAGIC
    assert encoded[8:10] == bytes((PROTOCOL_VERSION, FrameKind.SYNTHESIZE))
    assert encoded[10:12] == b"\x00\x00"
    assert encoded[12:16] == (7).to_bytes(4, "big")
    assert encoded[16:20] == (0).to_bytes(4, "big")
    assert encoded[20:28] == request_id.to_bytes(8, "big")
    assert encoded[28:] == b'{"a":1}'


@pytest.mark.parametrize("length", [0, 1, PROTOCOL_HEADER_SIZE - 1])
def test_short_header_is_a_stable_truncation(length: int) -> None:
    """Reject clean or partial EOF before interpreting attacker-controlled data."""

    with pytest.raises(ProtocolTruncatedError) as raised:
        read_frame(BytesIO(_header()[:length]))

    assert str(raised.value) == "GPT-SoVITS protocol frame is truncated."


@pytest.mark.parametrize(
    "encoded",
    [
        _header(metadata_length=2) + b"{",
        _header(
            kind=int(FrameKind.AUDIO),
            metadata_length=2,
            payload_length=3,
            request_id=1,
        )
        + b"{}ab",
    ],
)
def test_short_metadata_or_payload_is_a_stable_truncation(encoded: bytes) -> None:
    """Distinguish a well-sized header followed by incomplete body bytes."""

    with pytest.raises(ProtocolTruncatedError, match="truncated"):
        read_frame(BytesIO(encoded))


@pytest.mark.parametrize(
    "header",
    [
        _header(magic=b"NOTTTS01"),
        _header(version=PROTOCOL_VERSION + 1),
        _header(kind=0),
        _header(kind=255),
        _header(flags=1),
    ],
)
def test_bad_magic_version_kind_or_flags_is_rejected(header: bytes) -> None:
    """Keep the wire vocabulary closed before reading a variable-size body."""

    with pytest.raises(ProtocolValidationError, match="frame is invalid"):
        read_frame(BytesIO(header))


@pytest.mark.parametrize(
    "header",
    [
        _header(metadata_length=0),
        _header(metadata_length=PROTOCOL_MAX_METADATA_BYTES + 1),
        _header(
            kind=int(FrameKind.AUDIO),
            payload_length=PROTOCOL_MAX_PAYLOAD_BYTES + 1,
            request_id=1,
        ),
        _header(payload_length=1),
    ],
)
def test_declared_limits_and_kind_payload_rules_fail_before_body_read(
    header: bytes,
) -> None:
    """Validate all lengths before a stream can trigger body allocation."""

    stream = BytesIO(header + b"private trailing bytes")

    with pytest.raises(ProtocolValidationError, match="frame is invalid"):
        read_frame(stream)

    assert stream.tell() == PROTOCOL_HEADER_SIZE


@pytest.mark.parametrize(
    "metadata",
    [
        b'{"value":"\xff"}',
        b"[]",
        b"null",
        b'{"value":}',
        b'{"value":1,"value":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{ "value":1}',
        b'{"z":0,"a":1}',
        b'{"value":"\\u0061"}',
        b'{"value":1.0}',
        b'{"value":-0}',
    ],
)
def test_receiver_rejects_invalid_or_noncanonical_metadata(
    metadata: bytes,
) -> None:
    """Reject UTF-8, JSON, duplicate-key, constant, and canonicality ambiguity."""

    with pytest.raises(ProtocolValidationError) as raised:
        read_frame(BytesIO(_wire(metadata)))

    assert str(raised.value) == "GPT-SoVITS protocol frame is invalid."
    assert "value" not in str(raised.value)


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        {"value": 1.0},
        {"value": float("nan")},
        {"value": (1, 2)},
        {"value": b"private"},
        {1: "value"},
        {"value": PROTOCOL_MAX_JSON_INTEGER + 1},
        {"value": "\ud800"},
    ],
)
def test_sender_rejects_noncanonical_metadata_values(metadata: object) -> None:
    """Keep sender-only Python values out of canonical interoperable JSON."""

    with pytest.raises(ProtocolValidationError, match="frame is invalid"):
        ProtocolFrame(FrameKind.READY, 0, metadata)


def test_sender_rejects_excessive_metadata_depth_and_length() -> None:
    """Bound recursive validation and encoded metadata before transmission."""

    nested = {}  # type: object
    for _index in range(PROTOCOL_MAX_JSON_DEPTH + 1):
        nested = {"child": nested}

    with pytest.raises(ProtocolValidationError):
        ProtocolFrame(FrameKind.READY, 0, nested)
    with pytest.raises(ProtocolValidationError):
        ProtocolFrame(
            FrameKind.READY,
            0,
            {"value": "x" * PROTOCOL_MAX_METADATA_BYTES},
        )


@pytest.mark.parametrize(
    ("kind", "request_id"),
    [
        (FrameKind.INIT, 1),
        (FrameKind.READY, 1),
        (FrameKind.STOP, 1),
        (FrameKind.STOPPED, 1),
        (FrameKind.SYNTHESIZE, 0),
        (FrameKind.AUDIO, 0),
        (FrameKind.ERROR, -1),
        (FrameKind.ERROR, PROTOCOL_MAX_REQUEST_ID + 1),
        (FrameKind.ERROR, True),
    ],
)
def test_request_identifier_rules_are_closed(
    kind: FrameKind,
    request_id: int,
) -> None:
    """Reserve zero for controls and positive IDs for request-response pairs."""

    payload = b"audio" if kind is FrameKind.AUDIO else b""
    with pytest.raises(ProtocolValidationError, match="frame is invalid"):
        ProtocolFrame(kind, request_id, {}, payload)


def test_error_accepts_the_maximum_unsigned_request_identifier() -> None:
    """Preserve the complete u64 correlation range without integer truncation."""

    frame = ProtocolFrame(FrameKind.ERROR, PROTOCOL_MAX_REQUEST_ID)

    assert frame.request_id == PROTOCOL_MAX_REQUEST_ID


def test_sender_rejects_wrong_or_excessive_payloads() -> None:
    """Allow non-empty raw bytes only on bounded AUDIO frames."""

    with pytest.raises(ProtocolValidationError):
        ProtocolFrame(FrameKind.AUDIO, 1)
    with pytest.raises(ProtocolValidationError):
        ProtocolFrame(FrameKind.INIT, 0, payload=b"not-a-control-payload")
    with pytest.raises(ProtocolValidationError):
        ProtocolFrame(FrameKind.AUDIO, 1, payload=bytearray(b"audio"))  # type: ignore[arg-type]
    with pytest.raises(ProtocolValidationError):
        ProtocolFrame(
            FrameKind.AUDIO,
            1,
            payload=b"x" * (PROTOCOL_MAX_PAYLOAD_BYTES + 1),
        )


def test_partial_reads_and_writes_preserve_one_complete_frame() -> None:
    """Loop over short stream operations without duplicating or dropping bytes."""

    expected = ProtocolFrame(
        FrameKind.AUDIO,
        42,
        {"format": "wav", "sample_rate": 32000},
        b"RIFF" + bytes(range(64)),
    )
    writer = _PartialWriter(3)

    write_frame(writer, expected)  # type: ignore[arg-type]
    actual = read_frame(_PartialReader(writer.output.getvalue(), 2))  # type: ignore[arg-type]

    assert actual == expected


def test_next_frame_remains_buffered_after_one_read() -> None:
    """Consume exact declared lengths so trailing bytes form the next frame."""

    first = ProtocolFrame(FrameKind.SYNTHESIZE, 7, {"text": "first"})
    second = ProtocolFrame(FrameKind.ERROR, 7, {"code": "failed"})
    stream = BytesIO()
    write_frame(stream, first)
    write_frame(stream, second)
    stream.seek(0)

    assert read_frame(stream) == first
    assert read_frame(stream) == second
    assert stream.read() == b""


def test_frame_repr_and_metadata_copy_do_not_disclose_private_content() -> None:
    """Keep prompts and audio out of diagnostics and immutable frame state."""

    frame = ProtocolFrame(
        FrameKind.AUDIO,
        3,
        {"private_prompt": "do not log me"},
        b"private-audio-bytes",
    )
    metadata = frame.metadata
    metadata["private_prompt"] = "mutated"
    rendered = repr(frame)

    assert "do not log me" not in rendered
    assert "private-audio-bytes" not in rendered
    assert frame.metadata == {"private_prompt": "do not log me"}
    assert "metadata_length=" in rendered
    assert "payload_length=" in rendered


def test_frame_fields_cannot_be_mutated_after_validation() -> None:
    """Prevent callers from changing canonical sender bytes behind validation."""

    frame = ProtocolFrame(FrameKind.READY, 0, {"state": "ready"})

    with pytest.raises(AttributeError, match="immutable"):
        frame._metadata_bytes = b'{"state":NaN}'  # type: ignore[misc]
    with pytest.raises(AttributeError, match="immutable"):
        del frame._flags  # type: ignore[attr-defined]

    stream = BytesIO()
    write_frame(stream, frame)
    stream.seek(0)
    assert read_frame(stream) == frame


def test_write_revalidates_internal_canonical_metadata() -> None:
    """Reject even an internal bypass of ordinary frame immutability."""

    frame = ProtocolFrame(FrameKind.READY, 0, {"state": "ready"})
    object.__setattr__(frame, "_metadata_bytes", b'{"state":NaN}')

    with pytest.raises(ProtocolValidationError, match="frame is invalid"):
        write_frame(BytesIO(), frame)


def test_native_stream_failures_are_sanitized() -> None:
    """Map sensitive read details and stalled writes to stable protocol errors."""

    with pytest.raises(ProtocolIOError) as read_error:
        read_frame(_ExplodingReader())  # type: ignore[arg-type]
    with pytest.raises(ProtocolIOError) as write_error:
        write_frame(_StalledWriter(), ProtocolFrame(FrameKind.STOP, 0))  # type: ignore[arg-type]

    assert str(read_error.value) == "GPT-SoVITS protocol I/O failed."
    assert "private pipe" not in str(read_error.value)
    assert str(write_error.value) == "GPT-SoVITS protocol I/O failed."


def test_source_parses_with_the_python_39_grammar() -> None:
    """Keep the shared runtime module usable by upstream Python 3.9 installs."""

    source_path = Path(__file__).resolve().parents[1] / "scripts" / (
        "gpt_sovits_protocol.py"
    )
    source = source_path.read_text(encoding="utf-8")

    ast.parse(source, filename=str(source_path), feature_version=(3, 9))
