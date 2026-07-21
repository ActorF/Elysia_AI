from pathlib import Path

from .conversation import save_json_message
from .json_store import load_json_or_default, write_json


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

    @property
    def conversation_file(self) -> Path:
        return self._conversation_file

    @property
    def profile_file(self) -> Path:
        return self._profile_file

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
    ) -> list[dict]:
        if limit <= 0:
            raise ValueError("Message limit must be greater than zero.")

        conversation_data = load_json_or_default(
            self._conversation_file,
            {"messages": []},
        )

        return conversation_data["messages"][-limit:]

    def load_profile(self) -> dict:
        default_profile = {
            "user_name": "Ying",
            "assistant_name": "Elysia",
            "languages": ["Chinese", "English"],
            "project": "Elysia AI",
        }

        return load_json_or_default(
            self._profile_file,
            default_profile,
        )

    def save_profile(self, profile: dict) -> None:
        write_json(
            self._profile_file,
            profile,
        )

    def record_launch(self) -> dict:
        profile = self.load_profile()

        launch_count = profile.get("launch_count", 0)
        profile["launch_count"] = launch_count + 1

        self.save_profile(profile)

        return profile