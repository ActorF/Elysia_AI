"""Verify optional desktop speech composition without loading native models.

These tests keep the real sentence queue and private audio framing while
replacing only the managed worker bootstrap.  That boundary proves ordering,
cancellation, and text-path isolation without requiring GPU state in CI.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Any, cast

import pytest

import desktop_speech as desktop_speech_module
from desktop_protocol import (
    AUDIO_CHANNEL_HEADER_BYTES,
    AUDIO_CHANNEL_MAGIC,
    AudioChannelWriter,
)
from desktop_speech import (
    DesktopSpeechConfig,
    DesktopSpeechCoordinator,
)
from voice import SynthesisRequest, SynthesisResult
from voice.speech_queue import SpeechSynthesisBindingLease


_HEADER = struct.Struct("<8sBBHII32s32s")


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    """Encode one canonical even-sized RIFF chunk."""

    encoded = chunk_id + len(payload).to_bytes(4, "little") + payload
    return encoded + (b"\x00" if len(payload) & 1 else b"")


def _pcm_wav(sample: int = 1, *, samples: int = 32) -> bytes:
    """Build one canonical 32 kHz mono PCM16 clip for the fd3 contract."""

    format_payload = struct.pack(
        "<HHIIHH",
        1,
        1,
        32_000,
        64_000,
        2,
        16,
    )
    pcm = sample.to_bytes(2, "little", signed=True) * samples
    body = b"WAVE" + _chunk(b"fmt ", format_payload) + _chunk(b"data", pcm)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


class _RecordingStream:
    """Record exact binary writes while exposing deterministic close state."""

    def __init__(self) -> None:
        """Initialize an empty writable byte sink."""

        self.buffer = bytearray()
        self.flushes = 0
        self.closed = False

    def write(self, data: object) -> int:
        """Append a complete bytes-like value unless the stream is closed."""

        if self.closed:
            raise OSError("closed")
        payload = bytes(data)  # type: ignore[call-overload]
        self.buffer.extend(payload)
        return len(payload)

    def flush(self) -> None:
        """Record one frame becoming visible to Electron."""

        if self.closed:
            raise OSError("closed")
        self.flushes += 1

    def close(self) -> None:
        """Close idempotently while retaining bytes for assertions."""

        self.closed = True


class _SequenceSynthesizer:
    """Return canonical clips while recording exact segmented text."""

    def __init__(self) -> None:
        """Initialize a concurrency-safe-enough single-worker request log."""

        self.requests: list[SynthesisRequest] = []

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Return one distinguishable WAV for the next FIFO sequence."""

        self.requests.append(request)
        return SynthesisResult(
            _pcm_wav(len(self.requests)),
            "wav",
            1.0,
        )


