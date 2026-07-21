import logging

from memory.manager import Memory


logger = logging.getLogger(__name__)


class Brain:
    def __init__(
        self,
        model_name: str,
        memory: Memory,
    ) -> None:
        cleaned_model_name = model_name.strip()

        if not cleaned_model_name:
            raise ValueError("Model name cannot be empty.")

        self._model_name = cleaned_model_name
        self._memory = memory

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

    def start_session(self) -> dict:
        profile = self._memory.record_launch()

        self.hello()

        logger.info(
            "Session started. Launch count: %s",
            profile["launch_count"],
        )

        return profile

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
    ) -> list[dict]:
        return self._memory.get_recent_messages(limit)