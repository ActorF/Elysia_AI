"""Coordinate single-use Work approvals, durable before-images, safe audit, and conditional Undo.

Approvals bind exact parameters, Chat/Project owner, selected-root identity, and
the original file digest. They expire after five minutes and disappear on
restart. Original private bytes live only in bounded transaction backups;
public audit entries contain relative targets and content lengths/digests.

The journal commits an intent before opening a mutation handle. A failed or
interrupted operation remains recoverable and never triggers an automatic
rollback over potentially unrelated external edits. Undo is a separate explicit
user action, checks the current filesystem again, and durably consumes its own
right before executing so a crash cannot silently replay it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from .contracts import (
    MAX_TOOL_CONTENT_BYTES, TOOL_CATALOG, ToolCall, ToolProtocolError, ToolResult,
    validate_tool_call, validate_tool_result,
)
from .executor import is_sensitive_path, is_supported_text_path
from .mutations import WorkspaceMutations
from .workspace import WorkspaceBoundary, WorkspaceBoundaryError, validate_relative_path


_WRITE_TOOLS = frozenset({"create", "edit", "move", "delete"})
_TRANSACTION_ID = re.compile(r"undo_[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PREVIEW_BYTES = 32 * 1024
_MAX_RECORD_BYTES = 64 * 1024
_MAX_RECORDS = 1000
_MAX_AUDIT_PAGE_BYTES = 512 * 1024
_MAX_AUDIT_PAGE_ENTRIES = 100
_RECORD_FIELDS = frozenset({
    "version", "undoId", "timestamp", "chatId", "projectId", "rootPath", "rootIdentity",
    "toolId", "path", "destination", "beforeHash", "afterHash", "parameters",
    "result", "error", "undoState", "recoverable",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scope(chat_id: str, project_id: str | None) -> None:
    if type(chat_id) is not str or re.fullmatch(r"chat_[A-Za-z0-9_-]{1,123}", chat_id) is None:
        raise ToolProtocolError()
    if project_id is not None and (
        type(project_id) is not str or re.fullmatch(r"project_[A-Za-z0-9_-]{1,120}", project_id) is None
    ):
        raise ToolProtocolError()


def _text_file(boundary: WorkspaceBoundary, path: str) -> bytes:
    """Require an allowlisted nonsensitive UTF-8 text file before preview or backup."""

    if is_sensitive_path(boundary.root.as_posix()) or is_sensitive_path(path):
        raise ToolProtocolError("sensitive_file")
    if not is_supported_text_path(path):
        raise ToolProtocolError("binary_file")
    raw = boundary.read_bytes(path, max_bytes=MAX_TOOL_CONTENT_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolProtocolError("binary_file") from None
    if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        raise ToolProtocolError("binary_file")
    return raw


def _validate_target(path: str) -> None:
    validate_relative_path(path)
    if is_sensitive_path(path):
        raise ToolProtocolError("sensitive_file")
    if not is_supported_text_path(path):
        raise ToolProtocolError("binary_file")


def _absent(boundary: WorkspaceBoundary, path: str) -> bool:
    with boundary.guarded_parent(path) as access:
        return access.stat_leaf() is None


def _preview(call: ToolCall, before: bytes | None) -> str:
    """Bound a truthful diff, adding an explicit marker whenever displayed changes are incomplete."""

    if call.tool_id == "move":
        return f"Move {call.path} to {call.destination}. File contents are unchanged."
    old = b"" if before is None else before
    new = (call.content or "").encode("utf-8") if call.tool_id in {"create", "edit"} else b""
    impact = f"{call.tool_id.capitalize()} {call.path}: {len(old)} bytes -> {len(new)} bytes.\n"
    marker = "\n[Preview truncated; the complete parameters remain visible above.]\n"
    budget = _MAX_PREVIEW_BYTES - len(marker.encode("utf-8"))
    result = bytearray(impact.encode("utf-8"))
    for line in difflib.unified_diff(
        old.decode("utf-8").splitlines(keepends=True), new.decode("utf-8").splitlines(keepends=True),
        fromfile=call.path, tofile=call.path, n=3,
    ):
        encoded = line.encode("utf-8")
        if len(result) + len(encoded) > budget:
            result.extend(encoded[:max(0, budget - len(result))])
            return result.decode("utf-8", errors="ignore") + marker
        result.extend(encoded)
    return result.decode("utf-8")


@dataclass(frozen=True, slots=True)
class _Pending:
    """Freeze the exact owner, content, scope, and time that one approval can authorize."""

    chat_id: str
    project_id: str | None
    root_path: str
    root_identity: tuple[int, int]
    call: ToolCall
    before: bytes | None
    expires_at: datetime
    deadline: float


class WriteCoordinator:
    """Own explicit single-use approvals and private durable recovery for bounded text-file writes."""

    def __init__(self, private_directory: Path, *, clock: Callable[[], datetime] = _now) -> None:
        """Initialize one trusted private transaction directory and an empty approval table."""

        if not isinstance(private_directory, Path) or not private_directory.is_absolute() or not callable(clock):
            raise ToolProtocolError()
        self._clock = clock
        self._lock = RLock()
        # Revoking pending authorization must not wait for admitted native I/O.
        # Execution may acquire this short-lived mutex, never the reverse order.
        self._pending_lock = RLock()
        self._pending: dict[str, _Pending] = {}
        self._approval_epochs: dict[str, int] = {}
        # Create only fixed application-owned directories, pinning the existing
        # parent before each mkdir rather than recursively following aliases.
        missing: list[Path] = []
        parent = private_directory
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        boundary = WorkspaceBoundary(parent)
        for path in reversed(missing):
            with boundary.guarded_parent(path.name) as access:
                os.mkdir(access.path if access.directory_fd is None else access.leaf_name, 0o700, dir_fd=access.directory_fd)
            boundary = WorkspaceBoundary(path)
        self._storage = WorkspaceBoundary(private_directory)
        with self._storage.guarded_parent("transactions") as access:
            if access.stat_leaf() is None:
                os.mkdir(access.path if access.directory_fd is None else access.leaf_name, 0o700, dir_fd=access.directory_fd)
        # A replaced or linked transaction root must fail before any record is
        # read, even when the containing private application root remains valid.
        if self._storage.metadata("transactions").kind != "directory":
            raise ToolProtocolError("io_error")

    def preview(
        self, chat_id: str, project_id: str | None, boundary: WorkspaceBoundary, call: ToolCall,
    ) -> dict[str, Any]:
        """Prepare a bounded diff and an expiring exact-action approval without changing workspace files."""

        _scope(chat_id, project_id)
        if type(call) is not ToolCall:
            raise ToolProtocolError()
        checked = validate_tool_call(call.to_data())
        if checked.tool_id not in _WRITE_TOOLS:
            raise ToolProtocolError("unsupported_tool")
        if is_sensitive_path(boundary.root.as_posix()):
            raise ToolProtocolError("sensitive_file")
        if checked.content is not None and any(ord(char) < 32 and char not in "\t\n\r" for char in checked.content):
            raise ToolProtocolError("binary_file")
        _validate_target(checked.path)
        if checked.destination is not None:
            _validate_target(checked.destination)
            if checked.destination == checked.path:
                raise ToolProtocolError("invalid_request")
        with self._pending_lock:
            preview_epoch = self._approval_epochs.get(chat_id, 0)
        # Force a fresh native root check before capturing anything for approval.
        boundary.metadata()
        before = None if checked.tool_id == "create" else _text_file(boundary, checked.path)
        if checked.tool_id == "create" and not _absent(boundary, checked.path):
            raise ToolProtocolError("already_exists")
        if checked.destination is not None and not _absent(boundary, checked.destination):
            raise ToolProtocolError("already_exists")
        preview_text = _preview(checked, before)
        current_time = self._clock()
        if current_time.tzinfo is None:
            raise ToolProtocolError()
        expires = current_time + timedelta(minutes=5)
        token = "approval_" + uuid4().hex
        with self._pending_lock:
            # Invalidation can win while native preview I/O is outside this
            # mutex. A late preview must not resurrect a retired approval.
            if self._approval_epochs.get(chat_id, 0) != preview_epoch:
                raise ToolProtocolError("permission_denied")
            # A replacement preview revokes hidden older actions for this
            # Chat. Revoke only after the new action and diff are fully valid,
            # so a failed edit cannot erase the still-visible approval.
            self._pending = {
                key: value for key, value in self._pending.items()
                if value.chat_id != chat_id and value.expires_at > current_time
                and value.deadline > monotonic()
            }
            if len(self._pending) >= 128:
                raise ToolProtocolError("io_error")
            self._pending[token] = _Pending(
                chat_id, project_id, boundary.normalized_root, boundary.root_identity,
                checked, before, expires, monotonic() + 300,
            )
            self._approval_epochs[chat_id] = preview_epoch + 1
        descriptor = next(item for item in TOOL_CATALOG if item.tool_id == checked.tool_id)
        return {
            "approvalId": token, "call": checked.to_data(), "risk": descriptor.risk,
            "preview": preview_text, "expiresAt": expires.astimezone(timezone.utc).isoformat(),
        }

    def invalidate(self, chat_id: str) -> None:
        """Discard a Chat's pending approvals after binding, mode, or owner changes."""

        with self._pending_lock:
            self._approval_epochs[chat_id] = self._approval_epochs.get(chat_id, 0) + 1
            self._pending = {key: value for key, value in self._pending.items() if value.chat_id != chat_id}

    def confirm(
        self, chat_id: str, project_id: str | None, boundary: WorkspaceBoundary,
        approval_id: str, granted: bool,
    ) -> ToolResult:
        """Consume one token and admit mutation only within its cooperative twenty-second preparation budget.

        Preflight and durable backup are checked before native mutation begins.
        Once admitted, a non-preemptible write/flush settles and reports its
        verified actual result even if that preparation budget later elapses.
        """

        with self._lock:
            try:
                _scope(chat_id, project_id)
                if type(approval_id) is not str or type(granted) is not bool:
                    raise ToolProtocolError()
                with self._pending_lock:
                    pending = self._pending.pop(approval_id, None)
                if pending is None:
                    return ToolResult("denied", error_code="permission_denied")
                if pending.chat_id != chat_id or pending.project_id != project_id:
                    return ToolResult("denied", error_code="permission_denied")
                if not granted:
                    self._record_denial(pending, "permission_denied")
                    return ToolResult("denied", error_code="permission_denied")
                # A system-clock rollback must not extend a permission. UTC
                # remains the visible expiry; monotonic time bounds its lease.
                if pending.expires_at <= self._clock() or pending.deadline <= monotonic():
                    self._record_denial(pending, "conflict")
                    return ToolResult("denied", error_code="conflict")
                descriptor = next(item for item in TOOL_CATALOG if item.tool_id == pending.call.tool_id)
                admission_deadline = monotonic() + descriptor.timeout_seconds
                try:
                    self._check_pending(boundary, pending)
                except (ToolProtocolError, WorkspaceBoundaryError):
                    self._record_denial(pending, "conflict")
                    return ToolResult("denied", error_code="conflict")
                if monotonic() >= admission_deadline:
                    self._record_timeout(pending)
                    return ToolResult("failed", error_code="timeout")
                undo_id = "undo_" + uuid4().hex
                record = self._new_record(undo_id, pending)
                try:
                    self._prepare(record, pending.before)
                    # Only pre-mutation work is cooperatively timed. Once a
                    # native write starts, preempting it can corrupt data; let
                    # it verify and report its actual durable outcome instead.
                    if monotonic() >= admission_deadline:
                        record.update(result="failed", error="timeout", recoverable=False)
                        self._save(record)
                        return ToolResult("failed", error_code="timeout")
                    self._apply(boundary, pending.call, pending.before)
                    record.update(result="succeeded", undoState="available", recoverable=False)
                    self._save(record)
                except Exception:
                    record.update(result="failed", error="io_error", undoState="unavailable", recoverable=True)
                    try:
                        self._save(record)
                    except Exception:
                        # A storage failure cannot manufacture a rollback or
                        # successful outcome. The last durable intent remains
                        # the conservative recovery record when accessible.
                        pass
                    return ToolResult("failed", error_code="io_error")
                result = ToolResult("succeeded", self._result_data(record))
                validate_tool_result(pending.call.tool_id, result)
                return result
            except ToolProtocolError as error:
                return ToolResult("denied", error_code=error.code)
            except WorkspaceBoundaryError:
                return ToolResult("denied", error_code="conflict")
            except Exception:
                return ToolResult("failed", error_code="io_error")

    def audit(self, chat_id: str) -> list[dict[str, Any]]:
        """Read bounded safe history for one Chat without disclosing contents, backups, or native paths."""

        _scope(chat_id, None)
        with self._lock:
            records = [self._load(entry.name) for entry in self._storage.list_directory("transactions", max_entries=_MAX_RECORDS)]
            return [self._audit_data(record) for record in sorted(records, key=lambda record: record["timestamp"]) if record["chatId"] == chat_id]

    def audit_page(self, chat_id: str) -> dict[str, Any]:
        """Return at most the newest hundred safe entries within a 512 KiB encoded frame.

        Entries are newest first. Truncation is explicit when either the count
        or byte bound hides older history; no content or target summary is
        silently shortened to squeeze a record into the frame.
        """

        history = self.audit(chat_id)
        candidates = list(reversed(history[-_MAX_AUDIT_PAGE_ENTRIES:]))
        entries: list[dict[str, Any]] = []
        truncated = len(history) > _MAX_AUDIT_PAGE_ENTRIES
        # False is one encoded byte longer than true. Reserving that spelling
        # keeps either final outcome within the same byte bound.
        encoded_bytes = len(json.dumps({"entries": [], "truncated": False}, ensure_ascii=False).encode("utf-8"))
        for entry in candidates:
            entry_bytes = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
            separator_bytes = 2 if entries else 0
            if encoded_bytes + entry_bytes + separator_bytes > _MAX_AUDIT_PAGE_BYTES:
                truncated = True
                break
            entries.append(entry)
            encoded_bytes += entry_bytes + separator_bytes
        return {"entries": entries, "truncated": truncated}

    def undo(
        self, chat_id: str, project_id: str | None, boundary: WorkspaceBoundary, undo_id: str,
    ) -> ToolResult:
        """Consume one explicitly requested Undo only if owner, root, current hashes, and names still match."""

        with self._lock:
            try:
                _scope(chat_id, project_id)
                record = self._load(undo_id)
                if (
                    record["chatId"] != chat_id or record["projectId"] != project_id
                    or record["result"] != "succeeded" or record["undoState"] != "available"
                ):
                    return ToolResult("denied", error_code="undo_unavailable")
                self._check_root(boundary, record["rootPath"], tuple(record["rootIdentity"]))
                self._check_undo(boundary, record)
                backup = None
                if record["beforeHash"] is not None:
                    backup = self._storage.read_bytes("transactions/" + undo_id + "/before.bin", max_bytes=MAX_TOOL_CONTENT_BYTES)
                    if _hash(backup) != record["beforeHash"]:
                        raise ToolProtocolError("undo_unavailable")
                # Durable consumption precedes side effects. An interrupted
                # Undo is exposed for recovery, never automatically repeated.
                record.update(undoState="executing", recoverable=True)
                self._save(record)
                try:
                    self._apply_undo(boundary, record, backup)
                    record.update(result="undone", undoState="consumed", recoverable=False, error=None)
                    self._save(record)
                except Exception:
                    record.update(undoState="unavailable", recoverable=True, error="io_error")
                    self._save(record)
                    return ToolResult("failed", error_code="io_error")
                return ToolResult("succeeded", self._result_data(record))
            except (ToolProtocolError, WorkspaceBoundaryError):
                return ToolResult("denied", error_code="undo_unavailable")
            except Exception:
                return ToolResult("failed", error_code="io_error")

    def _check_pending(self, boundary: WorkspaceBoundary, pending: _Pending) -> None:
        self._check_root(boundary, pending.root_path, pending.root_identity)
        call = pending.call
        if pending.before is None:
            if not _absent(boundary, call.path):
                raise ToolProtocolError("conflict")
        elif _hash(_text_file(boundary, call.path)) != _hash(pending.before):
            raise ToolProtocolError("conflict")
        if call.destination is not None and not _absent(boundary, call.destination):
            raise ToolProtocolError("conflict")

    @staticmethod
    def _check_root(boundary: WorkspaceBoundary, root_path: str, root_identity: tuple[int, int]) -> None:
        if boundary.normalized_root != root_path or boundary.root_identity != root_identity or boundary.metadata().identity != root_identity:
            raise ToolProtocolError("conflict")

    @staticmethod
    def _apply(boundary: WorkspaceBoundary, call: ToolCall, before: bytes | None) -> None:
        mutations = WorkspaceMutations(boundary)
        if call.tool_id == "create":
            mutations.create(call.path, (call.content or "").encode("utf-8"))
        elif call.tool_id == "edit":
            assert before is not None
            mutations.replace(call.path, (call.content or "").encode("utf-8"), _hash(before))
        elif call.tool_id == "move":
            assert before is not None and call.destination is not None
            mutations.move(call.path, call.destination, _hash(before))
        else:
            assert before is not None
            mutations.remove(call.path, _hash(before))

    def _new_record(self, undo_id: str, pending: _Pending) -> dict[str, Any]:
        call = pending.call
        parameters: dict[str, Any] = {"path": call.path}
        if call.destination is not None:
            parameters["destination"] = call.destination
        if call.content is not None:
            content = call.content.encode("utf-8")
            parameters.update(contentBytes=len(content), contentSha256=_hash(content))
        before_hash = None if pending.before is None else _hash(pending.before)
        after_hash = _hash((call.content or "").encode("utf-8")) if call.tool_id in {"create", "edit"} else before_hash
        return {
            "version": 1, "undoId": undo_id, "timestamp": self._clock().astimezone(timezone.utc).isoformat(),
            "chatId": pending.chat_id, "projectId": pending.project_id, "rootPath": pending.root_path,
            "rootIdentity": list(pending.root_identity), "toolId": call.tool_id, "path": call.path,
            "destination": call.destination, "beforeHash": before_hash, "afterHash": after_hash,
            "parameters": parameters, "result": "prepared", "error": None,
            "undoState": "unavailable", "recoverable": True,
        }

    def _record_denial(self, pending: _Pending, error: str) -> None:
        record = self._new_record("undo_" + uuid4().hex, pending)
        record.update(result="denied", error=error, recoverable=False)
        self._prepare(record, None)

    def _record_timeout(self, pending: _Pending) -> None:
        record = self._new_record("undo_" + uuid4().hex, pending)
        record.update(result="failed", error="timeout", recoverable=False)
        self._prepare(record, None)

    def _prepare(self, record: dict[str, Any], backup: bytes | None) -> None:
        if len(self._storage.list_directory("transactions", max_entries=_MAX_RECORDS)) >= _MAX_RECORDS:
            raise ToolProtocolError("io_error")
        directory = "transactions/" + record["undoId"]
        with self._storage.guarded_parent(directory) as access:
            os.mkdir(access.path if access.directory_fd is None else access.leaf_name, 0o700, dir_fd=access.directory_fd)
        self._save(record)
        if backup is not None:
            WorkspaceMutations(self._storage).create(directory + "/before.bin", backup)
            # Commit the parent directory after the new backup name exists.
            # Fsync of bytes alone cannot persist a new POSIX directory entry.
            self._save(record)

    def _save(self, record: dict[str, Any]) -> None:
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > _MAX_RECORD_BYTES:
            raise ToolProtocolError("io_error")
        relative = "transactions/" + record["undoId"] + "/manifest.json"
        with self._storage.guarded_parent(relative) as access:
            temporary = ".manifest_" + uuid4().hex + ".tmp"
            temp_path = access.parent_path / temporary
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(temp_path if access.directory_fd is None else temporary, flags, 0o600, dir_fd=access.directory_fd)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(
                    temp_path if access.directory_fd is None else temporary,
                    access.path if access.directory_fd is None else access.leaf_name,
                    src_dir_fd=access.directory_fd, dst_dir_fd=access.directory_fd,
                )
                if access.directory_fd is not None:
                    os.fsync(access.directory_fd)
            finally:
                try:
                    os.unlink(temp_path if access.directory_fd is None else temporary, dir_fd=access.directory_fd)
                except FileNotFoundError:
                    pass

    def _load(self, undo_id: str) -> dict[str, Any]:
        if type(undo_id) is not str or _TRANSACTION_ID.fullmatch(undo_id) is None:
            raise ToolProtocolError("undo_unavailable")
        raw = self._storage.read_bytes("transactions/" + undo_id + "/manifest.json", max_bytes=_MAX_RECORD_BYTES)
        record = json.loads(raw)
        if type(record) is not dict or set(record) != _RECORD_FIELDS or type(record["version"]) is not int or record["version"] != 1 or record["undoId"] != undo_id:
            raise ToolProtocolError("undo_unavailable")
        _scope(record["chatId"], record["projectId"])
        if record["toolId"] not in _WRITE_TOOLS or type(record["rootPath"]) is not str:
            raise ToolProtocolError("undo_unavailable")
        _validate_target(record["path"])
        if (record["toolId"] == "move") != (record["destination"] is not None):
            raise ToolProtocolError("undo_unavailable")
        if record["destination"] is not None:
            _validate_target(record["destination"])
        if (
            type(record["rootIdentity"]) is not list or len(record["rootIdentity"]) != 2
            or any(type(value) is not int or value < 0 for value in record["rootIdentity"])
            or type(record["recoverable"]) is not bool
            or record["result"] not in {"prepared", "succeeded", "denied", "failed", "undone"}
            or record["undoState"] not in {"available", "unavailable", "executing", "consumed"}
            or record["error"] not in {None, "io_error", "conflict", "permission_denied", "timeout"}
        ):
            raise ToolProtocolError("undo_unavailable")
        for field in ("beforeHash", "afterHash"):
            if record[field] is not None and (type(record[field]) is not str or _SHA256.fullmatch(record[field]) is None):
                raise ToolProtocolError("undo_unavailable")
        if (
            record["afterHash"] is None
            or (record["toolId"] == "create") != (record["beforeHash"] is None)
            or (record["toolId"] in {"move", "delete"} and record["beforeHash"] != record["afterHash"])
        ):
            raise ToolProtocolError("undo_unavailable")
        # Derive public parameters from validated fields, never a stored
        # arbitrary dict that could smuggle backup bytes into an audit result.
        expected_keys = {"path"}
        if record["destination"] is not None:
            expected_keys.add("destination")
        if record["toolId"] in {"create", "edit"}:
            expected_keys.update({"contentBytes", "contentSha256"})
        parameters = record["parameters"]
        if type(parameters) is not dict or set(parameters) != expected_keys or parameters["path"] != record["path"]:
            raise ToolProtocolError("undo_unavailable")
        if "destination" in expected_keys and parameters["destination"] != record["destination"]:
            raise ToolProtocolError("undo_unavailable")
        if "contentBytes" in expected_keys and (
            type(parameters["contentBytes"]) is not int or not 0 <= parameters["contentBytes"] <= MAX_TOOL_CONTENT_BYTES
            or parameters["contentSha256"] != record["afterHash"]
        ):
            raise ToolProtocolError("undo_unavailable")
        if type(record["timestamp"]) is not str or len(record["timestamp"]) > 64:
            raise ToolProtocolError("undo_unavailable")
        timestamp = datetime.fromisoformat(record["timestamp"])
        if timestamp.tzinfo is None:
            raise ToolProtocolError("undo_unavailable")
        record["timestamp"] = timestamp.astimezone(timezone.utc).isoformat()
        return cast(dict[str, Any], record)

    @staticmethod
    def _audit_data(record: dict[str, Any]) -> dict[str, Any]:
        result = "failed" if record["result"] == "prepared" else record["result"]
        return {
            "timestamp": record["timestamp"], "chatId": record["chatId"], "projectId": record["projectId"],
            "toolId": record["toolId"], "parameters": dict(record["parameters"]), "approver": "user",
            "result": result, "error": record["error"],
            "undoId": record["undoId"] if record["result"] == "succeeded" and record["undoState"] == "available" else None,
            "recoverable": record["recoverable"] or record["undoState"] == "executing",
        }

    @staticmethod
    def _result_data(record: dict[str, Any]) -> dict[str, Any]:
        result = {"path": record["path"], "undoId": record["undoId"]}
        if record["destination"] is not None:
            result["destination"] = record["destination"]
        return result

    @staticmethod
    def _check_undo(boundary: WorkspaceBoundary, record: dict[str, Any]) -> None:
        if record["toolId"] == "delete":
            if not _absent(boundary, record["path"]):
                raise ToolProtocolError("undo_unavailable")
        else:
            current_path = record["destination"] if record["toolId"] == "move" else record["path"]
            if _hash(_text_file(boundary, current_path)) != record["afterHash"]:
                raise ToolProtocolError("undo_unavailable")
            if record["toolId"] == "move" and not _absent(boundary, record["path"]):
                raise ToolProtocolError("undo_unavailable")

    @staticmethod
    def _apply_undo(boundary: WorkspaceBoundary, record: dict[str, Any], backup: bytes | None) -> None:
        mutations = WorkspaceMutations(boundary)
        if record["toolId"] == "create":
            mutations.remove(record["path"], record["afterHash"])
        elif record["toolId"] == "edit":
            assert backup is not None
            mutations.replace(record["path"], backup, record["afterHash"])
        elif record["toolId"] == "move":
            mutations.move(record["destination"], record["path"], record["afterHash"])
        else:
            assert backup is not None
            mutations.create(record["path"], backup)
