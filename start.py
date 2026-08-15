"""Validate configuration, compose services, and start the console app."""

import json
import logging

from config.settings import SETTINGS
from core import (
    Brain,
    ChatModelConnectionError,
    ChatModelError,
    ConfigurationError,
    LangChainOllamaChatModel,
    ModelConversationSummarizer,
    ModelMemoryExtractor,
)
from memory import (
    Memory,
    MemoryRetriever,
    ShortTermMemory,
)
from ui import run_console_session

LOG_DIR = SETTINGS.base_dir / "logs"
LOG_FILE = LOG_DIR / "app.log"

# Logging is configured at the process boundary before services are created.
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def validate_settings() -> None:
    """Validate cross-service configuration before any model work begins.

    Raises:
        ConfigurationError: If a required value cannot safely configure its
            downstream service.
    """

    if not SETTINGS.model_name.strip():
        raise ConfigurationError(
            "MODEL_NAME cannot be empty."
        )

    ollama_host = SETTINGS.ollama_host.strip()

    if not ollama_host.startswith(
        ("http://", "https://")
    ):
        raise ConfigurationError(
            "OLLAMA_HOST must start with "
            "http:// or https://."
        )

    if (
        SETTINGS.short_term_memory_token_budget
        <= 0
    ):
        raise ConfigurationError(
            "SHORT_TERM_MEMORY_TOKEN_BUDGET "
            "must be greater than zero."
        )

    if SETTINGS.memory_retrieval_limit <= 0:
        raise ConfigurationError(
            "MEMORY_RETRIEVAL_LIMIT "
            "must be greater than zero."
        )


def create_brain() -> Brain:
    """Construct and connect the application's model and memory services.

    This function is the composition root: concrete infrastructure is created
    here and injected into ``Brain``, leaving core orchestration testable.
    """

    elysia_memory = Memory(
        SETTINGS.base_dir
    )

    short_term_memory = ShortTermMemory(
        SETTINGS.short_term_memory_token_budget,
    )

    chat_model = LangChainOllamaChatModel(
        SETTINGS.model_name,
        SETTINGS.ollama_host,
    )

    # Fail during startup instead of after the user sends the first message.
    chat_model.ensure_model_available()

    memory_extractor = ModelMemoryExtractor(
        chat_model
    )

    conversation_summarizer = (
        ModelConversationSummarizer(
            chat_model
        )
    )

    memory_retriever = MemoryRetriever(
        SETTINGS.memory_retrieval_limit,
    )

    return Brain(
        SETTINGS.model_name,
        elysia_memory,
        chat_model,
        short_term_memory=(
            short_term_memory
        ),
        memory_extractor=(
            memory_extractor
        ),
        conversation_summarizer=(
            conversation_summarizer
        ),
        memory_retriever=(
            memory_retriever
        ),
    )


def main() -> None:
    """Run one validated console application session."""

    validate_settings()

    print(f"Using model: {SETTINGS.model_name}")

    # Create Elysia's connected objects only after settings have passed checks.
    elysia_brain = create_brain()

    run_console_session(elysia_brain)


if __name__ == "__main__":
    # Translate expected boundary errors into concise user-facing messages,
    # while preserving detailed diagnostics in the application log.
    logging.info("Program started")

    try:
        main()

    except ConfigurationError as error:
        logging.error(
            "Configuration error: %s",
            error,
        )
        print(f"Configuration error: {error}")

    except FileNotFoundError as error:
        logging.error(
            "File not found: %s",
            error,
        )
        print(f"File not found: {error}")

    except json.JSONDecodeError as error:
        logging.error(
            "Invalid JSON data: %s",
            error,
        )
        print(
            "A JSON file contains invalid data. "
            "Check logs/app.log for details."
        )

    except ChatModelConnectionError as error:
        logging.error(
            "Chat model connection error: %s",
            error,
        )
        print(
            "Could not connect to Ollama. "
            "Make sure Ollama is running."
        )

    except ChatModelError as error:
        logging.error(
            "Chat model error: %s",
            error,
        )
        print(f"Chat model error: {error}")

    except Exception:
        logging.exception(
            "The program encountered "
            "an unexpected error."
        )
        print(
            "An unexpected error occurred. "
            "Check logs/app.log for details."
        )

    else:
        logging.info(
            "Elysia started successfully."
        )
        print("\nElysia started successfully.")

    finally:
        logging.info("Program ended")
        print("Program ended safely.")
