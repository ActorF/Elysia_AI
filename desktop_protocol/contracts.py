"""Define and validate version 1 of the local desktop wire protocol.

The protocol deliberately uses plain JSON values so Python and TypeScript can
validate the same fixtures without either runtime importing the other.  Every
wire message carries the protocol name and version; untrusted or stale peers
are rejected before application services are invoked.
"""

from __future__ import annotations

import base64
import binascii
import math
import ntpath
import re
import unicodedata
from typing import Any, Final, Literal, NotRequired, TypeAlias, TypedDict, cast
from urllib.parse import urlsplit

from attachments.domain import validate_file_name, validate_media_type

PROTOCOL_NAME: Final = "elysia.desktop"
PROTOCOL_VERSION: Final = 1
MAX_IDENTIFIER_LENGTH: Final = 128
MAX_METHOD_LENGTH: Final = 96
MAX_MESSAGE_LENGTH: Final = 1_000_000
MAX_PROJECT_NAME_LENGTH: Final = 200
MAX_WORKSPACE_PATH_LENGTH: Final = 32_767
MAX_SETTINGS_MODEL_NAME_LENGTH: Final = 200
MAX_OLLAMA_HOST_LENGTH: Final = 2_048
MAX_AUDIO_DEVICE_ID_LENGTH: Final = 2_048
VOICE_CAPTURE_SAMPLE_RATE_HZ: Final = 16_000
VOICE_CAPTURE_CHANNEL_COUNT: Final = 1
VOICE_CAPTURE_SAMPLE_FORMAT: Final = "s16le"
VOICE_CAPTURE_FRAME_SAMPLES: Final = 320
VOICE_CAPTURE_BYTES_PER_SAMPLE: Final = 2
VOICE_CAPTURE_MIN_SAMPLES: Final = 3_200
VOICE_CAPTURE_MAX_SAMPLES: Final = 480_000
VOICE_CAPTURE_MIN_SPEECH_SAMPLES: Final = 3_200
VOICE_CAPTURE_MAX_SESSION_ID_LENGTH: Final = 128
VOICE_CAPTURE_MAX_BASE64_CHARACTERS: Final = 1_280_000
VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS: Final = 4_096
VOICE_SPEECH_MIN_WAV_BYTES: Final = 46
VOICE_SPEECH_MAX_WAV_BYTES: Final = 8 * 1024 * 1024
VOICE_SPEECH_MAX_SEQUENCE: Final = (1 << 32) - 1
MAX_MEMORY_SETTING: Final = 10_000_000
MAX_DATA_IMPORT_BYTES: Final = 2_147_483_647
MAX_PROTOCOL_FRAME_BYTES: Final = 16_777_216
MAX_ATTACHMENTS_PER_SCOPE: Final = 10
MAX_ATTACHMENT_FILE_NAME_LENGTH: Final = 255
MAX_ATTACHMENT_MEDIA_TYPE_LENGTH: Final = 255
MAX_ATTACHMENT_SOURCE_PATH_LENGTH: Final = 32_767
MIN_SESSION_TOKEN_LENGTH: Final = 32
MAX_SESSION_TOKEN_LENGTH: Final = 512
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

TRANSCRIPTION_MODELS: Final = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "turbo",
)
TRANSCRIPTION_REQUESTED_DEVICES: Final = ("auto", "cuda", "cpu")
TRANSCRIPTION_LANGUAGES: Final = ("auto", "zh", "en")
TRANSCRIPTION_STATUS_STATES: Final = (
    "unavailable",
    "available",
    "ready",
)
TRANSCRIPTION_RESOLVED_DEVICES: Final = ("cuda", "cpu")
TRANSCRIPTION_COMPUTE_TYPES: Final = (
    "float16",
    "int8_float16",
    "int8",
    "float32",
)
TRANSCRIPTION_STATUS_REASONS: Final = (
    "model_missing",
    "dependencies_missing",
    "runtime_probe_failed",
    "device_unavailable",
    "cuda_unavailable",
    "cuda_initialization_failed",
    "initialization_failed",
)

# An explicit table keeps blank-string decisions identical in Python and
# TypeScript instead of depending on runtime-specific Unicode whitespace data.
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
_CHAT_ID_PATTERN: Final = re.compile(r"^chat_[A-Za-z0-9_-]+$")
_VOICE_SESSION_ID_PATTERN: Final = re.compile(r"^voice_[A-Za-z0-9_-]+$")
_ATTACHMENT_ID_PATTERN: Final = re.compile(
    r"^attachment_[A-Za-z0-9_-]+$"
)
_LOWERCASE_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_AUDIO_DEVICE_IDS: Final = frozenset({
    "default",
    "communications",
})

ProtocolEventName: TypeAlias = Literal[
    "chat.started",
    "chat.completed",
    "chat.cancelled",
    "voice.transcription.started",
    "voice.transcription.completed",
    "voice.transcription.cancelled",
    "voice.transcription.timed_out",
    "voice.transcription.failed",
    "voice.speech.clip",
    "voice.speech.failure",
    "voice.speech.terminal",
]
VoiceSpeechFailureCode: TypeAlias = Literal[
    "invalid_request",
    "unavailable",
    "synthesis_failed",
    "internal_error",
]
VoiceSpeechTerminalState: TypeAlias = Literal["completed", "cancelled"]

_CHAT_LIFECYCLE_EVENTS: Final = frozenset({
    "chat.started",
    "chat.completed",
    "chat.cancelled",
})
_TRANSCRIPTION_LIFECYCLE_EVENTS: Final = frozenset({
    "voice.transcription.started",
    "voice.transcription.completed",
    "voice.transcription.cancelled",
    "voice.transcription.timed_out",
    "voice.transcription.failed",
})
_VOICE_SPEECH_FAILURE_CODES: Final = frozenset({
    "invalid_request",
    "unavailable",
    "synthesis_failed",
    "internal_error",
})
_VOICE_SPEECH_TERMINAL_STATES: Final = frozenset({
    "completed",
    "cancelled",
})
_PROTOCOL_EVENT_NAMES: Final = (
    _CHAT_LIFECYCLE_EVENTS
    | _TRANSCRIPTION_LIFECYCLE_EVENTS
    | frozenset({
        "voice.speech.clip",
        "voice.speech.failure",
        "voice.speech.terminal",
    })
)

