"""Test the versioned Electron-to-Python bridge without starting Ollama."""

import json
from collections.abc import Callable, Generator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO, TextIOWrapper
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
from core import Brain
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
    ) -> Generator[str, None, None]:
        self.stream_calls.append((str(chat_id), message))
        yield "你好"
        yield "呀"
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
            _request(
                "list-after-chat",
                "chat.list",
                {"includeArchived": False},
            ),
            _request("shutdown-1", "shutdown", {}),
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
    refreshed = _success_result(messages, "list-after-chat")
    assert set(refreshed) == {"activeChat", "chats"}
    assert refreshed["activeChat"]["messageCount"] == 2
    assert [
        message["content"]
        for message in refreshed["activeChat"]["messages"]
    ] == ["你好呀", "你好呀"]
    assert messages[-1]["result"] == {"stopped": True}
    assert "chat.sessions" in SERVER_CAPABILITIES


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
