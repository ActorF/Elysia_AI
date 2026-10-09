"""Expose the existing Python Brain to Electron over newline-delimited JSON.

The bridge owns no Chat or Memory files. It validates small typed requests,
delegates all conversation work to Brain, and emits one JSON object per line
so Electron can monitor startup and forward streaming reply chunks.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from io import TextIOWrapper
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Any, Literal, TextIO, cast
from urllib.request import Request, urlopen

from attachments import (
    AttachmentConflictError,
    AttachmentNotFoundError,
    AttachmentRepository,
    AttachmentScope,
    AttachmentService,
    AttachmentState,
    AttachmentStorageError,
    AttachmentValidationError,
    JsonAttachmentStore,
)
from chats import (
    AttachmentId,
    AttachmentMetadata,
    ChatGroundedAnswer,
    ChatGroundedTableLocation,
    ChatId,
    ChatMessageId,
    ChatNotFoundError,
    ChatSession,
    ChatSessionMeta,
    ConversationMode,
    ProjectId,
)
from config.desktop_settings import (
    DesktopSettingsConflictError,
    DesktopSettingsRepository,
    DesktopSettingsStorageError,
    DesktopSettingsValidationError,
    DesktopSettingsSnapshot,
    EditableDesktopSettings,
    apply_editable_settings,
    apply_live_editable_settings,
    changed_setting_names,
    create_desktop_settings_repository,
    editable_from_app_settings,
    validate_transcription_device,
    validate_transcription_model,
)
from config.settings import AppSettings, SETTINGS, VoiceEmotion
from core import (
    Brain,
    ChatBusyError,
    ChatRetryTargetError,
    GenerationCancelledError,
)
from desktop_knowledge import (
    DesktopKnowledgeRuntime,
    create_desktop_knowledge_runtime,
)
from documents import (
    DocumentError,
    GroundedAnswerError,
    GroundedAnswerUnavailableError,
    RetrievalUnavailableError,
)
from knowledge_lifecycle import (
    KnowledgeExportCancelledError,
    KnowledgeLifecycleConflictError,
    KnowledgeLifecycleError,
    KnowledgeLifecycleNotFoundError,
    KnowledgeLifecycleRecoveryError,
    KnowledgeLifecycleStorageError,
    KnowledgeLifecycleValidationError,
    KnowledgeOperationSnapshot,
    KnowledgeSourceView,
)
from project_sources import (
    ProjectSourceAuthorizationError,
    ProjectSourceConflictError,
    ProjectSourceError,
    ProjectSourceNotFoundError,
    ProjectSourceStaleError,
    ProjectSourceStorageError,
    ProjectSourceValidationError,
)
from projects import (
    Project,
    ProjectArchivedError,
    ProjectChatBusyError,
    ProjectNotFoundError,
)
from desktop_protocol import (
    AudioChannelError,
    AudioChannelWriter,
    MAX_MESSAGE_LENGTH,
    MAX_PROTOCOL_FRAME_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ClientRequest,
    ProtocolEventName,
    ProtocolValidationError,
    ServerMessage,
    build_error_response,
    build_event,
    build_progress,
    build_stream_chunk,
    build_success_response,
    parse_client_request,
)
from desktop_speech import (
    DesktopSpeechConfig,
    DesktopSpeechCoordinator,
    DesktopSpeechStatus,
    DesktopSpeechTurn,
)
from start import create_brain, validate_settings
from voice import (
    FasterWhisperConfig,
    FasterWhisperStatus,
    FasterWhisperTranscriber,
    TranscriptionJobCallback,
    TranscriptionJobCapacityError,
    TranscriptionJobClosedError,
    TranscriptionJobConflictError,
    TranscriptionJobNotFoundError,
    TranscriptionJobRunner,
    TranscriptionJobRunnerConfig,
    TranscriptionJobSnapshot,
    TranscriptionLanguage,
    TranscriptionRequest,
    TranscriptionValidationError,
    VOICE_CAPTURE_CHANNEL_COUNT,
    VOICE_CAPTURE_SAMPLE_FORMAT,
    VoiceCapture,
    VoiceCaptureValidationError,
    JsonVoiceProfileCatalog,
    SynthesisUnavailableError,
    VoiceProfileCatalogError,
    VoiceSettingsConflictError,
    VoiceSettingsService,
    VoiceSettingsSnapshot,
    VoiceSettingsStorageError,
    VoiceSettingsValidationError,
    create_voice_settings_service,
)

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]
BrainFactory = Callable[[], Brain]
ModelLoader = Callable[[], tuple[str, ...]]
SettingsValidator = Callable[[], None]
TranscriptionRunnerFactory = Callable[
    [TranscriptionJobCallback],
    TranscriptionJobRunner,
]
KnowledgeRuntimeFactory = Callable[
    [AppSettings, AttachmentService],
    DesktopKnowledgeRuntime,
]
SpeechSelectionValidator = Callable[[AppSettings], bool]

SERVER_NAME = "elysia-python"
SERVER_VERSION = "0.1.0"
SERVER_CAPABILITIES = (
    "chat.sessions",
    "chat.stream",
    "chat.retry",
    "request.cancel",
    "project.management",
    "settings.management",
    "voice.settings",
    "voice.capture",
    "voice.transcription",
    "voice.speech",
    "voice.speech.start",
    "voice.speech.cancel",
    "attachment.management",
    "knowledge.management",
    "stream",
    "progress",
    "event",
)
MAX_REQUEST_ID_LENGTH = 128
MAX_ERROR_MESSAGE_LENGTH = 4096
MAX_RECENT_REQUEST_IDS = 4096
GENERATION_SHUTDOWN_TIMEOUT_SECONDS = 2.0
TRANSCRIPTION_TIMEOUT_SECONDS = 120.0


class _GenerationState(Enum):
    """Linearize cancellation against one generation's commit boundary."""

    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMMITTING = "committing"
    FINISHED = "finished"


