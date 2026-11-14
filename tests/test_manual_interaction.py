from unittest.mock import Mock
from uuid import uuid4

import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.business import business_connection, interaction_connection
from alter_api.main import create_app
from alter_domain.settings import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def manual_api(monkeypatch):
    settings = Settings(
        _env_file=None,
        alter_mode="live",
        demo_manual_interactions_enabled=True,
        llm_enabled=True,
        anthropic_api_key="synthetic",
    )
    app = create_app(settings)
    owner, conversation, job = uuid4(), uuid4(), uuid4()
    app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
    conn = Mock()
    conn.execute.return_value.scalar_one.return_value = conversation
    app.dependency_overrides[business_connection] = lambda: conn
    app.dependency_overrides[interaction_connection] = lambda: conn
    select = Mock(return_value=job)
    monkeypatch.setattr("alter_api.activity_routes.scheduling.select_due", select)
    with TestClient(app) as client:
        yield client, settings, select, owner, conversation


def test_manual_start_scopes_owner_and_returns_reserved_conversation(manual_api):
    client, settings, select, owner, conversation = manual_api
    response = client.post(
        "/api/v1/conversations/start", headers={"Idempotency-Key": "demo-request-1"}
    )
    assert response.status_code == 202
    assert response.json() == {"id": str(conversation)}
    assert response.headers["cache-control"] == "no-store"
    assert select.call_args.kwargs == {"owner_id": owner, "manual_key": "demo-request-1"}


@pytest.mark.parametrize("environment", ["preview", "production"])
def test_demo_flag_cannot_enable_manual_starts_outside_local(manual_api, environment):
    client, settings, select, _, _ = manual_api
    settings.alter_environment = environment
    assert client.get("/api/v1/conversations/start").json() == {
        "enabled": False,
        "unlimited_daily_count": False,
    }
    assert (
        client.post(
            "/api/v1/conversations/start", headers={"Idempotency-Key": "demo-request-1"}
        ).status_code
        == 403
    )
    select.assert_not_called()


def test_disabled_missing_key_disabled_llm_and_no_partner_do_not_start(manual_api):
    client, settings, select, _, _ = manual_api
    settings.demo_manual_interactions_enabled = False
    assert client.post("/api/v1/conversations/start").status_code == 403
    settings.demo_manual_interactions_enabled = True
    assert client.post("/api/v1/conversations/start").status_code == 422
    settings.llm_enabled = False
    headers = {"Idempotency-Key": "demo-request-1"}
    assert client.post("/api/v1/conversations/start", headers=headers).status_code == 503
    select.assert_not_called()
    settings.llm_enabled = True
    select.return_value = None
    assert client.post("/api/v1/conversations/start", headers=headers).status_code == 409


def test_manual_endpoints_require_authentication():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/api/v1/conversations/start").status_code == 401
        assert client.post("/api/v1/conversations/start").status_code == 401
