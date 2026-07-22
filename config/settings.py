import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

@dataclass(frozen=True)
class AppSettings:
    base_dir: Path
    model_name: str
    log_level: str
    debug: bool
    ollama_host: str

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

SETTINGS = AppSettings(
    base_dir=BASE_DIR,
    model_name=MODEL_NAME,
    log_level=LOG_LEVEL,
    debug=DEBUG,
    ollama_host=OLLAMA_HOST,
)