ProtocolMethod = Literal[
    "handshake",
    "initialize",
    "chat.stream",
    "chat.retry",
    "chat.list",
    "chat.create",
    "chat.open",
    "chat.rename",
    "chat.pin",
    "chat.archive",
    "chat.delete",
    "attachment.list",
    "attachment.add",
    "attachment.remove",
    "project.list",
    "project.create",
    "project.open",
    "project.update",
    "project.workspace",
    "project.archive",
    "project.chat.move",
    "settings.get",
    "settings.update",
    "voice.settings.get",
    "voice.settings.update",
    "voice.capture.complete",
    "voice.transcription.start",
    "request.cancel",
    "permission.respond",
    "shutdown",
]
SUPPORTED_METHODS: Final[tuple[ProtocolMethod, ...]] = (
    "handshake",
    "initialize",
    "chat.stream",
    "chat.retry",
    "chat.list",
    "chat.create",
    "chat.open",
    "chat.rename",
    "chat.pin",
    "chat.archive",
    "chat.delete",
    "attachment.list",
    "attachment.add",
    "attachment.remove",
    "project.list",
    "project.create",
    "project.open",
    "project.update",
    "project.workspace",
    "project.archive",
    "project.chat.move",
    "settings.get",
    "settings.update",
    "voice.settings.get",
    "voice.settings.update",
    "voice.capture.complete",
    "voice.transcription.start",
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
    attachmentIds: NotRequired[list[str]]


class AttachmentScope(TypedDict):
    """Identify one exact Chat or Project attachment namespace."""

    kind: Literal["chat", "project"]
    id: str


class AttachmentListParams(TypedDict):
    """List canonical local attachments for one scope."""

    scope: AttachmentScope


class AttachmentAddParams(TypedDict):
    """Import trusted native paths supplied only by Electron main."""

    scope: AttachmentScope
    sourcePaths: list[str]


class AttachmentRemoveParams(TypedDict):
    """Remove one uncommitted Chat item or one Project item."""

    scope: AttachmentScope
    attachmentId: str


class ChatRetryParams(TypedDict):
    """Regenerate or edit-and-retry one persisted tail turn."""

    chatId: str
    userMessageId: str
    assistantMessageId: str
    message: NotRequired[str]


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
    """Identify the in-flight operation a cancellation request should stop."""

    requestId: str
    reason: str


class PermissionResponseParams(TypedDict):
    """Return a renderer decision for a permission prompt."""

    permissionId: str
    granted: bool


class DesktopSettingsValues(TypedDict):
    """Expose only public settings that may safely cross the desktop wire."""

    modelName: str
    ollamaHost: str
    shortTermMemoryTokenBudget: int
    memoryRetrievalLimit: int
    dataImportMaxBytes: int
    transcriptionModel: Literal[
        "tiny",
        "base",
        "small",
        "medium",
        "large-v3",
        "turbo",
    ]
    transcriptionDevice: Literal["auto", "cuda", "cpu"]
    transcriptionLanguage: Literal["auto", "zh", "en"]


class SettingsUpdateParams(TypedDict):
    """Replace one complete revisioned global settings snapshot."""

    expectedRevision: int
    settings: DesktopSettingsValues


class VoiceSettingsUpdateParams(TypedDict):
    """Replace both opaque local audio-device preferences atomically."""

    expectedRevision: int
    inputDeviceId: str | None
    outputDeviceId: str | None


class VoiceCaptureMetadata(TypedDict):
    """Describe one fixed-format, frame-aligned Voice capture."""

    sessionId: str
    chatId: str
    sampleRateHz: Literal[16000]
    channelCount: Literal[1]
    sampleFormat: Literal["s16le"]
    sampleCount: int
    speechStartSample: int
    speechEndSample: int


class VoiceCaptureCompleteParams(VoiceCaptureMetadata):
    """Carry one transient canonical Base64 PCM capture to Python."""

    pcmBase64: str


class VoiceTranscriptionStartParams(VoiceCaptureCompleteParams):
    """Start one bounded transcription from the supplied transient PCM."""

    language: Literal["auto", "zh", "en"]


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
    """Publish one closed, request-correlated asynchronous Backend event."""

    type: Literal["event"]
    protocol: ProtocolDescriptor
    event: ProtocolEventName
    requestId: str
    data: JsonObject


class ChatAttachment(TypedDict):
    """Expose attachment metadata without file bytes or private paths."""

    attachmentId: str
    fileName: str
    mediaType: str
    sizeBytes: int


class AttachmentItem(ChatAttachment):
    """Expose one locally stored item without its private source or blob path."""

    status: Literal["ready"]


class AttachmentStateResult(TypedDict):
    """Return the exact canonical items and limits for one scope."""

    scope: AttachmentScope
    attachments: list[AttachmentItem]
    maxFileBytes: int
    maxFileCount: int


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


class SettingsProjectScope(TypedDict):
    """Describe a Project model override without mutating its semantics."""

    projectId: str
    projectName: str
    modelName: str | None
    inheritedModelName: str


class SettingsChatScope(TypedDict):
    """Describe the active Chat's persisted pinned model."""

    chatId: str
    chatTitle: str
    modelName: str


class SettingsScopes(TypedDict):
    """Distinguish global, Project, and Chat settings ownership."""

    project: SettingsProjectScope | None
    chat: SettingsChatScope | None


class SettingsStateResult(TypedDict):
    """Return desired settings beside the currently active runtime snapshot."""

    revision: int
    updatedAt: str | None
    settings: DesktopSettingsValues
    activeSettings: DesktopSettingsValues
    restartRequired: bool
    restartFields: list[str]
    scopes: SettingsScopes
    warning: str | None


class VoiceSettingsStateResult(TypedDict):
    """Expose saved device preferences without live hardware information."""

    kind: Literal["voice.settings"]
    revision: int
    updatedAt: str | None
    inputDeviceId: str | None
    outputDeviceId: str | None
    transcriptionStatus: "TranscriptionStatus"
    warning: str | None


class TranscriptionStatus(TypedDict):
    """Expose sanitized local STT readiness without paths or native errors."""

    state: Literal["unavailable", "available", "ready"]
    model: Literal["tiny", "base", "small", "medium", "large-v3", "turbo"]
    requestedDevice: Literal["auto", "cuda", "cpu"]
    resolvedDevice: Literal["cuda", "cpu"] | None
    computeType: Literal["float16", "int8_float16", "int8", "float32"] | None
    reason: Literal[
        "model_missing",
        "dependencies_missing",
        "runtime_probe_failed",
        "device_unavailable",
        "cuda_unavailable",
        "cuda_initialization_failed",
        "initialization_failed",
    ] | None


class VoiceCaptureResult(VoiceCaptureMetadata):
    """Acknowledge validated PCM through metadata and a digest only."""

    kind: Literal["voice.capture"]
    durationMs: int
    speechDurationMs: int
    sha256Hex: str


class VoiceTranscriptionResult(TypedDict):
    """Return one final transcript without PCM, model paths, or native errors."""

    kind: Literal["voice.transcription"]
    sessionId: str
    chatId: str
    text: str
    language: Literal["zh", "en"]
    languageProbability: float


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
    context = "chat.stream params"
    _require_fields(
        params,
        {"chatId", "message"},
        context,
        optional={"attachmentIds"},
    )
    _require_identifier(params, "chatId", context)
    message = _require_string(params, "message", context, minimum=0)
    attachment_ids = _validate_attachment_id_array(
        params.get("attachmentIds", []),
        context=f"{context}.attachmentIds",
    )
    if not attachment_ids and not any(
        character not in _PROTOCOL_BLANK_CHARACTERS
        for character in message
    ):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            "chat.stream params.message cannot be blank.",
        )


