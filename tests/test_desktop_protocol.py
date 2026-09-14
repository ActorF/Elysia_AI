"""Validate the shared desktop-protocol contract in Python."""

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from desktop_protocol import (
    MAX_AUDIO_DEVICE_ID_LENGTH,
    MAX_PROTOCOL_FRAME_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    VOICE_CAPTURE_MAX_BASE64_CHARACTERS,
    ProtocolValidationError,
    build_error_response,
    build_event,
    build_permission,
    build_progress,
    build_stream_chunk,
    build_success_response,
    parse_client_request,
    parse_server_message,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT / "desktop_protocol" / "fixtures" / "v1.samples.json"
)
SCHEMA_PATH = ROOT / "desktop_protocol" / "schema" / "v1.schema.json"
JsonObject = dict[str, Any]


def _fixtures() -> JsonObject:
    """Load canonical wire samples shared by Python and Electron validators."""
    return cast(JsonObject, json.loads(FIXTURE_PATH.read_text("utf-8")))


@pytest.mark.parametrize(
    "sample",
    _fixtures()["validClientMessages"],
    ids=lambda sample: cast(JsonObject, sample)["name"],
)
def test_python_accepts_every_shared_valid_client_sample(
    sample: JsonObject,
) -> None:
    """Verify that python accepts every shared valid client sample."""
    parsed = parse_client_request(sample["message"])

    assert parsed["protocol"] == {
        "name": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
    }


@pytest.mark.parametrize(
    "sample",
    _fixtures()["validServerMessages"],
    ids=lambda sample: cast(JsonObject, sample)["name"],
)
def test_python_accepts_every_shared_valid_server_sample(
    sample: JsonObject,
) -> None:
    """Verify that python accepts every shared valid server sample."""
    parsed = parse_server_message(sample["message"])

    assert parsed["protocol"] == {
        "name": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
    }


@pytest.mark.parametrize(
    "sample",
    _fixtures()["invalidClientMessages"],
    ids=lambda sample: cast(JsonObject, sample)["name"],
)
def test_python_rejects_every_shared_invalid_client_sample(
    sample: JsonObject,
) -> None:
    """Verify that python rejects every shared invalid client sample."""
    with pytest.raises(ProtocolValidationError):
        parse_client_request(sample["message"])


@pytest.mark.parametrize(
    "sample",
    _fixtures()["invalidServerMessages"],
    ids=lambda sample: cast(JsonObject, sample)["name"],
)
def test_python_rejects_every_shared_invalid_server_sample(
    sample: JsonObject,
) -> None:
    """Verify that python rejects every shared invalid server sample."""
    with pytest.raises(ProtocolValidationError):
        parse_server_message(sample["message"])


def test_all_server_message_builders_round_trip_through_the_parser() -> None:
    """Verify that all server message builders round trip through the parser."""
    messages = [
        build_success_response("request-1", {"stopped": True}),
        build_error_response(
            "request-1",
            "backend.unavailable",
            "Backend unavailable.",
            retryable=True,
        ),
        build_stream_chunk("request-1", 0, "你好", done=False),
        build_progress(
            "request-1",
            "chat.generate",
            1,
            total=2,
            message="Generating",
        ),
        build_permission(
            "permission-1",
            "microphone.capture",
            "Voice input",
            ["audio.input"],
            request_id="request-1",
        ),
        build_event(
            "chat.started",
            {"chatId": "chat-1"},
            request_id="request-1",
        ),
    ]

    assert [parse_server_message(message) for message in messages] == messages


def test_attachment_only_chat_request_is_valid_but_blank_without_ids_is_not(
) -> None:
    """Verify that attachment only chat request is valid but blank without IDs is not.
    """
    request: JsonObject = {
        "type": "request",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "id": "attachment-chat",
        "method": "chat.stream",
        "params": {
            "chatId": "chat_attachment",
            "message": "",
            "attachmentIds": ["attachment_ready"],
        },
    }

    assert parse_client_request(request)["params"]["attachmentIds"] == [
        "attachment_ready"
    ]
    invalid = deepcopy(request)
    cast(JsonObject, invalid["params"])["attachmentIds"] = []
    with pytest.raises(ProtocolValidationError, match="cannot be blank"):
        parse_client_request(invalid)


@pytest.mark.parametrize(
    "source_path",
    [
        "notes.txt",
        r"\\server\share\notes.txt",
        r"\\?\C:\notes.txt",
        r"\\.\C:\notes.txt",
        r"C:\notes.txt:secret",
        r"C:\safe\.\notes.txt",
        r"C:\safe\..\notes.txt",
    ],
)
def test_attachment_add_rejects_untrusted_path_forms(
    source_path: str,
) -> None:
    """Verify that attachment add rejects untrusted path forms."""
    request: JsonObject = {
        "type": "request",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "id": "attachment-add",
        "method": "attachment.add",
        "params": {
            "scope": {"kind": "chat", "id": "chat_attachment"},
            "sourcePaths": [source_path],
        },
    }

    with pytest.raises(ProtocolValidationError, match="invalid path"):
        parse_client_request(request)


