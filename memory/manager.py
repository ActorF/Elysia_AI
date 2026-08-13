from pathlib import Path
from .json_store import load_json_or_default, write_json
from .profile import (
    PROFILE_SCHEMA_VERSION,
    Profile,
    migrate_profile,
    validate_profile,
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
    load_long_term_memory,
    save_long_term_memory_record,
    search_long_term_memory_records,
)

class Memory:
    """Manages Elysia's conversation and profile memory."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

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
        return self._conversation_file

    @property
    def conversation_summary_file(self) -> Path:
        return self._conversation_summary_file

    def get_conversation_summary(
        self,
    ) -> ConversationSummaryData:
        return load_conversation_summary(
            self._conversation_summary_file,
        )

    def save_conversation_summary(
        self,
        summary: ConversationSummary,
    ) -> None:
        write_conversation_summary(
            self._conversation_summary_file,
            summary,
        )

    @property
    def profile_file(self) -> Path:
        return self._profile_file

    @property
    def long_term_memory_file(self) -> Path:
        return self._long_term_memory_file

    def get_long_term_memories(
        self,
    ) -> list[LongTermMemoryRecord]:
        memory_data = load_long_term_memory(
            self._long_term_memory_file,
        )
        return memory_data["memories"]

    def save_long_term_memory(
        self,
        key: str,
        value: str,
        source_type: LongTermMemorySource,
        source_text: str,
    ) -> LongTermMemoryRecord:
        return save_long_term_memory_record(
            self._long_term_memory_file,
            key,
            value,
            source_type,
            source_text,
        )

    def search_long_term_memories(
        self,
        query: str,
    ) -> list[LongTermMemorySearchResult]:
        return search_long_term_memory_records(
            self._long_term_memory_file,
            query,
        )

    def edit_long_term_memory(
        self,
        memory_number: int,
        key: str,
        value: str,
    ) -> LongTermMemoryRecord:
        return edit_long_term_memory_record(
            self._long_term_memory_file,
            memory_number,
            key,
            value,
        )

    def delete_long_term_memory(
        self,
        memory_number: int,
    ) -> LongTermMemoryRecord:
        return delete_long_term_memory_record(
            self._long_term_memory_file,
            memory_number,
        )

    def export_long_term_memories(
        self,
        export_file: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        return export_long_term_memory(
            self._long_term_memory_file,
            export_file,
            overwrite=overwrite,
        )

    def save_message(
        self,
        speaker: str,
        message: str,
    ) -> None:
        save_json_message(
            self._conversation_file,
            speaker,
            message,
        )

    def get_all_messages(
        self,
    ) -> list[ConversationMessage]:
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
        default_profile: Profile = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "user_name": "Ying",
            "assistant_name": "Elysia",
            "languages": ["Chinese", "English"],
            "project": "Elysia AI",
            "launch_count": 0,
        }

        profile_data = load_json_or_default(
            self._profile_file,
            default_profile,
        )

        return migrate_profile(profile_data)

    def save_profile(self, profile: Profile) -> None:
        validated_profile = validate_profile(profile)

        write_json(
            self._profile_file,
            validated_profile,
        )

    def record_launch(self) -> Profile:
        profile = self.load_profile()

        profile["launch_count"] += 1

        self.save_profile(profile)

        return profile