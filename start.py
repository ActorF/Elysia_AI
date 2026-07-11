import logging

from config import settings
from core import brain
from core.exceptions import ConfigurationError


logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
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

    except ConfigurationError as error:
        logging.error("Invalid configuration: %s", error)
        print(f"Configuration error: {error}")

    except ConnectionError as error:
        logging.error("Could not connect to the AI service: %s", error)
        print("Elysia could not connect to the AI service.")

    except Exception as error:
        logging.exception("The program encountered an unexpected error.")
        print(f"Elysia encountered an unexpected error: {error}")

    else:
        logging.info("Program completed successfully")
        print("Elysia started successfully.")

    finally:
        logging.info("Program ended")
        print("Program ended safely.")


if __name__ == "__main__":
    main()