class _BlockingSynthesizer(_SequenceSynthesizer):
    """Hold native work so cancellation can win before delivery."""

    def __init__(self) -> None:
        """Create explicit synthesis entry and release gates."""

        super().__init__()
        self.started = Event()
        self.release = Event()

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Wait for the test before returning a now-stale result."""

        self.requests.append(request)
        self.started.set()
        assert self.release.wait(2.0)
        return SynthesisResult(_pcm_wav(), "wav", 1.0)


class _PoisoningManagedLease:
    """Model an active native abort that permanently invalidates its worker."""

    def __init__(self) -> None:
        """Create exact active-token, cancellation, and release evidence."""

        self._lock = Lock()
        self._active_token: str | None = None
        self._poisoned = False
        self.started = Event()
        self.released = Event()
        self.abort_called = Event()
        self.calls: list[tuple[str, str, str]] = []
        self.abort_tokens: list[str] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep this partial-manifest managed generation non-cacheable."""

        return False

    @property
    def poisoned(self) -> bool:
        """Report permanent loss after the matching active abort."""

        with self._lock:
            return self._poisoned

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Publish the active token and return only after abort releases it."""

        with self._lock:
            self._active_token = operation_token
            self.calls.append((text, language, operation_token))
        self.started.set()
        assert self.released.wait(2.0)
        return SynthesisResult(_pcm_wav(), "wav", 1.0)

    def abort(self, operation_token: str) -> bool:
        """Poison only the exact active token and wake the simulated call."""

        with self._lock:
            self.abort_tokens.append(operation_token)
            if self._active_token != operation_token:
                return False
            self._active_token = None
            self._poisoned = True
        self.abort_called.set()
        self.released.set()
        return True


class _SpontaneouslyPoisoningManagedLease:
    """Model a native protocol failure that poisons itself during synthesis."""

    def __init__(self) -> None:
        """Create observable poisoned state without requiring cancellation."""

        self._lock = Lock()
        self._poisoned = False
        self.started = Event()
        self.calls: list[tuple[str, str, str]] = []
        self.abort_tokens: list[str] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep this partial-manifest managed generation non-cacheable."""

        return False

    @property
    def poisoned(self) -> bool:
        """Report permanent loss after the injected native failure."""

        with self._lock:
            return self._poisoned

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Poison this generation and raise one private worker failure."""

        with self._lock:
            self.calls.append((text, language, operation_token))
            self._poisoned = True
        self.started.set()
        raise RuntimeError("private native protocol detail")

    def abort(self, operation_token: str) -> bool:
        """Record stale cleanup attempts without changing poisoned state."""

        self.abort_tokens.append(operation_token)
        return False


class _PreStartManagedLease:
    """Model a cancellation tombstone that leaves its worker generation valid."""

    def __init__(self) -> None:
        """Create a registration gate and reusable healthy synthesis state."""

        self._lock = Lock()
        self._tombstones: set[str] = set()
        self.started = Event()
        self.allow_registration = Event()
        self.abort_called = Event()
        self.calls: list[tuple[str, str, str]] = []

    @property
    def cache_eligible(self) -> bool:
        """Keep the managed fake outside audio caching."""

        return False

    @property
    def poisoned(self) -> bool:
        """Confirm that a pre-start cancellation does not invalidate it."""

        return False

    def synthesize(
        self,
        text: str,
        language: str,
        operation_token: str,
    ) -> SynthesisResult:
        """Pause first registration, then return a clip for healthy work."""

        with self._lock:
            self.calls.append((text, language, operation_token))
            first_call = len(self.calls) == 1
        if first_call:
            self.started.set()
            assert self.allow_registration.wait(2.0)
        return SynthesisResult(_pcm_wav(len(self.calls)), "wav", 1.0)

    def abort(self, operation_token: str) -> bool:
        """Record a pre-start tombstone without poisoning the generation."""

        with self._lock:
            self._tombstones.add(operation_token)
        self.abort_called.set()
        return True


class _FakeRuntime:
    """Expose managed-runtime ownership gates without a subprocess or GPU."""

    def __init__(
        self,
        *,
        block_bootstrap: bool = False,
        runtime_lease: object | None = None,
    ) -> None:
        """Initialize lifecycle evidence and an optional acquisition gate."""

        self.block_bootstrap = block_bootstrap
        self.acquire_started = Event()
        self.release_acquire = Event()
        self.acquired: list[object] = []
        self.shutdown_calls = 0
        self.shutdown_called = Event()
        self.runtime_lease = runtime_lease if runtime_lease is not None else object()

    def acquire_lease(self, selection: object) -> object:
        """Record the fixed selection and optionally pause bootstrap."""

        self.acquire_started.set()
        if self.block_bootstrap:
            assert self.release_acquire.wait(2.0)
        self.acquired.append(selection)
        return self.runtime_lease

    def shutdown(self) -> None:
        """Record exclusive owner cleanup."""

        self.shutdown_calls += 1
        self.shutdown_called.set()


class _FakeCatalog:
    """Resolve the coordinator's one fixed profile and emotion."""

    def __init__(self) -> None:
        """Initialize an empty resolution log."""

        self.calls: list[tuple[str, str]] = []

    def resolve_selection(self, profile_id: str, emotion: str) -> object:
        """Return only the safe identity fields needed by the binding factory."""

        self.calls.append((profile_id, emotion))
        return SimpleNamespace(profile_id="elysia-test", emotion="neutral")


