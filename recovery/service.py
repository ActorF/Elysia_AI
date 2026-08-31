"""Export, import, quarantine, and recover Stage 5 user data."""

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import mkstemp
from typing import Final, Literal, cast
from uuid import uuid4

from chats import (
    ChatAlreadyExistsError,
    ChatId,
    ChatRepository,
    ChatSession,
    ProjectId,
)
from chats.serialization import session_from_data, session_to_data
from chats.storage import atomic_write_json
from config.desktop_settings import (
    MAX_JSON_SAFE_INTEGER,
    DesktopSettingsValidationError,
    desktop_settings_file_lock,
    desktop_settings_snapshot_from_document,
    validate_desktop_settings_document,
)
from chats.legacy import (
    LegacyConversationFormatError,
    chat_messages_match_legacy_prefix,
    legacy_conversation_messages_from_data,
)
from memory.conversation_summary import (
    validate_conversation_summary_data,
)
from memory.long_term_memory import validate_long_term_memory_data
from memory.profile import validate_profile
from projects import (
    Project,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ProjectRepository,
)
from projects.serialization import project_from_value, project_to_data

from .exceptions import (
    DataPortabilityError,
    ExportValidationError,
    ImportConflictError,
    ImportRollbackError,
    ImportValidationError,
)

DATA_EXPORT_SCHEMA_VERSION: Final[Literal[1]] = 1
RECOVERY_LOG_SCHEMA_VERSION: Final[Literal[1]] = 1
DEFAULT_MAX_IMPORT_BYTES: Final = 16 * 1024 * 1024
_MAX_WORKSPACE_FILE_COUNT: Final = 512
_BUNDLE_FIELDS: Final = frozenset({
    "schema_version",
    "bundle_type",
    "exported_at",
    "payload_sha256",
    "payload",
})
_SAFE_WORKSPACE_ROOTS: Final = frozenset({
    "conversations",
    "memory",
    "migrations",
    "settings",
})
_SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9_.-]+$")

