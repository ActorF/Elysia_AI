"""Verify sentence-scoped desktop GPU ownership and authoritative TTS cleanup.

Inert supervisors expose launch, attestation, cancellation, and physical drain
ordering without starting Python workers, loading voice weights, or using GPU.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable, Iterator, cast
import wave

import pytest

import desktop_speech
from desktop_protocol import AudioChannelWriter
from gpu_admission import GpuAdmissionCancelledError
from voice import gpu_synthesis
from voice import managed_gpt_sovits as managed
from voice.gpu_synthesis import _GpuSentenceSynthesisLease
from voice.managed_gpt_sovits import (
    ManagedGptSovitsCancelledError, ManagedGptSovitsConfig,
    ManagedGptSovitsUnavailableError, wait_for_managed_cleanup,
)
from voice.speech_queue import _create_managed_synthesis_binding_lease
from voice.synthesis import SynthesisResult


_TOKEN = "a" * 64
_NEXT_TOKEN = "b" * 64


def _audio() -> SynthesisResult:
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(32_000)
        output.writeframes(b"\x00\x00" * 8)
    return SynthesisResult(stream.getvalue(), "wav", 1.0)


def _config(root: Path) -> ManagedGptSovitsConfig:
    return ManagedGptSovitsConfig(
        runtime_root=root / "runtime", worker_script=root / "worker.py", stop_timeout_seconds=0.2,
    )


class _FakeGpu:
    """Record release or quarantine independently of logical cancellation."""

    def __init__(self, events: list[str], cancel_requested: Callable[[], bool] | None) -> None:
        """Retain a cooperative stop probe and observable grant lifetime."""

        self.events = events
        self.cancel_requested = cancel_requested
        self.poisoned = False

    @property
    def cancelled(self) -> bool:
        """Report only the supplied sentence's cancellation probe."""

        return self.cancel_requested is not None and self.cancel_requested()

    def poison(self) -> None:
        """Keep this grant occupied when native cleanup is unproven."""

        self.poisoned = True
        self.events.append("poison")


class _FakeLease:
    """Expose exact-token synthesis with optional cancellation blocking."""

    def __init__(self, events: list[str], *, blocked: bool = False) -> None:
        """Initialize a deterministic audio result and a native completion gate."""

        self.events = events
        self.blocked = blocked
        self.started = Event()
        self.release = Event()
        self.tokens: list[str] = []
        self.abort_tokens: list[str] = []

    def synthesize(self, text: str, language: str, operation_token: str) -> SynthesisResult:
        """Record the operation token and optionally wait for exact abort."""

        self.events.append("synthesize")
        self.tokens.append(operation_token)
        self.started.set()
        if self.blocked:
            assert self.release.wait(2.0)
        return _audio()

    def abort(self, operation_token: str) -> bool:
        """Release only an exact active native synthesis token."""

        self.events.append("abort")
        self.abort_tokens.append(operation_token)
        if operation_token in self.tokens:
            self.release.set()
            return True
        return False


class _FakeRuntime:
    """Separate supervisor construction, attestation, and native shutdown."""

    def __init__(self, events: list[str], *, block_startup: bool = False, blocked_audio: bool = False,
                 fail_shutdown: bool = False) -> None:
        """Configure explicit startup, synthesis, and cleanup outcomes."""

        self.events = events
        self.lease = _FakeLease(events, blocked=blocked_audio)
        self.block_startup = block_startup
        self.fail_shutdown = fail_shutdown
        self.acquire_started = Event()
        self.release_acquire = Event()
        self.shutdown_called = Event()
        self.selections: list[object] = []

    def acquire_lease(self, selection: object) -> _FakeLease:
        """Attest one fixed voice after an optional native startup gate."""

        self.events.append("attest")
        self.selections.append(selection)
        self.acquire_started.set()
        if self.block_startup:
            assert self.release_acquire.wait(2.0)
        return self.lease

    def shutdown(self) -> None:
        """Prove native stop or explicitly fail the physical cleanup boundary."""

        self.events.append("shutdown")
        self.shutdown_called.set()
        self.release_acquire.set()
        if self.fail_shutdown:
            raise RuntimeError("private native cleanup detail")


def _install(monkeypatch: pytest.MonkeyPatch, events: list[str], runtime: _FakeRuntime | None = None,
             *, cleanup_complete: bool = True) -> list[_FakeRuntime]:
    created: list[_FakeRuntime] = []

    def _runtime(_config: object) -> _FakeRuntime:
        events.append("construct")
        value = runtime if runtime is not None else _FakeRuntime(events)
        created.append(value)
        return value

    @contextmanager
    def _gpu(owner: str, *, cancel_requested: Callable[[], bool] | None = None) -> Iterator[_FakeGpu]:
        assert owner == "gpt-sovits"
        events.append("grant")
        grant = _FakeGpu(events, cancel_requested)
        try:
            yield grant
        finally:
            events.append("retained" if grant.poisoned else "release")

    def _drain(_timeout: float) -> bool:
        events.append("drain")
        return cleanup_complete

    monkeypatch.setattr(gpu_synthesis, "ManagedGptSovitsRuntime", _runtime)
    monkeypatch.setattr(gpu_synthesis, "gpu_lease", _gpu)
    monkeypatch.setattr(gpu_synthesis, "wait_for_managed_cleanup", _drain)
    return created


