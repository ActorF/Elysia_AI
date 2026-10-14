"""Verify bounded PNG framing for a future private desktop image pipe."""

from __future__ import annotations

from io import BytesIO
import struct
from threading import Event, Thread
from typing import Final

import pytest

from desktop_protocol.image_channel import (
    IMAGE_CHANNEL_HEADER_BYTES,
    IMAGE_CHANNEL_MAGIC,
    IMAGE_CHANNEL_MAX_EDGE_PIXELS,
    IMAGE_CHANNEL_MAX_PNG_BYTES,
    IMAGE_CHANNEL_VERSION,
    ImageChannelIOError,
    ImageChannelReader,
    ImageChannelStateError,
    ImageChannelValidationError,
    ImageChannelWriter,
    ImageFrameMetadata,
)


_HEADER: Final = struct.Struct("<8sB3x32sIII32s")


def _png(size: int = 64, *, width: int = 8, height: int = 8) -> bytes:
    """Build a framing-level PNG envelope with an IHDR and requested size."""

    prefix = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    assert size >= len(prefix)
    return prefix + bytes(size - len(prefix))


def _encoded_frame(
    payload: bytes | None = None,
    *,
    width: int = 8,
    height: int = 8,
) -> bytes:
    """Use the production writer to produce one deterministic-layout frame."""

    stream = BytesIO()
    writer = ImageChannelWriter(stream)
    frame = writer.prepare_png(
        payload if payload is not None else _png(width=width, height=height),
        width=width,
        height=height,
    )
    writer.write_frame(frame)
    return stream.getvalue()


class _PartialStream:
    def __init__(self, maximum_write: int = 5) -> None:
        self.maximum_write = maximum_write
        self.buffer = bytearray()
        self.flushes = 0
        self.closed = False

    def write(self, value: object) -> int:
        """Accept a short prefix to exercise complete-write behavior."""

        payload = bytes(value)  # type: ignore[call-overload]
        count = min(self.maximum_write, len(payload))
        self.buffer.extend(payload[:count])
        return count

    def flush(self) -> None:
        """Record visibility of a completed frame."""

        self.flushes += 1

    def close(self) -> None:
        """Record ownership-driven stream closure."""

        self.closed = True


class _FailingStream(_PartialStream):
    def write(self, _value: object) -> int:
        """Raise a private path that must not escape the channel boundary."""

        raise OSError(r"D:\private\generated.png")


class _BaseExceptionWriteStream(_PartialStream):
    def write(self, _value: object) -> int:
        """Raise a non-Exception diagnostic that must still be detached."""

        raise KeyboardInterrupt(r"D:\private\write.png")


class _ConsumingInterruptedStream(_PartialStream):
    def __init__(self) -> None:
        super().__init__()
        self.write_calls = 0

    def write(self, value: object) -> int:
        """Consume a prefix before interruption to make retrying ambiguous."""

        self.write_calls += 1
        payload = bytes(value)  # type: ignore[call-overload]
        self.buffer.extend(payload[:3])
        raise InterruptedError(r"D:\private\interrupted.png")


class _BaseExceptionFlushStream(_PartialStream):
    def flush(self) -> None:
        """Raise a non-Exception diagnostic after the complete frame write."""

        raise SystemExit(r"D:\private\flush.png")


class _ControlledCloseStream(_PartialStream):
    def __init__(self) -> None:
        super().__init__()
        self.close_entered = Event()
        self.close_released = Event()
        self.close_calls = 0

    def close(self) -> None:
        """Block and fail the first close so concurrent outcome sharing is visible."""

        self.close_calls += 1
        self.close_entered.set()
        assert self.close_released.wait(timeout=2.0)
        if self.close_calls == 1:
            raise KeyboardInterrupt(r"D:\private\close.png")
        self.closed = True


