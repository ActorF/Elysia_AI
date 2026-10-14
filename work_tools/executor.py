"""Execute bounded read-only Work tools without hidden filesystem writes.

The executor receives an identity-pinned WorkspaceBoundary from the desktop
coordinator. Every native operation goes through that boundary; saved mode and
binding authorization remain the coordinator's responsibility. Cancellation and
deadlines are cooperative: local native I/O is checked before and after each
call, rather than pretending an in-flight OS read can be safely terminated.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import PurePosixPath
import re
from time import monotonic
from typing import Any

from .contracts import (
    MAX_TOOL_CONTENT_BYTES,
    MAX_TOOL_RESULT_BYTES,
    TOOL_CATALOG,
    ToolCall,
    ToolProtocolError,
    ToolResult,
    validate_tool_call,
    validate_tool_result,
)
from .workspace import WorkspaceBoundary, WorkspaceBoundaryError, WorkspaceEntry


_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})
_MAX_DIRECTORY_ENTRIES = 1000
_MAX_SEARCH_VISITED = 5000
_MAX_SEARCH_RESULTS = 500
_RESULT_DATA_BUDGET = MAX_TOOL_RESULT_BYTES - 4096
_TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".rst", ".py", ".pyi", ".pyw", ".ts", ".tsx",
    ".js", ".jsx", ".mjs", ".cjs", ".css", ".html", ".htm", ".json",
    ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".csv", ".tsv", ".log", ".sql", ".ps1", ".psm1", ".sh",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
})
_TEXT_NAMES = frozenset({
    "readme", "license", "licence", "notice", "dockerfile", "makefile",
    ".gitignore", ".gitattributes", ".editorconfig",
})
_PRIVATE_COMPONENTS = frozenset({
    ".git", ".ssh", ".aws", ".azure", ".gcp", ".gnupg", ".kube",
    "secrets", "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "known_hosts", "authorized_keys", "private_key", "private-key",
})
_PRIVATE_SUFFIXES = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der",
    ".jks", ".keystore", ".kdbx",
})
_PRIVATE_WORD = re.compile(r"(?:^|[._-])(?:secrets?|credentials?|passwords?|tokens?)(?:$|[._-])")


def is_sensitive_path(relative_path: str) -> bool:
    """Identify restricted path components without opening their files.

    Matching components, rather than a filename alone, also protects files
    nested under a private directory. Names are case-folded because Windows
    treats alternate casing as the same file. The rule intentionally covers
    .env examples and public certificates: the initial tools have no reliable
    way to distinguish harmless samples from actual credentials.
    """

    for component in relative_path.split("/"):
        lowered = component.casefold()
        if (
            lowered in _PRIVATE_COMPONENTS or lowered.startswith(".env")
            or PurePosixPath(lowered).suffix in _PRIVATE_SUFFIXES
            or _PRIVATE_WORD.search(lowered) is not None
        ):
            return True
    return False


def is_supported_text_path(relative_path: str) -> bool:
    """Require an explicit text suffix or conventional extensionless text name."""

    name = PurePosixPath(relative_path).name.casefold()
    return name in _TEXT_NAMES or PurePosixPath(name).suffix in _TEXT_SUFFIXES


class _Stopped(Exception):
    """Unwind owned read capabilities on a cooperative cancellation or timeout."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ReadGuard:
    """Apply one request deadline across every directory and result checkpoint."""

    def __init__(self, timeout_seconds: int, should_cancel: Callable[[], bool] | None) -> None:
        self._deadline = monotonic() + timeout_seconds
        self._should_cancel = should_cancel

    def check(self) -> None:
        """Check cancellation first so an explicit stop wins a concurrent deadline."""

        if self._should_cancel is not None and self._should_cancel():
            raise _Stopped("cancelled")
        if monotonic() >= self._deadline:
            raise _Stopped("timeout")


def _entry_data(entry: WorkspaceEntry) -> dict[str, Any]:
    return {"path": entry.path, "name": entry.name, "kind": entry.kind, "sizeBytes": entry.size_bytes}


