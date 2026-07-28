"""Contract shared by chat-model implementations."""

from typing import Literal, Protocol, TypedDict

class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str

class ChatModel(Protocol):
    """Describe the behavior Brain needs from a chat model."""

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        """Generate one reply from ordered chat messages."""
        ...
