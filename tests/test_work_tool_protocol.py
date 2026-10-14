"""Test closed Work tool requests, portable paths, schemas, and safe results."""

from dataclasses import replace
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from work_tools.contracts import (
    ERROR_MESSAGES,
    MAX_TOOL_CONTENT_BYTES,
    TOOL_CATALOG,
    ToolCall,
    ToolProtocolError,
    ToolResult,
    descriptor_to_data,
    validate_tool_call,
    validate_tool_result,
)


@pytest.mark.parametrize("tool_id,payload", [
    ("read_text", {"path": "../outside.txt", "content": "x", "sizeBytes": 1}),
    ("metadata", {"path": "C:/Users/private.txt", "name": "private.txt", "kind": "file", "sizeBytes": 1}),
    ("search_files", {"matches": [{"path": "folder/../outside.txt"}], "truncated": False}),
])
def test_tool_outputs_cannot_publish_escaped_or_native_paths(tool_id: Any, payload: dict[str, Any]) -> None:
    """Python and Electron reject a defective adapter's unsafe result paths alike."""

    with pytest.raises(ToolProtocolError):
        validate_tool_result(tool_id, ToolResult("succeeded", payload))


_VALID_CALLS: list[dict[str, Any]] = [
    {"toolId": "list_directory", "arguments": {"path": ""}},
    {"toolId": "read_text", "arguments": {"path": "src/main.py"}},
    {"toolId": "search_files", "arguments": {"path": "src", "query": "main"}},
    {"toolId": "metadata", "arguments": {"path": ""}},
    {"toolId": "create", "arguments": {"path": "new.txt", "content": "新内容\n"}},
    {"toolId": "edit", "arguments": {"path": "old.txt", "content": ""}},
    {"toolId": "move", "arguments": {"path": "old.txt", "destination": "new.txt"}},
    {"toolId": "delete", "arguments": {"path": "old.txt"}},
]


@pytest.mark.parametrize("wire", _VALID_CALLS, ids=lambda wire: wire["toolId"])
def test_closed_tool_calls_round_trip_with_their_exact_input_schema(
    wire: dict[str, Any],
) -> None:
    """Accept each declared data operation without changing argument spelling."""

    call = validate_tool_call(wire)
    assert call.to_data() == wire
    descriptor = next(item for item in TOOL_CATALOG if item.tool_id == call.tool_id)
    Draft202012Validator(descriptor.input_schema).validate(call.arguments)
    detached = call.arguments
    detached["path"] = "different.txt"
    assert call.path == wire["arguments"]["path"]


@pytest.mark.parametrize("value", [
    None, [], "read_text file.txt", {},
    {"toolId": "shell", "arguments": {"command": "echo private"}},
    {"toolId": "read_text", "arguments": {"path": "file.txt"}, "command": "echo private"},
    {"toolId": "read_text", "arguments": {"path": "file.txt", "encoding": "utf-8"}},
    {"toolId": "create", "arguments": {"path": "file.txt"}},
    {"toolId": "move", "arguments": {"path": "file.txt", "destination": None}},
    {"toolId": "search_files", "arguments": {"path": "", "query": ""}},
    {"toolId": "search_files", "arguments": {"path": "", "query": "\nsecret"}},
    {"toolId": "search_files", "arguments": {"path": "", "query": "x" * 257}},
    {"toolId": "metadata", "arguments": {"path": 12}},
    {"toolId": ["metadata"], "arguments": {"path": ""}},
])
def test_tool_protocol_rejects_malformed_requests_without_executable_fallback(
    value: object,
) -> None:
    """Reject arbitrary commands and unexpected fields before an executor exists."""

    with pytest.raises(ToolProtocolError) as caught:
        validate_tool_call(value)
    assert str(caught.value) in ERROR_MESSAGES.values()
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("path", [
    "../private.txt", "src/../../private.txt", "/private/file.txt", "C:/private/file.txt",
    "//server/share/file.txt", "file.txt:secret", "src\\file.txt", "src//file.txt",
    "src/./file.txt", "CON.txt", "file.txt.", "file.txt ", "file\x00.txt",
])
def test_tool_paths_use_the_shared_portable_workspace_boundary(path: str) -> None:
    """Reject traversal, aliases, devices, and platform-specific escape spellings."""

    with pytest.raises(ToolProtocolError):
        validate_tool_call({"toolId": "read_text", "arguments": {"path": path}})


@pytest.mark.parametrize("tool_id", ["read_text", "create", "edit", "move", "delete"])
def test_leaf_operations_cannot_address_the_workspace_root(tool_id: str) -> None:
    """Keep directory-root addressing limited to explicitly read-only directory tools."""

    arguments = {"path": ""}
    if tool_id in {"create", "edit"}:
        arguments["content"] = "text"
    elif tool_id == "move":
        arguments["destination"] = "new.txt"
    with pytest.raises(ToolProtocolError):
        validate_tool_call({"toolId": tool_id, "arguments": arguments})


