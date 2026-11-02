import logging
import signal
from threading import Event

from alter_domain.settings import Settings


def run_mock_process(name: str, *, once: bool = False) -> None:
    settings = Settings()
    settings.require_mock()
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(name)
    logger.info("%s: mock cycle completed (no DB writes or external requests)", name)
    if once:
        return
    stopped = Event()

    def stop(signum: int, frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("%s: waiting; SIGINT/SIGTERM stops the process", name)
    while not stopped.wait(15):
        logger.info("%s: mock heartbeat", name)
