"""Verify the movable production-data layout and its composition boundaries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import desktop_knowledge
import start
from attachments import AttachmentService, JsonAttachmentStore
from config.data_layout import ProductionDataLayout
from config.desktop_settings import create_desktop_settings_repository
from config.settings import AppSettings, parse_data_root
from desktop_speech import DesktopSpeechConfig


class _OfflineChatModel:
    """Stand in for Ollama while preserving the startup model contract."""

    def __init__(self, model_name: str, ollama_host: str) -> None:
        """Record the exact model identity selected by the composition root."""

        self.model_name = model_name
        self.ollama_host = ollama_host

    def ensure_model_available(self) -> None:
        """Accept the synthetic model without performing network I/O."""


def _settings(resource_root: Path, data_root: Path | None) -> AppSettings:
    """Return a minimal settings snapshot with independently owned roots."""

    return AppSettings(
        base_dir=resource_root,
        data_root=data_root,
        model_name="fixture-model:1",
        log_level="INFO",
        debug=False,
        ollama_host="http://127.0.0.1:11434",
    )


def test_layout_maps_every_declared_category_below_the_exact_root(
    tmp_path: Path,
) -> None:
    """Keep each durable, transient, cache, and log category deterministic."""

    root = tmp_path / "private-data"
    layout = ProductionDataLayout(root)

    assert layout.root == root
    assert layout.workspace == root / "workspace"
    assert layout.settings == root / "workspace" / "settings"
    assert layout.chats == root / "workspace" / "chats"
    assert layout.projects == root / "workspace" / "projects"
    assert layout.memory == root / "workspace" / "memory"
    assert layout.attachments == root / "workspace" / "attachments"
    assert layout.knowledge == root / "workspace" / "knowledge"
    assert layout.audio == root / "audio"
    assert layout.cache == root / "cache"
    assert layout.logs == root / "logs"


@pytest.mark.parametrize("invalid_root", [Path("relative"), Path("a/b")])
def test_layout_rejects_relative_roots(invalid_root: Path) -> None:
    """Reject roots whose meaning could change with the process directory."""

    with pytest.raises(ValueError, match="root must be absolute"):
        ProductionDataLayout(invalid_root)


def test_layout_rejects_non_path_roots() -> None:
    """Reject untyped roots before services can derive child locations."""

    with pytest.raises(TypeError, match="root must be a Path"):
        ProductionDataLayout(cast(Any, "C:/ElysiaData"))


def test_missing_data_root_preserves_the_source_compatible_layout(
    tmp_path: Path,
) -> None:
    """Keep console and tests compatible when ELYSIA_DATA_ROOT is absent."""

    resource_root = tmp_path / "checkout"
    settings = _settings(resource_root, parse_data_root(None))

    assert settings.data_root is None
    assert settings.data_layout.root == resource_root.absolute()
    assert settings.data_layout.workspace == resource_root / "workspace"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "relative/data", "../escape", "C:\\unsafe\x00tail"],
)
def test_explicit_invalid_environment_data_roots_fail_closed(value: str) -> None:
    """Never turn a malformed ELYSIA_DATA_ROOT into an install-root fallback."""

    with pytest.raises(
        ValueError,
        match="ELYSIA_DATA_ROOT must be a non-empty absolute path",
    ):
        parse_data_root(value)


def test_absolute_environment_data_root_is_trimmed_and_preserved(
    tmp_path: Path,
) -> None:
    """Accept the absolute root injected by the trusted desktop process owner."""

    data_root = tmp_path / "Private Data"

    assert parse_data_root(f"  {data_root}  ") == data_root


def test_app_settings_keep_resource_and_private_data_roots_separate(
    tmp_path: Path,
) -> None:
    """Retain replaceable resources while routing writable state elsewhere."""

    resource_root = tmp_path / "installed-backend"
    data_root = tmp_path / "user-data"
    settings = _settings(resource_root, data_root)

    assert settings.base_dir == resource_root
    assert settings.data_root == data_root
    assert settings.data_layout.root == data_root
    assert settings.data_layout.settings == data_root / "workspace" / "settings"
    assert not settings.data_layout.workspace.is_relative_to(resource_root)


def test_brain_writes_chat_project_and_memory_only_below_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the main Python composition no longer persists beside resources."""

    resource_root = tmp_path / "installed-backend"
    data_root = tmp_path / "user-data"
    resource_root.mkdir()
    settings = _settings(resource_root, data_root)
    monkeypatch.setattr(start, "LangChainOllamaChatModel", _OfflineChatModel)

    brain = start.create_brain(settings)
    brain.start_session()
    chat = brain.create_chat(title="Layout Chat")
    brain.create_project(name="Layout Project")

    assert (data_root / "workspace" / "memory" / "profile.json").is_file()
    assert (data_root / "workspace" / "chats" / "index.json").is_file()
    assert (
        data_root / "workspace" / "chats" / "sessions" / f"{chat.chat_id}.json"
    ).is_file()
    assert (data_root / "workspace" / "projects" / "projects.json").is_file()
    assert not (resource_root / "workspace").exists()


