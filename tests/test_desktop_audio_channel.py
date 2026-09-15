"""Verify bounded PCM framing for the private Python-to-Electron audio pipe."""

from __future__ import annotations

from io import BytesIO
import os
import struct
from threading import Event, Thread
from typing import Final

import pytest

import desktop_protocol.audio_channel as audio_channel_module
from desktop_protocol.audio_channel import (
    AUDIO_CHANNEL_FD,
    AUDIO_CHANNEL_FORMAT_PCM_WAV,
    AUDIO_CHANNEL_HEADER_BYTES,
    AUDIO_CHANNEL_MAGIC,
    AUDIO_CHANNEL_MAX_DURATION_SECONDS,
    AUDIO_CHANNEL_MAX_WAV_BYTES,
    AUDIO_CHANNEL_SAMPLE_RATE_HZ,
    AUDIO_CHANNEL_VERSION,
    AudioChannelError,
    AudioChannelIOError,
    AudioChannelStateError,
    AudioChannelValidationError,
    AudioChannelWriter,
)


_HEADER: Final = struct.Struct("<8sBBHII32s32s")


def _chunk(chunk_id: bytes, payload: bytes, *, pad: bytes = b"\x00") -> bytes:
    encoded = chunk_id + len(payload).to_bytes(4, "little") + payload
    if len(payload) & 1:
        encoded += pad
    return encoded


def _pcm_wav(
    *,
    sample_rate: int = AUDIO_CHANNEL_SAMPLE_RATE_HZ,
    channels: int = 1,
    bits_per_sample: int = 16,
    samples: int = 16,
    before_format: tuple[bytes, ...] = (),
    after_data: tuple[bytes, ...] = (),
) -> bytes:
    frame_bytes = channels * bits_per_sample // 8
    format_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate,
        sample_rate * frame_bytes,
        frame_bytes,
        bits_per_sample,
    )
    body = b"WAVE"
    body += b"".join(before_format)
    body += _chunk(b"fmt ", format_payload)
    body += _chunk(b"data", b"\x00" * (samples * frame_bytes))
    body += b"".join(after_data)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def _with_riff_size(audio: bytes) -> bytes:
    return audio[:4] + (len(audio) - 8).to_bytes(4, "little") + audio[8:]


class _PartialStream:
    def __init__(self, maximum_write: int = 7) -> None:
        self.maximum_write = maximum_write
        self.buffer = bytearray()
        self.closed = False
        self.flushes = 0

    def write(self, data: object) -> int:
        """Accept only a short prefix to exercise the complete-write loop."""

        if self.closed:
            raise OSError("closed")
        payload = bytes(data)  # type: ignore[call-overload]
        length = min(len(payload), self.maximum_write)
        self.buffer.extend(payload[:length])
        return length

    def flush(self) -> None:
        """Record that one complete frame was made visible to the consumer."""

        self.flushes += 1

    def close(self) -> None:
        """Mark this deterministic test stream closed."""

        self.closed = True


class _FailingStream(_PartialStream):
    def write(self, _data: object) -> int:
        """Raise a path-bearing native error that the writer must sanitize."""

        raise OSError(r"secret D:\private\reference.wav")


class _BlockingStream(_PartialStream):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.released = Event()

    def write(self, _data: object) -> int:
        """Block until close demonstrates cancellation is not write-lock-bound."""

        self.entered.set()
        self.released.wait(timeout=2.0)
        raise OSError("closed")

    def close(self) -> None:
        """Release the simulated blocked system write."""

        self.closed = True
        self.released.set()


def test_prepared_frame_matches_the_fixed_binary_layout() -> None:
    """Encode one valid frame with exact little-endian fields and safe metadata."""

    stream = _PartialStream()
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    audio = _pcm_wav(samples=32)
    frame = writer.prepare_wav(audio, 17)

    metadata = frame.metadata()
    assert metadata == {
        "clipToken": frame.clip_token,
        "sequence": 17,
        "byteLength": len(audio),
        "sha256": frame.sha256_hex,
        "mediaType": "audio/wav",
    }
    metadata["sequence"] = 99
    assert frame.sequence == 17
    assert len(frame.clip_token) == 64
    assert len(frame.sha256_hex) == 64
    assert frame.clip_token not in repr(frame)
    assert frame.sha256_hex not in repr(frame)

    writer.write_frame(frame)
    encoded = bytes(stream.buffer)
    assert len(encoded) == AUDIO_CHANNEL_HEADER_BYTES + len(audio)
    fields = _HEADER.unpack(encoded[:AUDIO_CHANNEL_HEADER_BYTES])
    assert fields[:6] == (
        AUDIO_CHANNEL_MAGIC,
        AUDIO_CHANNEL_VERSION,
        AUDIO_CHANNEL_FORMAT_PCM_WAV,
        0,
        len(audio),
        17,
    )
    assert fields[6].hex() == frame.clip_token
    assert fields[7].hex() == frame.sha256_hex
    assert encoded[AUDIO_CHANNEL_HEADER_BYTES:] == audio
    assert stream.flushes == 1


