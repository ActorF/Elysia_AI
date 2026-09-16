"""Coordinate managed sentence synthesis and private desktop audio delivery.

This composition boundary deliberately runs beside the canonical Chat stream.
It may segment, synthesize, cancel, or discard speech, but it never owns or
rewrites the Assistant text that ``Brain`` persists.  Model assets and runtime
paths remain in Python; Electron receives only validated event metadata and the
matching PCM WAV bytes through its inherited private pipe.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Any, Final, Literal, Protocol, TypeAlias, runtime_checkable

from config.settings import AppSettings
from desktop_protocol import AudioChannelWriter, ProtocolEventName
from voice.managed_gpt_sovits import (
    ManagedGptSovitsConfig,
    ManagedGptSovitsRuntime,
)
from voice.profiles import JsonVoiceProfileCatalog
from voice.speech_queue import (
    SpeechQueueClip,
    SpeechQueueConfig,
    SpeechQueueEvent,
    SpeechQueueFailure,
    SpeechSynthesisBindingLease,
    SpeechSynthesisQueue,
    SpeechTurn,
    SpeechTurnTerminal,
    _create_managed_synthesis_binding_lease,
)


logger = logging.getLogger(__name__)

DesktopSpeechState: TypeAlias = Literal[
    "idle",
    "starting",
    "ready",
    "unavailable",
    "closed",
]
DesktopSpeechEventSink: TypeAlias = Callable[
    [ProtocolEventName, str, dict[str, Any]],
    None,
]

_PENDING_TEXT_MAX_CODE_POINTS: Final = 4_096
_DEFAULT_PROFILE_ID: Final = "default"
_DEFAULT_EMOTION: Final = "neutral"
_DEFAULT_LANGUAGE: Final = "auto"
_LEASE_ID: Final = "desktop-managed-lease"
_CACHE_IDENTITY: Final = "desktop-managed-voice"


@dataclass(frozen=True, slots=True, repr=False)
class DesktopSpeechConfig:
    """Freeze private runtime locations and bounded managed-worker policy."""

    runtime_root: Path
    worker_script: Path
    catalog_path: Path
    asset_root: Path
    allow_local_evaluation: bool
    deterministic_seed: int
    synthesis_timeout_seconds: float

    @classmethod
    def from_app_settings(cls, settings: AppSettings) -> "DesktopSpeechConfig":
        """Derive every path from the trusted application root, never Renderer."""

        if not isinstance(settings, AppSettings):
            raise TypeError("settings must be an AppSettings instance.")
        base_dir = settings.base_dir.resolve()
        return cls(
            runtime_root=(
                base_dir / "models" / "cache" / "GPT-SoVITS-v2-240821"
            ),
            worker_script=base_dir / "scripts" / "gpt_sovits_worker.py",
            catalog_path=(
                base_dir / "workspace" / "settings" / "voice-profiles.json"
            ),
            asset_root=base_dir / "models" / "weights" / "gpt-sovits",
            allow_local_evaluation=settings.gpt_sovits_allow_local_evaluation,
            deterministic_seed=settings.gpt_sovits_deterministic_seed,
            synthesis_timeout_seconds=(
                settings.gpt_sovits_request_timeout_seconds
            ),
        )

    def __post_init__(self) -> None:
        """Reject caller-defined relative paths and mutable scalar subclasses."""

        paths = (
            self.runtime_root,
            self.worker_script,
            self.catalog_path,
            self.asset_root,
        )
        if any(not isinstance(path, Path) or not path.is_absolute() for path in paths):
            raise ValueError("Desktop speech paths must be absolute Path values.")
        if type(self.allow_local_evaluation) is not bool:
            raise TypeError("allow_local_evaluation must be a Boolean.")
        if (
            type(self.deterministic_seed) is not int
            or not 0 <= self.deterministic_seed <= 2_147_483_647
        ):
            raise ValueError("deterministic_seed is outside the managed range.")
        if (
            type(self.synthesis_timeout_seconds) is not float
            or not 0.1 <= self.synthesis_timeout_seconds <= 300.0
        ):
            raise ValueError("synthesis_timeout_seconds is outside the safe range.")

    def __repr__(self) -> str:
        """Expose policy without leaking private model or runtime locations."""

        return (
            f"{type(self).__name__}("
            f"allow_local_evaluation={self.allow_local_evaluation!r}, "
            f"synthesis_timeout_seconds={self.synthesis_timeout_seconds!r})"
        )


@dataclass(frozen=True, slots=True)
class DesktopSpeechStatus:
    """Describe readiness without paths, prompts, model names, or diagnostics."""

    state: DesktopSpeechState
    available: bool


@runtime_checkable
class DesktopSpeechTurn(Protocol):
    """Receive a copy of streamed reply chunks for optional local playback."""

    def feed(self, chunk: str) -> None:
        """Offer one exact Brain chunk without changing its canonical owner."""
        ...

    def finish(self) -> None:
        """Flush the final speech tail after Brain commits the complete reply."""
        ...

    def cancel(self) -> bool:
        """Mark this Chat request stale and cancel work not across delivery."""
        ...


@dataclass(slots=True)
class _TurnRecord:
    """Hold one pending or queue-backed turn behind the coordinator lock."""

    request_id: str
    chat_id: str
    queue_turn_id: str
    buffered_chunks: list[str] = field(default_factory=list, repr=False)
    buffered_code_points: int = 0
    delegate: SpeechTurn | None = field(default=None, repr=False)
    input_finished: bool = False
    cancelled: bool = False
    terminal_emitted: bool = False


class _DesktopSpeechTurnHandle:
    """Route one generation's speech copy through its exact coordinator record."""

    __slots__ = ("_coordinator", "_record")

    def __init__(
        self,
        coordinator: "DesktopSpeechCoordinator",
        record: _TurnRecord,
    ) -> None:
        self._coordinator = coordinator
        self._record = record

    def feed(self, chunk: str) -> None:
        """Offer one chunk; speech admission failure remains text-independent."""

        self._coordinator._feed_turn(self._record, chunk)

    def finish(self) -> None:
        """Close sentence input exactly once for the matching record."""

        self._coordinator._finish_turn(self._record)

    def cancel(self) -> bool:
        """Cancel only the matching record and report whether it was active."""

        return self._coordinator._cancel_turn(self._record)


