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
from collections.abc import Callable
from io import TextIOWrapper
from typing import Any, TextIO, cast
from urllib.request import Request, urlopen

from chats import ChatId, ChatSession
from config.settings import SETTINGS
from core import Brain
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
    "chat.stream",
    "stream",
    "progress",
    "event",
)
MAX_REQUEST_ID_LENGTH = 128
MAX_ERROR_MESSAGE_LENGTH = 4096
MAX_RECENT_REQUEST_IDS = 4096


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
        self._models: tuple[str, ...] = ()
        self._seen_request_ids: set[str] = set()
        self._request_id_order: deque[str] = deque()

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
                self._emit_response(request_id, {"stopped": True})
                return False
            elif not self._authenticated:
                raise ProtocolValidationError(
                    "protocol.not_authenticated",
                    "Backend handshake must complete before this request.",
                )
            elif method == "initialize":
                self._initialize(request_id)
            elif self._brain is None or self._active_chat is None:
                raise ProtocolValidationError(
                    "protocol.not_initialized",
                    "Backend must be initialized before this request.",
                )
            elif method == "chat.stream":
                self._stream_chat(request_id, params)
            elif method == "request.cancel":
                raise ProtocolValidationError(
                    "request.not_cancellable",
                    "No cancellable Backend request is active.",
                )
            elif method == "permission.respond":
                raise ProtocolValidationError(
                    "permission.not_found",
                    "No Backend permission request is pending.",
                )
        except ProtocolValidationError as error:
            self._emit_error(request_id, error.code, str(error))
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
                    if method == "chat.stream"
                    else "backend.request_failed"
                ),
                (
                    "Chat request failed in the local Backend."
                    if method == "chat.stream"
                    else "Desktop Backend request failed."
                ),
                retryable=method == "chat.stream",
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

        if self._brain is None or self._active_chat is None:
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
            self._brain = self._brain_factory()
            self._active_chat = self._resolve_active_chat(self._brain)
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
                "modelName": self._brain.model_name,
                "models": list(self._models),
                "chatId": str(self._active_chat.chat_id),
                "chatTitle": self._active_chat.title,
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

    def _stream_chat(
        self,
        request_id: str,
        params: JsonObject,
    ) -> None:
        """Stream one real reply through the active Stage 5 Chat."""

        if self._brain is None or self._active_chat is None:
            raise RuntimeError(
                "Backend is not initialized."
            )

        raw_chat_id = params.get("chatId")
        raw_message = params.get("message")

        if not isinstance(raw_chat_id, str) or not raw_chat_id:
            raise ProtocolValidationError(
                "protocol.invalid_params",
                "chatId must be a non-empty string."
            )

        if raw_chat_id != str(self._active_chat.chat_id):
            raise ProtocolValidationError(
                "chat.not_active",
                "chatId is not the active desktop Chat."
            )

        if not isinstance(raw_message, str) or not raw_message.strip():
            raise ProtocolValidationError(
                "protocol.invalid_params",
                "message must be a non-empty string."
            )

        chat_id = ChatId(raw_chat_id)
        reply_chunks: list[str] = []
        reply_length = 0
        sequence = 0

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

        for chunk in self._brain.stream_chat(
            chat_id,
            raw_message,
        ):
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
                    request_id,
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
        self._emit(
            build_stream_chunk(
                request_id,
                sequence,
                "",
                done=True,
            )
        )
        self._emit_progress(
            request_id,
            "chat.generate",
            1,
            total=1,
            message=None,
        )
        self._emit_event(
            "chat.completed",
            request_id=request_id,
            data={"chatId": raw_chat_id},
        )
        self._emit_response(
            request_id,
            {
                "chatId": raw_chat_id,
                "reply": reply,
            },
        )

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
