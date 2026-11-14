import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from alter_domain.db import create_db_engine
from alter_domain.settings import Settings

validate_target = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/check_supabase.py")
)["validate_target"]


def test_pool_is_bounded_and_transaction_pooler_compatible():
    settings = Settings(_env_file=None)
    with patch("alter_domain.db.create_engine") as create:
        create_db_engine(settings)
    kwargs = create.call_args.kwargs
    assert kwargs["pool_size"] == 2
    assert kwargs["max_overflow"] == 0
    assert kwargs["connect_args"]["prepare_threshold"] is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres:secret@db.other.supabase.co/postgres",
        "postgresql+psycopg://postgres.ref:secret@evil.example/postgres?sslmode=verify-full&sslrootcert=/ca",
        "postgresql+psycopg://postgres.other:secret@aws-0.pooler.supabase.com/postgres?sslmode=verify-full&sslrootcert=/ca",
    ],
)
def test_probe_refuses_unsafe_or_wrong_project(url):
    with pytest.raises(ValueError):
        validate_target(url, "ref")


def test_probe_accepts_matching_tls_pooler():
    validate_target(
        "postgresql+psycopg://postgres.ref:secret@aws-0.pooler.supabase.com:6543/"
        "postgres?sslmode=verify-full&sslrootcert=/ca",
        "ref",
    )