def test_attachment_add_rejects_windows_equivalent_duplicate_paths() -> None:
    """Verify that attachment add rejects windows equivalent duplicate paths."""
    request: JsonObject = {
        "type": "request",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "id": "attachment-add",
        "method": "attachment.add",
        "params": {
            "scope": {"kind": "chat", "id": "chat_attachment"},
            "sourcePaths": [r"C:\Notes.txt", "c:/notes.txt"],
        },
    }

    with pytest.raises(ProtocolValidationError, match="must be unique"):
        parse_client_request(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fileName", "C:/secret.txt"),
        ("mediaType", "not a mime"),
    ],
)
def test_attachment_state_rejects_unsafe_or_inconsistent_metadata(
    field: str,
    value: object,
) -> None:
    """Verify that attachment state rejects unsafe or inconsistent metadata."""
    sample = next(
        cast(JsonObject, candidate)
        for candidate in _fixtures()["validServerMessages"]
        if cast(JsonObject, candidate)["name"] == "attachment state response"
    )
    message = cast(JsonObject, deepcopy(sample["message"]))
    result = cast(JsonObject, message["result"])
    attachment = cast(list[JsonObject], result["attachments"])[0]
    attachment[field] = value

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


def test_attachment_state_accepts_a_draft_from_an_older_larger_limit() -> None:
    """Verify that attachment state accepts a draft from an older larger limit."""
    sample = next(
        cast(JsonObject, candidate)
        for candidate in _fixtures()["validServerMessages"]
        if cast(JsonObject, candidate)["name"] == "attachment state response"
    )
    message = cast(JsonObject, deepcopy(sample["message"]))
    result = cast(JsonObject, message["result"])
    result["maxFileBytes"] = 10
    attachment = cast(list[JsonObject], result["attachments"])[0]
    attachment["sizeBytes"] = 100

    assert parse_server_message(message)["type"] == "response"


def test_success_response_requires_a_non_null_request_id() -> None:
    """Verify that success response requires a non null request ID."""
    message: JsonObject = {
        "type": "response",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "id": None,
        "ok": True,
        "result": {},
    }

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


def test_python_normalizes_json_mathematical_integers() -> None:
    """Verify that python normalizes JSON mathematical integers."""
    message = cast(
        JsonObject,
        json.loads(
            '{"type":"request","protocol":'
            '{"name":"elysia.desktop","version":1e0},'
            '"id":"shutdown-1","method":"shutdown","params":{}}'
        ),
    )

    parsed = parse_client_request(message)
    descriptor = cast(JsonObject, parsed["protocol"])

    assert descriptor["version"] == 1
    assert type(descriptor["version"]) is int


def test_permission_scopes_must_be_unique() -> None:
    """Verify that permission scopes must be unique."""
    message: JsonObject = {
        "type": "permission",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "requestId": None,
        "permissionId": "permission-1",
        "capability": "microphone.capture",
        "reason": "Voice input",
        "scopes": ["audio.input", "audio.input"],
    }

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "default",
        "communications",
        "private\x00device",
        "x" * (MAX_AUDIO_DEVICE_ID_LENGTH + 1),
    ],
)
def test_voice_settings_reject_unsafe_ids_without_echoing_them(
    invalid_id: str,
) -> None:
    """Verify that voice settings reject unsafe IDs without echoing them."""
    request: JsonObject = {
        "type": "request",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "id": "voice-settings-invalid",
        "method": "voice.settings.update",
        "params": {
            "expectedRevision": 0,
            "inputDeviceId": invalid_id,
            "outputDeviceId": None,
        },
    }

    with pytest.raises(ProtocolValidationError) as raised:
        parse_client_request(request)

    assert raised.value.code == "protocol.invalid_params"
    assert invalid_id not in str(raised.value)


def test_voice_settings_result_requires_its_explicit_kind() -> None:
    """Verify that voice settings result requires its explicit kind."""
    message: JsonObject = {
        "type": "response",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "id": "voice-settings-get",
        "ok": True,
        "result": {
            "kind": "audio.devices",
            "revision": 0,
            "updatedAt": None,
            "inputDeviceId": None,
            "outputDeviceId": None,
            "warning": None,
        },
    }

    with pytest.raises(ProtocolValidationError, match="kind is unsupported"):
        parse_server_message(message)