ExportBundleType = Literal["chat", "project", "user_data"]
Clock = Callable[[], datetime]
JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Describe exactly which records and files one import restored."""

    bundle_type: ExportBundleType
    chat_ids: tuple[ChatId, ...]
    project_ids: tuple[ProjectId, ...]
    restored_files: tuple[str, ...]


def _default_clock() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(timezone.utc)


class DataPortabilityService:
    """Own safe Stage 5 export, import, rollback, and recovery logging."""

    def __init__(
        self,
        *,
        base_dir: Path,
        chat_repository: ChatRepository,
        project_repository: ProjectRepository,
        max_import_bytes: int = DEFAULT_MAX_IMPORT_BYTES,
        clock: Clock = _default_clock,
    ) -> None:
        if (
            not isinstance(max_import_bytes, int)
            or isinstance(max_import_bytes, bool)
            or max_import_bytes <= 0
        ):
            raise ValueError("max_import_bytes must be a positive integer.")

        self._base_dir = Path(base_dir)
        self._workspace_directory = self._base_dir / "workspace"
        self._chat_repository = chat_repository
        self._project_repository = project_repository
        self._max_import_bytes = max_import_bytes
        self._clock = clock
        self._recovery_directory = (
            self._workspace_directory / "recovery"
        )
        self._quarantine_directory = (
            self._recovery_directory / "quarantine"
        )
        self._recovery_log_file = (
            self._recovery_directory / "recovery_log.json"
        )

    @property
    def max_import_bytes(self) -> int:
        """Return the configured byte limit for one import bundle."""

        return self._max_import_bytes

    def export_chat(
        self,
        chat_id: ChatId,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Export one complete Chat without loading unrelated Chats."""

        session = self._chat_repository.get_chat(chat_id)
        return self._write_bundle(
            destination,
            "chat",
            {"chat": session_to_data(session)},
            overwrite=overwrite,
        )

    def export_project(
        self,
        project_id: ProjectId,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Export one Project and every Chat assigned to it."""

        project = self._project_repository.get_project(project_id)
        sessions = self._project_chats(project_id)
        return self._write_bundle(
            destination,
            "project",
            {
                "project": project_to_data(project),
                "chats": [session_to_data(chat) for chat in sessions],
            },
            overwrite=overwrite,
        )

    def export_all_user_data(
        self,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Export all Projects, Chats, memory, migration, and legacy JSON."""

        projects = self._project_repository.list_projects(
            include_archived=True
        )
        sessions = tuple(
            self._chat_repository.get_chat(metadata.chat_id)
            for metadata in self._chat_repository.list_chats(
                include_archived=True
            )
        )
        return self._write_bundle(
            destination,
            "user_data",
            {
                "projects": [project_to_data(project) for project in projects],
                "chats": [session_to_data(chat) for chat in sessions],
                "workspace_files": self._collect_workspace_files(),
            },
            overwrite=overwrite,
        )

    def import_bundle(
        self,
        source: Path,
        *,
        overwrite_user_files: bool = False,
    ) -> ImportResult:
        """Validate a bundle completely, then restore it transactionally."""

        source_path = self._validate_import_path(source)
        raw_data = self._read_import_bytes(source_path)

        try:
            bundle_type, payload = self._decode_and_validate_bundle(raw_data)
            if bundle_type == "chat":
                return self._import_chat(payload)

            if bundle_type == "project":
                return self._import_project(payload)

            return self._import_all_user_data(
                payload,
                overwrite_user_files=overwrite_user_files,
            )
        except ImportValidationError as error:
            self._quarantine_corrupt_bundle(source_path, raw_data, error)
            raise

    def _write_bundle(
        self,
        destination: Path,
        bundle_type: ExportBundleType,
        payload: JsonObject,
        *,
        overwrite: bool,
    ) -> Path:
        destination_path = self._validate_export_path(
            destination,
            overwrite=overwrite,
        )
        exported_at = self._aware_clock().isoformat()
        try:
            payload_digest = self._payload_digest(payload)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ExportValidationError(
                "Export payload is not strict UTF-8 JSON data."
            ) from error
        bundle: JsonObject = {
            "schema_version": DATA_EXPORT_SCHEMA_VERSION,
            "bundle_type": bundle_type,
            "exported_at": exported_at,
            "payload_sha256": payload_digest,
            "payload": payload,
        }
        try:
            serialized_size = len(self._stored_json_bytes(bundle))
        except (TypeError, ValueError, UnicodeError) as error:
            raise ExportValidationError(
                "Export data cannot be represented as strict UTF-8 JSON."
            ) from error
        if serialized_size > self._max_import_bytes:
            raise ExportValidationError(
                "Export bundle exceeds the configured import size limit."
            )
        try:
            atomic_write_json(destination_path, bundle)
        except Exception as error:
            raise DataPortabilityError(
                f"Could not write export bundle: {destination_path.name}."
            ) from error
        return destination_path

    def _validate_export_path(
        self,
        destination: Path,
        *,
        overwrite: bool,
    ) -> Path:
        path = Path(destination)
        if path.suffix.casefold() != ".json":
            raise ExportValidationError(
                "Export destination must use the .json extension."
            )
        if path.is_symlink():
            raise ExportValidationError(
                "Export destination cannot be a symlink."
            )
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ExportValidationError(
                    "Export destination must be a regular file path."
                )
            if not overwrite:
                raise ExportValidationError(
                    f"Export destination already exists: {path.name}."
                )
        self._reject_symlink_parent(path)
        return path

    def _validate_import_path(self, source: Path) -> Path:
        path = Path(source)
        if path.suffix.casefold() != ".json":
            raise ImportValidationError(
                "Import source must use the .json extension."
            )
        if path.is_symlink():
            raise ImportValidationError("Import source cannot be a symlink.")
        try:
            size_bytes = path.stat().st_size
        except OSError as error:
            raise ImportValidationError(
                f"Import source is not readable: {path.name}."
            ) from error
        if not path.is_file():
            raise ImportValidationError(
                "Import source must be a regular file."
            )
        self._reject_symlink_parent(path, import_path=True)
        if size_bytes > self._max_import_bytes:
            raise ImportValidationError(
                "Import bundle exceeds the configured size limit."
            )
        return path

    def _reject_symlink_parent(
        self,
        path: Path,
        *,
        import_path: bool = False,
    ) -> None:
        parent = path.parent
        while parent != parent.parent:
            if parent.exists() and parent.is_symlink():
                error_type = (
                    ImportValidationError
                    if import_path
                    else ExportValidationError
                )
                raise error_type("Data path cannot traverse a symlink.")
            parent = parent.parent

    def _read_import_bytes(self, source: Path) -> bytes:
        try:
            raw_data = source.read_bytes()
        except OSError as error:
            raise ImportValidationError(
                f"Could not read import bundle: {source.name}."
            ) from error
        if len(raw_data) > self._max_import_bytes:
            raise ImportValidationError(
                "Import bundle exceeds the configured size limit."
            )
        return raw_data

    def _decode_and_validate_bundle(
        self,
        raw_data: bytes,
    ) -> tuple[ExportBundleType, JsonObject]:
        try:
            decoded: object = json.loads(
                raw_data.decode("utf-8"),
                parse_constant=self._reject_json_constant,
                object_pairs_hook=self._reject_duplicate_keys,
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ) as error:
            raise ImportValidationError(
                "Import bundle is not valid UTF-8 JSON."
            ) from error
        bundle = self._as_object(decoded, "bundle")
        self._require_exact_fields(bundle, _BUNDLE_FIELDS, "bundle")
        schema_version = bundle["schema_version"]
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != DATA_EXPORT_SCHEMA_VERSION
        ):
            raise ImportValidationError(
                "Import bundle uses an unsupported schema version."
            )
        bundle_type_value = bundle["bundle_type"]
        if bundle_type_value not in ("chat", "project", "user_data"):
            raise ImportValidationError("Import bundle type is invalid.")
        exported_at = bundle["exported_at"]
        if not isinstance(exported_at, str):
            raise ImportValidationError("exported_at must be a string.")
        self._parse_aware_timestamp(exported_at, "exported_at")
        payload = self._as_object(bundle["payload"], "payload")
        checksum = bundle["payload_sha256"]
        try:
            actual_checksum = self._payload_digest(payload)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ImportValidationError(
                "Import payload is not strict UTF-8 JSON data."
            ) from error
        if (
            not isinstance(checksum, str)
            or checksum != actual_checksum
        ):
            raise ImportValidationError(
                "Import bundle payload checksum does not match."
            )
        return cast(ExportBundleType, bundle_type_value), payload

    def _import_chat(self, payload: JsonObject) -> ImportResult:
        self._require_exact_fields(payload, {"chat"}, "chat payload")
        session = self._session_from_value(payload["chat"])
        if session.project_id is not None:
            try:
                self._project_repository.get_project(session.project_id)
            except ProjectNotFoundError as error:
                raise ImportConflictError(
                    "Imported Chat references a Project that does not exist."
                ) from error
        self._require_chat_ids_available((session,))
        try:
            self._chat_repository.restore_chat(session)
        except ChatAlreadyExistsError as error:
            raise ImportConflictError(str(error)) from error
        return ImportResult(
            bundle_type="chat",
            chat_ids=(session.chat_id,),
            project_ids=(),
            restored_files=(),
        )

    def _import_project(self, payload: JsonObject) -> ImportResult:
        self._require_exact_fields(
            payload,
            {"project", "chats"},
            "project payload",
        )
        project = self._project_from_value(payload["project"])
        sessions = self._sessions_from_value(payload["chats"])
        if any(chat.project_id != project.project_id for chat in sessions):
            raise ImportValidationError(
                "Project bundle contains a Chat outside its Project."
            )
        self._require_project_ids_available((project,))
        self._require_chat_ids_available(sessions)
        return self._restore_entities_transactionally(
            bundle_type="project",
            projects=(project,),
            sessions=sessions,
            workspace_files={},
            overwrite_user_files=False,
        )

    def _import_all_user_data(
        self,
        payload: JsonObject,
        *,
        overwrite_user_files: bool,
    ) -> ImportResult:
        self._require_exact_fields(
            payload,
            {"projects", "chats", "workspace_files"},
            "user-data payload",
        )
        projects = self._projects_from_value(payload["projects"])
        sessions = self._sessions_from_value(payload["chats"])
        workspace_files = self._validate_workspace_files(
            payload["workspace_files"]
        )
        workspace_files = self._rebase_legacy_backup_path(
            workspace_files
        )
        project_ids = {project.project_id for project in projects}
        for session in sessions:
            if (
                session.project_id is not None
                and session.project_id not in project_ids
            ):
                raise ImportValidationError(
                    "User-data bundle contains a Chat whose Project is missing."
                )
        self._validate_migration_chat_reference(
            workspace_files,
            sessions,
        )
        self._require_project_ids_available(projects)
        self._require_chat_ids_available(sessions)
        if not overwrite_user_files:
            conflicts = [
                relative_path
                for relative_path in workspace_files
                if self._workspace_path(relative_path).exists()
            ]
            if conflicts:
                raise ImportConflictError(
                    "User-data files already exist; explicitly allow "
                    "replacement to restore them."
                )
        return self._restore_entities_transactionally(
            bundle_type="user_data",
            projects=projects,
            sessions=sessions,
            workspace_files=workspace_files,
            overwrite_user_files=overwrite_user_files,
        )

    def _restore_entities_transactionally(
        self,
        *,
        bundle_type: ExportBundleType,
        projects: tuple[Project, ...],
        sessions: tuple[ChatSession, ...],
        workspace_files: dict[str, JsonObject],
        overwrite_user_files: bool,
    ) -> ImportResult:
        settings_path = "settings/global.json"
        if settings_path in workspace_files:
            with desktop_settings_file_lock(
                self._workspace_path(settings_path)
            ):
                return self._restore_entities_transactionally_locked(
                    bundle_type=bundle_type,
                    projects=projects,
                    sessions=sessions,
                    workspace_files=workspace_files,
                    overwrite_user_files=overwrite_user_files,
                )
        return self._restore_entities_transactionally_locked(
            bundle_type=bundle_type,
            projects=projects,
            sessions=sessions,
            workspace_files=workspace_files,
            overwrite_user_files=overwrite_user_files,
        )

    def _restore_entities_transactionally_locked(
        self,
        *,
        bundle_type: ExportBundleType,
        projects: tuple[Project, ...],
        sessions: tuple[ChatSession, ...],
        workspace_files: dict[str, JsonObject],
        overwrite_user_files: bool,
    ) -> ImportResult:
        restored_projects: list[Project] = []
        restored_chats: list[ChatSession] = []
        file_backups: dict[Path, bytes | None] = {}
        try:
            for project in projects:
                self._project_repository.restore_project(project)
                restored_projects.append(project)
            for session in sessions:
                self._chat_repository.restore_chat(session)
                restored_chats.append(session)
            for relative_path, data in workspace_files.items():
                target = self._workspace_path(relative_path)
                file_backups[target] = (
                    target.read_bytes() if target.exists() else None
                )
                if target.exists() and not overwrite_user_files:
                    raise ImportConflictError(
                        f"User-data file already exists: {relative_path}."
                    )
                if relative_path == "settings/global.json":
                    data = self._rebase_imported_settings(
                        data,
                        file_backups[target],
                    )
                atomic_write_json(target, data)
        except Exception as error:
            self._roll_back_import(
                restored_chats,
                restored_projects,
                file_backups,
                error,
            )
            if isinstance(error, DataPortabilityError):
                raise
            if isinstance(error, (
                ChatAlreadyExistsError,
                ProjectAlreadyExistsError,
            )):
                raise ImportConflictError(str(error)) from error
            raise DataPortabilityError(
                "Import failed; all completed changes were rolled back."
            ) from error
        return ImportResult(
            bundle_type=bundle_type,
            chat_ids=tuple(chat.chat_id for chat in sessions),
            project_ids=tuple(project.project_id for project in projects),
            restored_files=tuple(sorted(workspace_files)),
        )

    def _rebase_imported_settings(
        self,
        data: JsonObject,
        previous_data: bytes | None,
    ) -> JsonObject:
        """Keep an import from reusing a revision held by an open UI."""

        imported = desktop_settings_snapshot_from_document(data)
        current_revision = 0
        if previous_data is not None:
            try:
                current_value: object = json.loads(
                    previous_data.decode("utf-8"),
                    parse_constant=self._reject_json_constant,
                    object_pairs_hook=self._reject_duplicate_keys,
                )
                current_revision = (
                    desktop_settings_snapshot_from_document(
                        current_value
                    ).revision
                )
            except (
                DesktopSettingsValidationError,
                UnicodeError,
                json.JSONDecodeError,
                RecursionError,
                ValueError,
            ):
                current_revision = 0
        if current_revision == MAX_JSON_SAFE_INTEGER:
            raise ImportConflictError(
                "Settings revision limit prevents this restore."
            )
        revision = max(imported.revision, current_revision + 1)
        if revision > MAX_JSON_SAFE_INTEGER:
            raise ImportConflictError(
                "Imported Settings revision is outside the supported range."
            )
        rebased = dict(data)
        rebased["revision"] = revision
        rebased["updated_at"] = self._aware_clock().isoformat()
        validate_desktop_settings_document(rebased)
        return rebased

    def _roll_back_import(
        self,
        chats: list[ChatSession],
        projects: list[Project],
        file_backups: dict[Path, bytes | None],
        original_error: Exception,
    ) -> None:
        rollback_errors: list[Exception] = []
        for target, old_data in reversed(tuple(file_backups.items())):
            try:
                if old_data is None:
                    target.unlink(missing_ok=True)
                else:
                    self._atomic_write_bytes(target, old_data)
            except Exception as error:
                rollback_errors.append(error)
        for chat in reversed(chats):
            try:
                self._chat_repository.delete_chat(chat.chat_id)
            except Exception as error:
                rollback_errors.append(error)
        for project in reversed(projects):
            try:
                self._project_repository.delete_project(project.project_id)
            except Exception as error:
                rollback_errors.append(error)
        if rollback_errors:
            raise ImportRollbackError(
                "Import failed and rollback could not restore every change."
            ) from original_error

    def _collect_workspace_files(self) -> JsonObject:
        collected: JsonObject = {}
        for root_name in sorted(_SAFE_WORKSPACE_ROOTS):
            root = self._workspace_directory / root_name
            if not root.exists():
                continue
            if root.is_symlink() or any(
                managed_path.is_symlink()
                for managed_path in root.rglob("*")
            ):
                raise ExportValidationError(
                    "Managed workspace data cannot traverse symlinks."
                )
            for file_path in sorted(root.rglob("*.json")):
                if file_path.is_symlink() or not file_path.is_file():
                    raise ExportValidationError(
                        "Managed workspace data cannot contain symlinked JSON."
                    )
                relative_path = file_path.relative_to(
                    self._workspace_directory
                ).as_posix()
                if (
                    root_name == "settings"
                    and relative_path != "settings/global.json"
                ):
                    # Corrupt recovery copies stay local and are never part of
                    # a portable user-data bundle.
                    continue
                try:
                    decoded: object = json.loads(
                        file_path.read_text(encoding="utf-8"),
                        parse_constant=self._reject_json_constant,
                        object_pairs_hook=self._reject_duplicate_keys,
                    )
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    RecursionError,
                    ValueError,
                ) as error:
                    raise ExportValidationError(
                        f"Managed JSON is unreadable: {relative_path}."
                    ) from error
                if not isinstance(decoded, dict) or not all(
                    isinstance(key, str) for key in decoded
                ):
                    raise ExportValidationError(
                        f"Managed JSON root must be an object: {relative_path}."
                    )
                if relative_path == "settings/global.json":
                    try:
                        validate_desktop_settings_document(decoded)
                    except DesktopSettingsValidationError as error:
                        raise ExportValidationError(
                            "Managed Settings schema is invalid."
                        ) from error
                collected[relative_path] = cast(JsonObject, decoded)
                if len(collected) > _MAX_WORKSPACE_FILE_COUNT:
                    raise ExportValidationError(
                        "Too many managed workspace files to export safely."
                    )
        return collected

    def _validate_workspace_files(
        self,
        value: object,
    ) -> dict[str, JsonObject]:
        data = self._as_object(value, "workspace_files")
        if len(data) > _MAX_WORKSPACE_FILE_COUNT:
            raise ImportValidationError(
                "Import bundle contains too many workspace files."
            )
        validated: dict[str, JsonObject] = {}
        for relative_path, file_data in data.items():
            self._validate_workspace_relative_path(relative_path)
            json_data = self._as_object(file_data, relative_path)
            self._validate_known_workspace_schema(relative_path, json_data)
            validated[relative_path] = json_data
        self._validate_legacy_migration_backup(validated)
        return validated

    def _validate_known_workspace_schema(
        self,
        relative_path: str,
        data: JsonObject,
    ) -> None:
        try:
            if relative_path == "memory/profile.json":
                validate_profile(data)
            elif relative_path == "memory/long_term_memory.json":
                validate_long_term_memory_data(data)
            elif relative_path == "conversations/conversation_summary.json":
                validate_conversation_summary_data(data)
            elif relative_path == "conversations/conversation.json":
                self._validate_legacy_conversation(data)
            elif relative_path == "migrations/legacy_conversation_v1.json":
                self._validate_legacy_migration_state(data)
            elif relative_path == "settings/global.json":
                validate_desktop_settings_document(data)
            elif relative_path.startswith("settings/"):
                raise ValueError("Unknown managed Settings file.")
        except (DesktopSettingsValidationError, ValueError) as error:
            raise ImportValidationError(
                f"Managed file schema is invalid: {relative_path}."
            ) from error

    def _validate_legacy_conversation(self, data: JsonObject) -> None:
        legacy_conversation_messages_from_data(data)

    def _validate_legacy_migration_state(self, data: JsonObject) -> None:
        self._require_exact_fields(
            data,
            {
                "schema_version",
                "source_sha256",
                "backup_path",
                "chat_id",
                "message_count",
                "migrated_at",
                "sources",
            },
            "legacy migration state",
        )
        state_version = data["schema_version"]
        if (
            not isinstance(state_version, int)
            or isinstance(state_version, bool)
            or state_version != 1
        ):
            raise ImportValidationError(
                "Legacy migration state version is unsupported."
            )
        source_digest = data["source_sha256"]
        if (
            not isinstance(source_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_digest) is None
        ):
            raise ImportValidationError(
                "Legacy migration source hash is invalid."
            )
        for field_name in ("backup_path", "chat_id", "migrated_at"):
            field_value = data[field_name]
            if not isinstance(field_value, str) or not field_value.strip():
                raise ImportValidationError(
                    f"Legacy migration {field_name} is invalid."
                )
        self._parse_aware_timestamp(
            cast(str, data["migrated_at"]),
            "migrated_at",
        )
        message_count = data["message_count"]
        if (
            not isinstance(message_count, int)
            or isinstance(message_count, bool)
            or message_count <= 0
        ):
            raise ImportValidationError(
                "Legacy migration message_count is invalid."
            )
        sources = self._as_object(data["sources"], "migration sources")
        self._require_exact_fields(
            sources,
            {"conversation", "summary", "profile", "long_term_memory"},
            "migration sources",
        )
        for source_name, source_value in sources.items():
            source = self._as_object(
                source_value,
                f"migration source {source_name}",
            )
            self._require_exact_fields(
                source,
                {"path", "sha256"},
                f"migration source {source_name}",
            )
            if (
                not isinstance(source["path"], str)
                or not cast(str, source["path"]).strip()
            ):
                raise ImportValidationError(
                    f"Migration source path is invalid: {source_name}."
                )
            digest = source["sha256"]
            if digest is not None and (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise ImportValidationError(
                    f"Migration source hash is invalid: {source_name}."
                )

    def _validate_legacy_migration_backup(
        self,
        workspace_files: dict[str, JsonObject],
    ) -> None:
        state = workspace_files.get(
            "migrations/legacy_conversation_v1.json"
        )
        if state is None:
            return
        backup_value = cast(str, state["backup_path"])
        backup_name = backup_value.replace("\\", "/").rsplit("/", 1)[-1]
        relative_backup = f"migrations/backups/{backup_name}"
        if relative_backup not in workspace_files:
            raise ImportValidationError(
                "Legacy migration backup is missing from user data."
            )

    def _validate_workspace_relative_path(self, value: str) -> None:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] not in _SAFE_WORKSPACE_ROOTS
            or path.suffix.casefold() != ".json"
            or any(
                part in ("", ".", "..")
                or _SAFE_PATH_PART.fullmatch(part) is None
                for part in path.parts
            )
        ):
            raise ImportValidationError(
                f"Unsafe workspace file path in bundle: {value}."
            )

    def _rebase_legacy_backup_path(
        self,
        workspace_files: dict[str, JsonObject],
    ) -> dict[str, JsonObject]:
        """Point restored Module 6 state at its restored local backup."""

        state_path = "migrations/legacy_conversation_v1.json"
        state = workspace_files.get(state_path)
        if state is None:
            return workspace_files
        backup_value = state.get("backup_path")
        if not isinstance(backup_value, str):
            return workspace_files
        backup_name = backup_value.replace("\\", "/").rsplit("/", 1)[-1]
        relative_backup = f"migrations/backups/{backup_name}"
        if relative_backup not in workspace_files:
            return workspace_files
        rebased_files = dict(workspace_files)
        rebased_state = dict(state)
        rebased_state["backup_path"] = str(
            self._workspace_path(relative_backup)
        )
        conversation = workspace_files.get(
            "conversations/conversation.json"
        )
        if conversation is not None:
            rebased_state["source_sha256"] = hashlib.sha256(
                self._stored_json_bytes(conversation)
            ).hexdigest()
        rebased_files[state_path] = rebased_state
        return rebased_files

    def _validate_migration_chat_reference(
        self,
        workspace_files: dict[str, JsonObject],
        sessions: tuple[ChatSession, ...],
    ) -> None:
        state = workspace_files.get(
            "migrations/legacy_conversation_v1.json"
        )
        if state is None:
            return
        chat_id = cast(str, state["chat_id"])
        message_count = cast(int, state["message_count"])
        backup_value = cast(str, state["backup_path"])
        backup_name = backup_value.replace("\\", "/").rsplit("/", 1)[-1]
        backup = workspace_files[
            f"migrations/backups/{backup_name}"
        ]
        try:
            source_messages = legacy_conversation_messages_from_data(backup)
        except LegacyConversationFormatError as error:
            raise ImportValidationError(
                "Legacy migration backup is not a valid conversation."
            ) from error
        matching = [
            session
            for session in sessions
            if str(session.chat_id) == chat_id
        ]
        if (
            message_count != len(source_messages)
            or len(matching) != 1
            or not chat_messages_match_legacy_prefix(
                matching[0].messages,
                source_messages,
            )
        ):
            raise ImportValidationError(
                "Legacy migration state does not match its exported Chat."
            )

    def _workspace_path(self, relative_path: str) -> Path:
        self._validate_workspace_relative_path(relative_path)
        target = self._workspace_directory.joinpath(
            *PurePosixPath(relative_path).parts
        )
        current = target
        workspace_parent = self._workspace_directory.parent
        while current != workspace_parent:
            if current.is_symlink():
                raise ImportValidationError(
                    "Restore target cannot traverse a symlink."
                )
            current = current.parent
        return target

    def _project_chats(
        self,
        project_id: ProjectId,
    ) -> tuple[ChatSession, ...]:
        return tuple(
            self._chat_repository.get_chat(metadata.chat_id)
            for metadata in self._chat_repository.list_chats(
                include_archived=True
            )
            if metadata.project_id == project_id
        )

    def _require_chat_ids_available(
        self,
        sessions: tuple[ChatSession, ...],
    ) -> None:
        chat_ids = [session.chat_id for session in sessions]
        if len(chat_ids) != len(set(chat_ids)):
            raise ImportValidationError(
                "Import bundle contains duplicate Chat IDs."
            )
        existing_ids = {
            metadata.chat_id
            for metadata in self._chat_repository.list_chats(
                include_archived=True
            )
        }
        if existing_ids.intersection(chat_ids):
            raise ImportConflictError(
                "One or more imported Chat IDs already exist."
            )

    def _require_project_ids_available(
        self,
        projects: tuple[Project, ...],
    ) -> None:
        project_ids = [project.project_id for project in projects]
        if len(project_ids) != len(set(project_ids)):
            raise ImportValidationError(
                "Import bundle contains duplicate Project IDs."
            )
        existing_ids = {
            project.project_id
            for project in self._project_repository.list_projects(
                include_archived=True
            )
        }
        if existing_ids.intersection(project_ids):
            raise ImportConflictError(
                "One or more imported Project IDs already exist."
            )

    def _projects_from_value(self, value: object) -> tuple[Project, ...]:
        return tuple(
            self._project_from_value(item)
            for item in self._as_list(value, "projects")
        )

    def _project_from_value(self, value: object) -> Project:
        try:
            project = project_from_value(value)
        except (TypeError, ValueError) as error:
            raise ImportValidationError(
                "Imported Project does not match its schema."
            ) from error
        if project_to_data(project) != value:
            raise ImportValidationError(
                "Imported Project is not in canonical schema form."
            )
        return project

    def _sessions_from_value(
        self,
        value: object,
    ) -> tuple[ChatSession, ...]:
        return tuple(
            self._session_from_value(item)
            for item in self._as_list(value, "chats")
        )

    def _session_from_value(self, value: object) -> ChatSession:
        data = self._as_object(value, "chat")
        try:
            session = session_from_data(data)
        except (TypeError, ValueError) as error:
            raise ImportValidationError(
                "Imported Chat does not match its schema."
            ) from error
        if session_to_data(session) != data:
            raise ImportValidationError(
                "Imported Chat is not in canonical schema form."
            )
        return session

    def _quarantine_corrupt_bundle(
        self,
        source: Path,
        raw_data: bytes,
        error: ImportValidationError,
    ) -> None:
        digest = hashlib.sha256(raw_data).hexdigest()
        timestamp = self._aware_clock().strftime("%Y%m%dT%H%M%SZ")
        safe_stem = _SAFE_FILE_NAME.sub("_", source.stem)[:80] or "bundle"
        quarantine_file = self._quarantine_directory / (
            f"{safe_stem}-{timestamp}-{digest[:12]}-{uuid4().hex[:8]}.json"
        )
        try:
            self._quarantine_directory.mkdir(parents=True, exist_ok=True)
            with quarantine_file.open("xb") as output:
                output.write(raw_data)
            self._append_recovery_event(
                action="quarantine_import",
                status="rejected",
                source=str(source),
                quarantine_path=str(quarantine_file),
                detail=str(error),
            )
        except Exception as quarantine_error:
            raise ImportValidationError(
                f"{error} The rejected bundle could not be quarantined."
            ) from quarantine_error

    def _append_recovery_event(
        self,
        *,
        action: str,
        status: str,
        source: str,
        quarantine_path: str,
        detail: str,
    ) -> None:
        log_data: JsonObject = {
            "schema_version": RECOVERY_LOG_SCHEMA_VERSION,
            "events": [],
        }
        if self._recovery_log_file.exists():
            try:
                decoded: object = json.loads(
                    self._recovery_log_file.read_text(encoding="utf-8"),
                    parse_constant=self._reject_json_constant,
                    object_pairs_hook=self._reject_duplicate_keys,
                )
                existing = self._as_object(decoded, "recovery log")
                if (
                    existing.get("schema_version")
                    != RECOVERY_LOG_SCHEMA_VERSION
                    or not isinstance(existing.get("events"), list)
                ):
                    raise ValueError("Recovery log schema is invalid.")
                log_data = existing
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ImportValidationError,
                ValueError,
            ):
                corrupt_log = self._recovery_log_file.with_name(
                    "recovery_log.corrupt-"
                    f"{self._aware_clock().timestamp():.0f}-"
                    f"{uuid4().hex[:8]}.json"
                )
                os.replace(self._recovery_log_file, corrupt_log)
        events = cast(list[object], log_data["events"])
        if len(events) >= 1_000:
            del events[:-999]
        events.append({
            "timestamp": self._aware_clock().isoformat(),
            "action": action,
            "status": status,
            "source": source,
            "quarantine_path": quarantine_path,
            "detail": detail,
        })
        atomic_write_json(self._recovery_log_file, log_data)

    def _payload_digest(self, payload: Mapping[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _stored_json_bytes(self, data: Mapping[str, object]) -> bytes:
        """Match the atomic JSON format used for restored workspace files."""

        return (
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _reject_json_constant(value: str) -> object:
        """Reject NaN and infinity because they are not valid JSON values."""

        raise ValueError(f"Invalid JSON constant: {value}.")

    @staticmethod
    def _reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> JsonObject:
        """Reject ambiguous JSON objects containing the same key twice."""

        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}.")
            result[key] = value
        return result

    def _aware_clock(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Data portability clock must return an aware time.")
        return value.astimezone(timezone.utc)

    def _parse_aware_timestamp(self, value: str, field_name: str) -> datetime:
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError as error:
            raise ImportValidationError(
                f"{field_name} must be an ISO 8601 timestamp."
            ) from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ImportValidationError(
                f"{field_name} must include a timezone."
            )
        return timestamp

    def _as_object(self, value: object, context: str) -> JsonObject:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise ImportValidationError(f"{context} must be a JSON object.")
        return cast(JsonObject, value)

    def _as_list(self, value: object, context: str) -> list[object]:
        if not isinstance(value, list):
            raise ImportValidationError(f"{context} must be a JSON array.")
        return cast(list[object], value)

    def _require_exact_fields(
        self,
        data: Mapping[str, object],
        expected: set[str] | frozenset[str],
        context: str,
    ) -> None:
        if set(data) != set(expected):
            raise ImportValidationError(
                f"{context} does not match its expected schema."
            )

    def _atomic_write_bytes(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, target)
        finally:
            if descriptor != -1:
                os.close(descriptor)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
