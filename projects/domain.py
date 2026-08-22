"""Define stable project entities and workspace value objects."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final, Literal
from uuid import uuid4

from chats.domain import ProjectId

PROJECT_SCHEMA_VERSION: Final[Literal[1]] = 1

_PROJECT_ID_PATTERN = re.compile(r"^project_[A-Za-z0-9_-]+$")


def _validate_non_empty_text(value: object, field_name: str) -> None:
    """Require one non-empty human-readable string."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


def _validate_optional_text(value: object, field_name: str) -> None:
    """Require either None or one non-empty string."""

    if value is not None:
        _validate_non_empty_text(value, field_name)


def _validate_timestamp(value: object, field_name: str) -> None:
    """Require a timezone-aware datetime."""

    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime."
        )


def _validate_project_id(project_id: object) -> None:
    """Require the stable opaque Project ID format."""

    if (
        not isinstance(project_id, str)
        or _PROJECT_ID_PATTERN.fullmatch(project_id) is None
    ):
        raise ValueError(
            "project_id must use the project_<id> format."
        )


def generate_project_id() -> ProjectId:
    """Return an opaque ID independent of name or workspace path."""

    return ProjectId(f"project_{uuid4().hex}")


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    """Hold behavior settings owned by one project."""

    default_model_name: str | None = None
    custom_instructions: str | None = None

    def __post_init__(self) -> None:
        """Reject empty optional settings that have ambiguous meaning."""

        _validate_optional_text(
            self.default_model_name,
            "default_model_name",
        )
        _validate_optional_text(
            self.custom_instructions,
            "custom_instructions",
        )


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    """Bind a project to an absolute workspace path without using it as ID."""

    root_path: str

    def __post_init__(self) -> None:
        """Accept absolute Windows or POSIX paths without touching disk."""

        _validate_non_empty_text(self.root_path, "root_path")

        if self.root_path != self.root_path.strip():
            raise ValueError(
                "root_path cannot contain surrounding whitespace."
            )

        if "\x00" in self.root_path:
            raise ValueError("root_path cannot contain a null byte.")

        if not (
            PureWindowsPath(self.root_path).is_absolute()
            or PurePosixPath(self.root_path).is_absolute()
        ):
            raise ValueError("root_path must be an absolute path.")


@dataclass(frozen=True, slots=True)
class Project:
    """Represent one project aggregate without duplicating owned Chat IDs."""

    schema_version: Literal[1]
    project_id: ProjectId
    name: str
    created_at: datetime
    updated_at: datetime
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    workspace_binding: WorkspaceBinding | None = None
    is_archived: bool = False

    def __post_init__(self) -> None:
        """Validate stable identity, timestamps, settings, and workspace."""

        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != PROJECT_SCHEMA_VERSION
        ):
            raise ValueError(
                "Unsupported project schema version: "
                f"{self.schema_version}."
            )

        _validate_project_id(self.project_id)
        _validate_non_empty_text(self.name, "name")
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.updated_at, "updated_at")

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot be earlier than created_at."
            )

        if not isinstance(self.settings, ProjectSettings):
            raise ValueError("settings must be ProjectSettings.")

        if (
            self.workspace_binding is not None
            and not isinstance(
                self.workspace_binding,
                WorkspaceBinding,
            )
        ):
            raise ValueError(
                "workspace_binding must be WorkspaceBinding or None."
            )

        if not isinstance(self.is_archived, bool):
            raise ValueError("is_archived must be a boolean.")


def create_project(
    *,
    name: str,
    settings: ProjectSettings | None = None,
    workspace_binding: WorkspaceBinding | None = None,
    created_at: datetime | None = None,
) -> Project:
    """Create one project with a stable ID and timezone-aware timestamp."""

    project_created_at = (
        datetime.now(timezone.utc)
        if created_at is None
        else created_at
    )

    return Project(
        schema_version=PROJECT_SCHEMA_VERSION,
        project_id=generate_project_id(),
        name=name,
        created_at=project_created_at,
        updated_at=project_created_at,
        settings=(
            ProjectSettings()
            if settings is None
            else settings
        ),
        workspace_binding=workspace_binding,
    )
