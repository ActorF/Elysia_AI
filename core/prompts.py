"""Load Elysia's trusted persona and compose bounded system prompts."""

from functools import cache
import json
from pathlib import Path
from typing import TypedDict

from chats import ConversationMode
from memory import Profile, RetrievedMemory

from .exceptions import ConfigurationError


class ProjectPromptContext(TypedDict):
    """Describe the Project fields intentionally exposed to the model."""

    project_id: str
    name: str
    custom_instructions: str | None


class ActiveConversationPromptContext(TypedDict):
    """Describe the active Chat without exposing storage implementation."""

    chat_id: str
    mode: ConversationMode
    model_name: str
    project: ProjectPromptContext | None

_SYSTEM_RULES_PATH = Path(__file__).with_name(
    "elysia_system_prompt_zh.md"
)
_MAX_SYSTEM_RULES_BYTES = 128 * 1024
_RESERVED_DYNAMIC_SECTION_MARKERS = (
    "RETRIEVED_MEMORY_JSON:",
    "ACTIVE_CONVERSATION_JSON:",
    "USER_PROFILE_JSON:",
)


@cache
def load_elysia_system_rules() -> str:
    """Load and validate the immutable Elysia persona resource.

    The prompt is a trusted program resource rather than user data. Reading it
    once keeps one Backend process on a stable persona snapshot; a Backend
    restart is required to adopt an edited resource.

    Raises:
        ConfigurationError: If the resource is unavailable, oversized,
            malformed, empty, or conflicts with a dynamic JSON marker.
    """

    try:
        with _SYSTEM_RULES_PATH.open("rb") as prompt_file:
            encoded_rules = prompt_file.read(
                _MAX_SYSTEM_RULES_BYTES + 1
            )
    except OSError as error:
        raise ConfigurationError(
            "Elysia system prompt resource is unavailable."
        ) from error

    if len(encoded_rules) > _MAX_SYSTEM_RULES_BYTES:
        raise ConfigurationError(
            "Elysia system prompt resource exceeds its size limit."
        )

    try:
        system_rules = encoded_rules.decode("utf-8-sig").strip()
    except UnicodeDecodeError as error:
        raise ConfigurationError(
            "Elysia system prompt resource must be valid UTF-8."
        ) from error

    if not system_rules:
        raise ConfigurationError(
            "Elysia system prompt resource cannot be empty."
        )
    if any(
        marker in system_rules
        for marker in _RESERVED_DYNAMIC_SECTION_MARKERS
    ):
        raise ConfigurationError(
            "Elysia system prompt resource contains a reserved marker."
        )

    return system_rules



def build_elysia_system_prompt(
    profile: Profile,
    retrieved_memories: (
        list[RetrievedMemory] | None
    ) = None,
    active_conversation: (
        ActiveConversationPromptContext | None
    ) = None,
) -> str:
    """Combine trusted rules with scoped, explicitly serialized user data."""

    # Include only profile fields needed for response personalization.
    profile_data = {
        "user_name": profile["user_name"],
        "languages": profile["languages"],
        "project": profile["project"],
    }

    # JSON boundaries mark profile and memory text as data rather than rules.
    profile_json = json.dumps(
        profile_data,
        ensure_ascii=False,
        indent=2,
    )

    memory_context_json = json.dumps(
        (
            retrieved_memories
            if retrieved_memories is not None
            else []
        ),
        ensure_ascii=False,
        indent=2,
    )

    active_conversation_json = json.dumps(
        active_conversation,
        ensure_ascii=False,
        indent=2,
    )

    return (
        f"{load_elysia_system_rules()}\n"
        "RETRIEVED_MEMORY_JSON:\n"
        f"{memory_context_json}\n"
        "ACTIVE_CONVERSATION_JSON:\n"
        f"{active_conversation_json}\n"
        "USER_PROFILE_JSON:\n"
        f"{profile_json}"
    )