class _BlockingStream(_PartialStream):
    def __init__(self) -> None:
        super().__init__(maximum_write=1_000_000)
        self.entered = Event()
        self.released = Event()

    def write(self, value: object) -> int:
        """Hold the first write so lifecycle behavior can be observed."""

        self.entered.set()
        assert self.released.wait(timeout=2.0)
        return super().write(value)


class _BlockingWriteFailingCloseStream(_BlockingStream):
    def __init__(self) -> None:
        super().__init__()
        self.close_entered = Event()
        self.close_released = Event()
        self.close_calls = 0

    def close(self) -> None:
        """Fail the first delayed close after its active write has drained."""

        self.close_calls += 1
        self.close_entered.set()
        assert self.close_released.wait(timeout=2.0)
        if self.close_calls == 1:
            raise KeyboardInterrupt(r"D:\private\deferred-close.png")
        self.closed = True


def test_writer_emits_fixed_header_and_path_free_metadata() -> None:
    """Encode token, dimensions, length, and digest in the fixed layout."""

    stream = _PartialStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    payload = _png(width=16, height=12)
    frame = writer.prepare_png(payload, width=16, height=12)

    assert frame.metadata.as_dict() == {
        "previewToken": frame.metadata.preview_token,
        "byteLength": len(payload),
        "width": 16,
        "height": 12,
        "sha256": frame.metadata.sha256,
        "mediaType": "image/png",
    }
    assert len(frame.metadata.preview_token) == 64
    assert frame.metadata.preview_token not in repr(frame)
    assert frame.metadata.sha256 not in repr(frame)

    writer.write_frame(frame)
    encoded = bytes(stream.buffer)
    fields = _HEADER.unpack(encoded[:IMAGE_CHANNEL_HEADER_BYTES])
    assert fields[:2] == (IMAGE_CHANNEL_MAGIC, IMAGE_CHANNEL_VERSION)
    assert fields[2].hex() == frame.metadata.preview_token
    assert fields[3:6] == (len(payload), 16, 12)
    assert fields[6].hex() == frame.metadata.sha256
    assert encoded[IMAGE_CHANNEL_HEADER_BYTES:] == payload
    assert stream.flushes == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"preview_token": "A" * 64},
        {"preview_token": "a" * 63},
        {"preview_token": "0" * 64},
        {"byte_length": 0},
        {"byte_length": IMAGE_CHANNEL_MAX_PNG_BYTES + 1},
        {"width": 0},
        {"height": IMAGE_CHANNEL_MAX_EDGE_PIXELS + 1},
        {"sha256": "G" * 64},
        {"media_type": "image/jpeg"},
    ],
)
def test_metadata_rejects_noncanonical_external_fields(
    changes: dict[str, object],
) -> None:
    """Require lowercase fixed-width identities and bounded numeric fields."""

    values: dict[str, object] = {
        "preview_token": "a" * 64,
        "byte_length": 64,
        "width": 8,
        "height": 8,
        "sha256": "b" * 64,
        "media_type": "image/png",
    }
    values.update(changes)
    with pytest.raises(ImageChannelValidationError):
        ImageFrameMetadata(**values)  # type: ignore[arg-type]


def test_writer_accepts_exact_byte_cap_and_rejects_cap_plus_one() -> None:
    """Keep the image-layer 32 MiB cap identical at the desktop boundary."""

    writer = ImageChannelWriter(BytesIO())
    exact = writer.prepare_png(
        _png(IMAGE_CHANNEL_MAX_PNG_BYTES, width=1, height=1),
        width=1,
        height=1,
    )
    writer.discard_frame(exact)
    with pytest.raises(ImageChannelValidationError):
        writer.prepare_png(
            _png(IMAGE_CHANNEL_MAX_PNG_BYTES + 1, width=1, height=1),
            width=1,
            height=1,
        )


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (True, 8),
        (8, False),
        (0, 8),
        (8, 0),
        (IMAGE_CHANNEL_MAX_EDGE_PIXELS + 1, 8),
        (8, IMAGE_CHANNEL_MAX_EDGE_PIXELS + 1),
    ],
)
def test_writer_rejects_invalid_or_mismatched_dimensions(
    width: object,
    height: object,
) -> None:
    """Reject Boolean, zero, oversized, and IHDR-mismatched dimensions."""

    writer = ImageChannelWriter(BytesIO())
    with pytest.raises(ImageChannelValidationError):
        writer.prepare_png(_png(), width=width, height=height)  # type: ignore[arg-type]


