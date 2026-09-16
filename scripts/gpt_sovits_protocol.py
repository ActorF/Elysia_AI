"""Frame the private Elysia-to-GPT-SoVITS worker byte protocol.

The protocol deliberately uses a fixed-width binary header and raw payloads.
Audio can therefore reach a managed local worker without Base64 expansion or
the Desktop NDJSON frame limit.  Metadata is a small canonical JSON object;
strict decoding prevents parser ambiguity from becoming part of a process
binding or cache identity.

This module is intentionally standard-library-only and Python 3.9 compatible
so the application process and an isolated GPT-SoVITS runtime can load the
same framing implementation without importing either environment's packages.
"""

from __future__ import annotations

from enum import IntEnum
import json
import struct
from typing import Any, BinaryIO, Dict, List, Tuple


PROTOCOL_MAGIC = b"ELYTTS01"
PROTOCOL_VERSION = 1
PROTOCOL_HEADER_SIZE = 28
PROTOCOL_MAX_METADATA_BYTES = 64 * 1024
PROTOCOL_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
PROTOCOL_MAX_JSON_DEPTH = 32
PROTOCOL_MAX_JSON_INTEGER = 9_007_199_254_740_991
PROTOCOL_MAX_REQUEST_ID = (1 << 64) - 1

# Parent and Python 3.9 worker hash this tuple in order. Keeping it beside the
# shared wire contract prevents two independently edited manifests from making
# every otherwise-valid READY handshake fail.
MANAGED_RUNTIME_MANIFEST_RELATIVE_FILES = (
    ("runtime-python", "runtime/python.exe"),
    ("runtime-python-dll", "runtime/python39.dll"),
    ("runtime-python-abi-dll", "runtime/python3.dll"),
    ("runtime-python-path", "runtime/python39._pth"),
    ("runtime-stdlib-zip", "runtime/python39.zip"),
    ("runtime-vcruntime", "runtime/vcruntime140.dll"),
    ("runtime-vcruntime-1", "runtime/vcruntime140_1.dll"),
    ("runtime-libffi", "runtime/libffi-7.dll"),
    ("runtime-libcrypto", "runtime/libcrypto-1_1.dll"),
    ("runtime-libssl", "runtime/libssl-1_1.dll"),
    ("runtime-sqlite", "runtime/sqlite3.dll"),
    ("runtime-tcl", "runtime/tcl86t.dll"),
    ("runtime-tk", "runtime/tk86t.dll"),
    ("runtime-hashlib-extension", "runtime/_hashlib.pyd"),
    ("runtime-ssl-extension", "runtime/_ssl.pyd"),
    ("runtime-sqlite-extension", "runtime/_sqlite3.pyd"),
    ("runtime-ctypes-extension", "runtime/_ctypes.pyd"),
    ("runtime-socket-extension", "runtime/_socket.pyd"),
    ("runtime-select-extension", "runtime/select.pyd"),
    ("runtime-unicode-extension", "runtime/unicodedata.pyd"),
    ("ffmpeg", "ffmpeg.exe"),
    ("tts-entry", "GPT_SoVITS/TTS_infer_pack/TTS.py"),
    ("audio-loader", "tools/my_utils.py"),
    (
        "bert-config",
        "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/config.json",
    ),
    (
        "bert-model",
        "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/"
        "pytorch_model.bin",
    ),
    (
        "bert-tokenizer",
        "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/"
        "tokenizer.json",
    ),
    (
        "hubert-config",
        "GPT_SoVITS/pretrained_models/chinese-hubert-base/config.json",
    ),
    (
        "hubert-preprocessor",
        "GPT_SoVITS/pretrained_models/chinese-hubert-base/"
        "preprocessor_config.json",
    ),
    (
        "hubert-model",
        "GPT_SoVITS/pretrained_models/chinese-hubert-base/pytorch_model.bin",
    ),
)

# The copied interpreter and worker must agree on the complete closed sys.path
# spelling. Every entry is rendered below one separately verified DOS alias;
# no inherited, relative, or executable .pth entry is allowed.
MANAGED_RUNTIME_IMPORT_RELATIVE_ENTRIES = (
    "runtime/python39.zip",
    "runtime",
    "runtime/Lib/site-packages",
    "runtime/Lib/site-packages/ffmpy-0.0.3-py3.9.egg",
    "runtime/Lib/site-packages/future-0.18.2-py3.9.egg",
    "runtime/Lib/site-packages/win32",
    "runtime/Lib/site-packages/win32/lib",
    "runtime/Lib/site-packages/Pythonwin",
)

