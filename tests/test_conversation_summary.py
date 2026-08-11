from pathlib import Path

import pytest

from memory import (
    load_conversation_summary,
    save_conversation_summary,
)
from memory.conversation_summary import (
    validate_conversation_summary,
    validate_conversation_summary_content,
    validate_conversation_summary_data,
)


def _valid_content() -> dict[str, object]:
    return {
        "facts": [
            "Ying has a gray car.",
        ],
        "decisions": [
            "Use structured conversation summaries.",
        ],
        "action_items": [
            "Add automated tests.",
        ],
        "unresolved_questions": [
            "When should automatic summarization run?",
        ],
    }


def _valid_summary() -> dict[str, object]:
    return {
        "content": _valid_content(),
        "source_message_count": 4,
        "source_start_timestamp": "2026-08-11 12:00:00",
        "source_end_timestamp": "2026-08-11 12:03:00",
        "updated_at": "2026-08-11 12:04:00",
    }


def test_validate_content_accepts_all_categories() -> None:
    content = _valid_content()

    validated_content = (
        validate_conversation_summary_content(content)
    )

    assert validated_content == content


@pytest.mark.parametrize(
    "invalid_content",
    [
        [],
        {
            "facts": [],
            "decisions": [],
            "action_items": [],
        },
        {
            "facts": [],
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
            "notes": [],
        },
        {
            "facts": "One fact",
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
        },
        {
            "facts": [123],
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
        },
        {
            "facts": ["   "],
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
        },
    ],
)
def test_validate_content_rejects_invalid_data(
    invalid_content: object,
) -> None:
    with pytest.raises(ValueError):
        validate_conversation_summary_content(
            invalid_content
        )


def test_validate_summary_accepts_valid_metadata() -> None:
    summary = _valid_summary()

    validated_summary = validate_conversation_summary(
        summary
    )

    assert validated_summary == summary


@pytest.mark.parametrize(
    "invalid_count",
    [
        0,
        -1,
        True,
        "4",
        4.0,
    ],
)
def test_validate_summary_rejects_invalid_message_count(
    invalid_count: object,
) -> None:
    summary = _valid_summary()
    summary["source_message_count"] = invalid_count

    with pytest.raises(ValueError):
        validate_conversation_summary(summary)


@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
    ),
    [
        (
            "source_start_timestamp",
            "",
        ),
        (
            "source_end_timestamp",
            "   ",
        ),
        (
            "updated_at",
            None,
        ),
    ],
)
def test_validate_summary_rejects_invalid_timestamp(
    field_name: str,
    invalid_value: object,
) -> None:
    summary = _valid_summary()
    summary[field_name] = invalid_value

    with pytest.raises(ValueError):
        validate_conversation_summary(summary)


def test_validate_data_accepts_empty_summary() -> None:
    summary_data = {
        "schema_version": 1,
        "summary": None,
    }

    validated_data = validate_conversation_summary_data(
        summary_data
    )

    assert validated_data == summary_data


@pytest.mark.parametrize(
    "invalid_data",
    [
        [],
        {
            "schema_version": 1,
        },
        {
            "schema_version": 1,
            "summary": None,
            "unexpected": True,
        },
    ],
)
def test_validate_data_rejects_invalid_structure(
    invalid_data: object,
) -> None:
    with pytest.raises(ValueError):
        validate_conversation_summary_data(
            invalid_data
        )


@pytest.mark.parametrize(
    "invalid_version",
    [
        True,
        0,
        2,
        "1",
        1.0,
    ],
)
def test_validate_data_rejects_invalid_schema_version(
    invalid_version: object,
) -> None:
    summary_data = {
        "schema_version": invalid_version,
        "summary": None,
    }

    with pytest.raises(ValueError):
        validate_conversation_summary_data(
            summary_data
        )


def test_load_creates_empty_summary_store(
    tmp_path: Path,
) -> None:
    summary_file = (
        tmp_path / "conversation_summary.json"
    )

    summary_data = load_conversation_summary(
        summary_file
    )

    assert summary_data == {
        "schema_version": 1,
        "summary": None,
    }
    assert summary_file.exists()


def test_save_and_load_summary_round_trip(
    tmp_path: Path,
) -> None:
    summary_file = (
        tmp_path / "conversation_summary.json"
    )
    summary = _valid_summary()

    save_conversation_summary(
        summary_file,
        summary,
    )

    loaded_data = load_conversation_summary(
        summary_file
    )

    assert loaded_data == {
        "schema_version": 1,
        "summary": summary,
    }


def test_save_updates_existing_summary(
    tmp_path: Path,
) -> None:
    summary_file = (
        tmp_path / "conversation_summary.json"
    )
    original_summary = _valid_summary()

    save_conversation_summary(
        summary_file,
        original_summary,
    )

    updated_summary = _valid_summary()
    updated_summary["content"] = {
        "facts": [
            "Ying has a gray car.",
            "Ying is developing Elysia AI.",
        ],
        "decisions": [
            "Use structured conversation summaries.",
        ],
        "action_items": [
            "Run the full test suite.",
        ],
        "unresolved_questions": [],
    }
    updated_summary["source_message_count"] = 6
    updated_summary["source_end_timestamp"] = (
        "2026-08-11 12:05:00"
    )
    updated_summary["updated_at"] = (
        "2026-08-11 12:06:00"
    )

    save_conversation_summary(
        summary_file,
        updated_summary,
    )

    loaded_data = load_conversation_summary(
        summary_file
    )

    assert loaded_data["summary"] == updated_summary


def test_invalid_update_preserves_existing_summary(
    tmp_path: Path,
) -> None:
    summary_file = (
        tmp_path / "conversation_summary.json"
    )
    valid_summary = _valid_summary()

    save_conversation_summary(
        summary_file,
        valid_summary,
    )

    original_file_content = summary_file.read_text(
        encoding="utf-8"
    )

    invalid_summary = _valid_summary()
    invalid_summary["source_message_count"] = 0

    with pytest.raises(ValueError):
        save_conversation_summary(
            summary_file,
            invalid_summary,
        )

    assert (
        summary_file.read_text(encoding="utf-8")
        == original_file_content
    )