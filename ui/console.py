"""Console output functions for Elysia."""

from core import Brain
from memory import (
    ConversationMessage,
    LongTermMemoryRecord,
    Profile,
)


def display_recent_messages(
    messages: list[ConversationMessage],
) -> None:
    print("\nRecent conversation:")

    if not messages:
        print("No saved messages.")
        return

    for message_data in messages:
        timestamp = message_data["timestamp"]
        speaker = message_data["speaker"]
        message = message_data["message"]

        print(
            f"[{timestamp}] "
            f"{speaker}: {message}"
        )


def display_long_term_memories(
    memories: list[LongTermMemoryRecord],
) -> None:
    print("\nLong-term memories:")

    if not memories:
        print("No saved long-term memories.")
        return

    for memory_record in memories:
        print(
            f"- {memory_record['key']}: "
            f"{memory_record['value']}"
        )
        print(
            f"  Source type: "
            f"{memory_record['source_type']}"
        )
        print(
            f"  Source text: "
            f"{memory_record['source_text']}"
        )
        print(
            f"  Created at: "
            f"{memory_record['created_at']}"
        )


def display_profile(profile: Profile) -> None:
    print("\nProfile:")
    print(f"User: {profile['user_name']}")
    print(f"Assistant: {profile['assistant_name']}")
    print(f"Languages: {profile['languages']}")
    print(f"Project: {profile['project']}")
    print(f"Launch count: {profile['launch_count']}")


def run_console_session(brain: Brain) -> None:
    """Start and display an Elysia console session."""
    profile = brain.start_session()

    recent_messages = (
        brain.recall_recent_messages(10)
    )

    long_term_memories = (
        brain.recall_long_term_memories()
    )

    display_recent_messages(recent_messages)
    display_long_term_memories(long_term_memories)
    display_profile(profile)

    user_message = input("\nYou: ")

    if not user_message.strip():
        print("No message was entered.")
        return

    print("\nElysia: ", end="", flush=True)

    for chunk in brain.stream_chat(user_message):
        print(chunk, end="", flush=True)

    print()
