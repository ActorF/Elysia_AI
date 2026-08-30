"""Test the versioned Electron-to-Python bridge without starting Ollama."""

import json
from collections.abc import Callable, Generator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO, TextIOWrapper
from threading import Event
from typing import Any, cast
from unittest.mock import patch

from chats import (
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
from core import Brain, GenerationCancelledError
from desktop_backend import (
    SERVER_CAPABILITIES,
    SERVER_NAME,
    SERVER_VERSION,
    DesktopBackend,
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


class FakeBrain:
    """Provide only the Stage 5 methods used by the desktop bridge."""

    model_name = "test-model"

    def __init__(self) -> None:
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
        self.session_calls.append(("list_chats", include_archived))
        return tuple(
            chat.to_meta()
            for chat in self._chats.values()
            if include_archived or not chat.is_archived
        )

    def get_chat(self, chat_id: object) -> ChatSession:
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
        self.session_calls.append(("rename_chat", str(chat_id), title))
        renamed = replace(self._chats[chat_id], title=title)
        self.add_chat(renamed)
        return renamed

    def pin_chat(
        self,
        chat_id: ChatId,
        pinned: bool = True,
    ) -> ChatSessionMeta:
        self.session_calls.append(("pin_chat", str(chat_id), pinned))
        updated = replace(self._chats[chat_id], is_pinned=pinned)
        self.add_chat(updated)
        return updated.to_meta()

    def archive_chat(
        self,
        chat_id: ChatId,
        archived: bool = True,
    ) -> ChatSessionMeta:
        self.session_calls.append(("archive_chat", str(chat_id), archived))
        updated = replace(self._chats[chat_id], is_archived=archived)
        self.add_chat(updated)
        return updated.to_meta()

    def delete_chat(self, chat_id: ChatId) -> None:
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
        self.session_calls.append(("list_projects", include_archived))
        return tuple(
            project
            for project in self._projects.values()
            if include_archived or not project.is_archived
        )

    def get_project(self, project_id: ProjectId) -> Project:
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
        self.session_calls.append(("unbind_workspace", str(project_id)))
        updated = replace(
            self.get_project(project_id),
            workspace_binding=None,
        )
        self.add_project(updated)
        return updated

    def archive_project(self, project_id: ProjectId) -> Project:
        self.session_calls.append(("archive_project", str(project_id)))
        updated = replace(self.get_project(project_id), is_archived=True)
        self.add_project(updated)
        return updated

    def restore_project(self, project_id: ProjectId) -> Project:
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
        should_cancel: Callable[[], bool] | None = None,
        begin_commit: Callable[[], bool] | None = None,
    ) -> Generator[str, None, None]:
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


def _request(
    request_id: str,
    method: ProtocolMethod,
    params: JsonObject,
) -> JsonObject:
    """Return a JSON-compatible request validated by the real contract."""

    return cast(JsonObject, build_request(request_id, method, params))


def _handshake_request(*, token: str = SESSION_TOKEN) -> JsonObject:
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


def _initialize_request() -> JsonObject:
    return _request("initialize-1", "initialize", {})


def _run_bridge(
    build_lines: Callable[[str], list[JsonObject]],
    *,
    expected_session_token: str = SESSION_TOKEN,
    fake_brain: FakeBrain | None = None,
) -> tuple[FakeBrain, list[JsonObject]]:
    active_brain = fake_brain if fake_brain is not None else FakeBrain()
    lines = build_lines(str(active_brain.chat.chat_id))
    input_stream = StringIO(
        "".join(f"{json.dumps(line)}\n" for line in lines)
    )
    output_stream = StringIO()

    DesktopBackend(
        brain_factory=lambda: cast(Brain, active_brain),
        model_loader=lambda: ("test-model", "second-model"),
        settings_validator=lambda: None,
        input_stream=input_stream,
        output_stream=output_stream,
        expected_session_token=expected_session_token,
    ).run()

    messages = [
        cast(JsonObject, json.loads(line))
        for line in output_stream.getvalue().splitlines()
    ]
    return active_brain, messages


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


def test_bridge_retries_the_persisted_tail_with_stable_message_ids() -> None:
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
    class CancellableBrain(FakeBrain):
        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            del begin_commit
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


def test_chat_list_does_not_block_the_cancel_request_reader() -> None:
    generation_started = Event()

    class BlockingBrain(FakeBrain):
        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            del begin_commit
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


def test_cancel_is_rejected_after_generation_claims_commit() -> None:
    commit_claimed = Event()
    release_commit = Event()

    class CommittingBrain(FakeBrain):
        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            del should_cancel
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


def test_background_completion_does_not_reactivate_a_chat_after_switch() -> None:
    generation_started = Event()
    release_generation = Event()

    class SwitchingBrain(FakeBrain):
        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
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


def test_background_completion_does_not_replace_a_new_active_chat() -> None:
    generation_started = Event()
    release_generation = Event()

    class CreatingBrain(FakeBrain):
        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
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
    class RefreshFailingBrain(FakeBrain):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next_refresh = False

        def stream_chat(
            self,
            chat_id: object,
            message: str,
            *,
            should_cancel: Callable[[], bool] | None = None,
            begin_commit: Callable[[], bool] | None = None,
        ) -> Generator[str, None, None]:
            yield from super().stream_chat(
                chat_id,
                message,
                should_cancel=should_cancel,
                begin_commit=begin_commit,
            )
            self.fail_next_refresh = True

        def get_chat(self, chat_id: object) -> ChatSession:
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
    class FailingProjectBrain(FakeBrain):
        def update_project(
            self,
            project_id: ProjectId,
            *,
            name: str,
            custom_instructions: str | None,
        ) -> Project:
            del name, custom_instructions
            raise ProjectArchivedError(
                f"Archived Project is read-only: {project_id}."
            )

        def move_chat(
            self,
            chat_id: ChatId,
            project_id: ProjectId | None,
        ) -> ChatSession:
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


def test_bridge_rejects_a_chat_other_than_the_active_chat() -> None:
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
    brain, messages = _run_bridge(
        lambda _chat_id: [_initialize_request()],
    )

    assert brain.stream_calls == []
    assert messages[0]["error"]["code"] == "protocol.not_authenticated"


def test_bridge_rejects_a_version_mismatch_without_starting_services() -> None:
    request = _handshake_request()
    cast(JsonObject, request["protocol"])["version"] = 2
    brain, messages = _run_bridge(lambda _chat_id: [request])

    assert brain.stream_calls == []
    assert messages[0]["error"]["code"] == "protocol.version_mismatch"


def test_bridge_rejects_duplicate_request_ids() -> None:
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


def test_protocol_output_is_ascii_safe_for_non_ascii_text() -> None:
    output_stream = StringIO()
    backend = DesktopBackend(output_stream=output_stream)

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
    stream = TextIOWrapper(BytesIO(), encoding="cp1252")

    _configure_protocol_streams(stream)

    assert stream.encoding == "utf-8"


def test_input_frame_limit_counts_leading_json_whitespace() -> None:
    input_stream = StringIO(f"{' ' * 511}{{}}\n")
    output_stream = StringIO()

    with patch("desktop_backend.MAX_PROTOCOL_FRAME_BYTES", 512):
        DesktopBackend(
            input_stream=input_stream,
            output_stream=output_stream,
        ).run()

    response = cast(JsonObject, json.loads(output_stream.getvalue()))
    assert response["error"]["code"] == "protocol.frame_too_large"
