"""Exercise canonical name edits, old profiles, persistence failures, and prompt adoption."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Barrier

import pytest

import desktop_backend
import memory.manager as manager_module
from config.desktop_settings import DesktopSettingsStorageError
from memory import Memory
from memory.profile import ProfileConflictError, validate_user_name
from tests.test_brain import FakeChatModel, _active_brain
from tests.test_desktop_backend import (
    _desktop_settings_repository, _error, _handshake_request,
    _request, _run_bridge, _success_result,
)


def test_name_edit_preserves_legacy_profile_and_other_fields(tmp_path: Path) -> None:
    """Migrate only on explicit save and retain language, project, and launch data."""
    memory = Memory(tmp_path)
    memory.profile_file.parent.mkdir(parents=True)
    legacy = {
        "user_name": "Ying", "assistant_name": "Elysia",
        "languages": ["Chinese"], "project": "My project", "launch_count": 7,
    }
    memory.profile_file.write_text(json.dumps(legacy), encoding="utf-8")
    before = memory.profile_file.read_bytes()
    assert memory.load_profile()["user_name"] == "Ying"
    assert memory.profile_file.read_bytes() == before
    saved = memory.update_user_name("小星 🌟", expected_user_name="Ying")
    assert saved == {**legacy, "schema_version": 1, "user_name": "小星 🌟"}
    assert Memory(tmp_path).load_profile() == saved
    with pytest.raises(ProfileConflictError):
        memory.update_user_name("stale", expected_user_name="Ying")
    assert memory.load_profile() == saved


@pytest.mark.parametrize("value", ["", " ", " New", "New ", "a\nb", "a\tb", "a\0b", "a\x85b", "a\u2028b", "\ud800", "x" * 81])
def test_name_edits_reject_invalid_values(value: str) -> None:
    """Reject ambiguous single-line names without constraining legacy reads."""
    with pytest.raises(ValueError):
        validate_user_name(value)
    assert validate_user_name("🌟" * 80) == "🌟" * 80


def test_failed_name_save_preserves_previous_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed final replacement leaves the original readable and no temporary data."""
    memory = Memory(tmp_path)
    memory.load_profile()
    before = memory.profile_file.read_bytes()

    def _fail_replace(*_args: object) -> None:
        raise OSError("fixture disk failure")

    monkeypatch.setattr(manager_module.os, "replace", _fail_replace)
    with pytest.raises(DesktopSettingsStorageError):
        memory.update_user_name("New", expected_user_name="User")
    assert memory.profile_file.read_bytes() == before
    assert list(memory.profile_file.parent.glob(".profile-*.tmp")) == []


def test_launch_and_rename_preserve_each_other(tmp_path: Path) -> None:
    """Independent Memory instances serialize a rename with launch increments."""
    memory = Memory(tmp_path)
    memory.load_profile()
    barrier = Barrier(2)

    def _rename() -> None:
        barrier.wait()
        Memory(tmp_path).update_user_name("New", expected_user_name="User")

    def _launch() -> None:
        barrier.wait()
        for _ in range(8):
            Memory(tmp_path).record_launch()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_rename), pool.submit(_launch)]
        for future in futures:
            future.result()
    assert memory.load_profile()["user_name"] == "New"
    assert memory.load_profile()["launch_count"] == 8


def test_profile_can_be_saved_before_model_initialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings use the canonical data root without loading Ollama or requiring Brain."""
    monkeypatch.setattr(desktop_backend, "SETTINGS", replace(desktop_backend.SETTINGS, data_root=tmp_path))
    repository = _desktop_settings_repository(tmp_path / "settings.json")
    _, messages = _run_bridge(lambda _chat_id: [
        _handshake_request(), _request("name-read", "profile.get", {}),
        _request("name-save", "profile.update", {"expectedUserName": "User", "userName": "小星"}),
        _request("name-stale", "profile.update", {"expectedUserName": "User", "userName": "Wrong"}),
        _request("name-after", "profile.get", {}),
    ], settings_repository=repository)
    assert _success_result(messages, "name-read") == {"kind": "profile", "userName": "User"}
    assert _success_result(messages, "name-save") == {"kind": "profile", "userName": "小星"}
    assert _error(messages, "name-stale")["code"] == "profile.conflict"
    assert _success_result(messages, "name-after")["userName"] == "小星"
    assert Memory(tmp_path).load_profile()["user_name"] == "小星"


def test_renamed_profile_is_used_by_next_model_prompt_without_rewriting_history(tmp_path: Path) -> None:
    """The next streamed turn adopts the name while historical content and roles stay intact."""
    model = FakeChatModel("Hello, Ying!")
    brain, memory, chat = _active_brain(tmp_path, model)
    memory.update_user_name("Ying", expected_user_name="User")
    brain.chat(chat.chat_id, "Hello")
    earlier = brain.get_chat(chat.chat_id).messages
    memory.update_user_name("小星", expected_user_name="Ying")
    list(brain.stream_chat(chat.chat_id, "Continue"))
    assert model.received_messages is not None
    system = model.received_messages[0]["content"]
    assert '"user_name": "小星"' in system
    assert '"user_name": "Ying"' not in system
    assert "优先于旧聊天、摘要和记忆中的旧称呼" in system
    assert brain.get_chat(chat.chat_id).messages[:2] == earlier
    assert {"role": "assistant", "content": "Hello, Ying!"} in model.received_messages
