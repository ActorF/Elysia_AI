"""Persist chat sessions behind a path-independent repository interface."""

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .domain import (
    ChatId,
    ChatSession,
    ChatSessionMeta,
    ConversationMode,
    ProjectId,
    create_chat_session,
)
from .exceptions import (
    ChatAlreadyExistsError,
    ChatDataCorruptionError,
    ChatNotFoundError,
    ChatRepositoryError,
    ChatStorageError,
)
from .serialization import (
    index_from_data,
    index_to_data,
    session_from_data,
    session_to_data,
)
from .storage import atomic_write_json, read_json_object

_CHAT_ID_PATTERN = re.compile(r"^chat_[A-Za-z0-9_-]+$")
_INDEX_FILE_NAME = "index.json"
_SESSIONS_DIRECTORY_NAME = "sessions"

Clock = Callable[[], datetime]


class ChatRepository(Protocol):
    """Define chat persistence without exposing files to Brain or UI code."""

    def create_chat(
        self,
        *,
        title: str,
        mode: ConversationMode,
        model_name: str,
        project_id: ProjectId | None = None,
    ) -> ChatSession:
        """Create and persist one empty chat."""
        ...

    def list_chats(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """Return lightweight chat metadata without loading messages."""
        ...

    def get_chat(self, chat_id: ChatId) -> ChatSession:
        """Load one complete chat by stable ID."""
        ...

    def save_chat(self, session: ChatSession) -> None:
        """Persist an updated existing chat and refresh its index entry."""
        ...

    def rename_chat(
        self,
        chat_id: ChatId,
        new_title: str,
    ) -> ChatSession:
        """Change a chat title while preserving its ID and content."""
        ...

    def pin_chat(
        self,
        chat_id: ChatId,
        pinned: bool = True,
    ) -> ChatSessionMeta:
        """Pin or unpin a chat in the lightweight index."""
        ...

    def archive_chat(
        self,
        chat_id: ChatId,
        archived: bool = True,
    ) -> ChatSessionMeta:
        """Archive or restore a chat without deleting its content."""
        ...

    def delete_chat(self, chat_id: ChatId) -> None:
        """Delete one chat from both the index and detail storage."""
        ...

    def recover_index(self) -> tuple[ChatSessionMeta, ...]:
        """Rebuild the lightweight index from valid session files."""
        ...


def _default_clock() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(timezone.utc)


class JsonChatRepository:
    """Store one lightweight index plus one JSON detail file per chat."""

    def __init__(
        self,
        storage_directory: Path,
        *,
        clock: Clock = _default_clock,
    ) -> None:
        """Configure repository storage without exposing it through the API."""

        self._storage_directory = Path(storage_directory)
        self._index_file = self._storage_directory / _INDEX_FILE_NAME
        self._sessions_directory = (
            self._storage_directory / _SESSIONS_DIRECTORY_NAME
        )
        self._clock = clock

    def create_chat(
        self,
        *,
        title: str,
        mode: ConversationMode,
        model_name: str,
        project_id: ProjectId | None = None,
    ) -> ChatSession:
        """Create the detail file first, then publish it in the index."""

        metadata_entries = list(self._load_index())
        session = create_chat_session(
            title=title,
            mode=mode,
            model_name=model_name,
            project_id=project_id,
            created_at=self._clock(),
        )

        session_file = self._session_file(session.chat_id)
        if any(
            metadata.chat_id == session.chat_id
            for metadata in metadata_entries
        ) or session_file.exists():
            raise ChatAlreadyExistsError(
                f"Chat already exists: {session.chat_id}."
            )

        self._write_session(session)

        try:
            self._write_index(
                [*metadata_entries, session.to_meta()]
            )
        except ChatRepositoryError:
            try:
                session_file.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise ChatStorageError(
                    "Could not roll back a failed chat creation."
                ) from cleanup_error
            raise

        return session

    def list_chats(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """Read only the lightweight index, never the session details."""

        metadata_entries = self._load_index()

        if include_archived:
            return metadata_entries

        return tuple(
            metadata
            for metadata in metadata_entries
            if not metadata.is_archived
        )

    def get_chat(self, chat_id: ChatId) -> ChatSession:
        """Load a detail file and verify that it matches its index entry."""

        self._session_file(chat_id)
        metadata_entries = self._load_index()
        position = self._find_metadata_position(
            metadata_entries,
            chat_id,
        )
        session = self._read_session(chat_id)
        self._verify_metadata_matches(
            session,
            metadata_entries[position],
        )
        return session

    def save_chat(self, session: ChatSession) -> None:
        """Save content first and roll it back if index replacement fails."""

        metadata_entries = list(self._load_index())
        position = self._find_metadata_position(
            metadata_entries,
            session.chat_id,
        )
        old_session = self._read_session(session.chat_id)
        self._verify_metadata_matches(
            old_session,
            metadata_entries[position],
        )

        self._write_session(session)
        metadata_entries[position] = session.to_meta()

        try:
            self._write_index(metadata_entries)
        except ChatRepositoryError:
            try:
                self._write_session(old_session)
            except ChatRepositoryError as rollback_error:
                raise ChatStorageError(
                    "Could not roll back a failed chat update."
                ) from rollback_error
            raise

    def rename_chat(
        self,
        chat_id: ChatId,
        new_title: str,
    ) -> ChatSession:
        """Rename one chat and refresh its modification time."""

        session = self.get_chat(chat_id)
        updated_session = replace(
            session,
            title=new_title,
            updated_at=self._next_updated_at(session),
        )
        self.save_chat(updated_session)
        return updated_session

    def pin_chat(
        self,
        chat_id: ChatId,
        pinned: bool = True,
    ) -> ChatSessionMeta:
        """Persist pin state in both the detail file and index."""

        session = self.get_chat(chat_id)
        updated_session = replace(session, is_pinned=pinned)
        self.save_chat(updated_session)
        return updated_session.to_meta()

    def archive_chat(
        self,
        chat_id: ChatId,
        archived: bool = True,
    ) -> ChatSessionMeta:
        """Persist archive state while retaining all chat content."""

        session = self.get_chat(chat_id)
        updated_session = replace(session, is_archived=archived)
        self.save_chat(updated_session)
        return updated_session.to_meta()

    def delete_chat(self, chat_id: ChatId) -> None:
        """Remove the index entry before deleting its detail file."""

        self._session_file(chat_id)
        metadata_entries = list(self._load_index())
        position = self._find_metadata_position(
            metadata_entries,
            chat_id,
        )
        session = self._read_session(chat_id)
        self._verify_metadata_matches(
            session,
            metadata_entries[position],
        )
        remaining_entries = [
            metadata
            for metadata in metadata_entries
            if metadata.chat_id != chat_id
        ]

        self._write_index(remaining_entries)

        try:
            self._session_file(chat_id).unlink()
        except OSError as error:
            try:
                self._write_index(metadata_entries)
            except ChatRepositoryError as rollback_error:
                raise ChatStorageError(
                    "Could not roll back a failed chat deletion."
                ) from rollback_error

            raise ChatStorageError(
                f"Could not delete chat: {chat_id}."
            ) from error

    def recover_index(self) -> tuple[ChatSessionMeta, ...]:
        """Rebuild the index and preserve an invalid old index as backup."""

        recovered_entries = self._scan_session_metadata()
        should_back_up = False

        if self._index_file.exists():
            try:
                index_from_data(read_json_object(self._index_file))
            except (ChatDataCorruptionError, ValueError):
                should_back_up = True

        if should_back_up:
            backup_file = self._index_file.with_name(
                f"index.corrupt-{uuid4().hex}.json"
            )
            try:
                os.replace(self._index_file, backup_file)
            except OSError as error:
                raise ChatStorageError(
                    "Could not preserve the corrupted chat index."
                ) from error

        self._write_index(recovered_entries)
        return tuple(self._sort_metadata(recovered_entries))

    def _load_index(self) -> tuple[ChatSessionMeta, ...]:
        """Load the index or rebuild it when the file is missing."""

        if not self._index_file.exists():
            recovered_entries = self._scan_session_metadata()
            self._write_index(recovered_entries)
            return tuple(self._sort_metadata(recovered_entries))

        try:
            metadata_entries = index_from_data(
                read_json_object(self._index_file)
            )
        except ChatDataCorruptionError:
            raise
        except (TypeError, ValueError) as error:
            raise ChatDataCorruptionError(
                "Stored chat index does not match its schema."
            ) from error

        return tuple(self._sort_metadata(metadata_entries))

    def _write_index(
        self,
        metadata_entries: Iterable[ChatSessionMeta],
    ) -> None:
        """Sort and atomically persist lightweight metadata only."""

        sorted_entries = self._sort_metadata(metadata_entries)
        atomic_write_json(
            self._index_file,
            index_to_data(sorted_entries),
        )

    def _read_session(self, chat_id: ChatId) -> ChatSession:
        """Read and validate one complete session detail file."""

        session_file = self._session_file(chat_id)

        try:
            raw_data = read_json_object(session_file)
        except FileNotFoundError as error:
            raise ChatNotFoundError(
                f"Chat detail file does not exist: {chat_id}."
            ) from error

        try:
            session = session_from_data(raw_data)
        except (TypeError, ValueError) as error:
            raise ChatDataCorruptionError(
                f"Stored chat session does not match its schema: {chat_id}."
            ) from error

        if session.chat_id != chat_id:
            raise ChatDataCorruptionError(
                "Stored chat ID does not match its detail filename."
            )

        return session

    def _write_session(self, session: ChatSession) -> None:
        """Atomically persist one complete session detail file."""

        atomic_write_json(
            self._session_file(session.chat_id),
            session_to_data(session),
        )

    def _scan_session_metadata(self) -> list[ChatSessionMeta]:
        """Read detail files only during explicit or missing-index recovery."""

        if not self._sessions_directory.exists():
            return []

        metadata_entries: list[ChatSessionMeta] = []

        for session_file in sorted(
            self._sessions_directory.glob("chat_*.json")
        ):
            expected_chat_id = ChatId(session_file.stem)
            session = self._read_session(expected_chat_id)
            metadata_entries.append(session.to_meta())

        chat_ids = [entry.chat_id for entry in metadata_entries]
        if len(chat_ids) != len(set(chat_ids)):
            raise ChatDataCorruptionError(
                "Session storage contains duplicate chat IDs."
            )

        return self._sort_metadata(metadata_entries)

    def _session_file(self, chat_id: ChatId) -> Path:
        """Resolve a safe detail path from a storage-safe stable ID."""

        chat_id_text = str(chat_id)
        if _CHAT_ID_PATTERN.fullmatch(chat_id_text) is None:
            raise ChatNotFoundError(
                f"Invalid chat ID: {chat_id_text}."
            )

        return self._sessions_directory / f"{chat_id_text}.json"

    @staticmethod
    def _find_metadata_position(
        metadata_entries: Iterable[ChatSessionMeta],
        chat_id: ChatId,
    ) -> int:
        """Return one index position or raise a stable not-found error."""

        for position, metadata in enumerate(metadata_entries):
            if metadata.chat_id == chat_id:
                return position

        raise ChatNotFoundError(f"Chat does not exist: {chat_id}.")

    @staticmethod
    def _verify_metadata_matches(
        session: ChatSession,
        metadata: ChatSessionMeta,
    ) -> None:
        """Reject disagreement between the index and detail file."""

        if session.to_meta() != metadata:
            raise ChatDataCorruptionError(
                "Chat index metadata does not match its session detail."
            )

    @staticmethod
    def _sort_metadata(
        metadata_entries: Iterable[ChatSessionMeta],
    ) -> list[ChatSessionMeta]:
        """Order pinned chats first, then newest chats, then stable ID."""

        return sorted(
            metadata_entries,
            key=lambda metadata: (
                0 if metadata.is_pinned else 1,
                -metadata.updated_at.timestamp(),
                str(metadata.chat_id),
            ),
        )

    def _next_updated_at(self, session: ChatSession) -> datetime:
        """Return an aware clock value that never moves chat time backward."""

        current_time = self._clock()
        if (
            not isinstance(current_time, datetime)
            or current_time.tzinfo is None
            or current_time.utcoffset() is None
        ):
            raise ValueError(
                "Chat repository clock must return an aware datetime."
            )

        return max(current_time, session.updated_at)
