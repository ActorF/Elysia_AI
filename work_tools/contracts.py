"""Validate a closed, bounded Work tool protocol before filesystem execution.

The catalog contains data operations only. No tool accepts commands, programs,
URLs, or arbitrary model text as executable instructions. Lexical path checks
do not grant filesystem access; the executor must reacquire WorkspaceBoundary
and enforce the saved mode, binding, and confirmation for each action.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Final, Literal, Mapping, cast

from jsonschema import Draft202012Validator

from .workspace import WorkspaceBoundaryError, validate_relative_path


ToolId = Literal[
    "list_directory", "read_text", "search_files", "metadata",
    "create", "edit", "move", "delete",
]
ToolRisk = Literal["low", "medium", "high", "critical"]
ToolStatus = Literal["succeeded", "partial", "denied", "cancelled", "failed"]
JsonObject = dict[str, Any]
MAX_TOOL_CONTENT_BYTES: Final = 256 * 1024
MAX_TOOL_QUERY_CHARACTERS: Final = 256
MAX_TOOL_RESULT_BYTES: Final = 1024 * 1024
_MAX_RESULT_NODES = 10_000
_MAX_RESULT_DEPTH = 12
_TOOL_STATUSES = frozenset({"succeeded", "partial", "denied", "cancelled", "failed"})

# Only these fixed messages cross the desktop boundary. Exceptions from native
# adapters can include absolute paths, file contents, or process diagnostics.
ERROR_MESSAGES: Final[Mapping[str, str]] = MappingProxyType({
    "invalid_request": "The tool request is invalid.",
    "unsupported_tool": "The requested tool is unavailable.",
    "work_mode_required": "Workspace tools require explicit Work mode.",
    "workspace_required": "Choose a Workspace before using tools.",
    "path_denied": "The requested path is outside the permitted scope or unsafe.",
    "sensitive_file": "The requested file is restricted.",
    "file_too_large": "The file exceeds the permitted size.",
    "binary_file": "Only UTF-8 text files are supported.",
    "not_found": "The requested file does not exist.",
    "already_exists": "The destination already exists.",
    "permission_required": "The action requires explicit approval.",
    "permission_denied": "The action was declined.",
    "cancelled": "The action was cancelled.",
    "timeout": "The tool exceeded its time limit.",
    "io_error": "The local file operation failed.",
    "conflict": "The file changed; prepare the action again.",
    "undo_unavailable": "The action can no longer be undone safely.",
    "verification_failed": "The written result could not be verified.",
})


class ToolProtocolError(ValueError):
    """Carry one fixed protocol error without retaining untrusted input text."""

    def __init__(self, code: str = "invalid_request") -> None:
        """Require a known safe error code and its fixed public message."""

        if type(code) is not str or code not in ERROR_MESSAGES:
            raise ValueError("Unknown tool error code.")
        self.code = code
        super().__init__(ERROR_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Describe one closed tool's schemas, risk, and execution time limit."""

    tool_id: ToolId
    name: str
    description: str
    risk: ToolRisk
    timeout_seconds: int

    @property
    def input_schema(self) -> JsonObject:
        """Return an isolated exact-key schema for this tool's arguments."""

        properties: JsonObject = {
            "path": {"type": "string", "maxLength": 4096},
        }
        if self.tool_id in {"create", "edit"}:
            properties["content"] = {
                "type": "string", "maxLength": MAX_TOOL_CONTENT_BYTES,
                "description": "Valid UTF-8 text, at most 262144 encoded bytes, no NUL.",
            }
        elif self.tool_id == "move":
            properties["destination"] = {"type": "string", "minLength": 1, "maxLength": 4096}
        elif self.tool_id == "search_files":
            properties["query"] = {
                "type": "string", "minLength": 1, "maxLength": MAX_TOOL_QUERY_CHARACTERS,
            }
        if self.tool_id not in {"list_directory", "metadata", "search_files"}:
            properties["path"]["minLength"] = 1
        return _object_schema(properties)

    @property
    def output_schema(self) -> JsonObject:
        """Describe typed successful data and bounded safe terminal errors."""

        error: JsonObject = {"oneOf": [
            _object_schema({"code": {"const": code}, "message": {"const": message}})
            for code, message in ERROR_MESSAGES.items()
        ]}
        data = _data_schema(self.tool_id)
        schema = _object_schema({
            "status": {"enum": ["succeeded", "partial", "denied", "cancelled", "failed"]},
            "data": {"anyOf": [data, {"type": "null"}]},
            "error": {"anyOf": [error, {"type": "null"}]},
        })
        schema["allOf"] = [
            {
                "if": {"properties": {"status": {"enum": ["succeeded", "partial"]}}},
                # The base properties already validate the complete payload.
                # These branches only select nullability, avoiding a second
                # copy of every data/error schema in each catalog descriptor.
                "then": {"properties": {"data": {"not": {"type": "null"}}}},
                "else": {"properties": {"data": {"type": "null"}, "error": {"not": {"type": "null"}}}},
            },
            {
                "if": {"properties": {"status": {"const": "succeeded"}}},
                "then": {"properties": {"error": {"type": "null"}}},
            },
        ]
        return schema