def test_writer_allows_only_one_outstanding_frame() -> None:
    """Bound the metadata-to-binary gap to one image per writer."""

    writer = ImageChannelWriter(BytesIO())
    first = writer.prepare_png(_png(), width=8, height=8)
    with pytest.raises(ImageChannelStateError):
        writer.prepare_png(_png(), width=8, height=8)
    writer.discard_frame(first)
    second = writer.prepare_png(_png(), width=8, height=8)
    writer.write_frame(second)
    with pytest.raises(ImageChannelStateError):
        writer.write_frame(second)


def test_active_write_blocks_new_frames_and_owns_delayed_close() -> None:
    """Wait for a blocked Windows write before closing its owned stream."""

    stream = _BlockingStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    failures: list[BaseException] = []

    def _write() -> None:
        try:
            writer.write_frame(frame)
        except BaseException as error:
            failures.append(error)

    close_failures: list[BaseException] = []

    def _close() -> None:
        try:
            writer.close()
        except BaseException as error:
            close_failures.append(error)

    thread = Thread(target=_write)
    thread.start()
    assert stream.entered.wait(timeout=1.0)
    with pytest.raises(ImageChannelStateError):
        writer.prepare_png(_png(), width=8, height=8)
    close_thread = Thread(target=_close)
    close_thread.start()
    close_thread.join(timeout=0.05)
    assert close_thread.is_alive()
    assert not stream.closed
    stream.released.set()
    thread.join(timeout=1.0)
    close_thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not close_thread.is_alive()
    assert failures == []
    assert close_failures == []
    assert stream.closed


def test_concurrent_close_shares_failure_then_retries_unreleased_stream() -> None:
    """Give concurrent callers one failure and retry only a later close call."""

    stream = _ControlledCloseStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    failures: list[BaseException] = []

    def _close() -> None:
        try:
            writer.close()
        except BaseException as error:
            failures.append(error)

    owner = Thread(target=_close)
    owner.start()
    assert stream.close_entered.wait(timeout=1.0)
    joiner = Thread(target=_close)
    joiner.start()
    # The owner remains inside the controlled stream close, giving the second
    # caller time to register against the same attempt rather than retry it.
    joiner.join(timeout=0.05)
    assert joiner.is_alive()
    stream.close_released.set()
    owner.join(timeout=1.0)
    joiner.join(timeout=1.0)

    assert not owner.is_alive()
    assert not joiner.is_alive()
    assert stream.close_calls == 1
    assert len(failures) == 2
    for failure in failures:
        assert type(failure) is ImageChannelIOError
        assert str(failure) == "Desktop image channel I/O failed."
        assert failure.__cause__ is None
        assert failure.__context__ is None

    writer.close()
    assert stream.close_calls == 2
    assert stream.closed
    writer.close()
    assert stream.close_calls == 2