class DesktopSpeechCoordinator:
    """Own managed TTS bootstrap, bounded FIFO work, and fd3 frame delivery.

    Bootstrap runs on a daemon so missing or slow optional voice assets cannot
    delay text Chat initialization.  At most one pre-ready turn buffers a small
    fixed amount of text; once readiness settles, the existing sentence queue
    provides all synthesis, cancellation, and delivery accounting.
    """

    def __init__(
        self,
        config: DesktopSpeechConfig,
        audio_writer: AudioChannelWriter,
        event_sink: DesktopSpeechEventSink,
    ) -> None:
        """Take exclusive ownership of one private audio writer without I/O."""

        if not isinstance(config, DesktopSpeechConfig):
            raise TypeError("config must be a DesktopSpeechConfig.")
        if not isinstance(audio_writer, AudioChannelWriter):
            raise TypeError("audio_writer must be an AudioChannelWriter.")
        if not callable(event_sink):
            raise TypeError("event_sink must be callable.")
        self._config = config
        self._audio_writer: AudioChannelWriter | None = audio_writer
        self._event_sink = event_sink
        self._lock = RLock()
        self._turn_transition_lock = Lock()
        self._state: DesktopSpeechState = "idle"
        self._settled = Event()
        self._runtime: ManagedGptSovitsRuntime | None = None
        self._queue: SpeechSynthesisQueue | None = None
        self._binding: SpeechSynthesisBindingLease | None = None
        self._active_turn: _TurnRecord | None = None
        self._bootstrap_thread: Thread | None = None

    def get_status(self) -> DesktopSpeechStatus:
        """Return a safe in-memory readiness snapshot without probing disk."""

        with self._lock:
            state = self._state
        return DesktopSpeechStatus(state=state, available=state == "ready")

    def start(self) -> None:
        """Begin one lazy managed-runtime acquisition and never retry it."""

        start_failed = False
        with self._lock:
            if self._state != "idle":
                return
            self._state = "starting"
            bootstrap = Thread(
                target=self._bootstrap,
                name="elysia-desktop-speech-bootstrap",
                daemon=True,
            )
            self._bootstrap_thread = bootstrap
            try:
                bootstrap.start()
            except BaseException:
                self._bootstrap_thread = None
                start_failed = True
        if start_failed:
            # Thread creation is optional-voice infrastructure failure, not a
            # reason to fail Backend initialization or retain the fd3 writer.
            self._mark_unavailable()

    def start_turn(self, request_id: str, chat_id: str) -> DesktopSpeechTurn:
        """Replace stale playback and open one text-independent speech turn."""

        if type(request_id) is not str or type(chat_id) is not str:
            raise TypeError("request_id and chat_id must be strings.")
        with self._turn_transition_lock:
            self.start()
            # Protocol identifiers intentionally accept a wider Unicode
            # surface than the queue's internal identifier grammar. A fresh
            # opaque bridge ID preserves that contract without request text.
            record = _TurnRecord(
                request_id=request_id,
                chat_id=chat_id,
                queue_turn_id=f"desktop-{secrets.token_hex(16)}",
            )
            with self._lock:
                previous, self._active_turn = self._active_turn, None
            if previous is not None:
                self._cancel_turn(previous)

            delegate_to_cancel: SpeechTurn | None = None
            with self._lock:
                if self._state in ("unavailable", "closed"):
                    record.cancelled = True
                else:
                    self._active_turn = record
                    if self._state == "ready":
                        delegate_to_cancel = self._attach_delegate_locked(record)
            if delegate_to_cancel is not None:
                self._cancel_delegate(delegate_to_cancel)
            elif record.cancelled:
                self._emit_undelivered_cancel(record)
            return _DesktopSpeechTurnHandle(self, record)

    def wait_until_settled(self, timeout_seconds: float | None = None) -> bool:
        """Wait for ready/unavailable/closed state for tests and diagnostics."""

        if timeout_seconds is not None and (
            type(timeout_seconds) is not float or timeout_seconds < 0.0
        ):
            raise ValueError("timeout_seconds must be a non-negative float.")
        return self._settled.wait(timeout_seconds)

    def shutdown(self) -> None:
        """Stop admission and initiate bounded queue, worker, and pipe cleanup."""

        with self._lock:
            if self._state == "closed":
                return
            self._state = "closed"
            self._settled.set()
            active, self._active_turn = self._active_turn, None
            if active is not None:
                # Queue shutdown below performs non-blocking cancellation.
                # The ordinary turn method could wait behind a pipe write after
                # Electron has already begun its own teardown.
                active.cancelled = True
                active.buffered_chunks.clear()
                active.buffered_code_points = 0
            queue, self._queue = self._queue, None
            runtime, self._runtime = self._runtime, None
            writer, self._audio_writer = self._audio_writer, None
            self._binding = None
        self._release_optional_resources(writer, queue, runtime)

    @staticmethod
    def _release_optional_resources(
        writer: AudioChannelWriter | None,
        queue: SpeechSynthesisQueue | None,
        runtime: ManagedGptSovitsRuntime | None,
    ) -> None:
        """Attempt every independent cleanup even when an earlier owner fails."""

        if writer is not None:
            try:
                writer.close()
            except BaseException:
                logger.error("Desktop speech audio channel did not close cleanly.")
        if queue is not None:
            try:
                queue.shutdown(wait=False, cancel_pending=True)
            except BaseException:
                logger.error("Desktop speech queue did not close cleanly.")
        if runtime is not None:
            try:
                runtime.shutdown()
            except BaseException:
                logger.error("Desktop speech runtime did not close cleanly.")

    def _bootstrap(self) -> None:
        """Acquire a fixed catalog selection without blocking Backend startup."""

        runtime: ManagedGptSovitsRuntime | None = None
        queue: SpeechSynthesisQueue | None = None
        try:
            runtime = ManagedGptSovitsRuntime(
                ManagedGptSovitsConfig(
                    runtime_root=self._config.runtime_root,
                    worker_script=self._config.worker_script,
                    synthesis_timeout_seconds=(
                        self._config.synthesis_timeout_seconds
                    ),
                    device="cuda",
                    seed=self._config.deterministic_seed,
                )
            )
            with self._lock:
                publish_runtime = self._state == "starting"
                if publish_runtime:
                    self._runtime = runtime
            if not publish_runtime:
                # This runtime has not crossed the coordinator ownership
                # boundary. A concurrent terminal failure or shutdown therefore
                # cannot have detached it and the bootstrap thread must clean it.
                self._release_optional_resources(None, None, runtime)
                return
            catalog = JsonVoiceProfileCatalog.load(
                self._config.catalog_path,
                self._config.asset_root,
                allow_local_evaluation=self._config.allow_local_evaluation,
            )
            selection = catalog.resolve_selection(
                _DEFAULT_PROFILE_ID,
                _DEFAULT_EMOTION,
            )
            runtime_lease = runtime.acquire_lease(selection)
            binding = _create_managed_synthesis_binding_lease(
                lease_id=_LEASE_ID,
                cache_identity=_CACHE_IDENTITY,
                runtime_lease=runtime_lease,
                profile_id=selection.profile_id,
                emotion=selection.emotion,
                language=_DEFAULT_LANGUAGE,
                on_invalidated=self._mark_unavailable,
            )
            queue = SpeechSynthesisQueue(
                SpeechQueueConfig(
                    max_active_turns=2,
                    max_pending_sentences=8,
                    max_delivery_events=2,
                    max_delivery_bytes=64 * 1024 * 1024,
                    max_cache_entries=0,
                    max_cache_bytes=0,
                    max_retained_turns=16,
                )
            )
            with self._lock:
                publish_queue = self._state == "starting"
                if publish_queue:
                    self._binding = binding
                    self._queue = queue
                    self._state = "ready"
                    active = self._active_turn
                    delegate_to_cancel: SpeechTurn | None = None
                    if active is not None and not active.cancelled:
                        delegate_to_cancel = self._attach_delegate_locked(active)
                    self._settled.set()
            if not publish_queue:
                # Runtime ownership crossed the first checkpoint. Any state
                # transition away from ``starting`` detached and shut it down;
                # only this not-yet-published queue remains ours to release.
                self._release_optional_resources(None, queue, None)
                return
            if delegate_to_cancel is not None:
                self._cancel_delegate(delegate_to_cancel)
            elif active is not None and active.cancelled:
                self._emit_undelivered_cancel(active)
        except BaseException:
            self._release_optional_resources(None, queue, runtime)
            self._mark_unavailable()

    def _attach_delegate_locked(
        self,
        record: _TurnRecord,
    ) -> SpeechTurn | None:
        """Transfer startup text and return failed work for unlocked cleanup.

        The caller owns ``self._lock``.  Cancellation can wait for a delivery
        callback that also needs this lock, so cleanup is deliberately returned
        to the caller instead of running within the critical section.
        """

        queue = self._queue
        binding = self._binding
        if queue is None or binding is None or record.cancelled:
            return None
        try:
            delegate = queue.start_turn(
                record.queue_turn_id,
                binding,
                lambda event: self._on_queue_event(record, event),
            )
            record.delegate = delegate
            buffered = tuple(record.buffered_chunks)
            record.buffered_chunks.clear()
            record.buffered_code_points = 0
            for chunk in buffered:
                delegate.feed(chunk)
            if record.input_finished:
                delegate.finish()
            return None
        except BaseException:
            record.cancelled = True
            record.buffered_chunks.clear()
            record.buffered_code_points = 0
            if self._active_turn is record:
                self._active_turn = None
            return record.delegate

    @staticmethod
    def _cancel_delegate(delegate: SpeechTurn) -> None:
        """Best-effort cancel optional queue work outside coordinator locks."""

        try:
            delegate.cancel_nowait()
        except BaseException:
            # A failed optional queue is subsequently retired by its normal
            # shutdown path; text Chat must not inherit the failure.
            pass

    def _feed_turn(self, record: _TurnRecord, chunk: str) -> None:
        """Feed speech if admitted, dropping only speech on capacity failure."""

        if type(chunk) is not str:
            raise TypeError("chunk must be a string.")
        delegate: SpeechTurn | None = None
        cancelled_without_delegate = False
        with self._lock:
            if (
                record is not self._active_turn
                or record.cancelled
                or record.input_finished
            ):
                return
            delegate = record.delegate
            if delegate is None:
                next_size = record.buffered_code_points + len(chunk)
                if (
                    len(chunk) > _PENDING_TEXT_MAX_CODE_POINTS
                    or next_size > _PENDING_TEXT_MAX_CODE_POINTS
                ):
                    record.cancelled = True
                    record.buffered_chunks.clear()
                    record.buffered_code_points = 0
                    self._active_turn = None
                    cancelled_without_delegate = True
                else:
                    record.buffered_chunks.append(chunk)
                    record.buffered_code_points = next_size
                    return
            if delegate is not None:
                try:
                    delegate.feed(chunk)
                except BaseException:
                    record.cancelled = True
                    self._active_turn = None
        if cancelled_without_delegate:
            self._emit_undelivered_cancel(record)
            return
        if record.cancelled and delegate is not None:
            self._cancel_delegate(delegate)

    def _finish_turn(self, record: _TurnRecord) -> None:
        """Flush a ready delegate or remember closure during lazy bootstrap."""

        delegate: SpeechTurn | None = None
        with self._lock:
            if (
                record is not self._active_turn
                or record.cancelled
                or record.input_finished
            ):
                return
            record.input_finished = True
            delegate = record.delegate
            if delegate is None:
                return
            try:
                delegate.finish()
            except BaseException:
                record.cancelled = True
                self._active_turn = None
        if record.cancelled and delegate is not None:
            self._cancel_delegate(delegate)

    def _cancel_turn(self, record: _TurnRecord) -> bool:
        """Mark one record stale and cancel without joining callbacks.

        A clip callback that already crossed the queue's delivery boundary can
        finish later.  The Electron delivery owner independently rejects stale
        or terminal turns, so replacing a Chat never waits for that callback.
        """

        with self._lock:
            if record.cancelled:
                return False
            record.cancelled = True
            record.buffered_chunks.clear()
            record.buffered_code_points = 0
            if self._active_turn is record:
                self._active_turn = None
            delegate = record.delegate
        if delegate is not None:
            self._cancel_delegate(delegate)
        else:
            self._emit_undelivered_cancel(record)
        return True

    def _emit_undelivered_cancel(self, record: _TurnRecord) -> None:
        """Retire one turn that never acquired a queue-owned terminal event."""

        with self._lock:
            if (
                not record.cancelled
                or record.delegate is not None
                or record.terminal_emitted
            ):
                return
            record.terminal_emitted = True
        try:
            self._event_sink(
                "voice.speech.terminal",
                record.request_id,
                {
                    "chatId": record.chat_id,
                    "state": "cancelled",
                    "submittedSentences": 0,
                    "completedSentences": 0,
                    "failedSentences": 0,
                },
            )
        except BaseException:
            self._mark_unavailable()

    def _on_queue_event(
        self,
        record: _TurnRecord,
        event: SpeechQueueEvent,
    ) -> None:
        """Translate one queue event and pair clip metadata before fd3 bytes."""

        try:
            if isinstance(event, SpeechQueueClip):
                with self._lock:
                    writer = self._audio_writer
                    if self._state != "ready" or writer is None:
                        return
                frame = writer.prepare_wav(event.result.audio, event.sequence)
                data = {"chatId": record.chat_id, **frame.metadata()}
                try:
                    self._event_sink(
                        "voice.speech.clip",
                        record.request_id,
                        data,
                    )
                except BaseException:
                    writer.discard_frame(frame)
                    raise
                writer.write_frame(frame)
                return
            if isinstance(event, SpeechQueueFailure):
                self._event_sink(
                    "voice.speech.failure",
                    record.request_id,
                    {
                        "chatId": record.chat_id,
                        "sequence": event.sequence,
                        "code": event.code,
                    },
                )
                return
            if not isinstance(event, SpeechTurnTerminal):
                raise TypeError("Speech queue emitted an unknown event.")
            self._event_sink(
                "voice.speech.terminal",
                record.request_id,
                {
                    "chatId": record.chat_id,
                    "state": event.state,
                    "submittedSentences": event.submitted_sentences,
                    "completedSentences": event.completed_sentences,
                    "failedSentences": event.failed_sentences,
                },
            )
            with self._lock:
                if self._active_turn is record:
                    self._active_turn = None
        except BaseException:
            self._mark_unavailable()

    def _mark_unavailable(self) -> None:
        """Disable a failed optional speech path while preserving text Chat."""

        undelivered: _TurnRecord | None = None
        with self._lock:
            if self._state in ("unavailable", "closed"):
                return
            self._state = "unavailable"
            self._settled.set()
            active, self._active_turn = self._active_turn, None
            if active is not None:
                # This path can run from the queue notifier itself. Local state
                # plus non-blocking queue shutdown cannot wait on that callback.
                active.cancelled = True
                active.buffered_chunks.clear()
                active.buffered_code_points = 0
                if active.delegate is None:
                    # Bootstrap can fail on either side of start_turn's state
                    # check. Retire pre-queue work in both schedules so the
                    # observable lifecycle never depends on thread timing.
                    undelivered = active
            queue, self._queue = self._queue, None
            runtime, self._runtime = self._runtime, None
            writer, self._audio_writer = self._audio_writer, None
            self._binding = None
        self._release_optional_resources(writer, queue, runtime)
        if undelivered is not None:
            self._emit_undelivered_cancel(undelivered)
        logger.error("Optional managed desktop speech became unavailable.")


__all__ = [
    "DesktopSpeechConfig",
    "DesktopSpeechCoordinator",
    "DesktopSpeechEventSink",
    "DesktopSpeechState",
    "DesktopSpeechStatus",
    "DesktopSpeechTurn",
]
