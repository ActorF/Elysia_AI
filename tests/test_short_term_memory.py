"""Test token-bounded short-term conversation memory."""

import pytest

from memory.short_term_memory import (
    ShortTermMemory,
    estimate_token_count,
)


@pytest.mark.parametrize(
    ("text", "expected_token_count"),
    [
        ("", 0),
        ("abcd", 1),
        ("abcde", 2),
        ("你好", 2),
        ("ab你好", 3),
    ],
)
def test_estimate_token_count(
    text: str,
    expected_token_count: int,
) -> None:
    """Verify that estimate token count."""
    assert (
        estimate_token_count(text)
        == expected_token_count
    )


@pytest.mark.parametrize("token_budget", [0, -1])
def test_rejects_non_positive_token_budget(
    token_budget: int,
) -> None:
    """Reject non-positive token budgets before context-window calculations."""
    with pytest.raises(
        ValueError,
        match=r"Token budget must be greater than zero\.",
    ):
        ShortTermMemory(token_budget)


def test_remember_turn_cleans_and_counts_messages() -> None:
    """Verify that remember turn cleans and counts messages."""
    memory = ShortTermMemory(token_budget=10)

    assert memory.token_budget == 10

    memory.remember_turn(
        "  abcd  ",
        "  你好  ",
    )

    assert memory.get_turns() == [
        {
            "user_message": "abcd",
            "assistant_message": "你好",
        }
    ]
    assert memory.get_token_count() == 3


@pytest.mark.parametrize(
    (
        "user_message",
        "assistant_message",
        "error_pattern",
    ),
    [
        (
            "   ",
            "Answer",
            r"User message cannot be empty\.",
        ),
        (
            "Question",
            "   ",
            r"Assistant message cannot be empty\.",
        ),
    ],
)
def test_remember_turn_rejects_empty_messages(
    user_message: str,
    assistant_message: str,
    error_pattern: str,
) -> None:
    """Verify that remember turn rejects empty messages."""
    memory = ShortTermMemory(token_budget=10)

    with pytest.raises(
        ValueError,
        match=error_pattern,
    ):
        memory.remember_turn(
            user_message,
            assistant_message,
        )

    assert memory.get_turns() == []
    assert memory.get_token_count() == 0


def test_trims_oldest_complete_turns() -> None:
    """Trim oldest complete turns without splitting a user-assistant pair."""
    memory = ShortTermMemory(token_budget=4)

    memory.remember_turn("aaaa", "bbbb")
    memory.remember_turn("cccc", "dddd")
    memory.remember_turn("eeee", "ffff")

    assert memory.get_turns() == [
        {
            "user_message": "cccc",
            "assistant_message": "dddd",
        },
        {
            "user_message": "eeee",
            "assistant_message": "ffff",
        },
    ]
    assert memory.get_token_count() == 4


def test_discards_turn_larger_than_budget() -> None:
    """Verify that discards turn larger than budget."""
    memory = ShortTermMemory(token_budget=1)

    memory.remember_turn("aaaa", "bbbb")

    assert memory.get_turns() == []
    assert memory.get_token_count() == 0


def test_get_turns_returns_a_new_list() -> None:
    """Verify that get turns returns a new list."""
    memory = ShortTermMemory(token_budget=10)
    memory.remember_turn("Question", "Answer")

    returned_turns = memory.get_turns()
    returned_turns.clear()

    assert memory.get_turns() == [
        {
            "user_message": "Question",
            "assistant_message": "Answer",
        }
    ]