_HEADER = struct.Struct(">8sBBHIIQ")
_NO_METADATA = object()
_INVALID_MESSAGE = "GPT-SoVITS protocol frame is invalid."
_TRUNCATED_MESSAGE = "GPT-SoVITS protocol frame is truncated."
_IO_MESSAGE = "GPT-SoVITS protocol I/O failed."


class FrameKind(IntEnum):
    """Identify every permitted managed-worker protocol message."""

    INIT = 1
    READY = 2
    SYNTHESIZE = 3
    AUDIO = 4
    ERROR = 5
    STOP = 6
    STOPPED = 7


_CONTROL_KINDS = frozenset(
    (FrameKind.INIT, FrameKind.READY, FrameKind.STOP, FrameKind.STOPPED)
)
_REQUEST_KINDS = frozenset((FrameKind.SYNTHESIZE, FrameKind.AUDIO))


class ProtocolError(Exception):
    """Base class for sanitized managed-worker protocol failures."""


class ProtocolValidationError(ProtocolError):
    """Report a malformed, unsupported, or non-canonical frame."""


class ProtocolTruncatedError(ProtocolError):
    """Report EOF before a declared frame boundary was reached."""


class ProtocolIOError(ProtocolError):
    """Report a stream failure without exposing native error details."""


def _validate_json_value(value: Any, depth: int) -> None:
    """Accept only deterministic JSON-native values within a nesting bound."""

    if depth > PROTOCOL_MAX_JSON_DEPTH:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -PROTOCOL_MAX_JSON_INTEGER <= value <= PROTOCOL_MAX_JSON_INTEGER:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        return
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ProtocolValidationError(_INVALID_MESSAGE) from None
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ProtocolValidationError(_INVALID_MESSAGE)
            _validate_json_value(key, depth + 1)
            _validate_json_value(item, depth + 1)
        return
    # Floats are intentionally excluded.  Their cross-version spelling and
    # non-finite extensions are unnecessary for handshake/request metadata.
    raise ProtocolValidationError(_INVALID_MESSAGE)


def _encode_metadata(metadata: object) -> bytes:
    """Return the one canonical UTF-8 representation of a metadata object."""

    if type(metadata) is not dict:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    _validate_json_value(metadata, 0)
    try:
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        raise ProtocolValidationError(_INVALID_MESSAGE) from None
    if not 2 <= len(encoded) <= PROTOCOL_MAX_METADATA_BYTES:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    return encoded


