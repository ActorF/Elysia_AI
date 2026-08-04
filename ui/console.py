"""Console output functions for Elysia."""

from core import Brain
from memory import ConversationMessage, Profile

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

    display_recent_messages(recent_messages)
    display_profile(profile)

    user_message = input("\nYou: ")

    if not user_message.strip():
        print("No message was entered.")
        return

    print("\nElysia: ", end="", flush=True)

    for chunk in brain.stream_chat(user_message):
        print(chunk, end="", flush=True)

    print()
