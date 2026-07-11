import logging

from config import settings
from core import brain
from core.exceptions import ConfigurationError
from memory.conversation import save_message
from memory.file_manager import read_lines

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
        raise ConfigurationError("MODEL_NAME cannot be empty")

    if not settings.OLLAMA_HOST.startswith(("http://", "https://")):
        raise ConfigurationError(
            "OLLAMA_HOST must begin with http:// or https://"
        )


def main() -> None:
    logging.info("Program started")

    try:
        validate_settings()

        print(f"Using model: {settings.MODEL_NAME}")
        brain.hello()

        conversation_file = (
            settings.BASE_DIR
            / "workspace"
            / "conversations"
            / "conversation.txt"
        )

        save_message(
            conversation_file,
            "User",
            "Hello, Elysia!",
        )

        save_message(
            conversation_file,
            "Elysia",
            "Hi! It is nice to see you.",
        )

        conversation_lines = read_lines(conversation_file)
        recent_lines = conversation_lines[-10:]

        print("\nRecent conversation:")

        for line in recent_lines:
            print(line)

    except ConfigurationError as error:
        logging.error("Invalid configuration: %s", error)
        print(f"Configuration error: {error}")

    except FileNotFoundError as error:
        logging.error("Required file was not found: %s", error)
        print(f"File error: {error}")

    except ConnectionError as error:
        logging.error("Could not connect to the AI service: %s", error)
        print("Elysia could not connect to the AI service.")

    except Exception as error:
        logging.exception(
            "The program encountered an unexpected error."
        )
        print(f"Elysia encountered an unexpected error: {error}")

    else:
        logging.info("Program completed successfully")
        print("Elysia started successfully.")

    finally:
        logging.info("Program ended")
        print("Program ended safely.")


if __name__ == "__main__":
    main()