"""Test the versioned Electron-to-Python bridge without starting Ollama."""

import base64
import hashlib
import json
import sys
from collections.abc import Callable, Generator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
from time import monotonic
from typing import Any, cast
from unittest.mock import patch

import pytest

import desktop_backend as desktop_backend_module
from attachments import (
    AttachmentConflictError,
    AttachmentScope,
    JsonAttachmentStore,
)
from chats import (
    AttachmentMetadata,
    ChatId,
    ChatNotFoundError,
    ChatSession,
    ChatSessionMeta,
    ConversationMode,
    ProjectId,
    create_attachment_metadata,
    create_chat_message,
    create_chat_session,
)
from config.desktop_settings import (
    DesktopSettingsRepository,
    DesktopSettingsValidationError,
    EditableDesktopSettings,
)
from config.settings import (
    DEFAULT_DATA_IMPORT_MAX_BYTES,
    DEFAULT_MEMORY_RETRIEVAL_LIMIT,
    DEFAULT_MODEL_NAME,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET,
    DEFAULT_TRANSCRIPTION_DEVICE,
    DEFAULT_TRANSCRIPTION_LANGUAGE,
    DEFAULT_TRANSCRIPTION_MODEL,
    AppSettings,
)
from core import Brain, ChatBusyError, GenerationCancelledError
from desktop_backend import (
    SERVER_CAPABILITIES,
    SERVER_NAME,
    SERVER_VERSION,
    DesktopBackend,
    TranscriptionRunnerFactory,
    _configure_protocol_streams,
    _extract_model_names,
)
from desktop_protocol import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ProtocolMethod,
    build_event,
    build_request,
)
from projects import (
    Project,
    ProjectArchivedError,
    ProjectChatBusyError,
    ProjectNotFoundError,
    ProjectSettings,
    WorkspaceBinding,
    create_project,
)
from voice import (
    FasterWhisperConfig,
    FasterWhisperStatus,
    JsonVoiceSettingsRepository,
    Transcriber,
    TranscriptionJobCallback,
    TranscriptionJobRunner,
    TranscriptionJobRunnerConfig,
    TranscriptionJobSnapshot,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionUnavailableError,
    VoiceSettingsService,
    create_voice_settings_service,
)


