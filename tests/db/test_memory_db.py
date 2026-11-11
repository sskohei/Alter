import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain.conversation import run_one
from alter_domain.interaction import lock_interactions
from alter_domain.llm import BaselineModerator, FakeLLM
from alter_domain.memory import (
    context,
    delete_conversation,
    enrich_pending,
    extract_completed,
    recent_summary,
)
from alter_domain.scheduling import schedule
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def completed(engine):
    with engine.begin() as conn:
        conn.execute(text("update public.agent_settings set autonomy_enabled=true"))
    assert schedule(engine, datetime.now(UTC), FakeLLM.price, Decimal("10"))
    assert run_one(engine, FakeLLM(), BaselineModerator())
    assert enrich_pending(engine) == 1
    assert enrich_pending(engine) == 0
    with engine.begin() as conn:
        return conn.execute(text("select id from public.conversations")).scalar_one()


def test_memory_api_ownership_cursor_delete_replay_and_no_resurrection(
    engine: Engine, ai_pair: list[uuid.UUID]
):
    conversation = completed(engine)
    with engine.begin() as conn:
        owner = conn.execute(
            text("select owner_id from public.agents where id=:id"), {"id": ai_pair[0]}
        ).scalar_one()
        other = conn.execute(
            text("select owner_id from public.agents where id=:id"), {"id": ai_pair[1]}
        ).scalar_one()
        assert context(conn, ai_pair[0], ai_pair[1])
        assert recent_summary(conn, ai_pair[0], ai_pair[1])
        assert (
            conn.execute(
                text("select conversation_count from public.agent_relationships")
            ).scalar_one()
            == 1
        )
        lock_interactions(conn)
        extract_completed(conn, conversation)
        assert (
            conn.execute(
                text("select conversation_count from public.agent_relationships")
            ).scalar_one()
            == 1
        )

    def handler(req):
        uid = req.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"id": uid, "identities": [{"provider": "google", "id": uid}]}
        )

    settings = Settings(
        _env_file=None,
        alter_mode="live",
        database_url=engine.url.render_as_string(hide_password=False),
        supabase_secret_key="test",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        app = create_app(settings, http_client=http)
        app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/memories", params={"agent_id": str(ai_pair[0]), "limit": 1}
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["next_cursor"]
            memory_id = body["items"][0]["id"]
            page2 = client.get(
                "/api/v1/memories",
                params={"agent_id": str(ai_pair[0]), "limit": 1, "cursor": body["next_cursor"]},
            ).json()
            assert page2["items"][0]["id"] != memory_id
            assert (
                client.get(
                    "/api/v1/memories", params={"agent_id": str(ai_pair[0]), "cursor": "invalid!"}
                ).status_code
                == 422
            )
            assert (
                client.get("/api/v1/memories", params={"agent_id": str(ai_pair[1])}).status_code
                == 404
            )
            assert client.get("/api/v1/relationships").json()["items"][0]["conversation_count"] == 1
            app.dependency_overrides[get_principal] = lambda: Principal(other, "jwks")
            assert client.delete(f"/api/v1/memories/{memory_id}").status_code == 404
            app.dependency_overrides[get_principal] = lambda: Principal(owner, "jwks")
            headers = {"Idempotency-Key": "forget-memory-key"}
            assert (
                client.delete(f"/api/v1/memories/{memory_id}", headers=headers).status_code == 204
            )
            assert (
                client.delete(f"/api/v1/memories/{memory_id}", headers=headers).status_code == 204
            )
            assert (
                client.get("/api/v1/memories", params={"agent_id": str(ai_pair[0])}).json()["items"]
                == []
            )
    with engine.begin() as conn:
        assert context(conn, ai_pair[0], ai_pair[1]) == []
        assert recent_summary(conn, ai_pair[0], ai_pair[1]) is None
        assert context(conn, ai_pair[1], ai_pair[0])
        # Simulate a repeated extraction event even if consumer progress was lost.
        conn.execute(
            text("delete from public.memory_completions where conversation_id=:id"),
            {"id": conversation},
        )
        lock_interactions(conn)
        extract_completed(conn, conversation)
        assert context(conn, ai_pair[0], ai_pair[1]) == []
        delete_conversation(conn, conversation)
        assert conn.execute(text("select count(*) from public.memories")).scalar_one() == 0
        assert (
            conn.execute(text("select count(*) from public.agent_relationships")).scalar_one() == 0
        )
