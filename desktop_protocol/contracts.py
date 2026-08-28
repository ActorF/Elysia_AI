"""Define and validate version 1 of the local desktop wire protocol.

The protocol deliberately uses plain JSON values so Python and TypeScript can
validate the same fixtures without either runtime importing the other.  Every
wire message carries the protocol name and version; untrusted or stale peers
are rejected before application services are invoked.
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal, TypedDict, cast

PROTOCOL_NAME: Final = "elysia.desktop"
PROTOCOL_VERSION: Final = 1
MAX_IDENTIFIER_LENGTH: Final = 128
MAX_METHOD_LENGTH: Final = 96
MAX_MESSAGE_LENGTH: Final = 1_000_000
MAX_PROJECT_NAME_LENGTH: Final = 200
MAX_WORKSPACE_PATH_LENGTH: Final = 32_767
MAX_PROTOCOL_FRAME_BYTES: Final = 16_777_216
MIN_SESSION_TOKEN_LENGTH: Final = 32
MAX_SESSION_TOKEN_LENGTH: Final = 512
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

_PROTOCOL_BLANK_CHARACTERS: Final = frozenset(
    chr(code_point)
    for start, end in (
        (0x0009, 0x000D),
        (0x0020, 0x0020),
        (0x0085, 0x0085),
        (0x00A0, 0x00A0),
        (0x1680, 0x1680),
        (0x2000, 0x200A),
        (0x2028, 0x2029),
        (0x202F, 0x202F),
        (0x205F, 0x205F),
        (0x3000, 0x3000),
        (0xFEFF, 0xFEFF),
    )
    for code_point in range(start, end + 1)
)
_PROJECT_ID_PATTERN: Final = re.compile(r"^project_[A-Za-z0-9_-]+$")

ProtocolMethod = Literal[
    "handshake",
    "initialize",
    "chat.stream",
    "chat.list",
    "chat.create",
    "chat.open",
    "chat.rename",
    "chat.pin",
    "chat.archive",
    "chat.delete",
    "project.list",
    "project.create",
    "project.open",
    "project.update",
    "project.workspace",
    "project.archive",
    "project.chat.move",
    "request.cancel",
    "permission.respond",
    "shutdown",
]
SUPPORTED_METHODS: Final[tuple[ProtocolMethod, ...]] = (
    "handshake",
    "initialize",
    "chat.stream",
    "chat.list",
    "chat.create",
    "chat.open",
    "chat.rename",
    "chat.pin",
    "chat.archive",
    "chat.delete",
    "project.list",
    "project.create",
    "project.open",
    "project.update",
    "project.workspace",
    "project.archive",
    "project.chat.move",
    "request.cancel",
    "permission.respond",
    "shutdown",
)

JsonObject = dict[str, Any]

_CHAT_SUMMARY_FIELDS: Final[frozenset[str]] = frozenset(
    {
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
)


class ProtocolDescriptor(TypedDict):
    """Identify one incompatible wire-protocol generation."""

    name: str
    version: int


class ClientDescriptor(TypedDict):
    """Describe the Electron peer during the version handshake."""

    name: str
    version: str


class HandshakeParams(TypedDict):
    """Authenticate and describe the local Electron peer."""

    client: ClientDescriptor
    sessionToken: str


class ChatStreamParams(TypedDict):
    """Request one streamed Chat reply."""

    chatId: str
    message: str


class ChatListParams(TypedDict):
    """Choose whether archived Chats appear in the sidebar."""

    includeArchived: bool


class ChatCreateParams(TypedDict):
    """Create one Chat for the Backend's active model."""

    title: str
    mode: Literal["chat", "work"]


class ChatIdParams(TypedDict):
    """Identify one Chat for open and delete operations."""

    chatId: str


class ChatRenameParams(TypedDict):
    """Rename one existing Chat."""

    chatId: str
    title: str


class ChatPinParams(TypedDict):
    """Set one Chat's pinned state explicitly."""

    chatId: str
    pinned: bool


class ChatArchiveParams(TypedDict):
    """Set one Chat's archived state explicitly."""

    chatId: str
    archived: bool


class ProjectCreateParams(TypedDict):
    """Create one first-class Project with optional instructions."""

    name: str
    customInstructions: str | None


class ProjectIdParams(TypedDict):
    """Identify one Project for selection."""

    projectId: str


class ProjectUpdateParams(TypedDict):
    """Replace the editable text fields of one Project."""

    projectId: str
    name: str
    customInstructions: str | None


class ProjectWorkspaceParams(TypedDict):
    """Bind, replace, or clear one Project workspace path."""

    projectId: str
    workspacePath: str | None


