"""LangChain adapter for Ollama."""

from collections.abc import Iterator
from httpx import ConnectError, TimeoutException
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_ollama import ChatOllama
from ollama import ResponseError
from .chat_model import ChatMessage

from .exceptions import (
    ChatModelConnectionError,
    ChatModelResponseError,
)
from .ollama_chat_model import OllamaChatModel


class LangChainOllamaChatModel:
    """Generate Ollama replies through LangChain."""

    def __init__(
        self,
        model_name: str,
        ollama_host: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        """Configure LangChain generation and a direct availability checker.

        Raises:
            ValueError: If the model name, host URL, or timeout is invalid.
        """

        cleaned_model_name = model_name.strip()
        cleaned_host = ollama_host.strip().rstrip("/")

        if not cleaned_model_name:
            raise ValueError("Model name cannot be empty.")

        if not cleaned_host.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                "Ollama host must start with "
                "http:// or https://."
            )

        if timeout_seconds <= 0:
            raise ValueError(
                "Timeout must be greater than zero."
            )

        self._model_name = cleaned_model_name
        self._ollama_host = cleaned_host

        # Reuse the direct adapter's tested model-list validation.
        self._availability_checker = OllamaChatModel(
            self._model_name,
            self._ollama_host,
            timeout_seconds,
        )

        self._langchain_model = ChatOllama(
            model=self._model_name,
            base_url=self._ollama_host,
            reasoning=False,
            client_kwargs={
                "timeout": timeout_seconds,
            },
        )

    def ensure_model_available(self) -> None:
        """Verify that the configured Ollama model exists."""
        self._availability_checker.ensure_model_available()

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        """Generate one reply through LangChain."""
        if not messages:
            raise ValueError(
                "Chat messages cannot be empty."
            )

        # Translate the project's lightweight message contract to LangChain.
        langchain_messages: list[BaseMessage] = []

        for message in messages:
            role = message["role"]
            message_content = message["content"].strip()

            if not message_content:
                raise ValueError(
                    "Chat message content cannot be empty."
                )

            if role == "system":
                langchain_messages.append(
                    SystemMessage(content=message_content)
                )
            elif role == "user":
                langchain_messages.append(
                    HumanMessage(content=message_content)
                )
            elif role == "assistant":
                langchain_messages.append(
                    AIMessage(content=message_content)
                )

        try:
            response = self._langchain_model.invoke(
                langchain_messages
            )
        except (
            ConnectError,
            TimeoutException,
        ) as error:
            raise ChatModelConnectionError(
                "Could not connect to Ollama at "
                f"{self._ollama_host}."
            ) from error

        except ResponseError as error:
            raise ChatModelResponseError(
                f"Ollama request failed: {error}"
            ) from error

        content = response.content

        if not isinstance(content, str):
            raise ChatModelResponseError(
                "LangChain returned non-text content."
            )

        cleaned_reply = content.strip()

        if not cleaned_reply:
            raise ChatModelResponseError(
                "LangChain returned an empty reply."
            )

        return cleaned_reply

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        """Yield one reply through LangChain."""
        if not messages:
            raise ValueError(
                "Chat messages cannot be empty."
            )

        # Streaming uses the same role conversion as non-streaming generation.
        langchain_messages: list[BaseMessage] = []

        for message in messages:
            role = message["role"]
            message_content = message["content"].strip()

            if not message_content:
                raise ValueError(
                    "Chat message content cannot be empty."
                )

            if role == "system":
                langchain_messages.append(
                    SystemMessage(content=message_content)
                )
            elif role == "user":
                langchain_messages.append(
                    HumanMessage(content=message_content)
                )
            elif role == "assistant":
                langchain_messages.append(
                    AIMessage(content=message_content)
                )

        # Whitespace chunks may be yielded to preserve output order, but they
        # cannot make an otherwise empty model response valid.
        has_text = False

        try:
            for chunk in self._langchain_model.stream(
                langchain_messages
            ):
                content = chunk.content

                if not isinstance(content, str):
                    raise ChatModelResponseError(
                        "LangChain returned non-text content."
                    )

                if not content:
                    continue

                if content.strip():
                    has_text = True

                yield content

        except (
            ConnectError,
            TimeoutException,
        ) as error:
            raise ChatModelConnectionError(
                "Could not connect to Ollama at "
                f"{self._ollama_host}."
            ) from error

        except ResponseError as error:
            raise ChatModelResponseError(
                f"Ollama request failed: {error}"
            ) from error

        if not has_text:
            raise ChatModelResponseError(
                "LangChain returned an empty reply."
            )
