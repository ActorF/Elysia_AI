"""Read and write dictionary-shaped JSON data used by memory stores."""

import json
from pathlib import Path
from collections.abc import Mapping
from memory.file_manager import ensure_parent_directory
from typing import TypeVar, cast

# Preserve the caller's mapping type, including TypedDict schemas, on load.
JsonDataT = TypeVar(
    "JsonDataT",
    bound=Mapping[str, object],
)

def write_json(
        file_path: Path,
        data: Mapping[str, object]
    ) -> None:
    """Serialize mapping data as readable UTF-8 JSON.

    Parent directories are created automatically, and non-ASCII characters
    remain readable instead of being escaped.
    """

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
    """Load a JSON file whose top-level value must be an object.

    Raises:
        TypeError: If the decoded JSON root is not a dictionary.
        json.JSONDecodeError: If the file does not contain valid JSON.
    """

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
    """Load an existing JSON object or initialize it from ``default_data``.

    A shallow copy is returned for a newly created store so later mutations do
    not modify the caller's default mapping object.
    """

    if file_path.exists():
        return cast(JsonDataT, read_json(file_path))

    write_json(file_path, default_data)
    return cast(JsonDataT, dict(default_data))
