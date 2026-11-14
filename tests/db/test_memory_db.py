import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
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


@pytest.fixture
def memory_client(engine: Engine, ai_pair: list[uuid.UUID]):
    completed(engine)
    with engine.begin() as conn:
        owner = conn.execute(
            text("select owner_id from public.agents where id=:id"), {"id": ai_pair[0]}
        ).scalar_one()

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
            yield client


@pytest.mark.parametrize(
    "outgoing,incoming", [(False, False), (False, True), (True, False), (True, True)]
)
def test_relationship_blocked_only_reports_outgoing(
    engine: Engine,
    ai_pair: list[uuid.UUID],
    memory_client: TestClient,
    outgoing: bool,
    incoming: bool,
):
    with engine.begin() as conn:
        for enabled, blocker, blocked in (
            (outgoing, ai_pair[0], ai_pair[1]),
            (incoming, ai_pair[1], ai_pair[0]),
        ):
            if enabled:
                conn.execute(
                    text(
                        "insert into public.agent_blocks (blocker_agent_id,blocked_agent_id) "
                        "values (:blocker,:blocked)"
                    ),
                    {"blocker": blocker, "blocked": blocked},
                )
    response = memory_client.get("/api/v1/relationships")
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["blocked"] is outgoing


@pytest.mark.parametrize("deletion", ["account", "agent"])
def test_memory_list_hides_deleted_source_participant_before_purge(
    engine: Engine,
    ai_pair: list[uuid.UUID],
    memory_client: TestClient,
    created_users: list[uuid.UUID],
    deletion: str,
):
    # A separate source must remain visible, including after an existing cursor.
    third_owner, third_agent, visible_conversation, visible_memory = (
        uuid.uuid4() for _ in range(4)
    )
    created_users.append(third_owner)
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": third_owner})
        conn.execute(
            text("insert into public.profiles (id,display_name) values (:id,'Third')"),
            {"id": third_owner},
        )
        conn.execute(
            text("insert into public.agents (id,owner_id,name) values (:id,:owner,'Third AI')"),
            {"id": third_agent, "owner": third_owner},
        )
        conn.execute(
            text("insert into public.conversations (id,status) values (:id,'completed')"),
            {"id": visible_conversation},
        )
        for ordinal, agent in enumerate((ai_pair[0], third_agent), start=1):
            conn.execute(
                text(
                    "insert into public.conversation_participants "
                    "(conversation_id,agent_id,ordinal,discord_consent,web_consent) "
                    "values (:conv,:agent,:ordinal,false,true)"
                ),
                {"conv": visible_conversation, "agent": agent, "ordinal": ordinal},
            )
        conn.execute(
            text(
                "insert into public.memories "
                "(id,agent_id,source_conversation_id,text,importance,dedupe_key,created_at) "
                "values (:id,:agent,:conv,'Visible memory',3,'visible',now()-interval '1 day')"
            ),
            {"id": visible_memory, "agent": ai_pair[0], "conv": visible_conversation},
        )
    params = {"agent_id": str(ai_pair[0]), "limit": 1}
    before = memory_client.get("/api/v1/memories", params=params)
    assert before.status_code == 200, before.text
    cursor = before.json()["next_cursor"]
    assert before.json()["items"] and cursor
    with engine.begin() as conn:
        if deletion == "account":
            conn.execute(
                text(
                    "update public.profiles set deleted_at=now() where id="
                    "(select owner_id from public.agents where id=:id)"
                ),
                {"id": ai_pair[1]},
            )
        else:
            conn.execute(
                text("update public.agents set status='deleted' where id=:id"),
                {"id": ai_pair[1]},
            )
        assert context(conn, ai_pair[0], ai_pair[1]) == []
        assert recent_summary(conn, ai_pair[0], ai_pair[1]) is None
        assert (
            conn.execute(
                text("select count(*) from public.memories where agent_id=:id"),
                {"id": ai_pair[0]},
            ).scalar_one()
            > 0
        )
    for query in (params, {**params, "cursor": cursor}):
        response = memory_client.get("/api/v1/memories", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["id"] for item in body["items"]] == [str(visible_memory)]
        assert body["next_cursor"] is None
