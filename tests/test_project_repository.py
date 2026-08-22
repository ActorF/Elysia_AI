import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from projects import (
    JsonProjectRepository,
    ProjectDataCorruptionError,
    ProjectId,
    ProjectNotFoundError,
    ProjectRepository,
    ProjectSettings,
    ProjectStorageError,
    WorkspaceBinding,
)

BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _clock(*times: datetime) -> Callable[[], datetime]:
    time_iterator = iter(times)
    return lambda: next(time_iterator)


def _storage_directory(tmp_path: Path) -> Path:
    return tmp_path / "data" / "projects"


def test_complete_project_survives_repository_restart(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository: ProjectRepository = JsonProjectRepository(
        storage_directory,
        clock=_clock(BASE_TIME),
    )
    settings = ProjectSettings(
        default_model_name="qwen3.5:9b",
        custom_instructions="Use repository boundaries.",
    )
    workspace = WorkspaceBinding(root_path=r"D:\Elysia_AI")

    project = repository.create_project(
        name="Elysia AI",
        settings=settings,
        workspace_binding=workspace,
    )

    restarted_repository = JsonProjectRepository(storage_directory)
    assert restarted_repository.get_project(project.project_id) == project
    assert restarted_repository.list_projects() == (project,)


def test_duplicate_names_and_workspace_paths_keep_distinct_ids(
    tmp_path: Path,
) -> None:
    repository = JsonProjectRepository(
        _storage_directory(tmp_path),
        clock=lambda: BASE_TIME,
    )
    workspace = WorkspaceBinding(root_path=r"D:\Shared")

    first = repository.create_project(
        name="Same",
        workspace_binding=workspace,
    )
    second = repository.create_project(
        name="Same",
        workspace_binding=workspace,
    )

    assert first.project_id != second.project_id
    assert len(repository.list_projects()) == 2


def test_project_updates_are_persisted_and_timestamped(
    tmp_path: Path,
) -> None:
    repository = JsonProjectRepository(
        _storage_directory(tmp_path),
        clock=_clock(
            BASE_TIME,
            BASE_TIME + timedelta(minutes=1),
            BASE_TIME + timedelta(minutes=2),
            BASE_TIME + timedelta(minutes=3),
            BASE_TIME + timedelta(minutes=4),
        ),
    )
    project = repository.create_project(name="Old name")

    renamed = repository.rename_project(
        project.project_id,
        "New name",
    )
    settings = ProjectSettings(
        default_model_name="qwen3.5:9b",
        custom_instructions="Stay offline.",
    )
    updated_settings = repository.update_settings(
        project.project_id,
        settings,
    )
    workspace = WorkspaceBinding(root_path=r"D:\Workspace")
    bound = repository.set_workspace_binding(
        project.project_id,
        workspace,
    )
    unbound = repository.set_workspace_binding(
        project.project_id,
        None,
    )

    assert renamed.name == "New name"
    assert renamed.project_id == project.project_id
    assert renamed.updated_at == BASE_TIME + timedelta(minutes=1)
    assert updated_settings.settings == settings
    assert bound.workspace_binding == workspace
    assert unbound.workspace_binding is None
    assert (
        JsonProjectRepository(_storage_directory(tmp_path))
        .get_project(project.project_id)
        == unbound
    )


def test_archive_hides_and_restore_returns_project(
    tmp_path: Path,
) -> None:
    repository = JsonProjectRepository(
        _storage_directory(tmp_path),
        clock=lambda: BASE_TIME,
    )
    project = repository.create_project(name="Archive me")

    archived = repository.archive_project(project.project_id)

    assert archived.is_archived is True
    assert repository.list_projects() == ()
    assert repository.list_projects(include_archived=True) == (archived,)

    restored = repository.archive_project(
        project.project_id,
        archived=False,
    )

    assert restored.is_archived is False
    assert repository.list_projects() == (restored,)


def test_list_projects_orders_latest_update_first(
    tmp_path: Path,
) -> None:
    repository = JsonProjectRepository(
        _storage_directory(tmp_path),
        clock=_clock(
            BASE_TIME,
            BASE_TIME + timedelta(minutes=1),
        ),
    )
    older = repository.create_project(name="Older")
    newer = repository.create_project(name="Newer")

    assert [
        project.project_id for project in repository.list_projects()
    ] == [newer.project_id, older.project_id]


def test_delete_project_removes_persisted_record(
    tmp_path: Path,
) -> None:
    repository = JsonProjectRepository(
        _storage_directory(tmp_path),
        clock=lambda: BASE_TIME,
    )
    project = repository.create_project(name="Delete me")

    repository.delete_project(project.project_id)

    assert repository.list_projects(include_archived=True) == ()
    with pytest.raises(ProjectNotFoundError):
        repository.get_project(project.project_id)


@pytest.mark.parametrize(
    "operation",
    ["get", "rename", "settings", "workspace", "archive", "delete"],
)
def test_missing_project_operations_raise_not_found(
    tmp_path: Path,
    operation: str,
) -> None:
    repository = JsonProjectRepository(_storage_directory(tmp_path))
    missing_id = ProjectId("project_missing")

    with pytest.raises(ProjectNotFoundError):
        if operation == "get":
            repository.get_project(missing_id)
        elif operation == "rename":
            repository.rename_project(missing_id, "New name")
        elif operation == "settings":
            repository.update_settings(missing_id, ProjectSettings())
        elif operation == "workspace":
            repository.set_workspace_binding(missing_id, None)
        elif operation == "archive":
            repository.archive_project(missing_id)
        else:
            repository.delete_project(missing_id)


def test_atomic_replace_failure_preserves_existing_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonProjectRepository(
        storage_directory,
        clock=_clock(BASE_TIME, BASE_TIME),
    )
    project = repository.create_project(name="Original name")
    store_file = storage_directory / "projects.json"
    original_text = store_file.read_text(encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("projects.storage.os.replace", fail_replace)

    with pytest.raises(ProjectStorageError):
        repository.rename_project(project.project_id, "Lost name")

    assert store_file.read_text(encoding="utf-8") == original_text
    assert list(storage_directory.rglob("*.tmp")) == []
    assert repository.get_project(project.project_id).name == "Original name"


def test_corrupt_project_json_is_reported_explicitly(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonProjectRepository(
        storage_directory,
        clock=lambda: BASE_TIME,
    )
    repository.create_project(name="Corrupt me")
    (storage_directory / "projects.json").write_text(
        '{"broken":',
        encoding="utf-8",
    )

    with pytest.raises(ProjectDataCorruptionError):
        repository.list_projects()


def test_unknown_project_field_is_not_silently_accepted(
    tmp_path: Path,
) -> None:
    storage_directory = _storage_directory(tmp_path)
    repository = JsonProjectRepository(
        storage_directory,
        clock=lambda: BASE_TIME,
    )
    repository.create_project(name="Strict schema")
    store_file = storage_directory / "projects.json"
    store_data = json.loads(store_file.read_text(encoding="utf-8"))
    store_data["projects"][0]["unknown"] = "pollution"
    store_file.write_text(json.dumps(store_data), encoding="utf-8")

    with pytest.raises(
        ProjectDataCorruptionError,
        match=r"does not match its schema",
    ):
        repository.list_projects()


def test_project_repository_rejects_naive_clock(tmp_path: Path) -> None:
    repository = JsonProjectRepository(
        _storage_directory(tmp_path),
        clock=lambda: datetime(2026, 8, 21, 12, 0),
    )

    with pytest.raises(ValueError, match=r"timezone-aware"):
        repository.create_project(name="Invalid time")
