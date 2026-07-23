class ElysiaError(Exception):
    """Base exception for the Elysia AI project."""


class ConfigurationError(ElysiaError):
    """Raised when an Elysia configuration value is invalid."""

class ChatModelError(ElysiaError):
    """Base error raised by a chat-model adapter."""

class ChatModelConnectionError(ChatModelError):
    """Raised when the chat-model service is unavailable."""

class ChatModelNotFoundError(ChatModelError):
    """Raised when the configured model is not installed."""

class ChatModelResponseError(ChatModelError):
    """Raised when the model returns an invalid response."""