def test_utf8_content_limits_measure_bytes_and_accept_empty_files() -> None:
    """Treat multibyte text at the exact byte limit consistently with disk reads."""

    exact = "中" * (MAX_TOOL_CONTENT_BYTES // 3) + "x"
    assert len(exact.encode("utf-8")) == MAX_TOOL_CONTENT_BYTES
    assert ToolCall("create", "new.txt", content=exact).content == exact
    assert ToolCall("edit", "old.txt", content="").content == ""
    for invalid in (exact + "x", "x\x00y", "\ud800", b"not text"):
        with pytest.raises(ToolProtocolError):
            validate_tool_call({"toolId": "create", "arguments": {"path": "file.txt", "content": invalid}})


def test_direct_tool_calls_cannot_bypass_input_validation() -> None:
    """Keep the typed constructor closed when callers bypass the wire parser."""

    with pytest.raises(ToolProtocolError):
        ToolCall("read_text", "safe.txt", content="unexpected")
    with pytest.raises(ToolProtocolError):
        ToolCall("move", "safe.txt")
    with pytest.raises(ToolProtocolError):
        ToolCall("metadata", "", destination="out.txt")
    with pytest.raises(ToolProtocolError):
        ToolCall("search_files", "", query="x", content="unexpected")


def test_catalog_is_closed_documented_and_returns_isolated_schemas() -> None:
    """Expose all declared risks and limits without mutable policy state."""

    assert {item.tool_id for item in TOOL_CATALOG} == {wire["toolId"] for wire in _VALID_CALLS}
    for descriptor in TOOL_CATALOG:
        serialized = descriptor_to_data(descriptor)
        assert set(serialized) == {
            "toolId", "name", "description", "inputSchema", "outputSchema", "risk", "timeoutSeconds",
        }
        assert descriptor.name and descriptor.description
        assert 0 < descriptor.timeout_seconds <= 20
        assert descriptor.risk in {"low", "medium", "high", "critical"}
        Draft202012Validator.check_schema(descriptor.input_schema)
        Draft202012Validator.check_schema(descriptor.output_schema)
        serialized["inputSchema"]["properties"].clear()
        assert "path" in descriptor.input_schema["properties"]
    with pytest.raises(ToolProtocolError):
        descriptor_to_data(replace(TOOL_CATALOG[0], timeout_seconds=999))


@pytest.mark.parametrize("status,code", [
    ("denied", "permission_denied"), ("cancelled", "cancelled"), ("failed", "io_error"),
])
def test_tool_result_terminal_errors_only_expose_fixed_public_messages(
    status: Any,
    code: str,
) -> None:
    """Serialize denied, cancelled, and failed outcomes without native exception text."""

    result = ToolResult(status, error_code=code).to_data()
    assert result == {"status": status, "data": None, "error": {"code": code, "message": ERROR_MESSAGES[code]}}
    for descriptor in TOOL_CATALOG:
        Draft202012Validator(descriptor.output_schema).validate(result)


def test_success_and_partial_result_schemas_preserve_typed_data() -> None:
    """Keep metadata and filename-search results explicit without content snippets."""

    metadata = {"path": "file.txt", "name": "file.txt", "kind": "file", "sizeBytes": 12}
    search = {"matches": [{"path": "src/main.py"}], "truncated": True}
    for tool_id, result in (
        ("metadata", ToolResult("succeeded", metadata)),
        ("search_files", ToolResult("partial", search, "timeout")),
    ):
        descriptor = next(item for item in TOOL_CATALOG if item.tool_id == tool_id)
        Draft202012Validator(descriptor.output_schema).validate(result.to_data())
    metadata["sizeBytes"] = 13
    detached = ToolResult("succeeded", metadata).to_data()
    detached["data"]["sizeBytes"] = 99
    assert metadata["sizeBytes"] == 13
    schema = next(item for item in TOOL_CATALOG if item.tool_id == "search_files").output_schema
    leaked = ToolResult("succeeded", {"matches": [{"path": "file.txt", "text": "secret"}], "truncated": False})
    assert not Draft202012Validator(schema).is_valid(leaked.to_data())
    assert validate_tool_result("metadata", ToolResult("succeeded", metadata))["data"] == metadata
    with pytest.raises(ToolProtocolError):
        validate_tool_result("search_files", leaked)
    with pytest.raises(ToolProtocolError):
        validate_tool_result("read_text", ToolResult("succeeded", metadata))


@pytest.mark.parametrize("status,data,code", [
    ("success", {}, None), ("succeeded", None, None), ("succeeded", {}, "io_error"),
    ("denied", {}, "permission_denied"), ("failed", None, None),
    ("failed", None, "raw C:/private/native error"), ("failed", None, ["io_error"]),
    ("succeeded", {"value": object()}, None), ("succeeded", {"value": float("nan")}, None),
    ("succeeded", {"value": 2**53}, None),
    ("succeeded", {"value": "x" * (MAX_TOOL_CONTENT_BYTES + 1)}, None),
])
def test_results_reject_invalid_statuses_and_unbounded_non_json_data(
    status: Any,
    data: Any,
    code: Any,
) -> None:
    """Reject unsafe values before producing any desktop protocol frame."""

    with pytest.raises(ToolProtocolError):
        ToolResult(status, data, code)


def test_result_serialization_bounds_depth_nodes_bytes_and_later_mutation() -> None:
    """Bound output allocation and revalidate mutable data every time it is sent."""

    nested: dict[str, Any] = {}
    for _ in range(14):
        nested = {"nested": nested}
    oversized = {"values": [None] * 10_001}
    excessive_bytes = {"values": ["中" * 60_000] * 6}
    for payload in (nested, oversized, excessive_bytes):
        with pytest.raises(ToolProtocolError):
            ToolResult("succeeded", payload)
    data = {"content": "safe"}
    result = ToolResult("succeeded", data)
    data["content"] = "\ud800"
    with pytest.raises(ToolProtocolError):
        result.to_data()
    assert json.loads(json.dumps(ToolResult("succeeded", {}).to_data()))["status"] == "succeeded"
