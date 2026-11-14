import argparse
import logging
import signal
import threading

import httpx
from alter_domain.conversation import run_one
from alter_domain.db import create_db_engine
from alter_domain.memory import enrich_pending
from alter_domain.runtime import run_mock_process
from alter_domain.settings import Settings
from alter_domain.sonnet import SonnetClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Alter conversation Worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    args = parser.parse_args()
    settings = Settings()
    if settings.alter_mode == "mock":
        run_mock_process("worker", once=args.once)
        return
    if not settings.llm_enabled:
        logging.info("Worker LLM disabled")
        return
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()

    def stop(signum: int, frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with httpx.Client() as http:
        client = SonnetClient(settings, http)
        client.require_enabled()
        engine = create_db_engine(settings)
        try:
            while not stopped.is_set():
                forbidden = (settings.anthropic_api_key.get_secret_value(),)
                # Drain committed completion events even after a previous Worker crash.
                enriched = enrich_pending(engine, limit=1, forbidden=forbidden)
                if args.once and enriched:
                    return
                processed = run_one(
                    engine,
                    client,
                    stopped=stopped,
                    global_limit=settings.max_daily_cost_usd,
                    forbidden=forbidden,
                )
                if not stopped.is_set():
                    enrich_pending(engine, limit=1, forbidden=forbidden)
                if args.once:
                    return
                if not processed:
                    stopped.wait(2)
        finally:
            engine.dispose()
