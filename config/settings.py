import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.5:9b")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEBUG = os.getenv("DEBUG", "False")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")