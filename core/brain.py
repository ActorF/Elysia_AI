"""Orchestrate chat generation, prompt context, and memory services."""

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from chats import ChatSession
from memory import (
    ConversationMessage,
    ConversationSummary,
    ConversationSummarizer,
    LongTermMemoryRecord,
    LongTermMemorySearchResult,
    Memory,
    MemoryCandidate,
    MemoryExtractor,
    MemoryRetriever,
    MemoryScope,
    Profile,
    RetrievedMemory,
    ShortTermMemory,
)

from .chat_model import ChatMessage, ChatModel
from .prompts import build_elysia_system_prompt

logger = logging.getLogger(__name__)


def _get_unsummarized_messages(
    messages: list[ConversationMessage],
    existing_summary: ConversationSummary | None,
) -> list[ConversationMessage]:
    """Return the message suffix not covered by an existing summary.

    Count and timestamp anchors are checked before slicing so a stale or
    mismatched summary can never silently skip unrelated conversation data.

    Raises:
        ValueError: If summary metadata does not match stored messages.
    """

    if existing_summary is None:
        return list(messages)

    summarized_count = existing_summary[
        "source_message_count"
    ]

    if (
        summarized_count <= 0
        or summarized_count > len(messages)
    ):
        raise ValueError(
            "Conversation summary does not match "
            "stored messages."
        )

    if (
        messages[0]["timestamp"]
        != existing_summary[
            "source_start_timestamp"
        ]
        or messages[
            summarized_count - 1
        ]["timestamp"]
        != existing_summary[
            "source_end_timestamp"
        ]
    ):
        raise ValueError(
            "Conversation summary does not match "
            "stored messages."
        )

    return messages[summarized_count:]