def _validate_attachment_id_array(
    value: object,
    *,
    context: str,
) -> list[str]:
    if not isinstance(value, list):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context} must be an array.",
        )
    if len(value) > MAX_ATTACHMENTS_PER_SCOPE:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context} contains too many attachments.",
        )
    normalized: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or _ATTACHMENT_ID_PATTERN.fullmatch(item) is None
        ):
            raise ProtocolValidationError(
                "protocol.invalid_params",
                f"{context} must contain attachment IDs.",
            )
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context} must contain unique attachment IDs.",
        )
    return normalized


def _validate_attachment_scope(
    value: object,
    *,
    context: str,
    error_code: str = "protocol.invalid_params",
) -> AttachmentScope:
    scope = _as_object(value, context)
    _require_fields(scope, {"kind", "id"}, context)
    kind = _require_string(scope, "kind", context, maximum=7)
    identifier = _require_string(
        scope,
        "id",
        context,
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    expected_pattern = (
        _CHAT_ID_PATTERN if kind == "chat" else _PROJECT_ID_PATTERN
    )
    if (
        kind not in {"chat", "project"}
        or expected_pattern.fullmatch(identifier) is None
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context} must identify one valid Chat or Project.",
        )
    return cast(AttachmentScope, scope)


def _validate_attachment_list_params(params: JsonObject) -> None:
    context = "attachment.list params"
    _require_fields(params, {"scope"}, context)
    _validate_attachment_scope(params["scope"], context=f"{context}.scope")


def _validate_attachment_add_params(params: JsonObject) -> None:
    context = "attachment.add params"
    _require_fields(params, {"scope", "sourcePaths"}, context)
    _validate_attachment_scope(params["scope"], context=f"{context}.scope")
    source_paths = params["sourcePaths"]
    if (
        not isinstance(source_paths, list)
        or not source_paths
        or len(source_paths) > MAX_ATTACHMENTS_PER_SCOPE
    ):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.sourcePaths must contain 1 to "
            f"{MAX_ATTACHMENTS_PER_SCOPE} paths.",
        )
    normalized_paths: list[str] = []
    for source_path in source_paths:
        if not isinstance(source_path, str):
            raise ProtocolValidationError(
                "protocol.invalid_params",
                f"{context}.sourcePaths must contain strings.",
            )
        holder: JsonObject = {"path": source_path}
        path = _require_string(
            holder,
            "path",
            context,
            maximum=MAX_ATTACHMENT_SOURCE_PATH_LENGTH,
        )
        # ``ntpath`` makes the native-path contract deterministic even when
        # protocol fixtures are validated on a non-Windows CI host.
        normalized_path = path.replace("/", "\\")
        drive, tail = ntpath.splitdrive(normalized_path)
        path_parts = tuple(
            part
            for part in normalized_path[len(drive):].split("\\")
            if part
        )
        if (
            not path.strip()
            or path != path.strip()
            or "\x00" in path
            or "\r" in path
            or "\n" in path
            or not ntpath.isabs(normalized_path)
            or not drive
            or drive.startswith("\\")
            or normalized_path.startswith(("\\\\?\\", "\\\\.\\"))
            or ":" in tail
            or any(part in {".", ".."} for part in path_parts)
        ):
            raise ProtocolValidationError(
                "protocol.invalid_params",
                f"{context}.sourcePaths contains an invalid path.",
            )
        normalized_paths.append(
            ntpath.normcase(ntpath.normpath(normalized_path))
        )
    if len(normalized_paths) != len(set(normalized_paths)):
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.sourcePaths must be unique.",
        )


def _validate_attachment_remove_params(params: JsonObject) -> None:
    context = "attachment.remove params"
    _require_fields(params, {"scope", "attachmentId"}, context)
    _validate_attachment_scope(params["scope"], context=f"{context}.scope")
    attachment_id = _require_string(
        params,
        "attachmentId",
        context,
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    if _ATTACHMENT_ID_PATTERN.fullmatch(attachment_id) is None:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.attachmentId is invalid.",
        )


