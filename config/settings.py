"""Load immutable application settings from defaults and ``.env``."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET = 2048
DEFAULT_MEMORY_RETRIEVAL_LIMIT = 5
DEFAULT_DATA_IMPORT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_MODEL_NAME = "qwen3.5:9b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"


@dataclass(frozen=True)
class AppSettings:
    """Hold the validated-at-startup configuration used by the app.

    The dataclass is frozen so services receive one stable configuration
    snapshot instead of changing environment values during a session.
    """

    base_dir: Path
    model_name: str
    log_level: str
    debug: bool
    ollama_host: str

    short_term_memory_token_budget: int = (
        DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET
    )
    memory_retrieval_limit: int = (
        DEFAULT_MEMORY_RETRIEVAL_LIMIT
    )
    data_import_max_bytes: int = DEFAULT_DATA_IMPORT_MAX_BYTES


def parse_bool(value: str) -> bool:
    """Interpret common truthy environment-variable spellings.

    Unrecognized values intentionally evaluate to ``False`` so callers get
    deterministic behavior without relying on Python's non-empty-string rule.
    """

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def parse_int(value: str, default: int) -> int:
    """Parse one integer without making a malformed ``.env`` unloadable."""

    try:
        return int(value)
    except ValueError:
        return default


# Anchor file locations to the repository, not the process working directory.
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

# Load optional local overrides before reading individual environment values.
load_dotenv(ENV_FILE)

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    DEFAULT_MODEL_NAME,
)
LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
)
DEBUG = parse_bool(
    os.getenv("DEBUG", "False")
)
OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    DEFAULT_OLLAMA_HOST,
)
SHORT_TERM_MEMORY_TOKEN_BUDGET = parse_int(
    os.getenv(
        "SHORT_TERM_MEMORY_TOKEN_BUDGET",
        str(DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET),
    ),
    DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET,
)
MEMORY_RETRIEVAL_LIMIT = parse_int(
    os.getenv(
        "MEMORY_RETRIEVAL_LIMIT",
        str(DEFAULT_MEMORY_RETRIEVAL_LIMIT),
    ),
    DEFAULT_MEMORY_RETRIEVAL_LIMIT,
)
DATA_IMPORT_MAX_BYTES = parse_int(
    os.getenv(
        "DATA_IMPORT_MAX_BYTES",
        str(DEFAULT_DATA_IMPORT_MAX_BYTES),
    ),
    DEFAULT_DATA_IMPORT_MAX_BYTES,
)

# Export one settings object for the composition root and application services.
SETTINGS = AppSettings(
    base_dir=BASE_DIR,
    model_name=MODEL_NAME,
    log_level=LOG_LEVEL,
    debug=DEBUG,
    ollama_host=OLLAMA_HOST,
    short_term_memory_token_budget=(
        SHORT_TERM_MEMORY_TOKEN_BUDGET
    ),
    memory_retrieval_limit=MEMORY_RETRIEVAL_LIMIT,
    data_import_max_bytes=DATA_IMPORT_MAX_BYTES,
)
