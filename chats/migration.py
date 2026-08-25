"""Safely migrate the Stage 2-4 conversation store into one Chat."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal

from memory.conversation_summary import (
    ConversationSummaryData,
    validate_conversation_summary_data,
)

from .domain import (
    CHAT_SESSION_SCHEMA_VERSION,
    ChatId,
    ChatMessage,
    ChatModelSettings,
    ChatSession,
    ChatSummary,
)
from .exceptions import (
    ChatNotFoundError,
    ChatRepositoryError,
    LegacyMigrationError,
)
from .legacy import (
    LegacyConversationFormatError,
    chat_messages_match_legacy_prefix,
    legacy_conversation_messages_from_data,
)
from .repository import ChatRepository
from .storage import atomic_write_json, read_json_object

LEGACY_MIGRATION_SCHEMA_VERSION: Final[Literal[1]] = 1
_LEGACY_TIMESTAMP_FORMAT: Final = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True, slots=True)
class LegacyMigrationResult:
    """Report whether startup imported or recognized the legacy data."""

    status: Literal["not_needed", "migrated", "already_migrated"]
    chat_id: ChatId | None = None
    message_count: int = 0


class LegacyConversationMigrator:
    """Perform one backed-up, idempotent legacy conversation migration."""

    def __init__(
        self,
        *,
        base_dir: Path,
        chat_repository: ChatRepository,
        model_name: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._base_dir = Path(base_dir)
        self._chat_repository = chat_repository
        self._model_name = model_name
        self._clock = clock
        workspace = self._base_dir / "workspace"
        self._conversation_file = (
            workspace / "conversations" / "conversation.json"
        )
        self._summary_file = (
            workspace / "conversations" / "conversation_summary.json"
        )
        self._profile_file = workspace / "memory" / "profile.json"
        self._long_term_memory_file = (
            workspace / "memory" / "long_term_memory.json"
        )
        self._migration_directory = workspace / "migrations"
        self._state_file = (
            self._migration_directory / "legacy_conversation_v1.json"
        )
        self._backup_directory = (
            self._migration_directory / "backups"
        )

    def migrate(self) -> LegacyMigrationResult:
        """Import legacy data once, rolling back a failed Chat creation."""

        if not self._conversation_file.exists():
            return LegacyMigrationResult(status="not_needed")

        conversation_bytes = self._read_bytes(self._conversation_file)
        conversation_hash = hashlib.sha256(conversation_bytes).hexdigest()
        messages = self._load_messages(conversation_bytes)
        if not messages:
            return LegacyMigrationResult(status="not_needed")

        state = self._load_state()
        if state is not None:
            return self._resolve_existing_state(
                state,
                conversation_hash,
                messages,
            )

        summary_data = self._load_optional_summary()
        backup_file = self._create_backup(
            conversation_bytes,
            conversation_hash,
        )
        session = self._build_session(messages, summary_data)

        try:
            self._chat_repository.restore_chat(session)
            try:
                atomic_write_json(
                    self._state_file,
                    self._build_state(
                        session,
                        conversation_hash,
                        backup_file,
                    ),
                )
            except Exception as state_error:
                self._chat_repository.delete_chat(session.chat_id)
                raise LegacyMigrationError(
                    "Legacy conversation migration could not save its "
                    "completion record; the new Chat was rolled back."
                ) from state_error
        except LegacyMigrationError:
            raise
        except Exception as error:
            raise LegacyMigrationError(
                "Legacy conversation migration failed; the original file "
                "and its backup were preserved."
            ) from error

        return LegacyMigrationResult(
            status="migrated",
            chat_id=session.chat_id,
            message_count=len(session.messages),
        )

    def _read_bytes(self, file_path: Path) -> bytes:
        try:
            return file_path.read_bytes()
        except OSError as error:
            raise LegacyMigrationError(
                f"Could not read legacy data: {file_path.name}."
            ) from error

    def _load_messages(self, raw_data: bytes) -> tuple[ChatMessage, ...]:
        try:
            decoded: object = json.loads(raw_data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise LegacyMigrationError(
                "Legacy conversation.json is not valid UTF-8 JSON."
            ) from error

        try:
            return legacy_conversation_messages_from_data(decoded)
        except LegacyConversationFormatError as error:
            raise LegacyMigrationError(str(error)) from error

    def _load_optional_summary(self) -> ConversationSummaryData | None:
        if not self._summary_file.exists():
            return None
        try:
            return validate_conversation_summary_data(
                read_json_object(self._summary_file)
            )
        except (ChatRepositoryError, ValueError) as error:
            raise LegacyMigrationError(
                "Legacy conversation summary is invalid."
            ) from error

    def _build_session(
        self,
        messages: tuple[ChatMessage, ...],
        summary_data: ConversationSummaryData | None,
    ) -> ChatSession:
        created_at = messages[0].created_at
        updated_at = messages[-1].created_at
        summary: ChatSummary | None = None
        if summary_data is not None and summary_data["summary"] is not None:
            legacy = summary_data["summary"]
            count = legacy["source_message_count"]
            if count > len(messages):
                raise LegacyMigrationError(
                    "Legacy summary references more messages than exist."
                )
            try:
                summary_time = self._parse_legacy_timestamp(
                    legacy["updated_at"]
                )
            except ValueError as error:
                raise LegacyMigrationError(
                    "Legacy summary contains an invalid timestamp."
                ) from error
            summary_time = min(max(summary_time, created_at), updated_at)
            content = legacy["content"]
            summary = ChatSummary(
                facts=tuple(content["facts"]),
                decisions=tuple(content["decisions"]),
                action_items=tuple(content["action_items"]),
                unresolved_questions=tuple(content["unresolved_questions"]),
                source_message_ids=tuple(
                    message.message_id for message in messages[:count]
                ),
                updated_at=summary_time,
            )
        digest = hashlib.sha256(
            "\n".join(str(message.message_id) for message in messages).encode()
        ).hexdigest()[:24]
        return ChatSession(
            schema_version=CHAT_SESSION_SCHEMA_VERSION,
            chat_id=ChatId(f"chat_legacy_{digest}"),
            title="Legacy Conversation",
            mode="chat",
            created_at=created_at,
            updated_at=updated_at,
            messages=messages,
            summary=summary,
            project_id=None,
            model_settings=ChatModelSettings(model_name=self._model_name),
        )

    def _parse_legacy_timestamp(self, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = datetime.strptime(value, _LEGACY_TIMESTAMP_FORMAT)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _create_backup(self, data: bytes, digest: str) -> Path:
        self._backup_directory.mkdir(parents=True, exist_ok=True)
        backup_file = self._backup_directory / f"conversation-{digest}.json"
        if backup_file.exists():
            if self._read_bytes(backup_file) != data:
                raise LegacyMigrationError("Legacy backup hash collision.")
            return backup_file
        try:
            with backup_file.open("xb") as backup:
                backup.write(data)
        except OSError as error:
            raise LegacyMigrationError(
                "Could not create the legacy conversation backup."
            ) from error
        return backup_file

    def _load_state(self) -> dict[str, object] | None:
        if not self._state_file.exists():
            return None
        try:
            state = read_json_object(self._state_file)
        except ChatRepositoryError as error:
            raise LegacyMigrationError("Migration state is unreadable.") from error
        required = {
            "schema_version", "source_sha256", "backup_path", "chat_id",
            "message_count", "migrated_at", "sources",
        }
        if set(state) != required or state["schema_version"] != 1:
            raise LegacyMigrationError("Migration state is invalid.")
        return state

    def _resolve_existing_state(
        self,
        state: dict[str, object],
        digest: str,
        source_messages: tuple[ChatMessage, ...],
    ) -> LegacyMigrationResult:
        if state["source_sha256"] != digest:
            raise LegacyMigrationError(
                "Legacy conversation changed after migration; refusing to "
                "silently import a second copy."
            )
        chat_id_value = state["chat_id"]
        if not isinstance(chat_id_value, str):
            raise LegacyMigrationError("Migration state chat_id is invalid.")
        chat_id = ChatId(chat_id_value)
        try:
            session = self._chat_repository.get_chat(chat_id)
        except ChatNotFoundError as error:
            raise LegacyMigrationError(
                "Migration state exists but its Legacy Chat is missing."
            ) from error
        message_count = len(source_messages)
        if not chat_messages_match_legacy_prefix(
            session.messages,
            source_messages,
        ):
            raise LegacyMigrationError(
                "The migrated Legacy Chat no longer matches its source."
            )
        return LegacyMigrationResult(
            status="already_migrated",
            chat_id=chat_id,
            message_count=message_count,
        )

    def _source_record(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {"path": str(path), "sha256": None}
        return {
            "path": str(path),
            "sha256": hashlib.sha256(self._read_bytes(path)).hexdigest(),
        }

    def _build_state(
        self,
        session: ChatSession,
        digest: str,
        backup_file: Path,
    ) -> dict[str, object]:
        return {
            "schema_version": LEGACY_MIGRATION_SCHEMA_VERSION,
            "source_sha256": digest,
            "backup_path": str(backup_file),
            "chat_id": str(session.chat_id),
            "message_count": len(session.messages),
            "migrated_at": self._clock().astimezone(timezone.utc).isoformat(),
            "sources": {
                "conversation": self._source_record(self._conversation_file),
                "summary": self._source_record(self._summary_file),
                "profile": self._source_record(self._profile_file),
                "long_term_memory": self._source_record(
                    self._long_term_memory_file
                ),
            },
        }
