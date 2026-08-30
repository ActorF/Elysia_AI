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
from dataclasses import dataclass, field
from enum import Enum
from io import TextIOWrapper
from threading import Event, Lock, RLock, Thread
from typing import Any, TextIO, cast
from urllib.request import Request, urlopen

from chats import (
    ChatId,
    ChatMessageId,
    ChatNotFoundError,
    ChatSession,
    ChatSessionMeta,
    ConversationMode,
    ProjectId,
)
from config.settings import SETTINGS
from core import (
    Brain,
    ChatBusyError,
    ChatRetryTargetError,
    GenerationCancelledError,
)
from projects import (
    Project,
    ProjectArchivedError,
    ProjectChatBusyError,
    ProjectNotFoundError,
)
from desktop_protocol import (
    MAX_MESSAGE_LENGTH,
    MAX_PROTOCOL_FRAME_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    ClientRequest,
    ProtocolValidationError,
    ServerMessage,
    build_error_response,
    build_event,
    build_progress,
    build_stream_chunk,
    build_success_response,
    parse_client_request,
)
from start import create_brain, validate_settings

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]
BrainFactory = Callable[[], Brain]
ModelLoader = Callable[[], tuple[str, ...]]
SettingsValidator = Callable[[], None]

SERVER_NAME = "elysia-python"
SERVER_VERSION = "0.1.0"
SERVER_CAPABILITIES = (
    "chat.sessions",
    "chat.stream",
    "chat.retry",
    "request.cancel",
    "project.management",
    "stream",
    "progress",
    "event",
)
MAX_REQUEST_ID_LENGTH = 128
MAX_ERROR_MESSAGE_LENGTH = 4096
MAX_RECENT_REQUEST_IDS = 4096
GENERATION_SHUTDOWN_TIMEOUT_SECONDS = 2.0


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
    state: _GenerationState = _GenerationState.RUNNING
    done: Event = field(default_factory=Event)
    thread: Thread | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def request_cancel(self) -> bool:
        """Request cancellation only while commit can still be prevented."""

        with self._lock:
            if self.state is _GenerationState.RUNNING:
                self.state = _GenerationState.CANCEL_REQUESTED
                return True
            return self.state is _GenerationState.CANCEL_REQUESTED

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

    def finish(self) -> None:
        """Publish terminal state to shutdown and serialized readers."""

        with self._lock:
            self.state = _GenerationState.FINISHED
        self.done.set()

    def state_snapshot(self) -> _GenerationState:
        """Read the lifecycle state without exposing the task lock."""

        with self._lock:
            return self.state


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


def discover_ollama_models() -> tuple[str, ...]:
    """List locally installed Ollama models without reading model files."""

    endpoint = f"{SETTINGS.ollama_host.rstrip('/')}/api/tags"
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

    return _extract_model_names(payload, SETTINGS.model_name)


