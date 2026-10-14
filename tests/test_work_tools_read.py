"""Verify safe read-only Work tools, bounded traversal, and cancellation."""

import json
import os
from pathlib import Path
from threading import Event

import pytest

import work_tools.executor as executor_module
from work_tools.contracts import MAX_TOOL_CONTENT_BYTES, ToolCall, validate_tool_result
from work_tools.executor import WorkToolExecutor, is_sensitive_path
from work_tools.workspace import WorkspaceBoundary, WorkspaceEntry


def _executor(tmp_path: Path) -> tuple[Path, WorkspaceBoundary, WorkToolExecutor]:
    root = tmp_path / "selected"
    root.mkdir()
    boundary = WorkspaceBoundary(root)
    return root, boundary, WorkToolExecutor(boundary)


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Record content and modification times, excluding OS read-access bookkeeping."""

    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns, None if path.is_dir() else path.read_bytes(),
        )
        for path in [root, *root.rglob("*")]
    }


def test_read_only_tools_never_create_or_modify_workspace_files(tmp_path: Path) -> None:
    """Exercise all four real tools and verify the complete tree stays identical."""

    root, _boundary, executor = _executor(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "main.py").write_bytes("print('你好')\n".encode("utf-8"))
    (root / "notes.txt").write_text("main exists\n", encoding="utf-8")
    (root / "README").write_text("Read me.\n", encoding="utf-8")
    before = _snapshot(root)
    calls = [
        ToolCall("list_directory", ""), ToolCall("metadata", "src/main.py"),
        ToolCall("read_text", "src/main.py"), ToolCall("search_files", "", query="MAIN"),
    ]
    results = [executor.run(call) for call in calls]
    assert all(result.status == "succeeded" for result in results)
    assert results[2].data == {"path": "src/main.py", "content": "print('你好')\n", "sizeBytes": 16}
    assert results[3].data == {"matches": [{"path": "src/main.py"}], "truncated": False}
    assert executor.run(ToolCall("read_text", "README")).status == "succeeded"
    for call, result in zip(calls, results, strict=True):
        validate_tool_result(call.tool_id, result)
    assert _snapshot(root) == before


@pytest.mark.parametrize("path", [
    ".env", ".env.example", ".ENV.production", ".git/config", ".ssh/id_ed25519",
    "settings/credentials.json", "user-password.txt", "access_token.json", "private.pem",
    "public.crt", "nested/secrets/data.txt", "backup.kdbx",
])
def test_sensitive_paths_are_denied_before_any_native_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Block sensitive components case-insensitively without probing file contents."""

    _root, boundary, executor = _executor(tmp_path)

    def _unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Sensitive paths must not reach filesystem metadata or reads.")

    monkeypatch.setattr(boundary, "metadata", _unexpected)
    monkeypatch.setattr(boundary, "read_bytes", _unexpected)
    assert is_sensitive_path(path)
    for tool in ("metadata", "read_text"):
        result = executor.run(ToolCall(tool, path))  # type: ignore[arg-type]
        assert (result.status, result.error_code) == ("denied", "sensitive_file")