TOOL_CATALOG: Final[tuple[ToolDescriptor, ...]] = (
    ToolDescriptor("list_directory", "列出目录", "列出工作区目录的有界项目和元数据。", "low", 10),
    ToolDescriptor("read_text", "读取文本", "读取有界 UTF-8 文本，不修改文件。", "low", 10),
    ToolDescriptor("search_files", "搜索文件", "按字面名称搜索有界工作区路径，不读取文件正文。", "low", 20),
    ToolDescriptor("metadata", "查看元数据", "查看文件或目录的安全元数据，不读取正文。", "low", 10),
    ToolDescriptor("create", "创建文件", "预览并批准后创建一个 UTF-8 文本文件。", "medium", 20),
    ToolDescriptor("edit", "编辑文件", "预览并批准后替换文本，保留可恢复的旧版本。", "medium", 20),
    ToolDescriptor("move", "移动或重命名", "预览并批准后移动一个文件，不覆盖已有目标。", "high", 20),
    ToolDescriptor("delete", "移入恢复区", "明确确认后将一个文件移入可恢复区域。", "high", 20),
)
_DESCRIPTORS = {descriptor.tool_id: descriptor for descriptor in TOOL_CATALOG}


def _object_schema(properties: JsonObject) -> JsonObject:
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


def _data_schema(tool_id: ToolId) -> JsonObject:
    """Keep result metadata relative and avoid native path or identity disclosure."""

    path = {"type": "string", "maxLength": 4096}
    entry = _object_schema({
        "path": path, "name": {"type": "string", "maxLength": 4096},
        "kind": {"enum": ["file", "directory"]},
        "sizeBytes": {"type": "integer", "minimum": 0},
    })
    if tool_id == "list_directory":
        return _object_schema({
            "entries": {"type": "array", "items": entry, "maxItems": 1000},
            "truncated": {"type": "boolean"},
        })
    if tool_id == "read_text":
        return _object_schema({
            "path": path, "content": {"type": "string", "maxLength": MAX_TOOL_CONTENT_BYTES},
            "sizeBytes": {"type": "integer", "minimum": 0, "maximum": MAX_TOOL_CONTENT_BYTES},
        })
    if tool_id == "search_files":
        match = _object_schema({"path": path})
        return _object_schema({
            "matches": {"type": "array", "items": match, "maxItems": 500},
            "truncated": {"type": "boolean"},
        })
    if tool_id == "metadata":
        return entry
    properties: JsonObject = {"path": path, "undoId": {"type": "string", "maxLength": 128}}
    if tool_id == "move":
        properties["destination"] = path
    return _object_schema(properties)


