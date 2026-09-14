"""Load immutable application settings from defaults and ``.env``."""

import os
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Final, Literal, TypeAlias, cast

from dotenv import load_dotenv


DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET = 2048
DEFAULT_MEMORY_RETRIEVAL_LIMIT = 5
DEFAULT_DATA_IMPORT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_MODEL_NAME = "qwen3.5:9b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_GPT_SOVITS_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_GPT_SOVITS_PROBE_TIMEOUT_SECONDS = 1.0
DEFAULT_GPT_SOVITS_DETERMINISTIC_SEED = 42

TranscriptionModel: TypeAlias = Literal[
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "turbo",
]
TranscriptionDevice: TypeAlias = Literal["auto", "cuda", "cpu"]
TranscriptionLanguage: TypeAlias = Literal["auto", "zh", "en"]

DEFAULT_TRANSCRIPTION_MODEL: Final[TranscriptionModel] = "small"
DEFAULT_TRANSCRIPTION_DEVICE: Final[TranscriptionDevice] = "auto"
DEFAULT_TRANSCRIPTION_LANGUAGE: Final[TranscriptionLanguage] = "auto"

TRANSCRIPTION_MODELS: Final = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "turbo",
)
TRANSCRIPTION_DEVICES: Final = ("auto", "cuda", "cpu")
TRANSCRIPTION_LANGUAGES: Final = ("auto", "zh", "en")


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
    transcription_model: TranscriptionModel = DEFAULT_TRANSCRIPTION_MODEL
    transcription_device: TranscriptionDevice = DEFAULT_TRANSCRIPTION_DEVICE
    transcription_language: TranscriptionLanguage = (
        DEFAULT_TRANSCRIPTION_LANGUAGE
    )
    gpt_sovits_allow_local_evaluation: bool = False
    gpt_sovits_request_timeout_seconds: float = (
        DEFAULT_GPT_SOVITS_REQUEST_TIMEOUT_SECONDS
    )
    gpt_sovits_probe_timeout_seconds: float = (
        DEFAULT_GPT_SOVITS_PROBE_TIMEOUT_SECONDS
    )
    gpt_sovits_deterministic_seed: int = (
        DEFAULT_GPT_SOVITS_DETERMINISTIC_SEED
    )


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


def parse_float(value: str, default: float) -> float:
    """Parse one finite float without making a malformed ``.env`` unloadable."""

    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def parse_choice(value: str, allowed: tuple[str, ...], default: str) -> str:
    """Return a normalized allowlisted environment choice or its default.

    Environment configuration is untrusted local input. Falling back instead
    of forwarding an arbitrary model alias is especially important for local
    transcription because Faster-Whisper aliases may otherwise trigger an
    implicit network download.
    """

    normalized = value.strip().lower()
    return normalized if normalized in allowed else default


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
TRANSCRIPTION_MODEL = cast(
    TranscriptionModel,
    parse_choice(
        os.getenv("TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL),
        TRANSCRIPTION_MODELS,
        DEFAULT_TRANSCRIPTION_MODEL,
    ),
)
TRANSCRIPTION_DEVICE = cast(
    TranscriptionDevice,
    parse_choice(
        os.getenv("TRANSCRIPTION_DEVICE", DEFAULT_TRANSCRIPTION_DEVICE),
        TRANSCRIPTION_DEVICES,
        DEFAULT_TRANSCRIPTION_DEVICE,
    ),
)
TRANSCRIPTION_LANGUAGE = cast(
    TranscriptionLanguage,
    parse_choice(
        os.getenv("TRANSCRIPTION_LANGUAGE", DEFAULT_TRANSCRIPTION_LANGUAGE),
        TRANSCRIPTION_LANGUAGES,
        DEFAULT_TRANSCRIPTION_LANGUAGE,
    ),
)
GPT_SOVITS_ALLOW_LOCAL_EVALUATION = parse_bool(
    os.getenv("GPT_SOVITS_ALLOW_LOCAL_EVALUATION", "False")
)
GPT_SOVITS_REQUEST_TIMEOUT_SECONDS = parse_float(
    os.getenv(
        "GPT_SOVITS_REQUEST_TIMEOUT_SECONDS",
        str(DEFAULT_GPT_SOVITS_REQUEST_TIMEOUT_SECONDS),
    ),
    DEFAULT_GPT_SOVITS_REQUEST_TIMEOUT_SECONDS,
)
if not 0.1 <= GPT_SOVITS_REQUEST_TIMEOUT_SECONDS <= 300.0:
    GPT_SOVITS_REQUEST_TIMEOUT_SECONDS = (
        DEFAULT_GPT_SOVITS_REQUEST_TIMEOUT_SECONDS
    )
GPT_SOVITS_PROBE_TIMEOUT_SECONDS = parse_float(
    os.getenv(
        "GPT_SOVITS_PROBE_TIMEOUT_SECONDS",
        str(DEFAULT_GPT_SOVITS_PROBE_TIMEOUT_SECONDS),
    ),
    DEFAULT_GPT_SOVITS_PROBE_TIMEOUT_SECONDS,
)
if not 0.1 <= GPT_SOVITS_PROBE_TIMEOUT_SECONDS <= 10.0:
    GPT_SOVITS_PROBE_TIMEOUT_SECONDS = DEFAULT_GPT_SOVITS_PROBE_TIMEOUT_SECONDS
GPT_SOVITS_DETERMINISTIC_SEED = parse_int(
    os.getenv(
        "GPT_SOVITS_DETERMINISTIC_SEED",
        str(DEFAULT_GPT_SOVITS_DETERMINISTIC_SEED),
    ),
    DEFAULT_GPT_SOVITS_DETERMINISTIC_SEED,
)
if not 0 <= GPT_SOVITS_DETERMINISTIC_SEED <= 2_147_483_647:
    GPT_SOVITS_DETERMINISTIC_SEED = DEFAULT_GPT_SOVITS_DETERMINISTIC_SEED

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
    transcription_model=TRANSCRIPTION_MODEL,
    transcription_device=TRANSCRIPTION_DEVICE,
    transcription_language=TRANSCRIPTION_LANGUAGE,
    gpt_sovits_allow_local_evaluation=GPT_SOVITS_ALLOW_LOCAL_EVALUATION,
    gpt_sovits_request_timeout_seconds=GPT_SOVITS_REQUEST_TIMEOUT_SECONDS,
    gpt_sovits_probe_timeout_seconds=GPT_SOVITS_PROBE_TIMEOUT_SECONDS,
    gpt_sovits_deterministic_seed=GPT_SOVITS_DETERMINISTIC_SEED,
)
