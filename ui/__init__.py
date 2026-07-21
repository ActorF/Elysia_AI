"""Public interface for Elysia's UI package."""

from .console import (
    display_profile,
    display_recent_messages,
    run_console_session,
)


__all__ = [
    "display_profile",
    "display_recent_messages",
    "run_console_session",
]