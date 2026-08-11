import logging
from collections.abc import Iterator

from memory import (
    ConversationMessage,
    MemoryCandidate,
    MemoryExtractor,
    Memory,
    Profile,
    ShortTermMemory,
    LongTermMemoryRecord,

)
from .chat_model import ChatMessage, ChatModel
from .prompts import build_elysia_system_prompt

logger = logging.getLogger(__name__)


class Brain:
    def __init__(
        self,
        model_name: str,
        memory: Memory,
        chat_model: ChatModel | None = None,
        short_term_memory: ShortTermMemory | None = None,
        memory_extractor: MemoryExtractor | None = None,
    ) -> None:
        cleaned_model_name = model_name.strip()

        if not cleaned_model_name:
            raise ValueError("Model name cannot be empty.")

        self._model_name = cleaned_model_name
        self._memory = memory
        self._chat_model = chat_model
        self._short_term_memory = short_term_memory
        self._memory_extractor = memory_extractor

        logger.info(
            "Brain initialized with model: %s",
            self._model_name,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    def hello(self) -> None:
        print(
            f"Hello from Brain! "
            f"Model: {self.model_name}"
        )

    def start_session(self) -> Profile:
        profile = self._memory.record_launch()

        self.hello()

        logger.info(
            "Session started. Launch count: %s",
            profile["launch_count"],
        )

        return profile

    def chat(self, user_message: str) -> str:
        """Generate and save one complete chat turn."""
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
        """Yield and save one complete chat turn."""
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
        self._memory.save_message(
            speaker,
            message,
        )

    def _to_chat_message(
        self,
        conversation_message: ConversationMessage,
        profile: Profile,
    ) -> ChatMessage | None:
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

    def _build_chat_messages(
        self,
        profile: Profile,
        current_user_message: str,
        limit: int = 10,
    ) -> list[ChatMessage]:
        system_prompt = build_elysia_system_prompt(profile)

        messages: list[ChatMessage] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        messages.extend(
            self._build_recent_context(
                profile,
                limit,
            )
        )

        messages.append(
            {
                "role": "user",
                "content": current_user_message,
            }
        )

        return messages

    def recall_long_term_memories(
        self,
    ) -> list[LongTermMemoryRecord]:
        return self._memory.get_long_term_memories()

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
    ) -> LongTermMemoryRecord:
        """Save one candidate after the user confirms it."""
        return self._memory.save_long_term_memory(
            candidate["key"],
            candidate["value"],
            candidate["source_type"],
            candidate["source_text"],
        )

    def recall_recent_messages(
        self,
        limit: int = 10,
    ) -> list[ConversationMessage]:
        return self._memory.get_recent_messages(limit)