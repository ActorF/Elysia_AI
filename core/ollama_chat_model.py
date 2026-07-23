"""Direct HTTP adapter for Ollama."""

import logging
import requests

from typing import Any, cast

from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    JSONDecodeError as RequestsJSONDecodeError,
    RequestException,
    Timeout,
)

from .exceptions import (
    ChatModelConnectionError,
    ChatModelError,
    ChatModelNotFoundError,
    ChatModelResponseError,
)

logger = logging.getLogger(__name__)

JsonObject = dict[str, object]


class OllamaChatModel:
    """Generate chat replies through Ollama's HTTP API."""

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
        self._timeout_seconds = timeout_seconds

    def ensure_model_available(self) -> None:
        """Verify that the configured model is installed."""
        response_data = self._request_json(
            "GET",
            "/api/tags",
        )

        models_data = response_data.get("models")

        if not isinstance(models_data, list):
            raise ChatModelResponseError(
                "Ollama returned an invalid model list."
            )

        for model_data in models_data:
            if not isinstance(model_data, dict):
                continue

            model_name = model_data.get("name")
            model_identifier = model_data.get("model")

            if self._model_name in (
                model_name,
                model_identifier,
            ):
                logger.info(
                    "Verified Ollama model: %s",
                    self._model_name,
                )
                return

        raise ChatModelNotFoundError(
            f"Model '{self._model_name}' "
            "is not installed in Ollama."
        )

    def generate_reply(
        self,
        user_message: str,
    ) -> str:
        """Generate one non-streaming Ollama reply."""
        cleaned_user_message = user_message.strip()

        if not cleaned_user_message:
            raise ValueError(
                "User message cannot be empty."
            )

        payload: JsonObject = {
            "model": self._model_name,
            "messages": [
                {
                    "role": "user",
                    "content": cleaned_user_message,
                }
            ],
            "stream": False,
            "think": False,
        }

        response_data = self._request_json(
            "POST",
            "/api/chat",
            payload,
        )

        message_data = response_data.get("message")

        if not isinstance(message_data, dict):
            raise ChatModelResponseError(
                "Ollama response has no message object."
            )

        content = message_data.get("content")

        if not isinstance(content, str):
            raise ChatModelResponseError(
                "Ollama response has no text content."
            )

        cleaned_reply = content.strip()

        if not cleaned_reply:
            raise ChatModelResponseError(
                "Ollama returned an empty reply."
            )

        return cleaned_reply

    def _request_json(
        self,
        method: str,
        path: str,
        payload: JsonObject | None = None,
    ) -> JsonObject:
        """Send one request and validate its JSON response."""
        try:
            response = requests.request(
                method,
                f"{self._ollama_host}{path}",
                json=cast(Any,payload),
                timeout=self._timeout_seconds,
            )

        except (
            RequestsConnectionError,
            Timeout,
        ) as error:
            raise ChatModelConnectionError(
                "Could not connect to Ollama at "
                f"{self._ollama_host}."
            ) from error

        except RequestException as error:
            raise ChatModelError(
                "The Ollama request failed."
            ) from error

        try:
            decoded_data: object = response.json()

        except RequestsJSONDecodeError as error:
            raise ChatModelResponseError(
                "Ollama returned invalid JSON."
            ) from error

        if not isinstance(decoded_data, dict):
            raise ChatModelResponseError(
                "Ollama returned an invalid JSON structure."
            )

        response_data = cast(
            JsonObject,
            decoded_data,
        )

        if response.status_code >= 400:
            error_data = response_data.get("error")

            if isinstance(error_data, str):
                error_message = error_data
            else:
                error_message = (
                    f"HTTP {response.status_code}"
                )

            raise ChatModelResponseError(
                f"Ollama request failed: {error_message}"
            )

        return response_data