class FakeBrain:
    """Provide only the Stage 5 methods used by the desktop bridge."""

    model_name = "test-model"

    def __init__(self) -> None:
        """Initialize deterministic state for this test double."""
        self.chat = create_chat_session(
            title="Elysia Chat",
            mode="chat",
            model_name=self.model_name,
        )
        self.next_chat = create_chat_session(
            title="Pending",
            mode="chat",
            model_name=self.model_name,
        )
        self._chats: dict[ChatId, ChatSession] = {
            self.chat.chat_id: self.chat,
        }
        self.next_project = create_project(name="Pending Project")
        self._projects: dict[ProjectId, Project] = {}
        self.stream_calls: list[tuple[str, str]] = []
        self.session_calls: list[tuple[object, ...]] = []

    def add_chat(self, chat: ChatSession) -> None:
        """Add a prepared Chat for one bridge scenario."""

        self._chats[chat.chat_id] = chat

    def add_project(self, project: Project) -> None:
        """Add a prepared Project for one bridge scenario."""

        self._projects[project.project_id] = project

    def list_chats(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """Provide deterministic list chats behavior for this test double."""
        self.session_calls.append(("list_chats", include_archived))
        return tuple(
            chat.to_meta()
            for chat in self._chats.values()
            if include_archived or not chat.is_archived
        )

    def get_chat(self, chat_id: object) -> ChatSession:
        """Provide deterministic get chat behavior for this test double."""
        normalized_id = ChatId(str(chat_id))
        self.session_calls.append(("get_chat", str(normalized_id)))
        try:
            return self._chats[normalized_id]
        except KeyError as error:
            raise ChatNotFoundError(
                f"Chat does not exist: {normalized_id}."
            ) from error

    def create_chat(
        self,
        *,
        title: str,
        mode: ConversationMode = "chat",
    ) -> ChatSession:
        """Create chat for this scenario."""
        self.session_calls.append(("create_chat", title, mode))
        created = replace(self.next_chat, title=title, mode=mode)
        self.add_chat(created)
        self.next_chat = create_chat_session(
            title="Pending",
            mode="chat",
            model_name=self.model_name,
        )
        return created

    def rename_chat(
        self,
        chat_id: ChatId,
        title: str,
    ) -> ChatSession:
        """Provide deterministic rename chat behavior for this test double."""
        self.session_calls.append(("rename_chat", str(chat_id), title))
        renamed = replace(self._chats[chat_id], title=title)
        self.add_chat(renamed)
        return renamed

    def pin_chat(
        self,
        chat_id: ChatId,
        pinned: bool = True,
    ) -> ChatSessionMeta:
        """Provide deterministic pin chat behavior for this test double."""
        self.session_calls.append(("pin_chat", str(chat_id), pinned))
        updated = replace(self._chats[chat_id], is_pinned=pinned)
        self.add_chat(updated)
        return updated.to_meta()

    def archive_chat(
        self,
        chat_id: ChatId,
        archived: bool = True,
    ) -> ChatSessionMeta:
        """Provide deterministic archive chat behavior for this test double."""
        self.session_calls.append(("archive_chat", str(chat_id), archived))
        updated = replace(self._chats[chat_id], is_archived=archived)
        self.add_chat(updated)
        return updated.to_meta()

    def delete_chat(self, chat_id: ChatId) -> None:
        """Provide deterministic delete chat behavior for this test double."""
        self.session_calls.append(("delete_chat", str(chat_id)))
        try:
            del self._chats[chat_id]
        except KeyError as error:
            raise ChatNotFoundError(
                f"Chat does not exist: {chat_id}."
            ) from error

    def create_project(
        self,
        *,
        name: str,
        custom_instructions: str | None = None,
    ) -> Project:
        """Create project for this scenario."""
        self.session_calls.append(
            ("create_project", name, custom_instructions)
        )
        project = replace(
            self.next_project,
            name=name,
            settings=ProjectSettings(
                custom_instructions=custom_instructions,
            ),
        )
        self.add_project(project)
        self.next_project = create_project(name="Pending Project")
        return project

    def list_projects(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[Project, ...]:
        """Provide deterministic list projects behavior for this test double."""
        self.session_calls.append(("list_projects", include_archived))
        return tuple(
            project
            for project in self._projects.values()
            if include_archived or not project.is_archived
        )

    def get_project(self, project_id: ProjectId) -> Project:
        """Provide deterministic get project behavior for this test double."""
        self.session_calls.append(("get_project", str(project_id)))
        try:
            return self._projects[project_id]
        except KeyError as error:
            raise ProjectNotFoundError(
                f"Project does not exist: {project_id}."
            ) from error

    def update_project(
        self,
        project_id: ProjectId,
        *,
        name: str,
        custom_instructions: str | None,
    ) -> Project:
        """Provide deterministic update project behavior for this test double."""
        self.session_calls.append(
            (
                "update_project",
                str(project_id),
                name,
                custom_instructions,
            )
        )
        current = self.get_project(project_id)
        updated = replace(
            current,
            name=name,
            settings=ProjectSettings(
                default_model_name=current.settings.default_model_name,
                custom_instructions=custom_instructions,
            ),
        )
        self.add_project(updated)
        return updated

    def bind_workspace(
        self,
        project_id: ProjectId,
        root_path: str,
    ) -> Project:
        """Provide deterministic bind workspace behavior for this test double."""
        self.session_calls.append(
            ("bind_workspace", str(project_id), root_path)
        )
        updated = replace(
            self.get_project(project_id),
            workspace_binding=WorkspaceBinding(root_path=root_path),
        )
        self.add_project(updated)
        return updated

    def unbind_workspace(self, project_id: ProjectId) -> Project:
        """Provide deterministic unbind workspace behavior for this test double."""
        self.session_calls.append(("unbind_workspace", str(project_id)))
        updated = replace(
            self.get_project(project_id),
            workspace_binding=None,
        )
        self.add_project(updated)
        return updated

    def archive_project(self, project_id: ProjectId) -> Project:
        """Provide deterministic archive project behavior for this test double."""
        self.session_calls.append(("archive_project", str(project_id)))
        updated = replace(self.get_project(project_id), is_archived=True)
        self.add_project(updated)
        return updated

    def restore_project(self, project_id: ProjectId) -> Project:
        """Provide deterministic restore project behavior for this test double."""
        self.session_calls.append(("restore_project", str(project_id)))
        updated = replace(self.get_project(project_id), is_archived=False)
        self.add_project(updated)
        return updated

    def list_project_chats(
        self,
        project_id: ProjectId,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """Provide deterministic list project chats behavior for this test double."""
        return tuple(
            chat.to_meta()
            for chat in self._chats.values()
            if chat.project_id == project_id
            and (include_archived or not chat.is_archived)
        )

    def move_chat(
        self,
        chat_id: ChatId,
        project_id: ProjectId | None,
    ) -> ChatSession:
        """Provide deterministic move chat behavior for this test double."""
        self.session_calls.append(
            (
                "move_chat",
                str(chat_id),
                None if project_id is None else str(project_id),
            )
        )
        updated = replace(self.get_chat(chat_id), project_id=project_id)
        self.add_chat(updated)
        return updated

    def stream_chat(
        self,
        chat_id: object,
        message: str,
        *,
        attachments: tuple[AttachmentMetadata, ...] = (),
        should_cancel: Callable[[], bool] | None = None,
        begin_commit: Callable[[], bool] | None = None,
    ) -> Generator[str, None, None]:
        """Provide deterministic stream chat behavior for this test double."""
        self.stream_calls.append((str(chat_id), message))
        if should_cancel is not None and should_cancel():
            raise GenerationCancelledError("Chat generation was cancelled.")
        yield "你好"
        if should_cancel is not None and should_cancel():
            raise GenerationCancelledError("Chat generation was cancelled.")
        yield "呀"
        if should_cancel is not None and should_cancel():
            raise GenerationCancelledError("Chat generation was cancelled.")
        if begin_commit is not None and not begin_commit():
            raise GenerationCancelledError("Chat generation was cancelled.")
        stored_chat = self._chats[ChatId(str(chat_id))]
        committed_at = max(
            datetime.now(timezone.utc),
            stored_chat.updated_at + timedelta(microseconds=1),
        )
        updated = replace(
            stored_chat,
            updated_at=committed_at,
            messages=(
                *stored_chat.messages,
                create_chat_message(
                    role="user",
                    content=message,
                    attachments=attachments,
                    created_at=committed_at,
                ),
                create_chat_message(
                    role="assistant",
                    content="你好呀",
                    created_at=committed_at,
                ),
            ),
        )
        self.add_chat(updated)

    def stream_retry(
        self,
        chat_id: object,
        user_message_id: object,
        assistant_message_id: object,
        message: str | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
        begin_commit: Callable[[], bool] | None = None,
    ) -> Generator[str, None, None]:
        """Provide deterministic stream retry behavior for this test double."""
        stored_chat = self._chats[ChatId(str(chat_id))]
        user_record, assistant_record = stored_chat.messages[-2:]
        if (
            str(user_record.message_id) != str(user_message_id)
            or str(assistant_record.message_id) != str(assistant_message_id)
        ):
            raise ValueError("Retry target does not match the tail turn.")
        effective_message = (
            user_record.content if message is None else message.strip()
        )
        if should_cancel is not None and should_cancel():
            raise GenerationCancelledError("Chat generation was cancelled.")
        yield "重"
        if should_cancel is not None and should_cancel():
            raise GenerationCancelledError("Chat generation was cancelled.")
        yield "试"
        if should_cancel is not None and should_cancel():
            raise GenerationCancelledError("Chat generation was cancelled.")
        if begin_commit is not None and not begin_commit():
            raise GenerationCancelledError("Chat generation was cancelled.")
        committed_at = max(
            datetime.now(timezone.utc),
            stored_chat.updated_at + timedelta(microseconds=1),
        )
        self.add_chat(
            replace(
                stored_chat,
                updated_at=committed_at,
                messages=(
                    *stored_chat.messages[:-2],
                    replace(user_record, content=effective_message),
                    replace(assistant_record, content="重试"),
                ),
            )
        )


JsonObject = dict[str, Any]
SESSION_TOKEN = "0123456789abcdef0123456789abcdef"


class _ControlledTranscriber:
    """Hold one deterministic STT outcome behind test-controlled events."""

    def __init__(self, outcome: TranscriptionResult | Exception) -> None:
        """Store the outcome and expose inference lifecycle observations."""

        self.outcome = outcome
        self.entered = Event()
        self.release = Event()
        self.finished = Event()
        self.requests: list[TranscriptionRequest] = []

    def transcribe(
        self,
        request: TranscriptionRequest,
    ) -> TranscriptionResult:
        """Wait for test release, then return or raise the configured outcome."""

        self.requests.append(request)
        self.entered.set()
        try:
            if not self.release.wait(2.0):
                raise RuntimeError("Test did not release transcription.")
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome
        finally:
            self.finished.set()


class _StatusTranscriber:
    """Expose an available-to-ready transition for Backend readiness tests."""

    def __init__(self, *, fallback_from_cuda: bool = True) -> None:
        """Begin available and optionally model an automatic CUDA fallback."""

        self.ready = False
        self.finished = Event()
        self.requests: list[TranscriptionRequest] = []
        self.fallback_from_cuda = fallback_from_cuda

    def get_status(self) -> FasterWhisperStatus:
        """Return the sanitized readiness state observed by Voice settings."""

        return FasterWhisperStatus(
            state="ready" if self.ready else "available",
            device="cpu",
            compute_type="int8",
            reason="cuda_unavailable" if self.fallback_from_cuda else None,
        )

    def transcribe(
        self,
        request: TranscriptionRequest,
    ) -> TranscriptionResult:
        """Mark the shared adapter ready and return a deterministic result."""

        self.requests.append(request)
        self.ready = True
        self.finished.set()
        return TranscriptionResult(
            text="Local transcript",
            language="en",
            language_probability=1.0,
        )


def _transcription_runner_factory(
    transcriber: Transcriber,
    *,
    terminal_event: Event | None = None,
    timeout_seconds: float = 1.0,
) -> tuple[TranscriptionRunnerFactory, list[TranscriptionJobRunner]]:
    """Return an injectable real runner factory and its created instances."""

    runners: list[TranscriptionJobRunner] = []

    def _factory(
        callback: TranscriptionJobCallback,
    ) -> TranscriptionJobRunner:
        """Create one bounded test runner around the supplied Transcriber."""

        def _observe_terminal(snapshot: TranscriptionJobSnapshot) -> None:
            """Signal only after Backend terminal handling has returned."""

            try:
                callback(snapshot)
            finally:
                if terminal_event is not None:
                    terminal_event.set()

        runner = TranscriptionJobRunner(
            transcriber,
            config=TranscriptionJobRunnerConfig(
                max_concurrent_jobs=1,
                max_queued_jobs=0,
                default_timeout_seconds=timeout_seconds,
            ),
            on_terminal=_observe_terminal,
        )
        runners.append(runner)
        return runner

    return _factory, runners


def _initialized_backend(
    tmp_path: Path,
    transcription_runner_factory: TranscriptionRunnerFactory,
) -> tuple[DesktopBackend, FakeBrain, StringIO]:
    """Create and initialize one directly driven Backend for async tests."""

    fake_brain = FakeBrain()
    output_stream = StringIO()
    backend = DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: (fake_brain.model_name,),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        voice_settings_service=create_voice_settings_service(tmp_path),
        transcription_runner_factory=transcription_runner_factory,
        attachment_store=JsonAttachmentStore(
            tmp_path / "attachments",
            max_file_bytes=1_024 * 1_024,
        ),
        input_stream=StringIO(),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    )
    assert backend._handle_line(json.dumps(_handshake_request()))
    assert backend._handle_line(json.dumps(_initialize_request()))
    return backend, fake_brain, output_stream


def _output_messages(output_stream: StringIO) -> list[JsonObject]:
    """Parse every complete Backend frame currently held by a test stream."""

    return [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]


def _request(
    request_id: str,
    method: ProtocolMethod,
    params: JsonObject,
) -> JsonObject:
    """Return a JSON-compatible request validated by the real contract."""

    return cast(JsonObject, build_request(request_id, method, params))


def _handshake_request(*, token: str = SESSION_TOKEN) -> JsonObject:
    """Provide the handshake request fixture used by these tests."""
    return _request(
        "handshake-1",
        "handshake",
        {
            "client": {
                "name": "elysia-electron",
                "version": "0.1.0",
            },
            "sessionToken": token,
        },
    )


def _voice_capture_params(chat_id: str, *, silent: bool = False) -> JsonObject:
    """Build one frame-aligned transient PCM utterance for bridge tests."""

    pcm = (b"\x00\x00" if silent else b"\xe8\x03") * 3_200
    return {
        "sessionId": "voice_backend_fixture",
        "chatId": chat_id,
        "sampleRateHz": 16_000,
        "channelCount": 1,
        "sampleFormat": "s16le",
        "sampleCount": 3_200,
        "speechStartSample": 0,
        "speechEndSample": 3_200,
        "pcmBase64": base64.b64encode(pcm).decode("ascii"),
    }


def _initialize_request() -> JsonObject:
    """Provide the initialize request fixture used by these tests."""
    return _request("initialize-1", "initialize", {})


def _desktop_settings_values(
    *,
    model_name: str = "test-model",
    ollama_host: str = "http://localhost:11434",
    short_term_memory_token_budget: int = 2_048,
    memory_retrieval_limit: int = 5,
    data_import_max_bytes: int = 16 * 1024 * 1024,
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL,
    transcription_device: str = DEFAULT_TRANSCRIPTION_DEVICE,
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE,
) -> JsonObject:
    """Provide the desktop settings values fixture used by these tests."""
    return {
        "modelName": model_name,
        "ollamaHost": ollama_host,
        "shortTermMemoryTokenBudget": short_term_memory_token_budget,
        "memoryRetrievalLimit": memory_retrieval_limit,
        "dataImportMaxBytes": data_import_max_bytes,
        "transcriptionModel": transcription_model,
        "transcriptionDevice": transcription_device,
        "transcriptionLanguage": transcription_language,
    }


def _desktop_settings_repository(path: Path) -> DesktopSettingsRepository:
    """Provide the desktop settings repository fixture used by these tests."""
    return DesktopSettingsRepository(
        path,
        EditableDesktopSettings(
            model_name="test-model",
            ollama_host="http://localhost:11434",
            short_term_memory_token_budget=2_048,
            memory_retrieval_limit=5,
            data_import_max_bytes=16 * 1024 * 1024,
        ),
    )


def _run_bridge(
    build_lines: Callable[[str], list[JsonObject]],
    *,
    expected_session_token: str = SESSION_TOKEN,
    fake_brain: FakeBrain | None = None,
    settings_repository: DesktopSettingsRepository | None = None,
    voice_settings_service: VoiceSettingsService | None = None,
    attachment_store: JsonAttachmentStore | None = None,
) -> tuple[FakeBrain, list[JsonObject]]:
    """Provide the run bridge fixture used by these tests."""
    active_brain = fake_brain if fake_brain is not None else FakeBrain()
    lines = build_lines(str(active_brain.chat.chat_id))
    input_stream = StringIO(
        "".join(f"{json.dumps(line)}\n" for line in lines)
    )
    output_stream = StringIO()

    with TemporaryDirectory(prefix="elysia-settings-test-") as temp_dir:
        repository = settings_repository
        if repository is None:
            repository = _desktop_settings_repository(
                Path(temp_dir) / "global.json"
            )
        active_voice_settings = voice_settings_service
        if active_voice_settings is None:
            active_voice_settings = create_voice_settings_service(
                Path(temp_dir)
            )
        DesktopBackend(
            brain_factory=lambda: cast(Brain, active_brain),
            model_loader=lambda: ("test-model", "second-model"),
            settings_validator=lambda: None,
            settings_repository=repository,
            voice_settings_service=active_voice_settings,
            attachment_store=attachment_store,
            input_stream=input_stream,
            output_stream=output_stream,
            expected_session_token=expected_session_token,
        ).run()

    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]
    return active_brain, messages


def _run_bridge_with_bootstrap_settings(
    bootstrap_settings: AppSettings,
    fake_brain: FakeBrain,
) -> list[JsonObject]:
    """Provide the run bridge with bootstrap settings fixture used by these tests."""
    requests = [
        _handshake_request(),
        _request("settings-safe", "settings.get", {}),
        _initialize_request(),
        _request("shutdown-safe", "shutdown", {}),
    ]
    input_stream = StringIO(
        "".join(f"{json.dumps(request)}\n" for request in requests)
    )
    output_stream = StringIO()
    with patch.object(
        desktop_backend_module,
        "SETTINGS",
        bootstrap_settings,
    ):
        DesktopBackend(
            brain_factory=lambda: cast(Brain, fake_brain),
            model_loader=lambda: (
                fake_brain.model_name,
                "second-model",
            ),
            settings_validator=lambda: None,
            input_stream=input_stream,
            output_stream=output_stream,
            expected_session_token=SESSION_TOKEN,
        ).run()
    return [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]


def _success_result(
    messages: list[JsonObject],
    request_id: str,
) -> JsonObject:
    """Return one successful result by request ID."""

    response = next(
        message
        for message in messages
        if message.get("type") == "response"
        and message.get("id") == request_id
    )
    assert response["ok"] is True
    result = response["result"]
    assert isinstance(result, dict)
    return cast(JsonObject, result)


def _error(
    messages: list[JsonObject],
    request_id: str,
) -> JsonObject:
    """Return one error payload by request ID."""

    response = next(
        message
        for message in messages
        if message.get("type") == "response"
        and message.get("id") == request_id
    )
    assert response["ok"] is False
    error = response["error"]
    assert isinstance(error, dict)
    return cast(JsonObject, error)


def test_bridge_initializes_and_streams_one_real_brain_turn() -> None:
    """Verify that bridge initializes and streams one real brain turn."""
    actual_brain, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-1",
                "chat.stream",
                {"chatId": chat_id, "message": "你好呀"},
            ),
        ]
    )
    chat_id = str(actual_brain.chat.chat_id)

    assert actual_brain.stream_calls == [(chat_id, "你好呀")]
    assert messages[0] == {
        "type": "response",
        "protocol": {
            "name": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
        },
        "id": "handshake-1",
        "ok": True,
        "result": {
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
    }
    initialize_response = next(
        message
        for message in messages
        if message.get("id") == "initialize-1"
    )
    assert initialize_response["result"] == {
        "modelName": "test-model",
        "models": ["test-model", "second-model"],
        "chatId": chat_id,
        "chatTitle": "Elysia Chat",
    }
    assert [
        message["completed"]
        for message in messages
        if message.get("type") == "progress"
        and message.get("requestId") == "initialize-1"
    ] == [0, 1, 2, 3]
    stream_messages = [
        message for message in messages if message["type"] == "stream"
    ]
    assert [message["sequence"] for message in stream_messages] == [0, 1, 2]
    assert [message["chunk"] for message in stream_messages] == [
        "你好", "呀", "",
    ]
    assert [message["done"] for message in stream_messages] == [
        False, False, True,
    ]
    assert any(
        message["type"] == "progress"
        and message["completed"] == 1
        and message["total"] == 1
        for message in messages
    )
    assert any(
        message["type"] == "event"
        and message["event"] == "chat.completed"
        for message in messages
    )
    assert _success_result(messages, "chat-1") == {
        "chatId": chat_id,
        "reply": "你好呀",
    }
    persisted = actual_brain.get_chat(actual_brain.chat.chat_id)
    assert [
        message.content
        for message in persisted.messages
    ] == ["你好呀", "你好呀"]
    assert "chat.sessions" in SERVER_CAPABILITIES


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="attachment.add accepts only native Windows drive-absolute paths",
)
def test_attachment_add_returns_only_safe_canonical_metadata(
    tmp_path: Path,
) -> None:
    """Verify that attachment add returns only safe canonical metadata."""
    source = tmp_path / "course notes.md"
    source.write_text("Local notes", encoding="utf-8")
    store = JsonAttachmentStore(
        tmp_path / "attachments",
        max_file_bytes=1_024,
    )

    brain, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "attachment-add-1",
                "attachment.add",
                {
                    "scope": {"kind": "chat", "id": chat_id},
                    "sourcePaths": [str(source.resolve())],
                },
            ),
        ],
        attachment_store=store,
    )

    result = _success_result(messages, "attachment-add-1")
    assert set(result) == {
        "scope",
        "attachments",
        "maxFileBytes",
        "maxFileCount",
    }
    assert result["scope"] == {
        "kind": "chat",
        "id": str(brain.chat.chat_id),
    }
    attachments = cast(list[JsonObject], result["attachments"])
    assert len(attachments) == 1
    attachment = attachments[0]
    assert set(attachment) == {
        "attachmentId",
        "fileName",
        "mediaType",
        "sizeBytes",
        "status",
    }
    assert attachment["fileName"] == "course notes.md"
    assert attachment["mediaType"] == "text/markdown"
    assert attachment["sizeBytes"] == len(b"Local notes")
    assert attachment["status"] == "ready"


def test_attachment_only_stream_commits_metadata_and_finalizes_blob(
    tmp_path: Path,
) -> None:
    """Commit attachment metadata and its blob even when message text is empty."""
    brain = FakeBrain()
    scope = AttachmentScope(kind="chat", id=str(brain.chat.chat_id))
    source = tmp_path / "notes.txt"
    source.write_text("attachment bytes", encoding="utf-8")
    store = JsonAttachmentStore(
        tmp_path / "attachments",
        max_file_bytes=1_024,
    )
    staged = store.stage_files(scope, (source.resolve(),))
    attachment_id = staged.attachments[0].attachment_id

    _actual, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-attachment-1",
                "chat.stream",
                {
                    "chatId": chat_id,
                    "message": "",
                    "attachmentIds": [attachment_id],
                },
            ),
        ],
        fake_brain=brain,
        attachment_store=store,
    )

    assert _success_result(messages, "chat-attachment-1")["reply"] == "你好呀"
    user_message = brain.get_chat(brain.chat.chat_id).messages[-2]
    assert user_message.content == ""
    assert [
        str(attachment.attachment_id)
        for attachment in user_message.attachments
    ] == [attachment_id]
    assert store.list_state(
        scope,
        referenced_ids=(attachment_id,),
    ).attachments == ()
    assert not any(
        message.get("error", {}).get("message") == str(source)
        for message in messages
        if isinstance(message.get("error"), dict)
    )


