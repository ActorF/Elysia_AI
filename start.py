from core import brain
import logging
from config import settings

print(settings.MODEL_NAME)
logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logging.info("Program Started")

def main() -> None:
    brain.hello()


if __name__ == "__main__":
    main()