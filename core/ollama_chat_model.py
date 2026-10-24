"""Generate proxy-free local Ollama replies under desktop GPU admission.

Desktop inference requests unload idle weights and retain their global grant
through the complete HTTP response. Model-list discovery remains metadata-only.
"""

import logging
import requests
from collections.abc import Iterator
from contextlib import nullcontext

from typing import Any, cast
from gpu_admission import GpuAdmissionError, gpu_lease, using_desktop_gpu
from .chat_model import ChatMessage, normalize_chat_messages
from .local_ollama import (
    LOCAL_OLLAMA_MODEL_ERROR,
    is_cloud_ollama_model_name,
    model_name_with_default_tag,
    normalize_local_ollama_origin,
)

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


def _validate_ollama_completion(metadata: object) -> None:
    """Require an actual completed Ollama response and reject truncated output.

    Both adapters consume Ollama's terminal ``done`` marker. Older servers may
    omit ``done_reason``, so ``done=True`` alone remains compatible; an explicit
    length limit, load-only result, or unknown terminal reason is not a finished
    answer. Diagnostics retain only reviewed reasons and numeric token counts,
    never model content, prompt text, or arbitrary provider metadata.
    """

    details = metadata if isinstance(metadata, dict) else {}
    reason = details.get("done_reason")
    safe_reason = reason if reason in ("stop", "length", "load", "unload") else "unspecified" if reason is None else "unknown"
    counts = [value if type(value) is int and 0 <= value <= 1_000_000_000 else None
              for value in (details.get("prompt_eval_count"), details.get("eval_count"))]
    completed = details.get("done") is True
    logger.info("Ollama completion: done=%s, reason=%s, prompt_tokens=%s, output_tokens=%s.",
                completed, safe_reason, counts[0], counts[1])
    if reason == "length":
        raise ChatModelResponseError("Ollama reply reached its output limit before completion. Please retry with a shorter request.")
    if not completed:
        raise ChatModelResponseError("Ollama reply ended without a complete response. Please retry.")
    if reason is not None and reason != "stop":
        raise ChatModelResponseError("Ollama did not finish generating the reply. Please retry.")


class OllamaChatModel:
    """Generate chat replies through Ollama's HTTP API."""

    def __init__(
        self,
        model_name: str,
        ollama_host: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        """Configure direct Ollama requests after validating connection data.

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

        requested_name = model_name_with_default_tag(self._model_name)
        # Ollama versions may expose the identifier under either key.
        for model_data in models_data:
            if not isinstance(model_data, dict):
                continue

            model_name = model_data.get("name")
            model_identifier = model_data.get("model")

            if any(
                isinstance(candidate, str)
                and model_name_with_default_tag(candidate) == requested_name
                for candidate in (model_name, model_identifier)
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
        messages: list[ChatMessage],
        *,
        thinking: bool = False,
    ) -> str:
        """Generate one non-streaming Ollama reply."""
        if type(thinking) is not bool:
            raise ValueError("Thinking must be a boolean.")
        cleaned_messages = normalize_chat_messages(messages)

        payload: JsonObject = {
            "model": self._model_name,
            "messages": cleaned_messages,
            "stream": False,
            "think": thinking,
        }
        desktop_gpu = using_desktop_gpu()
        if desktop_gpu:
            # Main quiesces the Ollama daemon before every subsequent heavy
            # grant. Requesting zero idle residency also avoids retaining
            # weights after hidden memory extraction or Work inference calls.
            payload["keep_alive"] = 0
        try:
            with gpu_lease("ollama") if desktop_gpu else nullcontext():
                response_data = self._request_json("POST", "/api/chat", payload)
        except GpuAdmissionError:
            raise ChatModelError("Local GPU execution is unavailable.") from None

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

        _validate_ollama_completion(response_data)
        return cleaned_reply

    def stream_work_reply(self, messages: list[ChatMessage], *, thinking: bool) -> Iterator[str]:
        """Expose the direct adapter's complete answer with request-local thinking.

        The direct transport does not stream tokens, but yields only content;
        Ollama's separate thinking field is never persisted or spoken.
        """
        yield self.generate_reply(messages, thinking=thinking)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: JsonObject | None = None,
    ) -> JsonObject:
        """Send one request, translate transport errors, and require JSON.

        Project exceptions keep callers independent from ``requests`` and give
        the console a small, stable set of errors to report.
        """

        try:
            # Chat remains local even with machine-wide proxy variables or a
            # local service that attempts to redirect private prompt content.
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(
                    method,
                    f"{self._ollama_host}{path}",
                    json=cast(Any, payload),
                    timeout=self._timeout_seconds,
                    allow_redirects=False,
                )

        # Connection and timeout failures share the same actionable UI message.
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

        if 300 <= response.status_code < 400:
            raise ChatModelResponseError("Local Ollama redirects are disabled.")

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

        # Decode Ollama's structured error when available before raising.
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