def _config(tmp_path: Path) -> DesktopSpeechConfig:
    """Return absolute private paths that require no files in unit tests."""

    return DesktopSpeechConfig(
        runtime_root=(tmp_path / "runtime").resolve(),
        worker_script=(tmp_path / "worker.py").resolve(),
        catalog_path=(tmp_path / "catalog.json").resolve(),
        asset_root=(tmp_path / "assets").resolve(),
        allow_local_evaluation=True,
        deterministic_seed=123,
        synthesis_timeout_seconds=5.0,
    )


def _install_fake_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    runtime: _FakeRuntime,
    synthesizer: _SequenceSynthesizer,
) -> _FakeCatalog:
    """Replace native acquisition while retaining the real queue and framing."""

    catalog = _FakeCatalog()
    binding = SpeechSynthesisBindingLease(
        lease_id="desktop-test-lease",
        cache_identity="desktop-test-cache",
        synthesizer=synthesizer,
    )
    monkeypatch.setattr(
        desktop_speech_module,
        "ManagedGptSovitsRuntime",
        lambda _config: runtime,
    )
    monkeypatch.setattr(
        desktop_speech_module.JsonVoiceProfileCatalog,
        "load",
        lambda *_args, **_kwargs: catalog,
    )
    monkeypatch.setattr(
        desktop_speech_module,
        "_create_managed_synthesis_binding_lease",
        lambda **_kwargs: binding,
    )
    return catalog


def _install_managed_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    runtime: _FakeRuntime,
) -> _FakeCatalog:
    """Keep the real managed-binding factory around a model-free fake lease."""

    catalog = _FakeCatalog()
    monkeypatch.setattr(
        desktop_speech_module,
        "ManagedGptSovitsRuntime",
        lambda _config: runtime,
    )
    monkeypatch.setattr(
        desktop_speech_module.JsonVoiceProfileCatalog,
        "load",
        lambda *_args, **_kwargs: catalog,
    )
    return catalog


