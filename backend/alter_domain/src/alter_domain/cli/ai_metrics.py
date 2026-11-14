import argparse
from pathlib import Path

from alter_domain.db import create_db_engine
from alter_domain.metrics import snapshot
from alter_domain.settings import Settings


def run() -> None:
    parser = argparse.ArgumentParser(description="Export aggregate AI metrics without user content")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment", choices=("local", "production"), default="local")
    parser.add_argument("--observation-days", type=int, default=0)
    args = parser.parse_args()
    if args.observation_days < 0:
        parser.error("observation-days must be nonnegative")
    engine = create_db_engine(Settings())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("set transaction isolation level repeatable read, read only")
            result = snapshot(
                conn, environment=args.environment, observation_days=args.observation_days
            )
        args.output.write_text(result.model_dump_json(indent=2) + "\n")
    finally:
        engine.dispose()
