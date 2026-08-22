"""Provide atomic UTF-8 JSON storage for project repository data."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import mkstemp
from typing import cast

from .exceptions import ProjectDataCorruptionError, ProjectStorageError

JsonObject = dict[str, object]


def atomic_write_json(
    file_path: Path,
    data: Mapping[str, object],
) -> None:
    """Replace project JSON atomically through a same-directory temp file."""

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = mkstemp(
        dir=file_path.parent,
        prefix=f".{file_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as temp_file:
            file_descriptor = -1
            json.dump(
                data,
                temp_file,
                ensure_ascii=False,
                indent=2,
            )
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, file_path)
    except (OSError, TypeError, ValueError) as error:
        raise ProjectStorageError(
            f"Could not atomically write project data: {file_path.name}."
        ) from error
    finally:
        if file_descriptor != -1:
            os.close(file_descriptor)

        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def read_json_object(file_path: Path) -> JsonObject:
    """Read one UTF-8 JSON object and translate storage failures."""

    try:
        with file_path.open("r", encoding="utf-8") as json_file:
            loaded_data: object = json.load(json_file)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as error:
        raise ProjectDataCorruptionError(
            f"Stored project JSON is invalid: {file_path.name}."
        ) from error
    except UnicodeError as error:
        raise ProjectDataCorruptionError(
            f"Stored project JSON is not valid UTF-8: {file_path.name}."
        ) from error
    except OSError as error:
        raise ProjectStorageError(
            f"Could not read project data: {file_path.name}."
        ) from error

    if not isinstance(loaded_data, dict) or not all(
        isinstance(key, str) for key in loaded_data
    ):
        raise ProjectDataCorruptionError(
            f"Stored project JSON root must be an object: {file_path.name}."
        )

    return cast(JsonObject, loaded_data)