def _validate_chat_retry_params(params: JsonObject) -> None:
    context = "chat.retry params"
    _require_fields(
        params,
        {"chatId", "userMessageId", "assistantMessageId"},
        context,
        optional={"message"},
    )
    _require_identifier(params, "chatId", context)
    _require_identifier(params, "userMessageId", context)
    _require_identifier(params, "assistantMessageId", context)
    if "message" in params:
        message = _require_string(params, "message", context)
        if not any(
            character not in _PROTOCOL_BLANK_CHARACTERS
            for character in message
        ):
            raise ProtocolValidationError(
                "protocol.invalid_params",
                "chat.retry params.message cannot be blank.",
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


def _validate_settings_values(
    value: object,
    *,
    context: str,
    error_code: str = "protocol.invalid_params",
) -> DesktopSettingsValues:
    settings = _as_object(value, context)
    _require_fields(
        settings,
        {
            "modelName",
            "ollamaHost",
            "shortTermMemoryTokenBudget",
            "memoryRetrievalLimit",
            "dataImportMaxBytes",
            "transcriptionModel",
            "transcriptionDevice",
            "transcriptionLanguage",
        },
        context,
    )
    model_name = _require_non_blank_string(
        settings,
        "modelName",
        context,
        maximum=MAX_SETTINGS_MODEL_NAME_LENGTH,
        error_code=error_code,
    )
    ollama_host = _require_non_blank_string(
        settings,
        "ollamaHost",
        context,
        maximum=MAX_OLLAMA_HOST_LENGTH,
        error_code=error_code,
    )
    if (
        model_name != model_name.strip()
        or "\x00" in model_name
        or any(character in "\r\n" for character in model_name)
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context}.modelName must be trimmed and single-line.",
        )
    if (
        ollama_host != ollama_host.strip()
        or "\x00" in ollama_host
        or any(character.isspace() for character in ollama_host)
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context}.ollamaHost must be a valid HTTP origin.",
        )
    try:
        parsed_host = urlsplit(ollama_host)
        parsed_port = parsed_host.port
    except ValueError as error:
        raise ProtocolValidationError(
            error_code,
            f"{context}.ollamaHost must be a valid HTTP origin.",
        ) from error
    if (
        parsed_host.scheme not in {"http", "https"}
        or parsed_host.hostname is None
        or parsed_host.username is not None
        or parsed_host.password is not None
        or parsed_host.query
        or parsed_host.fragment
        or parsed_host.path not in {"", "/"}
        or parsed_port is not None and not 1 <= parsed_port <= 65_535
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context}.ollamaHost must be a valid HTTP origin.",
        )
    integer_limits = {
        "shortTermMemoryTokenBudget": MAX_MEMORY_SETTING,
        "memoryRetrievalLimit": MAX_MEMORY_SETTING,
        "dataImportMaxBytes": MAX_DATA_IMPORT_BYTES,
    }
    for key, maximum in integer_limits.items():
        number = _require_integer(settings, key, context)
        if number <= 0 or number > maximum:
            raise ProtocolValidationError(
                error_code,
                f"{context}.{key} is outside its supported range.",
            )
    transcription_model = _require_string(
        settings,
        "transcriptionModel",
        context,
        maximum=8,
    )
    if transcription_model not in TRANSCRIPTION_MODELS:
        raise ProtocolValidationError(
            error_code,
            f"{context}.transcriptionModel is unsupported.",
        )
    transcription_device = _require_string(
        settings,
        "transcriptionDevice",
        context,
        maximum=4,
    )
    if transcription_device not in TRANSCRIPTION_REQUESTED_DEVICES:
        raise ProtocolValidationError(
            error_code,
            f"{context}.transcriptionDevice is unsupported.",
        )
    transcription_language = _require_string(
        settings,
        "transcriptionLanguage",
        context,
        maximum=4,
    )
    if transcription_language not in TRANSCRIPTION_LANGUAGES:
        raise ProtocolValidationError(
            error_code,
            f"{context}.transcriptionLanguage is unsupported.",
        )
    return cast(DesktopSettingsValues, settings)


def _validate_settings_update_params(params: JsonObject) -> None:
    context = "settings.update params"
    _require_fields(params, {"expectedRevision", "settings"}, context)
    expected_revision = _require_integer(params, "expectedRevision", context)
    if expected_revision < 0:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            "settings.update params.expectedRevision cannot be negative.",
        )
    raw_settings = _as_object(params["settings"], f"{context}.settings")
    if set(raw_settings) != {
        "modelName",
        "ollamaHost",
        "shortTermMemoryTokenBudget",
        "memoryRetrievalLimit",
        "dataImportMaxBytes",
        "transcriptionModel",
        "transcriptionDevice",
        "transcriptionLanguage",
    }:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            "settings.update params.settings has invalid fields.",
        )
    _validate_settings_values(
        raw_settings,
        context="settings.update params.settings",
    )


def _validate_nullable_audio_device_id(
    value: JsonObject,
    key: str,
    context: str,
    *,
    error_code: str,
) -> str | None:
    """Validate an opaque ID without exposing or interpreting its content."""

    if value.get(key) is None:
        return None
    raw = value.get(key)
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > MAX_AUDIO_DEVICE_ID_LENGTH
        or raw in _RESERVED_AUDIO_DEVICE_IDS
        or any(unicodedata.category(character) == "Cc" for character in raw)
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context}.{key} must be null or a bounded hardware device ID "
            "without reserved values or control characters.",
        )
    return raw


def _validate_voice_settings_update_params(params: JsonObject) -> None:
    context = "voice.settings.update params"
    _require_fields(
        params,
        {"expectedRevision", "inputDeviceId", "outputDeviceId"},
        context,
    )
    expected_revision = _require_integer(params, "expectedRevision", context)
    if expected_revision < 0:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.expectedRevision cannot be negative.",
        )
    _validate_nullable_audio_device_id(
        params,
        "inputDeviceId",
        context,
        error_code="protocol.invalid_params",
    )
    _validate_nullable_audio_device_id(
        params,
        "outputDeviceId",
        context,
        error_code="protocol.invalid_params",
    )


