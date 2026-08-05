import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET = 2048

@dataclass(frozen=True)
class AppSettings:
    base_dir: Path
    model_name: str
    log_level: str
    debug: bool
    ollama_host: str

    short_term_memory_token_budget: int = (
        DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET
    )

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.5:9b")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEBUG = parse_bool(
    os.getenv("DEBUG", "False")
)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
SHORT_TERM_MEMORY_TOKEN_BUDGET = int(
    os.getenv(
        "SHORT_TERM_MEMORY_TOKEN_BUDGET",
        str(DEFAULT_SHORT_TERM_MEMORY_TOKEN_BUDGET),
    )
)

SETTINGS = AppSettings(
    base_dir=BASE_DIR,
    model_name=MODEL_NAME,
    log_level=LOG_LEVEL,
    debug=DEBUG,
    ollama_host=OLLAMA_HOST,
    short_term_memory_token_budget=SHORT_TERM_MEMORY_TOKEN_BUDGET,
)
