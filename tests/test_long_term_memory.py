import json
from pathlib import Path
from typing import cast

import pytest

from chats import JsonChatRepository
from core import ActiveConversationService, Brain
from memory import (
    LONG_TERM_MEMORY_SCHEMA_VERSION,
    MemoryScope,
    LongTermMemorySource,
    Memory,
    load_long_term_memory,
    save_long_term_memory_record,
)
from ui.console import run_console_session
from projects import JsonProjectRepository


def test_load_creates_empty_long_term_memory_store(
    tmp_path: Path,
) -> None:
    memory_file = tmp_path / "long_term_memory.json"

    memory_data = load_long_term_memory(memory_file)

    assert memory_data == {
        "schema_version": LONG_TERM_MEMORY_SCHEMA_VERSION,
        "memories": [],
    }
    assert memory_file.exists()


@pytest.mark.parametrize(
    "source_type",
    [
        "user_explicit",
        "model_inferred",
    ],
)
def test_save_preserves_memory_source(
    tmp_path: Path,
    source_type: LongTermMemorySource,
) -> None:
    memory_file = tmp_path / "long_term_memory.json"

    saved_record = save_long_term_memory_record(
        memory_file,
        "  preferred_language  ",
        "  Chinese  ",
        source_type,
        "  Please remember this preference.  ",
    )

    assert saved_record["key"] == "preferred_language"
    assert saved_record["value"] == "Chinese"
    assert saved_record["source_type"] == source_type
    assert (
        saved_record["source_text"]
        == "Please remember this preference."
    )
    assert saved_record["created_at"]
    assert saved_record["scope"] == "global"
    assert saved_record["scope_id"] is None

    assert load_long_term_memory(memory_file) == {
        "schema_version": LONG_TERM_MEMORY_SCHEMA_VERSION,
        "memories": [saved_record],
    }


@pytest.mark.parametrize(
    ("scope", "scope_id"),
    [
        ("project", "project_elysia"),
        ("chat", "chat_architecture"),
    ],
)
def test_save_preserves_exact_non_global_scope(
    tmp_path: Path,
    scope: MemoryScope,
    scope_id: str,
) -> None:
    saved_record = save_long_term_memory_record(
        tmp_path / "long_term_memory.json",
        "architecture",
        "Use stable IDs",
        "user_explicit",
        "Remember this architecture decision.",
        scope=scope,
        scope_id=scope_id,
    )

    assert saved_record["scope"] == scope
    assert saved_record["scope_id"] == scope_id


def test_load_migrates_versionless_records_to_global_scope(
    tmp_path: Path,
) -> None:
    memory_file = tmp_path / "long_term_memory.json"
    legacy_data = {
        "memories": [
            {
                "key": "preferred_language",
                "value": "Chinese",
                "source_type": "user_explicit",
                "source_text": "Please remember this.",
                "created_at": "2026-08-20 12:00:00",
            }
        ]
    }
    memory_file.write_text(
        json.dumps(legacy_data),
        encoding="utf-8",
    )

    migrated = load_long_term_memory(memory_file)

    assert migrated["schema_version"] == 1
    assert migrated["memories"][0]["scope"] == "global"
    assert migrated["memories"][0]["scope_id"] is None
    assert json.loads(
        memory_file.read_text(encoding="utf-8")
    ) == migrated


@pytest.mark.parametrize(
    ("scope", "scope_id"),
    [
        ("global", "project_wrong"),
        ("project", None),
        ("chat", "project_wrong"),
    ],
)
def test_save_rejects_invalid_scope_pair(
    tmp_path: Path,
    scope: MemoryScope,
    scope_id: str | None,
) -> None:
    with pytest.raises(ValueError):
        save_long_term_memory_record(
            tmp_path / "long_term_memory.json",
            "key",
            "value",
            "user_explicit",
            "Remember this.",
            scope=scope,
            scope_id=scope_id,
        )


@pytest.mark.parametrize(
    (
        "key",
        "value",
        "source_text",
    ),
    [
        ("   ", "Chinese", "Remember this."),
        ("language", "   ", "Remember this."),
        ("language", "Chinese", "   "),
    ],
)
def test_save_rejects_empty_required_text(
    tmp_path: Path,
    key: str,
    value: str,
    source_text: str,
) -> None:
    with pytest.raises(ValueError):
        save_long_term_memory_record(
            tmp_path / "long_term_memory.json",
            key,
            value,
            "user_explicit",
            source_text,
        )


def test_save_rejects_unknown_source_type(
    tmp_path: Path,
) -> None:
    invalid_source = cast(
        LongTermMemorySource,
        "unknown_source",
    )

    with pytest.raises(ValueError):
        save_long_term_memory_record(
            tmp_path / "long_term_memory.json",
            "preferred_language",
            "Chinese",
            invalid_source,
            "Remember this preference.",
        )


def test_memory_reads_record_after_restart(
    tmp_path: Path,
) -> None:
    first_memory = Memory(tmp_path)

    saved_record = first_memory.save_long_term_memory(
        "preferred_language",
        "Chinese",
        "user_explicit",
        "Please remember that I prefer Chinese replies.",
    )

    restarted_memory = Memory(tmp_path)

    assert restarted_memory.get_long_term_memories() == [
        saved_record,
    ]


def test_brain_recalls_long_term_memories(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)

    saved_record = memory.save_long_term_memory(
        "preferred_language",
        "Chinese",
        "user_explicit",
        "Please remember that I prefer Chinese replies.",
    )

    brain = Brain("fake-model", memory)

    assert brain.recall_long_term_memories() == [
        saved_record,
    ]


def test_console_session_displays_memory_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = Memory(tmp_path)

    saved_record = memory.save_long_term_memory(
        "preferred_language",
        "Chinese",
        "user_explicit",
        "Please remember that I prefer Chinese replies.",
    )

    brain = Brain(
        "fake-model",
        memory,
        active_conversation_service=ActiveConversationService(
            JsonChatRepository(tmp_path / "data" / "chats"),
            JsonProjectRepository(tmp_path / "data" / "projects"),
        ),
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "/quit",
    )

    run_console_session(brain)

    output = capsys.readouterr().out

    assert "Long-term memories:" in output
    assert "- preferred_language: Chinese" in output
    assert "Source type: user_explicit" in output
    assert (
        "Source text: Please remember that I prefer "
        "Chinese replies."
        in output
    )
    assert (
        f"Created at: {saved_record['created_at']}"
        in output
    )