def _validate_voice_capture_metadata(
    value: JsonObject,
    *,
    context: str,
    error_code: str,
) -> None:
    session_id = _require_string(
        value,
        "sessionId",
        context,
        maximum=VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
    )
    if _VOICE_SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ProtocolValidationError(
            error_code,
            f"{context}.sessionId must use the voice_<id> format.",
        )
    _require_identifier(value, "chatId", context)

    sample_rate_hz = _require_integer(value, "sampleRateHz", context)
    channel_count = _require_integer(value, "channelCount", context)
    sample_format = _require_string(
        value,
        "sampleFormat",
        context,
        maximum=len(VOICE_CAPTURE_SAMPLE_FORMAT),
    )
    if (
        sample_rate_hz != VOICE_CAPTURE_SAMPLE_RATE_HZ
        or channel_count != VOICE_CAPTURE_CHANNEL_COUNT
        or sample_format != VOICE_CAPTURE_SAMPLE_FORMAT
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context} must describe 16000 Hz mono s16le PCM.",
        )

    sample_count = _require_integer(value, "sampleCount", context)
    speech_start = _require_integer(value, "speechStartSample", context)
    speech_end = _require_integer(value, "speechEndSample", context)
    if (
        sample_count < VOICE_CAPTURE_MIN_SAMPLES
        or sample_count > VOICE_CAPTURE_MAX_SAMPLES
        or sample_count % VOICE_CAPTURE_FRAME_SAMPLES != 0
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context}.sampleCount must be a frame-aligned value from "
            f"{VOICE_CAPTURE_MIN_SAMPLES} to {VOICE_CAPTURE_MAX_SAMPLES}.",
        )
    if (
        speech_start < 0
        or speech_end > sample_count
        or speech_end - speech_start < VOICE_CAPTURE_MIN_SPEECH_SAMPLES
        or speech_start % VOICE_CAPTURE_FRAME_SAMPLES != 0
        or speech_end % VOICE_CAPTURE_FRAME_SAMPLES != 0
    ):
        raise ProtocolValidationError(
            error_code,
            f"{context} speech markers must be frame-aligned, ordered, "
            "bounded by sampleCount, and span at least 3200 samples.",
        )


def _validate_voice_pcm_params(
    params: JsonObject,
    *,
    context: str,
    additional_fields: set[str] | None = None,
) -> None:
    """Validate one exact fixed-format PCM payload shared by Voice methods."""

    _require_fields(
        params,
        {
            "sessionId",
            "chatId",
            "sampleRateHz",
            "channelCount",
            "sampleFormat",
            "sampleCount",
            "speechStartSample",
            "speechEndSample",
            "pcmBase64",
        } | (additional_fields or set()),
        context,
    )
    _validate_voice_capture_metadata(
        params,
        context=context,
        error_code="protocol.invalid_params",
    )
    encoded_pcm = _require_string(
        params,
        "pcmBase64",
        context,
        maximum=VOICE_CAPTURE_MAX_BASE64_CHARACTERS,
    )
    try:
        decoded_pcm = base64.b64decode(encoded_pcm, validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.pcmBase64 must be strict canonical Base64.",
        ) from error
    # Strict decoding checks the alphabet; the round trip also rejects
    # alternate padding and non-zero pad bits accepted by some decoders.
    if base64.b64encode(decoded_pcm).decode("ascii") != encoded_pcm:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.pcmBase64 must be strict canonical Base64.",
        )
    sample_count = cast(int, params["sampleCount"])
    if len(decoded_pcm) != sample_count * VOICE_CAPTURE_BYTES_PER_SAMPLE:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.pcmBase64 decoded length must equal sampleCount * 2.",
        )


def _validate_voice_capture_complete_params(params: JsonObject) -> None:
    """Validate the legacy receipt-only Voice capture request."""

    _validate_voice_pcm_params(
        params,
        context="voice.capture.complete params",
    )


def _validate_voice_transcription_start_params(params: JsonObject) -> None:
    """Validate one language-scoped PCM request before background work."""

    context = "voice.transcription.start params"
    _validate_voice_pcm_params(
        params,
        context=context,
        additional_fields={"language"},
    )
    language = _require_string(
        params,
        "language",
        context,
        maximum=4,
    )
    if language not in {"auto", "zh", "en"}:
        raise ProtocolValidationError(
            "protocol.invalid_params",
            f"{context}.language must be auto, zh, or en.",
        )


def parse_client_request(value: object) -> ClientRequest:
    """Validate and return one Electron-to-Python request.

    The common envelope is checked first, then the selected method's exact
    parameter schema is checked before the mapping is narrowed to
    ``ClientRequest``. This keeps partially validated renderer data from
    reaching application services.
    """

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
    elif method == "chat.retry":
        _validate_chat_retry_params(params)
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
    elif method == "attachment.list":
        _validate_attachment_list_params(params)
    elif method == "attachment.add":
        _validate_attachment_add_params(params)
    elif method == "attachment.remove":
        _validate_attachment_remove_params(params)
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
    elif method == "settings.get":
        _require_fields(params, set(), "settings.get params")
    elif method == "settings.update":
        _validate_settings_update_params(params)
    elif method == "voice.settings.get":
        _require_fields(params, set(), "voice.settings.get params")
    elif method == "voice.settings.update":
        _validate_voice_settings_update_params(params)
    elif method == "voice.capture.complete":
        _validate_voice_capture_complete_params(params)
    elif method == "voice.transcription.start":
        _validate_voice_transcription_start_params(params)
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
    attachment_id = _require_identifier(
        attachment,
        "attachmentId",
        context,
    )
    if _ATTACHMENT_ID_PATTERN.fullmatch(attachment_id) is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.attachmentId is invalid.",
        )
    file_name = _require_string(
        attachment,
        "fileName",
        context,
        maximum=MAX_ATTACHMENT_FILE_NAME_LENGTH,
    )
    media_type = _require_string(
        attachment,
        "mediaType",
        context,
        maximum=MAX_ATTACHMENT_MEDIA_TYPE_LENGTH,
    )
    try:
        validate_file_name(file_name)
        validate_media_type(media_type)
    except ValueError as error:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} contains unsafe attachment metadata.",
        ) from error
    size_bytes = _require_integer(attachment, "sizeBytes", context)
    if size_bytes <= 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sizeBytes must be positive.",
        )
    return cast(ChatAttachment, attachment)


