"""サーバー側 DB 接続（API・運営 CLI・Worker 共通）。

管理接続（RLS をバイパスする postgres ロール）で接続するため、所有権・招待の確認は
必ずドメイン処理の SQL 条件で行う。ブラウザへこの接続情報を渡さない。
"""

from sqlalchemy import Engine, create_engine

from alter_domain.settings import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # 接続文字列（パスワードを含む）をログに出さない
        hide_parameters=True,
    )
