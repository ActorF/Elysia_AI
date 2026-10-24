"""Coordinate all file-backed profile, conversation, and memory stores."""

from pathlib import Path
import json
import os
from tempfile import mkstemp

from config.desktop_settings import desktop_settings_file_lock
from .json_store import load_json_or_default, write_json
from .profile import (
    PROFILE_SCHEMA_VERSION,
    Profile,
    ProfileConflictError,
    migrate_profile,
    validate_profile,
    validate_user_name,
)
from .conversation import (
    ConversationData,
    ConversationMessage,
    save_json_message,
)
from .conversation_summary import (
    ConversationSummary,
    ConversationSummaryData,
    load_conversation_summary,
    save_conversation_summary as write_conversation_summary,
)
from .long_term_memory import (
    LongTermMemoryRecord,
    LongTermMemorySearchResult,
    LongTermMemorySource,
    delete_long_term_memory_record,
    edit_long_term_memory_record,
    export_long_term_memory,
    filter_long_term_memory_records,
    load_long_term_memory,
    save_long_term_memory_record,
    search_long_term_memory_records,
)
from .scope import MemoryScope


class Memory:
    """Provide one facade over Elysia's persistent JSON memory files.

    Centralizing paths and low-level store calls keeps ``Brain`` independent
    from the current on-disk directory layout.
    """

    def __init__(self, base_dir: Path) -> None:
        """Resolve every managed data file beneath the application base path."""

        self._base_dir = base_dir

        # Keep path ownership here so higher layers request data, not files.
        self._conversation_file = (
            self._base_dir
            / "workspace"
            / "conversations"
            / "conversation.json"
        )

        self._conversation_summary_file = (
            self._base_dir
            / "workspace"
            / "conversations"
            / "conversation_summary.json"
        )

        self._profile_file = (
            self._base_dir
            / "workspace"
            / "memory"
            / "profile.json"
        )

        self._long_term_memory_file = (
            self._base_dir
            / "workspace"
            / "memory"
            / "long_term_memory.json"
        )

    @property
    def conversation_file(self) -> Path:
        """Return the path of the raw structured conversation history."""

        return self._conversation_file

    @property
    def conversation_summary_file(self) -> Path:
        """Return the path of the durable structured summary."""

        return self._conversation_summary_file

    def get_conversation_summary(
        self,
    ) -> ConversationSummaryData:
        """Load summary data, creating an empty summary store if necessary."""

        return load_conversation_summary(
            self._conversation_summary_file,
        )

    def save_conversation_summary(
        self,
        summary: ConversationSummary,
    ) -> None:
        """Validate and persist the current structured summary."""

        write_conversation_summary(
            self._conversation_summary_file,
            summary,
        )

    @property
    def profile_file(self) -> Path:
        """Return the path of the persistent user profile."""

        return self._profile_file

    @property
    def long_term_memory_file(self) -> Path:
        """Return the path of the persistent long-term memory store."""

        return self._long_term_memory_file

    def get_long_term_memories(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> list[LongTermMemoryRecord]:
        """Return all records or those in one exact scope."""

        memory_data = load_long_term_memory(
            self._long_term_memory_file,
        )
        return filter_long_term_memory_records(
            memory_data["memories"],
            scope=scope,
            scope_id=scope_id,
        )

    def save_long_term_memory(
        self,
        key: str,
        value: str,
        source_type: LongTermMemorySource,
        source_text: str,
        *,
        scope: MemoryScope = "global",
        scope_id: str | None = None,
    ) -> LongTermMemoryRecord:
        """Validate and append one record to an exact scope."""

        return save_long_term_memory_record(
            self._long_term_memory_file,
            key,
            value,
            source_type,
            source_text,
            scope=scope,
            scope_id=scope_id,
        )

    def search_long_term_memories(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> list[LongTermMemorySearchResult]:
        """Search all records or one exact scope."""

        return search_long_term_memory_records(
            self._long_term_memory_file,
            query,
            scope=scope,
            scope_id=scope_id,
        )

    def edit_long_term_memory(
        self,
        memory_number: int,
        key: str,
        value: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> LongTermMemoryRecord:
        """Edit one record selected within an optional scoped view."""

        return edit_long_term_memory_record(
            self._long_term_memory_file,
            memory_number,
            key,
            value,
            scope=scope,
            scope_id=scope_id,
        )

    def delete_long_term_memory(
        self,
        memory_number: int,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> LongTermMemoryRecord:
        """Delete one record selected within an optional scoped view."""

        return delete_long_term_memory_record(
            self._long_term_memory_file,
            memory_number,
            scope=scope,
            scope_id=scope_id,
        )

    def export_long_term_memories(
        self,
        export_file: Path,
        *,
        overwrite: bool = False,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> Path:
        """Export all records or one exact scope."""

        return export_long_term_memory(
            self._long_term_memory_file,
            export_file,
            overwrite=overwrite,
            scope=scope,
            scope_id=scope_id,
        )

    def save_message(
        self,
        speaker: str,
        message: str,
    ) -> None:
        """Append one speaker message to persistent conversation history."""

        save_json_message(
            self._conversation_file,
            speaker,
            message,
        )

    def get_all_messages(
        self,
    ) -> list[ConversationMessage]:
        """Return the complete persistent conversation in chronological order."""

        default_data: ConversationData = {
            "messages": [],
        }

        conversation_data = load_json_or_default(
            self._conversation_file,
            default_data,
        )

        return list(
            conversation_data["messages"]
        )

    def get_recent_messages(
        self,
        limit: int = 10,
    ) -> list[ConversationMessage]:
        """Return at most ``limit`` newest persistent messages.

        Raises:
            ValueError: If ``limit`` is not greater than zero.
        """

        if limit <= 0:
            raise ValueError("Message limit must be greater than zero.")

        default_data: ConversationData = {
            "messages": [],
        }

        conversation_data = load_json_or_default(
            self._conversation_file,
            default_data,
        )
        return conversation_data["messages"][-limit:]

    def load_profile(self) -> Profile:
        """Load and migrate the profile, creating defaults on first use."""

        with desktop_settings_file_lock(self._profile_file):
            return self._load_profile_unlocked()

    def _load_profile_unlocked(self) -> Profile:
        """Read under the profile lock so first-use creation cannot erase an edit."""

        # Shipped defaults describe no specific customer. Existing profiles
        # retain their chosen name and project; only a new installation gets
        # the neutral speaker label required by legacy conversation storage.
        default_profile: Profile = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "user_name": "User",
            "assistant_name": "Elysia",
            "languages": ["Chinese", "English"],
            "project": "",
            "launch_count": 0,
        }

        if not self._profile_file.exists():
            self._save_profile_unlocked(default_profile)
            return default_profile
        with self._profile_file.open("r", encoding="utf-8") as stream:
            return migrate_profile(json.load(stream))

    def save_profile(self, profile: Profile) -> None:
        """Validate and persist a complete profile."""

        with desktop_settings_file_lock(self._profile_file):
            self._save_profile_unlocked(validate_profile(profile))

    def _save_profile_unlocked(self, profile: Profile) -> None:
        """Flush a sibling file before replacement, preserving the last good profile."""

        self._profile_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = mkstemp(
            prefix=".profile-", suffix=".tmp", dir=self._profile_file.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(profile, stream, ensure_ascii=False, indent=4)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._profile_file)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def update_user_name(self, user_name: str, *, expected_user_name: str) -> Profile:
        """Replace only the name if it still matches the caller's displayed value.

        Sharing the launch-counter lock prevents startup and Settings from
        losing each other's changes. Other profile fields are always loaded
        inside the transaction instead of accepting a renderer-owned profile.
        """

        validate_user_name(user_name)
        with desktop_settings_file_lock(self._profile_file):
            profile = self._load_profile_unlocked()
            if profile["user_name"] != expected_user_name:
                raise ProfileConflictError("Your name changed elsewhere. Reload it before saving.")
            if profile["user_name"] != user_name:
                profile["user_name"] = user_name
                self._save_profile_unlocked(profile)
            return profile

    def record_launch(self) -> Profile:
        """Increment, save, and return the profile's launch counter."""

        with desktop_settings_file_lock(self._profile_file):
            profile = self._load_profile_unlocked()
            profile["launch_count"] += 1
            self._save_profile_unlocked(profile)
            return profile