def test_pending_stream_reaches_fifo_and_metadata_precedes_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transfer pre-ready text and publish exact metadata before its fd3 frame."""

    runtime = _FakeRuntime(block_bootstrap=True)
    synthesizer = _SequenceSynthesizer()
    catalog = _install_fake_bootstrap(monkeypatch, runtime, synthesizer)
    stream = _RecordingStream()
    events: list[tuple[str, str, dict[str, Any], int]] = []
    terminal = Event()

    def _record_event(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Capture pipe length at the exact control-event boundary."""

        events.append((name, request_id, data, len(stream.buffer)))
        if name == "voice.speech.terminal":
            terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    turn = coordinator.start_turn("request-1", "chat-1")
    assert runtime.acquire_started.wait(1.0)
    turn.feed("First sentence. Second sentence!")
    turn.finish()
    assert synthesizer.requests == []

    runtime.release_acquire.set()
    assert coordinator.wait_until_settled(1.0)
    assert terminal.wait(2.0)

    assert catalog.calls == [("default", "neutral")]
    assert [request.text for request in synthesizer.requests] == [
        "First sentence.",
        " Second sentence!",
    ]
    assert [item[0] for item in events] == [
        "voice.speech.clip",
        "voice.speech.clip",
        "voice.speech.terminal",
    ]
    first_name, first_request_id, first_data, first_binary_size = events[0]
    assert first_name == "voice.speech.clip"
    assert first_request_id == "request-1"
    assert first_binary_size == 0
    assert first_data["chatId"] == "chat-1"
    assert set(first_data) == {
        "chatId",
        "clipToken",
        "sequence",
        "byteLength",
        "sha256",
        "mediaType",
    }
    assert first_data["sequence"] == 0
    assert first_data["sha256"] == hashlib.sha256(_pcm_wav(1)).hexdigest()
    assert events[1][3] > 0
    assert events[-1][2] == {
        "chatId": "chat-1",
        "state": "completed",
        "submittedSentences": 2,
        "completedSentences": 2,
        "failedSentences": 0,
    }

    encoded = bytes(stream.buffer)
    first_header = _HEADER.unpack(encoded[:AUDIO_CHANNEL_HEADER_BYTES])
    assert first_header[0] == AUDIO_CHANNEL_MAGIC
    assert first_header[4] == len(_pcm_wav(1))
    assert first_header[5] == 0
    assert first_data["clipToken"] == first_header[6].hex()
    assert first_data["sha256"] == first_header[7].hex()
    assert encoded[
        AUDIO_CHANNEL_HEADER_BYTES : AUDIO_CHANNEL_HEADER_BYTES + len(_pcm_wav(1))
    ] == _pcm_wav(1)
    second_offset = AUDIO_CHANNEL_HEADER_BYTES + len(_pcm_wav(1))
    second_header = _HEADER.unpack(
        encoded[second_offset : second_offset + AUDIO_CHANNEL_HEADER_BYTES]
    )
    second_data = events[1][2]
    assert events[1][3] == second_offset
    assert second_header[0] == AUDIO_CHANNEL_MAGIC
    assert second_header[4] == len(_pcm_wav(2))
    assert second_header[5] == 1
    assert second_data["sequence"] == 1
    assert second_data["clipToken"] == second_header[6].hex()
    assert second_data["sha256"] == second_header[7].hex()
    second_payload_offset = second_offset + AUDIO_CHANNEL_HEADER_BYTES
    assert encoded[second_payload_offset:] == _pcm_wav(2)
    assert events[-1][3] == len(encoded)
    assert stream.flushes == 2
    coordinator.shutdown()
    assert runtime.shutdown_calls == 1


def test_cancellation_suppresses_late_native_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard a result that returns after its exact turn was cancelled."""

    runtime = _FakeRuntime()
    synthesizer = _BlockingSynthesizer()
    _install_fake_bootstrap(monkeypatch, runtime, synthesizer)
    stream = _RecordingStream()
    events: list[tuple[str, dict[str, Any]]] = []
    terminal = Event()

    def _record_event(
        name: str,
        _request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Record only safe cancellation outcomes."""

        events.append((name, data))
        if name == "voice.speech.terminal":
            terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    assert coordinator.wait_until_settled(0.0) is False
    turn = coordinator.start_turn("request-cancel", "chat-cancel")
    assert coordinator.wait_until_settled(1.0)
    turn.feed("Do not play this.")
    turn.finish()
    assert synthesizer.started.wait(1.0)
    assert turn.cancel()
    synthesizer.release.set()
    assert terminal.wait(2.0)

    assert bytes(stream.buffer) == b""
    assert events == [
        (
            "voice.speech.terminal",
            {
                "chatId": "chat-cancel",
                "state": "cancelled",
                "submittedSentences": 1,
                "completedSentences": 0,
                "failedSentences": 0,
            },
        )
    ]
    coordinator.shutdown()


