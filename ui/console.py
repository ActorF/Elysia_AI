"""Console output functions for Elysia."""

from pathlib import Path

from core import Brain
from memory import (
    ConversationMessage,
    LongTermMemoryRecord,
    LongTermMemorySearchResult,
    MemoryCandidate,
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


def _display_long_term_memory(
    memory_number: int,
    memory_record: LongTermMemoryRecord,
) -> None:
    print(
        f"- {memory_record['key']}: "
        f"{memory_record['value']}"
    )
    print(f"  Memory number: {memory_number}")
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


def display_long_term_memories(
    memories: list[LongTermMemoryRecord],
) -> None:
    print("\nLong-term memories:")

    if not memories:
        print("No saved long-term memories.")
        return

    for memory_number, memory_record in enumerate(
        memories,
        start=1,
    ):
        _display_long_term_memory(
            memory_number,
            memory_record,
        )


def display_memory_search_results(
    results: list[LongTermMemorySearchResult],
) -> None:
    print("\nMemory search results:")

    if not results:
        print("No matching long-term memories.")
        return

    for result in results:
        _display_long_term_memory(
            result["number"],
            result["memory"],
        )


def display_profile(profile: Profile) -> None:
    print("\nProfile:")
    print(f"User: {profile['user_name']}")
    print(f"Assistant: {profile['assistant_name']}")
    print(f"Languages: {profile['languages']}")
    print(f"Project: {profile['project']}")
    print(f"Launch count: {profile['launch_count']}")


def review_memory_candidates(
    brain: Brain,
    candidates: list[MemoryCandidate],
) -> None:
    """Ask before saving each extracted memory candidate."""
    if not candidates:
        return

    print("\nPossible long-term memories:")

    for candidate in candidates:
        print(
            f"- {candidate['key']}: "
            f"{candidate['value']}"
        )
        print(
            f"  Source type: "
            f"{candidate['source_type']}"
        )

        decision = input(
            "  Save this memory? [y/N]: "
        ).strip().lower()

        if decision in {"y", "yes"}:
            brain.confirm_memory_candidate(candidate)
            print("  Memory saved.")
        else:
            print("  Memory not saved.")


def _prompt_memory_number() -> int | None:
    raw_number = input("Memory number: ").strip()

    try:
        memory_number = int(raw_number)
    except ValueError:
        print(
            "Memory number must be "
            "a positive integer."
        )
        return None

    if memory_number <= 0:
        print(
            "Memory number must be "
            "a positive integer."
        )
        return None

    return memory_number


def _search_memories(brain: Brain) -> None:
    query = input("Search text: ")

    try:
        results = brain.search_long_term_memories(
            query
        )
    except ValueError as error:
        print(f"Search failed: {error}")
        return

    display_memory_search_results(results)


def _edit_memory(brain: Brain) -> None:
    memories = brain.recall_long_term_memories()
    display_long_term_memories(memories)

    if not memories:
        return

    memory_number = _prompt_memory_number()

    if memory_number is None:
        return

    if memory_number > len(memories):
        print(
            "Long-term memory number "
            "does not exist."
        )
        return

    existing_record = memories[memory_number - 1]

    new_key = input(
        f"New key [{existing_record['key']}]: "
    ).strip()
    new_value = input(
        f"New value [{existing_record['value']}]: "
    ).strip()

    if not new_key:
        new_key = existing_record["key"]

    if not new_value:
        new_value = existing_record["value"]

    try:
        brain.edit_long_term_memory(
            memory_number,
            new_key,
            new_value,
        )
    except (IndexError, ValueError) as error:
        print(f"Memory edit failed: {error}")
        return

    print("Memory updated.")


def _export_memories(brain: Brain) -> None:
    export_text = input(
        "Export JSON path: "
    ).strip()

    if not export_text:
        print("Memory export cancelled.")
        return

    export_file = Path(export_text).expanduser()
    overwrite = False

    if export_file.exists():
        confirmation = input(
            "Export file exists. Type OVERWRITE "
            "to replace it: "
        ).strip()

        if confirmation != "OVERWRITE":
            print("Memory export cancelled.")
            return

        overwrite = True

    try:
        exported_file = (
            brain.export_long_term_memories(
                export_file,
                overwrite=overwrite,
            )
        )
    except (OSError, ValueError) as error:
        print(f"Memory export failed: {error}")
        return

    print(
        f"Memories exported to: {exported_file}"
    )


def _delete_memory(brain: Brain) -> None:
    memories = brain.recall_long_term_memories()
    display_long_term_memories(memories)

    if not memories:
        return

    memory_number = _prompt_memory_number()

    if memory_number is None:
        return

    if memory_number > len(memories):
        print(
            "Long-term memory number "
            "does not exist."
        )
        return

    target = memories[memory_number - 1]

    print(
        "Delete this memory: "
        f"{target['key']} = {target['value']}"
    )

    confirmation = input(
        "Type DELETE to confirm: "
    ).strip()

    if confirmation != "DELETE":
        print("Memory deletion cancelled.")
        return

    try:
        brain.delete_long_term_memory(
            memory_number,
            confirmed=True,
        )
    except (
        IndexError,
        PermissionError,
        ValueError,
    ) as error:
        print(
            f"Memory deletion failed: {error}"
        )
        return

    print("Memory deleted.")


def run_memory_management(brain: Brain) -> None:
    """Run the long-term memory management console."""
    while True:
        print("\nMemory management:")
        print(
            "1. list   - View all "
            "long-term memories"
        )
        print(
            "2. search - Search "
            "long-term memories"
        )
        print(
            "3. edit   - Edit one "
            "long-term memory"
        )
        print(
            "4. export - Export "
            "memories to JSON"
        )
        print(
            "5. delete - Delete one "
            "long-term memory"
        )
        print("6. back   - Return to chat")

        action = input(
            "Memory action: "
        ).strip().lower()

        if action in {
            "6",
            "back",
            "b",
            "quit",
            "q",
        }:
            return

        if action in {"1", "list", "l"}:
            display_long_term_memories(
                brain.recall_long_term_memories()
            )
        elif action in {"2", "search", "s"}:
            _search_memories(brain)
        elif action in {"3", "edit", "e"}:
            _edit_memory(brain)
        elif action in {"4", "export", "x"}:
            _export_memories(brain)
        elif action in {"5", "delete", "d"}:
            _delete_memory(brain)
        else:
            print("Unknown memory action.")


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
    display_long_term_memories(
        long_term_memories
    )
    display_profile(profile)

    print(
        "\nEnter /memory instead of a message "
        "to manage saved memories."
    )

    while True:
        user_message = input("\nYou: ")

        if (
            user_message.strip().lower()
            == "/memory"
        ):
            run_memory_management(brain)
            continue

        break

    if not user_message.strip():
        print("No message was entered.")
        return

    print("\nElysia: ", end="", flush=True)

    for chunk in brain.stream_chat(
        user_message
    ):
        print(chunk, end="", flush=True)

    print()

    try:
        candidates = brain.extract_memory_candidates(
            user_message
        )
    except ValueError as error:
        print(
            "Memory extraction skipped: "
            f"{error}"
        )
        return

    review_memory_candidates(
        brain,
        candidates,
    )