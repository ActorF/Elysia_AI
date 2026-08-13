"""Contract for conversation-summary generators."""

from typing import Protocol

from .conversation import ConversationMessage
from .conversation_summary import ConversationSummaryContent


class ConversationSummarizer(Protocol):
    """Describe how structured conversation content is generated."""

    def summarize(
        self,
        messages: list[ConversationMessage],
        previous_content: ConversationSummaryContent | None = None,
    ) -> ConversationSummaryContent:
        """Create or update structured content from source messages."""
        ...