def test_active_managed_cancel_fail_closes_speech_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disable fd3 after active abort instead of reusing a poisoned worker."""

    runtime_lease = _PoisoningManagedLease()
    runtime = _FakeRuntime(runtime_lease=runtime_lease)
    catalog = _install_managed_bootstrap(monkeypatch, runtime)
    stream = _RecordingStream()
    events: list[tuple[str, str, dict[str, Any]]] = []
    first_terminal = Event()

    def _record_event(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Capture closed events and expose the first cancellation boundary."""

        events.append((name, request_id, data))
        if name == "voice.speech.terminal" and request_id == "request-active":
            first_terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    first = coordinator.start_turn("request-active", "chat-active")
    assert coordinator.wait_until_settled(1.0)
    first.feed("Cancel active synthesis.")
    first.finish()
    assert runtime_lease.started.wait(1.0)

    assert first.cancel()
    assert runtime_lease.abort_called.wait(1.0)
    assert runtime.shutdown_called.wait(1.0)
    assert first_terminal.wait(1.0)
    assert coordinator.get_status().state == "unavailable"
    assert stream.closed
    assert bytes(stream.buffer) == b""

    second = coordinator.start_turn("request-after", "chat-after")
    second.feed("Do not reuse the poisoned worker.")
    second.finish()

    assert catalog.calls == [("default", "neutral")]
    assert len(runtime.acquired) == 1
    assert len(runtime_lease.calls) == 1
    assert runtime.shutdown_calls == 1
    assert [event[0] for event in events] == [
        "voice.speech.terminal",
        "voice.speech.terminal",
    ]
    assert [event[1] for event in events] == ["request-active", "request-after"]
    assert events[1][2] == {
        "chatId": "chat-after",
        "state": "cancelled",
        "submittedSentences": 0,
        "completedSentences": 0,
        "failedSentences": 0,
    }

    coordinator.shutdown()
    assert runtime.shutdown_calls == 1


def test_spontaneous_managed_failure_fail_closes_speech_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate an unrequested worker poison through the coordinator once."""

    runtime_lease = _SpontaneouslyPoisoningManagedLease()
    runtime = _FakeRuntime(runtime_lease=runtime_lease)
    catalog = _install_managed_bootstrap(monkeypatch, runtime)
    stream = _RecordingStream()
    events: list[tuple[str, str, dict[str, Any]]] = []
    replacement_terminal = Event()

    def _record_event(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Capture sanitized terminal events after the runtime invalidates."""

        events.append((name, request_id, data))
        if name == "voice.speech.terminal" and request_id == "request-after-fault":
            replacement_terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    failed = coordinator.start_turn("request-fault", "chat-fault")
    assert coordinator.wait_until_settled(1.0)
    failed.feed("Trigger one native worker failure.")
    failed.finish()

    assert runtime_lease.started.wait(1.0)
    assert runtime.shutdown_called.wait(1.0)
    assert coordinator.get_status().state == "unavailable"
    assert stream.closed
    assert bytes(stream.buffer) == b""

    replacement = coordinator.start_turn(
        "request-after-fault",
        "chat-after-fault",
    )
    replacement.feed("Do not retry the invalid generation.")
    replacement.finish()
    assert replacement_terminal.wait(1.0)

    assert catalog.calls == [("default", "neutral")]
    assert len(runtime.acquired) == 1
    assert len(runtime_lease.calls) == 1
    assert runtime.shutdown_calls == 1
    assert not any(name == "voice.speech.clip" for name, _, _ in events)
    assert events[-1] == (
        "voice.speech.terminal",
        "request-after-fault",
        {
            "chatId": "chat-after-fault",
            "state": "cancelled",
            "submittedSentences": 0,
            "completedSentences": 0,
            "failedSentences": 0,
        },
    )

    coordinator.shutdown()
    assert runtime.shutdown_calls == 1


