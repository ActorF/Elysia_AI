"""Validate configuration, compose services, and start the console app."""

import json
import logging

from chats import (
    JsonChatRepository,
    LegacyConversationMigrator,
    LegacyMigrationError,
)
from config.desktop_settings import (
    DesktopSettingsValidationError,
    validate_model_name,
    validate_ollama_host,
)
from config.settings import AppSettings, SETTINGS
from core import (
    ActiveConversationService,
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
from projects import JsonProjectRepository, ProjectChatService
from recovery import DataPortabilityError, DataPortabilityService
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


def validate_settings(settings: AppSettings | None = None) -> None:
    """Validate cross-service configuration before any model work begins.

    Raises:
        ConfigurationError: If a required value cannot safely configure its
            downstream service.
    """

    runtime_settings = SETTINGS if settings is None else settings

    if not runtime_settings.model_name.strip():
        raise ConfigurationError("MODEL_NAME cannot be empty.")
    if not runtime_settings.ollama_host.strip().startswith(
        ("http://", "https://")
    ):
        raise ConfigurationError(
            "OLLAMA_HOST must start with http:// or https://."
        )
    try:
        validate_model_name(runtime_settings.model_name)
        validate_ollama_host(runtime_settings.ollama_host)
    except DesktopSettingsValidationError as error:
        raise ConfigurationError(str(error)) from error

    if (
        runtime_settings.short_term_memory_token_budget
        <= 0
    ):
        raise ConfigurationError(
            "SHORT_TERM_MEMORY_TOKEN_BUDGET "
            "must be greater than zero."
        )

    if runtime_settings.memory_retrieval_limit <= 0:
        raise ConfigurationError(
            "MEMORY_RETRIEVAL_LIMIT "
            "must be greater than zero."
        )

    if runtime_settings.data_import_max_bytes <= 0:
        raise ConfigurationError(
            "DATA_IMPORT_MAX_BYTES must be greater than zero."
        )


def create_data_portability_service(
    settings: AppSettings | None = None,
) -> DataPortabilityService:
    """Compose the Stage 5 import, export, and recovery boundary."""

    runtime_settings = SETTINGS if settings is None else settings
    return DataPortabilityService(
        base_dir=runtime_settings.base_dir,
        chat_repository=JsonChatRepository(
            runtime_settings.base_dir / "workspace" / "chats"
        ),
        project_repository=JsonProjectRepository(
            runtime_settings.base_dir / "workspace" / "projects"
        ),
        max_import_bytes=runtime_settings.data_import_max_bytes,
    )


def create_brain(settings: AppSettings | None = None) -> Brain:
    """Construct and connect the application's model and memory services.

    This function is the composition root: concrete infrastructure is created
    here and injected into ``Brain``, leaving core orchestration testable.
    """

    runtime_settings = SETTINGS if settings is None else settings

    elysia_memory = Memory(
        runtime_settings.base_dir
    )

    short_term_memory = ShortTermMemory(
        runtime_settings.short_term_memory_token_budget,
    )

    chat_model = LangChainOllamaChatModel(
        runtime_settings.model_name,
        runtime_settings.ollama_host,
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
        runtime_settings.memory_retrieval_limit,
    )

    # Chats and Projects live in ignored runtime storage. Their repository
    # paths remain infrastructure details owned by the composition root.
    chat_repository = JsonChatRepository(
        runtime_settings.base_dir / "workspace" / "chats"
    )
    LegacyConversationMigrator(
        base_dir=runtime_settings.base_dir,
        chat_repository=chat_repository,
        model_name=runtime_settings.model_name,
    ).migrate()
    project_repository = JsonProjectRepository(
        runtime_settings.base_dir / "workspace" / "projects"
    )
    active_conversation_service = ActiveConversationService(
        chat_repository,
        project_repository,
    )
    project_service = ProjectChatService(
        project_repository,
        chat_repository,
        is_chat_busy=active_conversation_service.is_chat_busy,
    )

    return Brain(
        runtime_settings.model_name,
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
        active_conversation_service=(
            active_conversation_service
        ),
        project_service=project_service,
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

    except LegacyMigrationError as error:
        logging.error(
            "Legacy migration error: %s",
            error,
        )
        print(f"Legacy data migration error: {error}")

    except DataPortabilityError as error:
        logging.error(
            "Data portability error: %s",
            error,
        )
        print(f"Data import/export error: {error}")

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