def test_generation_terminal_response_waits_for_attachment_reconciliation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Verify that generation terminal response waits for attachment reconciliation."""
    fake_brain = FakeBrain()
    store = JsonAttachmentStore(
        tmp_path / "attachments",
        max_file_bytes=1_024,
    )
    reconcile_started = Event()
    release_reconcile = Event()
    first_response_seen = Event()
    real_reconcile = store.reconcile
    reconcile_calls = 0

    def blocking_reconcile(
        scope: AttachmentScope,
        referenced_ids: tuple[str, ...] = (),
    ) -> object:
        """Block the first reconciliation until the test inspects response order."""
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls == 1:
            reconcile_started.set()
            assert release_reconcile.wait(2.0)
        return real_reconcile(scope, referenced_ids)

    monkeypatch.setattr(store, "reconcile", blocking_reconcile)

    class ResponseNotifyingStream(StringIO):
        """Signal when the first generation response reaches the output stream."""

        def write(self, value: str) -> int:
            """Record output and notify on the first Chat terminal response."""
            written = super().write(value)
            if value.startswith("{"):
                message = json.loads(value)
                if (
                    message.get("type") == "response"
                    and message.get("id") == "chat-first"
                ):
                    first_response_seen.set()
            return written

    output_stream = ResponseNotifyingStream()
    chat_id = str(fake_brain.chat.chat_id)

    def request_lines() -> Generator[str, None, None]:
        """Feed requests around the deliberately blocked reconciliation point."""
        for request in (
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-first",
                "chat.stream",
                {"chatId": chat_id, "message": "First"},
            ),
        ):
            yield f"{json.dumps(request)}\n"
        assert reconcile_started.wait(2.0)
        assert not first_response_seen.is_set()
        release_reconcile.set()
        assert first_response_seen.wait(2.0)
        yield f"{json.dumps(_request(
            'chat-second',
            'chat.stream',
            {'chatId': chat_id, 'message': 'Second'},
        ))}\n"

    DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        attachment_store=store,
        input_stream=cast(TextIOWrapper, request_lines()),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    ).run()
    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]

    assert _success_result(messages, "chat-first")["reply"] == "你好呀"
    assert _success_result(messages, "chat-second")["reply"] == "你好呀"
    assert not any(
        message.get("id") == "chat-second"
        and isinstance(message.get("error"), dict)
        and message["error"].get("code") == "chat.busy"
        for message in messages
    )


def test_busy_generation_rejection_does_not_release_the_active_claim(
    tmp_path: Path,
) -> None:
    """Keep the original busy claim when a second generation request is rejected."""
    fake_brain = FakeBrain()
    chat_id = str(fake_brain.chat.chat_id)
    scope = AttachmentScope(kind="chat", id=chat_id)
    source = tmp_path / "claimed.txt"
    source.write_text("active claim", encoding="utf-8")
    store = JsonAttachmentStore(
        tmp_path / "attachments",
        max_file_bytes=1_024,
    )
    item = store.stage_files(scope, (source.resolve(),)).attachments[0]
    store.claim_chat(scope, (item.attachment_id,))
    backend = DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        attachment_store=store,
        input_stream=StringIO(),
        output_stream=StringIO(),
        expected_session_token=SESSION_TOKEN,
    )
    backend._brain = cast(Brain, fake_brain)
    backend._set_active_chat(fake_brain.chat)
    active_task = desktop_backend_module._GenerationTask(
        request_id="chat-active",
        chat_id=fake_brain.chat.chat_id,
        method="chat.stream",
    )
    backend._generation_task = active_task

    with pytest.raises(ChatBusyError):
        backend._start_chat_stream(
            "chat-rejected",
            {
                "chatId": chat_id,
                "message": "Must stay busy",
                "attachmentIds": [item.attachment_id],
            },
        )

    assert store.list_state(scope).attachments == ()
    with pytest.raises(AttachmentConflictError, match="already in use"):
        store.remove(scope, item.attachment_id)
    active_task.finish()


def test_bridge_retries_the_persisted_tail_with_stable_message_ids() -> None:
    """Verify that bridge retries the persisted tail with stable message IDs."""
    fake_brain = FakeBrain()
    turn_time = fake_brain.chat.created_at + timedelta(seconds=1)
    user_message = create_chat_message(
        role="user",
        content="Original question",
        created_at=turn_time,
    )
    assistant_message = create_chat_message(
        role="assistant",
        content="Original answer",
        created_at=turn_time,
    )
    fake_brain.add_chat(
        replace(
            fake_brain.chat,
            updated_at=turn_time,
            messages=(user_message, assistant_message),
        )
    )

    _, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "retry-1",
                "chat.retry",
                {
                    "chatId": chat_id,
                    "userMessageId": str(user_message.message_id),
                    "assistantMessageId": str(
                        assistant_message.message_id
                    ),
                    "message": "Edited question",
                },
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _success_result(messages, "retry-1") == {
        "chatId": str(fake_brain.chat.chat_id),
        "reply": "重试",
    }
    stream_messages = [
        message
        for message in messages
        if message.get("type") == "stream"
        and message.get("requestId") == "retry-1"
    ]
    assert [message["chunk"] for message in stream_messages] == [
        "重",
        "试",
        "",
    ]
    refreshed_messages = fake_brain.get_chat(fake_brain.chat.chat_id).messages
    assert [str(message.message_id) for message in refreshed_messages] == [
        str(user_message.message_id),
        str(assistant_message.message_id),
    ]
    assert [message.content for message in refreshed_messages] == [
        "Edited question",
        "重试",
    ]
    assert "chat.retry" in SERVER_CAPABILITIES
    assert "request.cancel" in SERVER_CAPABILITIES


def test_cancel_success_prevents_partial_turn_persistence() -> None:
    """Acknowledge cancellation while keeping streamed partial text transient."""
    class CancellableBrain(FakeBrain):
        """Stream a partial reply until the bridge cancellation flag is raised."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Hold generation after one chunk so cancellation can interrupt it."""
            del attachments, begin_commit
            self.stream_calls.append((str(chat_id), message))
            yield "partial"
            while should_cancel is None or not should_cancel():
                Event().wait(0.001)
            raise GenerationCancelledError(
                "Chat generation was cancelled."
            )

    fake_brain = CancellableBrain()
    _, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-cancelled",
                "chat.stream",
                {"chatId": chat_id, "message": "Do not save this"},
            ),
            _request(
                "cancel-1",
                "request.cancel",
                {
                    "requestId": "chat-cancelled",
                    "reason": "User stopped",
                },
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _success_result(messages, "cancel-1") == {"stopped": True}
    assert _error(messages, "chat-cancelled") == {
        "code": "request.cancelled",
        "message": "Chat generation was cancelled.",
        "retryable": False,
    }
    assert any(
        message.get("type") == "event"
        and message.get("event") == "chat.cancelled"
        and message.get("requestId") == "chat-cancelled"
        for message in messages
    )
    assert not any(
        message.get("type") == "stream"
        and message.get("requestId") == "chat-cancelled"
        and message.get("done") is True
        for message in messages
    )
    assert fake_brain.get_chat(fake_brain.chat.chat_id).messages == ()


def test_chat_list_does_not_block_the_cancel_request_reader(
    tmp_path: Path,
) -> None:
    """Serve Chat listing without starving cancellation of an active stream."""
    generation_started = Event()

    class BlockingBrain(FakeBrain):
        """Keep generation active while independent bridge requests are read."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Poll cancellation long enough to expose a blocked request reader."""
            del attachments, begin_commit
            self.stream_calls.append((str(chat_id), message))
            yield "partial"
            generation_started.set()
            for _ in range(2_000):
                if should_cancel is not None and should_cancel():
                    raise GenerationCancelledError(
                        "Chat generation was cancelled."
                    )
                Event().wait(0.001)
            raise RuntimeError("Cancel request reader was blocked.")

    fake_brain = BlockingBrain()

    def request_lines() -> Generator[str, None, None]:
        """Issue list and cancel requests only after generation has started."""
        chat_id = str(fake_brain.chat.chat_id)
        for request in (
            _handshake_request(),
            _initialize_request(),
            _request(
                "blocked-chat",
                "chat.stream",
                {"chatId": chat_id, "message": "Wait"},
            ),
        ):
            yield f"{json.dumps(request)}\n"
        assert generation_started.wait(2.0)
        yield f"{json.dumps(_request(
            'list-during-generation',
            'chat.list',
            {'includeArchived': False},
        ))}\n"
        yield f"{json.dumps(_request(
            'cancel-after-list',
            'request.cancel',
            {'requestId': 'blocked-chat'},
        ))}\n"

    output_stream = StringIO()
    DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        input_stream=cast(TextIOWrapper, request_lines()),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    ).run()
    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]

    assert _success_result(
        messages,
        "list-during-generation",
    )["activeChat"]["messageCount"] == 0
    assert _success_result(messages, "cancel-after-list") == {
        "stopped": True,
    }
    assert _error(messages, "blocked-chat")["code"] == "request.cancelled"


def test_cancel_is_rejected_after_generation_claims_commit(
    tmp_path: Path,
) -> None:
    """Reject late cancellation once generation crosses the persistence boundary."""
    commit_claimed = Event()
    release_commit = Event()

    class CommittingBrain(FakeBrain):
        """Pause after claiming commit to test the non-cancellable boundary."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Claim commit, wait for a late cancel, then persist the turn."""
            del attachments, should_cancel
            self.stream_calls.append((str(chat_id), message))
            yield "committed"
            assert begin_commit is not None and begin_commit()
            commit_claimed.set()
            assert release_commit.wait(2.0)
            stored_chat = self._chats[ChatId(str(chat_id))]
            committed_at = max(
                datetime.now(timezone.utc),
                stored_chat.updated_at + timedelta(microseconds=1),
            )
            self.add_chat(
                replace(
                    stored_chat,
                    updated_at=committed_at,
                    messages=(
                        *stored_chat.messages,
                        create_chat_message(
                            role="user",
                            content=message,
                            created_at=committed_at,
                        ),
                        create_chat_message(
                            role="assistant",
                            content="committed",
                            created_at=committed_at,
                        ),
                    ),
                )
            )

    fake_brain = CommittingBrain()

    def request_lines() -> Generator[str, None, None]:
        """Send cancellation only after generation has claimed commit."""
        chat_id = str(fake_brain.chat.chat_id)
        for request in (
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-committing",
                "chat.stream",
                {"chatId": chat_id, "message": "Save this"},
            ),
        ):
            yield f"{json.dumps(request)}\n"
        assert commit_claimed.wait(2.0)
        yield f"{json.dumps(_request(
            'cancel-too-late',
            'request.cancel',
            {'requestId': 'chat-committing'},
        ))}\n"
        release_commit.set()

    output_stream = StringIO()
    DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        input_stream=cast(TextIOWrapper, request_lines()),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    ).run()
    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]

    assert _error(messages, "cancel-too-late")["code"] == (
        "request.not_cancellable"
    )
    assert _success_result(messages, "chat-committing")["reply"] == (
        "committed"
    )
    assert len(fake_brain.get_chat(fake_brain.chat.chat_id).messages) == 2