def test_listing_and_filename_search_skip_secrets_and_read_no_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search file names alone while keeping secret names and subtrees hidden."""

    root, boundary, executor = _executor(tmp_path)
    (root / "secrets").mkdir()
    (root / "secrets" / "target.txt").write_text("private", encoding="utf-8")
    (root / ".env").write_text("private", encoding="utf-8")
    (root / "target.png").write_bytes(b"\x89PNG\x00private")
    (root / "notes.txt").write_text("target only appears in content", encoding="utf-8")

    def _unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Filename search cannot read binary or private contents.")

    monkeypatch.setattr(boundary, "read_bytes", _unexpected)
    listed = executor.run(ToolCall("list_directory", ""))
    assert listed.data is not None
    assert [entry["path"] for entry in listed.data["entries"]] == ["notes.txt", "target.png"]
    assert executor.run(ToolCall("metadata", "target.png")).status == "succeeded"
    result = executor.run(ToolCall("search_files", "", query="target"))
    assert result.data == {"matches": [{"path": "target.png"}], "truncated": False}
    assert "private" not in json.dumps(result.to_data())


def test_selecting_a_sensitive_root_does_not_erase_ancestor_restrictions(tmp_path: Path) -> None:
    """Treat a selected private directory as sensitive even for empty relative paths."""

    root = tmp_path / ".ssh"
    root.mkdir()
    (root / "config.txt").write_text("private", encoding="utf-8")
    executor = WorkToolExecutor(WorkspaceBoundary(root))
    for call in (ToolCall("list_directory", ""), ToolCall("read_text", "config.txt")):
        assert executor.run(call).error_code == "sensitive_file"


@pytest.mark.parametrize("name,content", [
    ("binary.png", b"plain-looking bytes"), ("invalid.txt", b"\xff\xfe"),
    ("embedded_nul.txt", b"valid\x00binary"), ("control.txt", b"data\x01"),
    ("unknown", b"text"), ("large.txt", b"x" * (MAX_TOOL_CONTENT_BYTES + 1)),
], ids=["unknown_suffix", "invalid_utf8", "nul_bytes", "control_bytes", "unknown_name", "oversized"])
def test_text_reads_reject_unknown_types_binary_and_oversized_files(
    tmp_path: Path,
    name: str,
    content: bytes,
) -> None:
    """Require both a recognized name and bounded strict UTF-8 plain text."""

    root, _boundary, executor = _executor(tmp_path)
    (root / name).write_bytes(content)
    result = executor.run(ToolCall("read_text", name))
    assert result.status == "denied"
    assert result.error_code == ("file_too_large" if name == "large.txt" else "binary_file")
    assert result.data is None


def test_text_read_exact_byte_limit_empty_file_and_missing_target(tmp_path: Path) -> None:
    """Keep inclusive byte bounds and useful missing-file outcomes."""

    root, _boundary, executor = _executor(tmp_path)
    (root / "empty.txt").write_bytes(b"")
    (root / "exact.txt").write_bytes(b"x" * MAX_TOOL_CONTENT_BYTES)
    assert executor.run(ToolCall("read_text", "empty.txt")).data == {
        "path": "empty.txt", "content": "", "sizeBytes": 0,
    }
    exact = executor.run(ToolCall("read_text", "exact.txt"))
    assert exact.status == "succeeded" and exact.data is not None
    assert exact.data["sizeBytes"] == MAX_TOOL_CONTENT_BYTES
    assert executor.run(ToolCall("read_text", "missing.txt")).error_code == "not_found"


@pytest.mark.parametrize("call", [
    ToolCall("create", "new.txt", content="text"), ToolCall("edit", "old.txt", content="text"),
    ToolCall("move", "old.txt", destination="new.txt"), ToolCall("delete", "old.txt"),
])
def test_read_executor_denies_all_write_tools_without_hidden_changes(
    tmp_path: Path,
    call: ToolCall,
) -> None:
    """Keep writes unavailable even when their typed arguments are valid."""

    root, _boundary, executor = _executor(tmp_path)
    (root / "old.txt").write_text("original", encoding="utf-8")
    before = _snapshot(root)
    result = executor.run(call)
    assert (result.status, result.error_code) == ("denied", "permission_required")
    assert _snapshot(root) == before


def test_executor_revalidates_forged_frozen_calls_before_io(tmp_path: Path) -> None:
    """Internal DTO mutation cannot bypass lexical or exact argument validation."""

    _root, _boundary, executor = _executor(tmp_path)
    call = ToolCall("read_text", "safe.txt")
    object.__setattr__(call, "path", "../private.txt")
    assert executor.run(call).error_code == "path_denied"
    object.__setattr__(call, "path", "safe.txt")
    object.__setattr__(call, "content", "unexpected")
    assert executor.run(call).error_code == "invalid_request"
    assert executor.run("raw command").error_code == "invalid_request"  # type: ignore[arg-type]


def test_list_and_search_handle_directory_entry_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail honestly on excessive native listings and mark incomplete search."""

    root, _boundary, executor = _executor(tmp_path)
    for index in range(4):
        (root / f"item{index}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(executor_module, "_MAX_DIRECTORY_ENTRIES", 3)
    listed = executor.run(ToolCall("list_directory", ""))
    assert (listed.status, listed.error_code) == ("denied", "file_too_large")
    searched = executor.run(ToolCall("search_files", "", query="item"))
    assert searched.status == "partial"
    assert searched.data == {"matches": [], "truncated": True}


def test_filename_search_bounds_total_visited_and_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop iterative traversal at explicit count limits without loading contents."""

    root, _boundary, executor = _executor(tmp_path)
    for name in ("a", "b"):
        (root / name).mkdir()
        for index in range(3):
            (root / name / f"match{index}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(executor_module, "_MAX_SEARCH_VISITED", 5)
    visited = executor.run(ToolCall("search_files", "", query="match"))
    assert visited.status == "partial" and visited.data is not None
    assert len(visited.data["matches"]) == 3 and visited.data["truncated"] is True
    monkeypatch.setattr(executor_module, "_MAX_SEARCH_VISITED", 5000)
    monkeypatch.setattr(executor_module, "_MAX_SEARCH_RESULTS", 2)
    bounded = executor.run(ToolCall("search_files", "", query="match"))
    assert bounded.status == "partial" and bounded.data is not None
    assert len(bounded.data["matches"]) == 2


def test_results_truncate_before_exceeding_the_wire_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return safe prefixes when long relative paths would exceed a result frame."""

    root, _boundary, executor = _executor(tmp_path)
    for index in range(3):
        (root / f"match{index}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(executor_module, "_RESULT_DATA_BUDGET", 125)
    listed = executor.run(ToolCall("list_directory", ""))
    assert listed.status == "partial" and listed.data is not None
    assert len(listed.data["entries"]) == 1
    searched = executor.run(ToolCall("search_files", "", query="match"))
    assert searched.status == "succeeded"
    monkeypatch.setattr(executor_module, "_RESULT_DATA_BUDGET", 30)
    searched = executor.run(ToolCall("search_files", "", query="match"))
    assert searched.status == "partial" and searched.data is not None
    assert len(searched.data["matches"]) == 1


def test_cancellation_before_and_after_read_suppresses_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop promptly at ownership checkpoints and discard a late completed read."""

    root, boundary, executor = _executor(tmp_path)
    (root / "note.txt").write_text("private result", encoding="utf-8")
    cancelled = Event()
    cancelled.set()
    before = executor.run(ToolCall("read_text", "note.txt"), should_cancel=cancelled.is_set)
    assert before.status == "cancelled" and before.data is None
    cancelled.clear()
    original = boundary.read_bytes

    def _read_then_cancel(path: str, *, max_bytes: int) -> bytes:
        value = original(path, max_bytes=max_bytes)
        cancelled.set()
        return value

    monkeypatch.setattr(boundary, "read_bytes", _read_then_cancel)
    after = executor.run(ToolCall("read_text", "note.txt"), should_cancel=cancelled.is_set)
    assert after.status == "cancelled" and after.data is None
    assert "private result" not in json.dumps(after.to_data())


def test_deadlines_fail_late_reads_and_return_partial_filename_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use cooperative deadlines without pretending to abort native OS calls."""

    root, boundary, executor = _executor(tmp_path)
    (root / "note.txt").write_text("text", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "note2.txt").write_text("text", encoding="utf-8")
    now = [100.0]
    monkeypatch.setattr(executor_module, "monotonic", lambda: now[0])
    original_read = boundary.read_bytes

    def _late_read(path: str, *, max_bytes: int) -> bytes:
        value = original_read(path, max_bytes=max_bytes)
        now[0] = 111.0
        return value

    monkeypatch.setattr(boundary, "read_bytes", _late_read)
    read = executor.run(ToolCall("read_text", "note.txt"))
    assert (read.status, read.error_code, read.data) == ("failed", "timeout", None)
    now[0] = 100.0
    original_list = boundary.list_directory

    def _late_child_listing(path: str = "", *, max_entries: int) -> tuple[WorkspaceEntry, ...]:
        value = original_list(path, max_entries=max_entries)
        if path:
            now[0] = 121.0
        return value

    monkeypatch.setattr(boundary, "list_directory", _late_child_listing)
    search = executor.run(ToolCall("search_files", "", query="note"))
    assert (search.status, search.error_code) == ("partial", "timeout")
    assert search.data == {"matches": [{"path": "note.txt"}], "truncated": True}


def test_native_errors_are_sanitized_and_replaced_roots_are_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject stale root identity and omit native paths, contents, and diagnostics."""

    root, boundary, executor = _executor(tmp_path)
    (root / "note.txt").write_text("text", encoding="utf-8")
    saved = root.with_name("original")
    root.rename(saved)
    root.mkdir()
    (root / "note.txt").write_text("replacement", encoding="utf-8")
    result = executor.run(ToolCall("read_text", "note.txt"))
    assert (result.status, result.error_code) == ("denied", "path_denied")

    def _native_error(_path: str) -> WorkspaceEntry:
        raise RuntimeError("C:/Users/private/secret native diagnostics and contents")

    monkeypatch.setattr(boundary, "metadata", _native_error)
    error = executor.run(ToolCall("metadata", "note.txt"))
    assert (error.status, error.error_code) == ("failed", "io_error")
    assert "private" not in json.dumps(error.to_data())


def test_hard_links_are_omitted_from_listing_search_and_denied_on_read(tmp_path: Path) -> None:
    """An in-scope hard link cannot inherit access to a file outside the workspace."""

    root, _boundary, executor = _executor(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private contents", encoding="utf-8")
    os.link(outside, root / "alias.txt")
    result = executor.run(ToolCall("read_text", "alias.txt"))
    assert (result.status, result.error_code) == ("denied", "path_denied")
    assert executor.run(ToolCall("list_directory", "")).data == {"entries": [], "truncated": False}
    assert executor.run(ToolCall("search_files", "", query="alias")).data == {"matches": [], "truncated": False}
