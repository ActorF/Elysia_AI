import logging

from memory import ConversationMessage, Memory, Profile

from .chat_model import ChatModel

logger = logging.getLogger(__name__)


class Brain:
    def __init__(
        self,
        model_name: str,
        memory: Memory,
        chat_model: ChatModel | None = None,
    ) -> None:
        cleaned_model_name = model_name.strip()

        if not cleaned_model_name:
            raise ValueError("Model name cannot be empty.")

        self._model_name = cleaned_model_name
        self._memory = memory
        self._chat_model = chat_model

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

        reply = self._chat_model.generate_reply(
            cleaned_user_message
        ).strip()

        if not reply:
            raise ValueError(
                "Model reply cannot be empty."
            )

        profile = self._memory.load_profile()

        self.remember_message(
            profile["user_name"],
            cleaned_user_message,
        )
        self.remember_message(
            profile["assistant_name"],
            reply,
        )

        logger.info("Chat turn completed.")

        return reply

    def remember_message(
        self,
        speaker: str,
        message: str,
    ) -> None:
        self._memory.save_message(
            speaker,
            message,
        )

    def recall_recent_messages(
        self,
        limit: int = 10,
    ) -> list[ConversationMessage]:
        return self._memory.get_recent_messages(limit)