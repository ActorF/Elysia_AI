"""Public interface for Elysia's core package."""

from .brain import Brain
from .chat_model import ChatModel
from .exceptions import (
    ChatModelConnectionError,
    ChatModelError,
    ChatModelNotFoundError,
    ChatModelResponseError,
    ConfigurationError,
)
from .ollama_chat_model import OllamaChatModel
from .langchain_ollama_chat_model import (
    LangChainOllamaChatModel,
)

__all__ = [
    "Brain",
    "ChatModel",
    "ChatModelConnectionError",
    "ChatModelError",
    "ChatModelNotFoundError",
    "ChatModelResponseError",
    "ConfigurationError",
    "LangChainOllamaChatModel",
    "OllamaChatModel",
]