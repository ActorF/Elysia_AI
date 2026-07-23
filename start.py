import json
import logging

from config.settings import SETTINGS
from core import (
    Brain,
    ChatModelConnectionError,
    ChatModelError,
    ConfigurationError,
    LangChainOllamaChatModel,
)
from memory import Memory
from ui import run_console_session

LOG_DIR = SETTINGS.base_dir / "logs"
LOG_FILE = LOG_DIR / "app.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def validate_settings() -> None:
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

def create_brain() -> Brain:
    """Create and connect Elysia's main objects."""
    elysia_memory = Memory(SETTINGS.base_dir)

    chat_model = LangChainOllamaChatModel(
        SETTINGS.model_name,
        SETTINGS.ollama_host,
    )

    chat_model.ensure_model_available()

    return Brain(
        SETTINGS.model_name,
        elysia_memory,
        chat_model,
    )

def main() -> None:
    validate_settings()

    print(f"Using model: {SETTINGS.model_name}")

    # Create Elysia's connected objects.
    elysia_brain = create_brain()

    run_console_session(elysia_brain)


if __name__ == "__main__":
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