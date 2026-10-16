"""Frame canonical PNG previews on a bounded private desktop byte stream.

The desktop JSON protocol carries only :class:`ImageFrameMetadata`.  Image
bytes travel in a separate binary stream using a fixed header, so a future
Electron fd4 owner can reject oversized input before allocation and correlate
the payload with an opaque token.  This module deliberately owns no descriptor:
the process supervisor must create, inherit, drain, and close the stream.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import os
import re
import secrets
import stat
import struct
from threading import Condition, Lock, get_ident
from typing import BinaryIO, Final

from image_generation.png import MAX_GENERATED_PNG_BYTES, PNG_SIGNATURE
from image_generation.profiles import MAX_IMAGE_EDGE_PIXELS, MAX_IMAGE_PIXELS


IMAGE_CHANNEL_MAGIC: Final = b"ELYSIMG1"
IMAGE_CHANNEL_VERSION: Final = 1
IMAGE_CHANNEL_HEADER_BYTES: Final = 88
IMAGE_CHANNEL_MAX_PNG_BYTES: Final = MAX_GENERATED_PNG_BYTES
IMAGE_CHANNEL_MAX_EDGE_PIXELS: Final = MAX_IMAGE_EDGE_PIXELS
IMAGE_CHANNEL_MEDIA_TYPE: Final = "image/png"
IMAGE_CHANNEL_FD: Final = 4

_HEADER = struct.Struct("<8sB3x32sIII32s")
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_PREFIX_BYTES: Final = 24
_MAX_TOKEN_COUNTER: Final = (1 << 64) - 1
_INVALID_FRAME_MESSAGE: Final = "Desktop image frame is invalid."
_STATE_MESSAGE: Final = "Desktop image channel is not available."
_IO_MESSAGE: Final = "Desktop image channel I/O failed."
_TRUNCATED_MESSAGE: Final = "Desktop image channel ended with a truncated frame."
_FRAME_SEAL = object()


class ImageChannelError(Exception):
    """Base class for sanitized image-channel failures."""


class ImageChannelValidationError(ImageChannelError):
    """Report malformed metadata, PNG bytes, or binary framing."""


class ImageChannelStateError(ImageChannelError):
    """Report use after close, poison, or an invalid frame transition."""


class ImageChannelIOError(ImageChannelError):
    """Report a sanitized failure from the caller-owned binary stream."""


@dataclass(frozen=True, slots=True)
class ImageFrameMetadata:
    """Describe one PNG without exposing a path or mutable byte buffer."""

    preview_token: str
    byte_length: int
    width: int
    height: int
    sha256: str
    media_type: str = IMAGE_CHANNEL_MEDIA_TYPE

    def __post_init__(self) -> None:
        """Reject values that cannot be represented by the fixed frame header."""

        if (
            type(self.preview_token) is not str
            or _TOKEN_PATTERN.fullmatch(self.preview_token) is None
            or self.preview_token == "0" * 64
            or type(self.byte_length) is not int
            or not 1 <= self.byte_length <= IMAGE_CHANNEL_MAX_PNG_BYTES
            or type(self.width) is not int
            or type(self.height) is not int
            or not 1 <= self.width <= IMAGE_CHANNEL_MAX_EDGE_PIXELS
            or not 1 <= self.height <= IMAGE_CHANNEL_MAX_EDGE_PIXELS
            or self.width * self.height > MAX_IMAGE_PIXELS
            or type(self.sha256) is not str
            or _DIGEST_PATTERN.fullmatch(self.sha256) is None
            or type(self.media_type) is not str
            or self.media_type != IMAGE_CHANNEL_MEDIA_TYPE
        ):
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)

    def as_dict(self) -> dict[str, object]:
        """Return renderer-safe camelCase fields for an NDJSON event."""

        return {
            "previewToken": self.preview_token,
            "byteLength": self.byte_length,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "mediaType": self.media_type,
        }


class PreparedImageFrame:
    """Hold one immutable writer-owned header and canonical PNG payload."""

    _header: bytes
    _metadata: ImageFrameMetadata
    _owner: object
    _payload: bytes

    __slots__ = ("_header", "_metadata", "_owner", "_payload")

    def __init__(
        self,
        *,
        header: bytes,
        metadata: ImageFrameMetadata,
        owner: object,
        payload: bytes,
        _seal: object,
    ) -> None:
        """Create a prepared frame only through :class:`ImageChannelWriter`."""

        if _seal is not _FRAME_SEAL:
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        object.__setattr__(self, "_header", header)
        object.__setattr__(self, "_metadata", metadata)
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_payload", payload)

    @property
    def metadata(self) -> ImageFrameMetadata:
        """Return immutable path-free metadata for the pending binary frame."""

        return self._metadata

    def __repr__(self) -> str:
        return (
            "PreparedImageFrame("
            f"byte_length={self._metadata.byte_length}, "
            f"width={self._metadata.width}, height={self._metadata.height})"
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("PreparedImageFrame values are immutable.")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("PreparedImageFrame values are immutable.")


class ReceivedImageFrame:
    """Expose one verified PNG and immutable correlation metadata."""

    _metadata: ImageFrameMetadata
    _png_bytes: bytes

    __slots__ = ("_metadata", "_png_bytes")

    def __init__(
        self,
        *,
        metadata: ImageFrameMetadata,
        png_bytes: bytes,
        _seal: object,
    ) -> None:
        """Create a received frame only after incremental verification."""

        if _seal is not _FRAME_SEAL:
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        object.__setattr__(self, "_metadata", metadata)
        object.__setattr__(self, "_png_bytes", png_bytes)

    @property
    def metadata(self) -> ImageFrameMetadata:
        """Return the verified token, dimensions, length, and digest."""

        return self._metadata

    @property
    def png_bytes(self) -> bytes:
        """Return the immutable PNG payload after hash verification."""

        return self._png_bytes

    def __repr__(self) -> str:
        return (
            "ReceivedImageFrame("
            f"byte_length={self._metadata.byte_length}, "
            f"width={self._metadata.width}, height={self._metadata.height})"
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("ReceivedImageFrame values are immutable.")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("ReceivedImageFrame values are immutable.")


@dataclass(frozen=True, slots=True)
class ImageChannelReadResult:
    """Report consumed input and at most one completed frame.

    Stopping at the first frame boundary lets a pipe owner retain any suffix
    and apply backpressure before offering a coalesced second frame.
    """

    consumed_bytes: int
    frame: ReceivedImageFrame | None


@dataclass(frozen=True, slots=True)
class _FrameSnapshot:
    header: bytes
    payload: bytes
    preview_token: str
    width: int
    height: int
    sha256: str


def _validate_png_envelope(payload: bytes, width: int, height: int) -> None:
    """Bind the frame dimensions to the canonical PNG IHDR.

    Full PNG canonicalization belongs to ``image_generation.png``.  Rechecking
    the signature and first IHDR here prevents accidental media confusion at
    this trust boundary without duplicating the image decoder.
    """

    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= IMAGE_CHANNEL_MAX_PNG_BYTES
        or len(payload) < 33
        or not payload.startswith(PNG_SIGNATURE)
        or payload[8:12] != b"\x00\x00\x00\r"
        or payload[12:16] != b"IHDR"
        or int.from_bytes(payload[16:20], "big") != width
        or int.from_bytes(payload[20:24], "big") != height
    ):
        raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)


def _safe_write(stream: BinaryIO, payload: bytes) -> None:
    """Complete one region across proven partial writes with sanitized errors.

    Only a positive integer return proves how much of the byte stream advanced.
    Retrying an exception, including ``InterruptedError``, would be ambiguous
    for a caller-supplied ``BinaryIO`` that consumed bytes before raising and
    could therefore duplicate a header or payload prefix.
    """

    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        failed = False
        try:
            written = stream.write(view[offset:])
        except BaseException:
            failed = True
            written = None
        if failed:
            raise ImageChannelIOError(_IO_MESSAGE)
        if (
            type(written) is not int
            or written <= 0
            or written > len(view) - offset
        ):
            raise ImageChannelIOError(_IO_MESSAGE)
        offset += written


def _safe_close(stream: BinaryIO) -> bool:
    """Close a stream and return success without retaining native diagnostics."""

    try:
        stream.close()
    except BaseException:
        return False
    return True


def _is_image_pipe_descriptor() -> bool:
    """Verify the fixed descriptor before transferring its ownership to FileIO."""

    if os.name == "nt":
        import ctypes

        get_handle = getattr(importlib.import_module("msvcrt"), "get_osfhandle")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        file_type = kernel32.GetFileType
        file_type.argtypes = [ctypes.c_void_p]
        file_type.restype = ctypes.c_uint32
        return file_type(ctypes.c_void_p(get_handle(IMAGE_CHANNEL_FD))) == 3
    mode = os.fstat(IMAGE_CHANNEL_FD).st_mode
    return stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)


class ImageChannelWriter:
    """Prepare and serialize one bounded PNG frame at a time.

    Only one frame may exist between metadata publication and binary writing.
    Any partial write poisons the channel because retrying cannot resynchronize
    a byte stream.  The caller supplies the stream, so this helper remains
    independent of the future inherited descriptor number.
    """

    def __init__(self, stream: BinaryIO) -> None:
        """Take ownership of a writable binary stream and its eventual close."""

        valid = True
        try:
            valid = all(
                callable(getattr(stream, name, None))
                for name in ("write", "flush", "close")
            )
        except BaseException:
            valid = False
        if not valid:
            raise TypeError("stream must be a writable binary stream.")
        failed = False
        try:
            prefix = secrets.token_bytes(_TOKEN_PREFIX_BYTES)
        except BaseException:
            failed = True
            prefix = b""
        if failed or type(prefix) is not bytes or len(prefix) != _TOKEN_PREFIX_BYTES:
            raise ImageChannelIOError(_IO_MESSAGE)
        self._stream = stream
        self._token_prefix = prefix
        self._next_counter = 0
        self._owner = object()
        self._pending: PreparedImageFrame | None = None
        self._snapshot: _FrameSnapshot | None = None
        self._writing: PreparedImageFrame | None = None
        self._closed = False
        self._poisoned = False
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._write_lock = Lock()
        self._writing_thread_id: int | None = None
        self._stream_released = False
        self._close_in_progress = False
        self._close_owner_thread_id: int | None = None
        self._close_attempt = 0
        self._close_results: dict[int, bool] = {}
        self._close_waiters: dict[int, int] = {}

    @classmethod
    def from_inherited_fd4(cls) -> "ImageChannelWriter":
        """Own only the fixed private image pipe and prevent descendant inheritance.

        Electron creates fd4 separately from speech fd3 and NDJSON stdout.
        Reject ordinary files or console descriptors before any PNG write so a
        console launch cannot accidentally send image pixels to another file.
        """

        stream: BinaryIO | None = None
        try:
            os.set_inheritable(IMAGE_CHANNEL_FD, False)
            if not _is_image_pipe_descriptor():
                raise ValueError
            stream = os.fdopen(IMAGE_CHANNEL_FD, "wb", buffering=0, closefd=True)
            return cls(stream)
        except Exception:
            if stream is not None:
                _safe_close(stream)
            else:
                try:
                    os.close(IMAGE_CHANNEL_FD)
                except OSError:
                    pass
        raise ImageChannelIOError(_IO_MESSAGE)

    def prepare_png(
        self,
        png_bytes: bytes,
        *,
        width: int,
        height: int,
    ) -> PreparedImageFrame:
        """Validate and reserve one canonical PNG for later ordered writing."""

        if (
            type(width) is not int
            or type(height) is not int
            or not 1 <= width <= IMAGE_CHANNEL_MAX_EDGE_PIXELS
            or not 1 <= height <= IMAGE_CHANNEL_MAX_EDGE_PIXELS
            or width * height > MAX_IMAGE_PIXELS
        ):
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        _validate_png_envelope(png_bytes, width, height)
        digest = hashlib.sha256(png_bytes).digest()
        with self._lock:
            if self._closed or self._poisoned:
                raise ImageChannelStateError(_STATE_MESSAGE)
            if self._pending is not None or self._writing is not None:
                raise ImageChannelStateError(
                    "Desktop image channel already has a pending frame."
                )
            if self._next_counter > _MAX_TOKEN_COUNTER:
                self._poisoned = True
                raise ImageChannelStateError(_STATE_MESSAGE)
            token = self._token_prefix + self._next_counter.to_bytes(8, "big")
            self._next_counter += 1
            if token == bytes(32):
                self._poisoned = True
                raise ImageChannelIOError(_IO_MESSAGE)
            metadata = ImageFrameMetadata(
                preview_token=token.hex(),
                byte_length=len(png_bytes),
                width=width,
                height=height,
                sha256=digest.hex(),
            )
            header = _HEADER.pack(
                IMAGE_CHANNEL_MAGIC,
                IMAGE_CHANNEL_VERSION,
                token,
                len(png_bytes),
                width,
                height,
                digest,
            )
            frame = PreparedImageFrame(
                header=header,
                metadata=metadata,
                owner=self._owner,
                payload=png_bytes,
                _seal=_FRAME_SEAL,
            )
            self._pending = frame
            self._snapshot = _FrameSnapshot(
                header,
                png_bytes,
                metadata.preview_token,
                width,
                height,
                metadata.sha256,
            )
            return frame

    def write_frame(self, frame: PreparedImageFrame) -> None:
        """Write the exact pending frame once and poison on partial failure."""

        if type(frame) is not PreparedImageFrame:
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        with self._write_lock:
            invalid_frame = False
            with self._lock:
                if (
                    self._closed
                    or self._poisoned
                    or self._pending is not frame
                    or self._snapshot is None
                ):
                    raise ImageChannelStateError(_STATE_MESSAGE)
                snapshot = self._snapshot
                if not self._frame_matches_snapshot(frame, snapshot):
                    self._poisoned = True
                    self._closed = True
                    self._pending = None
                    self._snapshot = None
                    invalid_frame = True
                else:
                    self._pending = None
                    self._snapshot = None
                    self._writing = frame
                    self._writing_thread_id = get_ident()
            if invalid_frame:
                self._close_stream_shared()
                raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
            failure: ImageChannelError | None = None
            try:
                _safe_write(self._stream, snapshot.header)
                _safe_write(self._stream, snapshot.payload)
                try:
                    self._stream.flush()
                except BaseException:
                    raise ImageChannelIOError(_IO_MESSAGE)
            except BaseException:
                # Reconstruct instead of re-raising even a channel exception:
                # a hostile BinaryIO can raise our public type while attaching
                # a private cause, context, traceback, or custom argument.
                failure = ImageChannelIOError(_IO_MESSAGE)
            if failure is not None:
                with self._condition:
                    self._poisoned = True
                    self._closed = True
                    self._writing = None
                    self._writing_thread_id = None
                    self._condition.notify_all()
                self._close_stream_shared()
                raise failure
            with self._condition:
                self._writing = None
                self._writing_thread_id = None
                self._condition.notify_all()

    def _frame_matches_snapshot(
        self,
        frame: PreparedImageFrame,
        snapshot: _FrameSnapshot,
    ) -> bool:
        """Revalidate reflected state against writer-private immutable values."""

        try:
            header = frame._header
            metadata = frame._metadata
            owner = frame._owner
            payload = frame._payload
            if (
                type(header) is not bytes
                or type(metadata) is not ImageFrameMetadata
                or owner is not self._owner
                or type(payload) is not bytes
                or type(snapshot.header) is not bytes
                or type(snapshot.payload) is not bytes
                or type(snapshot.preview_token) is not str
                or type(snapshot.width) is not int
                or type(snapshot.height) is not int
                or type(snapshot.sha256) is not str
                or type(metadata.preview_token) is not str
                or type(metadata.byte_length) is not int
                or type(metadata.width) is not int
                or type(metadata.height) is not int
                or type(metadata.sha256) is not str
                or type(metadata.media_type) is not str
                or header != snapshot.header
                or payload is not snapshot.payload
                or metadata.preview_token != snapshot.preview_token
                or metadata.byte_length != len(snapshot.payload)
                or metadata.width != snapshot.width
                or metadata.height != snapshot.height
                or metadata.sha256 != snapshot.sha256
                or metadata.media_type != IMAGE_CHANNEL_MEDIA_TYPE
            ):
                return False
            _validate_png_envelope(
                snapshot.payload,
                snapshot.width,
                snapshot.height,
            )
            digest = hashlib.sha256(snapshot.payload).digest()
            expected = _HEADER.pack(
                IMAGE_CHANNEL_MAGIC,
                IMAGE_CHANNEL_VERSION,
                bytes.fromhex(snapshot.preview_token),
                len(snapshot.payload),
                snapshot.width,
                snapshot.height,
                digest,
            )
            return snapshot.sha256 == digest.hex() and snapshot.header == expected
        except BaseException:
            return False

    def discard_frame(self, frame: PreparedImageFrame) -> None:
        """Release the exact pending frame before any bytes are written."""

        if type(frame) is not PreparedImageFrame:
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        with self._lock:
            if (
                self._closed
                or self._poisoned
                or self._pending is not frame
            ):
                raise ImageChannelStateError(_STATE_MESSAGE)
            self._pending = None
            self._snapshot = None

    def close(self) -> None:
        """Discard pending work and prove that the owned stream was released.

        A close racing an active Windows write waits for that producer to leave
        the native operation before closing the stream.  Concurrent callers
        join one exact close attempt and observe its shared outcome; a later
        call retries only when an earlier attempt could not prove release.
        """

        if not self._close_stream_shared(request_close=True):
            raise ImageChannelIOError(_IO_MESSAGE)

    def _close_stream_shared(self, *, request_close: bool = False) -> bool:
        """Run or join one sanitized stream-close attempt and share its result.

        Public close atomically stops admission and claims or joins an attempt.
        Failure cleanup uses the same machinery after it has already poisoned
        the channel, preventing a fast close from splitting concurrent callers
        across a failed attempt and its later retry.
        """

        owns_attempt = False
        joined_attempt = False
        joined_succeeded = False
        with self._condition:
            caller = get_ident()
            if request_close:
                if (
                    self._writing is not None
                    and self._writing_thread_id == caller
                ) or (
                    self._close_in_progress
                    and self._close_owner_thread_id == caller
                ):
                    raise ImageChannelStateError(
                        "Desktop image channel cannot close reentrantly."
                    )
                self._closed = True
                self._pending = None
                self._snapshot = None
            if self._stream_released:
                return True
            if self._close_in_progress:
                observed_attempt = self._close_attempt
                self._close_waiters[observed_attempt] = (
                    self._close_waiters.get(observed_attempt, 0) + 1
                )
                try:
                    while observed_attempt not in self._close_results:
                        self._condition.wait()
                    joined_succeeded = self._close_results[observed_attempt]
                    joined_attempt = True
                finally:
                    remaining = self._close_waiters[observed_attempt] - 1
                    if remaining:
                        self._close_waiters[observed_attempt] = remaining
                    else:
                        del self._close_waiters[observed_attempt]
                        self._close_results.pop(observed_attempt, None)
            else:
                for attempt in tuple(self._close_results):
                    if self._close_waiters.get(attempt, 0) == 0:
                        del self._close_results[attempt]
                self._close_attempt += 1
                observed_attempt = self._close_attempt
                self._close_in_progress = True
                self._close_owner_thread_id = caller
                owns_attempt = True
                # A blocked Windows FileIO write cannot be safely closed from
                # another thread.  Keeping the attempt claimed makes every
                # concurrent closer join the same eventual outcome.
                while self._writing is not None:
                    try:
                        self._condition.wait()
                    except BaseException:
                        continue

        if joined_attempt:
            return joined_succeeded
        if not owns_attempt:
            return self._stream_released

        succeeded = _safe_close(self._stream)
        with self._condition:
            if succeeded:
                self._stream_released = True
            self._close_results[observed_attempt] = succeeded
            self._close_in_progress = False
            self._close_owner_thread_id = None
            self._condition.notify_all()
        return succeeded


class ImageChannelReader:
    """Incrementally verify bounded frames while buffering at most one image."""

    def __init__(self) -> None:
        """Create an empty reader ready for the first fixed header."""

        self._header = bytearray()
        self._payload = bytearray()
        self._metadata: ImageFrameMetadata | None = None
        self._closed = False
        self._poisoned = False

    def feed(self, data: bytes) -> ImageChannelReadResult:
        """Consume bytes through at most one frame and leave any suffix to caller."""

        if self._closed or self._poisoned:
            raise ImageChannelStateError(_STATE_MESSAGE)
        if type(data) is not bytes:
            self._poisoned = True
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        offset = 0
        if len(self._header) < IMAGE_CHANNEL_HEADER_BYTES:
            take = min(IMAGE_CHANNEL_HEADER_BYTES - len(self._header), len(data))
            self._header.extend(data[:take])
            offset += take
            if len(self._header) < IMAGE_CHANNEL_HEADER_BYTES:
                return ImageChannelReadResult(offset, None)
            self._metadata = self._decode_header(bytes(self._header))

        metadata = self._metadata
        if metadata is None:
            self._poisoned = True
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        remaining = metadata.byte_length - len(self._payload)
        take = min(remaining, len(data) - offset)
        self._payload.extend(data[offset:offset + take])
        offset += take
        if len(self._payload) < metadata.byte_length:
            return ImageChannelReadResult(offset, None)

        payload = bytes(self._payload)
        if hashlib.sha256(payload).hexdigest() != metadata.sha256:
            self._poisoned = True
            raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)
        try:
            _validate_png_envelope(payload, metadata.width, metadata.height)
        except ImageChannelValidationError:
            self._poisoned = True
            raise
        frame = ReceivedImageFrame(
            metadata=metadata,
            png_bytes=payload,
            _seal=_FRAME_SEAL,
        )
        self._header.clear()
        self._payload.clear()
        self._metadata = None
        return ImageChannelReadResult(offset, frame)

    def finish(self) -> None:
        """Mark clean EOF or reject EOF in the middle of a header or payload."""

        if self._closed:
            return
        self._closed = True
        if self._header or self._payload or self._metadata is not None:
            self._poisoned = True
            raise ImageChannelValidationError(_TRUNCATED_MESSAGE)

    def _decode_header(self, header: bytes) -> ImageFrameMetadata:
        """Validate fixed fields before allocating or accepting payload bytes."""

        try:
            (
                magic,
                version,
                token,
                length,
                width,
                height,
                digest,
            ) = _HEADER.unpack(header)
            if magic != IMAGE_CHANNEL_MAGIC or version != IMAGE_CHANNEL_VERSION:
                raise ValueError
            return ImageFrameMetadata(
                preview_token=token.hex(),
                byte_length=length,
                width=width,
                height=height,
                sha256=digest.hex(),
            )
        except (ValueError, struct.error, ImageChannelValidationError):
            self._poisoned = True
        raise ImageChannelValidationError(_INVALID_FRAME_MESSAGE)


__all__ = [
    "IMAGE_CHANNEL_HEADER_BYTES",
    "IMAGE_CHANNEL_FD",
    "IMAGE_CHANNEL_MAGIC",
    "IMAGE_CHANNEL_MAX_EDGE_PIXELS",
    "IMAGE_CHANNEL_MAX_PNG_BYTES",
    "IMAGE_CHANNEL_MEDIA_TYPE",
    "IMAGE_CHANNEL_VERSION",
    "ImageChannelError",
    "ImageChannelIOError",
    "ImageChannelReadResult",
    "ImageChannelReader",
    "ImageChannelStateError",
    "ImageChannelValidationError",
    "ImageChannelWriter",
    "ImageFrameMetadata",
    "PreparedImageFrame",
    "ReceivedImageFrame",
]
