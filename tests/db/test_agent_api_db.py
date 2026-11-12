import base64
import os
import uuid

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain import agents
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.invitations import register_invitation, revoke_invitation
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

DRAFT = dict(
    name="分身",
    personality="穏やか",
    values_text="対話",
    interests=["読書"],
    speaking_style="丁寧",
    interaction_policy="尊重",
    shareable_info="本が好き",
    restrictions="推測禁止",
    private_info="非公開の秘密",
)


@pytest.fixture
def agent_api(engine: Engine, created_users: list[uuid.UUID]):
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    created_users.extend([owner, stranger])
    with engine.begin() as conn:
        for uid in (owner, stranger):
            conn.execute(text("insert into auth.users (id) values (:id)"), {"id": uid})
            register_invitation(
                conn, AuthUser(uid, (AuthIdentity("google", str(uid)),)), operator="sskohei"
            )

    def handler(req: httpx.Request) -> httpx.Response:
        uid = req.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"id": uid, "identities": [{"provider": "google", "id": uid}]}
        )

    settings = Settings(
        _env_file=None,
        alter_mode="live",
        database_url=engine.url.render_as_string(hide_password=False),
        supabase_secret_key="test-only",
        profile_encryption_key_v1=base64.b64encode(os.urandom(32)).decode(),
        api_idempotency_hmac_keys={1: SecretStr(base64.b64encode(os.urandom(32)).decode())},
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        app = create_app(settings, http_client=http)
        app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
        with TestClient(app) as client:
            yield client, app, settings, owner, stranger


def test_owned_profile_workflow_and_idempotency(engine: Engine, agent_api):
    client, app, settings, owner, stranger = agent_api
    key = {"Idempotency-Key": "test-agent-create"}
    response = client.post("/api/v1/agents", json=DRAFT, headers=key)
    assert response.status_code == 201, response.text
    body = response.json()
    aid = body["id"]
    assert "非公開" not in response.text
    assert client.post("/api/v1/agents", json=DRAFT, headers=key).json() == body
    assert (
        client.post("/api/v1/agents", json={**DRAFT, "name": "変更"}, headers=key).status_code
        == 409
    )
    assert client.get(f"/api/v1/agents/{aid}/profile/private").json() == {
        "private_info": DRAFT["private_info"]
    }
    assert "private_info" not in client.get(f"/api/v1/agents/{aid}/profile").text
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select onboarded_at from public.profiles where id=:id"), {"id": owner}
            ).scalar_one()
            is None
        )
    assert (
        client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 2}).status_code == 409
    )
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select onboarded_at from public.profiles where id=:id"), {"id": owner}
            ).scalar_one()
            is None
        )
    approved = client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 1})
    assert approved.status_code == 200
    with engine.connect() as conn:
        first_onboarded = conn.execute(
            text("select onboarded_at from public.profiles where id=:id"), {"id": owner}
        ).scalar_one()
        assert first_onboarded is not None
        assert (
            first_onboarded
            == conn.execute(
                text(
                    "select approved_at from public.agent_profiles where agent_id=:id and version=1"
                ),
                {"id": aid},
            ).scalar_one()
        )
    assert (
        client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 1}).status_code == 200
    )
    revision = client.post(
        f"/api/v1/agents/{aid}/profile/revisions", json={**DRAFT, "expected_version": 1}
    )
    assert revision.json()["version"] == 2, revision.text
    assert (
        client.post(
            f"/api/v1/agents/{aid}/profile/revisions", json={**DRAFT, "expected_version": 1}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 1}).status_code == 409
    )
    assert client.get(f"/api/v1/agents/{aid}/profile").json()["version"] == 2
    old = client.get(f"/api/v1/agents/{aid}/profile?version=1")
    assert old.status_code == 200
    assert old.json()["version"] == 1
    assert old.json()["approved_at"] is not None
    assert "private_info" not in old.text
    assert client.get(f"/api/v1/agents/{aid}/profile?version=3").status_code == 404
    for version in ("0", "-1", "bad"):
        assert client.get(f"/api/v1/agents/{aid}/profile?version={version}").status_code == 422
    assert (
        client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 2}).status_code == 200
    )
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select onboarded_at from public.profiles where id=:id"), {"id": owner}
            ).scalar_one()
            == first_onboarded
        )
    app.dependency_overrides[get_principal] = lambda: Principal(stranger, "jwks")
    for path in ("", "/profile", "/profile?version=1", "/profile/private"):
        method = client.patch if not path else client.get
        args = {"json": {"name": "stolen"}} if not path else {}
        assert method(f"/api/v1/agents/{aid}{path}", **args).status_code == 404
    app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
    with engine.begin() as conn:
        assert not conn.execute(
            text("select autonomy_enabled from public.agent_settings where agent_id=:id"),
            {"id": aid},
        ).scalar_one()
        conn.execute(
            text("update public.profiles set deleted_at=now() where id=:id"), {"id": owner}
        )
    inactive = client.get("/api/v1/agents/me")
    assert inactive.status_code == 403
    assert inactive.json()["error"]["code"] == "ACCOUNT_INACTIVE"
    with engine.begin() as conn:
        conn.execute(text("update public.profiles set deleted_at=null where id=:id"), {"id": owner})
        invitation = conn.execute(
            text("select id from public.invitations where auth_user_id=:id"), {"id": owner}
        ).scalar_one()
        revoke_invitation(conn, invitation, operator="sskohei")
    assert client.get("/api/v1/agents/me").json()["error"]["code"] == "ACCOUNT_INACTIVE"


