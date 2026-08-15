"""Public interface for Elysia's UI package."""

# Expose UI entry points without leaking console-module implementation details.
from .console import (
    display_long_term_memories,
    display_memory_search_results,
    display_profile,
    display_recent_messages,
    run_console_session,
    run_memory_management,
)


# Declare the functions supported as the package's public interface.
__all__ = [
    "display_long_term_memories",
    "display_memory_search_results",
    "display_profile",
    "display_recent_messages",
    "run_console_session",
    "run_memory_management",
]