def test_prestart_managed_cancel_keeps_next_turn_playable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a healthy worker after cancellation wins before native admission."""

    runtime_lease = _PreStartManagedLease()
    runtime = _FakeRuntime(runtime_lease=runtime_lease)
    _install_managed_bootstrap(monkeypatch, runtime)
    stream = _RecordingStream()
    events: list[tuple[str, str, dict[str, Any]]] = []
    second_terminal = Event()

    def _record_event(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Capture exact cancellation and replacement playback outcomes."""

        events.append((name, request_id, data))
        if name == "voice.speech.terminal" and request_id == "request-next":
            second_terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    first = coordinator.start_turn("request-prestart", "chat-prestart")
    assert coordinator.wait_until_settled(1.0)
    first.feed("Cancel before registration.")
    first.finish()
    assert runtime_lease.started.wait(1.0)

    assert first.cancel()
    assert runtime_lease.abort_called.wait(1.0)
    assert coordinator.get_status().state == "ready"
    runtime_lease.allow_registration.set()

    second = coordinator.start_turn("request-next", "chat-next")
    second.feed("This sentence still plays.")
    second.finish()
    assert second_terminal.wait(2.0)

    assert coordinator.get_status().state == "ready"
    assert runtime.shutdown_calls == 0
    assert len(runtime_lease.calls) == 2
    assert [event[:2] for event in events] == [
        ("voice.speech.terminal", "request-prestart"),
        ("voice.speech.clip", "request-next"),
        ("voice.speech.terminal", "request-next"),
    ]
    assert bytes(stream.buffer) != b""

    coordinator.shutdown()
    assert runtime.shutdown_calls == 1


def test_bootstrap_failure_closes_only_optional_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close fd3 and retire startup work without private failure details."""

    stream = _RecordingStream()
    events: list[tuple[str, str, dict[str, Any]]] = []
    runtime_started = Event()
    allow_runtime_failure = Event()
    terminal = Event()

    def _record_event(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Capture the timing-independent sanitized bootstrap outcome."""

        events.append((name, request_id, data))
        if name == "voice.speech.terminal":
            terminal.set()

    def _fail_runtime(_config: object) -> object:
        """Fail after the test has registered startup work with the coordinator."""

        runtime_started.set()
        assert allow_runtime_failure.wait(2.0)
        raise OSError(r"D:\private\runtime\missing.dll")

    monkeypatch.setattr(
        desktop_speech_module,
        "ManagedGptSovitsRuntime",
        _fail_runtime,
    )
    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    turn = coordinator.start_turn("request-failure", "chat-failure")
    assert runtime_started.wait(1.0)
    turn.feed("Text still succeeds.")
    turn.finish()
    allow_runtime_failure.set()

    assert coordinator.wait_until_settled(1.0)
    assert terminal.wait(1.0)
    assert coordinator.get_status().state == "unavailable"
    assert coordinator.get_status().available is False
    assert stream.closed
    assert bytes(stream.buffer) == b""
    assert events == [
        (
            "voice.speech.terminal",
            "request-failure",
            {
                "chatId": "chat-failure",
                "state": "cancelled",
                "submittedSentences": 0,
                "completedSentences": 0,
                "failedSentences": 0,
            },
        )
    ]
    assert "private" not in repr(events)
    coordinator.shutdown()
    coordinator.shutdown()
    turn.feed("Late text cannot reopen a retired turn.")
    turn.finish()
    assert not turn.cancel()
    assert len(events) == 1
    assert coordinator.get_status().state == "closed"