def _synthesis_thread(lease: _GpuSentenceSynthesisLease, token: str = _TOKEN) -> tuple[Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            lease.synthesize("A complete sentence.", "auto", token)
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=_run)
    thread.start()
    return thread, errors


def test_each_sentence_drains_a_fresh_worker_before_releasing_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No idle TTS weights survive the global grant that launched their worker."""

    events: list[str] = []
    created = _install(monkeypatch, events)
    selection = object()
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), selection)
    assert events == []
    lease.synthesize("One sentence.", "auto", _TOKEN)
    lease.synthesize("Another sentence.", "zh", _NEXT_TOKEN)
    expected = ["grant", "construct", "attest", "synthesize", "shutdown", "drain", "release"]
    assert events == expected + expected
    assert len(created) == 2 and created[0] is not created[1]
    assert created[0].selections == [selection]
    assert created[0].lease.tokens == [_TOKEN]
    assert created[1].lease.tokens == [_NEXT_TOKEN]
    assert lease.cache_eligible is False
    assert lease.poisoned is False


def test_lazy_binding_is_owned_and_noncacheable_without_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A controlled private lazy supervisor can bind a queue before its GPU turn."""

    events: list[str] = []
    _install(monkeypatch, events)
    wrapper = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    binding = _create_managed_synthesis_binding_lease(
        lease_id="gpu-test", cache_identity="gpu-test-voice", runtime_lease=wrapper,
    )
    assert binding.binding_trust == "elysia-owned"
    assert binding.cache_eligible is False
    assert events == []


def test_prestart_cancel_tombstone_prevents_gpu_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An abort before runtime registration cannot later start a model process."""

    events: list[str] = []
    _install(monkeypatch, events)
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    assert lease.abort(_TOKEN) is True
    assert lease.abort(_TOKEN) is False
    with pytest.raises(ManagedGptSovitsCancelledError):
        lease.synthesize("Stale sentence.", "auto", _TOKEN)
    assert events == []
    assert lease.abort(_TOKEN) is False
    lease.synthesize("Current sentence.", "auto", _NEXT_TOKEN)
    assert "synthesize" in events


def test_cancel_while_waiting_for_gpu_starts_no_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker wait observes sentence cancellation before physical startup."""

    events: list[str] = []
    _install(monkeypatch, events)
    waiting = Event()

    @contextmanager
    def _waiting_gpu(owner: str, *, cancel_requested: Callable[[], bool]) -> Iterator[object]:
        waiting.set()
        while not cancel_requested():
            Event().wait(0.005)
        raise GpuAdmissionCancelledError("cancelled")
        yield  # pragma: no cover

    monkeypatch.setattr(gpu_synthesis, "gpu_lease", _waiting_gpu)
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    thread, errors = _synthesis_thread(lease)
    assert waiting.wait(1.0)
    lease.abort(_TOKEN)
    thread.join(1.0)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ManagedGptSovitsCancelledError)
    assert events == []


def test_cancel_during_attestation_closes_starting_runtime_before_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup cancellation wakes READY and never enters native synthesis."""

    events: list[str] = []
    runtime = _FakeRuntime(events, block_startup=True)
    _install(monkeypatch, events, runtime)
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    thread, errors = _synthesis_thread(lease)
    assert runtime.acquire_started.wait(1.0)
    assert lease.abort(_TOKEN)
    thread.join(1.0)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ManagedGptSovitsCancelledError)
    assert "synthesize" not in events
    assert events[-2:] == ["drain", "release"]
    assert runtime.shutdown_called.is_set()
    assert lease.poisoned is False


def test_active_abort_routes_only_exact_token_and_drops_late_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale cancellation cannot kill the sentence currently holding the GPU."""

    events: list[str] = []
    runtime = _FakeRuntime(events, blocked_audio=True)
    _install(monkeypatch, events, runtime)
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    thread, errors = _synthesis_thread(lease)
    assert runtime.lease.started.wait(1.0)
    assert lease.abort(_NEXT_TOKEN)
    assert runtime.lease.abort_tokens == []
    assert lease.abort(_TOKEN)
    thread.join(1.0)
    assert runtime.lease.abort_tokens == [_TOKEN]
    assert len(errors) == 1 and isinstance(errors[0], ManagedGptSovitsCancelledError)
    assert events[-3:] == ["shutdown", "drain", "release"]
    assert lease.poisoned is False