def _voice_capture_request() -> JsonObject:
    """Provide the voice capture request fixture used by these tests."""
    sample = next(
        cast(JsonObject, candidate)
        for candidate in _fixtures()["validClientMessages"]
        if cast(JsonObject, candidate)["name"]
        == "voice capture complete request"
    )
    return cast(JsonObject, deepcopy(sample["message"]))


def _voice_capture_response() -> JsonObject:
    """Provide the voice capture response fixture used by these tests."""
    sample = next(
        cast(JsonObject, candidate)
        for candidate in _fixtures()["validServerMessages"]
        if cast(JsonObject, candidate)["name"] == "voice capture response"
    )
    return cast(JsonObject, deepcopy(sample["message"]))


def test_voice_capture_request_accepts_exact_canonical_pcm_metadata() -> None:
    """Verify that voice capture request accepts exact canonical PCM metadata."""
    request = _voice_capture_request()

    parsed = parse_client_request(request)
    params = cast(JsonObject, parsed["params"])

    assert parsed["method"] == "voice.capture.complete"
    assert params["sessionId"] == "voice_fixture"
    assert len(base64.b64decode(cast(str, params["pcmBase64"]))) == 6_400


@pytest.mark.parametrize(
    "invalid_base64",
    [
        "AB==",
        "AA==\n",
        "_A==",
        "AQ",
        "AAAA====",
        "音频",
    ],
)
def test_voice_capture_request_requires_strict_canonical_base64(
    invalid_base64: str,
) -> None:
    """Verify that voice capture request requires strict canonical Base64."""
    request = _voice_capture_request()
    cast(JsonObject, request["params"])["pcmBase64"] = invalid_base64

    with pytest.raises(ProtocolValidationError, match="canonical Base64"):
        parse_client_request(request)


def test_voice_capture_request_rejects_wrong_decoded_pcm_length() -> None:
    """Verify that voice capture request rejects wrong decoded PCM length."""
    request = _voice_capture_request()
    params = cast(JsonObject, request["params"])
    params["pcmBase64"] = base64.b64encode(bytes(6_402)).decode("ascii")

    with pytest.raises(ProtocolValidationError, match=r"sampleCount \* 2"):
        parse_client_request(request)


def test_voice_capture_request_bounds_encoded_pcm_before_decoding() -> None:
    """Verify that voice capture request bounds encoded PCM before decoding."""
    request = _voice_capture_request()
    params = cast(JsonObject, request["params"])
    params["pcmBase64"] = "A" * (VOICE_CAPTURE_MAX_BASE64_CHARACTERS + 1)

    with pytest.raises(ProtocolValidationError, match="length"):
        parse_client_request(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sampleCount", 3_199),
        ("sampleCount", 3_201),
        ("sampleCount", 480_320),
        ("speechStartSample", -320),
        ("speechStartSample", 1),
        ("speechStartSample", 320),
        ("speechEndSample", 3_520),
        ("speechEndSample", 3_199),
    ],
)
def test_voice_capture_request_enforces_frame_and_marker_invariants(
    field: str,
    value: int,
) -> None:
    """Verify that voice capture request enforces frame and marker invariants."""
    request = _voice_capture_request()
    cast(JsonObject, request["params"])[field] = value

    with pytest.raises(ProtocolValidationError):
        parse_client_request(request)


def test_voice_capture_result_contains_only_safe_exact_metadata() -> None:
    """Verify that voice capture result contains only safe exact metadata."""
    message = _voice_capture_response()

    parsed = parse_server_message(message)
    result = cast(JsonObject, parsed["result"])

    assert result["kind"] == "voice.capture"
    assert result["durationMs"] == 200
    assert result["speechDurationMs"] == 200
    assert "pcmBase64" not in result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("durationMs", -1),
        ("durationMs", 201),
        ("speechDurationMs", 1.5),
        ("speechDurationMs", 201),
        ("sha256Hex", "A" * 64),
        ("sampleCount", 3_201),
        ("speechEndSample", 3_520),
    ],
)
def test_voice_capture_result_rejects_invalid_metadata(
    field: str,
    value: object,
) -> None:
    """Verify that voice capture result rejects invalid metadata."""
    message = _voice_capture_response()
    cast(JsonObject, message["result"])[field] = value

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


def test_voice_capture_result_rejects_pcm_or_other_extra_fields() -> None:
    """Verify that voice capture result rejects PCM or other extra fields."""
    message = _voice_capture_response()
    cast(JsonObject, message["result"])["pcmBase64"] = "AAAA"

    with pytest.raises(ProtocolValidationError, match="supported result"):
        parse_server_message(message)


