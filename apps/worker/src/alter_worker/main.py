import argparse
import logging
import signal
import threading

from alter_domain.conversation import run_one
from alter_domain.db import create_db_engine
from alter_domain.llm import BaselineModerator, FakeLLM
from alter_domain.runtime import run_mock_process
from alter_domain.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Alter conversation Worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    args = parser.parse_args()
    settings = Settings()
    if settings.alter_mode == "mock":
        run_mock_process("worker", once=args.once)
        return
    if settings.llm_backend != "fake":
        raise RuntimeError("LLM provider is disabled pending evaluation")
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()

    def stop(signum: int, frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    engine = create_db_engine(settings)
    try:
        while not stopped.is_set():
            processed = run_one(
                engine,
                FakeLLM(),
                BaselineModerator(),
                stopped=stopped,
                global_limit=settings.max_daily_cost_usd,
            )
            if args.once:
                return
            if not processed:
                stopped.wait(2)
    finally:
        engine.dispose()