@dataclass
class _GenerationTask:
    """Track one globally exclusive streamed Chat operation."""

    request_id: str
    chat_id: ChatId
    method: str
    speak_reply: bool
    uses_project_knowledge: bool = False
    knowledge_runtime_owner: object | None = field(default=None, repr=False)
    state: _GenerationState = _GenerationState.RUNNING
    done: Event = field(default_factory=Event)
    thread: Thread | None = None
    speech_turn: DesktopSpeechTurn | None = field(default=None, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def request_cancel(self) -> bool:
        """Request cancellation and suppress the matching speech copy."""

        speech_turn: DesktopSpeechTurn | None = None
        with self._lock:
            if self.state is _GenerationState.RUNNING:
                self.state = _GenerationState.CANCEL_REQUESTED
                speech_turn, self.speech_turn = self.speech_turn, None
                accepted = True
            else:
                accepted = self.state is _GenerationState.CANCEL_REQUESTED
        if speech_turn is not None:
            try:
                speech_turn.cancel()
            except BaseException:
                # Speech is an optional copy of the canonical text stream.  A
                # broken adapter must not turn a successful cancellation into
                # a protocol failure or keep the text worker alive.
                logger.error("Desktop speech cancellation failed.")
        return accepted

    def should_cancel(self) -> bool:
        """Return whether the worker must stop before yielding or committing."""

        with self._lock:
            return self.state is _GenerationState.CANCEL_REQUESTED

    def begin_commit(self) -> bool:
        """Claim the commit boundary unless cancellation won the race."""

        with self._lock:
            if self.state is not _GenerationState.RUNNING:
                return False
            self.state = _GenerationState.COMMITTING
            return True

    def attach_speech_turn(self, speech_turn: DesktopSpeechTurn) -> None:
        """Attach one optional turn unless cancellation already won the race."""

        if not isinstance(speech_turn, DesktopSpeechTurn):
            raise TypeError("speech_turn must implement DesktopSpeechTurn.")
        with self._lock:
            if self.state is _GenerationState.RUNNING:
                self.speech_turn = speech_turn
                cancel_now = False
            else:
                cancel_now = True
        if cancel_now:
            try:
                speech_turn.cancel()
            except BaseException:
                logger.error("Desktop speech cancellation failed.")

    def feed_speech(self, chunk: str) -> None:
        """Copy one Brain chunk to speech without transferring text ownership."""

        with self._lock:
            speech_turn = self.speech_turn
        if speech_turn is not None:
            try:
                speech_turn.feed(chunk)
            except BaseException:
                # Detach only the same turn: cancellation may already have
                # installed no replacement, but this check documents and
                # preserves that ownership boundary.
                with self._lock:
                    if self.speech_turn is speech_turn:
                        self.speech_turn = None
                try:
                    speech_turn.cancel()
                except BaseException:
                    pass
                logger.error("Desktop speech input failed; text Chat continues.")

    def finish_speech(self) -> None:
        """Flush speech after the canonical Assistant reply has committed."""

        with self._lock:
            speech_turn, self.speech_turn = self.speech_turn, None
        if speech_turn is not None:
            try:
                speech_turn.finish()
            except BaseException:
                try:
                    speech_turn.cancel()
                except BaseException:
                    pass
                logger.error("Desktop speech finalization failed.")

    def cancel_speech(self) -> None:
        """Discard queued or late audio when generation has no valid reply."""

        with self._lock:
            speech_turn, self.speech_turn = self.speech_turn, None
        if speech_turn is not None:
            try:
                speech_turn.cancel()
            except BaseException:
                logger.error("Desktop speech cancellation failed.")

    def finish(self) -> None:
        """Publish terminal state to shutdown and serialized readers."""

        with self._lock:
            self.state = _GenerationState.FINISHED
        self.done.set()

    def state_snapshot(self) -> _GenerationState:
        """Read the lifecycle state without exposing the task lock."""

        with self._lock:
            return self.state


@dataclass(frozen=True, slots=True)
class _TranscriptionTask:
    """Correlate one admitted transcript without retaining its source PCM."""

    request_id: str
    session_id: str
    chat_id: ChatId


@dataclass(slots=True)
class _KnowledgeTask:
    """Track one cancellable Project Source lifecycle worker."""

    request_id: str
    project_id: ProjectId
    method: str
    initial_operation_ids: frozenset[str] = field(default_factory=frozenset)
    runtime_owner: object | None = field(default=None, repr=False)
    monitor_runtime_owner: object | None = field(default=None, repr=False)
    cancel_requested: Event = field(default_factory=Event, repr=False)
    monitor_stop: Event = field(default_factory=Event, repr=False)
    done: Event = field(default_factory=Event, repr=False)
    thread: Thread | None = field(default=None, repr=False)

    def request_cancel(self) -> bool:
        """Request cooperative cancellation until the worker is terminal."""

        # Recovery resumes pre-existing durable operations, while export is a
        # one-shot native-dialog action rather than a journaled UI operation.
        # Neither exposes a Renderer Stop control, so do not accept a protocol
        # cancellation that the caller cannot correlate to durable state.
        if self.method in {"knowledge.recover", "knowledge.source.export"}:
            return False
        if self.done.is_set():
            return False
        self.cancel_requested.set()
        return True

    def request_shutdown_cancel(self) -> bool:
        """Cancel work that can safely clean up during Backend shutdown."""

        if self.method == "knowledge.recover" or self.done.is_set():
            return False
        self.cancel_requested.set()
        return True

    def should_cancel(self) -> bool:
        """Return whether the lifecycle service should durably cancel."""

        return self.cancel_requested.is_set()


def _configure_protocol_streams(*streams: TextIO) -> None:
    """Use UTF-8 for the Electron protocol on every Windows locale."""

    for stream in streams:
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(
                encoding="utf-8",
                errors="strict",
            )


def _extract_model_names(
    payload: object,
    fallback_model: str,
) -> tuple[str, ...]:
    """Return unique Ollama model names with the configured model first."""

    discovered: list[str] = []

    if isinstance(payload, dict):
        raw_models = payload.get("models")
        if isinstance(raw_models, list):
            for raw_model in raw_models:
                if not isinstance(raw_model, dict):
                    continue

                raw_name = raw_model.get("name")
                if not isinstance(raw_name, str):
                    raw_name = raw_model.get("model")

                if isinstance(raw_name, str) and raw_name.strip():
                    discovered.append(raw_name.strip())

    ordered_names = [fallback_model.strip(), *sorted(discovered)]
    return tuple(dict.fromkeys(name for name in ordered_names if name))


def discover_ollama_models(
    settings: AppSettings | None = None,
) -> tuple[str, ...]:
    """Return Ollama model choices with the configured fallback listed first."""

    runtime_settings = SETTINGS if settings is None else settings
    endpoint = f"{runtime_settings.ollama_host.rstrip('/')}/api/tags"
    request = Request(
        endpoint,
        headers={"Accept": "application/json"},
    )

    try:
        with urlopen(request, timeout=3.0) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        logger.exception(
            "Could not enumerate Ollama models from %s.",
            endpoint,
        )
        payload = {}

    return _extract_model_names(payload, runtime_settings.model_name)


def _create_transcription_runner(
    transcriber: FasterWhisperTranscriber,
    on_terminal: TranscriptionJobCallback,
) -> TranscriptionJobRunner:
    """Build the bounded worker boundary around one configured transcriber."""

    return TranscriptionJobRunner(
        transcriber,
        config=TranscriptionJobRunnerConfig(
            max_concurrent_jobs=1,
            max_queued_jobs=0,
            default_timeout_seconds=TRANSCRIPTION_TIMEOUT_SECONDS,
        ),
        on_terminal=on_terminal,
    )


def _create_transcriber(settings: AppSettings) -> FasterWhisperTranscriber:
    """Build one offline adapter from allowlisted active settings.

    An explicit local directory prevents Faster-Whisper from interpreting a
    model alias as permission to download weights. Construction remains light:
    neither optional dependencies nor model weights load until a readiness
    query or admitted transcription asks for them.
    """

    model_name = validate_transcription_model(settings.transcription_model)
    requested_device = validate_transcription_device(
        settings.transcription_device
    )
    # The final segment is selected from a closed allowlist. No user-provided
    # path or model alias can cross this composition boundary and trigger an
    # implicit Faster-Whisper download.
    model_path = (
        settings.base_dir.resolve()
        / "models"
        / "weights"
        / "faster-whisper"
        / model_name
    )
    return FasterWhisperTranscriber(
        FasterWhisperConfig(
            model_path=model_path,
            device=requested_device,
        )
    )


def _validate_speech_selection(settings: AppSettings) -> bool:
    """Return whether the configured local catalog resolves one exact voice.

    This preflight reads only the trusted local manifest and does not start a
    model process or contact its loopback API. All catalog detail is collapsed
    to one Boolean so settings errors cannot expose private asset locations.
    """

    config = DesktopSpeechConfig.from_app_settings(settings)
    try:
        catalog = JsonVoiceProfileCatalog.load(
            config.catalog_path,
            config.asset_root,
            allow_local_evaluation=config.allow_local_evaluation,
        )
        catalog.resolve_selection(
            config.voice_profile_id,
            config.voice_emotion,
        )
    except (
        VoiceProfileCatalogError,
        SynthesisUnavailableError,
        TypeError,
        ValueError,
    ):
        return False
    return True


class DesktopBackend:
    """Translate the desktop protocol into existing Brain operations."""

    def __init__(
        self,
        *,
        brain_factory: BrainFactory = create_brain,
        model_loader: ModelLoader = discover_ollama_models,
        settings_validator: SettingsValidator = validate_settings,
        settings_repository: DesktopSettingsRepository | None = None,
        voice_settings_service: VoiceSettingsService | None = None,
        transcription_runner_factory: TranscriptionRunnerFactory | None = None,
        attachment_store: AttachmentRepository | AttachmentService | None = None,
        knowledge_runtime_factory: KnowledgeRuntimeFactory | None = None,
        speech_selection_validator: SpeechSelectionValidator = (
            _validate_speech_selection
        ),
        audio_writer: AudioChannelWriter | None = None,
        speech_coordinator: DesktopSpeechCoordinator | None = None,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        expected_session_token: str | None = None,
    ) -> None:
        """Store injected boundaries so the protocol can be tested offline."""

        self._brain_factory = brain_factory
        self._model_loader = model_loader
        self._settings_validator = settings_validator
        self._uses_default_brain_factory = brain_factory is create_brain
        self._uses_default_model_loader = model_loader is discover_ollama_models
        self._uses_default_settings_validator = (
            settings_validator is validate_settings
        )
        self._settings_repository = (
            create_desktop_settings_repository(SETTINGS)
            if settings_repository is None
            else settings_repository
        )
        self._voice_settings_service = (
            create_voice_settings_service(SETTINGS.data_layout.root)
            if voice_settings_service is None
            else voice_settings_service
        )
        self._attachment_store = (
            None
            if attachment_store is None
            else (
                attachment_store
                if isinstance(attachment_store, AttachmentService)
                else AttachmentService(attachment_store)
            )
        )
        initial_settings = self._settings_repository.load()
        self._settings_warning = initial_settings.warning
        self._desired_settings = initial_settings
        try:
            self._runtime_settings = apply_editable_settings(
                SETTINGS,
                initial_settings.values,
                model_override=os.environ.get("ELYSIA_MODEL_OVERRIDE"),
            )
        except DesktopSettingsValidationError:
            self._runtime_settings = apply_editable_settings(
                SETTINGS,
                initial_settings.values,
            )
            override_warning = (
                "An invalid temporary model override was ignored."
            )
            self._settings_warning = " ".join(
                warning
                for warning in (self._settings_warning, override_warning)
                if warning is not None
            )
        if audio_writer is not None and speech_coordinator is not None:
            raise TypeError(
                "audio_writer and speech_coordinator are mutually exclusive."
            )
        self._transcription_runner_factory = transcription_runner_factory
        self._knowledge_runtime_factory = knowledge_runtime_factory
        if not callable(speech_selection_validator):
            raise TypeError("speech_selection_validator must be callable.")
        self._speech_selection_validator = speech_selection_validator
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._expected_session_token = (
            expected_session_token
            if expected_session_token is not None
            else os.environ.get("ELYSIA_DESKTOP_SESSION_TOKEN")
        )
        self._authenticated = False
        self._brain: Brain | None = None
        self._active_chat: ChatSession | None = None
        self._active_project_id: ProjectId | None = None
        self._models: tuple[str, ...] = ()
        self._seen_request_ids: set[str] = set()
        self._request_id_order: deque[str] = deque()
        self._state_lock = RLock()
        self._output_lock = Lock()
        self._speech_coordinator = speech_coordinator
        # Keep the private pipe owner dormant until initialize so a valid
        # pre-initialize settings repair can still select the actual profile
        # and synthesis rate used by the first runtime. Injected coordinators
        # are already constructed and therefore remain restart-bound.
        self._speech_audio_writer = audio_writer
        self._generation_task: _GenerationTask | None = None
        self._knowledge_runtime: DesktopKnowledgeRuntime | None = None
        self._knowledge_task: _KnowledgeTask | None = None
        # Knowledge workers can outlive a bounded shutdown wait while a local
        # model or filesystem adapter drains.  This gate makes the shutdown
        # response/EOF a strict output boundary and prevents closing runtime
        # resources underneath a worker that still owns them.
        self._knowledge_lifecycle_lock = RLock()
        self._knowledge_closing = False
        self._knowledge_runtime_owners: set[object] = set()
        self._transcription_runner: TranscriptionJobRunner | None = None
        self._transcription_transcriber: FasterWhisperTranscriber | None = None
        self._transcription_task: _TranscriptionTask | None = None
        # This gate linearizes terminal output against shutdown. A callback
        # that already owns it may finish before the shutdown response; one
        # arriving after closure observes the flag and emits nothing.
        self._transcription_lifecycle_lock = RLock()
        self._transcription_closing = False
        # Speech callbacks originate on a queue daemon. This gate gives the
        # shutdown response/EOF a strict output boundary without joining that
        # daemon or optional native inference.
        self._speech_lifecycle_lock = RLock()
        self._speech_closing = False

    def _active_chat_snapshot(self) -> ChatSession | None:
        """Read the active Chat under the worker coordination lock."""

        with self._state_lock:
            return self._active_chat

    def _set_active_chat(self, chat: ChatSession) -> None:
        """Publish an active Chat without racing a completed generation."""

        with self._state_lock:
            self._active_chat = chat

    def run(self) -> None:
        """Read requests until shutdown or end-of-input."""

        for raw_line in self._input_stream:
            line = raw_line.removesuffix("\n").removesuffix("\r")
            if len(line.encode("utf-8")) > MAX_PROTOCOL_FRAME_BYTES:
                self._emit_error(
                    None,
                    "protocol.frame_too_large",
                    "Request exceeds the desktop protocol frame limit.",
                )
                continue
            if not line.strip():
                continue

            if not self._handle_line(line):
                return

        # Electron has disappeared once stdin closes, so transcription output
        # has no valid consumer. Close admission immediately and suppress every
        # callback that loses the EOF race; daemon workers may drain native I/O
        # without delaying process teardown.
        self._prepare_transcription_shutdown()
        self._prepare_speech_shutdown()
        self._prepare_knowledge_shutdown()
        # A finite test/input stream may end immediately after starting a
        # generation. Give a healthy worker time to finish; if it is blocked,
        # request cancellation so stdin closure cannot strand the process.
        if not self._wait_for_generation(
            timeout=GENERATION_SHUTDOWN_TIMEOUT_SECONDS,
        ):
            self._prepare_generation_shutdown()

    def _handle_line(self, line: str) -> bool:
        """Parse and dispatch one request, returning whether to continue."""

        request_id: str | None = None
        try:
            raw_request = json.loads(line)
        except json.JSONDecodeError as error:
            self._emit_error(
                None,
                "protocol.invalid_json",
                f"Request is not valid JSON: {error.msg}.",
            )
            return True

        if isinstance(raw_request, dict):
            candidate_id = raw_request.get("id")
            if (
                isinstance(candidate_id, str)
                and 0 < len(candidate_id) <= MAX_REQUEST_ID_LENGTH
            ):
                request_id = candidate_id

        try:
            request = parse_client_request(raw_request)
        except ProtocolValidationError as error:
            self._emit_error(request_id, error.code, str(error))
            return True

        request_id = request["id"]
        method = request["method"]
        params = request["params"]

        if request_id in self._seen_request_ids:
            self._emit_error(
                request_id,
                "protocol.duplicate_request",
                "Request id has already been used.",
            )
            return True
        if len(self._request_id_order) >= MAX_RECENT_REQUEST_IDS:
            expired_request_id = self._request_id_order.popleft()
            self._seen_request_ids.discard(expired_request_id)
        self._seen_request_ids.add(request_id)
        self._request_id_order.append(request_id)

        try:
            if method == "handshake":
                self._handshake(request_id, request)
            elif method == "shutdown":
                self._prepare_transcription_shutdown()
                self._prepare_speech_shutdown()
                self._prepare_generation_shutdown()
                self._prepare_knowledge_shutdown()
                self._emit_response(request_id, {"stopped": True})
                return False
            elif not self._authenticated:
                raise ProtocolValidationError(
                    "protocol.not_authenticated",
                    "Backend handshake must complete before this request.",
                )
            elif method == "initialize":
                self._initialize(request_id)
            elif method == "settings.get":
                self._get_settings(request_id)
            elif method == "settings.update":
                self._update_settings(request_id, params)
            elif method == "voice.settings.get":
                self._get_voice_settings(request_id)
            elif method == "voice.settings.update":
                self._update_voice_settings(request_id, params)
            elif self._brain is None or self._active_chat_snapshot() is None:
                raise ProtocolValidationError(
                    "protocol.not_initialized",
                    "Backend must be initialized before this request.",
                )
            elif method == "voice.capture.complete":
                self._complete_voice_capture(request_id, params)
            elif method == "voice.transcription.start":
                self._start_voice_transcription(request_id, params)
            elif method == "voice.speech.start":
                self._start_speech_turn(request_id, params)
            elif method == "voice.speech.cancel":
                self._cancel_speech_turn(request_id, params)
            elif method == "chat.stream":
                self._start_chat_stream(request_id, params)
            elif method == "chat.retry":
                self._start_chat_retry(request_id, params)
            elif method == "chat.list":
                self._list_chats(request_id, params)
            elif method == "chat.create":
                self._create_chat(request_id, params)
            elif method == "chat.open":
                self._open_chat(request_id, params)
            elif method == "chat.rename":
                self._rename_chat(request_id, params)
            elif method == "chat.pin":
                self._pin_chat(request_id, params)
            elif method == "chat.archive":
                self._archive_chat(request_id, params)
            elif method == "chat.delete":
                self._delete_chat(request_id, params)
            elif method == "attachment.list":
                self._list_attachments(request_id, params)
            elif method == "attachment.add":
                self._add_attachments(request_id, params)
            elif method == "attachment.remove":
                self._remove_attachment(request_id, params)
            elif method == "project.list":
                self._list_projects(request_id)
            elif method == "project.create":
                self._create_project(request_id, params)
            elif method == "project.open":
                self._open_project(request_id, params)
            elif method == "project.update":
                self._update_project(request_id, params)
            elif method == "project.workspace":
                self._set_project_workspace(request_id, params)
            elif method == "project.archive":
                self._archive_project(request_id, params)
            elif method == "project.chat.move":
                self._move_project_chat(request_id, params)
            elif method == "knowledge.list":
                self._list_project_knowledge(request_id, params)
            elif method == "knowledge.source.add":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.source.replace":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.source.reindex":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.source.delete":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.project.rebuild":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.project.revoke":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.recover":
                self._start_knowledge_operation(request_id, params, method)
            elif method == "knowledge.source.export":
                self._export_project_source(request_id, params)
            elif method == "request.cancel":
                self._cancel_request(request_id, params)
            elif method == "permission.respond":
                raise ProtocolValidationError(
                    "permission.not_found",
                    "No Backend permission request is pending.",
                )
        except ProtocolValidationError as error:
            self._emit_error(request_id, error.code, str(error))
        except DesktopSettingsConflictError as error:
            self._emit_error(
                request_id,
                "settings.conflict",
                str(error),
                retryable=True,
            )
        except DesktopSettingsValidationError as error:
            self._emit_error(request_id, "settings.invalid", str(error))
        except DesktopSettingsStorageError as error:
            self._emit_error(
                request_id,
                "settings.storage_failed",
                str(error),
                retryable=True,
            )
        except VoiceSettingsConflictError as error:
            self._emit_error(
                request_id,
                "voice.settings.conflict",
                str(error),
                retryable=True,
            )
        except VoiceSettingsValidationError as error:
            self._emit_error(request_id, "voice.settings.invalid", str(error))
        except VoiceSettingsStorageError as error:
            self._emit_error(
                request_id,
                "voice.settings.storage_failed",
                str(error),
                retryable=True,
            )
        except VoiceCaptureValidationError as error:
            self._emit_error(request_id, "voice.capture.invalid", str(error))
        except TranscriptionJobCapacityError:
            self._emit_error(
                request_id,
                "voice.transcription.busy",
                "Local voice transcription is already busy.",
                retryable=True,
            )
        except TranscriptionJobConflictError:
            self._emit_error(
                request_id,
                "voice.transcription.busy",
                "Local voice transcription is already busy.",
                retryable=True,
            )
        except TranscriptionJobClosedError:
            self._emit_error(
                request_id,
                "voice.transcription.unavailable",
                "Local voice transcription is unavailable.",
            )
        except ChatBusyError as error:
            self._emit_error(request_id, "chat.busy", str(error))
        except ChatRetryTargetError as error:
            self._emit_error(request_id, "chat.retry_target", str(error))
        except ChatNotFoundError as error:
            self._emit_error(request_id, "chat.not_found", str(error))
        except AttachmentValidationError as error:
            self._emit_error(request_id, "attachment.invalid", str(error))
        except AttachmentNotFoundError as error:
            self._emit_error(request_id, "attachment.not_found", str(error))
        except AttachmentConflictError as error:
            self._emit_error(request_id, "attachment.conflict", str(error))
        except AttachmentStorageError as error:
            self._emit_error(
                request_id,
                "attachment.storage_failed",
                str(error),
                retryable=True,
            )
        except ProjectChatBusyError as error:
            self._emit_error(
                request_id,
                "project.chat_busy",
                str(error),
                retryable=True,
            )
        except ProjectArchivedError as error:
            self._emit_error(request_id, "project.archived", str(error))
        except ProjectNotFoundError as error:
            self._emit_error(request_id, "project.not_found", str(error))
        except KnowledgeLifecycleValidationError as error:
            self._emit_error(request_id, "knowledge.invalid", str(error))
        except KnowledgeLifecycleNotFoundError as error:
            self._emit_error(request_id, "knowledge.not_found", str(error))
        except KnowledgeLifecycleConflictError as error:
            self._emit_error(
                request_id,
                "knowledge.conflict",
                str(error),
                retryable=True,
            )
        except KnowledgeLifecycleRecoveryError as error:
            self._emit_error(
                request_id,
                "knowledge.recovery_required",
                str(error),
                retryable=True,
            )
        except KnowledgeLifecycleStorageError as error:
            self._emit_error(
                request_id,
                "knowledge.storage_failed",
                str(error),
                retryable=True,
            )
        except KnowledgeLifecycleError as error:
            self._emit_error(request_id, "knowledge.failed", str(error))
        except ProjectSourceAuthorizationError as error:
            self._emit_error(
                request_id,
                "knowledge.unauthorized",
                str(error),
            )
        except ProjectSourceNotFoundError as error:
            self._emit_error(request_id, "knowledge.not_found", str(error))
        except ProjectSourceStaleError as error:
            self._emit_error(
                request_id,
                "knowledge.stale",
                str(error),
                retryable=True,
            )
        except ProjectSourceConflictError as error:
            self._emit_error(
                request_id,
                "knowledge.conflict",
                str(error),
                retryable=True,
            )
        except ProjectSourceValidationError as error:
            self._emit_error(request_id, "knowledge.invalid", str(error))
        except ProjectSourceStorageError as error:
            self._emit_error(
                request_id,
                "knowledge.storage_failed",
                str(error),
                retryable=True,
            )
        except ProjectSourceError as error:
            self._emit_error(request_id, "knowledge.failed", str(error))
        except Exception:
            if method == "voice.transcription.start":
                # This boundary can fail while constructing optional native
                # dependencies. Do not place an exception or model path on
                # stderr because Electron owns that child-process stream.
                logger.error(
                    "Desktop transcription request failed: request_id=%s.",
                    request_id,
                )
            else:
                logger.exception(
                    "Desktop request failed: method=%s request_id=%s.",
                    method,
                    request_id,
                )
            self._emit_error(
                request_id,
                (
                    "chat.failed"
                    if method in {"chat.stream", "chat.retry"}
                    else (
                        "voice.transcription.failed"
                        if method == "voice.transcription.start"
                        else "backend.request_failed"
                    )
                ),
                (
                    "Chat request failed in the local Backend."
                    if method in {"chat.stream", "chat.retry"}
                    else (
                        "Local voice transcription failed."
                        if method == "voice.transcription.start"
                        else "Desktop Backend request failed."
                    )
                ),
                retryable=method
                in {
                    "chat.stream",
                    "chat.retry",
                    "voice.transcription.start",
                },
            )

        return True

    def _handshake(
        self,
        request_id: str,
        request: ClientRequest,
    ) -> None:
        """Authenticate Electron and negotiate the protocol without I/O."""

        params = request["params"]
        session_token = cast(str, params["sessionToken"])
        if self._expected_session_token is None:
            raise ProtocolValidationError(
                "protocol.local_source_unavailable",
                "Desktop Backend did not receive a local session token.",
            )
        if not secrets.compare_digest(
            session_token,
            self._expected_session_token,
        ):
            raise ProtocolValidationError(
                "protocol.unauthorized_local_peer",
                "Desktop session token was rejected.",
            )

        self._authenticated = True
        self._emit_response(
            request_id,
            {
                "protocol": {
                    "name": PROTOCOL_NAME,
                    "version": PROTOCOL_VERSION,
                },
                "server": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "capabilities": list(SERVER_CAPABILITIES),
            },
        )

    def _initialize(self, request_id: str) -> None:
        """Validate settings, connect Brain, and select one model Chat."""

        brain = self._brain
        active_chat = self._active_chat_snapshot()
        if brain is None or active_chat is None:
            self._emit_progress(
                request_id,
                "backend.initialize",
                0,
                total=3,
                message="Validating local settings",
            )
            if self._uses_default_settings_validator:
                validate_settings(self._runtime_settings)
            else:
                self._settings_validator()
            self._emit_progress(
                request_id,
                "backend.initialize",
                1,
                total=3,
                message="Discovering local models",
            )
            self._models = (
                discover_ollama_models(self._runtime_settings)
                if self._uses_default_model_loader
                else self._model_loader()
            )
            self._emit_progress(
                request_id,
                "backend.initialize",
                2,
                total=3,
                message="Loading Chat services",
            )
            brain = (
                create_brain(self._runtime_settings)
                if self._uses_default_brain_factory
                else self._brain_factory()
            )
            if self._attachment_store is None:
                self._attachment_store = AttachmentService(
                    JsonAttachmentStore(
                        self._runtime_settings.data_layout.attachments,
                        max_file_bytes=(
                            self._runtime_settings.data_import_max_bytes
                        ),
                    )
                )
            chat_metadata = brain.list_chats(include_archived=True)
            project_metadata = brain.list_projects(include_archived=True)
            self._attachment_store.reconcile_owners(
                chat_ids=tuple(
                    str(chat.chat_id) for chat in chat_metadata
                ),
                project_ids=tuple(
                    str(project.project_id) for project in project_metadata
                ),
            )
            # Owner existence and message references are separate authorities:
            # the first pass removes scopes for deleted owners, while the Chat
            # pass repairs lifecycle state from persisted messages. Projects do
            # not have message references, but still reconcile with an empty set
            # so crash-left originals and legacy leftovers are reclaimed.
            for metadata in chat_metadata:
                chat = brain.get_chat(metadata.chat_id)
                self._attachment_store.reconcile(
                    AttachmentScope(kind="chat", id=str(chat.chat_id)),
                    self._chat_attachment_references(chat),
                )
            for project_metadata_item in project_metadata:
                self._attachment_store.reconcile(
                    AttachmentScope(
                        kind="project",
                        id=str(project_metadata_item.project_id),
                    ),
                    (),
                )
            if self._knowledge_runtime is None and (
                self._uses_default_brain_factory
                or self._knowledge_runtime_factory is not None
            ):
                runtime_factory = (
                    create_desktop_knowledge_runtime
                    if self._knowledge_runtime_factory is None
                    else self._knowledge_runtime_factory
                )
                knowledge_runtime = runtime_factory(
                    self._runtime_settings,
                    self._attachment_store,
                )
                self._knowledge_runtime = knowledge_runtime
                self._start_export_recovery(knowledge_runtime)
                # Project index recovery can repeat embedding work and must
                # remain explicit. Export recovery is different: it only
                # removes identity-pinned crash leftovers and runs on its own
                # daemon so initialize never waits for an external volume.
            active_chat = self._resolve_active_chat(brain)
            self._brain = brain
            self._set_active_chat(active_chat)
            self._active_project_id = active_chat.project_id
            try:
                if (
                    self._speech_coordinator is None
                    and self._speech_audio_writer is not None
                ):
                    coordinator = DesktopSpeechCoordinator(
                        DesktopSpeechConfig.from_app_settings(
                            self._runtime_settings
                        ),
                        self._speech_audio_writer,
                        self._emit_speech_event,
                    )
                    self._speech_coordinator = coordinator
                    self._speech_audio_writer = None
                if self._speech_coordinator is not None:
                    self._speech_coordinator.start()
            except BaseException:
                logger.error(
                    "Optional desktop speech could not start; text Chat continues."
                )
                self._prepare_speech_shutdown()
            self._emit_progress(
                request_id,
                "backend.initialize",
                3,
                total=3,
                message=None,
            )

        self._emit_response(
            request_id,
            {
                "modelName": brain.model_name,
                "models": list(self._models),
                "chatId": str(active_chat.chat_id),
                "chatTitle": active_chat.title,
            },
        )

    def _start_export_recovery(self, runtime: DesktopKnowledgeRuntime) -> None:
        """Schedule identity-safe export cleanup without delaying startup.

        Export destinations can live on unavailable or unusually slow
        removable volumes. The recovery service fails closed against its
        durable identities, while this daemon isolates that bounded cleanup
        from the single-threaded protocol dispatcher. It emits no protocol
        frames, so a late finish cannot cross the shutdown output boundary.
        """

        recover = getattr(runtime.export, "recover_pending_exports", None)
        if not callable(recover):
            # Narrow injected runtimes from older integrations may implement
            # export only; absence is not a reason to fail Backend startup.
            return
        runtime_owner = self._acquire_knowledge_runtime_owner()

        def _recover() -> None:
            """Run private cleanup without exposing destination paths in logs."""

            try:
                recover()
            except Exception:
                logger.error("Pending Knowledge export cleanup failed.")
            finally:
                # Shutdown must not close the shared runtime while this daemon
                # still owns its export service.  Finishing the final owner
                # retries delayed close without making shutdown wait on an
                # unavailable removable volume.
                self._release_knowledge_runtime_owner(runtime_owner)

        try:
            worker = Thread(
                target=_recover,
                name="elysia-knowledge-export-recovery",
                daemon=True,
            )
            worker.start()
        except Exception:
            # Startup cleanup is best-effort; the durable intent remains for
            # the next launch if the process cannot allocate another thread.
            self._release_knowledge_runtime_owner(runtime_owner)
            logger.error("Pending Knowledge export cleanup could not start.")

    @staticmethod
    def _serialize_settings_values(
        values: EditableDesktopSettings,
    ) -> JsonObject:
        """Map the public allowlist to its stable camel-case wire shape."""

        return {
            "modelName": values.model_name,
            "ollamaHost": values.ollama_host,
            "shortTermMemoryTokenBudget": (
                values.short_term_memory_token_budget
            ),
            "memoryRetrievalLimit": values.memory_retrieval_limit,
            "dataImportMaxBytes": values.data_import_max_bytes,
            "transcriptionModel": values.transcription_model,
            "transcriptionDevice": values.transcription_device,
            "transcriptionLanguage": values.transcription_language,
            "autoReadAloud": values.auto_read_aloud,
            "speechRatePercent": values.speech_rate_percent,
            "speechVolumePercent": values.speech_volume_percent,
            "voiceProfileId": values.voice_profile_id,
            "voiceEmotion": values.voice_emotion,
            "captionsEnabled": values.captions_enabled,
            "transcriptReviewMode": values.transcript_review_mode,
            "automaticRelisten": values.automatic_relisten,
        }

    def _load_desired_settings(self) -> DesktopSettingsSnapshot:
        """Refresh persisted desired values while retaining recovery notice."""

        snapshot = self._settings_repository.load()
        if snapshot.warning is not None:
            self._settings_warning = snapshot.warning
        self._desired_settings = snapshot
        return snapshot

    def _settings_state_result(self) -> JsonObject:
        """Describe desired/active values and Global/Project/Chat ownership."""

        desired = self._desired_settings
        active_values = editable_from_app_settings(self._runtime_settings)
        restart_fields = changed_setting_names(desired.values, active_values)
        active_chat = self._active_chat_snapshot()

        project_scope: JsonObject | None = None
        if self._brain is not None and self._active_project_id is not None:
            try:
                project = self._brain.get_project(self._active_project_id)
            except ProjectNotFoundError:
                project = None
            if project is not None:
                project_scope = {
                    "projectId": str(project.project_id),
                    "projectName": project.name,
                    "modelName": project.settings.default_model_name,
                    "inheritedModelName": desired.values.model_name,
                }

        chat_scope: JsonObject | None = None
        if active_chat is not None:
            chat_scope = {
                "chatId": str(active_chat.chat_id),
                "chatTitle": active_chat.title,
                "modelName": active_chat.model_settings.model_name,
            }

        return {
            "revision": desired.revision,
            "updatedAt": (
                None
                if desired.updated_at is None
                else desired.updated_at.isoformat()
            ),
            "settings": self._serialize_settings_values(desired.values),
            "activeSettings": self._serialize_settings_values(active_values),
            "restartRequired": bool(restart_fields),
            "restartFields": list(restart_fields),
            "speechStatus": self._speech_status_result(),
            "scopes": {
                "project": project_scope,
                "chat": chat_scope,
            },
            "warning": self._settings_warning,
        }

    def _get_settings(self, request_id: str) -> None:
        """Return settings even when Brain initialization has failed."""

        self._load_desired_settings()
        self._emit_response(request_id, self._settings_state_result())

    def _update_settings(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Validate and atomically replace the complete public settings set."""

        with self._state_lock:
            if self._generation_task is not None:
                raise ProtocolValidationError(
                    "settings.busy",
                    "Wait for the current reply before changing settings.",
                )
            if (
                self._knowledge_task is not None
                and not self._knowledge_task.done.is_set()
            ):
                raise ProtocolValidationError(
                    "settings.busy",
                    "Wait for Project Sources before changing settings.",
                )
            if self._transcription_capacity_reserved_locked():
                # Logical cancellation can finish before uninterruptible native
                # inference releases its slot. Waiting for physical release
                # prevents a settings response from promising a configuration
                # that an old model is still actively using.
                raise ProtocolValidationError(
                    "settings.busy",
                    "Wait for local transcription before changing settings.",
                )

            raw = cast(JsonObject, params["settings"])
            values = EditableDesktopSettings(
                model_name=cast(str, raw["modelName"]),
                ollama_host=cast(str, raw["ollamaHost"]),
                short_term_memory_token_budget=cast(
                    int,
                    raw["shortTermMemoryTokenBudget"],
                ),
                memory_retrieval_limit=cast(
                    int,
                    raw["memoryRetrievalLimit"],
                ),
                data_import_max_bytes=cast(int, raw["dataImportMaxBytes"]),
                transcription_model=cast(Any, raw["transcriptionModel"]),
                transcription_device=cast(Any, raw["transcriptionDevice"]),
                transcription_language=cast(Any, raw["transcriptionLanguage"]),
                auto_read_aloud=cast(bool, raw["autoReadAloud"]),
                speech_rate_percent=cast(int, raw["speechRatePercent"]),
                speech_volume_percent=cast(int, raw["speechVolumePercent"]),
                voice_profile_id=cast(str, raw["voiceProfileId"]),
                voice_emotion=cast(VoiceEmotion, raw["voiceEmotion"]),
                captions_enabled=cast(bool, raw["captionsEnabled"]),
                transcript_review_mode=cast(
                    Any,
                    raw["transcriptReviewMode"],
                ),
                automatic_relisten=cast(bool, raw["automaticRelisten"]),
            )
            current_values = self._desired_settings.values
            selection_changed = (
                values.voice_profile_id != current_values.voice_profile_id
                or values.voice_emotion != current_values.voice_emotion
            )
            if selection_changed:
                candidate_settings = apply_editable_settings(
                    self._runtime_settings,
                    values,
                )
                if not self._speech_selection_validator(candidate_settings):
                    raise DesktopSettingsValidationError(
                        "The selected local voice profile does not provide "
                        "that emotion. Choose a configured profile and "
                        "emotion pair."
                    )
            saved = self._settings_repository.save(
                values,
                expected_revision=cast(int, params["expectedRevision"]),
            )
            self._desired_settings = saved
            self._settings_warning = None

            # Before Brain exists, the same authenticated recovery process can
            # adopt repaired settings without requiring another child process.
            if self._brain is None:
                previous_runtime = self._runtime_settings
                next_runtime = apply_editable_settings(
                    SETTINGS,
                    saved.values,
                    model_override=os.environ.get("ELYSIA_MODEL_OVERRIDE"),
                )
                if self._speech_coordinator is not None:
                    # An injected coordinator is already bound to its startup
                    # profile and rate even though Brain has not initialized.
                    # Reporting those two fields as active would make a later
                    # initialize silently use settings different from the UI.
                    next_runtime = replace(
                        next_runtime,
                        speech_rate_percent=(
                            previous_runtime.speech_rate_percent
                        ),
                        voice_profile_id=previous_runtime.voice_profile_id,
                        voice_emotion=previous_runtime.voice_emotion,
                    )
                self._runtime_settings = next_runtime
                if (
                    previous_runtime.transcription_model
                    != self._runtime_settings.transcription_model
                    or previous_runtime.transcription_device
                    != self._runtime_settings.transcription_device
                    or previous_runtime.transcription_language
                    != self._runtime_settings.transcription_language
                ):
                    self._reset_transcription_runtime_locked()
            else:
                # Session policy and renderer playback preferences are safe to
                # adopt between admitted operations. Synthesis profile/rate and
                # the established model/STT fields remain restart-bound.
                self._runtime_settings = apply_live_editable_settings(
                    self._runtime_settings,
                    saved.values,
                )

        self._emit_response(request_id, self._settings_state_result())

    def _speech_status_result(self) -> JsonObject:
        """Return one closed renderer-safe managed-speech readiness state."""

        with self._speech_lifecycle_lock:
            coordinator = self._speech_coordinator
            closing = self._speech_closing
            pending_writer = self._speech_audio_writer is not None
            if closing:
                status = DesktopSpeechStatus("closed", False)
            elif coordinator is not None:
                status = coordinator.get_status()
            elif pending_writer:
                status = DesktopSpeechStatus("idle", False)
            else:
                status = DesktopSpeechStatus(
                    "unavailable",
                    False,
                    "setup_unavailable",
                )

        if status.state == "ready":
            return {"state": "ready", "reason": None}
        if status.state in ("idle", "starting"):
            return {"state": "starting", "reason": None}
        return {
            "state": "unavailable",
            "reason": (
                "runtime_failed"
                if status.state == "closed"
                else status.reason
            ),
        }

    def _voice_settings_state_result(
        self,
        snapshot: VoiceSettingsSnapshot,
    ) -> JsonObject:
        """Serialize preferences and sanitized, model-path-free readiness."""

        with self._state_lock:
            transcription_status = self._transcription_status_locked()

        return {
            "kind": "voice.settings",
            "revision": snapshot.revision,
            "updatedAt": (
                None
                if snapshot.updated_at is None
                else snapshot.updated_at.isoformat()
            ),
            "inputDeviceId": snapshot.values.input_device_id,
            "outputDeviceId": snapshot.values.output_device_id,
            "transcriptionStatus": transcription_status,
            "warning": snapshot.warning,
        }

    def _get_voice_settings(self, request_id: str) -> None:
        """Return device preferences before or after Brain initialization."""

        snapshot = self._voice_settings_service.get_settings()
        self._emit_response(
            request_id,
            self._voice_settings_state_result(snapshot),
        )

    def _update_voice_settings(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Save device preferences independently of Chat generation state."""

        with self._state_lock:
            if self._transcription_capacity_reserved_locked():
                raise ProtocolValidationError(
                    "voice.settings.busy",
                    "Wait for local transcription before changing voice settings.",
                )
            snapshot = self._voice_settings_service.update_settings(
                input_device_id=cast(str | None, params["inputDeviceId"]),
                output_device_id=cast(str | None, params["outputDeviceId"]),
                expected_revision=cast(int, params["expectedRevision"]),
            )
        self._emit_response(
            request_id,
            self._voice_settings_state_result(snapshot),
        )

    def _transcription_capacity_reserved_locked(self) -> bool:
        """Return whether logical or uninterruptible STT work owns capacity.

        Callers hold ``_state_lock`` so a new Chat or capture cannot slip
        between this check and its own reservation. The runner count matters
        after cancellation or timeout because native inference may still be
        draining even though the request already received a terminal result.
        """

        if self._transcription_task is not None:
            return True
        runner = self._transcription_runner
        return (
            runner is not None
            and runner.get_status().occupied_slots > 0
        )

    def _get_transcription_runner_locked(self) -> TranscriptionJobRunner:
        """Create the optional local STT worker pool on first use only."""

        if self._transcription_closing:
            raise TranscriptionJobClosedError(
                "The transcription boundary is closing."
            )
        runner = self._transcription_runner
        if runner is None:
            factory = self._transcription_runner_factory
            if factory is None:
                runner = _create_transcription_runner(
                    self._get_transcription_transcriber_locked(),
                    self._on_transcription_terminal,
                )
            else:
                runner = factory(self._on_transcription_terminal)
            if not isinstance(runner, TranscriptionJobRunner):
                raise TypeError(
                    "transcription_runner_factory returned an invalid runner."
                )
            self._transcription_runner = runner
        return runner

    def _get_transcription_transcriber_locked(
        self,
    ) -> FasterWhisperTranscriber:
        """Return the lightweight adapter configured from active settings."""

        transcriber = self._transcription_transcriber
        if transcriber is None:
            transcriber = _create_transcriber(self._runtime_settings)
            self._transcription_transcriber = transcriber
        return transcriber

    def _transcription_status_locked(self) -> JsonObject:
        """Return readiness from the adapter the production runner will use."""

        status: FasterWhisperStatus = (
            self._get_transcription_transcriber_locked().get_status()
        )
        return {
            "state": status.state,
            "model": self._runtime_settings.transcription_model,
            "requestedDevice": self._runtime_settings.transcription_device,
            "resolvedDevice": status.device,
            "computeType": status.compute_type,
            "reason": status.reason,
        }

    def _reset_transcription_runtime_locked(self) -> None:
        """Discard idle STT state after pre-initialization config adoption.

        Callers first prove that no logical or physically draining job owns a
        slot. Closing the idle runner avoids reusing a model initialized with
        the former selection; clearing the adapter also makes the next status
        query reflect the newly active settings.
        """

        runner = self._transcription_runner
        if runner is not None:
            runner.shutdown(wait=False)
        self._transcription_runner = None
        self._transcription_transcriber = None

    @staticmethod
    def _transcription_request_from_params(
        params: JsonObject,
    ) -> TranscriptionRequest:
        """Decode validated wire PCM once and convert it to the STT domain.

        Domain failures are deliberately replaced with a fixed protocol error:
        request audio and adapter diagnostics must never be reflected to the
        Renderer through an exception message.
        """

        try:
            if (
                params["channelCount"] != VOICE_CAPTURE_CHANNEL_COUNT
                or params["sampleFormat"] != VOICE_CAPTURE_SAMPLE_FORMAT
            ):
                raise VoiceCaptureValidationError(
                    "Voice capture PCM format is invalid."
                )
            capture = VoiceCapture.from_base64(
                session_id=cast(str, params["sessionId"]),
                pcm_s16le_base64=cast(str, params["pcmBase64"]),
                sample_rate_hz=cast(int, params["sampleRateHz"]),
                sample_count=cast(int, params["sampleCount"]),
                speech_start_sample=cast(int, params["speechStartSample"]),
                speech_end_sample=cast(int, params["speechEndSample"]),
            )
            return TranscriptionRequest(
                capture=capture,
                language=cast(TranscriptionLanguage, params["language"]),
            )
        except (VoiceCaptureValidationError, TranscriptionValidationError):
            raise ProtocolValidationError(
                "voice.transcription.invalid",
                "Voice transcription audio is invalid.",
            ) from None

    def _start_voice_transcription(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Admit one asynchronous transcript without retaining wire Base64."""

        active_chat = self._active_chat_snapshot()
        if active_chat is None:
            raise ProtocolValidationError(
                "protocol.not_initialized",
                "Backend must be initialized before this request.",
            )
        raw_chat_id = cast(str, params["chatId"])
        if raw_chat_id != str(active_chat.chat_id):
            raise ProtocolValidationError(
                "voice.transcription.chat_mismatch",
                "Voice transcription no longer belongs to the active Chat.",
            )

        transcription_request = self._transcription_request_from_params(params)
        task = _TranscriptionTask(
            request_id=request_id,
            session_id=transcription_request.capture.session_id,
            chat_id=ChatId(raw_chat_id),
        )
        with self._state_lock:
            generation = self._generation_task
            if generation is not None and not generation.done.is_set():
                raise TranscriptionJobCapacityError(
                    "Chat generation currently owns local model capacity."
                )
            if self._transcription_capacity_reserved_locked():
                raise TranscriptionJobCapacityError(
                    "Local voice transcription is already busy."
                )
            runner = self._get_transcription_runner_locked()
            self._transcription_task = task

        try:
            runner.submit(
                request_id,
                transcription_request,
                on_admitted=lambda snapshot: self._emit_transcription_started(
                    task,
                    snapshot,
                ),
            )
        except BaseException:
            # Admission can fail before the runner owns the PCM. Clearing this
            # correlation reservation restores Chat/capture availability while
            # leaving the scheduler responsible for any accepted native work.
            with self._state_lock:
                if self._transcription_task is task:
                    self._transcription_task = None
            raise

    def _emit_transcription_started(
        self,
        task: _TranscriptionTask,
        snapshot: TranscriptionJobSnapshot,
    ) -> None:
        """Publish started frames before a fast worker can publish completion."""

        if snapshot.job_id != task.request_id:
            raise RuntimeError("Transcription admission returned the wrong job.")
        with self._transcription_lifecycle_lock:
            if self._transcription_closing:
                raise TranscriptionJobClosedError(
                    "The transcription boundary is closing."
                )
            self._emit_event(
                "voice.transcription.started",
                request_id=task.request_id,
                data={
                    "sessionId": task.session_id,
                    "chatId": str(task.chat_id),
                },
            )
            self._emit_progress(
                task.request_id,
                "voice.transcribe",
                0,
                total=1,
                message="Transcribing audio",
            )

    def _on_transcription_terminal(
        self,
        snapshot: TranscriptionJobSnapshot,
    ) -> None:
        """Emit exactly one safe terminal sequence unless shutdown won."""

        with self._transcription_lifecycle_lock:
            with self._state_lock:
                task = self._transcription_task
                runner = self._transcription_runner
            if task is None or task.request_id != snapshot.job_id:
                return

            try:
                if not self._transcription_closing:
                    self._emit_transcription_terminal(task, snapshot)
            finally:
                # Clear correlation only after terminal output, keeping the
                # Backend busy until the Renderer can observe the final frame.
                with self._state_lock:
                    if self._transcription_task is task:
                        self._transcription_task = None
                if runner is not None:
                    try:
                        runner.forget(snapshot.job_id)
                    except (
                        TranscriptionJobNotFoundError,
                        TranscriptionJobConflictError,
                    ):
                        logger.error(
                            "Could not forget terminal transcription: request_id=%s.",
                            snapshot.job_id,
                        )

    def _emit_transcription_terminal(
        self,
        task: _TranscriptionTask,
        snapshot: TranscriptionJobSnapshot,
    ) -> None:
        """Map scheduler terminal state to bounded protocol events and response."""

        correlation = {
            "sessionId": task.session_id,
            "chatId": str(task.chat_id),
        }
        if snapshot.state == "succeeded" and snapshot.result is not None:
            result = snapshot.result
            self._emit_progress(
                task.request_id,
                "voice.transcribe",
                1,
                total=1,
                message=None,
            )
            self._emit_event(
                "voice.transcription.completed",
                request_id=task.request_id,
                data=correlation,
            )
            self._emit_response(
                task.request_id,
                {
                    "kind": "voice.transcription",
                    **correlation,
                    "text": result.text,
                    "language": result.language,
                    "languageProbability": result.language_probability,
                },
            )
            return

        if snapshot.state == "cancelled":
            self._emit_event(
                "voice.transcription.cancelled",
                request_id=task.request_id,
                data=correlation,
            )
            self._emit_error(
                task.request_id,
                "request.cancelled",
                "Voice transcription was cancelled.",
            )
            return

        if snapshot.state == "timed_out":
            self._emit_event(
                "voice.transcription.timed_out",
                request_id=task.request_id,
                data=correlation,
            )
            self._emit_error(
                task.request_id,
                "voice.transcription.timeout",
                "Local voice transcription timed out.",
                retryable=True,
            )
            return

        failure_code = (
            None if snapshot.failure is None else snapshot.failure.code
        )
        if failure_code == "invalid_request":
            code = "voice.transcription.invalid"
            message = "Voice transcription request was rejected."
            retryable = False
        elif failure_code == "unavailable":
            code = "voice.transcription.unavailable"
            message = "Local voice transcription is unavailable."
            retryable = False
        else:
            code = "voice.transcription.failed"
            message = "Local voice transcription failed."
            retryable = True
        self._emit_event(
            "voice.transcription.failed",
            request_id=task.request_id,
            data=correlation,
        )
        self._emit_error(
            task.request_id,
            code,
            message,
            retryable=retryable,
        )

    def _complete_voice_capture(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Validate one transient utterance without persisting or transcribing it."""

        active_chat = self._active_chat_snapshot()
        if active_chat is None:
            raise ProtocolValidationError(
                "protocol.not_initialized",
                "Backend must be initialized before this request.",
            )
        requested_chat_id = cast(str, params["chatId"])
        if requested_chat_id != str(active_chat.chat_id):
            raise ProtocolValidationError(
                "voice.capture.chat_mismatch",
                "Voice capture no longer belongs to the active Chat.",
            )
        with self._state_lock:
            if self._generation_task is not None:
                raise ProtocolValidationError(
                    "voice.capture.busy",
                    "Wait for the current reply before submitting voice input.",
                )
            if self._transcription_capacity_reserved_locked():
                raise ProtocolValidationError(
                    "voice.capture.busy",
                    "Wait for local transcription before submitting voice input.",
                )

        capture = VoiceCapture.from_base64(
            session_id=cast(str, params["sessionId"]),
            pcm_s16le_base64=cast(str, params["pcmBase64"]),
            sample_rate_hz=cast(int, params["sampleRateHz"]),
            sample_count=cast(int, params["sampleCount"]),
            speech_start_sample=cast(int, params["speechStartSample"]),
            speech_end_sample=cast(int, params["speechEndSample"]),
        )
        if (
            params["channelCount"] != VOICE_CAPTURE_CHANNEL_COUNT
            or params["sampleFormat"] != VOICE_CAPTURE_SAMPLE_FORMAT
        ):
            raise VoiceCaptureValidationError(
                "Voice capture PCM format is invalid."
            )

        # The PCM object intentionally remains request-local. The next Voice
        # slice may pass it directly to speech recognition; this milestone only
        # proves a validated, non-persistent boundary and returns safe metadata.
        self._emit_response(
            request_id,
            {
                "kind": "voice.capture",
                "sessionId": capture.session_id,
                "chatId": requested_chat_id,
                "sampleRateHz": capture.sample_rate_hz,
                "channelCount": VOICE_CAPTURE_CHANNEL_COUNT,
                "sampleFormat": VOICE_CAPTURE_SAMPLE_FORMAT,
                "sampleCount": capture.sample_count,
                "speechStartSample": capture.speech_start_sample,
                "speechEndSample": capture.speech_end_sample,
                "durationMs": capture.duration_ms,
                "speechDurationMs": capture.speech_duration_ms,
                "sha256Hex": capture.sha256_hex,
            },
        )

    @staticmethod
    def _resolve_active_chat(brain: Brain) -> ChatSession:
        """Resume a visible Chat using this model or create a safe default."""

        for chat_meta in brain.list_chats():
            if (
                not chat_meta.is_archived
                and chat_meta.model_name == brain.model_name
            ):
                return brain.get_chat(chat_meta.chat_id)

        return brain.create_chat(title="Elysia Chat")

    @staticmethod
    def _serialize_chat_summary(
        chat: ChatSession | ChatSessionMeta,
    ) -> JsonObject:
        """Map Stage 5 metadata to the stable desktop session shape."""

        if isinstance(chat, ChatSession):
            message_count = len(chat.messages)
            model_name = chat.model_settings.model_name
        else:
            message_count = chat.message_count
            model_name = chat.model_name

        return {
            "chatId": str(chat.chat_id),
            "title": chat.title,
            "mode": chat.mode,
            "createdAt": chat.created_at.isoformat(),
            "updatedAt": chat.updated_at.isoformat(),
            "messageCount": message_count,
            "projectId": (
                None if chat.project_id is None else str(chat.project_id)
            ),
            "modelName": model_name,
            "pinned": chat.is_pinned,
            "archived": chat.is_archived,
        }

    @classmethod
    def _serialize_active_chat(cls, chat: ChatSession) -> JsonObject:
        """Serialize an active Chat with complete messages and attachments."""

        result = cls._serialize_chat_summary(chat)
        result["messages"] = []
        serialized_messages = cast(list[JsonObject], result["messages"])
        for message in chat.messages:
            serialized: JsonObject = {
                "messageId": str(message.message_id),
                "role": message.role,
                "content": message.content,
                "createdAt": message.created_at.isoformat(),
                "attachments": [
                    {
                        "attachmentId": str(attachment.attachment_id),
                        "fileName": attachment.file_name,
                        "mediaType": attachment.media_type,
                        "sizeBytes": attachment.size_bytes,
                    }
                    for attachment in message.attachments
                ],
            }
            if message.grounded_answer is not None:
                serialized["groundedAnswer"] = (
                    cls._serialize_grounded_answer(
                        message.grounded_answer
                    )
                )
            serialized_messages.append(serialized)
        return result

    @staticmethod
    def _serialize_grounded_answer(answer: ChatGroundedAnswer) -> JsonObject:
        """Map persisted proof to the exact path-private renderer contract."""

        return {
            "status": answer.status,
            "contextPassageCount": answer.context_passage_count,
            "statements": [
                {
                    "statementId": statement.statement_id,
                    "kind": statement.kind,
                    "text": statement.text,
                    "citationIds": list(statement.citation_ids),
                }
                for statement in answer.statements
            ],
            "citations": [
                {
                    "citationId": citation.citation_id,
                    "kind": citation.kind,
                    "excerpt": citation.excerpt,
                    "fileName": citation.file_name,
                    "mediaType": citation.media_type,
                    "pageNumber": citation.page_number,
                    "locations": [
                        {
                            "kind": (
                                "table"
                                if isinstance(
                                    location,
                                    ChatGroundedTableLocation,
                                )
                                else "text"
                            ),
                            "blockOrdinal": location.block_ordinal,
                            "sourceStartCodePoint": (
                                location.source_start_code_point
                            ),
                            "sourceEndCodePoint": (
                                location.source_end_code_point
                            ),
                            **(
                                {
                                    "rowIndex": location.row_index,
                                    "columnIndex": location.column_index,
                                }
                                if isinstance(
                                    location,
                                    ChatGroundedTableLocation,
                                )
                                else {}
                            ),
                        }
                        for location in citation.locations
                    ],
                }
                for citation in answer.citations
            ],
        }

    def _require_attachment_store(self) -> AttachmentService:
        """Return the initialized path-private attachment application service."""

        if self._attachment_store is None:
            raise RuntimeError("Attachment storage is not initialized.")
        return self._attachment_store

    @staticmethod
    def _attachment_scope_from_params(params: JsonObject) -> AttachmentScope:
        """Build one already protocol-validated canonical scope."""

        raw_scope = cast(JsonObject, params["scope"])
        return AttachmentScope(
            kind=cast(Any, raw_scope["kind"]),
            id=cast(str, raw_scope["id"]),
        )

    def _attachment_references(
        self,
        scope: AttachmentScope,
        *,
        require_writable: bool = True,
    ) -> tuple[str, ...]:
        """Validate scope ownership and return committed Chat references."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        if scope.kind == "project":
            project = self._brain.get_project(ProjectId(scope.id))
            if require_writable and project.is_archived:
                raise ProjectArchivedError(
                    "Archived Projects cannot change local attachments."
                )
            return ()

        chat = self._brain.get_chat(ChatId(scope.id))
        if require_writable and chat.is_archived:
            raise ProtocolValidationError(
                "chat.archived",
                "Archived Chats cannot change local attachments.",
            )
        return self._chat_attachment_references(chat)

    @staticmethod
    def _chat_attachment_references(chat: ChatSession) -> tuple[str, ...]:
        """Return attachment IDs owned by canonical persisted messages."""

        return tuple(
            str(attachment.attachment_id)
            for message in chat.messages
            for attachment in message.attachments
        )

    @staticmethod
    def _serialize_attachment_state(state: AttachmentState) -> JsonObject:
        """Return only renderer-safe metadata and canonical limits."""

        return {
            "scope": {
                "kind": state.scope.kind,
                "id": state.scope.id,
            },
            "attachments": [
                {
                    "attachmentId": item.attachment_id,
                    "fileName": item.file_name,
                    "mediaType": item.media_type,
                    "sizeBytes": item.size_bytes,
                    "status": item.status,
                }
                for item in state.attachments
            ],
            "maxFileBytes": state.max_file_bytes,
            "maxFileCount": state.max_file_count,
        }

    def _list_attachments(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Reconcile and list one exact Chat or Project namespace."""

        scope = self._attachment_scope_from_params(params)
        if scope.kind == "project":
            raise ProtocolValidationError(
                "knowledge.required",
                "Project files are managed through Project Sources.",
            )
        references = self._attachment_references(
            scope,
            require_writable=False,
        )
        state = self._require_attachment_store().list_state(
            scope,
            references,
        )
        self._emit_response(
            request_id,
            self._serialize_attachment_state(state),
        )

    def _add_attachments(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Atomically copy trusted native files into one local namespace."""

        scope = self._attachment_scope_from_params(params)
        if scope.kind == "project":
            raise ProtocolValidationError(
                "knowledge.required",
                "Project files are managed through Project Sources.",
            )
        references = self._attachment_references(scope)
        source_paths = tuple(
            Path(path)
            for path in cast(list[str], params["sourcePaths"])
        )
        state = self._require_attachment_store().stage_files(
            scope,
            source_paths,
            references,
        )
        self._emit_response(
            request_id,
            self._serialize_attachment_state(state),
        )

    def _remove_attachment(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Remove one ready item only from its exact canonical namespace."""

        scope = self._attachment_scope_from_params(params)
        if scope.kind == "project":
            raise ProtocolValidationError(
                "knowledge.required",
                "Project files are managed through Project Sources.",
            )
        self._attachment_references(scope)
        state = self._require_attachment_store().remove(
            scope,
            cast(str, params["attachmentId"]),
        )
        self._emit_response(
            request_id,
            self._serialize_attachment_state(state),
        )

    def _session_result(
        self,
        *,
        include_archived: bool,
    ) -> JsonObject:
        """Refresh and serialize the active Chat plus one metadata listing."""

        brain = self._brain
        active_chat = self._active_chat_snapshot()
        if brain is None or active_chat is None:
            raise RuntimeError("Backend is not initialized.")

        refreshed_chat = brain.get_chat(active_chat.chat_id)
        with self._state_lock:
            current_chat = self._active_chat
            generation = self._generation_task
            if (
                current_chat is not None
                and current_chat.chat_id == active_chat.chat_id
                and (
                    generation is None
                    or generation.chat_id != active_chat.chat_id
                    or generation.done.is_set()
                )
            ):
                self._active_chat = refreshed_chat
        return {
            "activeChat": self._serialize_active_chat(refreshed_chat),
            "chats": [
                self._serialize_chat_summary(chat)
                for chat in brain.list_chats(
                    include_archived=include_archived,
                )
            ],
        }

    def _emit_session_response(
        self,
        request_id: str,
        *,
        include_archived: bool,
    ) -> None:
        """Emit the uniform result shared by desktop session operations."""

        self._emit_response(
            request_id,
            self._session_result(include_archived=include_archived),
        )

    def _list_chats(self, request_id: str, params: JsonObject) -> None:
        """Return the active Chat and the requested visible/archive listing."""

        self._emit_session_response(
            request_id,
            include_archived=cast(bool, params["includeArchived"]),
        )

    def _create_chat(self, request_id: str, params: JsonObject) -> None:
        """Create and activate one model-compatible Chat through Brain."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        chat = self._brain.create_chat(
            title=cast(str, params["title"]),
            mode=cast(ConversationMode, params["mode"]),
        )
        self._set_active_chat(chat)
        self._emit_session_response(request_id, include_archived=True)

    def _open_chat(self, request_id: str, params: JsonObject) -> None:
        """Activate a visible Chat owned by the connected Brain model."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        chat = self._brain.get_chat(ChatId(cast(str, params["chatId"])))
        if chat.is_archived:
            raise ProtocolValidationError(
                "chat.archived",
                "Archived Chat cannot become the active desktop Chat.",
            )
        if chat.model_settings.model_name != self._brain.model_name:
            raise ProtocolValidationError(
                "chat.model_mismatch",
                "Chat model does not match the connected desktop model.",
            )

        self._set_active_chat(chat)
        self._emit_session_response(request_id, include_archived=True)

    def _rename_chat(self, request_id: str, params: JsonObject) -> None:
        """Rename one Chat through Brain and refresh desktop session state."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        self._brain.rename_chat(
            ChatId(cast(str, params["chatId"])),
            cast(str, params["title"]),
        )
        self._emit_session_response(request_id, include_archived=True)

    def _pin_chat(self, request_id: str, params: JsonObject) -> None:
        """Set one Chat's pin state through Brain and return fresh state."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        self._brain.pin_chat(
            ChatId(cast(str, params["chatId"])),
            cast(bool, params["pinned"]),
        )
        self._emit_session_response(request_id, include_archived=True)

    def _archive_chat(self, request_id: str, params: JsonObject) -> None:
        """Set archive state and replace an archived active Chat safely."""

        active_chat = self._active_chat_snapshot()
        brain = self._brain
        if brain is None or active_chat is None:
            raise RuntimeError("Backend is not initialized.")

        chat_id = ChatId(cast(str, params["chatId"]))
        archived = cast(bool, params["archived"])
        was_active = chat_id == active_chat.chat_id
        brain.archive_chat(chat_id, archived)

        if was_active and archived:
            self._set_active_chat(self._resolve_active_chat(brain))

        self._emit_session_response(request_id, include_archived=True)

    def _delete_chat(self, request_id: str, params: JsonObject) -> None:
        """Delete one Chat and replace the active Chat when necessary."""

        active_chat = self._active_chat_snapshot()
        brain = self._brain
        if brain is None or active_chat is None:
            raise RuntimeError("Backend is not initialized.")

        chat_id = ChatId(cast(str, params["chatId"]))
        was_active = chat_id == active_chat.chat_id
        self._require_attachment_store().delete_owner(
            AttachmentScope(kind="chat", id=str(chat_id)),
            lambda: brain.delete_chat(chat_id),
        )

        if was_active:
            self._set_active_chat(self._resolve_active_chat(brain))

        self._emit_session_response(request_id, include_archived=True)

    @staticmethod
    def _serialize_project_summary(
        project: Project,
        *,
        chat_count: int,
    ) -> JsonObject:
        """Map one Project aggregate to the stable desktop shape."""

        return {
            "projectId": str(project.project_id),
            "name": project.name,
            "createdAt": project.created_at.isoformat(),
            "updatedAt": project.updated_at.isoformat(),
            "customInstructions": project.settings.custom_instructions,
            "workspacePath": (
                None
                if project.workspace_binding is None
                else project.workspace_binding.root_path
            ),
            "archived": project.is_archived,
            "chatCount": chat_count,
        }

    def _project_state_result(self) -> JsonObject:
        """Return all Projects together with one matching complete Chat state."""

        if self._brain is None or self._active_chat_snapshot() is None:
            raise RuntimeError("Backend is not initialized.")

        chat_state = self._session_result(include_archived=True)
        active_chat = self._active_chat_snapshot()
        if active_chat is None:
            raise RuntimeError("Backend active Chat became unavailable.")
        projects = self._brain.list_projects(include_archived=True)
        project_ids = {project.project_id for project in projects}

        if self._active_project_id not in project_ids:
            active_chat_project_id = active_chat.project_id
            if active_chat_project_id in project_ids:
                self._active_project_id = active_chat_project_id
            else:
                active_projects = tuple(
                    project
                    for project in projects
                    if not project.is_archived
                )
                selected = (
                    active_projects[0]
                    if active_projects
                    else projects[0] if projects else None
                )
                self._active_project_id = (
                    None if selected is None else selected.project_id
                )

        chat_counts = {
            str(project.project_id): 0
            for project in projects
        }
        raw_chats = cast(list[JsonObject], chat_state["chats"])
        for chat in raw_chats:
            project_id = cast(str | None, chat["projectId"])
            if project_id in chat_counts:
                chat_counts[project_id] += 1

        serialized_projects = [
            self._serialize_project_summary(
                project,
                chat_count=chat_counts[str(project.project_id)],
            )
            for project in projects
        ]
        active_project = next(
            (
                project
                for project in serialized_projects
                if project["projectId"] == self._active_project_id
            ),
            None,
        )
        return {
            "activeProject": active_project,
            "projects": serialized_projects,
            "chatState": chat_state,
        }

    def _emit_project_response(self, request_id: str) -> None:
        """Emit the canonical result shared by every Project operation."""

        self._emit_response(request_id, self._project_state_result())

    def _list_projects(self, request_id: str) -> None:
        """List every Project and select one stable active Project."""

        self._emit_project_response(request_id)

    def _create_project(self, request_id: str, params: JsonObject) -> None:
        """Create and select one first-class Project."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        project = self._brain.create_project(
            name=cast(str, params["name"]),
            custom_instructions=cast(
                str | None,
                params["customInstructions"],
            ),
        )
        self._active_project_id = project.project_id
        self._emit_project_response(request_id)

    def _open_project(self, request_id: str, params: JsonObject) -> None:
        """Select an existing Project without changing persisted state."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        project = self._brain.get_project(
            ProjectId(cast(str, params["projectId"]))
        )
        self._active_project_id = project.project_id
        self._emit_project_response(request_id)

    def _update_project(self, request_id: str, params: JsonObject) -> None:
        """Atomically replace one active Project's editable text fields."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._reject_project_mutation_during_knowledge()

        project_id = ProjectId(cast(str, params["projectId"]))
        self._brain.update_project(
            project_id,
            name=cast(str, params["name"]),
            custom_instructions=cast(
                str | None,
                params["customInstructions"],
            ),
        )
        self._active_project_id = project_id
        self._emit_project_response(request_id)

    def _set_project_workspace(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Bind, replace, or clear one active Project workspace path."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._reject_project_mutation_during_knowledge()

        project_id = ProjectId(cast(str, params["projectId"]))
        workspace_path = cast(str | None, params["workspacePath"])
        if workspace_path is None:
            self._brain.unbind_workspace(project_id)
        else:
            self._brain.bind_workspace(project_id, workspace_path)
        self._active_project_id = project_id
        self._emit_project_response(request_id)

    def _archive_project(self, request_id: str, params: JsonObject) -> None:
        """Set one Project's archive state without changing its Chats."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._reject_project_mutation_during_knowledge()

        project_id = ProjectId(cast(str, params["projectId"]))
        if cast(bool, params["archived"]):
            self._brain.archive_project(project_id)
        else:
            self._brain.restore_project(project_id)
        self._active_project_id = project_id
        self._emit_project_response(request_id)

    def _move_project_chat(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Move one idle Chat into, between, or out of Projects."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._reject_project_mutation_during_knowledge()

        raw_project_id = cast(str | None, params["projectId"])
        self._brain.move_chat(
            ChatId(cast(str, params["chatId"])),
            (
                None
                if raw_project_id is None
                else ProjectId(raw_project_id)
            ),
        )
        if raw_project_id is not None:
            self._active_project_id = ProjectId(raw_project_id)
        self._emit_project_response(request_id)

    def _reject_project_mutation_during_knowledge(self) -> None:
        """Keep Project authority stable while Knowledge work owns it.

        Lifecycle operations validate Project activity before entering their
        shared coordinator and may spend substantial time indexing afterward.
        Grounded generation similarly derives its corpus and Instructions from
        canonical Project state before it enters the shared read lease. Desktop
        Project writes do not participate in that coordinator, so rejecting
        them here prevents archive, instruction, workspace, or Chat membership
        changes from invalidating either authority snapshot in flight.
        """

        with self._state_lock:
            task = self._knowledge_task
            generation = self._generation_task
            if (
                task is not None
                and not task.done.is_set()
            ) or (
                generation is not None
                and generation.uses_project_knowledge
                and not generation.done.is_set()
            ):
                raise ProtocolValidationError(
                    "knowledge.busy",
                    "Wait for the active Project Source work.",
                )

    def _require_knowledge_runtime(self) -> DesktopKnowledgeRuntime:
        """Return the initialized trusted Project Source composition root."""

        if self._knowledge_runtime is None:
            raise ProtocolValidationError(
                "knowledge.unavailable",
                "Project Sources are unavailable in this Backend runtime.",
            )
        return self._knowledge_runtime

    def _acquire_knowledge_runtime_owner(self) -> object:
        """Pin the shared runtime for one admitted asynchronous consumer.

        Registration and shutdown admission share the lifecycle lock.  An
        owner that linearizes first may finish in the background; once closing
        wins, no later daemon can retain or reacquire the runtime.
        """

        with self._knowledge_lifecycle_lock:
            if self._knowledge_closing or self._knowledge_runtime is None:
                raise ProtocolValidationError(
                    "knowledge.unavailable",
                    "Project Sources are shutting down.",
                )
            owner = object()
            self._knowledge_runtime_owners.add(owner)
            return owner

    def _release_knowledge_runtime_owner(self, owner: object) -> None:
        """Release one exact runtime owner and retry any delayed close."""

        with self._knowledge_lifecycle_lock:
            if owner not in self._knowledge_runtime_owners:
                logger.error("Project Source runtime owner was already released.")
                return
            self._knowledge_runtime_owners.remove(owner)
        self._close_knowledge_runtime_after_shutdown()

    @staticmethod
    def _serialize_knowledge_source(source: KnowledgeSourceView) -> JsonObject:
        """Publish one source without its internal file identity or hashes."""

        return {
            "sourceId": source.source.link_id,
            "fileName": source.source.file_name,
            "mediaType": source.source.media_type,
            "sizeBytes": source.source.size_bytes,
            "state": source.state,
            "publishedAt": (
                None
                if source.published_at is None
                else source.published_at.isoformat()
            ),
            "operationId": (
                None
                if source.operation_id is None
                else str(source.operation_id)
            ),
        }

    @staticmethod
    def _serialize_knowledge_operation(
        operation: KnowledgeOperationSnapshot,
    ) -> JsonObject:
        """Publish one durable checkpoint through the sanitized wire shape."""

        return {
            "operationId": str(operation.operation_id),
            "projectId": operation.scope.id,
            "kind": operation.kind,
            "state": operation.state,
            "phase": operation.phase,
            "progressPercent": operation.progress_percent,
            "attempt": operation.attempt,
            "createdAt": operation.created_at.isoformat(),
            "updatedAt": operation.updated_at.isoformat(),
            "errorCode": operation.error_code,
            "targetSourceId": operation.target_link_id,
            "stagedSourceId": operation.staged_link_id,
        }

    def _knowledge_state_result(
        self,
        project_id: ProjectId,
    ) -> JsonObject:
        """Return current source health and durable operation history."""

        runtime = self._require_knowledge_runtime()
        return {
            "kind": "knowledge.state",
            "projectId": str(project_id),
            "sources": [
                self._serialize_knowledge_source(source)
                for source in runtime.lifecycle.list_project_sources(
                    project_id
                )
            ],
            "operations": [
                self._serialize_knowledge_operation(operation)
                for operation in runtime.lifecycle.list_operations(project_id)
            ],
        }

    def _list_project_knowledge(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """List one exact Project corpus without exposing private storage."""

        project_id = ProjectId(cast(str, params["projectId"]))
        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._reject_concurrent_grounded_generation()
        with self._state_lock:
            if (
                self._knowledge_task is not None
                and not self._knowledge_task.done.is_set()
            ):
                raise ProtocolValidationError(
                    "knowledge.busy",
                    "Wait for the active Project Source operation.",
                )
        self._brain.get_project(project_id)
        self._emit_response(
            request_id,
            self._knowledge_state_result(project_id),
        )

    def _reject_concurrent_grounded_generation(self) -> None:
        """Keep Knowledge control reads off a lease owned by grounded Chat.

        Protocol requests are dispatched serially, so this preflight closes
        the only race before a synchronous catalog/journal read.  Without it,
        ``knowledge.list`` or mutation admission could wait on the grounded
        answer's coordinator lease in the stdin thread and make that same Chat
        impossible to cancel.
        """

        with self._state_lock:
            generation = self._generation_task
            if (
                generation is not None
                and generation.uses_project_knowledge
                and not generation.done.is_set()
            ):
                raise ProtocolValidationError(
                    "knowledge.busy",
                    "Wait for the active Project knowledge answer.",
                )

    def _start_knowledge_operation(
        self,
        request_id: str,
        params: JsonObject,
        method: str,
    ) -> None:
        """Admit one background lifecycle operation so cancel stays responsive."""

        runtime = self._require_knowledge_runtime()
        project_id = ProjectId(cast(str, params["projectId"]))
        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._brain.get_project(project_id)
        self._reject_concurrent_grounded_generation()
        with self._knowledge_lifecycle_lock:
            if self._knowledge_closing:
                raise ProtocolValidationError(
                    "knowledge.unavailable",
                    "Project Sources are shutting down.",
                )
        with self._state_lock:
            existing = self._knowledge_task
            if existing is not None and not existing.done.is_set():
                raise ProtocolValidationError(
                    "knowledge.busy",
                    "Another Project Source operation is already active.",
                )
            runtime_owner = self._acquire_knowledge_runtime_owner()
            monitor_runtime_owner: object | None = None
            try:
                monitor_runtime_owner = self._acquire_knowledge_runtime_owner()
                task = _KnowledgeTask(
                    request_id=request_id,
                    project_id=project_id,
                    method=method,
                    initial_operation_ids=frozenset(
                        str(operation.operation_id)
                        for operation in runtime.lifecycle.list_operations(
                            project_id
                        )
                    ),
                    runtime_owner=runtime_owner,
                    monitor_runtime_owner=monitor_runtime_owner,
                )
            except Exception:
                if monitor_runtime_owner is not None:
                    self._release_knowledge_runtime_owner(
                        monitor_runtime_owner
                    )
                self._release_knowledge_runtime_owner(runtime_owner)
                raise
            self._knowledge_task = task
        try:
            worker = Thread(
                target=self._run_knowledge_operation,
                args=(task, dict(params)),
                name=f"elysia-{method}-{request_id}",
                daemon=True,
            )
            task.thread = worker
            worker.start()
        except Exception:
            task.done.set()
            with self._state_lock:
                if self._knowledge_task is task:
                    self._knowledge_task = None
            if task.monitor_runtime_owner is not None:
                monitor_runtime_owner = task.monitor_runtime_owner
                task.monitor_runtime_owner = None
                self._release_knowledge_runtime_owner(monitor_runtime_owner)
            if task.runtime_owner is not None:
                runtime_owner = task.runtime_owner
                task.runtime_owner = None
                self._release_knowledge_runtime_owner(runtime_owner)
            raise

    def _emit_knowledge_output(
        self,
        emit: Callable[[], None],
    ) -> bool:
        """Publish one Knowledge frame only before shutdown owns output."""

        with self._knowledge_lifecycle_lock:
            if self._knowledge_closing:
                return False
            emit()
            return True

    def _settle_knowledge_task(self, task: _KnowledgeTask) -> None:
        """Release mutation admission and runtime ownership before terminal I/O."""

        with self._state_lock:
            task.done.set()
            if self._knowledge_task is task:
                self._knowledge_task = None
        runtime_owner = task.runtime_owner
        task.runtime_owner = None
        if runtime_owner is not None:
            self._release_knowledge_runtime_owner(runtime_owner)

    def _emit_knowledge_operation_event(
        self,
        task: _KnowledgeTask,
        event: Literal[
            "knowledge.operation.changed",
            "knowledge.operation.completed",
        ],
        operation: KnowledgeOperationSnapshot,
    ) -> None:
        """Publish one path-private lifecycle snapshot before shutdown."""

        self._emit_knowledge_output(
            lambda: self._emit_event(
                event,
                request_id=task.request_id,
                data={
                    "projectId": str(task.project_id),
                    "operation": self._serialize_knowledge_operation(
                        operation
                    ),
                },
            )
        )

    def _stop_knowledge_monitor(
        self,
        task: _KnowledgeTask,
        monitor: Thread | None,
    ) -> None:
        """Drain the advisory monitor before any terminal request frame."""

        task.monitor_stop.set()
        if monitor is not None:
            # This join runs only inside the daemon lifecycle worker. Shutdown
            # waits on ``task.done`` with its own finite timeout, so draining
            # here can preserve release-before-terminal ordering without ever
            # blocking the protocol dispatcher or process teardown.
            monitor.join()

    def _monitor_knowledge_operation(self, task: _KnowledgeTask) -> None:
        """Publish journal revisions while the lifecycle worker is running."""

        try:
            revisions: dict[str, int] = {}
            while not task.monitor_stop.wait(0.1):
                operations = self._require_knowledge_runtime().lifecycle.list_operations(
                    task.project_id
                )
                for operation in operations:
                    if task.monitor_stop.is_set():
                        return
                    operation_id = str(operation.operation_id)
                    if operation_id in task.initial_operation_ids:
                        continue
                    if revisions.get(operation_id) == operation.journal_revision:
                        continue
                    revisions[operation_id] = operation.journal_revision
                    self._emit_knowledge_operation_event(
                        task,
                        "knowledge.operation.changed",
                        operation,
                    )
        except Exception:
            # Monitoring is advisory; the lifecycle result and durable journal
            # remain authoritative if a read races replacement.
            logger.exception(
                "Project Source progress monitor failed: request_id=%s.",
                task.request_id,
            )
        finally:
            runtime_owner = task.monitor_runtime_owner
            task.monitor_runtime_owner = None
            if runtime_owner is not None:
                self._release_knowledge_runtime_owner(runtime_owner)

    def _execute_knowledge_operation(
        self,
        task: _KnowledgeTask,
        params: JsonObject,
    ) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Invoke the exact lifecycle method selected by the closed protocol."""

        lifecycle = self._require_knowledge_runtime().lifecycle
        cancel = task.should_cancel
        source_id = cast(str | None, params.get("sourceId"))
        if task.method == "knowledge.source.add":
            results: list[KnowledgeOperationSnapshot] = []
            for raw_path in cast(list[str], params["sourcePaths"]):
                result = lifecycle.add_project_source(
                    task.project_id,
                    Path(raw_path),
                    cancel_requested=cancel,
                )
                results.append(result)
                if result.state == "cancelled":
                    break
            return tuple(results)
        if task.method == "knowledge.source.replace":
            return (
                lifecycle.replace_project_source(
                    task.project_id,
                    cast(str, source_id),
                    Path(cast(str, params["sourcePath"])),
                    cancel_requested=cancel,
                ),
            )
        if task.method == "knowledge.source.reindex":
            return (
                lifecycle.reindex_project_source(
                    task.project_id,
                    cast(str, source_id),
                    cancel_requested=cancel,
                ),
            )
        if task.method == "knowledge.source.delete":
            return (
                lifecycle.delete_project_source(
                    task.project_id,
                    cast(str, source_id),
                    cancel_requested=cancel,
                ),
            )
        if task.method == "knowledge.project.rebuild":
            return (
                lifecycle.rebuild_project_sources(
                    task.project_id,
                    cancel_requested=cancel,
                ),
            )
        if task.method == "knowledge.project.revoke":
            return (
                lifecycle.revoke_project_sources(
                    task.project_id,
                    cancel_requested=cancel,
                ),
            )
        if task.method == "knowledge.recover":
            return lifecycle.recover_pending(task.project_id)
        raise ProtocolValidationError(
            "protocol.method_not_found",
            "Unknown Project Source operation.",
        )

    def _run_knowledge_operation(
        self,
        task: _KnowledgeTask,
        params: JsonObject,
    ) -> None:
        """Run one lifecycle saga and emit safe correlated terminal frames."""

        monitor: Thread | None = None
        try:
            monitor_thread = Thread(
                target=self._monitor_knowledge_operation,
                args=(task,),
                name=f"elysia-knowledge-monitor-{task.request_id}",
                daemon=True,
            )
            monitor = monitor_thread
            monitor_thread.start()
        except Exception:
            # Journal monitoring is advisory. Release its separately admitted
            # runtime owner and let the authoritative operation continue.
            monitor = None
            runtime_owner = task.monitor_runtime_owner
            task.monitor_runtime_owner = None
            if runtime_owner is not None:
                self._release_knowledge_runtime_owner(runtime_owner)
            logger.error(
                "Project Source progress monitor could not start: request_id=%s.",
                task.request_id,
            )
        terminal_error: tuple[str, str, bool] | None = None
        terminal_result: JsonObject | None = None
        try:
            self._emit_knowledge_output(
                lambda: self._emit_progress(
                    task.request_id,
                    "knowledge.lifecycle",
                    0,
                    total=100,
                    message="Updating Project Sources",
                )
            )
            results = self._execute_knowledge_operation(task, params)
            cancelled = any(
                operation.state == "cancelled" for operation in results
            )
            state_result = (
                None
                if any(
                    operation.state != "succeeded"
                    for operation in results
                )
                else self._knowledge_state_result(task.project_id)
            )
            # Stop future polls before publishing the authoritative operation
            # snapshots. The daemon worker drains an in-flight read in
            # ``finally`` before it releases admission and emits terminal
            # output; shutdown itself remains bounded by its outer Event wait.
            task.monitor_stop.set()
            for operation in results:
                self._emit_knowledge_operation_event(
                    task,
                    (
                        "knowledge.operation.completed"
                        if operation.state
                        in {"succeeded", "cancelled", "failed"}
                        else "knowledge.operation.changed"
                    ),
                    operation,
                )
            if cancelled:
                terminal_error = (
                    "request.cancelled",
                    "Project Source operation was cancelled.",
                    False,
                )
            elif any(
                operation.state
                in {"running", "cancel_requested", "recovery_required"}
                for operation in results
            ):
                terminal_error = (
                    "knowledge.recovery_required",
                    "Project Source recovery still requires attention.",
                    True,
                )
            elif any(operation.state == "failed" for operation in results):
                terminal_error = (
                    "knowledge.failed",
                    "Project Source operation failed safely.",
                    True,
                )
            else:
                self._emit_knowledge_output(
                    lambda: self._emit_progress(
                        task.request_id,
                        "knowledge.lifecycle",
                        100,
                        total=100,
                        message=None,
                    )
                )
                assert state_result is not None
                terminal_result = state_result
        except KnowledgeLifecycleValidationError as error:
            self._emit_latest_knowledge_terminals(task)
            terminal_error = ("knowledge.invalid", str(error), False)
        except KnowledgeLifecycleNotFoundError as error:
            self._emit_latest_knowledge_terminals(task)
            terminal_error = ("knowledge.not_found", str(error), False)
        except KnowledgeLifecycleConflictError as error:
            self._emit_latest_knowledge_terminals(task)
            terminal_error = ("knowledge.conflict", str(error), True)
        except KnowledgeLifecycleRecoveryError as error:
            self._emit_latest_knowledge_terminals(task)
            terminal_error = (
                "knowledge.recovery_required",
                str(error),
                True,
            )
        except KnowledgeLifecycleStorageError as error:
            self._emit_latest_knowledge_terminals(task)
            terminal_error = (
                "knowledge.storage_failed",
                str(error),
                True,
            )
        except Exception:
            self._emit_latest_knowledge_terminals(task)
            logger.exception(
                "Project Source operation failed: method=%s request_id=%s.",
                task.method,
                task.request_id,
            )
            terminal_error = (
                "knowledge.failed",
                "Project Source operation failed safely.",
                True,
            )
        finally:
            self._stop_knowledge_monitor(task, monitor)
            self._settle_knowledge_task(task)

        if terminal_error is not None:
            code, message, retryable = terminal_error
            self._emit_knowledge_output(
                lambda: self._emit_error(
                    task.request_id,
                    code,
                    message,
                    retryable=retryable,
                )
            )
        elif terminal_result is not None:
            self._emit_knowledge_output(
                lambda: self._emit_response(
                    task.request_id,
                    terminal_result,
                )
            )

    def _emit_latest_knowledge_terminals(
        self,
        task: _KnowledgeTask,
    ) -> None:
        """Publish durable failure/recovery checkpoints before async errors."""

        try:
            lifecycle = self._require_knowledge_runtime().lifecycle
            for operation in lifecycle.list_operations(task.project_id):
                if str(operation.operation_id) in task.initial_operation_ids:
                    continue
                self._emit_knowledge_operation_event(
                    task,
                    (
                        "knowledge.operation.completed"
                        if operation.state
                        in {"succeeded", "cancelled", "failed"}
                        else "knowledge.operation.changed"
                    ),
                    operation,
                )
        except Exception:
            logger.exception(
                "Project Source terminal checkpoint could not be emitted: "
                "request_id=%s.",
                task.request_id,
            )

    def _export_project_source(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Start verified export without blocking cancel or shutdown input."""

        self._require_knowledge_runtime()
        project_id = ProjectId(cast(str, params["projectId"]))
        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")
        self._brain.get_project(project_id)
        self._reject_concurrent_grounded_generation()
        with self._knowledge_lifecycle_lock:
            if self._knowledge_closing:
                raise ProtocolValidationError(
                    "knowledge.unavailable",
                    "Project Sources are shutting down.",
                )
        with self._state_lock:
            existing = self._knowledge_task
            if existing is not None and not existing.done.is_set():
                raise ProtocolValidationError(
                    "knowledge.busy",
                    "Another Project Source operation is already active.",
                )
            runtime_owner = self._acquire_knowledge_runtime_owner()
            task = _KnowledgeTask(
                request_id=request_id,
                project_id=project_id,
                method="knowledge.source.export",
                runtime_owner=runtime_owner,
            )
            self._knowledge_task = task
        try:
            worker = Thread(
                target=self._run_knowledge_export,
                args=(task, dict(params)),
                name=f"elysia-knowledge-export-{request_id}",
                daemon=True,
            )
            task.thread = worker
            worker.start()
        except Exception:
            task.done.set()
            with self._state_lock:
                if self._knowledge_task is task:
                    self._knowledge_task = None
            if task.runtime_owner is not None:
                runtime_owner = task.runtime_owner
                task.runtime_owner = None
                self._release_knowledge_runtime_owner(runtime_owner)
            raise

    def _run_knowledge_export(
        self,
        task: _KnowledgeTask,
        params: JsonObject,
    ) -> None:
        """Copy one verified original and publish one correlated safe result."""

        terminal_error: tuple[str, str, bool] | None = None
        terminal_result: JsonObject | None = None
        try:
            self._emit_knowledge_output(
                lambda: self._emit_progress(
                    task.request_id,
                    "knowledge.export",
                    0,
                    total=1,
                    message="Exporting verified original",
                )
            )
            result = self._require_knowledge_runtime().export.export_original(
                task.project_id,
                cast(str, params["sourceId"]),
                Path(cast(str, params["destination"])),
                overwrite=cast(bool, params["overwrite"]),
                should_cancel=task.should_cancel,
            )
            payload: JsonObject = {
                "kind": "knowledge.export",
                "fileName": result.file_name,
                "mediaType": result.media_type,
                "bytesWritten": result.bytes_written,
            }
            self._emit_knowledge_output(
                lambda: self._emit_progress(
                    task.request_id,
                    "knowledge.export",
                    1,
                    total=1,
                    message=None,
                )
            )
            terminal_result = payload
        except KnowledgeExportCancelledError as error:
            terminal_error = ("request.cancelled", str(error), False)
        except KnowledgeLifecycleValidationError as error:
            terminal_error = ("knowledge.invalid", str(error), False)
        except KnowledgeLifecycleNotFoundError as error:
            terminal_error = ("knowledge.not_found", str(error), False)
        except KnowledgeLifecycleConflictError as error:
            terminal_error = ("knowledge.conflict", str(error), True)
        except KnowledgeLifecycleStorageError as error:
            terminal_error = (
                "knowledge.storage_failed",
                str(error),
                True,
            )
        except Exception:
            logger.exception(
                "Project Source export failed: request_id=%s.",
                task.request_id,
            )
            terminal_error = (
                "knowledge.failed",
                "Project Source export failed safely.",
                True,
            )
        finally:
            self._settle_knowledge_task(task)

        if terminal_error is not None:
            code, message, retryable = terminal_error
            self._emit_knowledge_output(
                lambda: self._emit_error(
                    task.request_id,
                    code,
                    message,
                    retryable=retryable,
                )
            )
        elif terminal_result is not None:
            self._emit_knowledge_output(
                lambda: self._emit_response(task.request_id, terminal_result)
            )

    def _start_chat_stream(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Start one cancellable new-turn generation worker."""

        raw_message = cast(str, params["message"])
        use_project_knowledge = cast(
            bool,
            params.get("useProjectKnowledge", False),
        )
        attachment_ids = tuple(
            cast(list[str], params.get("attachmentIds", []))
        )
        brain, task = self._reserve_generation(
            request_id,
            params,
            method="chat.stream",
        )
        attachment_records: tuple[AttachmentMetadata, ...] = ()
        try:
            attachment_records = self._claim_chat_attachments(
                task.chat_id,
                attachment_ids,
            )
            run: Callable[[Brain, _GenerationTask], Iterable[str]]
            if use_project_knowledge:
                answerer = self._require_knowledge_runtime().answers.answer
                run = (
                    lambda active_brain, active_task: (
                        active_brain.stream_grounded_chat(
                            active_task.chat_id,
                            raw_message,
                            answerer,
                            attachments=attachment_records,
                            should_cancel=active_task.should_cancel,
                            begin_commit=active_task.begin_commit,
                        )
                    )
                )
            elif attachment_records:
                run = lambda active_brain, active_task: active_brain.stream_chat(
                    active_task.chat_id,
                    raw_message,
                    attachments=attachment_records,
                    should_cancel=active_task.should_cancel,
                    begin_commit=active_task.begin_commit,
                )
            else:
                # Preserve the older callable shape for injected test/fallback
                # Brain implementations that have no attachment support.
                run = lambda active_brain, active_task: active_brain.stream_chat(
                    active_task.chat_id,
                    raw_message,
                    should_cancel=active_task.should_cancel,
                    begin_commit=active_task.begin_commit,
                )
            self._launch_reserved_generation(brain, task, run)
        except Exception:
            if attachment_records:
                self._release_chat_attachment_claims(
                    task.chat_id,
                    tuple(
                        str(attachment.attachment_id)
                        for attachment in attachment_records
                    ),
                )
            self._abandon_generation_task(task)
            raise

    def _claim_chat_attachments(
        self,
        chat_id: ChatId,
        attachment_ids: tuple[str, ...],
    ) -> tuple[AttachmentMetadata, ...]:
        """Reserve ready drafts and map them to the stable Chat domain."""

        if not attachment_ids:
            return ()
        scope = AttachmentScope(kind="chat", id=str(chat_id))
        references = self._attachment_references(scope)
        store = self._require_attachment_store()
        store.reconcile(scope, references)
        items = store.claim_chat(scope, attachment_ids)
        return tuple(
            AttachmentMetadata(
                attachment_id=AttachmentId(item.attachment_id),
                file_name=item.file_name,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
            )
            for item in items
        )

    def _reconcile_chat_attachments(self, chat_id: ChatId) -> None:
        """Finish committed blobs or release failed/cancelled claims."""

        scope = AttachmentScope(kind="chat", id=str(chat_id))
        try:
            references = self._attachment_references(scope)
            self._require_attachment_store().reconcile(scope, references)
        except Exception:
            # The Chat commit is already canonical at this point. A storage
            # cleanup error must not invite the renderer to submit it twice;
            # the next list/restart retries deterministic reconciliation.
            logger.exception(
                "Attachment reconciliation failed: chat_id=%s.",
                chat_id,
            )

    def _release_chat_attachment_claims(
        self,
        chat_id: ChatId,
        attachment_ids: tuple[str, ...],
    ) -> None:
        """Roll back only claims made by a generation that did not launch."""

        scope = AttachmentScope(kind="chat", id=str(chat_id))
        try:
            self._require_attachment_store().release_chat_claims(
                scope,
                attachment_ids,
            )
        except Exception:
            logger.exception(
                "Attachment claim rollback failed: chat_id=%s.",
                chat_id,
            )

    def _finish_generation_task(self, task: _GenerationTask) -> None:
        """Publish Backend readiness before emitting a terminal response."""

        if task.done.is_set():
            return
        self._reconcile_chat_attachments(task.chat_id)
        task.finish()
        with self._state_lock:
            if self._generation_task is task:
                self._generation_task = None
        runtime_owner = task.knowledge_runtime_owner
        task.knowledge_runtime_owner = None
        if runtime_owner is not None:
            self._release_knowledge_runtime_owner(runtime_owner)

    def _start_chat_retry(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Start one cancellable regenerate or edit-and-retry worker."""

        user_message_id = ChatMessageId(cast(str, params["userMessageId"]))
        assistant_message_id = ChatMessageId(
            cast(str, params["assistantMessageId"])
        )
        message = cast(str | None, params.get("message"))
        use_project_knowledge = cast(
            bool,
            params.get("useProjectKnowledge", False),
        )
        self._start_generation(
            request_id,
            params,
            method="chat.retry",
            run=(
                (
                    lambda brain, task: brain.stream_grounded_retry(
                        task.chat_id,
                        user_message_id,
                        assistant_message_id,
                        self._require_knowledge_runtime().answers.answer,
                        message,
                        should_cancel=task.should_cancel,
                        begin_commit=task.begin_commit,
                    )
                )
                if use_project_knowledge
                else (
                    lambda brain, task: brain.stream_retry(
                        task.chat_id,
                        user_message_id,
                        assistant_message_id,
                        message,
                        should_cancel=task.should_cancel,
                        begin_commit=task.begin_commit,
                    )
                )
            ),
        )

    def _start_generation(
        self,
        request_id: str,
        params: JsonObject,
        *,
        method: str,
        run: Callable[[Brain, _GenerationTask], Iterable[str]],
    ) -> None:
        """Validate and launch one globally exclusive streamed operation."""

        brain, task = self._reserve_generation(
            request_id,
            params,
            method=method,
        )
        self._launch_reserved_generation(brain, task, run)

    def _reserve_generation(
        self,
        request_id: str,
        params: JsonObject,
        *,
        method: str,
    ) -> tuple[Brain, _GenerationTask]:
        """Atomically admit one request before it can mutate attachment state."""

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        brain = self._brain
        raw_chat_id = cast(str, params["chatId"])
        speak_reply = cast(bool, params["speakReply"])
        uses_project_knowledge = cast(
            bool,
            params.get("useProjectKnowledge", False),
        )
        with self._state_lock:
            active_chat = self._active_chat
            existing = self._generation_task
            if existing is not None and not existing.done.is_set():
                raise ChatBusyError(
                    "Another desktop Chat generation is already active."
                )
            if self._transcription_capacity_reserved_locked():
                raise ChatBusyError(
                    "Wait for local voice transcription before generating a reply."
                )
            if active_chat is None:
                raise RuntimeError("Backend is not initialized.")
            if raw_chat_id != str(active_chat.chat_id):
                raise ProtocolValidationError(
                    "chat.not_active",
                    "chatId is not the active desktop Chat.",
                )
            if uses_project_knowledge and active_chat.project_id is None:
                raise ProtocolValidationError(
                    "knowledge.unauthorized",
                    "Project Sources require an active Project Chat.",
                )

            task = _GenerationTask(
                request_id=request_id,
                chat_id=ChatId(raw_chat_id),
                method=method,
                speak_reply=speak_reply,
                uses_project_knowledge=uses_project_knowledge,
            )
            if (
                task.uses_project_knowledge
                and self._knowledge_task is not None
                and not self._knowledge_task.done.is_set()
            ):
                raise ProtocolValidationError(
                    "knowledge.busy",
                    "Wait for the active Project Source operation.",
                )
            if task.uses_project_knowledge:
                task.knowledge_runtime_owner = (
                    self._acquire_knowledge_runtime_owner()
                )
            self._generation_task = task

        return brain, task

    def _launch_reserved_generation(
        self,
        brain: Brain,
        task: _GenerationTask,
        run: Callable[[Brain, _GenerationTask], Iterable[str]],
    ) -> None:
        """Publish and start a worker for an already admitted request."""

        try:
            self._emit_event(
                "chat.started",
                request_id=task.request_id,
                data={"chatId": str(task.chat_id)},
            )
            self._emit_progress(
                task.request_id,
                "chat.generate",
                0,
                total=None,
                message="Generating reply",
            )
            worker = Thread(
                target=self._run_generation,
                args=(brain, task, run),
                name=f"elysia-{task.method}-{task.request_id}",
                daemon=True,
            )
            task.thread = worker
            worker.start()
        except Exception:
            self._abandon_generation_task(task)
            raise

    def _abandon_generation_task(self, task: _GenerationTask) -> None:
        """Clear an admitted request that never became a running worker."""

        if not task.done.is_set():
            task.finish()
        with self._state_lock:
            if self._generation_task is task:
                self._generation_task = None
        runtime_owner = task.knowledge_runtime_owner
        task.knowledge_runtime_owner = None
        if runtime_owner is not None:
            self._release_knowledge_runtime_owner(runtime_owner)

    def _run_generation(
        self,
        brain: Brain,
        task: _GenerationTask,
        run: Callable[[Brain, _GenerationTask], Iterable[str]],
    ) -> None:
        """Consume one Brain generator and publish its correlated frames."""

        reply_chunks: list[str] = []
        reply_length = 0
        sequence = 0
        raw_chat_id = str(task.chat_id)
        speech_finished = False
        speech_coordinator = self._speech_coordinator
        # Voice Call registers its requested turn before either speech
        # transport answers. If Python cannot attach a managed turn, a
        # zero-work terminal retires that exact owner. Ordinary text Chat sets
        # speakReply=false, owns no speech delivery, and must emit no terminal.
        zero_work_terminal_pending = (
            task.speak_reply and speech_coordinator is not None
        )
        zero_work_terminal_emitted = False
        zero_work_success_state: Literal["completed", "cancelled"] = "completed"

        try:
            if (
                task.speak_reply
                and speech_coordinator is not None
                and self._runtime_settings.auto_read_aloud
            ):
                try:
                    task.attach_speech_turn(
                        speech_coordinator.start_turn(
                            task.request_id,
                            raw_chat_id,
                        )
                    )
                    zero_work_terminal_pending = False
                except Exception:
                    # Optional speech must never delay, cancel, or replace the
                    # canonical text generation path. Report a cancelled
                    # speech terminal so automatic relisten cannot mistake
                    # failed admission for a clean spoken reply.
                    zero_work_success_state = "cancelled"
                    logger.error(
                        "Desktop speech turn could not start: request_id=%s.",
                        task.request_id,
                    )
            for chunk in run(brain, task):
                if not chunk:
                    continue
                reply_length += len(chunk)
                if reply_length > MAX_MESSAGE_LENGTH:
                    raise ProtocolValidationError(
                        "chat.reply_too_large",
                        "The local model reply exceeds the protocol limit.",
                    )
                reply_chunks.append(chunk)
                self._emit(
                    build_stream_chunk(
                        task.request_id,
                        sequence,
                        chunk,
                        done=False,
                    )
                )
                task.feed_speech(chunk)
                sequence += 1

            reply = "".join(reply_chunks)
            if not reply:
                raise ProtocolValidationError(
                    "chat.empty_reply",
                    "The local model returned an empty reply.",
                )

            # Natural generator exhaustion means Brain has crossed its commit
            # boundary. Only now may the final unterminated speech tail play.
            task.finish_speech()
            speech_finished = True
            if zero_work_terminal_pending:
                self._emit_zero_work_speech_terminal(
                    task.request_id,
                    raw_chat_id,
                    state=zero_work_success_state,
                )
                zero_work_terminal_emitted = True

            try:
                refreshed_chat = brain.get_chat(task.chat_id)
            except Exception:
                # Natural generator exhaustion means Brain already committed.
                # A cache refresh failure must not invite a duplicate retry.
                logger.exception(
                    "Committed Chat could not refresh the desktop cache: "
                    "request_id=%s chat_id=%s.",
                    task.request_id,
                    task.chat_id,
                )
            else:
                with self._state_lock:
                    if (
                        self._active_chat is not None
                        and self._active_chat.chat_id == task.chat_id
                    ):
                        self._active_chat = refreshed_chat

            self._emit(
                build_stream_chunk(
                    task.request_id,
                    sequence,
                    "",
                    done=True,
                )
            )
            self._emit_progress(
                task.request_id,
                "chat.generate",
                1,
                total=1,
                message=None,
            )
            self._emit_event(
                "chat.completed",
                request_id=task.request_id,
                data={"chatId": raw_chat_id},
            )
            self._finish_generation_task(task)
            self._emit_response(
                task.request_id,
                {"chatId": raw_chat_id, "reply": reply},
            )
        except GenerationCancelledError:
            self._emit_event(
                "chat.cancelled",
                request_id=task.request_id,
                data={"chatId": raw_chat_id},
            )
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "request.cancelled",
                "Chat generation was cancelled.",
            )
        except ProtocolValidationError as error:
            self._finish_generation_task(task)
            self._emit_error(task.request_id, error.code, str(error))
        except ChatRetryTargetError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "chat.retry_target",
                str(error),
            )
        except ChatBusyError as error:
            self._finish_generation_task(task)
            self._emit_error(task.request_id, "chat.busy", str(error))
        except ChatNotFoundError as error:
            self._finish_generation_task(task)
            self._emit_error(task.request_id, "chat.not_found", str(error))
        except GroundedAnswerUnavailableError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.model_unavailable",
                str(error),
                retryable=True,
            )
        except RetrievalUnavailableError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.model_unavailable",
                str(error),
                retryable=True,
            )
        except ProjectSourceAuthorizationError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.unauthorized",
                str(error),
            )
        except ProjectSourceNotFoundError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.not_found",
                str(error),
            )
        except ProjectSourceStaleError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.stale",
                str(error),
                retryable=True,
            )
        except ProjectSourceConflictError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.conflict",
                str(error),
                retryable=True,
            )
        except ProjectSourceValidationError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.invalid",
                str(error),
            )
        except ProjectSourceStorageError as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.storage_failed",
                str(error),
                retryable=True,
            )
        except (ProjectSourceError, GroundedAnswerError) as error:
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.answer_failed",
                str(error),
                retryable=True,
            )
        except DocumentError as error:
            # Retrieval and vector failures are already normalized to stable,
            # path-free Document errors by the grounding boundary. Preserve
            # that typed Knowledge failure instead of misreporting Chat I/O.
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "knowledge.answer_failed",
                str(error),
                retryable=True,
            )
        except Exception:
            logger.exception(
                "Desktop generation failed: method=%s request_id=%s.",
                task.method,
                task.request_id,
            )
            self._finish_generation_task(task)
            self._emit_error(
                task.request_id,
                "chat.failed",
                "Chat request failed in the local Backend.",
                retryable=True,
            )
        finally:
            if not speech_finished:
                task.cancel_speech()
                if (
                    zero_work_terminal_pending
                    and not zero_work_terminal_emitted
                ):
                    self._emit_zero_work_speech_terminal(
                        task.request_id,
                        raw_chat_id,
                        state="cancelled",
                    )
            self._finish_generation_task(task)

    def _cancel_request(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Cancel one matching Chat, Knowledge, or transcription request."""

        target_request_id = cast(str, params["requestId"])
        with self._state_lock:
            generation = self._generation_task
            generation_stopped = (
                generation is not None
                and generation.request_id == target_request_id
                and generation.request_cancel()
            )
        if generation_stopped:
            self._emit_response(request_id, {"stopped": True})
            return

        with self._state_lock:
            knowledge = self._knowledge_task
            knowledge_stopped = (
                knowledge is not None
                and knowledge.request_id == target_request_id
                and knowledge.request_cancel()
            )
        if knowledge_stopped:
            # A volatile Event is enough to wake the in-process worker but not
            # enough to survive a crash. A separate waiter keeps stdin
            # responsive and acknowledges only after the journal contains the
            # intent or the target has already reached a terminal checkpoint.
            runtime_owner = self._acquire_knowledge_runtime_owner()
            try:
                waiter = Thread(
                    target=self._acknowledge_knowledge_cancellation,
                    args=(request_id, knowledge, runtime_owner),
                    name=f"elysia-knowledge-cancel-{request_id}",
                    daemon=True,
                )
                waiter.start()
            except Exception:
                self._release_knowledge_runtime_owner(runtime_owner)
                raise
            return

        # Serialize cancellation against a naturally completing callback. If
        # cancellation wins, the runner invokes the target terminal callback
        # re-entrantly before this command acknowledgement. If completion won,
        # its entire terminal sequence is already visible before rejection.
        with self._transcription_lifecycle_lock:
            with self._state_lock:
                transcription = self._transcription_task
                runner = self._transcription_runner
            if (
                transcription is not None
                and transcription.request_id == target_request_id
                and runner is not None
            ):
                try:
                    snapshot = runner.cancel(target_request_id)
                except TranscriptionJobNotFoundError:
                    snapshot = None
                if snapshot is not None and snapshot.state == "cancelled":
                    self._emit_response(request_id, {"stopped": True})
                    return

        raise ProtocolValidationError(
            "request.not_cancellable",
            "No matching cancellable Backend request is active.",
        )

    def _acknowledge_knowledge_cancellation(
        self,
        cancel_request_id: str,
        task: _KnowledgeTask,
        runtime_owner: object,
    ) -> None:
        """Persist one cancellation intent before acknowledging its request.

        Lifecycle operations create their durable journal entry inside the
        background worker, so a user can cancel in the narrow interval before
        that entry exists. Polling the bounded path-private journal off the
        stdin thread closes that admission race. Natural terminal completion
        may still win; in that case no cancellation intent remains to persist,
        but the completed target is already crash-safe.
        """

        try:
            lifecycle = self._require_knowledge_runtime().lifecycle
            while True:
                operations = tuple(
                    operation
                    for operation in lifecycle.list_operations(task.project_id)
                    if str(operation.operation_id)
                    not in task.initial_operation_ids
                )
                for operation in operations:
                    if operation.state in {
                        "cancel_requested",
                        "cancelled",
                    }:
                        self._emit_knowledge_output(
                            lambda: self._emit_response(
                                cancel_request_id,
                                {"stopped": True},
                            )
                        )
                        return
                    if operation.state in {
                        "running",
                        "recovery_required",
                    }:
                        try:
                            persisted = lifecycle.request_cancel(
                                operation.operation_id
                            )
                        except KnowledgeLifecycleConflictError:
                            # The worker may have committed a terminal state
                            # after this snapshot. Re-read it rather than
                            # misreporting that natural race as storage loss.
                            continue
                        if persisted.state == "cancel_requested":
                            self._emit_knowledge_output(
                                lambda: self._emit_response(
                                    cancel_request_id,
                                    {"stopped": True},
                                )
                            )
                            return
                if task.done.wait(0.02):
                    # Completion won before cancellation reached a mutable
                    # checkpoint. The target's own terminal response is
                    # durable and Electron already handles this accepted race.
                    self._emit_knowledge_output(
                        lambda: self._emit_response(
                            cancel_request_id,
                            {"stopped": True},
                        )
                    )
                    return
        except KnowledgeLifecycleError:
            self._emit_knowledge_output(
                lambda: self._emit_error(
                    cancel_request_id,
                    "knowledge.storage_failed",
                    "Project Source cancellation could not be persisted.",
                    retryable=True,
                )
            )
        except Exception:
            logger.exception(
                "Project Source cancellation persistence failed: request_id=%s.",
                cancel_request_id,
            )
            self._emit_knowledge_output(
                lambda: self._emit_error(
                    cancel_request_id,
                    "knowledge.storage_failed",
                    "Project Source cancellation could not be persisted.",
                    retryable=True,
                )
            )
        finally:
            self._release_knowledge_runtime_owner(runtime_owner)

    def _require_manual_speech_admission_locked(
        self,
        raw_chat_id: str,
    ) -> None:
        """Reject stale Chat ownership or overlap with active generation.

        Callers hold ``_state_lock`` through this check. The final admission
        caller retains it until the speech turn has been queued, preventing a
        generation reservation or Chat switch from winning between validation
        and the coordinator's replacement boundary.
        """

        active_chat = self._active_chat
        if active_chat is None or raw_chat_id != str(active_chat.chat_id):
            raise ProtocolValidationError(
                "chat.not_active",
                "chatId is not the active desktop Chat.",
            )
        generation = self._generation_task
        if generation is not None and not generation.done.is_set():
            raise ChatBusyError(
                "Wait for the active Chat reply before starting read aloud."
            )

    def _start_speech_turn(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Speak one persisted Assistant message without accepting source text.

        The Backend resolves the message from the canonical Chat repository so
        a compromised or stale renderer cannot substitute arbitrary synthesis
        input for the message identity displayed in the conversation.
        """

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        raw_chat_id = cast(str, params["chatId"])
        raw_message_id = cast(str, params["assistantMessageId"])
        with self._state_lock:
            self._require_manual_speech_admission_locked(raw_chat_id)

        try:
            canonical_chat = self._brain.get_chat(ChatId(raw_chat_id))
        except ChatNotFoundError as error:
            raise ProtocolValidationError(
                "chat.not_found",
                "The requested Chat no longer exists.",
            ) from error

        assistant_message = next(
            (
                message
                for message in canonical_chat.messages
                if str(message.message_id) == raw_message_id
            ),
            None,
        )
        if assistant_message is None or assistant_message.role != "assistant":
            raise ProtocolValidationError(
                "voice.speech.invalid_target",
                "assistantMessageId must name a persisted Assistant message.",
            )

        speech_turn: DesktopSpeechTurn | None = None
        try:
            # Recheck under the same lock used by Chat switching and retain it
            # through speech admission. The repository read above may yield,
            # so this closes the window in which an old Chat could begin
            # speaking after another Chat became active.
            with self._state_lock:
                self._require_manual_speech_admission_locked(raw_chat_id)
                with self._speech_lifecycle_lock:
                    coordinator = self._speech_coordinator
                    if self._speech_closing or coordinator is None:
                        raise ProtocolValidationError(
                            "voice.speech.unavailable",
                            "Read aloud needs a configured local GPT-SoVITS "
                            "runtime and compatible voice profile. Review "
                            "Voice behavior in Settings, restart the Backend, "
                            "and try again.",
                        )
                    speech_status = coordinator.get_status()
                    if speech_status.state in ("idle", "starting"):
                        raise ProtocolValidationError(
                            "voice.speech.starting",
                            "Local speech is still starting. Wait a moment, "
                            "then try Read aloud again.",
                        )
                    if not speech_status.available:
                        raise ProtocolValidationError(
                            "voice.speech.unavailable",
                            "Read aloud needs a configured local GPT-SoVITS "
                            "runtime and compatible voice profile. Review "
                            "Voice behavior in Settings, restart the Backend, "
                            "and try again.",
                        )
                    speech_turn = coordinator.start_turn(
                        request_id,
                        raw_chat_id,
                    )
                    speech_turn.feed(assistant_message.content)
                    speech_turn.finish()
        except ProtocolValidationError:
            raise
        except BaseException:
            if speech_turn is not None:
                try:
                    speech_turn.cancel()
                except BaseException:
                    pass
            logger.error(
                "Persisted Assistant speech could not start: request_id=%s.",
                request_id,
            )
            raise ProtocolValidationError(
                "voice.speech.start_failed",
                "Local speech could not be started.",
            ) from None

        self._emit_response(
            request_id,
            {
                "kind": "voice.speech.start",
                "requestId": request_id,
                "chatId": raw_chat_id,
                "assistantMessageId": raw_message_id,
            },
        )

    def _cancel_speech_turn(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Make one exact speech turn stale without changing its Chat result.

        A false result is deliberately successful: the same interruption may
        race a natural terminal event, generation cancellation, or an earlier
        identical speech stop. Matching both identifiers prevents any of those
        stale retries from reaching a replacement turn.
        """

        target_request_id = cast(str, params["requestId"])
        target_chat_id = cast(str, params["chatId"])
        try:
            with self._speech_lifecycle_lock:
                coordinator = self._speech_coordinator
                stopped = (
                    False
                    if self._speech_closing or coordinator is None
                    else coordinator.cancel_turn(
                        target_request_id,
                        target_chat_id,
                    )
                )
        except BaseException:
            # Speech cancellation is optional infrastructure, but claiming it
            # succeeded could leave native synthesis running. Fail closed and
            # expose only a stable error rather than adapter diagnostics.
            logger.error(
                "Desktop speech stop failed: request_id=%s.",
                target_request_id,
            )
            self._prepare_speech_shutdown()
            self._emit_error(
                request_id,
                "voice.speech.cancel_failed",
                "Local speech could not be cancelled.",
            )
            return

        self._emit_response(
            request_id,
            {
                "kind": "voice.speech.cancel",
                "requestId": target_request_id,
                "chatId": target_chat_id,
                "stopped": stopped,
            },
        )

    def _wait_for_generation(self, timeout: float | None = None) -> bool:
        """Wait for the current generation, if any, without holding locks."""

        with self._state_lock:
            task = self._generation_task
        if task is None:
            return True
        return task.done.wait(timeout)

    def _prepare_generation_shutdown(self) -> None:
        """Stop cancellable work and never exit during an atomic commit.

        A worker blocked in model I/O may outlive the short join, but once its
        state is CANCEL_REQUESTED it can no longer claim the commit gate. A
        COMMITTING worker is different: persistence already owns the linearized
        boundary, so shutdown waits for that short critical section to finish.
        """

        with self._state_lock:
            task = self._generation_task
        if task is None:
            return

        if task.request_cancel():
            task.done.wait(GENERATION_SHUTDOWN_TIMEOUT_SECONDS)
            return

        if task.state_snapshot() is _GenerationState.COMMITTING:
            task.done.wait()

    def _prepare_knowledge_shutdown(self) -> None:
        """Close admission, request safe cancellation, and drain boundedly.

        A model or filesystem call may ignore the cooperative request. In that
        case its daemon worker retains runtime ownership until it returns, but
        the lifecycle gate suppresses every late frame after shutdown/EOF.
        """

        with self._knowledge_lifecycle_lock:
            self._knowledge_closing = True
        with self._state_lock:
            task = self._knowledge_task
        if task is not None:
            task.request_shutdown_cancel()
            task.done.wait(GENERATION_SHUTDOWN_TIMEOUT_SECONDS)
        self._close_knowledge_runtime_after_shutdown()

    def _close_knowledge_runtime_after_shutdown(self) -> None:
        """Release the runtime only after its last worker relinquishes it."""

        with self._state_lock:
            task = self._knowledge_task
            generation = self._generation_task
        if task is not None and not task.done.is_set():
            return
        if (
            generation is not None
            and generation.uses_project_knowledge
            and not generation.done.is_set()
        ):
            return
        with self._knowledge_lifecycle_lock:
            if not self._knowledge_closing:
                return
            if self._knowledge_runtime_owners:
                return
            runtime, self._knowledge_runtime = self._knowledge_runtime, None
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                logger.exception("Project Source runtime cleanup failed.")

    def _prepare_transcription_shutdown(self) -> None:
        """Suppress future STT frames and close workers without joining native I/O.

        The lifecycle lock makes the shutdown response a strict output boundary:
        a terminal callback already emitting finishes first, while every later
        callback sees ``_transcription_closing``. Running native inference is
        logically cancelled and left only on daemon workers, so an uncooperative
        optional library cannot block Electron teardown.
        """

        with self._transcription_lifecycle_lock:
            self._transcription_closing = True
            with self._state_lock:
                runner = self._transcription_runner
            if runner is not None:
                runner.shutdown(wait=False, cancel_pending=True)

    def _prepare_speech_shutdown(self) -> None:
        """Linearize speech output, then release process and pipe owners."""

        with self._speech_lifecycle_lock:
            self._speech_closing = True
            coordinator, self._speech_coordinator = self._speech_coordinator, None
            audio_writer, self._speech_audio_writer = (
                self._speech_audio_writer,
                None,
            )
            if coordinator is not None:
                # Queue/notifier shutdown is intentionally non-joining, so the
                # output gate cannot wait on a callback trying to re-enter it.
                # Native owner cleanup may wait only for its bounded stop limit.
                try:
                    coordinator.shutdown()
                except BaseException:
                    # Speech is optional and has already lost admission.  A
                    # broken adapter must not suppress the shutdown response
                    # or turn clean stdin EOF into a Backend failure.
                    logger.error("Optional desktop speech shutdown failed.")
            elif audio_writer is not None:
                # Shutdown can precede initialize. In that case no coordinator
                # exists yet to release the inherited private pipe owner.
                try:
                    audio_writer.close()
                except BaseException:
                    logger.error("Optional desktop speech channel shutdown failed.")

    def _emit_response(
        self,
        request_id: str,
        result: JsonObject,
    ) -> None:
        """Write one successful response."""

        self._emit(build_success_response(request_id, result))

    def _emit_error(
        self,
        request_id: str | None,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        """Write one bounded error without exposing a traceback."""

        safe_message = message.strip()[:MAX_ERROR_MESSAGE_LENGTH]
        self._emit(
            build_error_response(
                request_id,
                code,
                safe_message or "Desktop request failed.",
                retryable=retryable,
            )
        )

    def _emit_event(
        self,
        event: ProtocolEventName,
        *,
        request_id: str,
        data: JsonObject,
    ) -> None:
        """Write one streaming event linked to its request."""

        self._emit(build_event(event, data, request_id=request_id))

    def _emit_speech_event(
        self,
        event: ProtocolEventName,
        request_id: str,
        data: JsonObject,
    ) -> None:
        """Publish speech only before shutdown claims its output boundary."""

        with self._speech_lifecycle_lock:
            if self._speech_closing:
                return
            self._emit_event(event, request_id=request_id, data=data)

    def _emit_zero_work_speech_terminal(
        self,
        request_id: str,
        chat_id: str,
        *,
        state: Literal["completed", "cancelled"],
    ) -> None:
        """Close Electron's correlated turn when no speech turn was attached.

        Electron claims optional speech ownership as soon as Chat is admitted,
        before it can know whether the live preference suppresses audio or the
        optional coordinator will reject per-turn admission. A zero-sentence
        terminal preserves that fixed protocol boundary without synthesizing
        or exposing audio and prevents an undrained owner.
        """

        try:
            self._emit_speech_event(
                "voice.speech.terminal",
                request_id,
                {
                    "chatId": chat_id,
                    "state": state,
                    "submittedSentences": 0,
                    "completedSentences": 0,
                    "failedSentences": 0,
                },
            )
        except Exception:
            # Optional playback accounting cannot turn a committed text reply
            # into a failed Chat request when the output channel is closing.
            logger.exception(
                "Zero-work desktop speech terminal could not be emitted: "
                "request_id=%s.",
                request_id,
            )

    def _emit_progress(
        self,
        request_id: str,
        operation: str,
        completed: int,
        *,
        total: int | None,
        message: str | None,
    ) -> None:
        """Write one typed progress message."""

        self._emit(
            build_progress(
                request_id,
                operation,
                completed,
                total=total,
                message=message,
            )
        )

    def _emit(self, message: ServerMessage) -> None:
        """Serialize exactly one protocol message and flush immediately."""

        wire_message = json.dumps(
            message,
            # ASCII escapes keep the wire safe even if an embedding process
            # accidentally supplies a legacy-encoded stream.
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(wire_message) > MAX_PROTOCOL_FRAME_BYTES:
            raise ProtocolValidationError(
                "protocol.frame_too_large",
                "Backend response exceeds the desktop protocol frame limit.",
            )
        with self._output_lock:
            self._output_stream.write(wire_message)
            self._output_stream.write("\n")
            self._output_stream.flush()


def main() -> None:
    """Run the stdio bridge until Electron asks it to stop."""

    _configure_protocol_streams(
        sys.stdin,
        sys.stdout,
        sys.stderr,
    )
    audio_writer: AudioChannelWriter | None = None
    try:
        audio_writer = AudioChannelWriter.from_inherited_fd3()
    except AudioChannelError:
        # Direct Python/console launches legitimately have no fd3 capability.
        # Text Chat remains fully usable and no arbitrary descriptor is used.
        logger.error("Desktop speech audio channel is unavailable.")
    DesktopBackend(audio_writer=audio_writer).run()


if __name__ == "__main__":
    main()
