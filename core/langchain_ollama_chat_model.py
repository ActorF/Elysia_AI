"""LangChain adapter for Ollama."""

from httpx import ConnectError, TimeoutException
from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama
from ollama import ResponseError

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
        user_message: str,
    ) -> str:
        """Generate one non-streaming reply through LangChain."""
        cleaned_user_message = user_message.strip()

        if not cleaned_user_message:
            raise ValueError(
                "User message cannot be empty."
            )

        try:
            response = self._langchain_model.invoke(
                [
                    HumanMessage(
                        content=cleaned_user_message
                    )
                ]
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