def test_approval_failure_rolls_back_onboarding(engine: Engine, agent_api, monkeypatch):
    client, app, settings, owner, stranger = agent_api
    aid = client.post("/api/v1/agents", json=DRAFT).json()["id"]
    original_owned = agents.owned

    def fail_after_approval(conn, owner, agent_id, *, lock=False):
        if not lock:
            raise agents.AgentConflict()
        return original_owned(conn, owner, agent_id, lock=lock)

    with monkeypatch.context() as patch:
        patch.setattr(agents, "owned", fail_after_approval)
        assert (
            client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 1}).status_code
            == 409
        )
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select onboarded_at from public.profiles where id=:id"), {"id": owner}
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("select approved_at from public.agent_profiles where agent_id=:id"),
                {"id": aid},
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("select approved_profile_version from public.agents where id=:id"), {"id": aid}
            ).scalar_one()
            is None
        )


def test_idempotency_survives_key_rotation_and_requires_retained_key(engine: Engine, agent_api):
    client, app, settings, owner, stranger = agent_api
    headers = {"Idempotency-Key": "rotate-agent-create"}
    first = client.post("/api/v1/agents", json=DRAFT, headers=headers)
    assert first.status_code == 201
    settings.profile_encryption_key_v1 = SecretStr("")
    settings.api_idempotency_hmac_keys[2] = SecretStr(base64.b64encode(os.urandom(32)).decode())
    settings.api_idempotency_hmac_active_version = 2
    replay = client.post("/api/v1/agents", json=DRAFT, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert (
        client.post(
            "/api/v1/agents", json={**DRAFT, "private_info": "different"}, headers=headers
        ).status_code
        == 409
    )
    with engine.connect() as conn:
        row = conn.execute(
            text("select hmac_key_version,response from public.api_idempotency where owner_id=:id"),
            {"id": owner},
        ).one()
        assert row.hmac_key_version == 1
        assert "非公開" not in str(row.response)
    del settings.api_idempotency_hmac_keys[1]
    assert client.post("/api/v1/agents", json=DRAFT, headers=headers).status_code == 503
    # The new owner writes with the new active version.
    settings.profile_encryption_key_v1 = SecretStr(base64.b64encode(os.urandom(32)).decode())
    app.dependency_overrides[get_principal] = lambda: Principal(stranger, "jwks")
    assert client.post("/api/v1/agents", json=DRAFT, headers=headers).status_code == 201
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("select hmac_key_version from public.api_idempotency where owner_id=:id"),
                {"id": stranger},
            ).scalar_one()
            == 2
        )


@pytest.mark.parametrize("invalid_key", [None, "", "invalid-base64!", "c2hvcnQ="])
def test_invalid_idempotency_key_does_not_write(engine: Engine, agent_api, invalid_key):
    client, app, settings, owner, stranger = agent_api
    settings.api_idempotency_hmac_keys = {} if invalid_key is None else {1: SecretStr(invalid_key)}
    # Empty private info must not bypass the dedicated HMAC configuration check.
    draft = {**DRAFT, "private_info": ""}
    response = client.post(
        "/api/v1/agents", json=draft, headers={"Idempotency-Key": "invalid-key-create"}
    )
    assert response.status_code == 503
    with engine.connect() as conn:
        for table in ("agents", "api_idempotency"):
            assert (
                conn.execute(
                    text(f"select count(*) from public.{table} where owner_id=:id"), {"id": owner}
                ).scalar_one()
                == 0
            )
    # Without an idempotency header, this API does not need the HMAC key.
    assert client.post("/api/v1/agents", json=draft).status_code == 201


def test_expired_idempotency_response_is_not_replayed(engine: Engine, agent_api):
    client, app, settings, owner, stranger = agent_api
    headers = {"Idempotency-Key": "expired-agent-create"}
    assert client.post("/api/v1/agents", json=DRAFT, headers=headers).status_code == 201
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.api_idempotency set created_at=now()-interval '25 hours' "
                "where owner_id=:id"
            ),
            {"id": owner},
        )
    # Expiration does not allow creation of a second AI, even if cleanup has not run yet.
    assert client.post("/api/v1/agents", json=DRAFT, headers=headers).status_code == 409
    with engine.begin() as conn:
        conn.execute(text("delete from public.api_idempotency where owner_id=:id"), {"id": owner})
    assert client.post("/api/v1/agents", json=DRAFT, headers=headers).status_code == 409