class WorkToolExecutor:
    """Run four read-only tools; write tools remain behind explicit permission."""

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        """Store a validated native capability without creating caches or state."""

        if not isinstance(boundary, WorkspaceBoundary):
            raise TypeError("A WorkspaceBoundary is required.")
        self._boundary = boundary

    def run(
        self,
        call: ToolCall,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ToolResult:
        """Return a typed safe outcome for one bounded data operation.

        Even frozen DTOs can be forged by an internal caller, so the wire
        validator runs again before filesystem access. Every exception maps to
        a closed public error; native exception strings never enter a result.
        """

        try:
            if type(call) is not ToolCall or (should_cancel is not None and not callable(should_cancel)):
                raise ToolProtocolError()
            checked = validate_tool_call(call.to_data())
            if checked.tool_id not in _READ_TOOLS:
                return ToolResult("denied", error_code="permission_required")
            # A user can select a private directory itself as the root. Empty
            # relative paths must not erase that ancestor's sensitive name.
            if is_sensitive_path(self._boundary.root.as_posix()) or is_sensitive_path(checked.path):
                return ToolResult("denied", error_code="sensitive_file")
            descriptor = next(item for item in TOOL_CATALOG if item.tool_id == checked.tool_id)
            guard = _ReadGuard(descriptor.timeout_seconds, should_cancel)
            guard.check()
            if checked.tool_id == "read_text":
                result = self._read_text(checked, guard)
            elif checked.tool_id == "metadata":
                result = ToolResult("succeeded", _entry_data(self._boundary.metadata(checked.path)))
            elif checked.tool_id == "list_directory":
                result = self._list_directory(checked, guard)
            else:
                result = self._search_files(checked, guard)
            try:
                guard.check()
            except _Stopped as stopped:
                # A filename search can return a bounded prefix that already
                # records its expired deadline. Cancellation still discards
                # that prefix, and no filesystem work follows this checkpoint.
                if not (stopped.code == "timeout" and result.status == "partial" and result.error_code == "timeout"):
                    raise
            validate_tool_result(checked.tool_id, result)
            return result
        except ToolProtocolError as error:
            return ToolResult("denied", error_code=error.code)
        except _Stopped as stopped:
            return ToolResult(
                "cancelled" if stopped.code == "cancelled" else "failed",
                error_code=stopped.code,
            )
        except WorkspaceBoundaryError as error:
            # Boundary messages are fixed trusted vocabulary. Only classify
            # those known cases; never serialize the original exception.
            message = str(error)
            if "exceeds" in message and ("read limit" in message or "entry limit" in message):
                return ToolResult("denied", error_code="file_too_large")
            if message == "Work path does not exist.":
                return ToolResult("failed", error_code="not_found")
            return ToolResult("denied", error_code="path_denied")
        except Exception:
            return ToolResult("failed", error_code="io_error")

    def _read_text(self, call: ToolCall, guard: _ReadGuard) -> ToolResult:
        if not is_supported_text_path(call.path):
            return ToolResult("denied", error_code="binary_file")
        guard.check()
        # This preflight gives useful size/type errors; read_bytes independently
        # pins and validates the actual opened file, so this metadata snapshot
        # cannot authorize a replacement that races with the subsequent read.
        entry = self._boundary.metadata(call.path)
        if entry.kind != "file":
            return ToolResult("denied", error_code="binary_file")
        if entry.size_bytes > MAX_TOOL_CONTENT_BYTES:
            return ToolResult("denied", error_code="file_too_large")
        guard.check()
        raw = self._boundary.read_bytes(call.path, max_bytes=MAX_TOOL_CONTENT_BYTES)
        guard.check()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult("denied", error_code="binary_file")
        if "\x00" in text or any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            return ToolResult("denied", error_code="binary_file")
        return ToolResult("succeeded", {"path": call.path, "content": text, "sizeBytes": len(raw)})

    def _list_directory(self, call: ToolCall, guard: _ReadGuard) -> ToolResult:
        guard.check()
        entries = self._boundary.list_directory(call.path, max_entries=_MAX_DIRECTORY_ENTRIES)
        visible: list[dict[str, Any]] = []
        byte_count = 0
        truncated = False
        for entry in entries:
            guard.check()
            if not is_sensitive_path(entry.path):
                data = _entry_data(entry)
                entry_bytes = len(json.dumps(data, ensure_ascii=False).encode("utf-8")) + 2
                if byte_count + entry_bytes > _RESULT_DATA_BUDGET:
                    truncated = True
                    break
                visible.append(data)
                byte_count += entry_bytes
        return ToolResult("partial" if truncated else "succeeded", {"entries": visible, "truncated": truncated})

    def _search_files(self, call: ToolCall, guard: _ReadGuard) -> ToolResult:
        """Use an iterative bounded traversal with literal path matching only.

        A stack avoids Python recursion for deep directories. Sensitive
        subtrees are never added to it, and boundary listings omit links and
        unsafe files. Per-directory native enumeration, visited entries, and
        result counts all have independent limits; no file contents are read.
        """

        assert call.query is not None
        query = call.query.casefold()
        pending = [call.path]
        visited = 0
        matches: list[dict[str, str]] = []
        truncated = False
        error_code: str | None = None
        byte_count = 0
        try:
            while pending:
                guard.check()
                current = pending.pop()
                if visited >= _MAX_SEARCH_VISITED:
                    truncated = True
                    break
                try:
                    entries = self._boundary.list_directory(
                        current, max_entries=min(_MAX_DIRECTORY_ENTRIES, _MAX_SEARCH_VISITED - visited),
                    )
                except WorkspaceBoundaryError as error:
                    if "exceeds the entry limit" not in str(error):
                        raise
                    truncated = True
                    break
                children: list[str] = []
                for entry in entries:
                    guard.check()
                    visited += 1
                    if is_sensitive_path(entry.path):
                        continue
                    if entry.kind == "directory":
                        children.append(entry.path)
                    elif query in entry.path.casefold():
                        match = {"path": entry.path}
                        match_bytes = len(json.dumps(match, ensure_ascii=False).encode("utf-8")) + 2
                        if len(matches) == _MAX_SEARCH_RESULTS or byte_count + match_bytes > _RESULT_DATA_BUDGET:
                            truncated = True
                            break
                        matches.append(match)
                        byte_count += match_bytes
                if truncated:
                    break
                # Reverse sorted children so stack traversal remains stable;
                # deterministic output makes a user's repeated search useful.
                pending.extend(reversed(children))
        except _Stopped as stopped:
            if stopped.code == "cancelled":
                raise
            truncated = True
            error_code = "timeout"
        return ToolResult(
            "partial" if truncated else "succeeded",
            {"matches": matches, "truncated": truncated},
            error_code=error_code,
        )
