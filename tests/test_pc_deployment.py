import runpy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alter_api.main import create_app
from alter_api.readiness import database_ready
from alter_domain.settings import Settings
from fastapi.testclient import TestClient

runner = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/run_service.py"))


def environment():
    return {
        "ALTER_MODE": "live",
        "ALTER_ENVIRONMENT": "preview",
        "ALTER_PROJECT_REF": "previewref",
        "ALTER_OTHER_PROJECT_REF": "prodref",
        "DATABASE_URL": "postgresql+psycopg://postgres.previewref:secret@aws-0.pooler.supabase.com:6543/"
        "postgres?sslmode=verify-full&sslrootcert=/ca",
        "SUPABASE_URL": "https://previewref.supabase.co",
        "SUPABASE_JWT_ISSUER": "https://previewref.supabase.co/auth/v1",
        "WEB_URL": "https://preview.test",
        "CORS_ORIGINS": "https://preview.test",
        "SCHEDULER_ENABLED": "true",
    }


@pytest.mark.parametrize("role", ["api", "worker", "bot", "scheduler"])
def test_matching_project_is_accepted(role):
    runner["validate"](role, "preview", environment())


@pytest.mark.parametrize(
    "changes",
    [
        {"ALTER_ENVIRONMENT": "production"},
        {"ALTER_OTHER_PROJECT_REF": "previewref"},
        {"DATABASE_URL": "postgresql+psycopg://localhost/postgres"},
        {"SUPABASE_JWT_ISSUER": "https://prodref.supabase.co/auth/v1"},
        {"CORS_ORIGINS": "*"},
        {"SUPABASE_DB_PASSWORD": "secret"},
    ],
)
def test_cross_environment_and_unsafe_configuration_rejected(changes):
    with pytest.raises(ValueError):
        runner["validate"]("api", "preview", environment() | changes)


@pytest.mark.parametrize("role", ["worker", "bot", "scheduler"])
def test_api_secret_cannot_be_shared(role):
    with pytest.raises(ValueError):
        runner["validate"](role, "preview", environment() | {"SUPABASE_SECRET_KEY": "secret"})


def test_api_is_single_loopback_process_without_reload():
    args = runner["command"]("api", "preview", Path("/opt/alter/preview/current"))
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "8001"
    assert "--reload" not in args
    assert "--no-access-log" in args


def test_readiness_fails_closed_without_database_and_does_not_leak():
    app = create_app(Settings(_env_file=None, alter_mode="live"))
    app.state.db_engine = MagicMock()
    app.state.db_engine.connect.side_effect = RuntimeError("secret-database-password")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.headers["Cache-Control"] == "no-store"
        assert "secret-database-password" not in response.text


def test_readiness_checks_all_migration_versions():
    engine = MagicMock()
    conn = engine.connect.return_value.__enter__.return_value
    conn.scalars.return_value = []
    assert not database_ready(engine)
    versions = {
        p.name.split("_", 1)[0]
        for p in Path(__file__).resolve().parents[1].joinpath("supabase/migrations").glob("*.sql")
    }
    conn.scalars.return_value = versions
    assert database_ready(engine)
    conn.scalars.return_value = versions | {"20990101000000"}
    assert database_ready(engine)  # Compatible migrations may precede an app rollout.