def _validate_attachment_state_result(
    value: object,
) -> AttachmentStateResult:
    context = "attachment state result"
    result = _as_object(value, context)
    _require_fields(
        result,
        {"scope", "attachments", "maxFileBytes", "maxFileCount"},
        context,
    )
    _validate_attachment_scope(
        result["scope"],
        context=f"{context}.scope",
        error_code="protocol.invalid_message",
    )
    max_file_bytes = _require_integer(result, "maxFileBytes", context)
    max_file_count = _require_integer(result, "maxFileCount", context)
    if (
        not 1 <= max_file_bytes <= MAX_DATA_IMPORT_BYTES
        or not 1 <= max_file_count <= MAX_ATTACHMENTS_PER_SCOPE
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} limits are invalid.",
        )
    attachments = result["attachments"]
    if (
        not isinstance(attachments, list)
        or len(attachments) > max_file_count
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.attachments exceeds its canonical limit.",
        )
    attachment_ids: set[str] = set()
    for position, raw_attachment in enumerate(attachments):
        item_context = f"{context}.attachments[{position}]"
        item = _as_object(raw_attachment, item_context)
        _require_fields(
            item,
            {
                "attachmentId",
                "fileName",
                "mediaType",
                "sizeBytes",
                "status",
            },
            item_context,
        )
        metadata = {
            key: item[key]
            for key in ("attachmentId", "fileName", "mediaType", "sizeBytes")
        }
        validated = _validate_chat_attachment(
            metadata,
            context=item_context,
        )
        if item.get("status") != "ready":
            raise ProtocolValidationError(
                "protocol.invalid_message",
                f"{item_context}.status must be 'ready'.",
            )
        attachment_id = validated["attachmentId"]
        if attachment_id in attachment_ids:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                f"{context}.attachments must have unique IDs.",
            )
        attachment_ids.add(attachment_id)
    return cast(AttachmentStateResult, result)


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
    if len(attachments) > MAX_ATTACHMENTS_PER_SCOPE:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.attachments contains too many items.",
        )
    attachment_ids: set[str] = set()
    for position, attachment in enumerate(attachments):
        validated_attachment = _validate_chat_attachment(
            attachment,
            context=f"{context}.attachments[{position}]",
        )
        attachment_id = validated_attachment["attachmentId"]
        if attachment_id in attachment_ids:
            raise ProtocolValidationError(
                "protocol.invalid_message",
                f"{context}.attachments must have unique IDs.",
            )
        attachment_ids.add(attachment_id)
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
    if fields == {
        "scope",
        "attachments",
        "maxFileBytes",
        "maxFileCount",
    }:
        _validate_attachment_state_result(result)
        return
    if fields == {
        "revision",
        "updatedAt",
        "settings",
        "activeSettings",
        "restartRequired",
        "restartFields",
        "scopes",
        "warning",
    }:
        _validate_settings_state_result(result)
        return
    if fields == {
        "kind",
        "revision",
        "updatedAt",
        "inputDeviceId",
        "outputDeviceId",
        "transcriptionStatus",
        "warning",
    }:
        _validate_voice_settings_state_result(result)
        return
    if fields == {
        "kind",
        "sessionId",
        "chatId",
        "sampleRateHz",
        "channelCount",
        "sampleFormat",
        "sampleCount",
        "speechStartSample",
        "speechEndSample",
        "durationMs",
        "speechDurationMs",
        "sha256Hex",
    }:
        _validate_voice_capture_result(result)
        return
    if fields == {
        "kind",
        "sessionId",
        "chatId",
        "text",
        "language",
        "languageProbability",
    }:
        _validate_voice_transcription_result(result)
        return
    if fields == {"stopped"} and result["stopped"] is True:
        return
    raise ProtocolValidationError(
        "protocol.invalid_message",
        "Success response.result does not match a supported result schema.",
    )


def _validate_settings_state_result(
    result: JsonObject,
) -> SettingsStateResult:
    context = "settings state result"
    revision = _require_integer(result, "revision", context)
    if revision < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.revision cannot be negative.",
        )
    if result.get("updatedAt") is not None:
        _require_string(result, "updatedAt", context, maximum=128)
    _validate_settings_values(
        result["settings"],
        context=f"{context}.settings",
        error_code="protocol.invalid_message",
    )
    _validate_settings_values(
        result["activeSettings"],
        context=f"{context}.activeSettings",
        error_code="protocol.invalid_message",
    )
    restart_required = _require_boolean(result, "restartRequired", context)
    restart_fields = result.get("restartFields")
    allowed_restart_fields = {
        "modelName",
        "ollamaHost",
        "shortTermMemoryTokenBudget",
        "memoryRetrievalLimit",
        "dataImportMaxBytes",
        "transcriptionModel",
        "transcriptionDevice",
        "transcriptionLanguage",
    }
    if (
        not isinstance(restart_fields, list)
        or not all(
            isinstance(field_name, str)
            and field_name in allowed_restart_fields
            for field_name in restart_fields
        )
        or len(restart_fields) != len(set(restart_fields))
        or restart_required != bool(restart_fields)
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.restartFields is inconsistent.",
        )
    scopes = _as_object(result["scopes"], f"{context}.scopes")
    _require_fields(scopes, {"project", "chat"}, f"{context}.scopes")
    raw_project = scopes.get("project")
    if raw_project is not None:
        project = _as_object(raw_project, f"{context}.scopes.project")
        _require_fields(
            project,
            {"projectId", "projectName", "modelName", "inheritedModelName"},
            f"{context}.scopes.project",
        )
        _require_project_identifier(
            project,
            "projectId",
            f"{context}.scopes.project",
        )
        _require_string(project, "projectName", f"{context}.scopes.project")
        if project.get("modelName") is not None:
            _require_string(
                project,
                "modelName",
                f"{context}.scopes.project",
                maximum=MAX_SETTINGS_MODEL_NAME_LENGTH,
            )
        _require_string(
            project,
            "inheritedModelName",
            f"{context}.scopes.project",
            maximum=MAX_SETTINGS_MODEL_NAME_LENGTH,
        )
    raw_chat = scopes.get("chat")
    if raw_chat is not None:
        chat = _as_object(raw_chat, f"{context}.scopes.chat")
        _require_fields(
            chat,
            {"chatId", "chatTitle", "modelName"},
            f"{context}.scopes.chat",
        )
        _require_identifier(chat, "chatId", f"{context}.scopes.chat")
        _require_string(chat, "chatTitle", f"{context}.scopes.chat")
        _require_string(
            chat,
            "modelName",
            f"{context}.scopes.chat",
            maximum=MAX_SETTINGS_MODEL_NAME_LENGTH,
        )
    if result.get("warning") is not None:
        _require_string(result, "warning", context, maximum=1_000)
    return cast(SettingsStateResult, result)


