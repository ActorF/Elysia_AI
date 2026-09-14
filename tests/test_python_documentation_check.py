"""Test source discovery for the Python documentation policy checker."""

from pathlib import Path

from scripts import check_python_documentation


def test_source_discovery_prunes_external_runtime_caches(tmp_path: Path) -> None:
    """Keep downloaded model runtimes outside maintained-source audits."""

    maintained = tmp_path / "models" / "source.py"
    external = tmp_path / "models" / "cache" / "runtime" / "external.py"
    maintained_cache = tmp_path / "core" / "cache" / "source.py"
    maintained.parent.mkdir(parents=True)
    external.parent.mkdir(parents=True)
    maintained_cache.parent.mkdir(parents=True)
    maintained.write_text('"""Maintained source."""\n', encoding="utf-8")
    external.write_text("third-party source without docs\n", encoding="utf-8")
    maintained_cache.write_text(
        '"""Maintained cache implementation."""\n',
        encoding="utf-8",
    )

    discovered = set(check_python_documentation._iter_python_files(tmp_path))

    assert maintained in discovered
    assert maintained_cache in discovered
    assert external not in discovered
