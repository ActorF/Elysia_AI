from pathlib import Path


def ensure_parent_directory(file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)


def write_text(file_path: Path, content: str) -> None:
    ensure_parent_directory(file_path)

    with file_path.open("w", encoding="utf-8") as file:
        file.write(content)


def append_text(file_path: Path, content: str) -> None:
    ensure_parent_directory(file_path)

    with file_path.open("a", encoding="utf-8") as file:
        file.write(content + "\n")


def read_text(file_path: Path) -> str:
    if not file_path.exists():
        raise FileNotFoundError(f"File does not exist: {file_path}")

    with file_path.open("r", encoding="utf-8") as file:
        return file.read()

def read_lines(file_path: Path) -> list[str]:
    if not file_path.exists():
        raise FileNotFoundError(f"File does not exist: {file_path}")

    with file_path.open("r", encoding="utf-8") as file:
        return [line.rstrip("\n") for line in file]