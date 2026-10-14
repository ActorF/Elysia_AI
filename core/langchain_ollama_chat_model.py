"""Generate and stream replies with LangChain over proxy-free local Ollama HTTP."""

from collections.abc import Generator, Iterator
from httpx import Auth, ConnectError, Request, Response, TimeoutException
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
from .local_ollama import (
    LOCAL_OLLAMA_MODEL_ERROR,
    is_cloud_ollama_model_name,
    normalize_local_ollama_origin,
)


class _LocalModelAuth(Auth):
    """Remove SDK-inherited credentials from both sync and async local requests."""

    def auth_flow(self, request: Request) -> Generator[Request, Response, None]:
        """Strip ambient Ollama credentials immediately before HTTPX sends bytes."""
        # Ollama's SDK inserts OLLAMA_API_KEY even for a loopback host. An auth
        # flow runs after its headers are assembled, without process-global
        # environment changes that could race unrelated clients.
        request.headers.pop("Authorization", None)
        yield request


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
        cleaned_host = normalize_local_ollama_origin(ollama_host)

        if not cleaned_model_name:
            raise ValueError("Model name cannot be empty.")
        if is_cloud_ollama_model_name(cleaned_model_name):
            raise ValueError(LOCAL_OLLAMA_MODEL_ERROR)

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
                # Ollama's SDK defaults to following redirects. Both sync and
                # async clients must preserve the same local-only boundary as
                # discovery instead of inheriting proxy or redirect policy.
                "trust_env": False,
                "follow_redirects": False,
                "auth": _LocalModelAuth(),
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
