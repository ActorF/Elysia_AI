"""Utilities for token-bounded short-term memory."""

from math import ceil
from typing import TypedDict


class ShortTermTurn(TypedDict):
    user_message: str
    assistant_message: str


def estimate_token_count(text: str) -> int:
    """Estimate local text token usage without an online service."""
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
        if token_budget <= 0:
            raise ValueError("Token budget must be greater than zero.")

        self._token_budget = token_budget
        self._turns: list[ShortTermTurn] = []
        self._token_count = 0

    def remember_turn(
        self,
        user_message: str,
        assistant_message: str,
    ) -> None:
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
        return list(self._turns)

    def get_token_count(self) -> int:
        return self._token_count

    def _trim_to_budget(self) -> None:
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
        return (
            estimate_token_count(turn["user_message"])
            + estimate_token_count(turn["assistant_message"])
        )