class DesktopBackend:
    """Translate the desktop protocol into existing Brain operations."""

    def __init__(
        self,
        *,
        brain_factory: BrainFactory = create_brain,
        model_loader: ModelLoader = discover_ollama_models,
        settings_validator: SettingsValidator = validate_settings,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        expected_session_token: str | None = None,
    ) -> None:
        """Store injected boundaries so the protocol can be tested offline."""

        self._brain_factory = brain_factory
        self._model_loader = model_loader
        self._settings_validator = settings_validator
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
        self._generation_task: _GenerationTask | None = None

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
                self._prepare_generation_shutdown()
                self._emit_response(request_id, {"stopped": True})
                return False
            elif not self._authenticated:
                raise ProtocolValidationError(
                    "protocol.not_authenticated",
                    "Backend handshake must complete before this request.",
                )
            elif method == "initialize":
                self._initialize(request_id)
            elif self._brain is None or self._active_chat_snapshot() is None:
                raise ProtocolValidationError(
                    "protocol.not_initialized",
                    "Backend must be initialized before this request.",
                )
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
            elif method == "request.cancel":
                self._cancel_request(request_id, params)
            elif method == "permission.respond":
                raise ProtocolValidationError(
                    "permission.not_found",
                    "No Backend permission request is pending.",
                )
        except ProtocolValidationError as error:
            self._emit_error(request_id, error.code, str(error))
        except ChatBusyError as error:
            self._emit_error(request_id, "chat.busy", str(error))
        except ChatRetryTargetError as error:
            self._emit_error(request_id, "chat.retry_target", str(error))
        except ChatNotFoundError as error:
            self._emit_error(request_id, "chat.not_found", str(error))
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
        except Exception:
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
                    else "backend.request_failed"
                ),
                (
                    "Chat request failed in the local Backend."
                    if method in {"chat.stream", "chat.retry"}
                    else "Desktop Backend request failed."
                ),
                retryable=method in {"chat.stream", "chat.retry"},
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
            self._settings_validator()
            self._emit_progress(
                request_id,
                "backend.initialize",
                1,
                total=3,
                message="Discovering local models",
            )
            self._models = self._model_loader()
            self._emit_progress(
                request_id,
                "backend.initialize",
                2,
                total=3,
                message="Loading Chat services",
            )
            brain = self._brain_factory()
            active_chat = self._resolve_active_chat(brain)
            self._brain = brain
            self._set_active_chat(active_chat)
            self._active_project_id = active_chat.project_id
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
        result["messages"] = [
            {
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
            for message in chat.messages
        ]
        return result

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
        if self._brain is None or active_chat is None:
            raise RuntimeError("Backend is not initialized.")

        chat_id = ChatId(cast(str, params["chatId"]))
        archived = cast(bool, params["archived"])
        was_active = chat_id == active_chat.chat_id
        self._brain.archive_chat(chat_id, archived)

        if was_active and archived:
            self._set_active_chat(self._resolve_active_chat(self._brain))

        self._emit_session_response(request_id, include_archived=True)

    def _delete_chat(self, request_id: str, params: JsonObject) -> None:
        """Delete one Chat and replace the active Chat when necessary."""

        active_chat = self._active_chat_snapshot()
        if self._brain is None or active_chat is None:
            raise RuntimeError("Backend is not initialized.")

        chat_id = ChatId(cast(str, params["chatId"]))
        was_active = chat_id == active_chat.chat_id
        self._brain.delete_chat(chat_id)

        if was_active:
            self._set_active_chat(self._resolve_active_chat(self._brain))

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

    def _start_chat_stream(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Start one cancellable new-turn generation worker."""

        raw_message = cast(str, params["message"])
        self._start_generation(
            request_id,
            params,
            method="chat.stream",
            run=lambda brain, task: brain.stream_chat(
                task.chat_id,
                raw_message,
                should_cancel=task.should_cancel,
                begin_commit=task.begin_commit,
            ),
        )

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
        self._start_generation(
            request_id,
            params,
            method="chat.retry",
            run=lambda brain, task: brain.stream_retry(
                task.chat_id,
                user_message_id,
                assistant_message_id,
                message,
                should_cancel=task.should_cancel,
                begin_commit=task.begin_commit,
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

        if self._brain is None:
            raise RuntimeError("Backend is not initialized.")

        raw_chat_id = cast(str, params["chatId"])
        with self._state_lock:
            active_chat = self._active_chat
            existing = self._generation_task
            if existing is not None and not existing.done.is_set():
                raise ChatBusyError(
                    "Another desktop Chat generation is already active."
                )
            if active_chat is None:
                raise RuntimeError("Backend is not initialized.")
            if raw_chat_id != str(active_chat.chat_id):
                raise ProtocolValidationError(
                    "chat.not_active",
                    "chatId is not the active desktop Chat.",
                )

            task = _GenerationTask(
                request_id=request_id,
                chat_id=ChatId(raw_chat_id),
                method=method,
            )
            self._generation_task = task

        self._emit_event(
            "chat.started",
            request_id=request_id,
            data={"chatId": raw_chat_id},
        )
        self._emit_progress(
            request_id,
            "chat.generate",
            0,
            total=None,
            message="Generating reply",
        )

        brain = self._brain
        try:
            worker = Thread(
                target=self._run_generation,
                args=(brain, task, run),
                name=f"elysia-{task.method}-{request_id}",
                daemon=True,
            )
            task.thread = worker
            worker.start()
        except Exception:
            task.finish()
            with self._state_lock:
                if self._generation_task is task:
                    self._generation_task = None
            raise

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

        try:
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
                sequence += 1

            reply = "".join(reply_chunks)
            if not reply:
                raise ProtocolValidationError(
                    "chat.empty_reply",
                    "The local model returned an empty reply.",
                )

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
            self._emit_error(
                task.request_id,
                "request.cancelled",
                "Chat generation was cancelled.",
            )
        except ProtocolValidationError as error:
            self._emit_error(task.request_id, error.code, str(error))
        except ChatRetryTargetError as error:
            self._emit_error(
                task.request_id,
                "chat.retry_target",
                str(error),
            )
        except ChatBusyError as error:
            self._emit_error(task.request_id, "chat.busy", str(error))
        except ChatNotFoundError as error:
            self._emit_error(task.request_id, "chat.not_found", str(error))
        except Exception:
            logger.exception(
                "Desktop generation failed: method=%s request_id=%s.",
                task.method,
                task.request_id,
            )
            self._emit_error(
                task.request_id,
                "chat.failed",
                "Chat request failed in the local Backend.",
                retryable=True,
            )
        finally:
            task.finish()
            with self._state_lock:
                if self._generation_task is task:
                    self._generation_task = None

    def _cancel_request(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Cancel one matching generation before it claims commit."""

        target_request_id = cast(str, params["requestId"])
        with self._state_lock:
            task = self._generation_task
            stopped = (
                task is not None
                and task.request_id == target_request_id
                and task.request_cancel()
            )
        if not stopped:
            raise ProtocolValidationError(
                "request.not_cancellable",
                "No matching cancellable Backend request is active.",
            )
        self._emit_response(request_id, {"stopped": True})

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
        event: str,
        *,
        request_id: str,
        data: JsonObject,
    ) -> None:
        """Write one streaming event linked to its request."""

        self._emit(build_event(event, data, request_id=request_id))

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
    DesktopBackend().run()


if __name__ == "__main__":
    main()