def test_tokens_are_unique_without_an_unbounded_seen_token_set() -> None:
    """Combine a process nonce and monotonic counter for lifecycle uniqueness."""

    stream = _PartialStream(maximum_write=1_000_000)
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    first = writer.prepare_wav(_pcm_wav(), 0)
    writer.write_frame(first)
    second = writer.prepare_wav(_pcm_wav(), 0)
    writer.write_frame(second)

    assert first.clip_token != second.clip_token
    assert first.clip_token[:48] == second.clip_token[:48]
    assert int(first.clip_token[48:], 16) == 0
    assert int(second.clip_token[48:], 16) == 1


def test_only_one_metadata_to_binary_gap_can_be_outstanding() -> None:
    """Bound orphan metadata by requiring write or discard before preparation."""

    writer = AudioChannelWriter(BytesIO())
    first = writer.prepare_wav(_pcm_wav(), 0)
    with pytest.raises(AudioChannelStateError):
        writer.prepare_wav(_pcm_wav(), 1)

    writer.discard_frame(first)
    second = writer.prepare_wav(_pcm_wav(), 1)
    writer.write_frame(second)
    with pytest.raises(AudioChannelStateError):
        writer.write_frame(second)


@pytest.mark.parametrize(
    ("sequence", "audio"),
    [
        (True, _pcm_wav()),
        (-1, _pcm_wav()),
        (1 << 32, _pcm_wav()),
        (0, bytearray(_pcm_wav())),
    ],
)
def test_prepare_rejects_noncanonical_input_types(
    sequence: object,
    audio: object,
) -> None:
    """Reject Boolean integers and mutable payloads before frame reservation."""

    writer = AudioChannelWriter(BytesIO())
    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(audio, sequence)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "audio",
    [
        b"",
        b"not a wave",
        _pcm_wav(sample_rate=16_000),
        _pcm_wav(channels=2),
        _pcm_wav(bits_per_sample=8),
    ],
)
def test_prepare_accepts_only_32khz_mono_pcm16(audio: bytes) -> None:
    """Keep decoder and duration assumptions fixed across both processes."""

    writer = AudioChannelWriter(BytesIO())
    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(audio, 0)


def test_prepare_enforces_the_duration_limit() -> None:
    """Reject structurally valid audio exceeding the 120-second playback bound."""

    samples = AUDIO_CHANNEL_SAMPLE_RATE_HZ * AUDIO_CHANNEL_MAX_DURATION_SECONDS
    writer = AudioChannelWriter(BytesIO())
    frame = writer.prepare_wav(_pcm_wav(samples=samples), 0)
    writer.discard_frame(frame)

    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(_pcm_wav(samples=samples + 1), 1)


def test_prepare_enforces_the_total_container_limit() -> None:
    """Reject oversized metadata chunks even when PCM duration is tiny."""

    oversized_junk = _chunk(b"JUNK", b"x" * AUDIO_CHANNEL_MAX_WAV_BYTES)
    audio = _pcm_wav(before_format=(oversized_junk,))
    writer = AudioChannelWriter(BytesIO())
    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(audio, 0)


@pytest.mark.parametrize(
    "malformed",
    [
        _pcm_wav()[:-1],
        _pcm_wav()[:4]
        + (len(_pcm_wav()) + 100).to_bytes(4, "little")
        + _pcm_wav()[8:],
        _with_riff_size(
            _pcm_wav()
            + _chunk(
                b"fmt ",
                struct.pack("<HHIIHH", 1, 1, 32_000, 64_000, 2, 16),
            )
        ),
        _with_riff_size(_pcm_wav() + _chunk(b"data", b"\x00\x00")),
    ],
)
def test_prepare_rejects_truncated_or_duplicate_riff_structure(
    malformed: bytes,
) -> None:
    """Walk the whole RIFF container instead of trusting valid-looking prefixes."""

    writer = AudioChannelWriter(BytesIO())
    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(malformed, 0)


