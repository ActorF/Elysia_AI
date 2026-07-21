"""Public interface for Elysia's core package."""

from .brain import Brain
from .exceptions import ConfigurationError


__all__ = [
    "Brain",
    "ConfigurationError",
]