def descriptor_to_data(descriptor: ToolDescriptor) -> JsonObject:
    """Serialize one catalog entry with fresh schemas safe for UI inspection."""

    if descriptor not in TOOL_CATALOG:
        raise ToolProtocolError("unsupported_tool")
    return {
        "toolId": descriptor.tool_id, "name": descriptor.name,
        "description": descriptor.description,
        "inputSchema": descriptor.input_schema,
        "outputSchema": descriptor.output_schema,
        "risk": descriptor.risk, "timeoutSeconds": descriptor.timeout_seconds,
    }


def _utf8_text(value: object, *, maximum_bytes: int, allow_empty: bool = True) -> str:
    """Reject binary sentinels and lone surrogates before any disk operation."""

    if type(value) is not str or len(value) > maximum_bytes or "\x00" in value:
        raise ToolProtocolError()
    if not allow_empty and not value.strip():
        raise ToolProtocolError()
    try:
        if len(value.encode("utf-8")) > maximum_bytes:
            raise ToolProtocolError()
    except UnicodeEncodeError:
        raise ToolProtocolError() from None
    return value


def validate_plan_title(value: object) -> str:
    """Accept at most 200 Unicode code points with one protocol-nonblank character.

    Python's whitespace table differs from JavaScript's. This explicit set is
    the desktop protocol's Unicode White_Space plus FEFF definition, preserving
    the same title in direct runner calls and both wire validators.
    """

    text = _utf8_text(value, maximum_bytes=800)
    blank = "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
    if len(text) > 200 or not text.strip(blank):
        raise ToolProtocolError()
    return text


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Hold one validated data operation with typed optional argument fields."""

    tool_id: ToolId
    path: str
    content: str | None = None
    destination: str | None = None
    query: str | None = None

    def __post_init__(self) -> None:
        """Enforce the same closed contract for direct and wire construction."""

        if type(self.tool_id) is not str or self.tool_id not in _DESCRIPTORS:
            raise ToolProtocolError("unsupported_tool")
        allow_root = self.tool_id in {"list_directory", "metadata", "search_files"}
        _utf8_text(self.path, maximum_bytes=16_384)
        try:
            validate_relative_path(self.path, allow_root=allow_root)
            if self.destination is not None:
                _utf8_text(self.destination, maximum_bytes=16_384)
                validate_relative_path(self.destination)
        except WorkspaceBoundaryError:
            raise ToolProtocolError("path_denied") from None
        if self.tool_id in {"create", "edit"}:
            _utf8_text(self.content, maximum_bytes=MAX_TOOL_CONTENT_BYTES)
        elif self.content is not None:
            raise ToolProtocolError()
        if (self.tool_id == "move") != (self.destination is not None):
            raise ToolProtocolError()
        if self.tool_id == "search_files":
            query = _utf8_text(self.query, maximum_bytes=1024, allow_empty=False)
            if len(query) > MAX_TOOL_QUERY_CHARACTERS or any(ord(char) < 32 for char in query):
                raise ToolProtocolError()
        elif self.query is not None:
            raise ToolProtocolError()

    @property
    def arguments(self) -> dict[str, str]:
        """Return this call's exact canonical argument keys without a shared dict."""

        arguments = {"path": self.path}
        for name, value in (("content", self.content), ("destination", self.destination), ("query", self.query)):
            if value is not None:
                arguments[name] = value
        return arguments

    def to_data(self) -> JsonObject:
        """Serialize only the tool identifier and validated canonical arguments."""

        return {"toolId": self.tool_id, "arguments": self.arguments}


def validate_tool_call(value: object) -> ToolCall:
    """Parse one exact-key tool request; reject unsupported or oversized inputs."""

    if type(value) is not dict or set(value) != {"toolId", "arguments"}:
        raise ToolProtocolError()
    tool_id = value["toolId"]
    if type(tool_id) is not str or tool_id not in _DESCRIPTORS:
        raise ToolProtocolError("unsupported_tool")
    arguments = value["arguments"]
    descriptor = _DESCRIPTORS[cast(ToolId, tool_id)]
    required = set(descriptor.input_schema["required"])
    if type(arguments) is not dict or set(arguments) != required:
        raise ToolProtocolError()
    return ToolCall(
        tool_id=cast(ToolId, tool_id), path=arguments["path"],
        content=arguments.get("content"),
        destination=arguments.get("destination"), query=arguments.get("query"),
    )