def test_prepare_rejects_nonzero_odd_chunk_padding() -> None:
    """Require the single canonical zero pad spelling for odd RIFF chunks."""

    bad_padding = _chunk(b"JUNK", b"x", pad=b"\x7f")
    writer = AudioChannelWriter(BytesIO())
    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(_pcm_wav(before_format=(bad_padding,)), 0)


def test_prepare_rejects_wavl_silence_that_bypasses_data_duration() -> None:
    """Reject RIFF playback semantics not represented by the primary data size."""

    semantic_silence = _chunk(
        b"LIST",
        b"wavl" + _chunk(b"slnt", (0xFFFF_FFFF).to_bytes(4, "little")),
    )
    ambiguous = _with_riff_size(_pcm_wav() + semantic_silence)
    writer = AudioChannelWriter(BytesIO())
    with pytest.raises(AudioChannelValidationError):
        writer.prepare_wav(ambiguous, 0)


def test_reflective_frame_mutation_poisons_without_writing() -> None:
    """Revalidate private bytes in case in-process reflection bypasses immutability."""

    stream = _PartialStream()
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_wav(_pcm_wav(), 0)
    object.__setattr__(frame, "_header", bytes(AUDIO_CHANNEL_HEADER_BYTES))

    with pytest.raises(AudioChannelValidationError):
        writer.write_frame(frame)
    assert stream.buffer == b""
    assert stream.closed
    with pytest.raises(AudioChannelStateError):
        writer.prepare_wav(_pcm_wav(), 1)


def test_coherent_token_forgery_cannot_reuse_a_previous_clip_token() -> None:
    """Compare with writer-owned state instead of trusting self-consistent fields."""

    stream = _PartialStream(maximum_write=1_000_000)
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    audio = _pcm_wav()
    first = writer.prepare_wav(audio, 0)
    writer.write_frame(first)
    bytes_after_first = len(stream.buffer)

    second = writer.prepare_wav(audio, 1)
    reused_token = bytes.fromhex(first.clip_token)
    digest = bytes.fromhex(second.sha256_hex)
    forged_header = _HEADER.pack(
        AUDIO_CHANNEL_MAGIC,
        AUDIO_CHANNEL_VERSION,
        AUDIO_CHANNEL_FORMAT_PCM_WAV,
        0,
        len(audio),
        second.sequence,
        reused_token,
        digest,
    )
    object.__setattr__(second, "_token", reused_token)
    object.__setattr__(second, "_header", forged_header)

    with pytest.raises(AudioChannelValidationError):
        writer.write_frame(second)
    assert len(stream.buffer) == bytes_after_first
    assert stream.closed


def test_pipe_failure_is_sanitized_and_permanently_poisoned() -> None:
    """Never retry a partial frame or expose a native path-bearing exception."""

    stream = _FailingStream()
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_wav(_pcm_wav(), 0)
    with pytest.raises(AudioChannelIOError) as captured:
        writer.write_frame(frame)

    assert str(captured.value) == "Desktop speech audio channel I/O failed."
    assert "private" not in str(captured.value)
    assert stream.closed
    with pytest.raises(AudioChannelStateError):
        writer.prepare_wav(_pcm_wav(), 1)


def test_close_returns_without_closing_a_fileio_blocked_in_another_thread() -> None:
    """Leave physical close to the writer after Electron releases backpressure."""

    stream = _BlockingStream()
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_wav(_pcm_wav(), 0)
    failures: list[BaseException] = []

    def _write() -> None:
        try:
            writer.write_frame(frame)
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=_write)
    thread.start()
    assert stream.entered.wait(timeout=1.0)
    writer.close()
    assert thread.is_alive()
    assert not stream.closed
    # This models Electron first resuming/draining or closing its pipe read end.
    stream.released.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], AudioChannelIOError)


def test_discard_cannot_claim_a_frame_after_binary_writing_starts() -> None:
    """Prevent cancellation from reporting discard while bytes are in flight."""

    stream = _BlockingStream()
    writer = AudioChannelWriter(stream)  # type: ignore[arg-type]
    frame = writer.prepare_wav(_pcm_wav(), 0)
    thread = Thread(target=lambda: _capture_write_failure(writer, frame))
    thread.start()
    assert stream.entered.wait(timeout=1.0)

    with pytest.raises(AudioChannelStateError):
        writer.discard_frame(frame)
    writer.close()
    stream.released.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()