@pytest.mark.parametrize("failure", ["shutdown", "cleanup-proof"])
def test_unproven_native_cleanup_poisons_gpu_until_backend_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """Neither a silent asynchronous failure nor a shutdown exception frees GPU."""

    events: list[str] = []
    runtime = _FakeRuntime(events, fail_shutdown=failure == "shutdown")
    _install(monkeypatch, events, runtime, cleanup_complete=failure != "cleanup-proof")
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    with pytest.raises(ManagedGptSovitsUnavailableError):
        lease.synthesize("One sentence.", "auto", _TOKEN)
    assert lease.poisoned
    assert events[-2:] == ["poison", "retained"]
    assert "release" not in events
    before = list(events)
    with pytest.raises(ManagedGptSovitsUnavailableError):
        lease.synthesize("Another sentence.", "auto", _NEXT_TOKEN)
    assert events == before


def test_shutdown_cancels_current_sentence_and_prevents_future_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coordinator cleanup drains the active physical owner without an idle child."""

    events: list[str] = []
    runtime = _FakeRuntime(events, blocked_audio=True)
    _install(monkeypatch, events, runtime)
    lease = _GpuSentenceSynthesisLease(_config(tmp_path), object())
    thread, errors = _synthesis_thread(lease)
    assert runtime.lease.started.wait(1.0)
    lease.shutdown()
    thread.join(1.0)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ManagedGptSovitsCancelledError)
    assert events[-1] == "release"
    with pytest.raises(ManagedGptSovitsUnavailableError):
        lease.synthesize("After close.", "auto", _NEXT_TOKEN)


@dataclass(frozen=True)
class _Selection:
    """Expose only the trusted selection fields the coordinator freezes."""

    profile_id: str = "fixture-voice"
    emotion: str = "neutral"
    speed_factor: float = 1.0


class _Catalog:
    """Resolve a private voice without launching or attesting any worker."""

    def resolve_selection(self, profile_id: str, emotion: str) -> _Selection:
        """Return one immutable fixture selection from the trusted catalog seam."""

        return _Selection()


def test_desktop_bootstrap_has_no_gpu_residency_before_first_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production broker mode resolves voice metadata while leaving GPU unowned."""

    events: list[str] = []
    _install(monkeypatch, events)
    monkeypatch.setattr(desktop_speech, "using_desktop_gpu", lambda: True)
    monkeypatch.setattr(desktop_speech.JsonVoiceProfileCatalog, "load", lambda *_args, **_kwargs: _Catalog())
    terminal = Event()

    def _event(name: str, _request: str, _data: dict[str, Any]) -> None:
        if name == "voice.speech.terminal":
            terminal.set()

    config = desktop_speech.DesktopSpeechConfig(
        runtime_root=tmp_path / "runtime", worker_script=tmp_path / "worker.py",
        catalog_path=tmp_path / "catalog.json", asset_root=tmp_path / "assets",
        voice_profile_id="default", voice_emotion="neutral", speech_rate_percent=100,
        allow_local_evaluation=True, deterministic_seed=42, synthesis_timeout_seconds=1.0,
    )
    coordinator = desktop_speech.DesktopSpeechCoordinator(config, AudioChannelWriter(BytesIO()), _event)
    try:
        coordinator.start()
        assert coordinator.wait_until_settled(1.0)
        assert coordinator.get_status().state == "ready"
        assert events == []
        turn = coordinator.start_turn("gpu-request", "gpu-chat")
        turn.feed("This complete spoken sentence is long enough for immediate delivery.")
        turn.finish()
        assert terminal.wait(2.0)
        assert events == ["grant", "construct", "attest", "synthesize", "shutdown", "drain", "release"]
    finally:
        coordinator.shutdown()


def test_cleanup_proof_waits_for_actual_async_barrier_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Logical runtime closure alone cannot release an unfinished process owner."""

    monkeypatch.setattr(managed, "_GLOBAL_CLEANUP_PENDING", 0)
    monkeypatch.setattr(managed, "_GLOBAL_CLEANUP_POISONED", False)
    managed._begin_cleanup_barrier()
    assert wait_for_managed_cleanup(0.01) is False
    completed = Event()
    results: list[bool] = []

    def _wait() -> None:
        results.append(wait_for_managed_cleanup(1.0))
        completed.set()

    thread = Thread(target=_wait)
    thread.start()
    assert not completed.wait(0.01)
    assert managed._complete_cleanup_barrier()
    assert completed.wait(1.0)
    thread.join(1.0)
    assert results == [True]


def test_cleanup_quarantine_never_proves_gpu_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """A permanent native poison remains a failure after pending count reaches zero."""

    monkeypatch.setattr(managed, "_GLOBAL_CLEANUP_PENDING", 0)
    monkeypatch.setattr(managed, "_GLOBAL_CLEANUP_POISONED", True)
    assert wait_for_managed_cleanup(0.0) is False


@pytest.mark.parametrize("timeout", [-1.0, float("inf"), float("nan"), True, 1])
def test_cleanup_proof_rejects_invalid_wait_policy(timeout: object) -> None:
    """A malformed timeout cannot create an unbounded or ambiguous cleanup wait."""

    with pytest.raises(ValueError):
        wait_for_managed_cleanup(cast(float, timeout))
