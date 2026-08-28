"""Orchestrate chat generation, prompt context, and memory services."""

import logging
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

from chats import (
    ChatId,
    ChatMessage as StoredChatMessage,
    ChatMessageId,
    ChatSession,
    ChatSessionMeta,
    ChatSummary,
    ConversationMode,
    ProjectId,
)
from memory import (
    ConversationMessage,
    ConversationSummary,
    ConversationSummaryContent,
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
from projects import Project, ProjectChatService

from .active_conversation import ActiveConversationService
from .chat_model import ChatMessage, ChatModel
from .exceptions import ChatModelMismatchError
from .prompts import (
    ActiveConversationPromptContext,
    ProjectPromptContext,
    build_elysia_system_prompt,
)

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


def _get_unsummarized_chat_messages(
    chat_session: ChatSession,
) -> tuple[StoredChatMessage, ...]:
    """Return user/assistant messages after a valid Chat summary prefix.

    Stable IDs in an existing summary must exactly match the chronological
    prefix they claim to cover.
    """

    messages = tuple(
        message
        for message in chat_session.messages
        if (
            message.role in ("user", "assistant")
            and message.content.strip()
        )
    )
    if chat_session.summary is None:
        return messages

    summarized_ids = chat_session.summary.source_message_ids
    if (
        len(summarized_ids) > len(messages)
        or tuple(
            message.message_id
            for message in messages[: len(summarized_ids)]
        )
        != summarized_ids
    ):
        raise ValueError(
            "Chat summary does not match the chronological message prefix."
        )
    return messages[len(summarized_ids) :]


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
        active_conversation_service: (
            ActiveConversationService | None
        ) = None,
        project_service: ProjectChatService | None = None,
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
        self._active_conversation_service = (
            active_conversation_service
        )
        self._project_service = project_service

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

    def create_chat(
        self,
        *,
        title: str,
        mode: ConversationMode = "chat",
        project_id: ProjectId | None = None,
    ) -> ChatSession:
        """Create one Chat through the active-conversation boundary."""

        service = self._require_active_conversation_service()
        return service.create_chat(
            title=title,
            mode=mode,
            model_name=self.model_name,
            project_id=project_id,
        )

    def create_project(
        self,
        *,
        name: str,
        custom_instructions: str | None = None,
    ) -> Project:
        """Create one Project through the Project application service."""

        return self._require_project_service().create_project(
            name=name,
            custom_instructions=custom_instructions,
        )

    def list_projects(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[Project, ...]:
        """List Projects through the application service boundary."""

        return self._require_project_service().list_projects(
            include_archived=include_archived,
        )

    def get_project(self, project_id: ProjectId) -> Project:
        """Load one Project through the application service boundary."""

        return self._require_project_service().get_project(project_id)

    def rename_project(
        self,
        project_id: ProjectId,
        new_name: str,
    ) -> Project:
        """Rename an active Project when its linked Chats are idle."""

        return self._require_project_service().rename_project(
            project_id,
            new_name,
        )

    def update_project(
        self,
        project_id: ProjectId,
        *,
        name: str,
        custom_instructions: str | None,
    ) -> Project:
        """Atomically update one Project's UI-editable fields."""

        return self._require_project_service().update_project(
            project_id,
            name=name,
            custom_instructions=custom_instructions,
        )

    def update_custom_instructions(
        self,
        project_id: ProjectId,
        custom_instructions: str | None,
    ) -> Project:
        """Replace one Project's optional custom instructions."""

        return self._require_project_service().update_custom_instructions(
            project_id,
            custom_instructions,
        )

    def bind_workspace(
        self,
        project_id: ProjectId,
        root_path: str,
    ) -> Project:
        """Set or replace one active Project's Workspace root."""

        return self._require_project_service().bind_workspace(
            project_id,
            root_path,
        )

    def unbind_workspace(self, project_id: ProjectId) -> Project:
        """Remove one active Project's Workspace binding."""

        return self._require_project_service().unbind_workspace(project_id)

    def archive_project(self, project_id: ProjectId) -> Project:
        """Archive one Project while preserving its Chat relationships."""

        return self._require_project_service().archive_project(project_id)

    def restore_project(self, project_id: ProjectId) -> Project:
        """Restore one archived Project and preserve its Chats."""

        return self._require_project_service().restore_project(project_id)

    def list_project_chats(
        self,
        project_id: ProjectId,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """List only Chat metadata linked to one Project."""

        return self._require_project_service().list_project_chats(
            project_id,
            include_archived=include_archived,
        )

    def attach_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Attach one unassigned idle Chat to an active Project."""

        return self._require_project_service().attach_chat(
            project_id,
            chat_id,
        )

    def detach_chat(
        self,
        project_id: ProjectId,
        chat_id: ChatId,
    ) -> ChatSession:
        """Detach one idle Chat from its active Project."""

        return self._require_project_service().detach_chat(
            project_id,
            chat_id,
        )

    def transfer_chat(
        self,
        chat_id: ChatId,
        destination_project_id: ProjectId,
    ) -> ChatSession:
        """Transfer one assigned idle Chat between active Projects."""

        return self._require_project_service().transfer_chat(
            chat_id,
            destination_project_id,
        )

    def move_chat(
        self,
        chat_id: ChatId,
        project_id: ProjectId | None,
    ) -> ChatSession:
        """Move one idle Chat to a Project or to the unassigned scope."""

        return self._require_project_service().move_chat(
            chat_id,
            project_id,
        )

    def get_or_create_default_chat(
        self,
        *,
        title: str = "Console Chat",
        mode: ConversationMode = "chat",
    ) -> ChatSession:
        """Resume one visible Chat or create a default for simple clients."""

        service = self._require_active_conversation_service()
        return service.get_or_create_default_chat(
            title=title,
            mode=mode,
            model_name=self.model_name,
        )

    def list_chats(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """List Chat metadata without exposing repository paths."""

        return self._require_active_conversation_service().list_chats(
            include_archived=include_archived,
        )

    def get_chat(self, chat_id: ChatId) -> ChatSession:
        """Load one persisted Chat through the application service."""

        return self._require_active_conversation_service().get_chat(chat_id)

    def is_chat_busy(self, chat_id: ChatId) -> bool:
        """Return whether the Chat currently owns a generation operation."""

        return self._require_active_conversation_service().is_chat_busy(
            chat_id
        )

    def rename_chat(
        self,
        chat_id: ChatId,
        new_title: str,
    ) -> ChatSession:
        """Rename an idle Chat through the active-conversation service."""

        return self._require_active_conversation_service().rename_chat(
            chat_id,
            new_title,
        )

    def pin_chat(
        self,
        chat_id: ChatId,
        pinned: bool = True,
    ) -> ChatSessionMeta:
        """Pin or unpin an idle Chat through the application boundary."""

        return self._require_active_conversation_service().pin_chat(
            chat_id,
            pinned,
        )

    def archive_chat(
        self,
        chat_id: ChatId,
        archived: bool = True,
    ) -> ChatSessionMeta:
        """Archive or restore an idle Chat while retaining its content."""

        return self._require_active_conversation_service().archive_chat(
            chat_id,
            archived,
        )

    def delete_chat(self, chat_id: ChatId) -> None:
        """Delete an idle Chat through the active-conversation service."""

        self._require_active_conversation_service().delete_chat(chat_id)

    def chat(
        self,
        chat_id: ChatId,
        user_message: str,
    ) -> str:
        """Generate and commit one non-streaming turn to an explicit Chat."""

        cleaned_user_message = user_message.strip()

        if not cleaned_user_message:
            raise ValueError(
                "User message cannot be empty."
            )

        if self._chat_model is None:
            raise RuntimeError(
                "Chat model is not connected."
            )

        service = self._require_active_conversation_service()
        with service.open_turn(chat_id) as active_conversation:
            self._validate_active_model(
                active_conversation.chat_session
            )
            profile = self._memory.load_profile()
            chat_messages = self._build_chat_messages(
                profile,
                cleaned_user_message,
                chat_session=active_conversation.chat_session,
                project=active_conversation.project,
            )

            reply = self._chat_model.generate_reply(
                chat_messages
            ).strip()
            if not reply:
                raise ValueError(
                    "Model reply cannot be empty."
                )

            service.commit_turn(
                active_conversation,
                user_message=cleaned_user_message,
                assistant_message=reply,
            )

        logger.info("Chat turn completed: chat_id=%s.", chat_id)

        return reply

    def stream_chat(
        self,
        chat_id: ChatId,
        user_message: str,
    ) -> Generator[str, None, None]:
        """Yield chunks, then commit a complete turn to the named Chat.

        A failed, cancelled, empty, or concurrently invalidated stream is never
        written as a partial turn. The Chat busy guard remains held for the
        complete generator lifetime and is released when the generator closes.
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

        service = self._require_active_conversation_service()
        with service.open_turn(chat_id) as active_conversation:
            self._validate_active_model(
                active_conversation.chat_session
            )
            profile = self._memory.load_profile()
            chat_messages = self._build_chat_messages(
                profile,
                cleaned_user_message,
                chat_session=active_conversation.chat_session,
                project=active_conversation.project,
            )

            reply_chunks: list[str] = []
            for chunk in self._chat_model.stream_reply(chat_messages):
                reply_chunks.append(chunk)
                yield chunk

            reply = "".join(reply_chunks).strip()
            if not reply:
                raise ValueError(
                    "Model reply cannot be empty."
                )

            service.commit_turn(
                active_conversation,
                user_message=cleaned_user_message,
                assistant_message=reply,
            )

        logger.info(
            "Streaming chat turn completed: chat_id=%s.",
            chat_id,
        )

    def _require_active_conversation_service(
        self,
    ) -> ActiveConversationService:
        """Return the lifecycle service or fail before any model work."""

        if self._active_conversation_service is None:
            raise RuntimeError(
                "Active conversation service is not connected."
            )
        return self._active_conversation_service

    def _require_project_service(self) -> ProjectChatService:
        """Return the Project service or fail before repository access."""

        if self._project_service is None:
            raise RuntimeError("Project service is not connected.")
        return self._project_service

    def _validate_active_model(self, chat_session: ChatSession) -> None:
        """Prevent silently answering a Chat with the wrong model adapter."""

        requested_model = chat_session.model_settings.model_name
        if requested_model != self.model_name:
            raise ChatModelMismatchError(
                f"Chat {chat_session.chat_id} requires model "
                f"{requested_model}, but Brain is connected to "
                f"{self.model_name}."
            )

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

    def _build_session_recent_context(
        self,
        chat_session: ChatSession,
        limit: int = 10,
    ) -> list[ChatMessage]:
        """Build one Chat-local context without sharing another Chat's RAM.

        When a short-term-memory budget is configured, a fresh buffer is
        rebuilt from this Chat's persisted complete turns. The configured
        object supplies policy only; its mutable turns are never reused across
        Chat IDs.
        """

        if limit <= 0:
            raise ValueError(
                "Message limit must be greater than zero."
            )

        recent_messages = [
            message
            for message in chat_session.messages
            if (
                message.role in ("user", "assistant")
                and message.content.strip()
            )
        ]

        if self._short_term_memory is None:
            return [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in recent_messages[-limit:]
            ]

        chat_window = ShortTermMemory(
            self._short_term_memory.token_budget
        )
        pending_user_message: str | None = None
        for message in recent_messages:
            if message.role == "user":
                pending_user_message = message.content
                continue

            if pending_user_message is not None:
                chat_window.remember_turn(
                    pending_user_message,
                    message.content,
                )
                pending_user_message = None

        context: list[ChatMessage] = []
        for turn in chat_window.get_turns():
            context.extend(
                [
                    {
                        "role": "user",
                        "content": turn["user_message"],
                    },
                    {
                        "role": "assistant",
                        "content": turn["assistant_message"],
                    },
                ]
            )
        return context

    @staticmethod
    def _build_active_prompt_context(
        chat_session: ChatSession,
        project: Project | None,
    ) -> ActiveConversationPromptContext:
        """Select safe Chat and Project fields for prompt serialization."""

        project_context: ProjectPromptContext | None = None
        if project is not None:
            project_context = {
                "project_id": str(project.project_id),
                "name": project.name,
                "custom_instructions": (
                    project.settings.custom_instructions
                ),
            }

        return {
            "chat_id": str(chat_session.chat_id),
            "mode": chat_session.mode,
            "model_name": chat_session.model_settings.model_name,
            "project": project_context,
        }

    def _build_chat_messages(
        self,
        profile: Profile,
        current_user_message: str,
        limit: int = 10,
        *,
        chat_session: ChatSession | None = None,
        project: Project | None = None,
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

        active_prompt_context = (
            None
            if chat_session is None
            else self._build_active_prompt_context(
                chat_session,
                project,
            )
        )

        system_prompt = (
            build_elysia_system_prompt(
                profile,
                retrieved_memories,
                active_prompt_context,
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

    def get_unsummarized_chat_message_count(
        self,
        chat_id: ChatId,
    ) -> int:
        """Return active Chat messages not covered by its structured summary."""

        chat_session = (
            self._require_active_conversation_service().get_chat(chat_id)
        )
        return len(_get_unsummarized_chat_messages(chat_session))

    def summarize_chat(
        self,
        chat_id: ChatId,
    ) -> ChatSummary | None:
        """Create or incrementally update the named Chat's summary.

        Summary generation shares the Chat busy guard with reply generation,
        so a summary can never race a new Turn or attach to another Chat.
        """

        if self._conversation_summarizer is None:
            raise RuntimeError(
                "Conversation summarizer is not connected."
            )

        service = self._require_active_conversation_service()
        with service.open_turn(chat_id) as active_conversation:
            chat_session = active_conversation.chat_session
            self._validate_active_model(chat_session)
            source_messages = tuple(
                message
                for message in chat_session.messages
                if (
                    message.role in ("user", "assistant")
                    and message.content.strip()
                )
            )
            if not source_messages:
                return None

            unsummarized_messages = _get_unsummarized_chat_messages(
                chat_session
            )
            if not unsummarized_messages:
                return chat_session.summary

            profile = self._memory.load_profile()
            messages_for_model: list[ConversationMessage] = [
                {
                    "timestamp": message.created_at.isoformat(),
                    "speaker": (
                        profile["user_name"]
                        if message.role == "user"
                        else profile["assistant_name"]
                    ),
                    "message": message.content,
                }
                for message in unsummarized_messages
            ]
            existing_summary = chat_session.summary
            previous_content: ConversationSummaryContent | None = (
                None
                if existing_summary is None
                else {
                    "facts": list(existing_summary.facts),
                    "decisions": list(existing_summary.decisions),
                    "action_items": list(
                        existing_summary.action_items
                    ),
                    "unresolved_questions": list(
                        existing_summary.unresolved_questions
                    ),
                }
            )
            content = self._conversation_summarizer.summarize(
                messages_for_model,
                previous_content,
            )
            source_message_ids: tuple[ChatMessageId, ...] = tuple(
                message.message_id
                for message in source_messages
            )
            updated_session = service.commit_summary(
                active_conversation,
                facts=content["facts"],
                decisions=content["decisions"],
                action_items=content["action_items"],
                unresolved_questions=(
                    content["unresolved_questions"]
                ),
                source_message_ids=source_message_ids,
            )

        logger.info(
            "Chat summary updated: chat_id=%s messages=%s.",
            chat_id,
            len(source_messages),
        )
        return updated_session.summary

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