def test_background_completion_does_not_reactivate_a_chat_after_switch(
    tmp_path: Path,
) -> None:
    """Verify that background completion does not reactivate a chat after switch."""
    generation_started = Event()
    release_generation = Event()

    class SwitchingBrain(FakeBrain):
        """Delay one Chat's completion while the active Chat changes."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Persist the original Chat only after the bridge opens another."""
            del attachments
            self.stream_calls.append((str(chat_id), message))
            yield "reply"
            generation_started.set()
            assert release_generation.wait(2.0)
            if should_cancel is not None and should_cancel():
                raise GenerationCancelledError(
                    "Chat generation was cancelled."
                )
            assert begin_commit is None or begin_commit()
            stored_chat = self._chats[ChatId(str(chat_id))]
            committed_at = max(
                datetime.now(timezone.utc),
                stored_chat.updated_at + timedelta(microseconds=1),
            )
            self.add_chat(
                replace(
                    stored_chat,
                    updated_at=committed_at,
                    messages=(
                        *stored_chat.messages,
                        create_chat_message(
                            role="user",
                            content=message,
                            created_at=committed_at,
                        ),
                        create_chat_message(
                            role="assistant",
                            content="reply",
                            created_at=committed_at,
                        ),
                    ),
                )
            )

    fake_brain = SwitchingBrain()
    second_chat = create_chat_session(
        title="Second Chat",
        mode="chat",
        model_name=fake_brain.model_name,
    )
    fake_brain.add_chat(second_chat)

    def request_lines() -> Generator[str, None, None]:
        """Open another Chat while the first Chat's generation is suspended."""
        first_chat_id = str(fake_brain.chat.chat_id)
        for request in (
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-in-first",
                "chat.stream",
                {"chatId": first_chat_id, "message": "First Chat"},
            ),
        ):
            yield f"{json.dumps(request)}\n"
        assert generation_started.wait(2.0)
        yield f"{json.dumps(_request(
            'open-second',
            'chat.open',
            {'chatId': str(second_chat.chat_id)},
        ))}\n"
        release_generation.set()
        yield f"{json.dumps(_request(
            'list-after-switch',
            'chat.list',
            {'includeArchived': False},
        ))}\n"

    output_stream = StringIO()
    DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        input_stream=cast(TextIOWrapper, request_lines()),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    ).run()
    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]

    assert _success_result(
        messages,
        "open-second",
    )["activeChat"]["chatId"] == str(second_chat.chat_id)
    assert _success_result(
        messages,
        "list-after-switch",
    )["activeChat"]["chatId"] == str(second_chat.chat_id)
    assert _success_result(messages, "chat-in-first")["chatId"] == str(
        fake_brain.chat.chat_id
    )
    assert len(fake_brain.get_chat(fake_brain.chat.chat_id).messages) == 2


def test_background_completion_does_not_replace_a_new_active_chat(
    tmp_path: Path,
) -> None:
    """Verify that background completion does not replace a new active chat."""
    generation_started = Event()
    release_generation = Event()

    class CreatingBrain(FakeBrain):
        """Delay one Chat's completion while the bridge creates another Chat."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Persist the original Chat after a new active Chat is created."""
            del attachments
            self.stream_calls.append((str(chat_id), message))
            yield "reply"
            generation_started.set()
            assert release_generation.wait(2.0)
            if should_cancel is not None and should_cancel():
                raise GenerationCancelledError(
                    "Chat generation was cancelled."
                )
            assert begin_commit is None or begin_commit()
            stored_chat = self._chats[ChatId(str(chat_id))]
            committed_at = stored_chat.updated_at + timedelta(microseconds=1)
            self.add_chat(replace(
                stored_chat,
                updated_at=committed_at,
                messages=(
                    create_chat_message(
                        role="user",
                        content=message,
                        created_at=committed_at,
                    ),
                    create_chat_message(
                        role="assistant",
                        content="reply",
                        created_at=committed_at,
                    ),
                ),
            ))

    fake_brain = CreatingBrain()
    created_chat_id = str(fake_brain.next_chat.chat_id)

    def request_lines() -> Generator[str, None, None]:
        """Create another Chat while the first Chat's generation is suspended."""
        first_chat_id = str(fake_brain.chat.chat_id)
        for request in (
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-before-create",
                "chat.stream",
                {"chatId": first_chat_id, "message": "First Chat"},
            ),
        ):
            yield f"{json.dumps(request)}\n"
        assert generation_started.wait(2.0)
        yield f"{json.dumps(_request(
            'create-second',
            'chat.create',
            {'title': 'Second Chat', 'mode': 'chat'},
        ))}\n"
        release_generation.set()

    output_stream = StringIO()
    backend = DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        input_stream=cast(TextIOWrapper, request_lines()),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    )
    backend.run()
    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]

    assert _success_result(
        messages,
        "create-second",
    )["activeChat"]["chatId"] == created_chat_id
    final_active_chat = backend._active_chat_snapshot()
    assert final_active_chat is not None
    assert str(final_active_chat.chat_id) == created_chat_id