def _validate_voice_settings_state_result(
    result: JsonObject,
) -> VoiceSettingsStateResult:
    context = "voice settings state result"
    if result.get("kind") != "voice.settings":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.kind is unsupported.",
        )
    revision = _require_integer(result, "revision", context)
    if revision < 0:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.revision cannot be negative.",
        )
    if result.get("updatedAt") is not None:
        _require_string(result, "updatedAt", context, maximum=128)
    _validate_nullable_audio_device_id(
        result,
        "inputDeviceId",
        context,
        error_code="protocol.invalid_message",
    )
    _validate_nullable_audio_device_id(
        result,
        "outputDeviceId",
        context,
        error_code="protocol.invalid_message",
    )
    _validate_transcription_status(result.get("transcriptionStatus"))
    if result.get("warning") is not None:
        _require_string(result, "warning", context, maximum=1_000)
    return cast(VoiceSettingsStateResult, result)


def _validate_transcription_status(value: object) -> TranscriptionStatus:
    """Validate the fixed, path-free local transcription readiness shape."""

    context = "voice settings state result.transcriptionStatus"
    status = _as_object(value, context)
    _require_fields(
        status,
        {
            "state",
            "model",
            "requestedDevice",
            "resolvedDevice",
            "computeType",
            "reason",
        },
        context,
    )
    state = _require_string(status, "state", context, maximum=11)
    if state not in TRANSCRIPTION_STATUS_STATES:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.state is unsupported.",
        )
    model = _require_string(status, "model", context, maximum=8)
    if model not in TRANSCRIPTION_MODELS:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.model is unsupported.",
        )
    requested_device = _require_string(
        status,
        "requestedDevice",
        context,
        maximum=4,
    )
    if requested_device not in TRANSCRIPTION_REQUESTED_DEVICES:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.requestedDevice is unsupported.",
        )
    resolved_device = status.get("resolvedDevice")
    if (
        resolved_device is not None
        and resolved_device not in TRANSCRIPTION_RESOLVED_DEVICES
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.resolvedDevice is unsupported.",
        )
    compute_type = status.get("computeType")
    if compute_type is not None and compute_type not in TRANSCRIPTION_COMPUTE_TYPES:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.computeType is unsupported.",
        )
    reason = status.get("reason")
    if reason is not None and reason not in TRANSCRIPTION_STATUS_REASONS:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.reason is unsupported.",
        )
    if state == "unavailable" and (
        resolved_device is not None
        or compute_type is not None
        or reason
        not in {
            "model_missing",
            "dependencies_missing",
            "runtime_probe_failed",
            "device_unavailable",
            "initialization_failed",
        }
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} unavailable state has inconsistent details.",
        )
    if state in {"available", "ready"} and resolved_device == "cuda" and (
        requested_device not in {"auto", "cuda"}
        or compute_type not in {"float16", "int8_float16"}
        or reason is not None
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} has inconsistent CUDA readiness details.",
        )
    if state in {"available", "ready"} and resolved_device == "cpu":
        allowed_cpu_reasons: set[object]
        if requested_device == "cpu":
            allowed_cpu_reasons = {None}
        elif requested_device == "auto" and state == "available":
            allowed_cpu_reasons = {"cuda_unavailable"}
        elif requested_device == "auto":
            allowed_cpu_reasons = {
                "cuda_unavailable",
                "cuda_initialization_failed",
            }
        else:
            allowed_cpu_reasons = set()
        if (
            compute_type not in {"int8", "float32"}
            or reason not in allowed_cpu_reasons
        ):
            raise ProtocolValidationError(
                "protocol.invalid_message",
                f"{context} has inconsistent CPU readiness details.",
            )
    if state in {"available", "ready"} and resolved_device is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} usable state requires a resolved device.",
        )
    return cast(TranscriptionStatus, status)


def _validate_voice_capture_result(
    result: JsonObject,
) -> VoiceCaptureResult:
    context = "voice capture result"
    if result.get("kind") != "voice.capture":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.kind must be 'voice.capture'.",
        )
    _validate_voice_capture_metadata(
        result,
        context=context,
        error_code="protocol.invalid_message",
    )
    duration_ms = _require_integer(result, "durationMs", context)
    speech_duration_ms = _require_integer(result, "speechDurationMs", context)
    sample_count = cast(int, result["sampleCount"])
    speech_start = cast(int, result["speechStartSample"])
    speech_end = cast(int, result["speechEndSample"])
    if (
        duration_ms != sample_count * 1_000 // VOICE_CAPTURE_SAMPLE_RATE_HZ
        or speech_duration_ms
        != (speech_end - speech_start) * 1_000
        // VOICE_CAPTURE_SAMPLE_RATE_HZ
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} durations must exactly match its sample metadata.",
        )
    sha256_hex = _require_string(
        result,
        "sha256Hex",
        context,
        maximum=64,
    )
    if re.fullmatch(r"[0-9a-f]{64}", sha256_hex) is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sha256Hex must be a lowercase SHA-256 digest.",
        )
    return cast(VoiceCaptureResult, result)


