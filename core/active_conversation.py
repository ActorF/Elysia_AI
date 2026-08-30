"""Coordinate one active Chat lifecycle across Chat and Project stores."""

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import Lock, RLock

from chats import (
    ChatId,
    ChatMessage,
    ChatMessageId,
    ChatRepository,
    ChatSession,
    ChatSessionMeta,
    ChatSummary,
    ConversationMode,
    ProjectId,
    create_chat_message,
)
from projects import Project, ProjectRepository

from .exceptions import (
    ChatBusyError,
    ChatChangedDuringGenerationError,
    ChatRetryTargetError,
    ConversationUnavailableError,
)

Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    """Return a timezone-aware UTC timestamp for persisted lifecycle data."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ActiveConversation:
    """Hold one immutable Chat/Project snapshot for a guarded operation."""

    chat_session: ChatSession
    project: Project | None
    _turn_token: object = field(
        repr=False,
        compare=False,
    )


class ActiveConversationService:
    """Own active Chat state, busy guards, and completed persistence.

    The service never stores model output incrementally. It loads one immutable
    snapshot when an operation starts, then saves a complete replacement only
    after generation succeeds and the persisted snapshot is still unchanged.
    """

    def __init__(
        self,
        chat_repository: ChatRepository,
        project_repository: ProjectRepository,
        *,
        clock: Clock = _default_clock,
    ) -> None:
        """Inject repositories and initialize per-Chat in-process state."""

        self._chat_repository = chat_repository
        self._project_repository = project_repository
        self._clock = clock
        self._state_lock = Lock()
        # Chat details are independent, but every commit also updates the one
        # shared lightweight index. Serialize that short persistence phase
        # while still allowing models for different Chats to run in parallel.
        self._commit_lock = RLock()
        self._active_turn_tokens: dict[ChatId, object] = {}

    def create_chat(
        self,
        *,
        title: str,
        mode: ConversationMode,
        model_name: str,
        project_id: ProjectId | None = None,
    ) -> ChatSession:
        """Create a Chat after validating an optional active Project."""

        if project_id is not None:
            project = self._project_repository.get_project(project_id)
            if project.is_archived:
                raise ConversationUnavailableError(
                    "An archived Project cannot receive a new Chat: "
                    f"{project_id}."
                )

        with self._commit_lock:
            return self._chat_repository.create_chat(
                title=title,
                mode=mode,
                model_name=model_name,
                project_id=project_id,
            )

    def get_or_create_default_chat(
        self,
        *,
        title: str,
        mode: ConversationMode,
        model_name: str,
    ) -> ChatSession:
        """Resume the first visible Chat or create one for a simple client."""

        with self._commit_lock:
            available_chats = self._chat_repository.list_chats()
            if available_chats:
                return self._chat_repository.get_chat(
                    available_chats[0].chat_id
                )

            return self.create_chat(
                title=title,
                mode=mode,
                model_name=model_name,
            )

    def list_chats(
        self,
        *,
        include_archived: bool = False,
    ) -> tuple[ChatSessionMeta, ...]:
        """Return lightweight Chat metadata through the service boundary."""

        with self._commit_lock:
            return self._chat_repository.list_chats(
                include_archived=include_archived,
            )

    def get_chat(self, chat_id: ChatId) -> ChatSession:
        """Return one persisted Chat without opening a generation operation."""

        with self._commit_lock:
            return self._chat_repository.get_chat(chat_id)

    def is_chat_busy(self, chat_id: ChatId) -> bool:
        """Return whether this process currently owns an operation for Chat."""

        with self._state_lock:
            return chat_id in self._active_turn_tokens

    def rename_chat(
        self,
        chat_id: ChatId,
        new_title: str,
    ) -> ChatSession:
        """Rename an idle Chat without changing its messages or Project link.

        Raises:
            ChatBusyError: If generation or summarization currently owns the
                Chat.
        """

        with self._guard_idle_chat_action(chat_id):
            return self._chat_repository.rename_chat(chat_id, new_title)

    def pin_chat(
        self,
        chat_id: ChatId,
        pinned: bool = True,
    ) -> ChatSessionMeta:
        """Set an idle Chat's pin state through the repository boundary.

        Raises:
            ChatBusyError: If generation or summarization currently owns the
                Chat.
        """

        with self._guard_idle_chat_action(chat_id):
            return self._chat_repository.pin_chat(chat_id, pinned)

    def archive_chat(
        self,
        chat_id: ChatId,
        archived: bool = True,
    ) -> ChatSessionMeta:
        """Set an idle Chat's archive state while retaining its full content.

        Raises:
            ChatBusyError: If generation or summarization currently owns the
                Chat.
        """

        with self._guard_idle_chat_action(chat_id):
            return self._chat_repository.archive_chat(chat_id, archived)

    def delete_chat(self, chat_id: ChatId) -> None:
        """Delete an idle Chat without modifying its associated Project.

        Raises:
            ChatBusyError: If generation or summarization currently owns the
                Chat.
        """

        with self._guard_idle_chat_action(chat_id):
            self._chat_repository.delete_chat(chat_id)

    @contextmanager
    def _guard_idle_chat_action(self, chat_id: ChatId) -> Iterator[None]:
        """Make an idle check and repository action atomic against new turns.

        The state lock stays held until the persistence call finishes, so an
        ``open_turn`` cannot claim the Chat between the busy check and write.
        The commit lock also protects the repository's shared Chat index.
        """

        with self._state_lock:
            if chat_id in self._active_turn_tokens:
                raise ChatBusyError(
                    "Chat cannot be changed while it is generating or "
                    f"being summarized: {chat_id}."
                )

            with self._commit_lock:
                yield

    @contextmanager
    def open_turn(
        self,
        chat_id: ChatId,
    ) -> Iterator[ActiveConversation]:
        """Guard and yield the current Chat, Project, mode, and settings.

        The guard remains held until the caller leaves the context, including
        generator close, model failure, validation failure, or persistence
        failure. Different Chat IDs may remain active at the same time.
        """

        turn_token = object()

        with self._state_lock:
            if chat_id in self._active_turn_tokens:
                raise ChatBusyError(
                    f"Chat is already generating a reply: {chat_id}."
                )
            self._active_turn_tokens[chat_id] = turn_token

        try:
            with self._commit_lock:
                chat_session = self._chat_repository.get_chat(chat_id)
            if chat_session.is_archived:
                raise ConversationUnavailableError(
                    f"Archived Chat cannot accept a new turn: {chat_id}."
                )

            project = self._load_active_project(chat_session.project_id)
            yield ActiveConversation(
                chat_session=chat_session,
                project=project,
                _turn_token=turn_token,
            )
        finally:
            with self._state_lock:
                if self._active_turn_tokens.get(chat_id) is turn_token:
                    del self._active_turn_tokens[chat_id]

    def commit_turn(
        self,
        active_conversation: ActiveConversation,
        *,
        user_message: str,
        assistant_message: str,
    ) -> ChatSession:
        """Append one complete turn if the guarded snapshot is still current."""

        self._require_active_token(active_conversation)
        cleaned_user_message = user_message.strip()
        cleaned_assistant_message = assistant_message.strip()

        if not cleaned_user_message:
            raise ValueError("User message cannot be empty.")
        if not cleaned_assistant_message:
            raise ValueError("Assistant message cannot be empty.")

        with self._commit_lock:
            current_session = self._load_unchanged_session(
                active_conversation
            )
            commit_time = self._next_timestamp(current_session.updated_at)
            user_record = create_chat_message(
                role="user",
                content=cleaned_user_message,
                created_at=commit_time,
            )
            assistant_record = create_chat_message(
                role="assistant",
                content=cleaned_assistant_message,
                created_at=commit_time,
            )
            updated_session = replace(
                current_session,
                updated_at=commit_time,
                messages=(
                    *current_session.messages,
                    user_record,
                    assistant_record,
                ),
            )
            self._chat_repository.save_chat(updated_session)
            return updated_session

    def get_retry_turn(
        self,
        active_conversation: ActiveConversation,
        *,
        user_message_id: ChatMessageId,
        assistant_message_id: ChatMessageId,
    ) -> tuple[ChatMessage, ChatMessage]:
        """Return the named persisted tail pair for guarded retry generation."""

        self._require_active_token(active_conversation)
        return self._require_retry_tail(
            active_conversation.chat_session,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )

    def commit_retry(
        self,
        active_conversation: ActiveConversation,
        *,
        user_message_id: ChatMessageId,
        assistant_message_id: ChatMessageId,
        user_message: str,
        assistant_message: str,
    ) -> ChatSession:
        """Atomically replace the persisted tail pair after a successful retry.

        Stable message identity, timestamps, and attachments are retained. A
        summary cannot survive a content rewrite because its facts may describe
        the superseded text even when its source message IDs remain unchanged.
        """

        self._require_active_token(active_conversation)
        cleaned_user_message = user_message.strip()
        cleaned_assistant_message = assistant_message.strip()
        if not cleaned_user_message:
            raise ValueError("User message cannot be empty.")
        if not cleaned_assistant_message:
            raise ValueError("Assistant message cannot be empty.")

        with self._commit_lock:
            current_session = self._load_unchanged_session(
                active_conversation
            )
            user_record, assistant_record = self._require_retry_tail(
                current_session,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
            commit_time = self._next_timestamp(current_session.updated_at)
            updated_session = replace(
                current_session,
                updated_at=commit_time,
                messages=(
                    *current_session.messages[:-2],
                    replace(user_record, content=cleaned_user_message),
                    replace(
                        assistant_record,
                        content=cleaned_assistant_message,
                    ),
                ),
                summary=None,
            )
            self._chat_repository.save_chat(updated_session)
            return updated_session

    def commit_summary(
        self,
        active_conversation: ActiveConversation,
        *,
        facts: Iterable[str],
        decisions: Iterable[str],
        action_items: Iterable[str],
        unresolved_questions: Iterable[str],
        source_message_ids: Iterable[ChatMessageId],
    ) -> ChatSession:
        """Save one structured Chat summary under the same lifecycle guard."""

        self._require_active_token(active_conversation)
        with self._commit_lock:
            current_session = self._load_unchanged_session(
                active_conversation
            )
            commit_time = self._next_timestamp(current_session.updated_at)
            summary = ChatSummary(
                facts=tuple(facts),
                decisions=tuple(decisions),
                action_items=tuple(action_items),
                unresolved_questions=tuple(unresolved_questions),
                source_message_ids=tuple(source_message_ids),
                updated_at=commit_time,
            )
            updated_session = replace(
                current_session,
                updated_at=commit_time,
                summary=summary,
            )
            self._chat_repository.save_chat(updated_session)
            return updated_session

    def _load_active_project(
        self,
        project_id: ProjectId | None,
    ) -> Project | None:
        """Load a linked Project and reject read-only archived state."""

        if project_id is None:
            return None

        project = self._project_repository.get_project(project_id)
        if project.is_archived:
            raise ConversationUnavailableError(
                "A Chat inside an archived Project cannot generate: "
                f"{project_id}."
            )
        return project

    @staticmethod
    def _require_retry_tail(
        chat_session: ChatSession,
        *,
        user_message_id: ChatMessageId,
        assistant_message_id: ChatMessageId,
    ) -> tuple[ChatMessage, ChatMessage]:
        """Require one exact adjacent user/assistant pair at the Chat tail."""

        if len(chat_session.messages) < 2:
            raise ChatRetryTargetError(
                "Chat retry requires a completed user and assistant tail turn."
            )
        user_record, assistant_record = chat_session.messages[-2:]
        if (
            user_record.role != "user"
            or assistant_record.role != "assistant"
            or user_record.message_id != user_message_id
            or assistant_record.message_id != assistant_message_id
        ):
            raise ChatRetryTargetError(
                "Chat retry must target the adjacent persisted tail turn."
            )
        return user_record, assistant_record

    def _require_active_token(
        self,
        active_conversation: ActiveConversation,
    ) -> None:
        """Reject stale or forged contexts outside their guarded lifetime."""

        if not isinstance(active_conversation, ActiveConversation):
            raise ValueError(
                "active_conversation must be ActiveConversation."
            )

        chat_id = active_conversation.chat_session.chat_id
        with self._state_lock:
            active_token = self._active_turn_tokens.get(chat_id)

        if active_token is not active_conversation._turn_token:
            raise ChatChangedDuringGenerationError(
                "The active Chat operation is no longer valid: "
                f"{chat_id}."
            )

    def _load_unchanged_session(
        self,
        active_conversation: ActiveConversation,
    ) -> ChatSession:
        """Detect a concurrent write before replacing the Chat aggregate."""

        expected_session = active_conversation.chat_session
        current_session = self._chat_repository.get_chat(
            expected_session.chat_id
        )
        if current_session != expected_session:
            raise ChatChangedDuringGenerationError(
                "Chat changed before the generated result could be saved: "
                f"{expected_session.chat_id}."
            )
        return current_session

    def _next_timestamp(self, previous: datetime) -> datetime:
        """Return a valid aware timestamp that never moves state backward."""

        current = self._clock()
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise ValueError(
                "Active conversation clock must return an aware datetime."
            )
        return max(current, previous)
