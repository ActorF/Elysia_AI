class ElysiaError(Exception):
    """Base exception for the Elysia AI project."""


class ConfigurationError(ElysiaError):
    """Raised when an Elysia configuration value is invalid."""