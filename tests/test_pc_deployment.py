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


# -- #30 パージ・#36 状態確認の定期実行 ------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
PURGE_KEYS = {"SUPABASE_SECRET_KEY": "s", "SUPABASE_STORAGE_SECRET_KEY": "t"}


def test_purge_gets_only_auth_and_storage_admin_keys():
    runner["validate"]("purge", "preview", environment() | PURGE_KEYS)
    # 管理鍵がなければ起動しない
    with pytest.raises(ValueError):
        runner["validate"]("purge", "preview", environment())
    with pytest.raises(ValueError):
        runner["validate"](
            "purge",
            "preview",
            environment() | PURGE_KEYS | {"SUPABASE_URL": "https://prodref.supabase.co"},
        )
    for extra in (
        "PROFILE_ENCRYPTION_KEY_V1",
        "API_IDEMPOTENCY_HMAC_KEYS",
        "DISCORD_CLIENT_SECRET",
        "ANTHROPIC_API_KEY",
        "DISCORD_BOT_TOKEN",
    ):
        with pytest.raises(ValueError):
            runner["validate"]("purge", "preview", environment() | PURGE_KEYS | {extra: "x"})


@pytest.mark.parametrize(
    "extra",
    [
        "SUPABASE_SECRET_KEY",
        "SUPABASE_STORAGE_SECRET_KEY",
        "ANTHROPIC_API_KEY",
        "DISCORD_BOT_TOKEN",
    ],
)
def test_ops_status_gets_no_credentials(extra):
    runner["validate"]("ops", "preview", environment())
    with pytest.raises(ValueError):
        runner["validate"]("ops", "preview", environment() | {extra: "x"})


def test_purge_and_ops_commands():
    root = Path("/opt/alter/preview/current")
    assert runner["command"]("purge", "preview", root) == [
        str(root / ".venv/bin/alter-api-purge"),
        "--max-jobs",
        "20",
    ]
    ops = runner["command"]("ops", "production", root)
    assert ops[0] == str(root / ".venv/bin/alter-ops")
    # 読み取りの status だけ（照合系の書き込みコマンドは定期実行しない）
    assert ops[-1] == "status"
    assert ops[ops.index("--operator") + 1] == "systemd-production"


def _unit(name):
    return (ROOT / "deploy/systemd" / name).read_text()


@pytest.mark.parametrize(
    ("service", "env_file", "role"),
    [("alter-purge@.service", "purge.env", "purge"), ("alter-ops@.service", "ops.env", "ops")],
)
def test_oneshot_units_use_their_own_env_and_lock(service, env_file, role):
    unit = _unit(service)
    assert "Type=oneshot" in unit
    assert f"EnvironmentFile=/etc/alter/%i/{env_file}" in unit
    assert f"/run/alter-%i-{role}/run.lock" in unit
    assert f"scripts/run_service.py {role} %i" in unit
    assert "Restart=no" in unit
    assert "NoNewPrivileges=true" in unit


def test_purge_output_is_suppressed_and_ops_output_is_kept():
    # パージのログには削除要求 ID が出るので残さない。状態確認は件数と固定コードだけ
    assert "StandardOutput=null" in _unit("alter-purge@.service")
    assert "StandardOutput=journal" in _unit("alter-ops@.service")
    assert "StandardError=null" in _unit("alter-ops@.service")


def test_timers():
    purge = _unit("alter-purge@.timer")
    assert "OnCalendar=hourly" in purge
    # 停止中に逃した回は起動後に 1 回実行（削除の 30 日の期限）
    assert "Persistent=true" in purge
    assert "Unit=alter-purge@%i.service" in purge
    ops = _unit("alter-ops@.timer")
    assert "/15:00" in ops
    assert "Persistent=false" in ops
    assert "Unit=alter-ops@%i.service" in ops


@pytest.mark.parametrize("env", ["preview", "production"])
def test_env_examples_separate_credentials(env):
    def keys(role):
        lines = (ROOT / f"config/pc/{env}/{role}.env.example").read_text().splitlines()
        return {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}

    assert {"SUPABASE_SECRET_KEY", "SUPABASE_STORAGE_SECRET_KEY"} <= keys("purge")
    assert not keys("purge") & {
        "PROFILE_ENCRYPTION_KEY_V1",
        "ANTHROPIC_API_KEY",
        "DISCORD_BOT_TOKEN",
    }
    assert not keys("ops") & {
        "SUPABASE_SECRET_KEY",
        "SUPABASE_STORAGE_SECRET_KEY",
        "ANTHROPIC_API_KEY",
    }
    assert "API_CLIENT_IP_HEADER" in keys("api")