def test_post_commit_cache_refresh_failure_does_not_invite_retry() -> None:
    """Report post-commit refresh failure as non-retryable to avoid duplicate turns."""
    class RefreshFailingBrain(FakeBrain):
        """Commit successfully, then fail the bridge's post-commit refresh once."""

        def __init__(self) -> None:
            """Initialize the one-shot post-commit failure flag."""
            super().__init__()
            self.fail_next_refresh = False

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Delegate persistence, then arm a one-shot refresh failure."""
            yield from super().stream_chat(
                chat_id,
                message,
                attachments=attachments,
                should_cancel=should_cancel,
                begin_commit=begin_commit,
            )
            self.fail_next_refresh = True

        def get_chat(self, chat_id: object) -> ChatSession:
            """Raise once after commit, then resume normal Chat lookup."""
            if self.fail_next_refresh:
                self.fail_next_refresh = False
                raise RuntimeError("Cache refresh failed after commit.")
            return super().get_chat(chat_id)

    fake_brain = RefreshFailingBrain()
    _, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "committed-with-refresh-error",
                "chat.stream",
                {"chatId": chat_id, "message": "Save once"},
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _success_result(
        messages,
        "committed-with-refresh-error",
    )["reply"] == "你好呀"
    assert len(fake_brain._chats[fake_brain.chat.chat_id].messages) == 2


def test_chat_list_serializes_metadata_messages_and_attachments() -> None:
    """Verify that chat list serializes metadata messages and attachments."""
    fake_brain = FakeBrain()
    attachment = create_attachment_metadata(
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=128,
    )
    message_time = fake_brain.chat.created_at + timedelta(seconds=1)
    user_message = create_chat_message(
        role="user",
        content="Read the attachment.",
        attachments=(attachment,),
        created_at=message_time,
    )
    detailed_chat = replace(
        fake_brain.chat,
        updated_at=message_time,
        messages=(user_message,),
        project_id=ProjectId("project_sidebar"),
        is_pinned=True,
    )
    fake_brain.add_chat(detailed_chat)
    archived_chat = replace(
        create_chat_session(
            title="Archived",
            mode="work",
            model_name=fake_brain.model_name,
        ),
        is_archived=True,
    )
    fake_brain.add_chat(archived_chat)

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "visible-list",
                "chat.list",
                {"includeArchived": False},
            ),
            _request(
                "complete-list",
                "chat.list",
                {"includeArchived": True},
            ),
        ],
        fake_brain=fake_brain,
    )

    visible_result = _success_result(messages, "visible-list")
    complete_result = _success_result(messages, "complete-list")
    assert set(visible_result) == {"activeChat", "chats"}
    assert visible_result["activeChat"] == {
        "chatId": str(detailed_chat.chat_id),
        "title": "Elysia Chat",
        "mode": "chat",
        "createdAt": detailed_chat.created_at.isoformat(),
        "updatedAt": message_time.isoformat(),
        "messageCount": 1,
        "projectId": "project_sidebar",
        "modelName": "test-model",
        "pinned": True,
        "archived": False,
        "messages": [
            {
                "messageId": str(user_message.message_id),
                "role": "user",
                "content": "Read the attachment.",
                "createdAt": message_time.isoformat(),
                "attachments": [
                    {
                        "attachmentId": str(attachment.attachment_id),
                        "fileName": "notes.txt",
                        "mediaType": "text/plain",
                        "sizeBytes": 128,
                    }
                ],
            }
        ],
    }
    assert [
        chat["chatId"] for chat in visible_result["chats"]
    ] == [str(detailed_chat.chat_id)]
    assert {
        chat["chatId"] for chat in complete_result["chats"]
    } == {str(detailed_chat.chat_id), str(archived_chat.chat_id)}
    archived_summary = next(
        chat
        for chat in complete_result["chats"]
        if chat["chatId"] == str(archived_chat.chat_id)
    )
    assert set(archived_summary) == {
        "chatId",
        "title",
        "mode",
        "createdAt",
        "updatedAt",
        "messageCount",
        "projectId",
        "modelName",
        "pinned",
        "archived",
    }
    assert archived_summary["archived"] is True


def test_project_actions_return_one_canonical_project_and_chat_state() -> None:
    """Verify that project actions return one canonical project and chat state."""
    fake_brain = FakeBrain()
    project_id = str(fake_brain.next_project.project_id)
    chat_id = str(fake_brain.chat.chat_id)

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request("projects-empty", "project.list", {}),
            _request(
                "project-create",
                "project.create",
                {
                    "name": "Desktop Project",
                    "customInstructions": "Use concise answers.",
                },
            ),
            _request(
                "project-open",
                "project.open",
                {"projectId": project_id},
            ),
            _request(
                "project-update",
                "project.update",
                {
                    "projectId": project_id,
                    "name": "Renamed Project",
                    "customInstructions": None,
                },
            ),
            _request(
                "project-workspace",
                "project.workspace",
                {
                    "projectId": project_id,
                    "workspacePath": r"C:\Work\Elysia",
                },
            ),
            _request(
                "project-move-chat",
                "project.chat.move",
                {"chatId": chat_id, "projectId": project_id},
            ),
            _request(
                "project-archive",
                "project.archive",
                {"projectId": project_id, "archived": True},
            ),
            _request(
                "project-restore",
                "project.archive",
                {"projectId": project_id, "archived": False},
            ),
            _request(
                "project-unbind",
                "project.workspace",
                {"projectId": project_id, "workspacePath": None},
            ),
            _request(
                "project-detach-chat",
                "project.chat.move",
                {"chatId": chat_id, "projectId": None},
            ),
        ],
        fake_brain=fake_brain,
    )

    empty_state = _success_result(messages, "projects-empty")
    assert empty_state["activeProject"] is None
    assert empty_state["projects"] == []
    assert empty_state["chatState"]["activeChat"]["chatId"] == chat_id

    created_state = _success_result(messages, "project-create")
    assert created_state["activeProject"] == {
        "projectId": project_id,
        "name": "Desktop Project",
        "createdAt": fake_brain._projects[
            ProjectId(project_id)
        ].created_at.isoformat(),
        "updatedAt": fake_brain._projects[
            ProjectId(project_id)
        ].updated_at.isoformat(),
        "customInstructions": "Use concise answers.",
        "workspacePath": None,
        "archived": False,
        "chatCount": 0,
    }
    assert created_state["projects"] == [created_state["activeProject"]]
    assert _success_result(
        messages,
        "project-open",
    )["activeProject"]["projectId"] == project_id

    moved_state = _success_result(messages, "project-move-chat")
    assert moved_state["activeProject"]["chatCount"] == 1
    assert moved_state["chatState"]["activeChat"]["projectId"] == project_id
    assert moved_state["chatState"]["chats"][0]["projectId"] == project_id

    assert _success_result(
        messages,
        "project-update",
    )["activeProject"]["name"] == "Renamed Project"
    assert _success_result(
        messages,
        "project-workspace",
    )["activeProject"]["workspacePath"] == r"C:\Work\Elysia"
    assert _success_result(
        messages,
        "project-archive",
    )["activeProject"]["archived"] is True
    assert _success_result(
        messages,
        "project-restore",
    )["activeProject"]["archived"] is False
    assert _success_result(
        messages,
        "project-unbind",
    )["activeProject"]["workspacePath"] is None
    detached_state = _success_result(messages, "project-detach-chat")
    assert detached_state["activeProject"]["chatCount"] == 0
    assert detached_state["chatState"]["activeChat"]["projectId"] is None

    assert "project.management" in SERVER_CAPABILITIES
    assert (
        "update_project",
        project_id,
        "Renamed Project",
        None,
    ) in fake_brain.session_calls
    assert ("move_chat", chat_id, project_id) in fake_brain.session_calls
    assert ("move_chat", chat_id, None) in fake_brain.session_calls


def test_project_bridge_exposes_stable_not_found_archived_and_busy_errors(
) -> None:
    """Map Project absence, archival, and busy conflicts to stable wire errors."""
    class FailingProjectBrain(FakeBrain):
        """Expose stable archived and busy Project failures to the bridge."""

        def update_project(
            self,
            project_id: ProjectId,
            *,
            name: str,
            custom_instructions: str | None,
        ) -> Project:
            """Reject updates as if the selected Project were archived."""
            del name, custom_instructions
            raise ProjectArchivedError(
                f"Archived Project is read-only: {project_id}."
            )

        def move_chat(
            self,
            chat_id: ChatId,
            project_id: ProjectId | None,
        ) -> ChatSession:
            """Reject relationship changes as if the Chat were generating."""
            del project_id
            raise ProjectChatBusyError(
                f"Chat cannot change Project while busy: {chat_id}."
            )

    fake_brain = FailingProjectBrain()
    project = create_project(name="Error Project")
    fake_brain.add_project(project)

    _, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "project-missing",
                "project.open",
                {"projectId": "project_missing"},
            ),
            _request(
                "project-archived",
                "project.update",
                {
                    "projectId": str(project.project_id),
                    "name": "Blocked",
                    "customInstructions": None,
                },
            ),
            _request(
                "project-busy",
                "project.chat.move",
                {
                    "chatId": chat_id,
                    "projectId": str(project.project_id),
                },
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _error(messages, "project-missing") == {
        "code": "project.not_found",
        "message": "Project does not exist: project_missing.",
        "retryable": False,
    }
    assert _error(messages, "project-archived")["code"] == (
        "project.archived"
    )
    assert _error(messages, "project-archived")["retryable"] is False
    assert _error(messages, "project-busy")["code"] == "project.chat_busy"
    assert _error(messages, "project-busy")["retryable"] is True


def test_create_open_rename_and_pin_return_uniform_session_state() -> None:
    """Verify that create open rename and pin return uniform session state."""
    fake_brain = FakeBrain()
    created_id = str(fake_brain.next_chat.chat_id)
    open_target = create_chat_session(
        title="Open target",
        mode="chat",
        model_name=fake_brain.model_name,
    )
    fake_brain.add_chat(open_target)

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "create-1",
                "chat.create",
                {"title": "Work session", "mode": "work"},
            ),
            _request(
                "open-1",
                "chat.open",
                {"chatId": str(open_target.chat_id)},
            ),
            _request(
                "rename-1",
                "chat.rename",
                {"chatId": str(open_target.chat_id), "title": "Renamed"},
            ),
            _request(
                "pin-1",
                "chat.pin",
                {"chatId": str(open_target.chat_id), "pinned": True},
            ),
        ],
        fake_brain=fake_brain,
    )

    create_result = _success_result(messages, "create-1")
    open_result = _success_result(messages, "open-1")
    rename_result = _success_result(messages, "rename-1")
    pin_result = _success_result(messages, "pin-1")
    for result in (
        create_result,
        open_result,
        rename_result,
        pin_result,
    ):
        assert set(result) == {"activeChat", "chats"}

    assert create_result["activeChat"]["chatId"] == created_id
    assert create_result["activeChat"]["title"] == "Work session"
    assert create_result["activeChat"]["mode"] == "work"
    assert open_result["activeChat"]["chatId"] == str(
        open_target.chat_id
    )
    assert rename_result["activeChat"]["title"] == "Renamed"
    assert pin_result["activeChat"]["pinned"] is True
    assert (
        "create_chat",
        "Work session",
        "work",
    ) in fake_brain.session_calls
    assert (
        "rename_chat",
        str(open_target.chat_id),
        "Renamed",
    ) in fake_brain.session_calls
    assert (
        "pin_chat",
        str(open_target.chat_id),
        True,
    ) in fake_brain.session_calls


def test_open_rejects_archived_and_wrong_model_chats() -> None:
    """Verify that open rejects archived and wrong model chats."""
    fake_brain = FakeBrain()
    archived = replace(
        create_chat_session(
            title="Archived",
            mode="chat",
            model_name=fake_brain.model_name,
        ),
        is_archived=True,
    )
    wrong_model = create_chat_session(
        title="Other model",
        mode="chat",
        model_name="other-model",
    )
    fake_brain.add_chat(archived)
    fake_brain.add_chat(wrong_model)

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "open-archived",
                "chat.open",
                {"chatId": str(archived.chat_id)},
            ),
            _request(
                "open-wrong-model",
                "chat.open",
                {"chatId": str(wrong_model.chat_id)},
            ),
            _request(
                "active-after-errors",
                "chat.list",
                {"includeArchived": False},
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _error(messages, "open-archived")["code"] == "chat.archived"
    assert (
        _error(messages, "open-wrong-model")["code"]
        == "chat.model_mismatch"
    )
    assert _success_result(
        messages,
        "active-after-errors",
    )["activeChat"]["chatId"] == str(fake_brain.chat.chat_id)


def test_archiving_active_chat_selects_visible_same_model_chat() -> None:
    """Verify that archiving active chat selects visible same model chat."""
    fake_brain = FakeBrain()
    fallback = create_chat_session(
        title="Fallback",
        mode="work",
        model_name=fake_brain.model_name,
    )
    wrong_model = create_chat_session(
        title="Wrong model",
        mode="chat",
        model_name="other-model",
    )
    fake_brain.add_chat(fallback)
    fake_brain.add_chat(wrong_model)

    _, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "archive-active",
                "chat.archive",
                {"chatId": chat_id, "archived": True},
            ),
        ],
        fake_brain=fake_brain,
    )

    result = _success_result(messages, "archive-active")
    assert set(result) == {"activeChat", "chats"}
    assert result["activeChat"]["chatId"] == str(fallback.chat_id)
    archived_summary = next(
        chat
        for chat in result["chats"]
        if chat["chatId"] == str(fake_brain.chat.chat_id)
    )
    assert archived_summary["archived"] is True
    assert (
        "archive_chat",
        str(fake_brain.chat.chat_id),
        True,
    ) in fake_brain.session_calls


def test_deleting_active_chat_creates_default_when_no_model_match() -> None:
    """Verify that deleting active chat creates default when no model match."""
    fake_brain = FakeBrain()
    replacement_id = str(fake_brain.next_chat.chat_id)
    wrong_model = create_chat_session(
        title="Wrong model",
        mode="chat",
        model_name="other-model",
    )
    archived_same_model = replace(
        create_chat_session(
            title="Archived same model",
            mode="chat",
            model_name=fake_brain.model_name,
        ),
        is_archived=True,
    )
    fake_brain.add_chat(wrong_model)
    fake_brain.add_chat(archived_same_model)

    _, messages = _run_bridge(
        lambda chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "delete-active",
                "chat.delete",
                {"chatId": chat_id},
            ),
        ],
        fake_brain=fake_brain,
    )

    result = _success_result(messages, "delete-active")
    assert set(result) == {"activeChat", "chats"}
    assert result["activeChat"]["chatId"] == replacement_id
    assert result["activeChat"]["title"] == "Elysia Chat"
    assert result["activeChat"]["modelName"] == fake_brain.model_name
    assert str(fake_brain.chat.chat_id) not in {
        chat["chatId"] for chat in result["chats"]
    }
    assert (
        "delete_chat",
        str(fake_brain.chat.chat_id),
    ) in fake_brain.session_calls
    assert (
        "create_chat",
        "Elysia Chat",
        "chat",
    ) in fake_brain.session_calls


def test_settings_can_be_read_and_repaired_before_initialize(
    tmp_path: Path,
) -> None:
    """Verify that settings can be read and repaired before initialize."""
    repository = _desktop_settings_repository(tmp_path / "global.json")
    changed = _desktop_settings_values(
        model_name="second-model",
        ollama_host="https://ollama.example.test:11434",
        short_term_memory_token_budget=4_096,
        memory_retrieval_limit=9,
        data_import_max_bytes=32 * 1024 * 1024,
        transcription_model="medium",
        transcription_device="cuda",
        transcription_language="zh",
    )

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _request("settings-before", "settings.get", {}),
            _request(
                "settings-repair",
                "settings.update",
                {"expectedRevision": 0, "settings": changed},
            ),
        ],
        settings_repository=repository,
    )

    initial = _success_result(messages, "settings-before")
    assert initial == {
        "revision": 0,
        "updatedAt": None,
        "settings": _desktop_settings_values(),
        "activeSettings": _desktop_settings_values(),
        "restartRequired": False,
        "restartFields": [],
        "scopes": {"project": None, "chat": None},
        "warning": None,
    }
    repaired = _success_result(messages, "settings-repair")
    assert repaired["revision"] == 1
    assert repaired["settings"] == changed
    assert repaired["activeSettings"] == changed
    assert repaired["restartRequired"] is False
    assert repaired["restartFields"] == []
    assert repository.load().values.model_name == "second-model"
    assert "settings.management" in SERVER_CAPABILITIES


def test_preinitialize_transcription_settings_rebuild_readiness_config(
    tmp_path: Path,
) -> None:
    """Adopt repaired STT settings and discard a status probe using old values."""

    observed_settings: list[AppSettings] = []

    def build_status_transcriber(settings: AppSettings) -> _StatusTranscriber:
        """Record each active configuration used to construct an STT adapter."""

        observed_settings.append(settings)
        return _StatusTranscriber(
            fallback_from_cuda=settings.transcription_device == "auto"
        )

    changed = _desktop_settings_values(
        transcription_model="medium",
        transcription_device="cpu",
        transcription_language="zh",
    )
    with patch.object(
        desktop_backend_module,
        "_create_transcriber",
        side_effect=build_status_transcriber,
    ):
        _, messages = _run_bridge(
            lambda _chat_id: [
                _handshake_request(),
                _request("voice-status-old", "voice.settings.get", {}),
                _request(
                    "settings-stt-repair",
                    "settings.update",
                    {"expectedRevision": 0, "settings": changed},
                ),
                _request("voice-status-new", "voice.settings.get", {}),
            ],
            settings_repository=_desktop_settings_repository(
                tmp_path / "global.json"
            ),
        )

    assert [settings.transcription_model for settings in observed_settings] == [
        "small",
        "medium",
    ]
    assert [settings.transcription_device for settings in observed_settings] == [
        "auto",
        "cpu",
    ]
    repaired = _success_result(messages, "settings-stt-repair")
    assert repaired["activeSettings"] == changed
    assert repaired["restartRequired"] is False
    assert _success_result(messages, "voice-status-old")[
        "transcriptionStatus"
    ]["model"] == "small"
    new_status = _success_result(messages, "voice-status-new")[
        "transcriptionStatus"
    ]
    assert new_status["model"] == "medium"
    assert new_status["requestedDevice"] == "cpu"


def test_preinitialize_transcription_settings_replace_an_idle_runner(
    tmp_path: Path,
) -> None:
    """Close an idle old-config worker before constructing its replacement."""

    observed_settings: list[AppSettings] = []

    def build_transcriber(settings: AppSettings) -> _StatusTranscriber:
        """Record the active configuration used by each default runner."""

        observed_settings.append(settings)
        return _StatusTranscriber()

    output_stream = StringIO()
    with patch.object(
        desktop_backend_module,
        "_create_transcriber",
        side_effect=build_transcriber,
    ):
        backend = DesktopBackend(
            settings_repository=_desktop_settings_repository(
                tmp_path / "global.json"
            ),
            voice_settings_service=create_voice_settings_service(tmp_path),
            input_stream=StringIO(),
            output_stream=output_stream,
            expected_session_token=SESSION_TOKEN,
        )
        with backend._state_lock:
            original_runner = backend._get_transcription_runner_locked()
        assert backend._handle_line(json.dumps(_handshake_request()))
        assert backend._handle_line(
            json.dumps(
                _request(
                    "settings-replace-idle-stt",
                    "settings.update",
                    {
                        "expectedRevision": 0,
                        "settings": _desktop_settings_values(
                            transcription_model="medium",
                            transcription_device="cpu",
                            transcription_language="en",
                        ),
                    },
                )
            )
        )
        assert original_runner.get_status().closed is True
        with backend._state_lock:
            replacement_runner = backend._get_transcription_runner_locked()
        assert replacement_runner is not original_runner
        assert [settings.transcription_model for settings in observed_settings] == [
            "small",
            "medium",
        ]
        backend._prepare_transcription_shutdown()


def test_voice_capture_returns_only_safe_metadata_without_chat_side_effects(
) -> None:
    """Verify that voice capture returns only safe metadata without chat side effects.
    """
    fake_brain = FakeBrain()
    original_chat = fake_brain.chat
    chat_id = str(original_chat.chat_id)
    params = _voice_capture_params(chat_id)
    encoded_pcm = cast(str, params["pcmBase64"])
    pcm = base64.b64decode(encoded_pcm, validate=True)

    brain, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "voice-capture-valid",
                "voice.capture.complete",
                params,
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _success_result(messages, "voice-capture-valid") == {
        "kind": "voice.capture",
        "sessionId": "voice_backend_fixture",
        "chatId": chat_id,
        "sampleRateHz": 16_000,
        "channelCount": 1,
        "sampleFormat": "s16le",
        "sampleCount": 3_200,
        "speechStartSample": 0,
        "speechEndSample": 3_200,
        "durationMs": 200,
        "speechDurationMs": 200,
        "sha256Hex": hashlib.sha256(pcm).hexdigest(),
    }
    assert encoded_pcm not in json.dumps(messages)
    assert brain.stream_calls == []
    assert brain.get_chat(original_chat.chat_id) == original_chat


def test_voice_capture_rejects_silence_without_leaking_pcm() -> None:
    """Verify that voice capture rejects silence without leaking PCM."""
    fake_brain = FakeBrain()
    original_chat = fake_brain.chat
    params = _voice_capture_params(str(original_chat.chat_id), silent=True)
    encoded_pcm = cast(str, params["pcmBase64"])

    brain, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "voice-capture-silent",
                "voice.capture.complete",
                params,
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _error(messages, "voice-capture-silent") == {
        "code": "voice.capture.invalid",
        "message": "Voice capture speech window must contain non-silent PCM.",
        "retryable": False,
    }
    assert encoded_pcm not in json.dumps(messages)
    assert brain.stream_calls == []
    assert brain.get_chat(original_chat.chat_id) == original_chat


def test_voice_capture_rejects_a_chat_other_than_the_active_chat() -> None:
    """Verify that voice capture rejects a chat other than the active chat."""
    fake_brain = FakeBrain()
    original_chat = fake_brain.chat
    params = _voice_capture_params("chat_voice_mismatch")

    brain, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "voice-capture-wrong-chat",
                "voice.capture.complete",
                params,
            ),
        ],
        fake_brain=fake_brain,
    )

    assert _error(messages, "voice-capture-wrong-chat") == {
        "code": "voice.capture.chat_mismatch",
        "message": "Voice capture no longer belongs to the active Chat.",
        "retryable": False,
    }
    assert brain.stream_calls == []
    assert brain.get_chat(original_chat.chat_id) == original_chat


def test_voice_capture_is_rejected_while_chat_generation_is_active() -> None:
    """Verify that voice capture is rejected while chat generation is active."""
    generation_started = Event()
    release_generation = Event()

    class BlockingBrain(FakeBrain):
        """Hold generation active while a Voice capture request is handled."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Signal active generation and wait before delegating persistence."""
            generation_started.set()
            assert release_generation.wait(1.0)
            yield from super().stream_chat(
                chat_id,
                message,
                attachments=attachments,
                should_cancel=should_cancel,
                begin_commit=begin_commit,
            )

    fake_brain = BlockingBrain()
    release_timer = Timer(0.1, release_generation.set)
    release_timer.start()
    try:
        brain, messages = _run_bridge(
            lambda chat_id: [
                _handshake_request(),
                _initialize_request(),
                _request(
                    "chat-active-for-capture",
                    "chat.stream",
                    {"chatId": chat_id, "message": "Keep working"},
                ),
                _request(
                    "voice-capture-busy",
                    "voice.capture.complete",
                    _voice_capture_params(chat_id),
                ),
            ],
            fake_brain=fake_brain,
        )
    finally:
        release_generation.set()
        release_timer.join()

    assert generation_started.is_set()
    assert _error(messages, "voice-capture-busy") == {
        "code": "voice.capture.busy",
        "message": "Wait for the current reply before submitting voice input.",
        "retryable": False,
    }
    assert brain.stream_calls == [
        (str(fake_brain.chat.chat_id), "Keep working")
    ]


