"""Verify the path-free control contract for private desktop speech audio."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from desktop_protocol import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    VOICE_SPEECH_MAX_SEQUENCE,
    VOICE_SPEECH_MAX_WAV_BYTES,
    VOICE_SPEECH_MIN_WAV_BYTES,
    ProtocolValidationError,
    build_event,
    parse_server_message,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "desktop_protocol" / "schema" / "v1.schema.json"
JsonObject = dict[str, Any]


def _clip_event() -> JsonObject:
    """Return one valid clip event whose metadata can pair with one frame."""

    return {
        "type": "event",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "event": "voice.speech.clip",
        "requestId": "chat-voice-1",
        "data": {
            "chatId": "chat_fixture",
            "clipToken": "a" * 64,
            "sequence": 0,
            "byteLength": 32_044,
            "sha256": "b" * 64,
            "mediaType": "audio/wav",
        },
    }


def _terminal_event() -> JsonObject:
    """Return one valid completed speech-turn terminal event."""

    return {
        "type": "event",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "event": "voice.speech.terminal",
        "requestId": "chat-voice-1",
        "data": {
            "chatId": "chat_fixture",
            "state": "completed",
            "submittedSentences": 2,
            "completedSentences": 2,
            "failedSentences": 1,
        },
    }


def test_speech_event_builders_round_trip_without_private_details() -> None:
    """Build all three public speech events with their exact safe fields."""

    messages = [
        build_event(
            "voice.speech.clip",
            cast(JsonObject, _clip_event()["data"]),
            request_id="chat-voice-1",
        ),
        build_event(
            "voice.speech.failure",
            {
                "chatId": "chat_fixture",
                "sequence": 1,
                "code": "unavailable",
            },
            request_id="chat-voice-1",
        ),
        build_event(
            "voice.speech.terminal",
            cast(JsonObject, _terminal_event()["data"]),
            request_id="chat-voice-1",
        ),
    ]

    assert [parse_server_message(message) for message in messages] == messages
    encoded = json.dumps(messages)
    for private_field in ("text", "path", "profile", "cacheHit", "message"):
        assert f'"{private_field}"' not in encoded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clipToken", "A" * 64),
        ("clipToken", "a" * 63),
        ("sequence", -1),
        ("sequence", VOICE_SPEECH_MAX_SEQUENCE + 1),
        ("sequence", True),
        ("byteLength", VOICE_SPEECH_MIN_WAV_BYTES - 1),
        ("byteLength", VOICE_SPEECH_MAX_WAV_BYTES + 1),
        ("sha256", "B" * 64),
        ("mediaType", "audio/ogg"),
    ],
)
def test_speech_clip_event_rejects_untrusted_frame_metadata(
    field: str,
    value: object,
) -> None:
    """Reject metadata that cannot exactly match the binary reader's frame."""

    message = _clip_event()
    cast(JsonObject, message["data"])[field] = value

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


@pytest.mark.parametrize(
    "data",
    [
        {
            "chatId": "chat_fixture",
            "sequence": 0,
            "code": "native_error",
        },
        {
            "chatId": "chat_fixture",
            "sequence": 0,
            "code": "internal_error",
            "message": "D:/private/model failed",
        },
    ],
)
def test_speech_failure_event_is_a_closed_sanitized_enum(
    data: JsonObject,
) -> None:
    """Keep adapter diagnostics and future unknown codes off the wire."""

    with pytest.raises(ProtocolValidationError):
        build_event(
            "voice.speech.failure",
            data,
            request_id="chat-voice-1",
        )


@pytest.mark.parametrize(
    ("state", "submitted", "completed", "failed"),
    [
        ("completed", 2, 1, 0),
        ("completed", 1, 1, 2),
        ("cancelled", 1, 2, 0),
        ("cancelled", -1, 0, 0),
        ("failed", 1, 1, 0),
    ],
)
def test_speech_terminal_rejects_inconsistent_queue_accounting(
    state: object,
    submitted: object,
    completed: object,
    failed: object,
) -> None:
    """Reject impossible final counters before they affect playback state."""

    message = _terminal_event()
    data = cast(JsonObject, message["data"])
    data.update(
        state=state,
        submittedSentences=submitted,
        completedSentences=completed,
        failedSentences=failed,
    )

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


@pytest.mark.parametrize(
    "mutation",
    [
        {"requestId": None},
        {"event": "voice.private.diagnostic"},
        {"data": {"chatId": "chat_fixture", "path": "D:/private.wav"}},
    ],
)
def test_every_event_is_known_correlated_and_exact(
    mutation: JsonObject,
) -> None:
    """Disallow uncorrelated extensions and extra lifecycle-event fields."""

    message: JsonObject = {
        "type": "event",
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "event": "chat.started",
        "requestId": "chat-1",
        "data": {"chatId": "chat_fixture"},
    }
    message.update(deepcopy(mutation))

    with pytest.raises(ProtocolValidationError):
        parse_server_message(message)


def test_schema_declares_the_same_speech_event_bounds() -> None:
    """Keep portable schema limits aligned with both runtime validators."""

    schema = cast(JsonObject, json.loads(SCHEMA_PATH.read_text("utf-8")))
    event = cast(JsonObject, cast(JsonObject, schema["$defs"])["event"])
    branches = cast(list[JsonObject], event["oneOf"])
    clip = next(
        branch
        for branch in branches
        if cast(JsonObject, branch["properties"])["event"]
        == {"const": "voice.speech.clip"}
    )
    data = cast(
        JsonObject,
        cast(JsonObject, cast(JsonObject, clip["properties"])["data"])[
            "properties"
        ],
    )

    assert (
        cast(JsonObject, data["sequence"])["maximum"]
        == VOICE_SPEECH_MAX_SEQUENCE
    )
    assert (
        cast(JsonObject, data["byteLength"])["minimum"]
        == VOICE_SPEECH_MIN_WAV_BYTES
    )
    assert (
        cast(JsonObject, data["byteLength"])["maximum"]
        == VOICE_SPEECH_MAX_WAV_BYTES
    )
    Draft202012Validator.check_schema(schema)
