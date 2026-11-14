"""#16: 交流設定・ブロック・通知設定 API の認証と OpenAPI 契約（DB を使わないもの）。"""

import uuid

import pytest
from alter_api.main import create_app
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
from pydantic import ValidationError

AGENT = uuid.uuid4()
PATHS = [
    ("get", f"/api/v1/agents/{AGENT}/settings"),
    ("patch", f"/api/v1/agents/{AGENT}/settings"),
    ("put", f"/api/v1/agents/{AGENT}/blocks/{uuid.uuid4()}"),
    ("delete", f"/api/v1/agents/{AGENT}/blocks/{uuid.uuid4()}"),
    ("get", "/api/v1/preferences"),
    ("patch", "/api/v1/preferences"),
]


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(("method", "path"), PATHS)
def test_requires_token(method: str, path: str) -> None:
    client = TestClient(create_app(_settings()), raise_server_exceptions=False)
    response = client.request(method, path, json={"bad": 1})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_openapi_matches_preview_contract() -> None:
    schema = create_app(_settings()).openapi()
    paths = schema["paths"]
    settings_path = paths["/api/v1/agents/{id}/settings"]
    blocks_path = paths["/api/v1/agents/{id}/blocks/{other_id}"]
    assert settings_path["get"]["operationId"] == "get_interaction_settings"
    assert settings_path["patch"]["operationId"] == "patch_interaction_settings"
    assert blocks_path["put"]["operationId"] == "block_agent"
    assert blocks_path["delete"]["operationId"] == "unblock_agent"
    assert "204" in blocks_path["put"]["responses"]
    assert paths["/api/v1/preferences"]["get"]["operationId"] == "get_preferences"
    assert paths["/api/v1/preferences"]["patch"]["operationId"] == "patch_preferences"

    schemas = schema["components"]["schemas"]
    assert set(schemas["AgentSettings"]["required"]) == {
        "autonomy_enabled",
        "frequency_per_day",
        "daily_budget_usd",
        "max_session_usd",
        "web_visibility",
        "discord_publish",
        "updated_at",
    }
    patch = schemas["AgentSettingsPatch"]
    assert patch["additionalProperties"] is False
    assert patch["minProperties"] == 1
    assert schemas["PreferencesPatch"]["minProperties"] == 1


@pytest.mark.parametrize("value", ["-1", "1.0000001", "abc"])
def test_budget_max_setting_is_validated(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_daily_budget_max_usd=value)  # type: ignore[call-arg]