def test_shutdown_does_not_wait_for_a_blocked_delivery_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initiate cleanup without joining an Electron-facing notifier callback."""

    runtime = _FakeRuntime()
    synthesizer = _SequenceSynthesizer()
    _install_fake_bootstrap(monkeypatch, runtime, synthesizer)
    stream = _RecordingStream()
    callback_entered = Event()
    release_callback = Event()

    def _block_clip(
        name: str,
        _request_id: str,
        _data: dict[str, Any],
    ) -> None:
        """Model Electron pausing immediately after receiving clip metadata."""

        if name == "voice.speech.clip":
            callback_entered.set()
            assert release_callback.wait(2.0)

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _block_clip,
    )
    turn = coordinator.start_turn("request-shutdown", "chat-shutdown")
    assert coordinator.wait_until_settled(1.0)
    turn.feed("One sentence.")
    turn.finish()
    assert callback_entered.wait(1.0)

    coordinator.shutdown()
    assert coordinator.get_status().state == "closed"
    assert stream.closed
    assert runtime.shutdown_calls == 1
    release_callback.set()


def test_replacement_does_not_wait_for_an_older_delivery_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admit Unicode protocol IDs while replacing a callback-blocked turn."""

    runtime = _FakeRuntime()
    synthesizer = _SequenceSynthesizer()
    _install_fake_bootstrap(monkeypatch, runtime, synthesizer)
    stream = _RecordingStream()
    first_clip_entered = Event()
    release_first_clip = Event()
    replacement_returned = Event()
    second_terminal = Event()
    events: list[tuple[str, str]] = []

    def _block_first_clip(
        name: str,
        request_id: str,
        _data: dict[str, Any],
    ) -> None:
        """Keep the first callback across the exact replacement boundary."""

        if name == "voice.speech.clip" and request_id == "请求 one / 🙂":
            first_clip_entered.set()
            assert release_first_clip.wait(2.0)
        events.append((name, request_id))
        if name == "voice.speech.terminal" and request_id == "请求 two / 🙂":
            second_terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _block_first_clip,
    )
    first_turn = coordinator.start_turn("请求 one / 🙂", "chat one / 🙂")
    assert coordinator.wait_until_settled(1.0)
    first_turn.feed("First sentence.")
    first_turn.finish()
    assert first_clip_entered.wait(1.0)

    def _replace_turn() -> None:
        """Open and complete the new request without joining the old callback."""

        replacement = coordinator.start_turn("请求 two / 🙂", "chat two / 🙂")
        replacement.feed("Second sentence.")
        replacement.finish()
        replacement_returned.set()

    replacement_thread = Thread(target=_replace_turn)
    replacement_thread.start()
    try:
        assert replacement_returned.wait(0.2)
        release_first_clip.set()
        assert second_terminal.wait(2.0)
        assert ("voice.speech.clip", "请求 two / 🙂") in events
        assert ("voice.speech.terminal", "请求 two / 🙂") in events
    finally:
        release_first_clip.set()
        replacement_thread.join(1.0)
        coordinator.shutdown()


def test_pre_ready_replacements_emit_bounded_zero_work_terminals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retire every superseded startup turn before Electron reaches its cap."""

    runtime = _FakeRuntime(block_bootstrap=True)
    synthesizer = _SequenceSynthesizer()
    _install_fake_bootstrap(monkeypatch, runtime, synthesizer)
    stream = _RecordingStream()
    events: list[tuple[str, str, dict[str, Any]]] = []
    final_terminal = Event()

    def _record_event(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Capture exact zero-work terminals while native startup is blocked."""

        events.append((name, request_id, data))
        if name == "voice.speech.terminal" and request_id == "request-6":
            final_terminal.set()

    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        _record_event,
    )
    latest = coordinator.start_turn("request-0", "chat-0")
    assert runtime.acquire_started.wait(1.0)
    try:
        for index in range(1, 7):
            latest = coordinator.start_turn(
                f"request-{index}",
                f"chat-{index}",
            )
        latest.feed("Only the newest turn is spoken.")
        latest.finish()

        assert events == [
            (
                "voice.speech.terminal",
                f"request-{index}",
                {
                    "chatId": f"chat-{index}",
                    "state": "cancelled",
                    "submittedSentences": 0,
                    "completedSentences": 0,
                    "failedSentences": 0,
                },
            )
            for index in range(6)
        ]

        runtime.release_acquire.set()
        assert coordinator.wait_until_settled(1.0)
        assert final_terminal.wait(2.0)
        assert [request.text for request in synthesizer.requests] == [
            "Only the newest turn is spoken.",
        ]
        assert events[-1][0:2] == (
            "voice.speech.terminal",
            "request-6",
        )
        assert events[-1][2]["state"] == "completed"
    finally:
        runtime.release_acquire.set()
        coordinator.shutdown()