def _validate_voice_transcription_result(
    result: JsonObject,
) -> VoiceTranscriptionResult:
    """Validate one bounded final transcript and its safe correlation fields."""

    context = "voice transcription result"
    if result.get("kind") != "voice.transcription":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.kind must be 'voice.transcription'.",
        )
    session_id = _require_string(
        result,
        "sessionId",
        context,
        maximum=VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
    )
    if _VOICE_SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sessionId must use the voice_<id> format.",
        )
    _require_identifier(result, "chatId", context)
    _require_non_blank_string(
        result,
        "text",
        context,
        maximum=VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
        error_code="protocol.invalid_message",
    )
    language = _require_string(result, "language", context, maximum=2)
    if language not in {"zh", "en"}:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.language must be zh or en.",
        )
    probability = result.get("languageProbability")
    if (
        isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        # JSON integers are unbounded in Python; math.isfinite would convert a
        # huge integer to float and raise instead of producing a protocol error.
        or (
            isinstance(probability, float)
            and not math.isfinite(probability)
        )
        or not 0.0 <= probability <= 1.0
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.languageProbability must be finite and between 0 and 1.",
        )
    return cast(VoiceTranscriptionResult, result)


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


def _validate_chat_lifecycle_event_data(data: JsonObject) -> None:
    """Accept only the Chat identifier needed to correlate lifecycle UI."""

    context = "chat lifecycle event.data"
    _require_fields(data, {"chatId"}, context)
    _require_identifier(data, "chatId", context)


def _validate_transcription_lifecycle_event_data(data: JsonObject) -> None:
    """Keep transcription lifecycle events free of PCM and native details."""

    context = "voice transcription lifecycle event.data"
    _require_fields(data, {"sessionId", "chatId"}, context)
    session_id = _require_string(
        data,
        "sessionId",
        context,
        maximum=VOICE_CAPTURE_MAX_SESSION_ID_LENGTH,
    )
    if _VOICE_SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sessionId must use the voice_<id> format.",
        )
    _require_identifier(data, "chatId", context)


def _validate_voice_speech_clip_event_data(data: JsonObject) -> None:
    """Validate metadata that must exactly match one private binary frame."""

    context = "voice.speech.clip event.data"
    _require_fields(
        data,
        {
            "chatId",
            "clipToken",
            "sequence",
            "byteLength",
            "sha256",
            "mediaType",
        },
        context,
    )
    _require_identifier(data, "chatId", context)
    clip_token = _require_string(
        data,
        "clipToken",
        context,
        maximum=64,
    )
    sha256_hex = _require_string(
        data,
        "sha256",
        context,
        maximum=64,
    )
    if (
        _LOWERCASE_SHA256_PATTERN.fullmatch(clip_token) is None
        or _LOWERCASE_SHA256_PATTERN.fullmatch(sha256_hex) is None
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} tokens must be lowercase 256-bit hexadecimal values.",
        )
    sequence = _require_integer(data, "sequence", context)
    if not 0 <= sequence <= VOICE_SPEECH_MAX_SEQUENCE:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sequence is outside the binary frame range.",
        )
    byte_length = _require_integer(data, "byteLength", context)
    if not (
        VOICE_SPEECH_MIN_WAV_BYTES
        <= byte_length
        <= VOICE_SPEECH_MAX_WAV_BYTES
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.byteLength is outside the bounded WAV range.",
        )
    media_type = _require_string(data, "mediaType", context, maximum=9)
    if media_type != "audio/wav":
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.mediaType must be audio/wav.",
        )


def _validate_voice_speech_failure_event_data(data: JsonObject) -> None:
    """Accept one sanitized per-sentence failure without diagnostic text."""

    context = "voice.speech.failure event.data"
    _require_fields(data, {"chatId", "sequence", "code"}, context)
    _require_identifier(data, "chatId", context)
    sequence = _require_integer(data, "sequence", context)
    if not 0 <= sequence <= VOICE_SPEECH_MAX_SEQUENCE:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.sequence is outside the binary frame range.",
        )
    code = _require_string(data, "code", context, maximum=16)
    if code not in _VOICE_SPEECH_FAILURE_CODES:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.code is unsupported.",
        )


def _validate_voice_speech_terminal_event_data(data: JsonObject) -> None:
    """Validate final queue counters and their cancellation semantics."""

    context = "voice.speech.terminal event.data"
    _require_fields(
        data,
        {
            "chatId",
            "state",
            "submittedSentences",
            "completedSentences",
            "failedSentences",
        },
        context,
    )
    _require_identifier(data, "chatId", context)
    state = _require_string(data, "state", context, maximum=9)
    if state not in _VOICE_SPEECH_TERMINAL_STATES:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context}.state is unsupported.",
        )
    submitted = _require_integer(data, "submittedSentences", context)
    completed = _require_integer(data, "completedSentences", context)
    failed = _require_integer(data, "failedSentences", context)
    if (
        not 0 <= failed <= completed <= submitted
        or (state == "completed" and completed != submitted)
    ):
        raise ProtocolValidationError(
            "protocol.invalid_message",
            f"{context} counters are inconsistent.",
        )


def _validate_event(message: JsonObject) -> EventMessage:
    """Dispatch one event to its exact path-free payload validator."""

    _require_fields(
        message,
        {"type", "protocol", "event", "requestId", "data"},
        "event",
    )
    event = _require_string(
        message,
        "event",
        "event",
        maximum=MAX_IDENTIFIER_LENGTH,
    )
    if event not in _PROTOCOL_EVENT_NAMES:
        raise ProtocolValidationError(
            "protocol.invalid_message",
            "event.event is unsupported.",
        )
    _require_identifier(message, "requestId", "event")
    data = _as_object(message["data"], "event.data")
    if event in _CHAT_LIFECYCLE_EVENTS:
        _validate_chat_lifecycle_event_data(data)
    elif event in _TRANSCRIPTION_LIFECYCLE_EVENTS:
        _validate_transcription_lifecycle_event_data(data)
    elif event == "voice.speech.clip":
        _validate_voice_speech_clip_event_data(data)
    elif event == "voice.speech.failure":
        _validate_voice_speech_failure_event_data(data)
    else:
        _validate_voice_speech_terminal_event_data(data)
    return cast(EventMessage, message)


def parse_server_message(value: object) -> ServerMessage:
    """Validate and return one Python-to-Electron message.

    Protocol metadata is verified before dispatching to the exact validator
    for the declared message type, so consumers never receive a partially
    checked response, stream, progress, permission, or event payload.
    """

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
    event: ProtocolEventName,
    data: JsonObject,
    *,
    request_id: str,
) -> EventMessage:
    """Build one exact request-correlated asynchronous Backend event."""

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
