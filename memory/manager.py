from pathlib import Path
from .json_store import load_json_or_default, write_json
from typing import NotRequired, TypedDict
from .conversation import (
    ConversationData,
    ConversationMessage,
    save_json_message,
)
from .long_term_memory import (
    LongTermMemoryRecord,
    LongTermMemorySource,
    load_long_term_memory,
    save_long_term_memory_record,
)

class Profile(TypedDict):
    user_name: str
    assistant_name: str
    languages: list[str]
    project: str
    launch_count: NotRequired[int]

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
            "user_name": "Ying",
            "assistant_name": "Elysia",
            "languages": ["Chinese", "English"],
            "project": "Elysia AI",
        }

        return load_json_or_default(
            self._profile_file,
            default_profile,
        )

    def save_profile(self, profile: Profile) -> None:
        write_json(
            self._profile_file,
            profile,
        )

    def record_launch(self) -> Profile:
        profile = self.load_profile()

        launch_count = profile.get("launch_count", 0)
        profile["launch_count"] = launch_count + 1

        self.save_profile(profile)

        return profile