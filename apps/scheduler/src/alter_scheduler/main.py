import logging
from datetime import UTC, datetime

from alter_domain.db import create_db_engine
from alter_domain.runtime import run_mock_process
from alter_domain.scheduling import schedule
from alter_domain.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if settings.alter_mode == "mock":
        run_mock_process("scheduler", once=True)
        return
    if not settings.scheduler_enabled:
        logging.info("Scheduler disabled")
        return
    engine = create_db_engine(settings)
    try:
        jobs = schedule(
            engine,
            datetime.now(UTC),
            settings.max_daily_cost_usd,
            batch_size=settings.scheduler_batch_size,
        )
        logging.info("Scheduler reserved %d jobs", len(jobs))
    finally:
        engine.dispose()