def test_voice_transcription_is_lazy_ordered_and_pcm_free(
    tmp_path: Path,
) -> None:
    """Emit an ordered final transcript without echoing transient audio."""

    terminal = Event()
    transcriber = _ControlledTranscriber(
        TranscriptionResult(
            text="你好，世界。",
            language="zh",
            language_probability=0.875,
        )
    )
    transcriber.release.set()
    factory, runners = _transcription_runner_factory(
        transcriber,
        terminal_event=terminal,
    )
    backend, fake_brain, output_stream = _initialized_backend(
        tmp_path,
        factory,
    )
    assert runners == []
    params = {
        **_voice_capture_params(str(fake_brain.chat.chat_id)),
        "language": "auto",
    }
    encoded_pcm = cast(str, params["pcmBase64"])

    try:
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-transcription-success",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        assert terminal.wait(2.0)

        messages = _output_messages(output_stream)
        related = [
            message
            for message in messages
            if message.get("requestId") == "voice-transcription-success"
            or message.get("id") == "voice-transcription-success"
        ]
        assert [
            (
                message["type"],
                message.get("event")
                or message.get("operation")
                or "response",
            )
            for message in related
        ] == [
            ("event", "voice.transcription.started"),
            ("progress", "voice.transcribe"),
            ("progress", "voice.transcribe"),
            ("event", "voice.transcription.completed"),
            ("response", "response"),
        ]
        assert _success_result(
            messages,
            "voice-transcription-success",
        ) == {
            "kind": "voice.transcription",
            "sessionId": "voice_backend_fixture",
            "chatId": str(fake_brain.chat.chat_id),
            "text": "你好，世界。",
            "language": "zh",
            "languageProbability": 0.875,
        }
        assert len(runners) == 1
        assert len(transcriber.requests) == 1
        assert transcriber.requests[0].language == "auto"
        assert encoded_pcm not in output_stream.getvalue()
        assert "pcmBase64" not in output_stream.getvalue()
        assert "modelPath" not in output_stream.getvalue()
        assert "voice.transcription" in SERVER_CAPABILITIES
    finally:
        backend._prepare_transcription_shutdown()


@pytest.mark.parametrize(
    "model_name",
    ["tiny", "base", "small", "medium", "large-v3", "turbo"],
)
def test_transcriber_path_is_derived_only_from_the_model_allowlist(
    tmp_path: Path,
    model_name: str,
) -> None:
    """Map every public model choice to its fixed offline directory."""

    settings = replace(
        desktop_backend_module.SETTINGS,
        base_dir=tmp_path,
        transcription_model=cast(Any, model_name),
        transcription_device="cpu",
    )
    sentinel = object()
    with patch.object(
        desktop_backend_module,
        "FasterWhisperTranscriber",
        return_value=sentinel,
    ) as constructor:
        assert desktop_backend_module._create_transcriber(settings) is sentinel

    config = constructor.call_args.args[0]
    assert isinstance(config, FasterWhisperConfig)
    assert config.model_path == (
        tmp_path.resolve()
        / "models"
        / "weights"
        / "faster-whisper"
        / model_name
    )
    assert config.device == "cpu"


def test_transcriber_rejects_a_model_alias_before_path_construction(
    tmp_path: Path,
) -> None:
    """Prevent arbitrary paths or downloadable aliases reaching the adapter."""

    settings = replace(
        desktop_backend_module.SETTINGS,
        base_dir=tmp_path,
        transcription_model=cast(Any, "../../remote-model"),
    )

    with pytest.raises(DesktopSettingsValidationError):
        desktop_backend_module._create_transcriber(settings)


def test_voice_readiness_uses_the_same_adapter_as_default_transcription(
    tmp_path: Path,
) -> None:
    """Report the production adapter's available-to-ready transition safely."""

    transcriber = _StatusTranscriber()
    fake_brain = FakeBrain()
    output_stream = StringIO()
    with patch.object(
        desktop_backend_module,
        "_create_transcriber",
        return_value=transcriber,
    ) as create_transcriber:
        backend = DesktopBackend(
            brain_factory=lambda: cast(Brain, fake_brain),
            model_loader=lambda: (fake_brain.model_name,),
            settings_validator=lambda: None,
            settings_repository=_desktop_settings_repository(
                tmp_path / "global.json"
            ),
            voice_settings_service=create_voice_settings_service(tmp_path),
            attachment_store=JsonAttachmentStore(
                tmp_path / "attachments",
                max_file_bytes=1_024 * 1_024,
            ),
            input_stream=StringIO(),
            output_stream=output_stream,
            expected_session_token=SESSION_TOKEN,
        )
        assert backend._handle_line(json.dumps(_handshake_request()))
        assert backend._handle_line(json.dumps(_initialize_request()))
        assert backend._handle_line(
            json.dumps(_request("voice-status-before", "voice.settings.get", {}))
        )
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-status-transcribe",
                    "voice.transcription.start",
                    {
                        **_voice_capture_params(str(fake_brain.chat.chat_id)),
                        "language": "zh",
                    },
                )
            )
        )
        assert transcriber.finished.wait(2.0)
        assert backend._handle_line(
            json.dumps(_request("voice-status-after", "voice.settings.get", {}))
        )

        messages = _output_messages(output_stream)
        before = _success_result(messages, "voice-status-before")
        after = _success_result(messages, "voice-status-after")
        assert before["transcriptionStatus"] == {
            "state": "available",
            "model": "small",
            "requestedDevice": "auto",
            "resolvedDevice": "cpu",
            "computeType": "int8",
            "reason": "cuda_unavailable",
        }
        assert after["transcriptionStatus"] == {
            **before["transcriptionStatus"],
            "state": "ready",
        }
        assert create_transcriber.call_count == 1
        assert transcriber.requests[0].language == "zh"
        assert str(tmp_path) not in output_stream.getvalue()
        assert "native" not in output_stream.getvalue().lower()
        backend._prepare_transcription_shutdown()


def test_voice_transcription_failure_hides_adapter_details(
    tmp_path: Path,
) -> None:
    """Map an unavailable adapter to a fixed Renderer-safe failure."""

    private_detail = "D:/Users/private/model and raw PCM diagnostics"
    terminal = Event()
    transcriber = _ControlledTranscriber(
        TranscriptionUnavailableError(private_detail)
    )
    transcriber.release.set()
    factory, _runners = _transcription_runner_factory(
        transcriber,
        terminal_event=terminal,
    )
    backend, fake_brain, output_stream = _initialized_backend(
        tmp_path,
        factory,
    )
    params = {
        **_voice_capture_params(str(fake_brain.chat.chat_id)),
        "language": "en",
    }

    try:
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-transcription-unavailable",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        assert terminal.wait(2.0)
        messages = _output_messages(output_stream)

        assert _error(messages, "voice-transcription-unavailable") == {
            "code": "voice.transcription.unavailable",
            "message": "Local voice transcription is unavailable.",
            "retryable": False,
        }
        assert any(
            message.get("event") == "voice.transcription.failed"
            for message in messages
        )
        assert private_detail not in output_stream.getvalue()
        assert cast(str, params["pcmBase64"]) not in output_stream.getvalue()
    finally:
        backend._prepare_transcription_shutdown()


def test_active_chat_generation_rejects_transcription_before_runner_creation(
    tmp_path: Path,
) -> None:
    """Keep Chat and STT mutually exclusive without allocating idle workers."""

    transcriber = _ControlledTranscriber(
        TranscriptionResult(
            text="unused",
            language="en",
            language_probability=1.0,
        )
    )
    factory, runners = _transcription_runner_factory(transcriber)
    backend, fake_brain, output_stream = _initialized_backend(
        tmp_path,
        factory,
    )
    active_task = desktop_backend_module._GenerationTask(
        request_id="active-chat",
        chat_id=fake_brain.chat.chat_id,
        method="chat.stream",
    )
    backend._generation_task = active_task
    params = {
        **_voice_capture_params(str(fake_brain.chat.chat_id)),
        "language": "auto",
    }

    try:
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-during-chat",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        assert _error(
            _output_messages(output_stream),
            "voice-during-chat",
        ) == {
            "code": "voice.transcription.busy",
            "message": "Local voice transcription is already busy.",
            "retryable": True,
        }
        assert runners == []
        assert cast(str, params["pcmBase64"]) not in output_stream.getvalue()
    finally:
        active_task.finish()
        backend._generation_task = None
        transcriber.release.set()
        backend._prepare_transcription_shutdown()


def test_cancelled_transcription_blocks_work_until_native_inference_drains(
    tmp_path: Path,
) -> None:
    """Cancel promptly while preserving physical STT exclusion until return."""

    terminal = Event()
    transcriber = _ControlledTranscriber(
        TranscriptionResult(
            text="late result",
            language="en",
            language_probability=0.75,
        )
    )
    factory, runners = _transcription_runner_factory(
        transcriber,
        terminal_event=terminal,
    )
    backend, fake_brain, output_stream = _initialized_backend(
        tmp_path,
        factory,
    )
    chat_id = str(fake_brain.chat.chat_id)
    params = {**_voice_capture_params(chat_id), "language": "en"}

    try:
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-cancel-target",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        assert transcriber.entered.wait(2.0)
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-cancel-command",
                    "request.cancel",
                    {"requestId": "voice-cancel-target"},
                )
            )
        )
        assert terminal.wait(2.0)
        runner = runners[0]
        assert backend._transcription_task is None
        assert runner.get_status().occupied_slots == 1

        assert backend._handle_line(
            json.dumps(
                _request(
                    "capture-during-drain",
                    "voice.capture.complete",
                    _voice_capture_params(chat_id),
                )
            )
        )
        assert backend._handle_line(
            json.dumps(
                _request(
                    "chat-during-drain",
                    "chat.stream",
                    {"chatId": chat_id, "message": "Do not overlap"},
                )
            )
        )
        assert backend._handle_line(
            json.dumps(
                _request(
                    "settings-during-drain",
                    "settings.update",
                    {
                        "expectedRevision": 0,
                        "settings": _desktop_settings_values(
                            transcription_model="medium"
                        ),
                    },
                )
            )
        )
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-settings-during-drain",
                    "voice.settings.update",
                    {
                        "expectedRevision": 0,
                        "inputDeviceId": None,
                        "outputDeviceId": None,
                    },
                )
            )
        )
        messages = _output_messages(output_stream)
        assert _error(messages, "voice-cancel-target")["code"] == (
            "request.cancelled"
        )
        assert _success_result(messages, "voice-cancel-command") == {
            "stopped": True,
        }
        target_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("id") == "voice-cancel-target"
        )
        cancel_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("id") == "voice-cancel-command"
        )
        assert target_index < cancel_index
        assert _error(messages, "capture-during-drain")["code"] == (
            "voice.capture.busy"
        )
        assert _error(messages, "chat-during-drain") == {
            "code": "chat.busy",
            "message": (
                "Wait for local voice transcription before generating a reply."
            ),
            "retryable": False,
        }
        assert _error(messages, "settings-during-drain") == {
            "code": "settings.busy",
            "message": "Wait for local transcription before changing settings.",
            "retryable": False,
        }
        assert _error(messages, "voice-settings-during-drain") == {
            "code": "voice.settings.busy",
            "message": (
                "Wait for local transcription before changing voice settings."
            ),
            "retryable": False,
        }

        output_before_native_return = output_stream.getvalue()
        transcriber.release.set()
        assert transcriber.finished.wait(2.0)
        assert runner.shutdown(wait=True, timeout_seconds=1.0)
        assert output_stream.getvalue() == output_before_native_return

        assert backend._handle_line(
            json.dumps(
                _request(
                    "capture-after-drain",
                    "voice.capture.complete",
                    _voice_capture_params(chat_id),
                )
            )
        )
        assert _success_result(
            _output_messages(output_stream),
            "capture-after-drain",
        )["kind"] == "voice.capture"
    finally:
        transcriber.release.set()
        backend._prepare_transcription_shutdown()


