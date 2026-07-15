import json
from pathlib import Path

from memory.file_manager import ensure_parent_directory


def write_json(file_path: Path, data: dict) -> None:
    ensure_parent_directory(file_path)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4,
        )

def read_json(file_path: Path) -> dict:
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {file_path}")

    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)

def load_json_or_default(
    file_path: Path,
    default_data: dict,
) -> dict:
    if file_path.exists():
        return read_json(file_path)

    write_json(file_path, default_data)
    return default_data.copy()