class ProjectArchiveParams(TypedDict):
    """Set one Project's archived state explicitly."""

    projectId: str
    archived: bool


class ProjectChatMoveParams(TypedDict):
    """Set one Chat's Project relationship or clear it."""

    chatId: str
    projectId: str | None


class CancelParams(TypedDict, total=False):
    """Identify a request that a future cancellable operation may stop."""

    requestId: str
    reason: str


class PermissionResponseParams(TypedDict):
    """Return a renderer decision for a permission prompt."""

    permissionId: str
    granted: bool


class ClientRequest(TypedDict):
    """Represent one request sent from Electron to Python."""

    type: Literal["request"]
    protocol: ProtocolDescriptor
    id: str
    method: ProtocolMethod
    params: JsonObject


class ProtocolError(TypedDict):
    """Expose a stable bounded failure without a traceback."""

    code: str
    message: str
    retryable: bool


class SuccessResponse(TypedDict):
    """Complete one request successfully."""

    type: Literal["response"]
    protocol: ProtocolDescriptor
    id: str
    ok: Literal[True]
    result: JsonObject


class ErrorResponse(TypedDict):
    """Complete or reject one request with a typed error."""

    type: Literal["response"]
    protocol: ProtocolDescriptor
    id: str | None
    ok: Literal[False]
    error: ProtocolError


class StreamChunkMessage(TypedDict):
    """Carry one ordered chunk from a named response stream."""

    type: Literal["stream"]
    protocol: ProtocolDescriptor
    requestId: str
    stream: Literal["chat.reply"]
    sequence: int
    chunk: str
    done: bool


class ProgressMessage(TypedDict):
    """Report bounded progress for a long-running request."""

    type: Literal["progress"]
    protocol: ProtocolDescriptor
    requestId: str
    operation: str
    completed: int
    total: int | None
    message: str | None


class PermissionMessage(TypedDict):
    """Ask Electron to obtain one explicit local permission decision."""

    type: Literal["permission"]
    protocol: ProtocolDescriptor
    requestId: str | None
    permissionId: str
    capability: str
    reason: str
    scopes: list[str]


class EventMessage(TypedDict):
    """Publish a typed asynchronous Backend event."""

    type: Literal["event"]
    protocol: ProtocolDescriptor
    event: str
    requestId: str | None
    data: JsonObject


class ChatAttachment(TypedDict):
    """Expose attachment metadata without file bytes or private paths."""

    attachmentId: str
    fileName: str
    mediaType: str
    sizeBytes: int


class ChatSessionMessage(TypedDict):
    """Expose one persisted message in a Chat detail response."""

    messageId: str
    role: Literal["system", "user", "assistant"]
    content: str
    createdAt: str
    attachments: list[ChatAttachment]


class ChatSessionSummary(TypedDict):
    """Expose bounded sidebar metadata for one Chat."""

    chatId: str
    title: str
    mode: Literal["chat", "work"]
    createdAt: str
    updatedAt: str
    messageCount: int
    projectId: str | None
    modelName: str
    pinned: bool
    archived: bool


class ChatDetail(ChatSessionSummary):
    """Expose one complete Chat together with its persisted messages."""

    messages: list[ChatSessionMessage]


class ChatStateResult(TypedDict):
    """Return one atomic active-Chat detail and matching sidebar list."""

    activeChat: ChatDetail
    chats: list[ChatSessionSummary]


class ProjectSummary(TypedDict):
    """Expose one complete lightweight Project aggregate."""

    projectId: str
    name: str
    createdAt: str
    updatedAt: str
    customInstructions: str | None
    workspacePath: str | None
    archived: bool
    chatCount: int


class ProjectStateResult(TypedDict):
    """Return canonical Project selection, Projects, and Chat state."""

    activeProject: ProjectSummary | None
    projects: list[ProjectSummary]
    chatState: ChatStateResult


ServerMessage = (
    SuccessResponse
    | ErrorResponse
    | StreamChunkMessage
    | ProgressMessage
    | PermissionMessage
    | EventMessage
)


