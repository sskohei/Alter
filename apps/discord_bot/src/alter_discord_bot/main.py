import argparse
import asyncio
import logging

from alter_domain.runtime import run_mock_process
from alter_domain.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Alter Discord bot")
    parser.add_argument("--once", action="store_true", help="Run one mock cycle and exit")
    parser.add_argument(
        "--sync-commands",
        action="store_true",
        help="Register slash commands with Discord and exit (run once per deploy)",
    )
    args = parser.parse_args()
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    if not settings.discord_enabled:
        logging.info("Discord disabled; no Gateway connection or token required")
        return
    if settings.alter_mode != "live":
        # mock では Discord に接続しない（資格情報なしの待機プロセス）
        run_mock_process("discord_bot", once=args.once)
        return
    from alter_discord_bot.bot import run

    asyncio.run(run(settings, sync_commands=args.sync_commands))
