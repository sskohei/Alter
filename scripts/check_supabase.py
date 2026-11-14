"""Read-only remote readiness probe. Never print credentials or exception text."""

import argparse
import os
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def validate_target(database_url: str, project_ref: str) -> None:
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Use postgresql+psycopg")
    if url.query.get("sslmode") != "verify-full" or not url.query.get("sslrootcert"):
        raise ValueError("Require verify-full and sslrootcert")
    direct = url.host == f"db.{project_ref}.supabase.co"
    pooler = bool(
        url.host
        and url.host.endswith(".pooler.supabase.com")
        and url.username == f"postgres.{project_ref}"
    )
    if not direct and not pooler:
        raise ValueError("Database does not match expected project")


def check(database_url: str, project_ref: str) -> None:
    validate_target(database_url, project_ref)
    engine = create_engine(
        database_url,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        connect_args={"prepare_threshold": None, "connect_timeout": 10},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '10s'"))
            if not conn.scalar(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")):
                raise ValueError("TLS required")
            applied = set(
                conn.scalars(text("SELECT version FROM supabase_migrations.schema_migrations"))
            )
            expected = {
                p.name.split("_", 1)[0]
                for p in Path(__file__)
                .resolve()
                .parents[1]
                .joinpath("supabase/migrations")
                .glob("*.sql")
            }
            if applied != expected:
                raise ValueError("Migration versions differ")
            # EXPLAIN parses the claim query without executing or locking any row.
            conn.execute(
                text(
                    "EXPLAIN SELECT id FROM public.background_jobs "
                    "WHERE false FOR UPDATE SKIP LOCKED"
                )
            )
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(
                f"https://{project_ref}.supabase.co/auth/v1/.well-known/jwks.json"
            )
            response.raise_for_status()
            keys = response.json()["keys"]
            if not keys or not all(k["kty"] in {"EC", "RSA"} for k in keys):
                raise ValueError("Asymmetric JWKS required")
        print("PASS: TLS, migration versions, SKIP LOCKED syntax, asymmetric JWKS")
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    args = parser.parse_args()
    try:
        if not args.project_ref.isalnum():
            raise ValueError("Invalid project ref")
        check(os.environ["DATABASE_URL"], args.project_ref)
    except Exception:
        print("FAIL: Supabase probe; check private configuration (details suppressed)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
