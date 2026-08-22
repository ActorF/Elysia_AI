"""Public interface for Elysia's memory package."""

# Re-export schemas and services through one stable memory API.
from .conversation import ConversationMessage
from .conversation_summary import (
    CONVERSATION_SUMMARY_SCHEMA_VERSION,
    ConversationSummary,
    ConversationSummaryContent,
    ConversationSummaryData,
    load_conversation_summary,
    save_conversation_summary,
    validate_conversation_summary_content,
)
from .extraction import (
    MemoryCandidate,
    MemoryExtractor,
)
from .long_term_memory import (
    LONG_TERM_MEMORY_SCHEMA_VERSION,
    LongTermMemoryData,
    LongTermMemoryRecord,
    LongTermMemorySearchResult,
    LongTermMemorySource,
    delete_long_term_memory_record,
    edit_long_term_memory_record,
    export_long_term_memory,
    filter_long_term_memory_records,
    load_long_term_memory,
    save_long_term_memory_record,
    search_long_term_memory_records,
    validate_long_term_memory_data,
    validate_long_term_memory_record,
)
from .manager import Memory
from .profile import (
    PROFILE_SCHEMA_VERSION,
    Profile,
)
from .retrieval import (
    MemoryRetrievalSource,
    MemoryRetriever,
    RetrievedMemory,
)
from .scope import (
    MemoryScope,
    MemoryScopeContext,
    MemoryScopeRef,
    validate_memory_scope,
)
from .short_term_memory import (
    ShortTermMemory,
    ShortTermTurn,
)
from .summarization import (
    ConversationSummarizer,
)


# Explicit exports prevent internal helpers from becoming accidental API.
__all__ = [
    "CONVERSATION_SUMMARY_SCHEMA_VERSION",
    "ConversationMessage",
    "ConversationSummarizer",
    "ConversationSummary",
    "ConversationSummaryContent",
    "ConversationSummaryData",
    "LONG_TERM_MEMORY_SCHEMA_VERSION",
    "LongTermMemoryData",
    "LongTermMemoryRecord",
    "LongTermMemorySearchResult",
    "LongTermMemorySource",
    "Memory",
    "MemoryCandidate",
    "MemoryExtractor",
    "MemoryRetrievalSource",
    "MemoryRetriever",
    "MemoryScope",
    "MemoryScopeContext",
    "MemoryScopeRef",
    "PROFILE_SCHEMA_VERSION",
    "Profile",
    "RetrievedMemory",
    "ShortTermMemory",
    "ShortTermTurn",
    "delete_long_term_memory_record",
    "edit_long_term_memory_record",
    "export_long_term_memory",
    "filter_long_term_memory_records",
    "load_conversation_summary",
    "load_long_term_memory",
    "save_conversation_summary",
    "save_long_term_memory_record",
    "search_long_term_memory_records",
    "validate_long_term_memory_data",
    "validate_long_term_memory_record",
    "validate_memory_scope",
    "validate_conversation_summary_content",
]
