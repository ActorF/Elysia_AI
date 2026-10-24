"""Share the chat-model contract and role-safe local-template compatibility."""

from collections.abc import Iterator
from typing import Literal, Protocol, TypedDict


class ChatMessage(TypedDict):
    """Represent one ordered message sent to a chat-model adapter."""

    role: Literal["system", "user", "assistant"]
    content: str


def normalize_chat_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Copy a prompt with all system guidance in one leading system message.

    Some local templates permit a system message only at index zero. Work and
    research add trusted guidance after conversation history, so both adapters
    normalize at the provider boundary rather than requiring every producer to
    know individual model templates. Existing system messages retain their
    encounter order; user/assistant messages retain their roles and order.
    Only an existing system role grants this placement: literal role labels,
    command output and web snippets inside user text remain untrusted data.

    Outer whitespace is trimmed consistently with the adapters' existing
    contract; content inside each message is unchanged. Empty prompts, blank
    content and unknown roles are rejected before any model request is sent.
    The caller's message list and dictionaries are never modified.
    """

    if not messages:
        raise ValueError("Chat messages cannot be empty.")
    system_parts: list[str] = []
    conversation: list[ChatMessage] = []
    for message in messages:
        role = message["role"]
        if role not in ("system", "user", "assistant"):
            raise ValueError("Chat message role is invalid.")
        content = message["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Chat message content cannot be empty.")
        cleaned = content.strip()
        if role == "system":
            system_parts.append(cleaned)
        else:
            conversation.append({"role": role, "content": cleaned})
    if system_parts:
        return [{"role": "system", "content": "\n\n".join(system_parts)}, *conversation]
    return conversation


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
