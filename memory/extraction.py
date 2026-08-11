"""Data contract for possible long-term memories."""

from typing import Protocol, TypedDict

from .long_term_memory import LongTermMemorySource


class MemoryCandidate(TypedDict):
    """Describe extracted information before it is saved."""

    key: str
    value: str
    source_type: LongTermMemorySource
    source_text: str
    requires_confirmation: bool

class MemoryExtractor(Protocol):
    """Describe how candidate memories are extracted."""

    def extract_candidates(
        self,
        user_message: str,
    ) -> list[MemoryCandidate]:
        """Return possible memories without saving them."""
        ...