def test_timed_out_transcription_keeps_stt_capacity_until_native_return(
    tmp_path: Path,
) -> None:
    """Publish timeout once and reject replacement STT while work drains."""

    terminal = Event()
    transcriber = _ControlledTranscriber(
        TranscriptionResult(
            text="too late",
            language="en",
            language_probability=0.5,
        )
    )
    factory, runners = _transcription_runner_factory(
        transcriber,
        terminal_event=terminal,
        timeout_seconds=0.05,
    )
    backend, fake_brain, output_stream = _initialized_backend(
        tmp_path,
        factory,
    )
    chat_id = str(fake_brain.chat.chat_id)
    params = {**_voice_capture_params(chat_id), "language": "auto"}

    try:
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-timeout-target",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        assert transcriber.entered.wait(2.0)
        assert terminal.wait(2.0)
        runner = runners[0]
        assert runner.get_status().occupied_slots == 1

        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-during-timeout-drain",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        messages = _output_messages(output_stream)
        assert _error(messages, "voice-timeout-target") == {
            "code": "voice.transcription.timeout",
            "message": "Local voice transcription timed out.",
            "retryable": True,
        }
        assert any(
            message.get("event") == "voice.transcription.timed_out"
            for message in messages
        )
        assert _error(messages, "voice-during-timeout-drain") == {
            "code": "voice.transcription.busy",
            "message": "Local voice transcription is already busy.",
            "retryable": True,
        }
        assert len(transcriber.requests) == 1

        output_before_native_return = output_stream.getvalue()
        transcriber.release.set()
        assert transcriber.finished.wait(2.0)
        assert runner.shutdown(wait=True, timeout_seconds=1.0)
        assert output_stream.getvalue() == output_before_native_return
    finally:
        transcriber.release.set()
        backend._prepare_transcription_shutdown()


def test_shutdown_suppresses_a_completed_job_waiting_to_enter_its_callback(
    tmp_path: Path,
) -> None:
    """Let shutdown win after native success but before terminal emission."""

    callback_waiting = Event()
    release_callback = Event()
    callback_finished = Event()
    transcriber = _ControlledTranscriber(
        TranscriptionResult(
            text="must stay private after shutdown",
            language="en",
            language_probability=1.0,
        )
    )
    transcriber.release.set()
    runners: list[TranscriptionJobRunner] = []

    def _factory(
        callback: TranscriptionJobCallback,
    ) -> TranscriptionJobRunner:
        """Delay Backend callback entry after the runner reaches success."""

        def _delay_terminal(snapshot: TranscriptionJobSnapshot) -> None:
            """Expose the exact native-complete/shutdown race to the test."""

            callback_waiting.set()
            assert release_callback.wait(2.0)
            try:
                callback(snapshot)
            finally:
                callback_finished.set()

        runner = TranscriptionJobRunner(
            transcriber,
            config=TranscriptionJobRunnerConfig(
                max_concurrent_jobs=1,
                max_queued_jobs=0,
                default_timeout_seconds=1.0,
            ),
            on_terminal=_delay_terminal,
        )
        runners.append(runner)
        return runner

    backend, fake_brain, output_stream = _initialized_backend(
        tmp_path,
        _factory,
    )
    params = {
        **_voice_capture_params(str(fake_brain.chat.chat_id)),
        "language": "auto",
    }

    try:
        assert backend._handle_line(
            json.dumps(
                _request(
                    "voice-completed-before-shutdown",
                    "voice.transcription.start",
                    params,
                )
            )
        )
        assert callback_waiting.wait(2.0)
        output_before_shutdown = output_stream.getvalue()

        backend._prepare_transcription_shutdown()
        release_callback.set()
        assert callback_finished.wait(2.0)
        assert output_stream.getvalue() == output_before_shutdown
        assert not any(
            message.get("id") == "voice-completed-before-shutdown"
            for message in _output_messages(output_stream)
        )
        assert runners[0].shutdown(wait=True, timeout_seconds=1.0)
    finally:
        release_callback.set()
        backend._prepare_transcription_shutdown()


@pytest.mark.parametrize("termination", ["shutdown", "eof"])
def test_transcription_shutdown_is_bounded_and_suppresses_late_output(
    tmp_path: Path,
    termination: str,
) -> None:
    """Return promptly and never emit STT terminal frames after teardown."""

    terminal = Event()
    transcriber = _ControlledTranscriber(
        TranscriptionResult(
            text="late private transcript",
            language="en",
            language_probability=0.5,
        )
    )
    factory, runners = _transcription_runner_factory(
        transcriber,
        terminal_event=terminal,
    )
    fake_brain = FakeBrain()
    chat_id = str(fake_brain.chat.chat_id)
    params = {**_voice_capture_params(chat_id), "language": "auto"}

    def _request_lines() -> Generator[str, None, None]:
        """End input or request shutdown while native STT remains blocked."""

        for request in (
            _handshake_request(),
            _initialize_request(),
            _request(
                "voice-shutdown-target",
                "voice.transcription.start",
                params,
            ),
        ):
            yield f"{json.dumps(request)}\n"
        assert transcriber.entered.wait(2.0)
        if termination == "shutdown":
            yield f"{json.dumps(_request('shutdown-stt', 'shutdown', {}))}\n"

    output_stream = StringIO()
    backend = DesktopBackend(
        brain_factory=lambda: cast(Brain, fake_brain),
        model_loader=lambda: (fake_brain.model_name,),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        voice_settings_service=create_voice_settings_service(tmp_path),
        transcription_runner_factory=factory,
        attachment_store=JsonAttachmentStore(
            tmp_path / "attachments",
            max_file_bytes=1_024 * 1_024,
        ),
        input_stream=cast(TextIOWrapper, _request_lines()),
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    )

    started_at = monotonic()
    backend.run()
    elapsed = monotonic() - started_at
    assert elapsed < 0.5
    assert terminal.is_set()
    messages = _output_messages(output_stream)
    assert not any(
        message.get("id") == "voice-shutdown-target"
        for message in messages
    )
    if termination == "shutdown":
        assert _success_result(messages, "shutdown-stt") == {"stopped": True}
    else:
        assert not any(
            message.get("id") == "shutdown-stt"
            for message in messages
        )

    output_at_shutdown = output_stream.getvalue()
    transcriber.release.set()
    assert transcriber.finished.wait(2.0)
    assert runners[0].shutdown(wait=True, timeout_seconds=1.0)
    assert output_stream.getvalue() == output_at_shutdown


def test_voice_settings_round_trip_before_brain_initialization(
    tmp_path: Path,
) -> None:
    """Verify that voice settings round trip before brain initialization."""
    service = create_voice_settings_service(tmp_path)

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _request("voice-before", "voice.settings.get", {}),
            _request(
                "voice-save",
                "voice.settings.update",
                {
                    "expectedRevision": 0,
                    "inputDeviceId": "opaque-input-id",
                    "outputDeviceId": None,
                },
            ),
            _request("voice-after", "voice.settings.get", {}),
        ],
        voice_settings_service=service,
    )

    before = _success_result(messages, "voice-before")
    transcription_status = before.pop("transcriptionStatus")
    assert before == {
        "kind": "voice.settings",
        "revision": 0,
        "updatedAt": None,
        "inputDeviceId": None,
        "outputDeviceId": None,
        "warning": None,
    }
    assert transcription_status["model"] == "small"
    assert transcription_status["requestedDevice"] == "auto"
    assert set(transcription_status) == {
        "state",
        "model",
        "requestedDevice",
        "resolvedDevice",
        "computeType",
        "reason",
    }
    assert str(tmp_path) not in json.dumps(transcription_status)
    saved = _success_result(messages, "voice-save")
    assert saved["revision"] == 1
    assert saved["inputDeviceId"] == "opaque-input-id"
    assert saved["outputDeviceId"] is None
    assert _success_result(messages, "voice-after") == saved
    assert JsonVoiceSettingsRepository(
        tmp_path / "workspace" / "settings" / "audio-device.json"
    ).load().values.input_device_id == "opaque-input-id"
    assert "voice.settings" in SERVER_CAPABILITIES


def test_voice_settings_remain_available_after_brain_initialization_fails(
    tmp_path: Path,
) -> None:
    """Verify that voice settings remain available after brain initialization fails."""
    requests = [
        _handshake_request(),
        _initialize_request(),
        _request("voice-after-failure", "voice.settings.get", {}),
        _request("shutdown-after-failure", "shutdown", {}),
    ]
    input_stream = StringIO(
        "".join(f"{json.dumps(request)}\n" for request in requests)
    )
    output_stream = StringIO()

    def fail_brain() -> Brain:
        """Simulate deferred Brain initialization failing after handshake."""
        raise RuntimeError("simulated Brain initialization failure")

    DesktopBackend(
        brain_factory=fail_brain,
        model_loader=lambda: ("test-model",),
        settings_validator=lambda: None,
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        voice_settings_service=create_voice_settings_service(tmp_path),
        input_stream=input_stream,
        output_stream=output_stream,
        expected_session_token=SESSION_TOKEN,
    ).run()
    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]

    assert _error(messages, "initialize-1")["code"] == (
        "backend.request_failed"
    )
    assert _success_result(messages, "voice-after-failure")["kind"] == (
        "voice.settings"
    )


def test_voice_settings_conflict_is_typed_and_retryable(
    tmp_path: Path,
) -> None:
    """Verify that voice settings conflict is typed and retryable."""
    service = create_voice_settings_service(tmp_path)

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _request(
                "voice-first",
                "voice.settings.update",
                {
                    "expectedRevision": 0,
                    "inputDeviceId": "first-input",
                    "outputDeviceId": None,
                },
            ),
            _request(
                "voice-stale",
                "voice.settings.update",
                {
                    "expectedRevision": 0,
                    "inputDeviceId": "stale-input",
                    "outputDeviceId": None,
                },
            ),
        ],
        voice_settings_service=service,
    )

    assert _error(messages, "voice-stale") == {
        "code": "voice.settings.conflict",
        "message": "Voice settings changed elsewhere. Reload before saving.",
        "retryable": True,
    }
    assert service.get_settings().values.input_device_id == "first-input"


def test_voice_settings_storage_failure_is_typed_and_keeps_ids_private(
    tmp_path: Path,
) -> None:
    """Type Voice storage failures without leaking private device identifiers."""
    blocked_base = tmp_path / "blocked"
    blocked_base.mkdir()
    (blocked_base / "workspace").write_text("not a directory", encoding="utf-8")
    private_id = "private-device-id"

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _request(
                "voice-storage-failure",
                "voice.settings.update",
                {
                    "expectedRevision": 0,
                    "inputDeviceId": private_id,
                    "outputDeviceId": None,
                },
            ),
        ],
        voice_settings_service=create_voice_settings_service(blocked_base),
    )

    error = _error(messages, "voice-storage-failure")
    assert error["code"] == "voice.settings.storage_failed"
    assert error["retryable"] is True
    assert private_id not in json.dumps(messages)


def test_voice_settings_update_is_not_blocked_by_active_chat_generation(
    tmp_path: Path,
) -> None:
    """Verify that voice settings update is not blocked by active chat generation."""
    generation_started = Event()
    release_generation = Event()

    class BlockingBrain(FakeBrain):
        """Hold generation active while Voice device settings are updated."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Signal active generation and wait before delegating persistence."""
            generation_started.set()
            assert release_generation.wait(1.0)
            yield from super().stream_chat(
                chat_id,
                message,
                attachments=attachments,
                should_cancel=should_cancel,
                begin_commit=begin_commit,
            )

    service = create_voice_settings_service(tmp_path)
    release_timer = Timer(0.1, release_generation.set)
    release_timer.start()
    try:
        _, messages = _run_bridge(
            lambda chat_id: [
                _handshake_request(),
                _initialize_request(),
                _request(
                    "chat-active-for-voice",
                    "chat.stream",
                    {"chatId": chat_id, "message": "Keep working"},
                ),
                _request(
                    "voice-during-generation",
                    "voice.settings.update",
                    {
                        "expectedRevision": 0,
                        "inputDeviceId": "input-during-generation",
                        "outputDeviceId": None,
                    },
                ),
            ],
            fake_brain=BlockingBrain(),
            voice_settings_service=service,
        )
    finally:
        release_generation.set()
        release_timer.join()

    assert generation_started.is_set()
    result = _success_result(messages, "voice-during-generation")
    assert result["revision"] == 1
    assert result["inputDeviceId"] == "input-during-generation"
    assert service.get_settings().values.input_device_id == (
        "input-during-generation"
    )