def _capture_write_failure(
    writer: AudioChannelWriter,
    frame: object,
) -> None:
    try:
        writer.write_frame(frame)  # type: ignore[arg-type]
    except AudioChannelError:
        return


def test_inherited_factory_always_claims_and_deinherits_fd3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse configurable descriptors and clear descendant inheritance first."""

    calls: list[tuple[object, ...]] = []
    stream = BytesIO()

    def _record_inheritance(descriptor: int, inheritable: bool) -> None:
        calls.append(("set_inheritable", descriptor, inheritable))

    def _return_stream(
        descriptor: int,
        mode: str,
        buffering: int,
        closefd: bool,
    ) -> BytesIO:
        calls.append(("fdopen", descriptor, mode, buffering, closefd))
        return stream

    monkeypatch.setattr(os, "set_inheritable", _record_inheritance)
    monkeypatch.setattr(os, "fdopen", _return_stream)
    monkeypatch.setattr(
        audio_channel_module,
        "_is_local_pipe_descriptor",
        lambda descriptor: descriptor == AUDIO_CHANNEL_FD,
    )

    writer = AudioChannelWriter.from_inherited_fd3()
    assert calls == [
        ("set_inheritable", AUDIO_CHANNEL_FD, False),
        ("fdopen", AUDIO_CHANNEL_FD, "wb", 0, True),
    ]
    writer.close()


def test_os_descriptor_probe_accepts_pipe_and_rejects_null_device() -> None:
    """Exercise the production OS handle-type check without replacing fd3."""

    read_descriptor, write_descriptor = os.pipe()
    null_descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        assert audio_channel_module._is_local_pipe_descriptor(read_descriptor)
        assert audio_channel_module._is_local_pipe_descriptor(write_descriptor)
        assert not audio_channel_module._is_local_pipe_descriptor(null_descriptor)
    finally:
        os.close(null_descriptor)
        os.close(write_descriptor)
        os.close(read_descriptor)


def test_inherited_factory_sanitizes_descriptor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed without attempting to wrap a descriptor that was not secured."""

    opened = False
    closed: list[int] = []

    def _fail_inheritance(_descriptor: int, _inheritable: bool) -> None:
        raise OSError("native secret")

    def _record_open(*_args: object, **_kwargs: object) -> BytesIO:
        nonlocal opened
        opened = True
        return BytesIO()

    monkeypatch.setattr(os, "set_inheritable", _fail_inheritance)
    monkeypatch.setattr(os, "fdopen", _record_open)
    monkeypatch.setattr(os, "close", closed.append)
    with pytest.raises(AudioChannelIOError) as captured:
        AudioChannelWriter.from_inherited_fd3()

    assert not opened
    assert closed == [AUDIO_CHANNEL_FD]
    assert "secret" not in str(captured.value)


def test_inherited_factory_rejects_a_non_pipe_fd3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never send private speech bytes to a regular file or device handle."""

    closed: list[int] = []
    opened = False

    def _record_open(*_args: object, **_kwargs: object) -> BytesIO:
        nonlocal opened
        opened = True
        return BytesIO()

    monkeypatch.setattr(os, "set_inheritable", lambda _fd, _flag: None)
    monkeypatch.setattr(
        audio_channel_module,
        "_is_local_pipe_descriptor",
        lambda _descriptor: False,
    )
    monkeypatch.setattr(os, "fdopen", _record_open)
    monkeypatch.setattr(os, "close", closed.append)

    with pytest.raises(AudioChannelIOError):
        AudioChannelWriter.from_inherited_fd3()
    assert not opened
    assert closed == [AUDIO_CHANNEL_FD]


def test_inherited_factory_closes_fd3_when_wrapping_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoke the inherited capability if conversion to an unbuffered file fails."""

    closed: list[int] = []

    def _fail_fdopen(*_args: object, **_kwargs: object) -> BytesIO:
        raise OSError("secret")

    monkeypatch.setattr(os, "set_inheritable", lambda _fd, _flag: None)
    monkeypatch.setattr(
        audio_channel_module,
        "_is_local_pipe_descriptor",
        lambda _descriptor: True,
    )
    monkeypatch.setattr(os, "fdopen", _fail_fdopen)
    monkeypatch.setattr(os, "close", closed.append)

    with pytest.raises(AudioChannelIOError):
        AudioChannelWriter.from_inherited_fd3()
    assert closed == [AUDIO_CHANNEL_FD]
