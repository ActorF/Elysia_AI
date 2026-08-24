"""Persist lightweight project aggregates behind a repository interface."""

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from chats.domain import ProjectId

from .domain import (
    Project,
    ProjectSettings,
    WorkspaceBinding,
    create_project,
)
from .exceptions import (
    ProjectAlreadyExistsError,
    ProjectDataCorruptionError,
    ProjectNotFoundError,
)
from .serialization import (
    project_store_from_data,
    project_store_to_data,
)
from .storage import atomic_write_json, read_json_object

_PROJECT_STORE_FILE_NAME = "projects.json"

Clock = Callable[[], datetime]


class ProjectRepository(Protocol):
    """Define Project persistence without exposing filesystem paths."""

    def create_project(
        self,
        *,
        name: str,
        settings: ProjectSettings | None = None,
        workspace_binding: WorkspaceBinding | None = None,
    ) -> Project:
        """Create and persist one Project."""
        ...

    def restore_project(self, project: Project) -> None:
        """Restore one complete Project with its existing stable ID."""
        ...

    def list_projects(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[Project, ...]:
        """List lightweight Projects without loading Chats."""
        ...

    def get_project(self, project_id: ProjectId) -> Project:
        """Return one Project by stable ID."""
        ...

    def save_project(self, project: Project) -> None:
        """Persist a fully validated existing Project."""
        ...

    def rename_project(
        self,
        project_id: ProjectId,
        new_name: str,
    ) -> Project:
        """Rename a Project without changing its stable ID."""
        ...

    def update_settings(
        self,
        project_id: ProjectId,
        settings: ProjectSettings,
    ) -> Project:
        """Replace Project-owned behavior settings."""
        ...

    def set_workspace_binding(
        self,
        project_id: ProjectId,
        workspace_binding: WorkspaceBinding | None,
    ) -> Project:
        """Bind, replace, or remove the Project workspace path."""
        ...

    def archive_project(
        self,
        project_id: ProjectId,
        archived: bool = True,
    ) -> Project:
        """Archive or restore one Project."""
        ...

    def delete_project(self, project_id: ProjectId) -> None:
        """Delete only the Project record after relationships are handled."""
        ...


def _default_clock() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(timezone.utc)


class JsonProjectRepository:
    """Store lightweight Projects in one atomically replaced JSON file."""

    def __init__(
        self,
        storage_directory: Path,
        *,
        clock: Clock = _default_clock,
    ) -> None:
        """Configure storage while keeping its path private."""

        self._storage_directory = Path(storage_directory)
        self._store_file = (
            self._storage_directory / _PROJECT_STORE_FILE_NAME
        )
        self._clock = clock

    def create_project(
        self,
        *,
        name: str,
        settings: ProjectSettings | None = None,
        workspace_binding: WorkspaceBinding | None = None,
    ) -> Project:
        """Create a Project whose name and path do not determine its ID."""

        projects = list(self._load_projects())
        project = create_project(
            name=name,
            settings=settings,
            workspace_binding=workspace_binding,
            created_at=self._clock(),
        )

        if any(
            existing.project_id == project.project_id
            for existing in projects
        ):
            raise ProjectAlreadyExistsError(
                f"Project already exists: {project.project_id}."
            )

        self._write_projects([*projects, project])
        return project

    def restore_project(self, project: Project) -> None:
        """Insert validated imported or rollback Project data unchanged."""

        if not isinstance(project, Project):
            raise ValueError("project must be Project.")

        projects = list(self._load_projects())
        if any(
            existing.project_id == project.project_id
            for existing in projects
        ):
            raise ProjectAlreadyExistsError(
                f"Project already exists: {project.project_id}."
            )

        self._write_projects([*projects, project])

    def list_projects(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[Project, ...]:
        """Return Projects sorted by latest update then stable ID."""

        projects = self._load_projects()
        if include_archived:
            return projects

        return tuple(
            project
            for project in projects
            if not project.is_archived
        )

    def get_project(self, project_id: ProjectId) -> Project:
        """Return one Project or raise a stable not-found error."""

        projects = self._load_projects()
        position = self._find_project_position(projects, project_id)
        return projects[position]

    def save_project(self, project: Project) -> None:
        """Replace one existing Project in the atomic store."""

        if not isinstance(project, Project):
            raise ValueError("project must be Project.")

        projects = list(self._load_projects())
        position = self._find_project_position(
            projects,
            project.project_id,
        )
        projects[position] = project
        self._write_projects(projects)

    def rename_project(
        self,
        project_id: ProjectId,
        new_name: str,
    ) -> Project:
        """Rename one Project and update its modification time."""

        project = self.get_project(project_id)
        return self._save_replacement(
            replace(
                project,
                updated_at=self._next_updated_at(project),
                name=new_name,
            )
        )

    def update_settings(
        self,
        project_id: ProjectId,
        settings: ProjectSettings,
    ) -> Project:
        """Replace validated Project settings."""

        if not isinstance(settings, ProjectSettings):
            raise ValueError("settings must be ProjectSettings.")

        project = self.get_project(project_id)
        return self._save_replacement(
            replace(
                project,
                updated_at=self._next_updated_at(project),
                settings=settings,
            )
        )

    def set_workspace_binding(
        self,
        project_id: ProjectId,
        workspace_binding: WorkspaceBinding | None,
    ) -> Project:
        """Set or clear an optional workspace path property."""

        if (
            workspace_binding is not None
            and not isinstance(workspace_binding, WorkspaceBinding)
        ):
            raise ValueError(
                "workspace_binding must be WorkspaceBinding or None."
            )

        project = self.get_project(project_id)
        return self._save_replacement(
            replace(
                project,
                updated_at=self._next_updated_at(project),
                workspace_binding=workspace_binding,
            )
        )

    def archive_project(
        self,
        project_id: ProjectId,
        archived: bool = True,
    ) -> Project:
        """Persist archive state without deleting Project data."""

        project = self.get_project(project_id)
        return self._save_replacement(
            replace(
                project,
                updated_at=self._next_updated_at(project),
                is_archived=archived,
            )
        )

    def delete_project(self, project_id: ProjectId) -> None:
        """Delete one Project record after a coordinator handles Chats."""

        projects = list(self._load_projects())
        position = self._find_project_position(projects, project_id)
        del projects[position]
        self._write_projects(projects)

    def _save_replacement(self, project: Project) -> Project:
        """Persist and return one already validated Project replacement."""

        self.save_project(project)
        return project

    def _load_projects(self) -> tuple[Project, ...]:
        """Load the atomic store or initialize an empty one."""

        if not self._store_file.exists():
            self._write_projects(())
            return ()

        try:
            projects = project_store_from_data(
                read_json_object(self._store_file)
            )
        except ProjectDataCorruptionError:
            raise
        except (TypeError, ValueError) as error:
            raise ProjectDataCorruptionError(
                "Stored Project data does not match its schema."
            ) from error

        return tuple(self._sort_projects(projects))

    def _write_projects(self, projects: Iterable[Project]) -> None:
        """Sort and atomically replace the complete lightweight store."""

        sorted_projects = self._sort_projects(projects)
        atomic_write_json(
            self._store_file,
            project_store_to_data(sorted_projects),
        )

    @staticmethod
    def _find_project_position(
        projects: Iterable[Project],
        project_id: ProjectId,
    ) -> int:
        """Return one Project position or raise not-found."""

        for position, project in enumerate(projects):
            if project.project_id == project_id:
                return position

        raise ProjectNotFoundError(
            f"Project does not exist: {project_id}."
        )

    @staticmethod
    def _sort_projects(projects: Iterable[Project]) -> list[Project]:
        """Sort newest Projects first, then by stable ID."""

        return sorted(
            projects,
            key=lambda project: (
                -project.updated_at.timestamp(),
                str(project.project_id),
            ),
        )

    def _next_updated_at(self, project: Project) -> datetime:
        """Return an aware clock value that never moves time backward."""

        current_time = self._clock()
        if (
            not isinstance(current_time, datetime)
            or current_time.tzinfo is None
            or current_time.utcoffset() is None
        ):
            raise ValueError(
                "Project repository clock must return an aware datetime."
            )

        return max(current_time, project.updated_at)
