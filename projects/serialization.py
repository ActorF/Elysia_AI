"""Convert validated project entities to and from JSON-shaped data."""

from collections.abc import Iterable, Mapping, Set
from datetime import datetime, timezone
from typing import Final, Literal, cast

from chats.domain import ProjectId

from .domain import (
    PROJECT_SCHEMA_VERSION,
    Project,
    ProjectSettings,
    WorkspaceBinding,
)
from .storage import JsonObject

PROJECT_STORE_SCHEMA_VERSION: Final[Literal[1]] = 1


def _require_exact_fields(
    data: Mapping[str, object],
    expected_fields: Set[str],
    context: str,
) -> None:
    """Reject missing or unknown stored fields for the active schema."""

    actual_fields = set(data)
    missing_fields = sorted(expected_fields - actual_fields)
    unknown_fields = sorted(actual_fields - expected_fields)

    if missing_fields:
        raise ValueError(
            f"{context} is missing fields: {', '.join(missing_fields)}."
        )

    if unknown_fields:
        raise ValueError(
            f"{context} contains unknown fields: "
            f"{', '.join(unknown_fields)}."
        )


def _as_object(value: object, field_name: str) -> JsonObject:
    """Require a dictionary with string keys."""

    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{field_name} must be an object.")

    return cast(JsonObject, value)


def _as_list(value: object, field_name: str) -> list[object]:
    """Require a JSON array."""

    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array.")

    return cast(list[object], value)


def _as_string(value: object, field_name: str) -> str:
    """Require a JSON string."""

    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")

    return value


def _as_optional_string(value: object, field_name: str) -> str | None:
    """Require either null or a JSON string."""

    if value is None:
        return None

    return _as_string(value, field_name)


def _as_integer(value: object, field_name: str) -> int:
    """Require an integer while rejecting booleans."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")

    return value


def _as_boolean(value: object, field_name: str) -> bool:
    """Require a real JSON boolean."""

    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")

    return value


def _datetime_to_text(value: datetime) -> str:
    """Serialize one aware timestamp as normalized UTC ISO 8601."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Stored timestamps must be timezone-aware.")

    return value.astimezone(timezone.utc).isoformat()


def _datetime_from_value(value: object, field_name: str) -> datetime:
    """Parse one timezone-aware ISO 8601 timestamp."""

    timestamp_text = _as_string(value, field_name)

    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be an ISO 8601 datetime."
        ) from error

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone.")

    return timestamp


def _settings_to_data(settings: ProjectSettings) -> JsonObject:
    """Serialize project-owned behavior settings."""

    return {
        "default_model_name": settings.default_model_name,
        "custom_instructions": settings.custom_instructions,
    }


def _settings_from_value(value: object) -> ProjectSettings:
    """Build validated settings from stored JSON."""

    data = _as_object(value, "settings")
    _require_exact_fields(
        data,
        {"default_model_name", "custom_instructions"},
        "settings",
    )

    return ProjectSettings(
        default_model_name=_as_optional_string(
            data["default_model_name"],
            "default_model_name",
        ),
        custom_instructions=_as_optional_string(
            data["custom_instructions"],
            "custom_instructions",
        ),
    )


def _workspace_to_data(binding: WorkspaceBinding) -> JsonObject:
    """Serialize a workspace path as a project property."""

    return {"root_path": binding.root_path}


def _workspace_from_value(value: object) -> WorkspaceBinding:
    """Build one validated workspace binding from stored JSON."""

    data = _as_object(value, "workspace_binding")
    _require_exact_fields(
        data,
        {"root_path"},
        "workspace_binding",
    )
    return WorkspaceBinding(
        root_path=_as_string(data["root_path"], "root_path")
    )


def project_to_data(project: Project) -> JsonObject:
    """Serialize one complete, lightweight project aggregate."""

    return {
        "schema_version": project.schema_version,
        "project_id": str(project.project_id),
        "name": project.name,
        "created_at": _datetime_to_text(project.created_at),
        "updated_at": _datetime_to_text(project.updated_at),
        "settings": _settings_to_data(project.settings),
        "workspace_binding": (
            None
            if project.workspace_binding is None
            else _workspace_to_data(project.workspace_binding)
        ),
        "is_archived": project.is_archived,
    }


def project_from_value(value: object) -> Project:
    """Build one validated Project from stored JSON."""

    data = _as_object(value, "project")
    _require_exact_fields(
        data,
        {
            "schema_version",
            "project_id",
            "name",
            "created_at",
            "updated_at",
            "settings",
            "workspace_binding",
            "is_archived",
        },
        "project",
    )
    schema_version = _as_integer(
        data["schema_version"],
        "schema_version",
    )
    if schema_version != PROJECT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported project schema version: {schema_version}."
        )

    workspace_value = data["workspace_binding"]

    return Project(
        schema_version=PROJECT_SCHEMA_VERSION,
        project_id=ProjectId(
            _as_string(data["project_id"], "project_id")
        ),
        name=_as_string(data["name"], "name"),
        created_at=_datetime_from_value(
            data["created_at"],
            "created_at",
        ),
        updated_at=_datetime_from_value(
            data["updated_at"],
            "updated_at",
        ),
        settings=_settings_from_value(data["settings"]),
        workspace_binding=(
            None
            if workspace_value is None
            else _workspace_from_value(workspace_value)
        ),
        is_archived=_as_boolean(
            data["is_archived"],
            "is_archived",
        ),
    )


def project_store_to_data(projects: Iterable[Project]) -> JsonObject:
    """Serialize all lightweight Projects into one atomic store."""

    return {
        "schema_version": PROJECT_STORE_SCHEMA_VERSION,
        "projects": [project_to_data(project) for project in projects],
    }


def project_store_from_data(
    data: Mapping[str, object],
) -> tuple[Project, ...]:
    """Build and validate every Project in the repository store."""

    _require_exact_fields(
        data,
        {"schema_version", "projects"},
        "project store",
    )
    schema_version = _as_integer(
        data["schema_version"],
        "schema_version",
    )
    if schema_version != PROJECT_STORE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported project store schema version: {schema_version}."
        )

    projects = tuple(
        project_from_value(project)
        for project in _as_list(data["projects"], "projects")
    )
    project_ids = [project.project_id for project in projects]

    if len(project_ids) != len(set(project_ids)):
        raise ValueError("Project store contains duplicate Project IDs.")

    return projects
