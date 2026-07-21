import json
import logging

from config import settings
from core import Brain, ConfigurationError
from memory import Memory
from ui import run_console_session

LOG_DIR = settings.BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def validate_settings() -> None:
    if not settings.MODEL_NAME.strip():
        raise ConfigurationError(
            "MODEL_NAME cannot be empty."
        )

    ollama_host = settings.OLLAMA_HOST.strip()

    if not ollama_host.startswith(
        ("http://", "https://")
    ):
        raise ConfigurationError(
            "OLLAMA_HOST must start with "
            "http:// or https://."
        )

def create_brain() -> Brain:
    """Create and connect Elysia's main objects."""
    elysia_memory = Memory(settings.BASE_DIR)

    return Brain(
        settings.MODEL_NAME,
        elysia_memory,
    )

def main() -> None:
    validate_settings()

    print(f"Using model: {settings.MODEL_NAME}")

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

    except ConnectionError as error:
        logging.error(
            "Connection error: %s",
            error,
        )
        print(
            "Could not connect to the required service."
        )

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