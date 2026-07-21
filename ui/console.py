"""Console output functions for Elysia."""

from core import Brain

def display_recent_messages(
    messages: list[dict],
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


def display_profile(profile: dict) -> None:
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