class Brain:
    """Coordinate one conversational use case across pluggable services.

    ``Brain`` owns application flow—building prompts, calling the model, and
    committing completed results—while model adapters and memory stores own
    their respective infrastructure details.
    """

    def __init__(
        self,
        model_name: str,
        memory: Memory,
        chat_model: ChatModel | None = None,
        short_term_memory: (
            ShortTermMemory | None
        ) = None,
        memory_extractor: (
            MemoryExtractor | None
        ) = None,
        conversation_summarizer: (
            ConversationSummarizer | None
        ) = None,
        memory_retriever: (
            MemoryRetriever | None
        ) = None,
    ) -> None:
        """Inject the model, persistence, retrieval, and summarization services.

        Optional collaborators allow smaller configurations and make each use
        case independently testable. Features return an empty result or raise a
        clear runtime error when their required collaborator is unavailable.

        Raises:
            ValueError: If ``model_name`` contains no text.
        """

        cleaned_model_name = model_name.strip()

        if not cleaned_model_name:
            raise ValueError(
                "Model name cannot be empty."
            )

        self._model_name = cleaned_model_name
        self._memory = memory
        self._chat_model = chat_model
        self._short_term_memory = (
            short_term_memory
        )
        self._memory_extractor = (
            memory_extractor
        )
        self._conversation_summarizer = (
            conversation_summarizer
        )
        self._memory_retriever = (
            memory_retriever
        )

        logger.info(
            "Brain initialized with model: %s",
            self._model_name,
        )

    @property
    def model_name(self) -> str:
        """Return the configured local model identifier."""

        return self._model_name

    def hello(self) -> None:
        """Print a small diagnostic banner naming the active model."""

        print(
            f"Hello from Brain! "
            f"Model: {self.model_name}"
        )

    def start_session(self) -> Profile:
        """Record a launch, display the banner, and return the active profile."""

        profile = self._memory.record_launch()

        self.hello()

        logger.info(
            "Session started. Launch count: %s",
            profile["launch_count"],
        )

        return profile

    def chat(self, user_message: str) -> str:
        """Generate, validate, and persist one non-streaming chat turn."""

        cleaned_user_message = user_message.strip()

        if not cleaned_user_message:
            raise ValueError(
                "User message cannot be empty."
            )

        if self._chat_model is None:
            raise RuntimeError(
                "Chat model is not connected."
            )

        profile = self._memory.load_profile()

        chat_messages = self._build_chat_messages(
            profile,
            cleaned_user_message,
        )

        reply = self._chat_model.generate_reply(
            chat_messages
        ).strip()

        if not reply:
            raise ValueError(
                "Model reply cannot be empty."
            )

        # Commit both sides only after a complete, non-empty reply exists.
        self.remember_message(
            profile["user_name"],
            cleaned_user_message,
        )
        self.remember_message(
            profile["assistant_name"],
            reply,
        )

        if self._short_term_memory is not None:
            self._short_term_memory.remember_turn(
                cleaned_user_message,
                reply,
            )

        logger.info("Chat turn completed.")

        return reply

    def stream_chat(
        self,
        user_message: str,
    ) -> Iterator[str]:
        """Yield model chunks immediately, then persist the completed turn.

        Chunks are accumulated while streaming because memory must store one
        coherent assistant message. A failed or empty stream is not committed.
        """

        cleaned_user_message = user_message.strip()

        if not cleaned_user_message:
            raise ValueError(
                "User message cannot be empty."
            )

        if self._chat_model is None:
            raise RuntimeError(
                "Chat model is not connected."
            )

        profile = self._memory.load_profile()

        chat_messages = self._build_chat_messages(
            profile,
            cleaned_user_message,
        )

        reply_chunks: list[str] = []

        for chunk in self._chat_model.stream_reply(
            chat_messages
        ):
            # Preserve exact chunk order for both live output and final storage.
            reply_chunks.append(chunk)
            yield chunk

        reply = "".join(reply_chunks).strip()

        if not reply:
            raise ValueError(
                "Model reply cannot be empty."
            )

        self.remember_message(
            profile["user_name"],
            cleaned_user_message,
        )
        self.remember_message(
            profile["assistant_name"],
            reply,
        )

        if self._short_term_memory is not None:
            self._short_term_memory.remember_turn(
                cleaned_user_message,
                reply,
            )

        logger.info("Streaming chat turn completed.")

    def remember_message(
        self,
        speaker: str,
        message: str,
    ) -> None:
        """Persist one named speaker message through the memory facade."""

        self._memory.save_message(
            speaker,
            message,
        )

    def _to_chat_message(
        self,
        conversation_message: ConversationMessage,
        profile: Profile,
    ) -> ChatMessage | None:
        """Map a stored speaker name to a model role.

        Messages from unknown speakers return ``None`` so unsupported records
        do not acquire an invented model role.
        """

        speaker = conversation_message["speaker"]
        content = conversation_message["message"]

        if speaker == profile["user_name"]:
            return {
                "role": "user",
                "content": content,
            }

        if speaker == profile["assistant_name"]:
            return {
                "role": "assistant",
                "content": content,
            }

        return None

    def _build_recent_context(
        self,
        profile: Profile,
        limit: int = 10,
    ) -> list[ChatMessage]:
        """Build ordered recent context from RAM or persistent history.

        Configured short-term memory takes precedence because it already holds
        complete token-bounded turns and avoids duplicating persistent records.
        """

        context: list[ChatMessage] = []

        if self._short_term_memory is not None:
            for turn in self._short_term_memory.get_turns():
                context.append(
                    {
                        "role": "user",
                        "content": turn["user_message"],
                    }
                )
                context.append(
                    {
                        "role": "assistant",
                        "content": turn["assistant_message"],
                    }
                )

            return context

        recent_messages = self._memory.get_recent_messages(
            limit
        )

        for conversation_message in recent_messages:
            chat_message = self._to_chat_message(
                conversation_message,
                profile,
            )

            if chat_message is not None:
                context.append(chat_message)

        return context

    @staticmethod
    def _build_session_recent_context(
        chat_session: ChatSession,
        limit: int = 10,
    ) -> list[ChatMessage]:
        """Build safe recent context from one explicit Chat aggregate."""

        if limit <= 0:
            raise ValueError(
                "Message limit must be greater than zero."
            )

        return [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in chat_session.messages[-limit:]
            if (
                message.role in ("user", "assistant")
                and message.content.strip()
            )
        ]

    def _build_chat_messages(
        self,
        profile: Profile,
        current_user_message: str,
        limit: int = 10,
        *,
        chat_session: ChatSession | None = None,
    ) -> list[ChatMessage]:
        """Assemble system rules, recent context, and the current user message.

        The ordering is deliberate: trusted system prompt first, chronological
        history second, and the new request last.
        """

        if chat_session is None:
            retrieved_memories = (
                self.retrieve_relevant_memories(
                    current_user_message,
                    profile,
                )
            )
            recent_context = self._build_recent_context(
                profile,
                limit,
            )
        else:
            retrieved_memories = (
                self.retrieve_relevant_memories_for_chat(
                    current_user_message,
                    chat_session,
                    profile,
                )
            )
            recent_context = (
                self._build_session_recent_context(
                    chat_session,
                    limit,
                )
            )

        system_prompt = (
            build_elysia_system_prompt(
                profile,
                retrieved_memories,
            )
        )

        messages: list[ChatMessage] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        messages.extend(recent_context)

        messages.append(
            {
                "role": "user",
                "content": (
                    current_user_message
                ),
            }
        )

        return messages

    def retrieve_relevant_memories(
        self,
        query: str,
        profile: Profile | None = None,
    ) -> list[RetrievedMemory]:
        """Retrieve relevant saved context for one user query."""
        cleaned_query = query.strip()

        if not cleaned_query:
            raise ValueError(
                "Memory retrieval query cannot be empty."
            )

        if self._memory_retriever is None:
            return []

        active_profile = (
            profile
            if profile is not None
            else self._memory.load_profile()
        )

        summary_data = (
            self._memory.get_conversation_summary()
        )

        results = (
            self._memory_retriever.retrieve(
                cleaned_query,
                active_profile,
                summary_data["summary"],
                self._memory.get_long_term_memories(
                    scope="global",
                ),
            )
        )

        logger.info(
            "Retrieved %s relevant memory items.",
            len(results),
        )

        return results

    def retrieve_relevant_memories_for_chat(
        self,
        query: str,
        chat_session: ChatSession,
        profile: Profile | None = None,
    ) -> list[RetrievedMemory]:
        """Retrieve Global and matching Project/Chat context only."""

        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError(
                "Memory retrieval query cannot be empty."
            )

        if not isinstance(chat_session, ChatSession):
            raise ValueError(
                "chat_session must be ChatSession."
            )

        if self._memory_retriever is None:
            return []

        active_profile = (
            profile
            if profile is not None
            else self._memory.load_profile()
        )
        results = self._memory_retriever.retrieve_for_chat(
            cleaned_query,
            active_profile,
            chat_session,
            self._memory.get_long_term_memories(),
        )

        logger.info(
            "Retrieved %s scoped memory items for Chat %s.",
            len(results),
            chat_session.chat_id,
        )
        return results

    def recall_long_term_memories(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> list[LongTermMemoryRecord]:
        """Return all records or one exact scoped view."""

        return self._memory.get_long_term_memories(
            scope=scope,
            scope_id=scope_id,
        )

    def search_long_term_memories(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> list[LongTermMemorySearchResult]:
        """Search all records or one scoped view."""

        return self._memory.search_long_term_memories(
            query,
            scope=scope,
            scope_id=scope_id,
        )

    def edit_long_term_memory(
        self,
        memory_number: int,
        key: str,
        value: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> LongTermMemoryRecord:
        """Edit a selected memory and log the resulting key."""

        updated_record = (
            self._memory.edit_long_term_memory(
                memory_number,
                key,
                value,
                scope=scope,
                scope_id=scope_id,
            )
        )

        logger.info(
            "Long-term memory edited: "
            "number=%s key=%s",
            memory_number,
            updated_record["key"],
        )

        return updated_record

    def export_long_term_memories(
        self,
        export_file: Path,
        *,
        overwrite: bool = False,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> Path:
        """Export persistent memories and log the destination path."""

        exported_file = (
            self._memory.export_long_term_memories(
                export_file,
                overwrite=overwrite,
                scope=scope,
                scope_id=scope_id,
            )
        )

        logger.info(
            "Long-term memories exported: path=%s",
            exported_file,
        )

        return exported_file

    def delete_long_term_memory(
        self,
        memory_number: int,
        *,
        confirmed: bool = False,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
    ) -> LongTermMemoryRecord:
        """Delete one memory only after an explicit confirmation flag.

        Raises:
            PermissionError: If ``confirmed`` is not exactly ``True``.
        """

        # An exact True check prevents truthy strings or integers bypassing UI
        # confirmation semantics.
        if confirmed is not True:
            raise PermissionError(
                "Deleting long-term memory "
                "requires confirmation."
            )

        deleted_record = (
            self._memory.delete_long_term_memory(
                memory_number,
                scope=scope,
                scope_id=scope_id,
            )
        )

        logger.info(
            "Long-term memory deleted: "
            "number=%s key=%s",
            memory_number,
            deleted_record["key"],
        )

        return deleted_record

    def extract_memory_candidates(
        self,
        user_message: str,
    ) -> list[MemoryCandidate]:
        """Extract possible memories without saving them."""
        cleaned_user_message = user_message.strip()

        if not cleaned_user_message:
            raise ValueError(
                "User message cannot be empty."
            )

        if self._memory_extractor is None:
            return []

        return self._memory_extractor.extract_candidates(
            cleaned_user_message
        )

    def confirm_memory_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        scope: MemoryScope = "global",
        scope_id: str | None = None,
    ) -> LongTermMemoryRecord:
        """Save one confirmed candidate to an explicit scope."""
        return self._memory.save_long_term_memory(
            candidate["key"],
            candidate["value"],
            candidate["source_type"],
            candidate["source_text"],
            scope=scope,
            scope_id=scope_id,
        )

    def get_unsummarized_message_count(
        self,
    ) -> int:
        """Return the number of messages not covered by the summary."""
        messages = self._memory.get_all_messages()

        summary_data = (
            self._memory.get_conversation_summary()
        )

        unsummarized_messages = (
            _get_unsummarized_messages(
                messages,
                summary_data["summary"],
            )
        )

        return len(
            unsummarized_messages
        )

    def summarize_conversation(
        self,
    ) -> ConversationSummary | None:
        """Incrementally summarize messages not covered by the saved summary.

        Returns the existing summary when nothing is new and ``None`` when no
        conversation exists. Newly generated content is saved with source
        count and timestamp anchors for later consistency checks.
        """

        if self._conversation_summarizer is None:
            raise RuntimeError(
                "Conversation summarizer is not connected."
            )

        messages = self._memory.get_all_messages()

        if not messages:
            return None

        summary_data = (
            self._memory.get_conversation_summary()
        )
        existing_summary = summary_data["summary"]

        unsummarized_messages = (
            _get_unsummarized_messages(
                messages,
                existing_summary,
            )
        )

        if not unsummarized_messages:
            return existing_summary

        previous_content = (
            existing_summary["content"]
            if existing_summary is not None
            else None
        )

        content = (
            self._conversation_summarizer.summarize(
                unsummarized_messages,
                previous_content,
            )
        )

        summary: ConversationSummary = {
            "content": content,
            "source_message_count": len(messages),
            "source_start_timestamp": (
                messages[0]["timestamp"]
            ),
            "source_end_timestamp": (
                messages[-1]["timestamp"]
            ),
            "updated_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

        self._memory.save_conversation_summary(
            summary
        )

        logger.info(
            "Conversation summary updated through "
            "%s messages.",
            len(messages),
        )

        return summary

    def recall_recent_messages(
        self,
        limit: int = 10,
    ) -> list[ConversationMessage]:
        """Return at most ``limit`` newest persistent conversation messages."""

        return self._memory.get_recent_messages(limit)
