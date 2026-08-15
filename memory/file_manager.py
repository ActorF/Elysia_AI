"""Provide small UTF-8 file operations shared by memory modules."""

from pathlib import Path


def ensure_parent_directory(file_path: Path) -> None:
    """Create the target file's parent directories when they are missing."""

    file_path.parent.mkdir(parents=True, exist_ok=True)


def write_text(file_path: Path, content: str) -> None:
    """Replace a file with UTF-8 text, creating parent directories first."""

    ensure_parent_directory(file_path)

    with file_path.open("w", encoding="utf-8") as file:
        file.write(content)


def append_text(file_path: Path, content: str) -> None:
    """Append one UTF-8 text line, creating the file path when necessary."""

    ensure_parent_directory(file_path)

    with file_path.open("a", encoding="utf-8") as file:
        file.write(content + "\n")


def read_text(file_path: Path) -> str:
    """Read an existing UTF-8 text file in full.

    Raises:
        FileNotFoundError: If ``file_path`` does not exist.
    """

    if not file_path.exists():
        raise FileNotFoundError(f"File does not exist: {file_path}")

    with file_path.open("r", encoding="utf-8") as file:
        return file.read()

def read_lines(file_path: Path) -> list[str]:
    """Read UTF-8 lines while removing only their trailing newline markers.

    Raises:
        FileNotFoundError: If ``file_path`` does not exist.
    """

    if not file_path.exists():
        raise FileNotFoundError(f"File does not exist: {file_path}")

    with file_path.open("r", encoding="utf-8") as file:
        return [line.rstrip("\n") for line in file]
