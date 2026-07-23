from pathlib import Path
import pytest

from memory.manager import Memory


def test_load_profile_creates_default_profile(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)

    profile = memory.load_profile()

    assert profile == {
        "user_name": "Ying",
        "assistant_name": "Elysia",
        "languages": ["Chinese", "English"],
        "project": "Elysia AI",
    }
    assert memory.profile_file.exists()


def test_record_launch_increments_and_saves_count(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)

    first_profile = memory.record_launch()
    second_profile = memory.record_launch()

    assert first_profile["launch_count"] == 1
    assert second_profile["launch_count"] == 2
    assert memory.load_profile()["launch_count"] == 2


def test_get_recent_messages_rejects_non_positive_limit(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)

    with pytest.raises(
        ValueError,
        match=r"Message limit must be greater than zero\.",
    ):
        memory.get_recent_messages(0)


def test_get_recent_messages_returns_requested_messages(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)

    memory.save_message("Ying", "First message")
    memory.save_message("Elysia", "Second message")
    memory.save_message("Ying", "Third message")

    recent_messages = memory.get_recent_messages(2)

    assert len(recent_messages) == 2
    assert recent_messages[0]["speaker"] == "Elysia"
    assert recent_messages[0]["message"] == "Second message"
    assert recent_messages[1]["speaker"] == "Ying"
    assert recent_messages[1]["message"] == "Third message"