import logging

logger = logging.getLogger(__name__)


def hello() -> None:
    logger.info("Brain module loaded")
    print("Hello from Brain!")