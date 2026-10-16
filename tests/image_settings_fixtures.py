"""Provide an explicit native-preflight seam for portable image settings tests.

Windows exercises its real platform and fixed-volume checks. Other hosts can
exercise the persistence/DTO contract with the managed runtime's sealed test
factory, retaining its real hashes, path checks, and Python environment review.
Unsupported production hosts are tested separately without this fixture.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

import desktop_image_settings as image_settings
import image_generation.managed_comfyui as managed


@pytest.fixture
def supported_image_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only native availability for explicitly opted-in contract tests."""

    if os.name == "nt":
        return

    def _reviewed_fixture_volume(path: Path) -> None:
        """Accept the pytest-owned local fixture instead of invoking Win32 APIs."""

    dependencies = replace(
        managed._production_dependencies(),
        platform_name="nt",
        path_volume_verifier=_reviewed_fixture_volume,
    )

    def _runtime(config: managed.ManagedComfyUiConfig) -> managed.ManagedComfyUiRuntime:
        return managed.ManagedComfyUiRuntime._create_for_testing(
            config, dependencies, dependency_seal=managed._TEST_DEPENDENCY_SEAL,
        )

    # Never patch os.name or a process-wide native adapter: unrelated runtime
    # tests must keep proving that unsupported production hosts fail closed.
    monkeypatch.setattr(image_settings, "ManagedComfyUiRuntime", _runtime)