def test_pre_ready_sink_failure_cannot_resurrect_blocked_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep terminal speech failure final while acquisition finishes late."""

    runtime = _FakeRuntime(block_bootstrap=True)
    synthesizer = _SequenceSynthesizer()
    _install_fake_bootstrap(monkeypatch, runtime, synthesizer)
    queue_shutdown = Event()

    class _CleanupObservedQueue:
        """Expose completion of the late bootstrap queue rollback."""

        def __init__(self, _config: object) -> None:
            """Accept the production configuration without starting daemons."""

        def shutdown(self, **_kwargs: object) -> bool:
            """Record that the unpublished queue returned to its owner."""

            queue_shutdown.set()
            return True

    def fail_terminal_sink(
        name: str,
        request_id: str,
        data: dict[str, Any],
    ) -> None:
        """Fail the pre-ready terminal at the optional output boundary."""

        assert name == "voice.speech.terminal"
        assert request_id == "request-race"
        assert data["state"] == "cancelled"
        raise RuntimeError("private output failure")

    monkeypatch.setattr(
        desktop_speech_module,
        "SpeechSynthesisQueue",
        _CleanupObservedQueue,
    )
    stream = _RecordingStream()
    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        fail_terminal_sink,
    )
    turn = coordinator.start_turn("request-race", "chat-race")
    assert runtime.acquire_started.wait(1.0)
    try:
        assert turn.cancel()
        assert runtime.shutdown_called.wait(1.0)
        assert coordinator.get_status().state == "unavailable"
        assert stream.closed

        runtime.release_acquire.set()
        assert queue_shutdown.wait(1.0)

        assert coordinator.get_status().state == "unavailable"
        assert runtime.shutdown_calls == 1
        assert len(runtime.acquired) == 1
        assert synthesizer.requests == []
    finally:
        runtime.release_acquire.set()
        coordinator.shutdown()
    assert runtime.shutdown_calls == 1


def test_bootstrap_thread_start_failure_disables_only_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close the private writer if the optional bootstrap daemon cannot start."""

    class _StartFailingThread:
        """Represent an operating-system thread creation failure."""

        def __init__(self, **_kwargs: object) -> None:
            """Accept the production constructor without starting work."""

        def start(self) -> None:
            """Fail before the bootstrap target can acquire any resource."""

            raise RuntimeError("private thread creation detail")

    stream = _RecordingStream()
    monkeypatch.setattr(desktop_speech_module, "Thread", _StartFailingThread)
    coordinator = DesktopSpeechCoordinator(
        _config(tmp_path),
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        lambda *_event: None,
    )

    coordinator.start()

    assert coordinator.wait_until_settled(0.0)
    assert coordinator.get_status().state == "unavailable"
    assert stream.closed
    coordinator.shutdown()
    assert coordinator.get_status().state == "closed"


def test_cleanup_attempts_queue_and_runtime_after_writer_failure() -> None:
    """Release every independent owner even if an earlier cleanup raises."""

    class _FailingCloseStream(_RecordingStream):
        """Expose a deterministic writer close failure."""

        def close(self) -> None:
            """Record closure and fail as a damaged pipe implementation might."""

            self.closed = True
            raise OSError("private writer close detail")

    class _FailingQueue:
        """Expose a deterministic queue cleanup failure."""

        def __init__(self) -> None:
            """Initialize exact shutdown-call accounting."""

            self.shutdown_calls = 0

        def shutdown(self, *, wait: bool, cancel_pending: bool) -> None:
            """Verify non-joining cleanup and then raise."""

            assert wait is False
            assert cancel_pending is True
            self.shutdown_calls += 1
            raise RuntimeError("private queue cleanup detail")

    stream = _FailingCloseStream()
    queue = _FailingQueue()
    runtime = _FakeRuntime()

    DesktopSpeechCoordinator._release_optional_resources(
        AudioChannelWriter(stream),  # type: ignore[arg-type]
        cast(Any, queue),
        cast(Any, runtime),
    )

    assert stream.closed
    assert queue.shutdown_calls == 1
    assert runtime.shutdown_calls == 1
