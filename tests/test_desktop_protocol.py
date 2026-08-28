"""Validate the shared Stage 6 desktop-protocol contract in Python."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from desktop_protocol import (
    MAX_PROTOCOL_FRAME_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
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
    return cast(JsonObject, json.loads(FIXTURE_PATH.read_text("utf-8")))


@pytest.mark.parametrize(
    "sample",
    _fixtures()["validClientMessages"],
    ids=lambda sample: cast(JsonObject, sample)["name"],
)
def test_python_accepts_every_shared_valid_client_sample(
    sample: JsonObject,
) -> None:
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
    with pytest.raises(ProtocolValidationError):
        parse_server_message(sample["message"])


def test_all_server_message_builders_round_trip_through_the_parser() -> None:
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


def test_success_response_requires_a_non_null_request_id() -> None:
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


def test_machine_readable_schema_covers_every_protocol_message_kind() -> None:
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
    }.issubset(definitions)


def _project_state_response() -> JsonObject:
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