def test_deferred_close_shares_failure_after_active_write_drains() -> None:
    """Keep concurrent closers on one attempt claimed during an active write."""

    stream = _BlockingWriteFailingCloseStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    write_failures: list[BaseException] = []
    close_failures: list[BaseException] = []

    def _write() -> None:
        try:
            writer.write_frame(frame)
        except BaseException as error:
            write_failures.append(error)

    def _close() -> None:
        try:
            writer.close()
        except BaseException as error:
            close_failures.append(error)

    producer = Thread(target=_write)
    producer.start()
    assert stream.entered.wait(timeout=1.0)
    first = Thread(target=_close)
    second = Thread(target=_close)
    first.start()
    second.start()
    first.join(timeout=0.05)
    second.join(timeout=0.05)
    assert first.is_alive()
    assert second.is_alive()

    stream.released.set()
    assert stream.close_entered.wait(timeout=1.0)
    first.join(timeout=0.05)
    second.join(timeout=0.05)
    assert first.is_alive()
    assert second.is_alive()
    stream.close_released.set()
    producer.join(timeout=1.0)
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not producer.is_alive()
    assert not first.is_alive()
    assert not second.is_alive()
    assert write_failures == []
    assert stream.close_calls == 1
    assert len(close_failures) == 2
    assert all(type(error) is ImageChannelIOError for error in close_failures)

    writer.close()
    assert stream.close_calls == 2
    assert stream.closed


def test_reader_accepts_every_single_byte_chunk_boundary() -> None:
    """Reassemble a frame without assuming header or payload read sizes."""

    encoded = _encoded_frame()
    reader = ImageChannelReader()
    frame = None
    for value in encoded:
        result = reader.feed(bytes((value,)))
        assert result.consumed_bytes == 1
        if result.frame is not None:
            assert frame is None
            frame = result.frame
    assert frame is not None
    assert frame.png_bytes == encoded[IMAGE_CHANNEL_HEADER_BYTES:]
    assert frame.metadata.byte_length == len(frame.png_bytes)
    reader.finish()


def test_reader_stops_at_first_of_two_coalesced_frames() -> None:
    """Return a consumed prefix so the owner can apply per-frame backpressure."""

    first = _encoded_frame(_png(width=8, height=8), width=8, height=8)
    second = _encoded_frame(_png(width=9, height=7), width=9, height=7)
    coalesced = first + second
    reader = ImageChannelReader()

    one = reader.feed(coalesced)
    assert one.frame is not None
    assert one.consumed_bytes == len(first)
    two = reader.feed(coalesced[one.consumed_bytes:])
    assert two.frame is not None
    assert two.consumed_bytes == len(second)
    assert (one.frame.metadata.width, two.frame.metadata.width) == (8, 9)
    reader.finish()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (0, b"NOTIMAGE"),
        (1, IMAGE_CHANNEL_VERSION + 1),
        (2, bytes(32)),
        (3, 0),
        (3, IMAGE_CHANNEL_MAX_PNG_BYTES + 1),
        (4, 0),
        (4, IMAGE_CHANNEL_MAX_EDGE_PIXELS + 1),
        (5, 0),
        (5, IMAGE_CHANNEL_MAX_EDGE_PIXELS + 1),
    ],
)
def test_reader_rejects_bad_header_fields(field: int, replacement: object) -> None:
    """Fail before payload allocation for malformed fixed-header values."""

    encoded = _encoded_frame()
    fields = list(_HEADER.unpack(encoded[:IMAGE_CHANNEL_HEADER_BYTES]))
    fields[field] = replacement
    malformed = _HEADER.pack(*fields) + encoded[IMAGE_CHANNEL_HEADER_BYTES:]
    reader = ImageChannelReader()
    with pytest.raises(ImageChannelValidationError):
        reader.feed(malformed)
    with pytest.raises(ImageChannelStateError):
        reader.feed(b"")


def test_reader_rejects_bad_hash_and_png_dimensions() -> None:
    """Authenticate payload bytes and bind their IHDR to framed dimensions."""

    encoded = bytearray(_encoded_frame())
    encoded[-1] ^= 1
    with pytest.raises(ImageChannelValidationError):
        ImageChannelReader().feed(bytes(encoded))

    valid = _encoded_frame()
    fields = list(_HEADER.unpack(valid[:IMAGE_CHANNEL_HEADER_BYTES]))
    fields[4] = 9
    malformed = _HEADER.pack(*fields) + valid[IMAGE_CHANNEL_HEADER_BYTES:]
    # Replacing width also leaves the original digest intact, so the payload
    # passes authentication before the independent IHDR binding rejects it.
    with pytest.raises(ImageChannelValidationError):
        ImageChannelReader().feed(malformed)


