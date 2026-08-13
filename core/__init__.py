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
from .model_conversation_summarizer import (
    ModelConversationSummarizer,
)
from .model_memory_extractor import ModelMemoryExtractor
from .prompts import build_elysia_system_prompt

__all__ = [
    "Brain",
    "ChatModel",
    "ChatModelConnectionError",
    "ChatModelError",
    "ChatModelNotFoundError",
    "ChatModelResponseError",
    "ConfigurationError",
    "LangChainOllamaChatModel",
    "ModelConversationSummarizer",
    "ModelMemoryExtractor",
    "OllamaChatModel",
    "build_elysia_system_prompt",
]