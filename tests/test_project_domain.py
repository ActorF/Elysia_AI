from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

import pytest

from projects import (
    PROJECT_SCHEMA_VERSION,
    Project,
    ProjectId,
    ProjectSettings,
    WorkspaceBinding,
    create_project,
)

BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _project() -> Project:
    return Project(
        schema_version=PROJECT_SCHEMA_VERSION,
        project_id=ProjectId("project_test"),
        name="Elysia AI",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
        settings=ProjectSettings(
            default_model_name="qwen3.5:9b",
            custom_instructions="Keep changes local.",
        ),
        workspace_binding=WorkspaceBinding(
            root_path=r"D:\Elysia_AI"
        ),
    )


def test_create_project_builds_complete_default_entity() -> None:
    project = create_project(
        name="New project",
        created_at=BASE_TIME,
    )

    assert project.schema_version == 1
    assert project.project_id.startswith("project_")
    assert project.project_id != project.name
    assert project.created_at == BASE_TIME
    assert project.updated_at == BASE_TIME
    assert project.settings == ProjectSettings()
    assert project.workspace_binding is None
    assert project.is_archived is False


def test_name_and_workspace_path_do_not_determine_project_id() -> None:
    workspace = WorkspaceBinding(root_path=r"D:\Shared")

    first = create_project(
        name="Same name",
        workspace_binding=workspace,
        created_at=BASE_TIME,
    )
    second = create_project(
        name="Same name",
        workspace_binding=workspace,
        created_at=BASE_TIME,
    )

    assert first.project_id != second.project_id
    assert first.workspace_binding == second.workspace_binding


@pytest.mark.parametrize(
    "root_path",
    [r"D:\Elysia_AI", r"\\server\share\project", "/opt/elysia"],
)
def test_workspace_binding_accepts_absolute_cross_platform_paths(
    root_path: str,
) -> None:
    assert WorkspaceBinding(root_path=root_path).root_path == root_path


@pytest.mark.parametrize(
    "root_path, error_message",
    [
        ("", "non-empty string"),
        ("relative/project", "absolute path"),
        (" D:\\Elysia_AI", "surrounding whitespace"),
        ("D:\\bad\x00path", "null byte"),
    ],
)
def test_workspace_binding_rejects_ambiguous_paths(
    root_path: str,
    error_message: str,
) -> None:
    with pytest.raises(ValueError, match=error_message):
        WorkspaceBinding(root_path=root_path)


@pytest.mark.parametrize(
    "field_name",
    ["default_model_name", "custom_instructions"],
)
def test_project_settings_reject_empty_optional_text(
    field_name: str,
) -> None:
    values: dict[str, str | None] = {
        "default_model_name": None,
        "custom_instructions": None,
    }
    values[field_name] = "   "

    with pytest.raises(ValueError, match=field_name):
        ProjectSettings(**values)


def test_project_rejects_invalid_stable_id() -> None:
    with pytest.raises(ValueError, match=r"project_<id>"):
        replace(_project(), project_id=ProjectId("Elysia AI"))


def test_project_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match=r"schema version"):
        replace(
            _project(),
            schema_version=cast(Literal[1], 2),
        )


def test_project_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match=r"timezone-aware"):
        replace(
            _project(),
            updated_at=datetime(2026, 8, 21, 12, 0),
        )


def test_project_rejects_update_before_creation() -> None:
    with pytest.raises(ValueError, match=r"earlier than created_at"):
        replace(
            _project(),
            updated_at=BASE_TIME - timedelta(seconds=1),
        )


def test_project_rejects_non_boolean_archive_status() -> None:
    with pytest.raises(ValueError, match=r"is_archived must be a boolean"):
        replace(_project(), is_archived=cast(bool, 1))
