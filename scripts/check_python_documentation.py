"""Enforce module and public-API docstrings across Python source files."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sys
import tokenize
from typing import Iterable


_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "dist-electron",
        "node_modules",
        "out",
        "playwright-report",
        "test-results",
        "temp",
        "tmp",
        "workspace",
    }
)
_EXCLUDED_RELATIVE_DIRECTORIES = frozenset({Path("models") / "cache"})
_PYTHON_SOURCE_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})


@dataclass(frozen=True, slots=True)
class DocumentationProblem:
    """Describe one missing or invalid Python documentation requirement."""

    path: Path
    line: int
    message: str


def _iter_python_files(root: Path) -> Iterable[Path]:
    """Yield maintained Python sources while pruning generated/runtime trees."""

    for directory, child_directories, file_names in root.walk(top_down=True):
        relative_directory = directory.relative_to(root)
        child_directories[:] = [
            name
            for name in child_directories
            if name not in _EXCLUDED_DIRECTORIES
            and not name.startswith((".test-tmp", "pytest-cache-files-"))
            and relative_directory / name
            not in _EXCLUDED_RELATIVE_DIRECTORIES
        ]
        for file_name in file_names:
            path = directory / file_name
            if path.suffix.casefold() in _PYTHON_SOURCE_SUFFIXES:
                yield path


def _is_public(name: str) -> bool:
    """Return whether a Python name belongs to the documented public surface."""

    return not name.startswith("_")


def _has_docstring(
    node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return whether an AST module, class, or callable has a non-empty docstring."""

    docstring = ast.get_docstring(node, clean=False)
    return docstring is not None and bool(docstring.strip())


def _iter_scope_declarations(
    nodes: Iterable[ast.AST],
) -> Iterable[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield declarations through control-flow blocks without entering callables.

    Platform-specific APIs are often declared under ``if`` or ``try`` blocks.
    Stopping at each declaration keeps nested local functions private while
    still finding declarations in module and class control-flow suites.
    """

    for node in nodes:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
            continue
        yield from _iter_scope_declarations(ast.iter_child_nodes(node))


def _check_class(
    path: Path,
    node: ast.ClassDef,
    problems: list[DocumentationProblem],
) -> None:
    """Check a class plus its public methods and nested class declarations."""

    if _is_public(node.name) and not _has_docstring(node):
        problems.append(
            DocumentationProblem(path, node.lineno, f"public class {node.name!r} lacks a docstring")
        )

    for child in _iter_scope_declarations(node.body):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public(child.name) and not _has_docstring(child):
                problems.append(
                    DocumentationProblem(
                        path,
                        child.lineno,
                        f"public method {node.name}.{child.name} lacks a docstring",
                    )
                )
        else:
            _check_class(path, child, problems)


def _check_file(root: Path, path: Path) -> list[DocumentationProblem]:
    """Parse one Python file and return all structural documentation failures."""

    relative_path = path.relative_to(root)
    try:
        with tokenize.open(path) as stream:
            source = stream.read()
        module = ast.parse(source, filename=str(relative_path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        line = getattr(exc, "lineno", None) or 1
        return [DocumentationProblem(relative_path, line, f"cannot parse source: {exc}")]

    problems: list[DocumentationProblem] = []
    if not _has_docstring(module):
        problems.append(DocumentationProblem(relative_path, 1, "module lacks a docstring"))

    for node in _iter_scope_declarations(module.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public(node.name) and not _has_docstring(node):
                problems.append(
                    DocumentationProblem(
                        relative_path,
                        node.lineno,
                        f"public function {node.name!r} lacks a docstring",
                    )
                )
        else:
            _check_class(relative_path, node, problems)

    return problems


def main() -> int:
    """Audit the repository and return a process status suitable for local CI."""

    repository_root = Path(__file__).resolve().parents[1]
    files = sorted(_iter_python_files(repository_root))
    problems = [
        problem
        for path in files
        for problem in _check_file(repository_root, path)
    ]

    if problems:
        for problem in problems:
            print(f"{problem.path}:{problem.line}: {problem.message}")
        print(f"Python documentation check failed: {len(problems)} problem(s).")
        return 1

    print(f"Python documentation check passed: {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
