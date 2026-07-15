import json
import logging

from config import settings
from core import brain
from core.exceptions import ConfigurationError
from memory.conversation import save_json_message
from memory.json_store import (
    load_json_or_default,
    write_json,
)


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

# JSON conversation file

        conversation_file = (
            settings.BASE_DIR
            / "workspace"
            / "conversations"
            / "conversation.json"
        )

        save_json_message(
            conversation_file,
            "User",
            "Hello, Elysia!",
        )

        save_json_message(
            conversation_file,
            "Elysia",
            "Hi! It is nice to see you.",
        )

        conversation_data = load_json_or_default(
            conversation_file,
            {"messages": []},
        )

        recent_messages = conversation_data["messages"][-10:]

        print("\nRecent conversation:")

        for record in recent_messages:
            print(
                f"[{record['timestamp']}] "
                f"{record['speaker']}: "
                f"{record['message']}"
            )

        # --------------------------------
        # JSON profile file
        # --------------------------------

        profile_file = (
            settings.BASE_DIR
            / "workspace"
            / "memory"
            / "profile.json"
        )

        profile_data = {
            "user_name": "Ying",
            "assistant_name": "Elysia",
            "languages": [
                "Chinese",
                "English",
            ],
            "project": "Elysia AI",
        }

        loaded_profile = load_json_or_default(
            profile_file,
            profile_data,
        )

        launch_count = loaded_profile.get("launch_count", 0)
        loaded_profile["launch_count"] = launch_count + 1

        write_json(profile_file, loaded_profile)

        print(f"\nProfile saved to: {profile_file}")

        print("\nLoaded profile:")
        print(f"User: {loaded_profile['user_name']}")
        print(f"Assistant: {loaded_profile['assistant_name']}")
        print(f"Languages: {loaded_profile['languages']}")
        print(f"Project: {loaded_profile['project']}")
        print(f"Launch count: {loaded_profile['launch_count']}")

    except ConfigurationError as error:
        logging.error("Invalid configuration: %s", error)
        print(f"Configuration error: {error}")

    except FileNotFoundError as error:
        logging.error("Required file was not found: %s", error)
        print(f"File error: {error}")

    except json.JSONDecodeError as error:
        logging.error("Invalid JSON data: %s", error)
        print(f"JSON error: {error}")
        
    except ConnectionError as error:
        logging.error(
            "Could not connect to the AI service: %s",
            error,
        )
        print("Elysia could not connect to the AI service.")

    except Exception as error:
        logging.exception(
            "The program encountered an unexpected error."
        )
        print(f"Elysia encountered an unexpected error: {error}")

    else:
        logging.info("Program completed successfully")
        print("\nElysia started successfully.")

    finally:
        logging.info("Program ended")
        print("Program ended safely.")


if __name__ == "__main__":
    main()