@pytest.mark.parametrize(
    "cut",
    [1, IMAGE_CHANNEL_HEADER_BYTES - 1, IMAGE_CHANNEL_HEADER_BYTES + 1],
)
def test_reader_rejects_truncated_eof(cut: int) -> None:
    """Distinguish clean frame boundaries from header and payload truncation."""

    reader = ImageChannelReader()
    result = reader.feed(_encoded_frame()[:cut])
    assert result.frame is None
    with pytest.raises(
        ImageChannelValidationError,
        match="ended with a truncated frame",
    ):
        reader.finish()


def test_writer_sanitizes_private_stream_errors_and_poison_state() -> None:
    """Discard native diagnostics and never retry a potentially partial frame."""

    stream = _FailingStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    with pytest.raises(ImageChannelIOError) as captured:
        writer.write_frame(frame)
    assert str(captured.value) == "Desktop image channel I/O failed."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private" not in repr(captured.value)
    assert stream.closed
    with pytest.raises(ImageChannelStateError):
        writer.prepare_png(_png(), width=8, height=8)


@pytest.mark.parametrize(
    "stream",
    [_BaseExceptionWriteStream(), _BaseExceptionFlushStream()],
)
def test_writer_sanitizes_base_exceptions_from_write_and_flush(
    stream: _PartialStream,
) -> None:
    """Detach even non-Exception diagnostics raised by stream collaborators."""

    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    with pytest.raises(ImageChannelIOError) as captured:
        writer.write_frame(frame)
    assert str(captured.value) == "Desktop image channel I/O failed."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private" not in repr(captured.value)
    assert stream.closed


def test_writer_does_not_retry_an_ambiguous_interrupted_write() -> None:
    """Poison instead of duplicating bytes consumed before interruption."""

    stream = _ConsumingInterruptedStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    with pytest.raises(ImageChannelIOError) as captured:
        writer.write_frame(frame)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert stream.write_calls == 1
    assert len(stream.buffer) == 3
    assert stream.closed


def test_reflective_frame_mutation_is_revalidated_before_output() -> None:
    """Compare with writer-private values when reflection bypasses immutability."""

    stream = _PartialStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    object.__setattr__(frame.metadata, "width", 9)
    with pytest.raises(ImageChannelValidationError):
        writer.write_frame(frame)
    assert stream.buffer == b""
    assert stream.closed


def test_reflective_comparison_exception_is_sanitized_and_fail_closed() -> None:
    """Reject malicious reflected fields without invoking their operators."""

    class _ExplosiveComparison:
        def __ne__(self, _other: object) -> bool:
            raise KeyboardInterrupt(r"D:\private\reflected.png")

    stream = _PartialStream()
    writer = ImageChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_png(_png(), width=8, height=8)
    object.__setattr__(frame, "_header", _ExplosiveComparison())
    with pytest.raises(ImageChannelValidationError) as captured:
        writer.write_frame(frame)
    assert str(captured.value) == "Desktop image frame is invalid."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert stream.buffer == b""
    assert stream.closed
    with pytest.raises(ImageChannelStateError):
        writer.prepare_png(_png(), width=8, height=8)


def test_frames_and_read_results_are_immutable() -> None:
    """Prevent consumers from changing verified correlation or payload state."""

    writer = ImageChannelWriter(BytesIO())
    prepared = writer.prepare_png(_png(), width=8, height=8)
    with pytest.raises(AttributeError):
        prepared.metadata = prepared.metadata  # type: ignore[misc]
    with pytest.raises(AttributeError):
        prepared.metadata.width = 9  # type: ignore[misc]

    result = ImageChannelReader().feed(_encoded_frame())
    assert result.frame is not None
    with pytest.raises(AttributeError):
        result.consumed_bytes = 0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.frame.png_bytes = b""  # type: ignore[misc]
