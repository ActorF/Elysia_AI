import json
import logging

from config import settings
from core.brain import Brain
from core.exceptions import ConfigurationError
from memory.manager import Memory


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


def main() -> None:
    validate_settings()

    print(f"Using model: {settings.MODEL_NAME}")

    # Create Elysia's objects.
    elysia_memory = Memory(settings.BASE_DIR)

    elysia_brain = Brain(
        settings.MODEL_NAME,
        elysia_memory,
    )

    # Start the session and update launch_count.
    loaded_profile = elysia_brain.start_session()

    # Save sample conversation messages.
    elysia_brain.remember_message(
        "User",
        "Hello, Elysia!",
    )

    elysia_brain.remember_message(
        "Elysia",
        "Hi! It is nice to see you.",
    )

    # Read and display recent messages.
    recent_messages = (
        elysia_brain.recall_recent_messages(10)
    )

    print("\nRecent conversation:")

    for message_data in recent_messages:
        timestamp = message_data["timestamp"]
        speaker = message_data["speaker"]
        message = message_data["message"]

        print(
            f"[{timestamp}] "
            f"{speaker}: {message}"
        )

    # Display the user profile.
    print("\nProfile:")
    print(f"User: {loaded_profile['user_name']}")
    print(
        f"Assistant: "
        f"{loaded_profile['assistant_name']}"
    )
    print(
        f"Languages: "
        f"{loaded_profile['languages']}"
    )
    print(f"Project: {loaded_profile['project']}")
    print(
        f"Launch count: "
        f"{loaded_profile['launch_count']}"
    )


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