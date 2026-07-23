import json
import pytest

from pathlib import Path
from memory.json_store import (
    load_json_or_default,
    read_json,
    write_json,
)

def test_write_json_creates_file(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "data.json"
    data: dict[str, object] = {
        "message": "Hello, Elysia!",
        "launch_count": 1,
    }

    write_json(file_path, data)

    assert file_path.exists()

    saved_data = json.loads(
        file_path.read_text(encoding="utf-8")
    )
    assert saved_data == data


def test_read_json_returns_saved_data(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "data.json"
    expected_data: dict[str, object] = {
        "message": "Hello, Elysia!",
        "launch_count": 1,
    }

    file_path.write_text(
        json.dumps(expected_data),
        encoding="utf-8",
    )

    actual_data = read_json(file_path)

    assert actual_data == expected_data



def test_read_json_rejects_non_object_json(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "data.json"

    file_path.write_text(
        '["message 1", "message 2"]',
        encoding="utf-8",
    )

    with pytest.raises(
        TypeError,
        match=r"JSON root must be an object\.",
    ):
        read_json(file_path)


def test_read_json_rejects_invalid_json(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "invalid.json"

    file_path.write_text(
        '{"message":',
        encoding="utf-8",
    )

    with pytest.raises(json.JSONDecodeError):
        read_json(file_path)


def test_read_json_rejects_missing_file(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "missing.json"

    with pytest.raises(FileNotFoundError):
        read_json(file_path)


def test_load_json_or_default_creates_missing_file(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "profile.json"
    default_data: dict[str, object] = {
        "name": "",
        "language": "English",
    }

    loaded_data = load_json_or_default(
        file_path,
        default_data,
    )

    assert loaded_data == default_data
    assert file_path.exists()
    assert read_json(file_path) == default_data
