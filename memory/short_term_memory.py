"""Utilities for token-bounded short-term memory."""

from math import ceil
from typing import TypedDict


class ShortTermTurn(TypedDict):
    """Keep a user message and its assistant reply as one eviction unit."""

    user_message: str
    assistant_message: str


def estimate_token_count(text: str) -> int:
    """Estimate token usage locally with separate ASCII and CJK heuristics.

    ASCII text is approximated at four characters per token, while every
    non-ASCII character counts as one token. This intentionally favors a safe
    upper estimate for Chinese text without requiring a model tokenizer.
    """
    if not text:
        return 0

    ascii_character_count = sum(
        character.isascii()
        for character in text
    )
    non_ascii_character_count = (
        len(text) - ascii_character_count
    )

    return (
        ceil(ascii_character_count / 4)
        + non_ascii_character_count
    )


class ShortTermMemory:
    """Keep recent complete turns within a token budget."""

    def __init__(self, token_budget: int) -> None:
        """Create an empty turn buffer with a positive token budget.

        Raises:
            ValueError: If ``token_budget`` is not greater than zero.
        """

        if token_budget <= 0:
            raise ValueError("Token budget must be greater than zero.")

        self._token_budget = token_budget
        self._turns: list[ShortTermTurn] = []
        self._token_count = 0

    @property
    def token_budget(self) -> int:
        """Return the configured budget for rebuilding a Chat-local window."""

        return self._token_budget

    def remember_turn(
        self,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Store a complete turn and evict oldest turns until it fits.

        Raises:
            ValueError: If either side of the turn contains no text.
        """

        cleaned_user_message = user_message.strip()
        cleaned_assistant_message = assistant_message.strip()

        if not cleaned_user_message:
            raise ValueError("User message cannot be empty.")

        if not cleaned_assistant_message:
            raise ValueError("Assistant message cannot be empty.")

        turn: ShortTermTurn = {
            "user_message": cleaned_user_message,
            "assistant_message": cleaned_assistant_message,
        }

        self._turns.append(turn)
        self._token_count += self._estimate_turn_token_count(turn)
        self._trim_to_budget()

    def get_turns(self) -> list[ShortTermTurn]:
        """Return a shallow list copy so callers cannot resize the buffer."""

        return list(self._turns)

    def get_token_count(self) -> int:
        """Return the estimated token usage of all retained turns."""

        return self._token_count

    def _trim_to_budget(self) -> None:
        """Evict complete turns from oldest to newest until within budget."""

        while (
            self._turns
            and self._token_count > self._token_budget
        ):
            oldest_turn = self._turns.pop(0)
            self._token_count -= self._estimate_turn_token_count(
                oldest_turn
            )

    @staticmethod
    def _estimate_turn_token_count(
        turn: ShortTermTurn,
    ) -> int:
        """Return the combined estimate for both messages in one turn."""

        return (
            estimate_token_count(turn["user_message"])
            + estimate_token_count(turn["assistant_message"])
        )
