import os
import subprocess
import sys

import pytest
from alter_api.main import create_app
from alter_domain.settings import Settings
from fastapi.testclient import TestClient


def test_health_and_cors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTER_MODE", "mock")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = TestClient(create_app())
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.json() == {"status": "ok", "service": "api", "mode": "mock"}
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    denied = client.get("/health", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in denied.headers
    assert (
        client.get("/openapi.json").json()["paths"]["/health"]["get"]["operationId"] == "get_health"
    )


@pytest.mark.parametrize(
    "entry,args,enabled",
    [
        ("alter_worker.main", ["--once"], "false"),
        ("alter_scheduler.main", [], "false"),
        ("alter_discord_bot.main", [], "false"),
        ("alter_discord_bot.main", ["--once"], "true"),
    ],
)
def test_independent_entries(entry: str, args: list[str], enabled: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"from {entry} import main; main()", *args],
        env={**os.environ, "ALTER_MODE": "mock", "DISCORD_ENABLED": enabled},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "mock cycle completed" in result.stderr or "Discord disabled" in result.stderr


def test_live_processing_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="not implemented"):
        Settings(_env_file=None, alter_mode="live").require_mock()
