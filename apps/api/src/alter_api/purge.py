"""アカウント削除のパージと保持期限の削除を 1 回実行する運営 CLI（#30 後半、ADR-016）。

手元 PC の OS の定期実行（1 時間ごと、ADR-015）から ``alter-api-purge`` として 1 回ずつ
起動する（設定は #34）。Auth・Storage の管理鍵を使うのはこの CLI と API だけで、Worker・Bot・
Scheduler の環境ファイルには入れない。

- ``ALTER_MODE`` が live でなければ何もしない（mock で DB や管理 API に触れない）
- 実行できる削除ジョブを最大 ``--max-jobs`` 件処理し、そのあと保持期限の削除を行う
- ログには削除要求 ID と件数だけを出す（表示名・メール・本文・鍵を出さない）
"""

import argparse
import logging
from datetime import UTC, datetime

import httpx
from alter_domain import account_purge
from alter_domain.db import create_db_engine
from alter_domain.settings import Settings

logger = logging.getLogger("alter_api.purge")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purge deleted accounts and expired records")
    parser.add_argument("--max-jobs", type=int, default=20, help="deletion jobs per run (1-100)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if settings.alter_mode != "live":
        logger.info("Purge skipped outside live mode")
        return 0
    engine = create_db_engine(settings)
    try:
        with httpx.Client() as http:
            auth = account_purge.SupabaseAuthDeleter(
                settings.supabase_url, settings.supabase_secret_key, http
            )
            storage = account_purge.SupabaseStoragePurger(
                settings.supabase_url,
                settings.supabase_storage_secret_key.get_secret_value(),
                http,
            )
            processed = 0
            for _ in range(max(1, min(args.max_jobs, 100))):
                if account_purge.run_one(engine, auth, storage, datetime.now(UTC)) is None:
                    break
                processed += 1
        with engine.begin() as conn:
            counts = account_purge.purge_expired(conn, datetime.now(UTC))
        logger.info("Purge finished: jobs=%d expired=%s", processed, counts)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
