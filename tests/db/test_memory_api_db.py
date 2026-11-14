"""#25 owned read/delete integration; external Auth is synthetic, SQL is real."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.invitations import register_invitation
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text


@dataclass
class World:
    client: TestClient
    app: Any
    owners: list[uuid.UUID]
    agents: list[uuid.UUID]
    conversation: uuid.UUID
    memories: list[uuid.UUID]

    def as_user(self, index: int = 0) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(self.owners[index], "jwks")
        return self.client


def candidate(
    conn: Connection, agent: uuid.UUID, source: uuid.UUID | None, key: str, *, days: int = 0
) -> uuid.UUID:
    return conn.execute(
        text(
            "insert into "
            "public.memories(agent_id,source_conversation_id,"
            "text,importance,dedupe_key,created_at) "
            "values (:a,:s,:body,3,:k,:created) returning id"
        ),
        {
            "a": agent,
            "s": source,
            "body": f"候補 {key}",
            "k": key,
            "created": datetime.now(UTC) - timedelta(days=days),
        },
    ).scalar_one()


@pytest.fixture
def world(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[World]:
    owners = [uuid.uuid4() for _ in range(3)]
    agents = [uuid.uuid4() for _ in owners]
    identities = {str(owner): [{"provider": "google", "id": f"g-{owner.hex}"}] for owner in owners}
    with engine.begin() as conn:
        for owner, agent in zip(owners, agents, strict=True):
            conn.execute(text("insert into auth.users(id) values (:id)"), {"id": owner})
            created_users.append(owner)
            register_invitation(
                conn, AuthUser(owner, (AuthIdentity("google", f"g-{owner.hex}"),)), operator="test"
            )
            conn.execute(
                text("insert into public.profiles(id,display_name) values (:id,'synthetic')"),
                {"id": owner},
            )
            conn.execute(
                text("insert into public.agents(id,owner_id,name) values (:id,:o,'AI')"),
                {"id": agent, "o": owner},
            )
        conversation = conn.execute(
            text(
                "insert into public.conversations(status,ended_at) values "
                "('completed',now()) returning id"
            )
        ).scalar_one()
        for ordinal, agent in enumerate(agents[:2], 1):
            conn.execute(
                text(
                    "insert into "
                    "public.conversation_participants(conversation_id,agent_id,"
                    "ordinal,web_consent,discord_consent) "
                    "values (:c,:a,:n,true,false)"
                ),
                {"c": conversation, "a": agent, "n": ordinal},
            )
        memories = [
            candidate(conn, agents[0], conversation, "own1"),
            candidate(conn, agents[0], conversation, "own2"),
            candidate(conn, agents[1], conversation, "other"),
            candidate(conn, agents[0], None, "no-source"),
        ]
        low, high = sorted(agents[:2])
        conn.execute(
            text(
                "insert into "
                "public.agent_relationships(agent_low_id,agent_high_id,conversation_count) "
                "values (:l,:h,99)"
            ),
            {"l": low, "h": high},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        uid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"id": uid, "identities": identities[uid]})

    settings = Settings(
        _env_file=None,
        alter_mode="live",  # type: ignore[call-arg]
        database_url=engine.url.render_as_string(hide_password=False),
        supabase_secret_key="test-only",
    )
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            app = create_app(settings, http_client=http)
            with TestClient(app, raise_server_exceptions=False) as client:
                yield World(client, app, owners, agents, conversation, memories)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("delete from public.conversations where id=:id"), {"id": conversation}
            )


def query(world: World, **extra: Any) -> dict[str, Any]:
    response = world.as_user().get(
        "/api/v1/memories", params={"agent_id": world.agents[0], **extra}
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    result: dict[str, Any] = response.json()
    return result


def test_owned_lists_nullable_source_and_hidden_foreign_memory(world: World) -> None:
    items = query(world)["items"]
    assert len(items) == 3
    assert any(m["source_conversation_id"] is None for m in items)
    assert str(world.memories[2]) not in {m["id"] for m in items}
    client = world.as_user(2)
    assert client.get("/api/v1/memories", params={"agent_id": world.agents[0]}).status_code == 404
    assert client.delete(f"/api/v1/memories/{world.memories[0]}").status_code == 404


def test_delete_removes_own_source_siblings_only_and_suppresses_reinsertion(
    engine: Engine, world: World
) -> None:
    client = world.as_user()
    path = f"/api/v1/memories/{world.memories[0]}"
    assert client.delete(path).status_code == 204
    assert client.delete(path).status_code == 204
    assert len(query(world)["items"]) == 1
    with engine.begin() as conn:
        assert (
            conn.execute(
                text("select count(*) from public.memories where id=:id"), {"id": world.memories[2]}
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("select count(*) from public.conversations where id=:id"),
                {"id": world.conversation},
            ).scalar_one()
            == 1
        )
        for source, key in [(world.conversation, "new-key"), (None, "own1")]:
            result = conn.execute(
                text(
                    "insert into "
                    "public.memories(agent_id,source_conversation_id,text,importance,dedupe_key) "
                    "values (:a,:s,'must not return',3,:k) returning id"
                ),
                {"a": world.agents[0], "s": source, "k": key},
            ).first()
            assert result is None
        assert (
            conn.execute(
                text(
                    "select count(*) from public.audit_events where actor_profile_id=:o "
                    "and action='memory.delete'"
                ),
                {"o": world.owners[0]},
            ).scalar_one()
            == 1
        )
    assert (
        world.as_user(1)
        .get("/api/v1/memories", params={"agent_id": world.agents[1]})
        .json()["items"]
    )


def test_pagination_survives_deleted_boundary_and_rejects_invalid_cursor(
    engine: Engine, world: World
) -> None:
    first = query(world, limit=1)
    assert first["next_cursor"]
    world.as_user().delete(f"/api/v1/memories/{first['items'][0]['id']}")
    remaining = query(world, limit=1, cursor=first["next_cursor"])
    assert remaining["items"]
    assert remaining["items"][0]["id"] != first["items"][0]["id"]
    client = world.as_user()
    for path, params in [
        ("/api/v1/memories", {"agent_id": world.agents[0]}),
        ("/api/v1/relationships", {}),
    ]:
        assert client.get(path, params={**params, "cursor": "invalid"}).status_code == 422
        assert client.get(path, params={**params, "limit": 51}).status_code == 422


def test_retention_deleted_participant_and_source_cascade(engine: Engine, world: World) -> None:
    with engine.begin() as conn:
        candidate(conn, world.agents[0], None, "expired", days=181)
        conn.execute(
            text("update public.profiles set deleted_at=now() where id=:id"),
            {"id": world.owners[1]},
        )
    assert len(query(world)["items"]) == 1
    assert world.as_user().get("/api/v1/relationships").json()["items"] == []
    with engine.begin() as conn:
        conn.execute(
            text("update public.profiles set deleted_at=null where id=:id"), {"id": world.owners[1]}
        )
        conn.execute(
            text("delete from public.conversations where id=:id"), {"id": world.conversation}
        )
    assert len(query(world)["items"]) == 1
    assert world.as_user().get("/api/v1/relationships").json()["items"] == []


def test_relationship_recounts_visible_completions_and_reports_only_own_block(
    engine: Engine, world: World
) -> None:
    client = world.as_user()
    response = client.get("/api/v1/relationships")
    assert response.status_code == 200, response.text
    [relationship] = response.json()["items"]
    assert relationship["conversation_count"] == 1  # cached 99 must never be exposed
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.agent_blocks(blocker_agent_id,blocked_agent_id) values (:b,:a)"
            ),
            {"a": world.agents[0], "b": world.agents[1]},
        )
    assert client.get("/api/v1/relationships").json()["items"][0]["blocked"] is False
    path = f"/api/v1/agents/{world.agents[0]}/blocks/{world.agents[1]}"
    assert client.put(path).status_code == 204
    assert client.get("/api/v1/relationships").json()["items"][0]["blocked"] is True
    assert client.delete(path).status_code == 204
    assert client.get("/api/v1/relationships").json()["items"][0]["blocked"] is False


def test_generated_client_with_real_http(world: World) -> None:
    import shutil
    import socket
    import subprocess
    import threading
    import time
    from pathlib import Path

    import uvicorn
    from fastapi import Request

    def principal(request: Request) -> Principal:
        return Principal(
            uuid.UUID(request.headers["authorization"].removeprefix("Bearer ")), "jwks"
        )

    world.app.dependency_overrides[get_principal] = principal
    pnpm = shutil.which("pnpm")
    assert pnpm
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(world.app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            result = subprocess.run(
                [
                    pnpm,
                    "--filter",
                    "@alter/api-client",
                    "exec",
                    "tsx",
                    "tests/memory-live.ts",
                    f"http://127.0.0.1:{port}",
                    str(world.owners[0]),
                    str(world.owners[2]),
                    str(world.agents[0]),
                    str(world.agents[1]),
                    str(world.memories[0]),
                ],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_candidate_text_and_identity_cannot_be_relabelled(engine: Engine, world: World) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("update public.memories set text='replacement' where id=:id"),
                {"id": world.memories[0]},
            )
    with engine.begin() as conn:
        conn.execute(
            text("update public.memories set importance=5 where id=:id"), {"id": world.memories[0]}
        )
    assert any(m["importance"] == 5 for m in query(world)["items"])


def test_zero_pair_paging_and_only_approved_public_interests(engine: Engine, world: World) -> None:
    with engine.begin() as conn:
        low, high = sorted((world.agents[0], world.agents[2]))
        conn.execute(
            text(
                "insert into public.agent_relationships(agent_low_id,agent_high_id) values (:l,:h)"
            ),
            {"l": low, "h": high},
        )
        for agent, interests in [
            (world.agents[0], ["読書", "散歩"]),
            (world.agents[1], ["読書", "音楽"]),
        ]:
            conn.execute(
                text(
                    "insert into public.agent_profiles(agent_id,version,personality,values_text,"
                    "interests,speaking_style,interaction_policy,"
                    "shareable_info,restrictions,approved_at) "
                    "values (:a,1,'p','v',:i,'s','i','s','r',now())"
                ),
                {"a": agent, "i": interests},
            )
            conn.execute(
                text("update public.agents set approved_profile_version=1 where id=:a"),
                {"a": agent},
            )
    client = world.as_user()
    response = client.get("/api/v1/relationships")
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert {r["conversation_count"] for r in rows} == {0, 1}
    assert next(r for r in rows if r["other_agent_id"] == str(world.agents[1]))[
        "shared_interests"
    ] == ["読書"]
    first = client.get("/api/v1/relationships", params={"limit": 1}).json()
    last = client.get(
        "/api/v1/relationships", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert last["next_cursor"] is None
    assert first["items"][0]["other_agent_id"] != last["items"][0]["other_agent_id"]


def test_expired_source_hides_derived_memory_and_relationship(engine: Engine, world: World) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.conversations set created_at=now()-interval '91 days' where id=:id"
            ),
            {"id": world.conversation},
        )
    assert len(query(world)["items"]) == 1
    assert world.as_user().get("/api/v1/relationships").json()["items"] == []