def _strict_object(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    """Reject duplicate JSON keys instead of retaining the final value."""

    result = {}  # type: Dict[str, object]
    for key, value in pairs:
        if key in result:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    """Reject the non-standard NaN and Infinity JSON extensions."""

    raise ProtocolValidationError(_INVALID_MESSAGE)


def _decode_metadata(encoded: bytes) -> Dict[str, object]:
    """Decode one strict object and require canonical sender bytes."""

    if not 2 <= len(encoded) <= PROTOCOL_MAX_METADATA_BYTES:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    try:
        text = encoded.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        if type(value) is not dict:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        _validate_json_value(value, 0)
        if _encode_metadata(value) != encoded:
            raise ProtocolValidationError(_INVALID_MESSAGE)
    except ProtocolError:
        raise
    except (
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        UnicodeError,
        ValueError,
    ):
        raise ProtocolValidationError(_INVALID_MESSAGE) from None
    return value


def _validate_request_id(kind: FrameKind, request_id: object) -> int:
    """Enforce control-zero and synthesis-positive request identifiers."""

    if (
        type(request_id) is not int
        or not 0 <= request_id <= PROTOCOL_MAX_REQUEST_ID
    ):
        raise ProtocolValidationError(_INVALID_MESSAGE)
    if kind in _CONTROL_KINDS and request_id != 0:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    if kind in _REQUEST_KINDS and request_id == 0:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    # ERROR may identify startup with zero or one failed synthesis request.
    return request_id


def _validate_lengths(
    kind: FrameKind,
    metadata_length: object,
    payload_length: object,
) -> None:
    """Reject declared lengths before allocating either variable-size body."""

    if (
        type(metadata_length) is not int
        or not 2 <= metadata_length <= PROTOCOL_MAX_METADATA_BYTES
        or type(payload_length) is not int
        or not 0 <= payload_length <= PROTOCOL_MAX_PAYLOAD_BYTES
    ):
        raise ProtocolValidationError(_INVALID_MESSAGE)
    if kind is FrameKind.AUDIO:
        if payload_length == 0:
            raise ProtocolValidationError(_INVALID_MESSAGE)
    elif payload_length != 0:
        raise ProtocolValidationError(_INVALID_MESSAGE)


class ProtocolFrame:
    """Hold one validated frame without revealing its private body in repr."""

    _kind: FrameKind
    _flags: int
    _request_id: int
    _metadata_bytes: bytes
    _payload: bytes

    __slots__ = (
        "_kind",
        "_flags",
        "_request_id",
        "_metadata_bytes",
        "_payload",
    )

    def __init__(
        self,
        kind: FrameKind,
        request_id: int,
        metadata: object = _NO_METADATA,
        payload: bytes = b"",
        flags: int = 0,
    ) -> None:
        """Validate canonical sender values and copy their immutable bytes."""

        if not isinstance(kind, FrameKind) or type(kind) is not FrameKind:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        if type(flags) is not int or flags != 0:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        if type(payload) is not bytes:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        selected_metadata = {} if metadata is _NO_METADATA else metadata
        metadata_bytes = _encode_metadata(selected_metadata)
        _validate_request_id(kind, request_id)
        _validate_lengths(kind, len(metadata_bytes), len(payload))
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_flags", flags)
        object.__setattr__(self, "_request_id", request_id)
        object.__setattr__(self, "_metadata_bytes", metadata_bytes)
        object.__setattr__(self, "_payload", payload)

    @property
    def kind(self) -> FrameKind:
        """Return this frame's closed-set message kind."""

        return self._kind

    @property
    def flags(self) -> int:
        """Return the validated flags field, currently always zero."""

        return self._flags

    @property
    def request_id(self) -> int:
        """Return zero for controls or the positive synthesis correlation ID."""

        return self._request_id

    @property
    def metadata(self) -> Dict[str, object]:
        """Return a fresh mutable copy of the private metadata object."""

        return _decode_metadata(self._metadata_bytes)

    @property
    def payload(self) -> bytes:
        """Return the immutable raw payload bytes."""

        return self._payload

    @property
    def metadata_length(self) -> int:
        """Return the encoded canonical metadata byte length."""

        return len(self._metadata_bytes)

    @property
    def payload_length(self) -> int:
        """Return the raw payload byte length."""

        return len(self._payload)

    def __repr__(self) -> str:
        return (
            "ProtocolFrame("
            "kind={0}, request_id={1}, metadata_length={2}, "
            "payload_length={3})"
        ).format(
            self._kind.name,
            self._request_id,
            len(self._metadata_bytes),
            len(self._payload),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProtocolFrame):
            return NotImplemented
        return (
            self._kind == other._kind
            and self._flags == other._flags
            and self._request_id == other._request_id
            and self._metadata_bytes == other._metadata_bytes
            and self._payload == other._payload
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("ProtocolFrame values are immutable.")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("ProtocolFrame values are immutable.")


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    """Read exactly one validated length despite partial stream reads."""

    chunks = bytearray()
    while len(chunks) < length:
        try:
            chunk = stream.read(length - len(chunks))
        except InterruptedError:
            continue
        except (AttributeError, OSError, TypeError, ValueError):
            raise ProtocolIOError(_IO_MESSAGE) from None
        if type(chunk) is not bytes:
            raise ProtocolIOError(_IO_MESSAGE)
        if not chunk:
            raise ProtocolTruncatedError(_TRUNCATED_MESSAGE)
        if len(chunk) > length - len(chunks):
            raise ProtocolIOError(_IO_MESSAGE)
        chunks.extend(chunk)
    return bytes(chunks)


def _write_all(stream: BinaryIO, data: bytes) -> None:
    """Write every byte while rejecting stalled or invalid stream results."""

    view = memoryview(data)
    offset = 0
    while offset < len(view):
        try:
            written = stream.write(view[offset:])
        except InterruptedError:
            continue
        except (AttributeError, OSError, TypeError, ValueError):
            raise ProtocolIOError(_IO_MESSAGE) from None
        if (
            type(written) is not int
            or written <= 0
            or written > len(view) - offset
        ):
            raise ProtocolIOError(_IO_MESSAGE)
        offset += written


def read_frame(stream: BinaryIO) -> ProtocolFrame:
    """Read one frame while leaving following bytes untouched on success.

    Any failure may occur after part of a frame was consumed, so a caller must
    poison and discard that connection instead of attempting to resynchronize.
    """

    header = _read_exact(stream, PROTOCOL_HEADER_SIZE)
    try:
        (
            magic,
            version,
            kind_value,
            flags,
            metadata_length,
            payload_length,
            request_id,
        ) = _HEADER.unpack(header)
        if magic != PROTOCOL_MAGIC or version != PROTOCOL_VERSION or flags != 0:
            raise ProtocolValidationError(_INVALID_MESSAGE)
        try:
            kind = FrameKind(kind_value)
        except ValueError:
            raise ProtocolValidationError(_INVALID_MESSAGE) from None
        _validate_request_id(kind, request_id)
        _validate_lengths(kind, metadata_length, payload_length)
    except ProtocolError:
        raise
    except (struct.error, TypeError, ValueError):
        raise ProtocolValidationError(_INVALID_MESSAGE) from None

    metadata_bytes = _read_exact(stream, metadata_length)
    metadata = _decode_metadata(metadata_bytes)
    payload = _read_exact(stream, payload_length) if payload_length else b""
    return ProtocolFrame(
        kind=kind,
        request_id=request_id,
        metadata=metadata,
        payload=payload,
        flags=flags,
    )


def write_frame(stream: BinaryIO, frame: ProtocolFrame) -> None:
    """Write one validated frame with partial-write-safe stream operations.

    This primitive neither flushes nor serializes concurrent writers. Protocol
    owners must use one writer lock/thread and an unbuffered stream or explicit
    flush so two frames can never interleave or remain invisibly buffered.
    """

    if type(frame) is not ProtocolFrame:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    # Revalidate the immutable fields so a future internal construction path
    # cannot bypass the wire limits before the fixed header is emitted.
    if type(frame.kind) is not FrameKind or type(frame.flags) is not int:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    if frame.flags != 0 or type(frame.payload) is not bytes:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    if type(frame._metadata_bytes) is not bytes:
        raise ProtocolValidationError(_INVALID_MESSAGE)
    _validate_request_id(frame.kind, frame.request_id)
    _validate_lengths(frame.kind, frame.metadata_length, frame.payload_length)
    _decode_metadata(frame._metadata_bytes)
    try:
        header = _HEADER.pack(
            PROTOCOL_MAGIC,
            PROTOCOL_VERSION,
            int(frame.kind),
            frame.flags,
            frame.metadata_length,
            frame.payload_length,
            frame.request_id,
        )
    except (struct.error, TypeError, ValueError):
        raise ProtocolValidationError(_INVALID_MESSAGE) from None
    _write_all(stream, header)
    _write_all(stream, frame._metadata_bytes)
    if frame.payload:
        _write_all(stream, frame.payload)


__all__ = [
    "FrameKind",
    "MANAGED_RUNTIME_IMPORT_RELATIVE_ENTRIES",
    "MANAGED_RUNTIME_MANIFEST_RELATIVE_FILES",
    "PROTOCOL_HEADER_SIZE",
    "PROTOCOL_MAGIC",
    "PROTOCOL_MAX_JSON_DEPTH",
    "PROTOCOL_MAX_JSON_INTEGER",
    "PROTOCOL_MAX_METADATA_BYTES",
    "PROTOCOL_MAX_PAYLOAD_BYTES",
    "PROTOCOL_MAX_REQUEST_ID",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "ProtocolFrame",
    "ProtocolIOError",
    "ProtocolTruncatedError",
    "ProtocolValidationError",
    "read_frame",
    "write_frame",
]