def test_machine_readable_schema_covers_every_protocol_message_kind() -> None:
    """Verify that machine readable schema covers every protocol message kind."""
    schema = cast(JsonObject, json.loads(SCHEMA_PATH.read_text("utf-8")))
    definitions = cast(JsonObject, schema["$defs"])

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-elysia-frameMaxBytes"] == MAX_PROTOCOL_FRAME_BYTES
    assert schema["x-elysia-stringLengthUnit"] == "Unicode code points"
    assert "U+FEFF" in schema["x-elysia-blankCodePoints"]
    assert (
        "initializeResult.modelName is present in initializeResult.models"
        in schema["x-elysia-runtimeInvariants"]
    )
    assert {
        "handshakeRequest",
        "initializeRequest",
        "chatStreamRequest",
        "chatRetryRequest",
        "chatListRequest",
        "chatCreateRequest",
        "chatOpenRequest",
        "chatRenameRequest",
        "chatPinRequest",
        "chatArchiveRequest",
        "chatDeleteRequest",
        "projectListRequest",
        "projectCreateRequest",
        "projectOpenRequest",
        "projectUpdateRequest",
        "projectWorkspaceRequest",
        "projectArchiveRequest",
        "projectChatMoveRequest",
        "settingsGetRequest",
        "settingsUpdateRequest",
        "voiceSettingsGetRequest",
        "voiceSettingsUpdateRequest",
        "voiceCaptureCompleteRequest",
        "cancelRequest",
        "permissionResponseRequest",
        "shutdownRequest",
        "successResponse",
        "errorResponse",
        "streamChunk",
        "progress",
        "permission",
        "event",
        "chatSessionSummary",
        "chatDetail",
        "chatStateResult",
        "projectSummary",
        "projectStateResult",
        "settingsValues",
        "settingsProjectScope",
        "settingsChatScope",
        "settingsScopes",
        "settingsStateResult",
        "voiceSettingsStateResult",
        "voiceCaptureCompleteParams",
        "voiceCaptureResult",
        "voiceSessionIdentifier",
        "audioDeviceId",
        "nullableAudioDeviceId",
    }.issubset(definitions)

    runtime_invariants = schema["x-elysia-runtimeInvariants"]
    assert any(
        "decoded byte length equals sampleCount * 2" in invariant
        for invariant in runtime_invariants
    )
    assert any(
        "aligned to 320-sample frames" in invariant
        for invariant in runtime_invariants
    )

    settings_values = cast(JsonObject, definitions["settingsValues"])
    properties = cast(JsonObject, settings_values["properties"])
    assert set(properties) == {
        "modelName",
        "ollamaHost",
        "shortTermMemoryTokenBudget",
        "memoryRetrievalLimit",
        "dataImportMaxBytes",
    }
    assert settings_values["additionalProperties"] is False


def _project_state_response() -> JsonObject:
    """Provide the project state response fixture used by these tests."""
    sample = next(
        cast(JsonObject, candidate)
        for candidate in _fixtures()["validServerMessages"]
        if cast(JsonObject, candidate)["name"] == "project state response"
    )
    return cast(JsonObject, deepcopy(sample["message"]))


@pytest.mark.parametrize(
    "invalid_state",
    [
        "active-absent",
        "active-mismatch",
        "duplicate-project",
        "dangling-chat-project",
        "wrong-chat-count",
    ],
)
def test_project_state_runtime_invariants_are_enforced(
    invalid_state: str,
) -> None:
    """Verify that project state runtime invariants are enforced."""
    message = _project_state_response()
    result = cast(JsonObject, message["result"])
    projects = cast(list[JsonObject], result["projects"])
    active_project = cast(JsonObject, result["activeProject"])
    chat_state = cast(JsonObject, result["chatState"])
    chats = cast(list[JsonObject], chat_state["chats"])

    if invalid_state == "active-absent":
        active_project["projectId"] = "project_missing"
    elif invalid_state == "active-mismatch":
        active_project["name"] = "Stale Project"
    elif invalid_state == "duplicate-project":
        projects.append(deepcopy(projects[0]))
    elif invalid_state == "dangling-chat-project":
        chats[1]["projectId"] = "project_missing"
    else:
        projects[0]["chatCount"] = 2
        active_project["chatCount"] = 2

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


def test_json_schema_validates_the_shared_structural_samples() -> None:
    """Verify that JSON schema validates the shared structural samples."""
    fixtures = _fixtures()
    schema = cast(JsonObject, json.loads(SCHEMA_PATH.read_text("utf-8")))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    valid_samples = [
        *fixtures["validClientMessages"],
        *fixtures["validServerMessages"],
    ]
    for sample in valid_samples:
        errors = list(validator.iter_errors(sample["message"]))
        assert errors == [], sample["name"]

    invalid_samples = [
        *fixtures["invalidClientMessages"],
        *fixtures["invalidServerMessages"],
    ]
    for sample in invalid_samples:
        if sample.get("runtimeOnly") is True:
            continue
        assert not validator.is_valid(sample["message"]), sample["name"]
