from pathlib import Path

import pytest

from memory.file_manager import (
    append_text,
    read_lines,
    read_text,
    write_text,
)


def test_write_text_creates_parent_directories(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "nested" / "notes.txt"
    content = "你好，Elysia！"

    write_text(file_path, content)

    assert file_path.exists()
    assert read_text(file_path) == content


def test_append_text_preserves_existing_content(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "conversation.txt"

    write_text(file_path, "First message\n")
    append_text(file_path, "Second message")

    assert read_lines(file_path) == [
        "First message",
        "Second message",
    ]


def test_read_text_rejects_missing_file(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "missing.txt"

    with pytest.raises(
        FileNotFoundError,
        match=r"File does not exist:",
    ):
        read_text(file_path)


def test_read_lines_rejects_missing_file(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "missing.txt"

    with pytest.raises(
        FileNotFoundError,
        match=r"File does not exist:",
    ):
        read_lines(file_path)
