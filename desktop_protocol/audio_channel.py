"""Frame validated speech audio for Electron over inherited file descriptor 3.

The desktop NDJSON stream carries only bounded correlation metadata.  PCM WAV
bytes use a separate anonymous pipe so binary payloads never need Base64 and
never enter the renderer-facing JSON protocol.  A fixed header lets Electron
validate lengths before allocation and pair each payload by an opaque token.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import secrets
import stat
import struct
from threading import Lock
from typing import BinaryIO, Final


AUDIO_CHANNEL_FD: Final = 3
AUDIO_CHANNEL_MAGIC: Final = b"ELYSAUD1"
AUDIO_CHANNEL_VERSION: Final = 1
AUDIO_CHANNEL_FORMAT_PCM_WAV: Final = 1
AUDIO_CHANNEL_HEADER_BYTES: Final = 84
AUDIO_CHANNEL_MAX_WAV_BYTES: Final = 8 * 1024 * 1024
AUDIO_CHANNEL_MAX_DURATION_SECONDS: Final = 120
AUDIO_CHANNEL_SAMPLE_RATE_HZ: Final = 32_000
AUDIO_CHANNEL_CHANNEL_COUNT: Final = 1
AUDIO_CHANNEL_BITS_PER_SAMPLE: Final = 16
AUDIO_CHANNEL_MEDIA_TYPE: Final = "audio/wav"

_MAX_SEQUENCE: Final = (1 << 32) - 1
_MAX_TOKEN_COUNTER: Final = (1 << 64) - 1
_TOKEN_PREFIX_BYTES: Final = 24
_WINDOWS_FILE_TYPE_PIPE: Final = 3
_HEADER = struct.Struct("<8sBBHII32s32s")
_INVALID_AUDIO_MESSAGE: Final = "Desktop speech audio is invalid."
_INVALID_FRAME_MESSAGE: Final = "Desktop speech audio frame is invalid."
_STATE_MESSAGE: Final = "Desktop speech audio channel is not writable."
_IO_MESSAGE: Final = "Desktop speech audio channel I/O failed."
_FRAME_SEAL = object()


class AudioChannelError(Exception):
    """Base class for sanitized desktop speech-channel failures."""


class AudioChannelValidationError(AudioChannelError):
    """Report malformed PCM WAV data or a forged prepared frame."""


class AudioChannelStateError(AudioChannelError):
    """Report invalid channel lifecycle or outstanding-frame use."""


class AudioChannelIOError(AudioChannelError):
    """Report a poisoned or failed anonymous-pipe operation."""


class PreparedAudioFrame:
    """Hold one immutable, writer-owned header and validated WAV payload.

    The payload and ownership seal remain private and ``repr`` omits the token,
    hash, and bytes.  Callers may expose only :meth:`metadata` over NDJSON and
    then pass the exact object back to its creating writer.
    """

    _byte_length: int
    _header: bytes
    _owner: object
    _payload: bytes
    _sequence: int
    _sha256_hex: str
    _token: bytes

    __slots__ = (
        "_byte_length",
        "_header",
        "_owner",
        "_payload",
        "_sequence",
        "_sha256_hex",
        "_token",
    )

    def __init__(
        self,
        *,
        token: bytes,
        sequence: int,
        sha256_digest: bytes,
        header: bytes,
        payload: bytes,
        owner: object,
        _seal: object,
    ) -> None:
        """Create a frame only for the private validated writer factory."""

        if _seal is not _FRAME_SEAL:
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
        object.__setattr__(self, "_token", token)
        object.__setattr__(self, "_sequence", sequence)
        object.__setattr__(self, "_sha256_hex", sha256_digest.hex())
        object.__setattr__(self, "_byte_length", len(payload))
        object.__setattr__(self, "_header", header)
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(self, "_owner", owner)

    @property
    def clip_token(self) -> str:
        """Return the lowercase 256-bit token used to pair NDJSON metadata."""

        return self._token.hex()

    @property
    def sequence(self) -> int:
        """Return the caller-supplied sentence sequence encoded in the header."""

        return self._sequence

    @property
    def byte_length(self) -> int:
        """Return the exact WAV payload byte length."""

        return self._byte_length

    @property
    def sha256_hex(self) -> str:
        """Return the lowercase digest Electron must verify incrementally."""

        return self._sha256_hex

    @property
    def media_type(self) -> str:
        """Return the sole media type admitted by this desktop channel."""

        return AUDIO_CHANNEL_MEDIA_TYPE

    def metadata(self) -> dict[str, object]:
        """Build the safe correlation fields for one NDJSON speech event."""

        return {
            "clipToken": self.clip_token,
            "sequence": self.sequence,
            "byteLength": self.byte_length,
            "sha256": self.sha256_hex,
            "mediaType": self.media_type,
        }

    def __repr__(self) -> str:
        return (
            "PreparedAudioFrame("
            f"sequence={self._sequence}, byte_length={self._byte_length})"
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("PreparedAudioFrame values are immutable.")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("PreparedAudioFrame values are immutable.")


class _FrameSnapshot:
    """Retain writer-owned values so reflective frame edits cannot change I/O."""

    __slots__ = ("header", "payload", "sequence", "sha256_hex", "token")

    def __init__(
        self,
        *,
        header: bytes,
        payload: bytes,
        sequence: int,
        sha256_hex: str,
        token: bytes,
    ) -> None:
        self.header = header
        self.payload = payload
        self.sequence = sequence
        self.sha256_hex = sha256_hex
        self.token = token


def _validate_desktop_pcm_wav(audio: bytes) -> None:
    """Require the one canonical PCM layout emitted by the managed worker.

    RIFF permits chunks such as ``wavl``/``slnt`` whose playback duration is
    not described by the primary ``data`` size.  Accepting unknown chunks
    would therefore let two decoders disagree about the 120-second bound.  The
    managed worker emits exactly a 16-byte ``fmt`` chunk followed by ``data``,
    so the desktop boundary deliberately admits only that spelling.
    """

    if (
        type(audio) is not bytes
        or not 46 <= len(audio) <= AUDIO_CHANNEL_MAX_WAV_BYTES
        or audio[:4] != b"RIFF"
        or audio[8:12] != b"WAVE"
        or audio[12:16] != b"fmt "
        or int.from_bytes(audio[16:20], "little") != 16
        or audio[36:40] != b"data"
        or int.from_bytes(audio[4:8], "little") + 8 != len(audio)
    ):
        raise AudioChannelValidationError(_INVALID_AUDIO_MESSAGE)

    frame_bytes = AUDIO_CHANNEL_CHANNEL_COUNT * AUDIO_CHANNEL_BITS_PER_SAMPLE // 8
    expected_format = (
        1,
        AUDIO_CHANNEL_CHANNEL_COUNT,
        AUDIO_CHANNEL_SAMPLE_RATE_HZ,
        AUDIO_CHANNEL_SAMPLE_RATE_HZ * frame_bytes,
        frame_bytes,
        AUDIO_CHANNEL_BITS_PER_SAMPLE,
    )
    data_size = int.from_bytes(audio[40:44], "little")
    if (
        struct.unpack_from("<HHIIHH", audio, 20) != expected_format
        or data_size == 0
        or data_size % frame_bytes != 0
        or data_size + 44 != len(audio)
    ):
        raise AudioChannelValidationError(_INVALID_AUDIO_MESSAGE)

    maximum_pcm_bytes = (
        AUDIO_CHANNEL_SAMPLE_RATE_HZ
        * AUDIO_CHANNEL_CHANNEL_COUNT
        * AUDIO_CHANNEL_BITS_PER_SAMPLE
        // 8
        * AUDIO_CHANNEL_MAX_DURATION_SECONDS
    )
    if data_size > maximum_pcm_bytes:
        raise AudioChannelValidationError(_INVALID_AUDIO_MESSAGE)


def _is_local_pipe_descriptor(descriptor: int) -> bool:
    """Return whether fd3 names a pipe-like IPC handle instead of a file."""

    try:
        if os.name == "nt":
            import ctypes

            msvcrt = importlib.import_module("msvcrt")
            get_osfhandle = getattr(msvcrt, "get_osfhandle")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_file_type = kernel32.GetFileType
            get_file_type.argtypes = [ctypes.c_void_p]
            get_file_type.restype = ctypes.c_uint32
            handle = get_osfhandle(descriptor)
            return (
                type(handle) is int
                and handle != -1
                and get_file_type(ctypes.c_void_p(handle))
                == _WINDOWS_FILE_TYPE_PIPE
            )
        mode = os.fstat(descriptor).st_mode
        return stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)
    except Exception:
        return False


def _write_all(stream: BinaryIO, payload: bytes) -> None:
    """Write a complete immutable region despite legal partial writes."""

    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            written = stream.write(view[offset:])
        except InterruptedError:
            continue
        except Exception:
            raise AudioChannelIOError(_IO_MESSAGE) from None
        if (
            type(written) is not int
            or written <= 0
            or written > len(view) - offset
        ):
            raise AudioChannelIOError(_IO_MESSAGE)
        offset += written


class AudioChannelWriter:
    """Serialize one bounded WAV frame at a time onto a private binary pipe.

    Preparation admits at most one outstanding frame, which bounds the gap
    between NDJSON metadata and binary delivery. Writes are serialized while
    ``close`` never waits for a blocked ``FileIO.write``: Windows does not let
    another thread close that object until the write returns. Electron must
    first resume draining or close its read end, after which the writing thread
    performs the physical close. Any validation or I/O failure poisons the
    writer and frames are never retried because a partial pipe write cannot be
    resynchronized safely.
    """

    def __init__(self, stream: BinaryIO) -> None:
        """Own a writable stream that its writer thread will close safely."""

        try:
            write = getattr(stream, "write", None)
            flush = getattr(stream, "flush", None)
            close = getattr(stream, "close", None)
        except BaseException:
            raise TypeError("stream must be a writable binary stream.") from None
        if not callable(write) or not callable(flush) or not callable(close):
            raise TypeError("stream must be a writable binary stream.")
        try:
            token_prefix = secrets.token_bytes(_TOKEN_PREFIX_BYTES)
        except Exception:
            raise AudioChannelIOError(_IO_MESSAGE) from None
        if (
            type(token_prefix) is not bytes
            or len(token_prefix) != _TOKEN_PREFIX_BYTES
        ):
            raise AudioChannelIOError(_IO_MESSAGE)
        self._stream = stream
        self._token_prefix = token_prefix
        self._next_token_counter = 0
        self._owner = object()
        self._pending: PreparedAudioFrame | None = None
        self._pending_snapshot: _FrameSnapshot | None = None
        self._writing: PreparedAudioFrame | None = None
        self._closed = False
        self._poisoned = False
        self._state_lock = Lock()
        self._write_lock = Lock()

    @classmethod
    def from_inherited_fd3(cls) -> "AudioChannelWriter":
        """Take exclusive ownership of the fixed inherited audio descriptor.

        The descriptor number is intentionally not configurable.  Clearing its
        inheritable flag before constructing any service prevents GPT-SoVITS,
        FFmpeg, or another descendant from retaining the pipe and delaying EOF.
        """

        try:
            os.set_inheritable(AUDIO_CHANNEL_FD, False)
        except Exception:
            _close_inherited_fd3_safely()
            raise AudioChannelIOError(_IO_MESSAGE) from None
        if not _is_local_pipe_descriptor(AUDIO_CHANNEL_FD):
            _close_inherited_fd3_safely()
            raise AudioChannelIOError(_IO_MESSAGE)
        try:
            stream = os.fdopen(
                AUDIO_CHANNEL_FD,
                "wb",
                buffering=0,
                closefd=True,
            )
        except Exception:
            _close_inherited_fd3_safely()
            raise AudioChannelIOError(_IO_MESSAGE) from None
        try:
            return cls(stream)
        except BaseException:
            try:
                stream.close()
            except BaseException:
                pass
            raise

    def prepare_wav(self, audio: bytes, sequence: int) -> PreparedAudioFrame:
        """Validate and reserve one PCM WAV frame for later ordered writing."""

        if type(sequence) is not int or not 0 <= sequence <= _MAX_SEQUENCE:
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
        if type(audio) is not bytes:
            raise AudioChannelValidationError(_INVALID_AUDIO_MESSAGE)
        _validate_desktop_pcm_wav(audio)
        digest = hashlib.sha256(audio).digest()

        with self._state_lock:
            if self._closed or self._poisoned:
                raise AudioChannelStateError(_STATE_MESSAGE)
            if self._pending is not None or self._writing is not None:
                raise AudioChannelStateError(
                    "Desktop speech audio already has a pending frame."
                )
            if self._next_token_counter > _MAX_TOKEN_COUNTER:
                self._poisoned = True
                raise AudioChannelStateError(_STATE_MESSAGE)
            token = self._token_prefix + self._next_token_counter.to_bytes(8, "big")
            self._next_token_counter += 1
            header = _HEADER.pack(
                AUDIO_CHANNEL_MAGIC,
                AUDIO_CHANNEL_VERSION,
                AUDIO_CHANNEL_FORMAT_PCM_WAV,
                0,
                len(audio),
                sequence,
                token,
                digest,
            )
            frame = PreparedAudioFrame(
                token=token,
                sequence=sequence,
                sha256_digest=digest,
                header=header,
                payload=audio,
                owner=self._owner,
                _seal=_FRAME_SEAL,
            )
            self._pending = frame
            self._pending_snapshot = _FrameSnapshot(
                header=header,
                payload=audio,
                sequence=sequence,
                sha256_hex=digest.hex(),
                token=token,
            )
            return frame

    def write_frame(self, frame: PreparedAudioFrame) -> None:
        """Write the exact pending frame once, poisoning on any partial failure."""

        if type(frame) is not PreparedAudioFrame:
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
        with self._write_lock:
            try:
                with self._state_lock:
                    if (
                        self._closed
                        or self._poisoned
                        or self._pending is not frame
                        or frame._owner is not self._owner
                    ):
                        raise AudioChannelStateError(_STATE_MESSAGE)
                    snapshot = self._pending_snapshot
                    if snapshot is None:
                        raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
                    self._revalidate_frame(frame, snapshot)
                    stream = self._stream
                    self._pending = None
                    self._pending_snapshot = None
                    self._writing = frame
                # Use only the writer-owned snapshot after releasing the state
                # lock. A caller can reflectively mutate the public frame from
                # another thread, but cannot create a check/use gap in output.
                _write_all(stream, snapshot.header)
                _write_all(stream, snapshot.payload)
                try:
                    stream.flush()
                except Exception:
                    raise AudioChannelIOError(_IO_MESSAGE) from None
            except AudioChannelStateError:
                raise
            except AudioChannelError:
                self._poison_and_close(frame)
                raise
            except BaseException:
                self._poison_and_close(frame)
                raise
            else:
                close_after_write = False
                with self._state_lock:
                    if self._writing is frame:
                        self._writing = None
                    close_after_write = self._closed
                if close_after_write:
                    try:
                        stream.close()
                    except Exception:
                        raise AudioChannelIOError(_IO_MESSAGE) from None

    def discard_frame(self, frame: PreparedAudioFrame) -> None:
        """Release one unwritten pending frame after trusted cancellation."""

        if type(frame) is not PreparedAudioFrame:
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
        with self._state_lock:
            if (
                self._closed
                or self._poisoned
                or self._pending is not frame
                or frame._owner is not self._owner
            ):
                raise AudioChannelStateError(_STATE_MESSAGE)
            self._pending = None
            self._pending_snapshot = None

    def close(self) -> None:
        """Close idempotently without waiting for a concurrent blocked write.

        The Electron owner must drain or destroy the read end before requesting
        Python cancellation. If a write is active, that producer closes the
        stream as soon as the OS operation returns; otherwise this call closes
        it immediately.
        """

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._pending_snapshot = None
            stream = self._stream if self._writing is None else None
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            raise AudioChannelIOError(_IO_MESSAGE) from None

    def _revalidate_frame(
        self,
        frame: PreparedAudioFrame,
        snapshot: _FrameSnapshot,
    ) -> None:
        """Detect reflective mutation before any untrusted bytes reach the pipe."""

        if (
            type(frame._token) is not bytes
            or len(frame._token) != 32
            or type(frame._sequence) is not int
            or not 0 <= frame._sequence <= _MAX_SEQUENCE
            or type(frame._sha256_hex) is not str
            or type(frame._byte_length) is not int
            or type(frame._header) is not bytes
            or len(frame._header) != AUDIO_CHANNEL_HEADER_BYTES
            or type(frame._payload) is not bytes
            or frame._byte_length != len(frame._payload)
            or frame._token != snapshot.token
            or frame._sequence != snapshot.sequence
            or frame._sha256_hex != snapshot.sha256_hex
            or frame._byte_length != len(snapshot.payload)
            or frame._header != snapshot.header
            or frame._payload is not snapshot.payload
        ):
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
        _validate_desktop_pcm_wav(snapshot.payload)
        digest = hashlib.sha256(snapshot.payload).digest()
        if snapshot.sha256_hex != digest.hex():
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)
        expected = _HEADER.pack(
            AUDIO_CHANNEL_MAGIC,
            AUDIO_CHANNEL_VERSION,
            AUDIO_CHANNEL_FORMAT_PCM_WAV,
            0,
            len(snapshot.payload),
            snapshot.sequence,
            snapshot.token,
            digest,
        )
        if snapshot.header != expected:
            raise AudioChannelValidationError(_INVALID_FRAME_MESSAGE)

    def _poison_and_close(self, frame: PreparedAudioFrame) -> None:
        """Make a non-resynchronizable failure permanent without leaking detail."""

        with self._state_lock:
            self._poisoned = True
            self._closed = True
            if self._pending is frame:
                self._pending = None
                self._pending_snapshot = None
            if self._writing is frame:
                self._writing = None
            stream = self._stream
        try:
            stream.close()
        except BaseException:
            pass


def _close_inherited_fd3_safely() -> None:
    """Remove a failed channel capability before text-only startup continues."""

    try:
        os.close(AUDIO_CHANNEL_FD)
    except Exception:
        pass


__all__ = [
    "AUDIO_CHANNEL_BITS_PER_SAMPLE",
    "AUDIO_CHANNEL_CHANNEL_COUNT",
    "AUDIO_CHANNEL_FD",
    "AUDIO_CHANNEL_FORMAT_PCM_WAV",
    "AUDIO_CHANNEL_HEADER_BYTES",
    "AUDIO_CHANNEL_MAGIC",
    "AUDIO_CHANNEL_MAX_DURATION_SECONDS",
    "AUDIO_CHANNEL_MAX_WAV_BYTES",
    "AUDIO_CHANNEL_MEDIA_TYPE",
    "AUDIO_CHANNEL_SAMPLE_RATE_HZ",
    "AUDIO_CHANNEL_VERSION",
    "AudioChannelError",
    "AudioChannelIOError",
    "AudioChannelStateError",
    "AudioChannelValidationError",
    "AudioChannelWriter",
    "PreparedAudioFrame",
]
