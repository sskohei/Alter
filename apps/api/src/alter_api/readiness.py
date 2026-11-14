"""Readiness is separate from liveness and never discloses database details."""

from pathlib import Path

from sqlalchemy import Engine, text


def database_ready(engine: Engine) -> bool:
    expected = {
        path.name.split("_", 1)[0]
        for path in Path(__file__)
        .resolve()
        .parents[4]
        .joinpath("supabase/migrations")
        .glob("*.sql")
    }
    if not expected:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '3s'"))
            applied = set(
                conn.scalars(text("SELECT version FROM supabase_migrations.schema_migrations"))
            )
            return expected <= applied
    except Exception:
        return False