def _validate_json_result(value: object, *, depth: int, budget: list[int]) -> None:
    """Bound nested result traversal before serialization can allocate large output."""

    budget[0] -= 1
    if budget[0] < 0 or depth > _MAX_RESULT_DEPTH:
        raise ToolProtocolError()
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 2**53 - 1:
            raise ToolProtocolError()
        return
    if type(value) is str:
        _utf8_text(value, maximum_bytes=MAX_TOOL_CONTENT_BYTES)
    elif type(value) is list:
        for item in value:
            _validate_json_result(item, depth=depth + 1, budget=budget)
    elif type(value) is dict:
        for key, item in value.items():
            _utf8_text(key, maximum_bytes=4096, allow_empty=False)
            _validate_json_result(item, depth=depth + 1, budget=budget)
    else:
        raise ToolProtocolError()


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Represent bounded success, partial progress, denial, cancellation, or failure."""

    status: ToolStatus
    data: JsonObject | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Validate status/error consistency and require a bounded JSON payload."""

        if type(self.status) is not str or self.status not in _TOOL_STATUSES:
            raise ToolProtocolError()
        if self.error_code is not None and (
            type(self.error_code) is not str or self.error_code not in ERROR_MESSAGES
        ):
            raise ToolProtocolError()
        if self.status == "succeeded" and self.error_code is not None:
            raise ToolProtocolError()
        if self.status in {"denied", "cancelled", "failed"} and self.error_code is None:
            raise ToolProtocolError()
        if self.status in {"succeeded", "partial"} and type(self.data) is not dict:
            raise ToolProtocolError()
        if self.status in {"denied", "cancelled", "failed"} and self.data is not None:
            raise ToolProtocolError()
        self.to_data()

    def to_data(self) -> JsonObject:
        """Return a fresh JSON payload with fixed error text and strict byte limits."""

        _validate_json_result(self.data, depth=0, budget=[_MAX_RESULT_NODES])
        result: JsonObject = {
            "status": self.status, "data": self.data,
            "error": None if self.error_code is None else {
                "code": self.error_code, "message": ERROR_MESSAGES[self.error_code],
            },
        }
        # A round trip detaches caller-owned data so a renderer snapshot cannot
        # mutate the next result; UTF-8 bytes, not code points, bound the frame.
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
            raise ToolProtocolError()
        return cast(JsonObject, json.loads(encoded))


def validate_tool_result(tool_id: ToolId, result: ToolResult) -> JsonObject:
    """Serialize an outcome only when it satisfies that tool's output schema.

    Executors use this final gate before returning their result. ToolResult
    checks frame bounds independently; this gate catches mismatched data keys
    or accidental content snippets in filename-only search results.
    """

    if type(tool_id) is not str or tool_id not in _DESCRIPTORS:
        raise ToolProtocolError("unsupported_tool")
    if not isinstance(result, ToolResult):
        raise ToolProtocolError()
    data = result.to_data()
    if not Draft202012Validator(_DESCRIPTORS[tool_id].output_schema).is_valid(data):
        raise ToolProtocolError()
    payload = data["data"]
    if payload is not None:
        # JSON Schema covers field shapes. Reuse the execution path grammar
        # here so a defective adapter cannot publish native or escaped paths.
        entries = payload["entries"] if tool_id == "list_directory" else payload["matches"] if tool_id == "search_files" else [payload]
        try:
            for entry in entries:
                validate_relative_path(entry["path"], allow_root=(tool_id == "metadata" and entry.get("kind") == "directory"))
                if "destination" in entry:
                    validate_relative_path(entry["destination"])
                if "name" in entry:
                    validate_relative_path(entry["name"])
                    if "/" in entry["name"]:
                        raise WorkspaceBoundaryError("Result names must be single components.")
        except WorkspaceBoundaryError:
            raise ToolProtocolError("path_denied") from None
    return data