class ProtocolValidationError(ValueError):
    """Report one stable protocol-validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _descriptor() -> ProtocolDescriptor:
    return {
        "name": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
    }


def _as_object(value: object, context: str) -> JsonObject:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} must be a JSON object.",
        )
    return cast(JsonObject, value)


def _require_fields(
    value: JsonObject,
    required: set[str],
    context: str,
    *,
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    actual = set(value)
    if not required.issubset(actual) or not actual.issubset(allowed):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} has invalid fields.",
        )


def _require_string(
    value: JsonObject,
    key: str,
    context: str,
    *,
    maximum: int = MAX_MESSAGE_LENGTH,
    minimum: int = 1,
) -> str:
    raw = value.get(key)
    if (
        not isinstance(raw, str)
        or len(raw) < minimum
        or len(raw) > maximum
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must be a string with length "
            f"{minimum}..{maximum}.",
        )
    return raw


def _require_identifier(
    value: JsonObject,
    key: str,
    context: str,
) -> str:
    return _require_string(
        value,
        key,
        context,
        maximum=MAX_IDENTIFIER_LENGTH,
    )


def _require_project_identifier(
    value: JsonObject,
    key: str,
    context: str,
) -> str:
    raw = _require_identifier(value, key, context)
    if _PROJECT_ID_PATTERN.fullmatch(raw) is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must use the project_<id> format.",
        )
    return raw


def _has_non_blank_character(value: str) -> bool:
    return any(
        character not in _PROTOCOL_BLANK_CHARACTERS
        for character in value
    )


def _require_non_blank_string(
    value: JsonObject,
    key: str,
    context: str,
    *,
    maximum: int,
    error_code: str,
) -> str:
    raw = _require_string(value, key, context, maximum=maximum)
    if not _has_non_blank_character(raw):
        raise ProtocolValidationError(
            error_code,
            f"{context}.{key} cannot be blank.",
        )
    return raw


def _require_nullable_non_blank_string(
    value: JsonObject,
    key: str,
    context: str,
    *,
    maximum: int,
    error_code: str,
) -> str | None:
    if value.get(key) is None:
        return None
    return _require_non_blank_string(
        value,
        key,
        context,
        maximum=maximum,
        error_code=error_code,
    )


def _require_integer(
    value: JsonObject,
    key: str,
    context: str,
) -> int:
    raw = value.get(key)
    if isinstance(raw, bool):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must be a safe JSON integer.",
        )
    elif isinstance(raw, int):
        normalized = raw
    elif (
        isinstance(raw, float)
        and raw.is_integer()
        and abs(raw) <= MAX_SAFE_INTEGER
    ):
        normalized = int(raw)
    else:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must be a safe JSON integer.",
        )
    if abs(normalized) > MAX_SAFE_INTEGER:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must be a safe JSON integer.",
        )
    value[key] = normalized
    return normalized


def _require_boolean(
    value: JsonObject,
    key: str,
    context: str,
) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must be a boolean.",
        )
    return raw


def _validate_descriptor(value: object) -> ProtocolDescriptor:
    descriptor = _as_object(value, "protocol")
    _require_fields(descriptor, {"name", "version"}, "protocol")
    name = _require_string(
        descriptor,
        "name",
        "protocol",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    version = _require_integer(descriptor, "version", "protocol")
    if name != PROTOCOL_NAME:
        raise ProtocolValidationError(
            "protocol.name_mismatch",
            f"Unsupported protocol name: {name}.",
        )
    if version != PROTOCOL_VERSION:
        raise ProtocolValidationError(
            "protocol.version_mismatch",
            f"Unsupported protocol version: {version}.",
        )
    return cast(ProtocolDescriptor, descriptor)


def _validate_handshake_params(params: JsonObject) -> None:
    _require_fields(params, {"client", "sessionToken"}, "handshake params")
    client = _as_object(params["client"], "handshake params.client")
    _require_fields(client, {"name", "version"}, "handshake params.client")
    _require_string(
        client,
        "name",
        "handshake params.client",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    _require_string(
        client,
        "version",
        "handshake params.client",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    _require_string(
        params,
        "sessionToken",
        "handshake params",
        minimum=MIN_SESSION_TOKEN_LENGTH,
        maximum=MAX_SESSION_TOKEN_LENGTH,
    )


def _validate_chat_params(params: JsonObject) -> None:
    _require_fields(params, {"chatId", "message"}, "chat.stream params")
    _require_identifier(params, "chatId", "chat.stream params")
    message = _require_string(params, "message", "chat.stream params")
    if not any(
        character not in _PROTOCOL_BLANK_CHARACTERS
        for character in message
    ):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            "chat.stream params.message cannot be blank.",
        )


def _validate_chat_list_params(params: JsonObject) -> None:
    _require_fields(params, {"includeArchived"}, "chat.list params")
    _require_boolean(params, "includeArchived", "chat.list params")


def _validate_chat_title(
    params: JsonObject,
    *,
    context: str,
) -> None:
    title = _require_string(params, "title", context)
    if not any(
        character not in _PROTOCOL_BLANK_CHARACTERS
        for character in title
    ):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.title cannot be blank.",
        )


def _validate_chat_mode(params: JsonObject, *, context: str) -> None:
    mode = _require_string(
        params,
        "mode",
        context,
        maximum=4,
    )
    if mode not in {"chat", "work"}:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.mode must be 'chat' or 'work'.",
        )


def _validate_chat_create_params(params: JsonObject) -> None:
    context = "chat.create params"
    _require_fields(params, {"title", "mode"}, context)
    _validate_chat_title(params, context=context)
    _validate_chat_mode(params, context=context)


def _validate_chat_id_params(params: JsonObject, *, method: str) -> None:
    context = f"{method} params"
    _require_fields(params, {"chatId"}, context)
    _require_identifier(params, "chatId", context)


def _validate_chat_rename_params(params: JsonObject) -> None:
    context = "chat.rename params"
    _require_fields(params, {"chatId", "title"}, context)
    _require_identifier(params, "chatId", context)
    _validate_chat_title(params, context=context)


def _validate_chat_pin_params(params: JsonObject) -> None:
    context = "chat.pin params"
    _require_fields(params, {"chatId", "pinned"}, context)
    _require_identifier(params, "chatId", context)
    _require_boolean(params, "pinned", context)


def _validate_chat_archive_params(params: JsonObject) -> None:
    context = "chat.archive params"
    _require_fields(params, {"chatId", "archived"}, context)
    _require_identifier(params, "chatId", context)
    _require_boolean(params, "archived", context)


def _validate_project_create_params(params: JsonObject) -> None:
    context = "project.create params"
    _require_fields(params, {"name", "customInstructions"}, context)
    _require_non_blank_string(
        params,
        "name",
        context,
        maximum=MAX_PROJECT_NAME_LENGTH,
        error_code="protocol.invalid_params",
    )
    _require_nullable_non_blank_string(
        params,
        "customInstructions",
        context,
        maximum=MAX_MESSAGE_LENGTH,
        error_code="protocol.invalid_params",
    )


def _validate_project_id_params(
    params: JsonObject,
    *,
    method: str,
) -> None:
    context = f"{method} params"
    _require_fields(params, {"projectId"}, context)
    _require_project_identifier(params, "projectId", context)


def _validate_project_update_params(params: JsonObject) -> None:
    context = "project.update params"
    _require_fields(
        params,
        {"projectId", "name", "customInstructions"},
        context,
    )
    _require_project_identifier(params, "projectId", context)
    _require_non_blank_string(
        params,
        "name",
        context,
        maximum=MAX_PROJECT_NAME_LENGTH,
        error_code="protocol.invalid_params",
    )
    _require_nullable_non_blank_string(
        params,
        "customInstructions",
        context,
        maximum=MAX_MESSAGE_LENGTH,
        error_code="protocol.invalid_params",
    )


def _validate_project_workspace_params(params: JsonObject) -> None:
    context = "project.workspace params"
    _require_fields(params, {"projectId", "workspacePath"}, context)
    _require_project_identifier(params, "projectId", context)
    _require_nullable_non_blank_string(
        params,
        "workspacePath",
        context,
        maximum=MAX_WORKSPACE_PATH_LENGTH,
        error_code="protocol.invalid_params",
    )


def _validate_project_archive_params(params: JsonObject) -> None:
    context = "project.archive params"
    _require_fields(params, {"projectId", "archived"}, context)
    _require_project_identifier(params, "projectId", context)
    _require_boolean(params, "archived", context)


def _validate_project_chat_move_params(params: JsonObject) -> None:
    context = "project.chat.move params"
    _require_fields(params, {"chatId", "projectId"}, context)
    _require_identifier(params, "chatId", context)
    if params.get("projectId") is not None:
        _require_project_identifier(params, "projectId", context)


def _validate_cancel_params(params: JsonObject) -> None:
    _require_fields(
        params,
        {"requestId"},
        "request.cancel params",
        optional={"reason"},
    )
    _require_identifier(params, "requestId", "request.cancel params")
    if "reason" in params:
        _require_string(
            params,
            "reason",
            "request.cancel params",
            maximum=512,
        )


def _validate_permission_response_params(params: JsonObject) -> None:
    _require_fields(
        params,
        {"permissionId", "granted"},
        "permission.respond params",
    )
    _require_identifier(
        params,
        "permissionId",
        "permission.respond params",
    )
    _require_boolean(params, "granted", "permission.respond params")


def parse_client_request(value: object) -> ClientRequest:
    """Validate and return one Electron-to-Python request."""

    request = _as_object(value, "request")
    _require_fields(
        request,
        {"type", "protocol", "id", "method", "params"},
        "request",
    )
    if request.get("type") != "request":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "request.type must be 'request'.",
        )
    _validate_descriptor(request["protocol"])
    _require_identifier(request, "id", "request")
    method = _require_string(
        request,
        "method",
        "request",
        maximum=MAX_METHOD_LENGTH,
    )
    if method not in SUPPORTED_METHODS:
        raise ProtocolValidationError(
            "protocol.method_not_found",
            f"Unknown request method: {method}.",
        )
    params = _as_object(request["params"], "request.params")
    if method == "handshake":
        _validate_handshake_params(params)
    elif method == "initialize":
        _require_fields(params, set(), "initialize params")
    elif method == "chat.stream":
        _validate_chat_params(params)
    elif method == "chat.list":
        _validate_chat_list_params(params)
    elif method == "chat.create":
        _validate_chat_create_params(params)
    elif method in {"chat.open", "chat.delete"}:
        _validate_chat_id_params(params, method=method)
    elif method == "chat.rename":
        _validate_chat_rename_params(params)
    elif method == "chat.pin":
        _validate_chat_pin_params(params)
    elif method == "chat.archive":
        _validate_chat_archive_params(params)
    elif method == "project.list":
        _require_fields(params, set(), "project.list params")
    elif method == "project.create":
        _validate_project_create_params(params)
    elif method == "project.open":
        _validate_project_id_params(params, method=method)
    elif method == "project.update":
        _validate_project_update_params(params)
    elif method == "project.workspace":
        _validate_project_workspace_params(params)
    elif method == "project.archive":
        _validate_project_archive_params(params)
    elif method == "project.chat.move":
        _validate_project_chat_move_params(params)
    elif method == "request.cancel":
        _validate_cancel_params(params)
    elif method == "permission.respond":
        _validate_permission_response_params(params)
    else:
        _require_fields(params, set(), "shutdown params")
    return cast(ClientRequest, request)


def _validate_result_string_array(
    value: JsonObject,
    key: str,
    context: str,
) -> list[str]:
    raw = value.get(key)
    if (
        not isinstance(raw, list)
        or not raw
        or not all(
            isinstance(item, str)
            and 0 < len(item) <= MAX_IDENTIFIER_LENGTH
            for item in raw
        )
        or len(set(raw)) != len(raw)
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.{key} must contain unique identifiers.",
        )
    return cast(list[str], raw)


def _validate_chat_attachment(
    value: object,
    *,
    context: str,
) -> ChatAttachment:
    attachment = _as_object(value, context)
    _require_fields(
        attachment,
        {"attachmentId", "fileName", "mediaType", "sizeBytes"},
        context,
    )
    _require_identifier(attachment, "attachmentId", context)
    _require_string(attachment, "fileName", context)
    _require_string(attachment, "mediaType", context)
    size_bytes = _require_integer(attachment, "sizeBytes", context)
    if size_bytes < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sizeBytes cannot be negative.",
        )
    return cast(ChatAttachment, attachment)


def _validate_chat_message(
    value: object,
    *,
    context: str,
) -> ChatSessionMessage:
    message = _as_object(value, context)
    _require_fields(
        message,
        {"messageId", "role", "content", "createdAt", "attachments"},
        context,
    )
    _require_identifier(message, "messageId", context)
    role = _require_string(message, "role", context, maximum=9)
    if role not in {"system", "user", "assistant"}:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.role is unsupported.",
        )
    _require_string(message, "content", context, minimum=0)
    _require_string(message, "createdAt", context, maximum=128)
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.attachments must be an array.",
        )
    for position, attachment in enumerate(attachments):
        _validate_chat_attachment(
            attachment,
            context=f"{context}.attachments[{position}]",
        )
    return cast(ChatSessionMessage, message)


def _validate_chat_summary(
    value: object,
    *,
    context: str,
    detail: bool = False,
) -> ChatSessionSummary | ChatDetail:
    chat = _as_object(value, context)
    required = set(_CHAT_SUMMARY_FIELDS)
    if detail:
        required.add("messages")
    _require_fields(chat, required, context)
    _require_identifier(chat, "chatId", context)
    _require_string(chat, "title", context)
    mode = _require_string(chat, "mode", context, maximum=4)
    if mode not in {"chat", "work"}:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.mode is unsupported.",
        )
    _require_string(chat, "createdAt", context, maximum=128)
    _require_string(chat, "updatedAt", context, maximum=128)
    message_count = _require_integer(chat, "messageCount", context)
    if message_count < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.messageCount cannot be negative.",
        )
    if chat.get("projectId") is not None:
        _require_identifier(chat, "projectId", context)
    _require_identifier(chat, "modelName", context)
    _require_boolean(chat, "pinned", context)
    _require_boolean(chat, "archived", context)

    if not detail:
        return cast(ChatSessionSummary, chat)

    messages = chat.get("messages")
    if not isinstance(messages, list):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.messages must be an array.",
        )
    message_ids: set[str] = set()
    for position, raw_message in enumerate(messages):
        message = _validate_chat_message(
            raw_message,
            context=f"{context}.messages[{position}]",
        )
        message_id = message["messageId"]
        if message_id in message_ids:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                f"{context}.messages must have unique messageId values.",
            )
        message_ids.add(message_id)
    if message_count != len(messages):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.messageCount must equal the messages length.",
        )
    return cast(ChatDetail, chat)


def _validate_chat_state_result(value: object) -> ChatStateResult:
    result = _as_object(value, "chat state result")
    _require_fields(result, {"activeChat", "chats"}, "chat state result")
    active_chat = _validate_chat_summary(
        result["activeChat"],
        context="chat state result.activeChat",
        detail=True,
    )
    chats = result.get("chats")
    if not isinstance(chats, list):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "chat state result.chats must be an array.",
        )

    chat_ids: set[str] = set()
    matching_summary: ChatSessionSummary | None = None
    for position, raw_summary in enumerate(chats):
        summary = cast(
            ChatSessionSummary,
            _validate_chat_summary(
                raw_summary,
                context=f"chat state result.chats[{position}]",
            ),
        )
        chat_id = summary["chatId"]
        if chat_id in chat_ids:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "chat state result.chats must have unique chatId values.",
            )
        chat_ids.add(chat_id)
        if chat_id == active_chat["chatId"]:
            matching_summary = summary

    if matching_summary is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "chat state result.activeChat must appear in chats.",
        )
    active_object = cast(JsonObject, active_chat)
    active_summary = {
        key: active_object[key]
        for key in _CHAT_SUMMARY_FIELDS
    }
    if active_summary != matching_summary:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "chat state result.activeChat summary must match chats.",
        )
    return cast(ChatStateResult, result)


def _validate_project_summary(
    value: object,
    *,
    context: str,
) -> ProjectSummary:
    project = _as_object(value, context)
    _require_fields(
        project,
        {
            "projectId",
            "name",
            "createdAt",
            "updatedAt",
            "customInstructions",
            "workspacePath",
            "archived",
            "chatCount",
        },
        context,
    )
    _require_project_identifier(project, "projectId", context)
    _require_non_blank_string(
        project,
        "name",
        context,
        maximum=MAX_PROJECT_NAME_LENGTH,
        error_code="protocol.invalid_message",
    )
    _require_string(project, "createdAt", context, maximum=128)
    _require_string(project, "updatedAt", context, maximum=128)
    _require_nullable_non_blank_string(
        project,
        "customInstructions",
        context,
        maximum=MAX_MESSAGE_LENGTH,
        error_code="protocol.invalid_message",
    )
    _require_nullable_non_blank_string(
        project,
        "workspacePath",
        context,
        maximum=MAX_WORKSPACE_PATH_LENGTH,
        error_code="protocol.invalid_message",
    )
    _require_boolean(project, "archived", context)
    chat_count = _require_integer(project, "chatCount", context)
    if chat_count < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.chatCount cannot be negative.",
        )
    return cast(ProjectSummary, project)


def _validate_project_state_result(value: object) -> ProjectStateResult:
    result = _as_object(value, "project state result")
    _require_fields(
        result,
        {"activeProject", "projects", "chatState"},
        "project state result",
    )
    raw_projects = result.get("projects")
    if not isinstance(raw_projects, list):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "project state result.projects must be an array.",
        )

    projects: list[ProjectSummary] = []
    projects_by_id: dict[str, ProjectSummary] = {}
    for position, raw_project in enumerate(raw_projects):
        project = _validate_project_summary(
            raw_project,
            context=f"project state result.projects[{position}]",
        )
        project_id = project["projectId"]
        if project_id in projects_by_id:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "project state result.projects must have unique "
                "projectId values.",
            )
        projects.append(project)
        projects_by_id[project_id] = project

    raw_active_project = result.get("activeProject")
    active_project: ProjectSummary | None = None
    if raw_active_project is not None:
        active_project = _validate_project_summary(
            raw_active_project,
            context="project state result.activeProject",
        )
        matching_project = projects_by_id.get(active_project["projectId"])
        if matching_project is None:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "project state result.activeProject must appear in projects.",
            )
        if active_project != matching_project:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "project state result.activeProject must match projects.",
            )

    chat_state = _validate_chat_state_result(result["chatState"])
    observed_chat_counts = {
        project_id: 0 for project_id in projects_by_id
    }
    for chat in chat_state["chats"]:
        chat_project_id = chat["projectId"]
        if chat_project_id is None:
            continue
        if chat_project_id not in projects_by_id:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "project state result contains a Chat whose projectId "
                "is absent from projects.",
            )
        observed_chat_counts[chat_project_id] += 1

    for project in projects:
        project_id = project["projectId"]
        if project["chatCount"] != observed_chat_counts[project_id]:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "project state result.project chatCount must match "
                "chatState.chats.",
            )

    return cast(ProjectStateResult, result)


def _validate_success_result(result: JsonObject) -> None:
    fields = set(result)
    if fields == {"protocol", "server", "capabilities"}:
        _validate_descriptor(result["protocol"])
        server = _as_object(result["server"], "handshake result.server")
        _require_fields(server, {"name", "version"}, "handshake result.server")
        _require_identifier(server, "name", "handshake result.server")
        _require_identifier(server, "version", "handshake result.server")
        _validate_result_string_array(
            result,
            "capabilities",
            "handshake result",
        )
        return
    if fields == {"modelName", "models", "chatId", "chatTitle"}:
        model_name = _require_identifier(
            result,
            "modelName",
            "initialize result",
        )
        models = _validate_result_string_array(
            result,
            "models",
            "initialize result",
        )
        if model_name not in models:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "initialize result.modelName must be present in models.",
            )
        _require_identifier(result, "chatId", "initialize result")
        _require_string(result, "chatTitle", "initialize result")
        return
    if fields == {"chatId", "reply"}:
        _require_identifier(result, "chatId", "chat result")
        _require_string(result, "reply", "chat result")
        return
    if fields == {"activeChat", "chats"}:
        _validate_chat_state_result(result)
        return
    if fields == {"activeProject", "projects", "chatState"}:
        _validate_project_state_result(result)
        return
    if fields == {"stopped"} and result["stopped"] is True:
        return
    raise ProtocolValidationError(
        "protocol.invalid_message",
        "Success response.result does not match a supported result schema.",
    )


def _validate_response(message: JsonObject) -> ServerMessage:
    ok = _require_boolean(message, "ok", "response")
    request_id = message.get("id")
    if request_id is not None:
        _require_identifier(message, "id", "response")

    if ok:
        _require_fields(
            message,
            {"type", "protocol", "id", "ok", "result"},
            "success response",
        )
        _require_identifier(message, "id", "success response")
        result = _as_object(message["result"], "response.result")
        _validate_success_result(result)
        return cast(SuccessResponse, message)

    _require_fields(
        message,
        {"type", "protocol", "id", "ok", "error"},
        "error response",
    )
    error = _as_object(message["error"], "response.error")
    _require_fields(
        error,
        {"code", "message", "retryable"},
        "response.error",
    )
    _require_string(
        error,
        "code",
        "response.error",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    _require_string(error, "message", "response.error")
    _require_boolean(error, "retryable", "response.error")
    return cast(ErrorResponse, message)


def _validate_stream(message: JsonObject) -> StreamChunkMessage:
    _require_fields(
        message,
        {
            "type", "protocol", "requestId", "stream",
            "sequence", "chunk", "done",
        },
        "stream chunk",
    )
    _require_identifier(message, "requestId", "stream chunk")
    if message.get("stream") != "chat.reply":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "stream chunk.stream is unsupported.",
        )
    sequence = _require_integer(message, "sequence", "stream chunk")
    if sequence < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "stream chunk.sequence cannot be negative.",
        )
    _require_string(
        message,
        "chunk",
        "stream chunk",
        minimum=0,
    )
    done = _require_boolean(message, "done", "stream chunk")
    if done and message["chunk"] != "":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "The terminal stream chunk must be empty.",
        )
    return cast(StreamChunkMessage, message)


def _validate_progress(message: JsonObject) -> ProgressMessage:
    _require_fields(
        message,
        {
            "type", "protocol", "requestId", "operation",
            "completed", "total", "message",
        },
        "progress",
    )
    _require_identifier(message, "requestId", "progress")
    _require_string(
        message,
        "operation",
        "progress",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    completed = _require_integer(message, "completed", "progress")
    if completed < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "progress.completed cannot be negative.",
        )
    total = message.get("total")
    if total is not None:
        total = _require_integer(message, "total", "progress")
        if total < completed:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                "progress.total cannot be less than completed.",
            )
    progress_message = message.get("message")
    if progress_message is not None:
        _require_string(message, "message", "progress")
    return cast(ProgressMessage, message)


def _validate_permission(message: JsonObject) -> PermissionMessage:
    _require_fields(
        message,
        {
            "type", "protocol", "requestId", "permissionId",
            "capability", "reason", "scopes",
        },
        "permission",
    )
    if message.get("requestId") is not None:
        _require_identifier(message, "requestId", "permission")
    _require_identifier(message, "permissionId", "permission")
    _require_string(
        message,
        "capability",
        "permission",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    _require_string(message, "reason", "permission")
    scopes = message.get("scopes")
    if (
        not isinstance(scopes, list)
        or not all(
            isinstance(scope, str)
            and 0 < len(scope) <= MAX_IDENTIFIER_LENGTH
            for scope in scopes
        )
        or len(set(scopes)) != len(scopes)
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "permission.scopes must contain unique non-empty strings.",
        )
    return cast(PermissionMessage, message)


def _validate_event(message: JsonObject) -> EventMessage:
    _require_fields(
        message,
        {"type", "protocol", "event", "requestId", "data"},
        "event",
    )
    _require_string(
        message,
        "event",
        "event",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    if message.get("requestId") is not None:
        _require_identifier(message, "requestId", "event")
    _as_object(message["data"], "event.data")
    return cast(EventMessage, message)


def parse_server_message(value: object) -> ServerMessage:
    """Validate and return one Python-to-Electron message."""

    message = _as_object(value, "server message")
    message_type = message.get("type")
    if message_type not in {
        "response", "stream", "progress", "permission", "event",
    }:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "Server message.type is unsupported.",
        )
    if "protocol" not in message:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "Server message is missing protocol metadata.",
        )
    _validate_descriptor(message["protocol"])
    if message_type == "response":
        return _validate_response(message)
    if message_type == "stream":
        return _validate_stream(message)
    if message_type == "progress":
        return _validate_progress(message)
    if message_type == "permission":
        return _validate_permission(message)
    return _validate_event(message)


def build_request(
    request_id: str,
    method: ProtocolMethod,
    params: JsonObject,
) -> ClientRequest:
    """Build and validate one client request."""

    return parse_client_request(
        {
            "type": "request",
            "protocol": _descriptor(),
            "id": request_id,
            "method": method,
            "params": params,
        }
    )


def build_success_response(
    request_id: str,
    result: JsonObject,
) -> SuccessResponse:
    """Build one successful response."""

    return cast(
        SuccessResponse,
        parse_server_message(
            {
                "type": "response",
                "protocol": _descriptor(),
                "id": request_id,
                "ok": True,
                "result": result,
            }
        ),
    )


def build_error_response(
    request_id: str | None,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> ErrorResponse:
    """Build one stable bounded error response."""

    return cast(
        ErrorResponse,
        parse_server_message(
            {
                "type": "response",
                "protocol": _descriptor(),
                "id": request_id,
                "ok": False,
                "error": {
                    "code": code,
                    "message": message or "Desktop request failed.",
                    "retryable": retryable,
                },
            }
        ),
    )


def build_stream_chunk(
    request_id: str,
    sequence: int,
    chunk: str,
    *,
    done: bool,
) -> StreamChunkMessage:
    """Build one ordered Chat stream chunk."""

    return cast(
        StreamChunkMessage,
        parse_server_message(
            {
                "type": "stream",
                "protocol": _descriptor(),
                "requestId": request_id,
                "stream": "chat.reply",
                "sequence": sequence,
                "chunk": chunk,
                "done": done,
            }
        ),
    )


def build_progress(
    request_id: str,
    operation: str,
    completed: int,
    *,
    total: int | None,
    message: str | None,
) -> ProgressMessage:
    """Build one progress update."""

    return cast(
        ProgressMessage,
        parse_server_message(
            {
                "type": "progress",
                "protocol": _descriptor(),
                "requestId": request_id,
                "operation": operation,
                "completed": completed,
                "total": total,
                "message": message,
            }
        ),
    )


def build_permission(
    permission_id: str,
    capability: str,
    reason: str,
    scopes: list[str],
    *,
    request_id: str | None,
) -> PermissionMessage:
    """Build one permission prompt."""

    return cast(
        PermissionMessage,
        parse_server_message(
            {
                "type": "permission",
                "protocol": _descriptor(),
                "requestId": request_id,
                "permissionId": permission_id,
                "capability": capability,
                "reason": reason,
                "scopes": scopes,
            }
        ),
    )


def build_event(
    event: str,
    data: JsonObject,
    *,
    request_id: str | None,
) -> EventMessage:
    """Build one asynchronous Backend event."""

    return cast(
        EventMessage,
        parse_server_message(
            {
                "type": "event",
                "protocol": _descriptor(),
                "event": event,
                "requestId": request_id,
                "data": data,
            }
        ),
    )
