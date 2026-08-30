"""Project-specific exceptions translated at application boundaries."""


class ElysiaError(Exception):
    """Base exception for the Elysia AI project."""


class ConfigurationError(ElysiaError):
    """Raised when an Elysia configuration value is invalid."""


class ActiveConversationError(ElysiaError):
    """Base error for one active Chat lifecycle."""


class ChatBusyError(ActiveConversationError):
    """Raised when a Chat already has an active generation operation."""


class ChatChangedDuringGenerationError(ActiveConversationError):
    """Raised when persisted Chat state changed before a turn committed."""


class ChatRetryTargetError(ActiveConversationError):
    """Raised when a retry does not name the persisted tail turn."""


class GenerationCancelledError(ActiveConversationError):
    """Raised when a generation is cancelled before its atomic commit."""


class ConversationUnavailableError(ActiveConversationError):
    """Raised when an archived Chat or Project cannot accept a new turn."""


class ChatModelMismatchError(ActiveConversationError):
    """Raised when a Chat requests a model other than Brain's adapter."""


class ChatModelError(ElysiaError):
    """Base error raised by a chat-model adapter."""


class ChatModelConnectionError(ChatModelError):
    """Raised when the chat-model service is unavailable."""


class ChatModelNotFoundError(ChatModelError):
    """Raised when the configured model is not installed."""


class ChatModelResponseError(ChatModelError):
    """Raised when the model returns an invalid response."""
