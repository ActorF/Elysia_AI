"""Contract shared by chat-model implementations."""

from typing import Protocol


class ChatModel(Protocol):
    """Describe the behavior Brain needs from a chat model."""

    def generate_reply(
        self,
        user_message: str,
    ) -> str:
        """Generate one reply for one user message."""
        ...