def test_invalid_bootstrap_uses_safe_defaults_and_still_initializes(
    tmp_path: Path,
) -> None:
    """Verify that invalid bootstrap uses safe defaults and still initializes."""
    class DefaultModelBrain(FakeBrain):
        """Represent the safe fallback Brain selected for invalid bootstrap data."""

        model_name = DEFAULT_MODEL_NAME

    invalid_bootstrap = AppSettings(
        base_dir=tmp_path,
        model_name=" ",
        log_level="INFO",
        debug=False,
        ollama_host="not-an-origin",
        short_term_memory_token_budget=0,
        memory_retrieval_limit=-1,
        data_import_max_bytes=2_147_483_648,
    )

    messages = _run_bridge_with_bootstrap_settings(
        invalid_bootstrap,
        DefaultModelBrain(),
    )

    settings = _success_result(messages, "settings-safe")
    assert settings["revision"] == 0
    assert settings["settings"] == {
        "modelName": DEFAULT_MODEL_NAME,
        "ollamaHost": DEFAULT_OLLAMA_HOST,
        "shortTermMemoryTokenBudget": (
            DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET
        ),
        "memoryRetrievalLimit": DEFAULT_MEMORY_RETRIEVAL_LIMIT,
        "dataImportMaxBytes": DEFAULT_DATA_IMPORT_MAX_BYTES,
        "transcriptionModel": DEFAULT_TRANSCRIPTION_MODEL,
        "transcriptionDevice": DEFAULT_TRANSCRIPTION_DEVICE,
        "transcriptionLanguage": DEFAULT_TRANSCRIPTION_LANGUAGE,
    }
    initialized = _success_result(messages, "initialize-1")
    assert initialized["modelName"] == DEFAULT_MODEL_NAME


def test_invalid_bootstrap_prefers_valid_persisted_settings(
    tmp_path: Path,
) -> None:
    """Verify that invalid bootstrap prefers valid persisted settings."""
    settings_path = (
        tmp_path / "workspace" / "settings" / "global.json"
    )
    persisted_repository = _desktop_settings_repository(settings_path)
    persisted_values = EditableDesktopSettings(
        model_name="test-model",
        ollama_host="https://ollama.example.test:11434",
        short_term_memory_token_budget=4_096,
        memory_retrieval_limit=9,
        data_import_max_bytes=32 * 1024 * 1024,
    )
    persisted_repository.save(persisted_values, expected_revision=0)
    invalid_bootstrap = AppSettings(
        base_dir=tmp_path,
        model_name="",
        log_level="INFO",
        debug=False,
        ollama_host="ftp://invalid.example.test",
        short_term_memory_token_budget=0,
        memory_retrieval_limit=0,
        data_import_max_bytes=0,
    )

    messages = _run_bridge_with_bootstrap_settings(
        invalid_bootstrap,
        FakeBrain(),
    )

    settings = _success_result(messages, "settings-safe")
    assert settings["revision"] == 1
    assert settings["settings"] == _desktop_settings_values(
        model_name="test-model",
        ollama_host="https://ollama.example.test:11434",
        short_term_memory_token_budget=4_096,
        memory_retrieval_limit=9,
        data_import_max_bytes=32 * 1024 * 1024,
    )
    assert settings["activeSettings"] == settings["settings"]
    initialized = _success_result(messages, "initialize-1")
    assert initialized["modelName"] == "test-model"


def test_settings_update_reports_restart_fields_and_active_scopes(
    tmp_path: Path,
) -> None:
    """Verify that settings update reports restart fields and active scopes."""
    repository = _desktop_settings_repository(tmp_path / "global.json")
    fake_brain = FakeBrain()
    project = create_project(
        name="Scoped Project",
        settings=ProjectSettings(default_model_name="project-model"),
    )
    fake_brain.add_project(project)
    fake_brain.add_chat(
        replace(fake_brain.chat, project_id=project.project_id)
    )
    changed = _desktop_settings_values(
        model_name="second-model",
        ollama_host="https://ollama.example.test:11434",
        short_term_memory_token_budget=4_096,
        memory_retrieval_limit=9,
        data_import_max_bytes=32 * 1024 * 1024,
        transcription_model="medium",
        transcription_device="cuda",
        transcription_language="zh",
    )

    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "settings-after",
                "settings.update",
                {"expectedRevision": 0, "settings": changed},
            ),
        ],
        fake_brain=fake_brain,
        settings_repository=repository,
    )

    result = _success_result(messages, "settings-after")
    assert result["revision"] == 1
    assert result["settings"] == changed
    assert result["activeSettings"] == _desktop_settings_values()
    assert result["restartRequired"] is True
    assert result["restartFields"] == [
        "modelName",
        "ollamaHost",
        "shortTermMemoryTokenBudget",
        "memoryRetrievalLimit",
        "dataImportMaxBytes",
        "transcriptionModel",
        "transcriptionDevice",
        "transcriptionLanguage",
    ]
    assert result["scopes"]["project"] == {
        "projectId": str(project.project_id),
        "projectName": "Scoped Project",
        "modelName": "project-model",
        "inheritedModelName": "second-model",
    }
    assert result["scopes"]["chat"] == {
        "chatId": str(fake_brain.chat.chat_id),
        "chatTitle": fake_brain.chat.title,
        "modelName": "test-model",
    }


def test_settings_update_rejects_unknown_sensitive_fields_at_wire_boundary(
    tmp_path: Path,
) -> None:
    """Verify that settings update rejects unknown sensitive fields at wire boundary."""
    repository = _desktop_settings_repository(tmp_path / "global.json")
    update = {
        "type": "request",
        "protocol": {
            "name": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
        },
        "id": "settings-sensitive",
        "method": "settings.update",
        "params": {
            "expectedRevision": 0,
            "settings": {
                **_desktop_settings_values(),
                "apiKey": "must-not-cross-the-wire",
            },
        },
    }

    _, messages = _run_bridge(
        lambda _chat_id: [_handshake_request(), update],
        settings_repository=repository,
    )

    assert _error(messages, "settings-sensitive")["code"] == (
        "protocol.invalid_params"
    )
    assert "must-not-cross-the-wire" not in json.dumps(messages)
    assert repository.load().revision == 0
    assert not repository.path.exists()


def test_settings_update_rejects_unsafe_ollama_origin_without_echoing_it(
    tmp_path: Path,
) -> None:
    """Reject an unsafe Ollama origin without reflecting the secret-bearing URL."""
    repository = _desktop_settings_repository(tmp_path / "global.json")
    unsafe_origin = "http://user:secret@localhost:11434"
    update = {
        "type": "request",
        "protocol": {
            "name": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
        },
        "id": "settings-invalid",
        "method": "settings.update",
        "params": {
            "expectedRevision": 0,
            "settings": _desktop_settings_values(
                ollama_host=unsafe_origin
            ),
        },
    }

    _, messages = _run_bridge(
        lambda _chat_id: [_handshake_request(), update],
        settings_repository=repository,
    )

    error = _error(messages, "settings-invalid")
    assert error["code"] == "protocol.invalid_params"
    assert error["retryable"] is False
    assert unsafe_origin not in json.dumps(messages)
    assert repository.load().revision == 0


def test_settings_update_is_rejected_while_generation_is_active(
    tmp_path: Path,
) -> None:
    """Verify that settings update is rejected while generation is active."""
    generation_started = Event()
    release_generation = Event()

    class BlockingBrain(FakeBrain):
        """Hold generation active while a global settings update is attempted."""

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            attachments: tuple[AttachmentMetadata, ...] = (),
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            """Signal active generation and wait before delegating persistence."""
            generation_started.set()
            assert release_generation.wait(1.0)
            yield from super().stream_chat(
                chat_id,
                message,
                attachments=attachments,
                should_cancel=should_cancel,
                begin_commit=begin_commit,
            )

    repository = _desktop_settings_repository(tmp_path / "global.json")
    release_timer = Timer(0.1, release_generation.set)
    release_timer.start()
    try:
        _, messages = _run_bridge(
            lambda chat_id: [
                _handshake_request(),
                _initialize_request(),
                _request(
                    "chat-active",
                    "chat.stream",
                    {"chatId": chat_id, "message": "Keep working"},
                ),
                _request(
                    "settings-busy",
                    "settings.update",
                    {
                        "expectedRevision": 0,
                        "settings": _desktop_settings_values(
                            model_name="second-model"
                        ),
                    },
                ),
            ],
            fake_brain=BlockingBrain(),
            settings_repository=repository,
        )
    finally:
        release_generation.set()
        release_timer.join()

    assert generation_started.is_set()
    assert _error(messages, "settings-busy") == {
        "code": "settings.busy",
        "message": "Wait for the current reply before changing settings.",
        "retryable": False,
    }
    assert repository.load().revision == 0


def test_bridge_rejects_a_chat_other_than_the_active_chat() -> None:
    """Verify that bridge rejects a chat other than the active chat."""
    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "chat-1",
                "chat.stream",
                {"chatId": "chat_wrong", "message": "Hi"},
            ),
        ]
    )

    assert messages[-1]["error"] == {
        "code": "chat.not_active",
        "message": "chatId is not the active desktop Chat.",
        "retryable": False,
    }


def test_bridge_rejects_the_wrong_local_session_token_before_startup() -> None:
    """Verify that bridge rejects the wrong local session token before startup."""
    brain, messages = _run_bridge(
        lambda _chat_id: [_handshake_request(token="x" * 32)],
    )

    assert brain.stream_calls == []
    assert messages[0]["error"] == {
        "code": "protocol.unauthorized_local_peer",
        "message": "Desktop session token was rejected.",
        "retryable": False,
    }


def test_bridge_requires_handshake_before_initialization() -> None:
    """Verify that bridge requires handshake before initialization."""
    brain, messages = _run_bridge(
        lambda _chat_id: [_initialize_request()],
    )

    assert brain.stream_calls == []
    assert messages[0]["error"]["code"] == "protocol.not_authenticated"


def test_bridge_rejects_a_version_mismatch_without_starting_services() -> None:
    """Verify that bridge rejects a version mismatch without starting services."""
    request = _handshake_request()
    cast(JsonObject, request["protocol"])["version"] = 2
    brain, messages = _run_bridge(lambda _chat_id: [request])

    assert brain.stream_calls == []
    assert messages[0]["error"]["code"] == "protocol.version_mismatch"


def test_bridge_rejects_duplicate_request_ids() -> None:
    """Verify that bridge rejects duplicate request IDs."""
    _, messages = _run_bridge(
        lambda _chat_id: [
            _handshake_request(),
            _initialize_request(),
            _request(
                "duplicate-1",
                "request.cancel",
                {"requestId": "none"},
            ),
            _request(
                "duplicate-1",
                "request.cancel",
                {"requestId": "none"},
            ),
        ]
    )

    assert messages[-2]["error"]["code"] == "request.not_cancellable"
    assert messages[-1]["error"]["code"] == "protocol.duplicate_request"


def test_model_names_are_unique_and_keep_the_active_model_first() -> None:
    """Verify that model names are unique and keep the active model first."""
    models = _extract_model_names(
        {
            "models": [
                {"name": "other:latest"},
                {"model": "active:latest"},
                {"name": "other:latest"},
                {"name": ""},
            ]
        },
        "active:latest",
    )

    assert models == ("active:latest", "other:latest")


def test_protocol_output_is_ascii_safe_for_non_ascii_text(
    tmp_path: Path,
) -> None:
    """Verify that protocol output is ascii safe for non ascii text."""
    output_stream = StringIO()
    backend = DesktopBackend(
        settings_repository=_desktop_settings_repository(
            tmp_path / "global.json"
        ),
        output_stream=output_stream,
    )

    backend._emit(
        build_event(
            "test.message",
            {"message": "你好呀"},
            request_id=None,
        )
    )

    wire_message = output_stream.getvalue()
    wire_message.encode("ascii")
    assert json.loads(wire_message)["data"] == {"message": "你好呀"}


def test_protocol_streams_are_reconfigured_to_utf8() -> None:
    """Verify that protocol streams are reconfigured to UTF-8."""
    stream = TextIOWrapper(BytesIO(), encoding="cp1252")

    _configure_protocol_streams(stream)

    assert stream.encoding == "utf-8"


def test_input_frame_limit_counts_leading_json_whitespace(
    tmp_path: Path,
) -> None:
    """Verify that input frame limit counts leading JSON whitespace."""
    input_stream = StringIO(f"{' ' * 511}{{}}\n")
    output_stream = StringIO()

    with patch("desktop_backend.MAX_PROTOCOL_FRAME_BYTES", 512):
        DesktopBackend(
            settings_repository=_desktop_settings_repository(
                tmp_path / "global.json"
            ),
            input_stream=input_stream,
            output_stream=output_stream,
        ).run()

    response = cast(JsonObject, json.loads(output_stream.getvalue()))
    assert response["error"]["code"] == "protocol.frame_too_large"
