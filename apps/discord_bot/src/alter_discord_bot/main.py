import argparse
import logging

from alter_domain.runtime import run_mock_process
from alter_domain.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Alter Discord bot scaffold")
    parser.add_argument("--once", action="store_true", help="Run one mock cycle and exit")
    args = parser.parse_args()
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    if not settings.discord_enabled:
        logging.info("Discord disabled; no Gateway connection or token required")
        return
    # Even when enabled, the scaffold never connects to a real Discord guild.
    run_mock_process("discord_bot", once=args.once)
