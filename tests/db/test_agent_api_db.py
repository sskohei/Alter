import base64
import os
import uuid

import httpx
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.invitations import register_invitation, revoke_invitation
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
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


def test_owned_profile_workflow_and_idempotency(engine: Engine, created_users: list[uuid.UUID]):
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
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        app = create_app(settings, http_client=http)
        app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
        with TestClient(app) as client:
            key = {"Idempotency-Key": "test-agent-create"}
            response = client.post("/api/v1/agents", json=DRAFT, headers=key)
            assert response.status_code == 201, response.text
            body = response.json()
            aid = body["id"]
            assert "非公開" not in response.text
            assert client.post("/api/v1/agents", json=DRAFT, headers=key).json() == body
            assert (
                client.post(
                    "/api/v1/agents", json={**DRAFT, "name": "変更"}, headers=key
                ).status_code
                == 409
            )
            assert client.get(f"/api/v1/agents/{aid}/profile/private").json() == {
                "private_info": DRAFT["private_info"]
            }
            assert "private_info" not in client.get(f"/api/v1/agents/{aid}/profile").text
            approved = client.post(f"/api/v1/agents/{aid}/profile/approve", json={"version": 1})
            assert approved.status_code == 200
            assert (
                client.post(
                    f"/api/v1/agents/{aid}/profile/approve", json={"version": 1}
                ).status_code
                == 200
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
                client.post(
                    f"/api/v1/agents/{aid}/profile/approve", json={"version": 1}
                ).status_code
                == 409
            )
            app.dependency_overrides[get_principal] = lambda: Principal(stranger, "jwks")
            for path in ("", "/profile", "/profile/private"):
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
                conn.execute(
                    text("update public.profiles set deleted_at=null where id=:id"), {"id": owner}
                )
                invitation = conn.execute(
                    text("select id from public.invitations where auth_user_id=:id"), {"id": owner}
                ).scalar_one()
                revoke_invitation(conn, invitation, operator="sskohei")
            assert client.get("/api/v1/agents/me").json()["error"]["code"] == "ACCOUNT_INACTIVE"
