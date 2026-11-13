"""#21: 会話一覧・詳細と Web 通知 API の DB 統合テスト（docs/07・docs/15、#47 の合意）。

JWT の検証は #43 のテストで確認済みのため ``get_principal`` を差し替える。
Auth 管理 API は偽物、招待は共通ドメイン処理で実 DB に登録する。
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain import activity
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text


@dataclass
class Api:
    app: FastAPI
    client: TestClient
    identities: dict[str, list[dict[str, str]]]

    def as_user(self, sub: uuid.UUID) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(sub, "jwks")
        return self.client


@pytest.fixture
def api(engine: Engine) -> Iterator[Api]:
    identities: dict[str, list[dict[str, str]]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        uid = request.url.path.rsplit("/", 1)[1]
        if uid not in identities:
            return httpx.Response(404)
        return httpx.Response(200, json={"id": uid, "identities": identities[uid]})

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        alter_mode="live",
        database_url=engine.url.render_as_string(hide_password=False),
        supabase_secret_key="test-only",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        app = create_app(settings, http_client=http)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Api(app, client, identities)


@pytest.fixture
def users(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[list[uuid.UUID]]:
    """テスト後に会話・費用・ジョブを先に消す（参加者 AI は RESTRICT のため）。"""
    yield created_users
    with engine.begin() as conn:
        agent_ids = (
            conn.execute(
                text("select id from public.agents where owner_id = any(:ids)"),
                {"ids": created_users},
            )
            .scalars()
            .all()
        )
        conv_ids = (
            conn.execute(
                text(
                    "select conversation_id from public.conversation_participants "
                    "where agent_id = any(:a)"
                ),
                {"a": agent_ids},
            )
            .scalars()
            .all()
        )
        conn.execute(
            text("delete from public.usage_records where conversation_id = any(:c)"),
            {"c": conv_ids},
        )
        conn.execute(text("delete from public.conversations where id = any(:c)"), {"c": conv_ids})


@dataclass
class World:
    a: uuid.UUID
    b: uuid.UUID
    c: uuid.UUID
    agent_a: uuid.UUID
    agent_b: uuid.UUID
    agent_c: uuid.UUID


def _user(
    engine: Engine, api: Api, users: list[uuid.UUID], name: str
) -> tuple[uuid.UUID, uuid.UUID]:
    sub = uuid.uuid4()
    pid = f"g-{sub.hex[:10]}"
    api.identities[str(sub)] = [{"provider": "google", "id": pid}]
    agent = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(conn, AuthUser(sub, (AuthIdentity("google", pid),)), operator="t")
        conn.execute(
            text("insert into public.profiles (id, display_name) values (:id, 'u')"), {"id": sub}
        )
        conn.execute(
            text("insert into public.agents (id, owner_id, name) values (:id, :o, :n)"),
            {"id": agent, "o": sub, "n": name},
        )
    users.append(sub)
    return sub, agent


@pytest.fixture
def world(engine: Engine, api: Api, users: list[uuid.UUID]) -> World:
    a, agent_a = _user(engine, api, users, "AのAI")
    b, agent_b = _user(engine, api, users, "BのAI")
    c, agent_c = _user(engine, api, users, "CのAI")
    return World(a, b, c, agent_a, agent_b, agent_c)


def _conversation(
    conn: Connection, status: str, first: uuid.UUID, second: uuid.UUID, *, created: str
) -> uuid.UUID:
    conv = conn.execute(
        text(
            "insert into public.conversations (status, summary, started_at, created_at) "
            "values (cast(:s as varchar), :summary, "
            "case when cast(:s as varchar) = 'queued' then null else now() end, "
            "cast(:created as timestamptz)) returning id"
        ),
        {"s": status, "summary": f"{status} の要約", "created": created},
    ).scalar_one()
    for ordinal, agent in enumerate((first, second), start=1):
        conn.execute(
            text(
                "insert into public.conversation_participants "
                "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                "values (:c, :a, :o, false, true)"
            ),
            {"c": conv, "a": agent, "o": ordinal},
        )
    return conv


def _usage(conn: Connection, agent: uuid.UUID, conv: uuid.UUID, kind: str, usd: str) -> None:
    conn.execute(
        text(
            "insert into public.usage_records (agent_id, conversation_id, provider, model, "
            "estimated_usd, actual_usd, kind, idempotency_key) "
            "values (:a, :c, 'mock', 'mock-1', :usd, :actual, :k, :key)"
        ),
        {
            "a": agent,
            "c": conv,
            "usd": Decimal(usd),
            "actual": Decimal(usd) if kind == "charge" else None,
            "k": kind,
            "key": f"t-{uuid.uuid4()}",
        },
    )


@pytest.fixture
def convs(engine: Engine, world: World) -> dict[str, uuid.UUID]:
    with engine.begin() as conn:
        done = _conversation(
            conn, "completed", world.agent_a, world.agent_b, created="2026-11-01T00:00:00Z"
        )
        for turn, (agent, status) in enumerate(
            [(world.agent_a, "allowed"), (world.agent_b, "blocked"), (world.agent_b, "allowed")],
            start=1,
        ):
            conn.execute(
                text(
                    "insert into public.messages "
                    "(conversation_id, agent_id, turn_no, content, moderation_status) "
                    "values (:c, :a, :t, :body, :m)"
                ),
                {"c": done, "a": agent, "t": turn, "body": f"発言{turn}", "m": status},
            )
        _usage(conn, world.agent_a, done, "reserve", "0.05")
        _usage(conn, world.agent_a, done, "charge", "0.01")
        _usage(conn, world.agent_a, done, "charge", "0.02")
        _usage(conn, world.agent_a, done, "release", "0.02")
        # actual_usd が入った reserve 行があっても、charge 以外は数えない
        conn.execute(
            text(
                "update public.usage_records set actual_usd = 0.07 "
                "where conversation_id = :c and agent_id = :a and kind = 'reserve'"
            ),
            {"c": done, "a": world.agent_a},
        )
        _usage(conn, world.agent_b, done, "charge", "0.5")
        running = _conversation(
            conn, "running", world.agent_b, world.agent_a, created="2026-11-02T00:00:00Z"
        )
        _usage(conn, world.agent_a, running, "charge", "0.01")
        others = _conversation(
            conn, "completed", world.agent_b, world.agent_c, created="2026-11-03T00:00:00Z"
        )
    return {"done": done, "running": running, "others": others}


def _error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


# -- 会話一覧 -------------------------------------------------------------------


def test_list_only_own_conversations_newest_first(
    api: Api, world: World, convs: dict[str, uuid.UUID]
) -> None:
    response = api.as_user(world.a).get("/api/v1/conversations")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [i["id"] for i in items] == [str(convs["running"]), str(convs["done"])]
    assert response.json()["next_cursor"] is None
    running, done = items
    assert running["partner"] == {"id": str(world.agent_b), "name": "BのAI"}
    assert done["partner"] == {"id": str(world.agent_b), "name": "BのAI"}
    assert done["visibility"] == "participants"
    assert done["summary"] == "completed の要約"


def test_cost_is_own_confirmed_charges_only(
    api: Api, world: World, convs: dict[str, uuid.UUID]
) -> None:
    items = {i["id"]: i for i in api.as_user(world.a).get("/api/v1/conversations").json()["items"]}
    # 自分の charge だけ（reserve/release と相手の 0.5 は含めない）
    assert items[str(convs["done"])]["cost_usd"] == 0.03
    # 実行中は確定前として null
    assert items[str(convs["running"])]["cost_usd"] is None
    other_view = api.as_user(world.b).get(f"/api/v1/conversations/{convs['done']}").json()
    assert other_view["cost_usd"] == 0.5


def test_completed_without_charges_costs_zero(api: Api, engine: Engine, world: World) -> None:
    with engine.begin() as conn:
        conv = _conversation(
            conn, "cancelled", world.agent_a, world.agent_c, created="2026-11-04T00:00:00Z"
        )
    body = api.as_user(world.a).get(f"/api/v1/conversations/{conv}").json()
    assert body["status"] == "cancelled"
    assert body["cost_usd"] == 0


def test_pagination(api: Api, world: World, convs: dict[str, uuid.UUID]) -> None:
    client = api.as_user(world.a)
    first = client.get("/api/v1/conversations", params={"limit": 1}).json()
    assert [i["id"] for i in first["items"]] == [str(convs["running"])]
    assert first["next_cursor"]
    second = client.get(
        "/api/v1/conversations", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert [i["id"] for i in second["items"]] == [str(convs["done"])]
    assert second["next_cursor"] is None
    for bad in ("not-a-cursor", "MjAyNi0xMS0wMXxub3QtdXVpZA"):
        _error(client.get("/api/v1/conversations", params={"cursor": bad}), 422, "VALIDATION_ERROR")
    _error(client.get("/api/v1/conversations", params={"limit": 51}), 422, "VALIDATION_ERROR")


def test_user_without_agent_gets_empty_list(
    api: Api, engine: Engine, users: list[uuid.UUID]
) -> None:
    sub = uuid.uuid4()
    api.identities[str(sub)] = [{"provider": "github", "id": f"gh-{sub.hex[:8]}"}]
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(
            conn, AuthUser(sub, (AuthIdentity("github", f"gh-{sub.hex[:8]}"),)), operator="t"
        )
    users.append(sub)
    response = api.as_user(sub).get("/api/v1/conversations")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}


# -- 会話詳細 -------------------------------------------------------------------


def test_detail_returns_allowed_messages_in_order(
    api: Api, world: World, convs: dict[str, uuid.UUID]
) -> None:
    response = api.as_user(world.a).get(f"/api/v1/conversations/{convs['done']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["name"] for p in body["participants"]] == ["AのAI", "BのAI"]
    assert [(m["turn_no"], m["content"]) for m in body["messages"]] == [(1, "発言1"), (3, "発言3")]
    assert "moderation_status" not in body["messages"][0]


def test_detail_hides_other_and_unknown_conversations(
    api: Api, world: World, convs: dict[str, uuid.UUID]
) -> None:
    client = api.as_user(world.a)
    _error(client.get(f"/api/v1/conversations/{convs['others']}"), 404, "NOT_FOUND")
    _error(client.get(f"/api/v1/conversations/{uuid.uuid4()}"), 404, "NOT_FOUND")


def test_deletion_request_hides_shared_conversations(
    api: Api, engine: Engine, world: World, convs: dict[str, uuid.UUID]
) -> None:
    with engine.begin() as conn:
        activity.notify_conversation_completed(conn, convs["done"])
        conn.execute(
            text("update public.profiles set deleted_at = now() where id = :id"), {"id": world.b}
        )
    client = api.as_user(world.a)
    assert client.get("/api/v1/conversations").json()["items"] == []
    _error(client.get(f"/api/v1/conversations/{convs['done']}"), 404, "NOT_FOUND")
    # 隠した会話の通知も出さない
    assert client.get("/api/v1/notifications").json()["items"] == []


# -- 通知 -----------------------------------------------------------------------


def test_completion_notifications_are_idempotent_and_respect_preferences(
    engine: Engine, world: World, convs: dict[str, uuid.UUID]
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("update public.profiles set web_notifications = false where id = :id"),
            {"id": world.b},
        )
        assert activity.notify_conversation_completed(conn, convs["done"]) == 1
        assert activity.notify_conversation_completed(conn, convs["done"]) == 0
        # 完了していない会話には作らない
        assert activity.notify_conversation_completed(conn, convs["running"]) == 0
        rows = conn.execute(
            text("select profile_id, body from public.notifications where conversation_id = :c"),
            {"c": convs["done"]},
        ).all()
    assert [(r.profile_id, r.body) for r in rows] == [(world.a, {})]


def test_list_and_read_notifications(
    api: Api, engine: Engine, world: World, convs: dict[str, uuid.UUID]
) -> None:
    with engine.begin() as conn:
        activity.notify_conversation_completed(conn, convs["done"])
    client = api.as_user(world.a)
    response = client.get("/api/v1/notifications")
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["kind"] == "conversation.completed"
    assert item["body"] == "BのAIとの交流が完了しました。"
    assert item["conversation_id"] == str(convs["done"])
    assert item["read_at"] is None
    assert len(client.get("/api/v1/notifications", params={"unread": "true"}).json()["items"]) == 1
    assert client.get("/api/v1/notifications", params={"unread": "false"}).json()["items"] == []

    first = client.patch(f"/api/v1/notifications/{item['id']}/read")
    assert first.status_code == 200, first.text
    assert first.json()["read_at"] is not None
    again = client.patch(f"/api/v1/notifications/{item['id']}/read")
    assert again.json()["read_at"] == first.json()["read_at"]
    assert client.get("/api/v1/notifications", params={"unread": "true"}).json()["items"] == []

    # 他人の通知は存在を隠す
    _error(api.as_user(world.b).patch(f"/api/v1/notifications/{item['id']}/read"), 404, "NOT_FOUND")
    _error(client.patch(f"/api/v1/notifications/{uuid.uuid4()}/read"), 404, "NOT_FOUND")
