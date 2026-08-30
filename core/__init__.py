"""Public interface for Elysia's core package."""

# Re-export stable entry points so callers need not know the internal layout.
from .active_conversation import (
    ActiveConversation,
    ActiveConversationService,
)
from .brain import Brain
from .chat_model import ChatModel
from .exceptions import (
    ActiveConversationError,
    ChatBusyError,
    ChatChangedDuringGenerationError,
    ChatRetryTargetError,
    GenerationCancelledError,
    ChatModelMismatchError,
    ChatModelConnectionError,
    ChatModelError,
    ChatModelNotFoundError,
    ChatModelResponseError,
    ConfigurationError,
    ConversationUnavailableError,
)
from .ollama_chat_model import OllamaChatModel
from .langchain_ollama_chat_model import (
    LangChainOllamaChatModel,
)
from .model_conversation_summarizer import (
    ModelConversationSummarizer,
)
from .model_memory_extractor import ModelMemoryExtractor
from .prompts import (
    ActiveConversationPromptContext,
    ProjectPromptContext,
    build_elysia_system_prompt,
)

# Keep the supported package API explicit for tools and future maintainers.
__all__ = [
    "ActiveConversation",
    "ActiveConversationError",
    "ActiveConversationPromptContext",
    "ActiveConversationService",
    "Brain",
    "ChatBusyError",
    "ChatChangedDuringGenerationError",
    "ChatRetryTargetError",
    "ChatModel",
    "ChatModelConnectionError",
    "ChatModelError",
    "ChatModelNotFoundError",
    "ChatModelResponseError",
    "ChatModelMismatchError",
    "ConfigurationError",
    "ConversationUnavailableError",
    "GenerationCancelledError",
    "LangChainOllamaChatModel",
    "ModelConversationSummarizer",
    "ModelMemoryExtractor",
    "OllamaChatModel",
    "ProjectPromptContext",
    "build_elysia_system_prompt",
]