def test_desktop_settings_repository_writes_only_below_data_root(
    tmp_path: Path,
) -> None:
    """Keep revisioned global Settings outside the replaceable installation."""

    resource_root = tmp_path / "installed-backend"
    data_root = tmp_path / "user-data"
    resource_root.mkdir()
    settings = _settings(resource_root, data_root)
    repository = create_desktop_settings_repository(settings)
    initial = repository.load()
    changed = replace(initial.values, model_name="saved-model:2")

    repository.save(changed, expected_revision=initial.revision)

    assert repository.path == data_root / "workspace" / "settings" / "global.json"
    assert repository.path.is_file()
    assert not (resource_root / "workspace").exists()


def test_knowledge_runtime_owns_sources_indexes_and_intents_below_data_root(
    tmp_path: Path,
) -> None:
    """Route every writable Project Knowledge path away from resources."""

    resource_root = tmp_path / "installed-backend"
    data_root = tmp_path / "user-data"
    resource_root.mkdir()
    settings = _settings(resource_root, data_root)
    layout = settings.data_layout
    attachments = AttachmentService(
        JsonAttachmentStore(layout.attachments, max_file_bytes=1_048_576)
    )

    runtime = desktop_knowledge.create_desktop_knowledge_runtime(
        settings,
        attachments,
    )
    try:
        lifecycle = cast(Any, runtime.lifecycle)
        answers = cast(Any, runtime.answers)
        export = cast(Any, runtime.export)
        assert runtime.store._database_path == layout.knowledge / "vectors.sqlite3"
        assert (
            lifecycle._project_repository._storage_directory
            == layout.projects
        )
        assert (
            lifecycle._source_repository._storage_directory
            == layout.knowledge / "project-sources"
        )
        assert (
            lifecycle._operation_repository._storage_directory
            == layout.knowledge / "operations"
        )
        assert answers._chat_repository._storage_directory == layout.chats
        assert export._intent_directory == layout.knowledge / "export-intents"
        assert not (resource_root / "workspace").exists()
    finally:
        runtime.close()


def test_speech_config_keeps_assets_read_only_and_catalog_in_data_root(
    tmp_path: Path,
) -> None:
    """Separate install-owned speech code and models from writable profiles."""

    resource_root = (tmp_path / "installed-backend").resolve()
    data_root = (tmp_path / "user-data").resolve()
    settings = _settings(resource_root, data_root)

    config = DesktopSpeechConfig.from_app_settings(settings)

    assert config.worker_script == resource_root / "scripts" / "gpt_sovits_worker.py"
    assert config.runtime_root == (
        resource_root / "models" / "cache" / "GPT-SoVITS-v2-240821"
    )
    assert config.asset_root == resource_root / "models" / "weights" / "gpt-sovits"
    assert config.catalog_path == (
        data_root / "workspace" / "settings" / "voice-profiles.json"
    )
    assert not (resource_root / "workspace").exists()
