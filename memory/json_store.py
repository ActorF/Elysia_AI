import json
from pathlib import Path
from collections.abc import Mapping
from memory.file_manager import ensure_parent_directory
from typing import TypeVar, cast

JsonDataT = TypeVar(
    "JsonDataT",
    bound=Mapping[str, object],
)

def write_json(
        file_path: Path,
        data: Mapping[str, object]
    ) -> None:
    ensure_parent_directory(file_path)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4,
        )

def read_json(
    file_path: Path,
) -> dict[str, object]:
    with file_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        data: object = json.load(file)

    if not isinstance(data, dict):
        raise TypeError(
            "JSON root must be an object."
        )

    return cast(dict[str, object], data)

def load_json_or_default(
    file_path: Path,
    default_data: JsonDataT,
) -> JsonDataT:
    if file_path.exists():
        return cast(JsonDataT, read_json(file_path))

    write_json(file_path, default_data)
    return cast(JsonDataT, dict(default_data))