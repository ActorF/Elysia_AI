"""Contract shared by chat-model implementations."""

from collections.abc import Iterator
from typing import Literal, Protocol, TypedDict


class ChatMessage(TypedDict):
    """Represent one ordered message sent to a chat-model adapter."""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatModel(Protocol):
    """Decouple ``Brain`` from any specific local model implementation."""

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        """Generate one reply from ordered chat messages."""
        ...

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        """Yield one reply as ordered text chunks."""
        ...
