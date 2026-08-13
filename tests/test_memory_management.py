import json
import logging
from pathlib import Path

import pytest

from core import Brain
from memory import Memory
from ui.console import (
    run_console_session,
    run_memory_management,
)


def _memory_with_records(
    tmp_path: Path,
) -> Memory:
    memory = Memory(tmp_path)

    memory.save_long_term_memory(
        "preferred_language",
        "Chinese",
        "user_explicit",
        "Please remember that I prefer Chinese.",
    )
    memory.save_long_term_memory(
        "degree_program",
        "Computer Science",
        "model_inferred",
        "I study computer science.",
    )

    return memory


def test_search_is_case_insensitive_and_preserves_number(
    tmp_path: Path,
) -> None:
    memory = _memory_with_records(tmp_path)

    results = memory.search_long_term_memories(
        "COMPUTER"
    )

    assert len(results) == 1
    assert results[0]["number"] == 2
    assert (
        results[0]["memory"]["key"]
        == "degree_program"
    )


def test_edit_preserves_source_metadata(
    tmp_path: Path,
) -> None:
    memory = _memory_with_records(tmp_path)
    original = (
        memory.get_long_term_memories()[0]
    )

    updated = memory.edit_long_term_memory(
        1,
        "reply_language",
        "Traditional Chinese",
    )

    assert updated["key"] == "reply_language"
    assert (
        updated["value"]
        == "Traditional Chinese"
    )
    assert (
        updated["source_type"]
        == original["source_type"]
    )
    assert (
        updated["source_text"]
        == original["source_text"]
    )
    assert (
        updated["created_at"]
        == original["created_at"]
    )
    assert (
        Memory(tmp_path)
        .get_long_term_memories()[0]
        == updated
    )


def test_invalid_edit_preserves_file(
    tmp_path: Path,
) -> None:
    memory = _memory_with_records(tmp_path)
    before = (
        memory.long_term_memory_file.read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(IndexError):
        memory.edit_long_term_memory(
            99,
            "key",
            "value",
        )

    assert (
        memory.long_term_memory_file.read_text(
            encoding="utf-8"
        )
        == before
    )


def test_export_creates_portable_copy_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    memory = _memory_with_records(tmp_path)
    export_file = (
        tmp_path
        / "exports"
        / "memories.json"
    )

    assert (
        memory.export_long_term_memories(
            export_file
        )
        == export_file
    )
    assert json.loads(
        export_file.read_text(
            encoding="utf-8"
        )
    ) == {
        "memories": (
            memory.get_long_term_memories()
        ),
    }

    with pytest.raises(FileExistsError):
        memory.export_long_term_memories(
            export_file
        )

    with pytest.raises(ValueError):
        memory.export_long_term_memories(
            memory.long_term_memory_file,
            overwrite=True,
        )


def test_brain_requires_confirmation_and_logs_deletion(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    memory = _memory_with_records(tmp_path)
    brain = Brain("fake-model", memory)

    with pytest.raises(PermissionError):
        brain.delete_long_term_memory(1)

    assert len(
        memory.get_long_term_memories()
    ) == 2

    with caplog.at_level(
        logging.INFO,
        logger="core.brain",
    ):
        deleted = brain.delete_long_term_memory(
            1,
            confirmed=True,
        )

    assert (
        deleted["key"]
        == "preferred_language"
    )
    assert [
        item["key"]
        for item in (
            memory.get_long_term_memories()
        )
    ] == [
        "degree_program",
    ]
    assert (
        "Long-term memory deleted"
        in caplog.text
    )


def test_console_cancels_deletion_without_exact_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = _memory_with_records(tmp_path)
    brain = Brain("fake-model", memory)
    answers = iter(
        [
            "delete",
            "1",
            "yes",
            "back",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(answers),
    )

    run_memory_management(brain)

    assert len(
        memory.get_long_term_memories()
    ) == 2
    assert (
        "Memory deletion cancelled."
        in capsys.readouterr().out
    )


def test_console_can_search_edit_export_and_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = _memory_with_records(tmp_path)
    brain = Brain("fake-model", memory)
    export_file = (
        tmp_path / "memory-export.json"
    )
    answers = iter(
        [
            "search",
            "Chinese",
            "edit",
            "1",
            "",
            "Traditional Chinese",
            "export",
            str(export_file),
            "delete",
            "2",
            "DELETE",
            "back",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(answers),
    )

    run_memory_management(brain)

    remaining = (
        memory.get_long_term_memories()
    )

    assert len(remaining) == 1
    assert (
        remaining[0]["value"]
        == "Traditional Chinese"
    )
    assert export_file.exists()

    output = capsys.readouterr().out

    assert (
        "Memory search results:"
        in output
    )
    assert "Memory updated." in output
    assert "Memory deleted." in output


def test_console_session_opens_memory_management_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = _memory_with_records(tmp_path)
    brain = Brain("fake-model", memory)
    answers = iter(
        [
            "/memory",
            "list",
            "back",
            "",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(answers),
    )

    run_console_session(brain)

    output = capsys.readouterr().out

    assert "Memory management:" in output
    assert (
        "Memory number: 1"
        in output
    )
    assert (
        "No message was entered."
        in output
    )