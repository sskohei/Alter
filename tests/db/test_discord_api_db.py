"""#26: Discord 本人連携 API の DB 統合テスト（docs/06・docs/07・docs/11）。

JWT の検証は差し替え、Supabase Auth 管理 API と Discord は偽物（httpx.MockTransport）。
Discord のトークンが応答・DB・監査に残らないことも確認する。
"""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.discord_link import hash_state
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

ACCESS_TOKEN = "discord-access-token-must-not-leak"
DISCORD_A = "100000000000000001"
DISCORD_B = "100000000000000002"
GUILD = "200000000000000001"
CHANNEL = "300000000000000001"
ADMIN = "400000000000000001"


@dataclass
class FakeDiscord:
    """code → Discord ユーザー ID。``down`` で到達不能、``rejected`` のコードは 400。"""

    users: dict[str, str] = field(default_factory=dict)
    down: bool = False
    calls: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if self.down:
            raise httpx.ConnectError("down")
        if path.endswith("/oauth2/token/revoke"):
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if path.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            assert form["redirect_uri"] == ["http://web.test/auth/discord/callback"]
            if code not in self.users:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={"access_token": f"{ACCESS_TOKEN}:{code}", "scope": "identify"},
            )
        if path.endswith("/users/@me"):
            token = request.headers["Authorization"].removeprefix("Bearer ")
            code = token.split(":", 1)[1]
            return httpx.Response(200, json={"id": self.users[code], "username": "x"})
        return httpx.Response(404)


@dataclass
class Api:
    app: FastAPI
    client: TestClient
    identities: dict[str, list[dict[str, str]]]
    discord: FakeDiscord

    def as_user(self, sub: uuid.UUID) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(sub, "jwks")
        return self.client


def _make_api(engine: Engine, **overrides: Any) -> Iterator[Api]:
    identities: dict[str, list[dict[str, str]]] = {}
    discord = FakeDiscord()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "discord.com":
            return discord.handle(request)
        uid = request.url.path.rsplit("/", 1)[1]
        if uid not in identities:
            return httpx.Response(404)
        return httpx.Response(200, json={"id": uid, "identities": identities[uid]})

    values: dict[str, Any] = {
        "alter_mode": "live",
        "database_url": engine.url.render_as_string(hide_password=False),
        "supabase_secret_key": "test-only",
        "discord_client_id": "500000000000000001",
        "discord_client_secret": "test-only-secret",
        "discord_redirect_uri": "http://web.test/auth/discord/callback",
        **overrides,
    }
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        app = create_app(settings, http_client=http)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Api(app, client, identities, discord)


@pytest.fixture
def api(engine: Engine) -> Iterator[Api]:
    yield from _make_api(engine)


@pytest.fixture
def users(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[list[uuid.UUID]]:
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
        conn.execute(
            text(
                "delete from public.conversations where id in ("
                "  select conversation_id from public.conversation_participants "
                "  where agent_id = any(:a))"
            ),
            {"a": agent_ids},
        )
        conn.execute(text("delete from public.discord_channels where guild_id = :g"), {"g": GUILD})
        conn.execute(text("delete from public.discord_guilds where id = :g"), {"g": GUILD})
        conn.execute(
            text("delete from public.audit_events where actor_profile_id = any(:ids)"),
            {"ids": created_users},
        )


def _new_user(engine: Engine, api: Api, users: list[uuid.UUID]) -> uuid.UUID:
    sub = uuid.uuid4()
    api.identities[str(sub)] = [{"provider": "google", "id": f"g-{sub.hex[:10]}"}]
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(
            conn, AuthUser(sub, (AuthIdentity("google", f"g-{sub.hex[:10]}"),)), operator="test"
        )
    users.append(sub)
    return sub


def _start(api: Api, sub: uuid.UUID) -> str:
    response = api.as_user(sub).post("/api/v1/discord/link/start")
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    url = urlparse(response.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "discord.com"
    assert query["scope"] == ["identify"]
    assert query["redirect_uri"] == ["http://web.test/auth/discord/callback"]
    return query["state"][0]


def _complete(api: Api, sub: uuid.UUID, code: str, state: str) -> httpx.Response:
    return api.as_user(sub).post(
        "/api/v1/discord/link/complete", json={"code": code, "state": state}
    )


def _link(api: Api, sub: uuid.UUID, discord_user_id: str) -> None:
    code = f"code-{uuid.uuid4().hex[:8]}"
    api.discord.users[code] = discord_user_id
    response = _complete(api, sub, code, _start(api, sub))
    assert response.status_code == 200, response.text


def _states(conn: Connection, sub: uuid.UUID) -> int:
    return int(
        conn.execute(
            text("select count(*) from public.discord_oauth_states where profile_id = :id"),
            {"id": sub},
        ).scalar_one()
    )


# -- 連携の正常系 -------------------------------------------------------------------------


def test_link_flow_stores_only_hashed_state_and_no_token(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _new_user(engine, api, users)
    assert api.as_user(sub).get("/api/v1/discord/connection").json() == {
        "connected": False,
        "discord_user_id": None,
    }
    state = _start(api, sub)
    assert len(state) >= 43
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select state_hash, expires_at - created_at as ttl "
                "from public.discord_oauth_states where profile_id = :id"
            ),
            {"id": sub},
        ).one()
    assert row.state_hash == hash_state(state) and state not in row.state_hash
    assert row.ttl.total_seconds() == 600

    api.discord.users["good"] = DISCORD_A
    response = _complete(api, sub, "good", state)
    assert response.status_code == 200, response.text
    assert response.json() == {"connected": True, "discord_user_id": DISCORD_A}
    assert ACCESS_TOKEN not in response.text
    # 取得したトークンは失効させて破棄する
    assert api.discord.revoked == [f"{ACCESS_TOKEN}:good"]

    with engine.connect() as conn:
        assert _states(conn, sub) == 0  # 使い捨て
        dump = conn.execute(
            text(
                "select coalesce(json_agg(c)::text, '') || coalesce("
                "  (select json_agg(e)::text from public.audit_events e "
                "   where e.actor_profile_id = :id), '') "
                "from public.discord_connections c where c.profile_id = :id"
            ),
            {"id": sub},
        ).scalar_one()
    assert ACCESS_TOKEN not in dump and "test-only-secret" not in dump and state not in dump
    assert api.as_user(sub).get("/api/v1/discord/connection").json()["connected"] is True


# -- state の検証 -------------------------------------------------------------------------


def test_state_is_single_use(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users)
    api.discord.users["c1"] = DISCORD_A
    state = _start(api, sub)
    assert _complete(api, sub, "c1", state).status_code == 200
    api.as_user(sub).delete("/api/v1/discord/connection")
    again = _complete(api, sub, "c1", state)
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "OAUTH_STATE_INVALID"


def test_failed_completion_still_consumes_state(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    """Discord がコードを拒否しても state は消費され、同じ state で再試行できない。"""
    sub = _new_user(engine, api, users)
    state = _start(api, sub)
    rejected = _complete(api, sub, "unknown-code", state)
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "OAUTH_STATE_INVALID"
    api.discord.users["late"] = DISCORD_A
    assert _complete(api, sub, "late", state).status_code == 400
    with engine.connect() as conn:
        assert _states(conn, sub) == 0
        assert (
            conn.execute(
                text("select count(*) from public.discord_connections where profile_id = :id"),
                {"id": sub},
            ).scalar_one()
            == 0
        )


def test_expired_state_is_rejected(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users)
    state = _start(api, sub)
    with engine.begin() as conn:
        conn.execute(
            text(
                "update public.discord_oauth_states "
                "set created_at = now() - interval '11 minutes', "
                "    expires_at = now() - interval '1 minute' where profile_id = :id"
            ),
            {"id": sub},
        )
    api.discord.users["c"] = DISCORD_A
    response = _complete(api, sub, "c", state)
    assert response.status_code == 400
    assert api.discord.calls == []  # Discord には問い合わせない


def test_other_users_state_is_rejected_without_consuming(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    owner = _new_user(engine, api, users)
    attacker = _new_user(engine, api, users)
    state = _start(api, owner)
    api.discord.users["c"] = DISCORD_B
    assert _complete(api, attacker, "c", state).status_code == 400
    with engine.connect() as conn:
        assert _states(conn, owner) == 1  # 本人の state は残る
    assert _complete(api, owner, "c", state).status_code == 200


def test_unknown_state_is_rejected(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users)
    api.discord.users["c"] = DISCORD_A
    response = _complete(api, sub, "c", "x" * 43)
    assert response.status_code == 400
    assert api.discord.calls == []


def test_pending_states_are_capped(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users)
    first = _start(api, sub)
    for _ in range(6):
        _start(api, sub)
    with engine.connect() as conn:
        assert _states(conn, sub) == 5
    api.discord.users["c"] = DISCORD_A
    assert _complete(api, sub, "c", first).status_code == 400  # 古いものから捨てる


# -- 衝突 -----------------------------------------------------------------------------------


def test_start_conflicts_when_already_linked(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _new_user(engine, api, users)
    _link(api, sub, DISCORD_A)
    response = api.as_user(sub).post("/api/v1/discord/link/start")
    assert response.status_code == 409


def test_same_discord_account_cannot_link_two_users(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    a = _new_user(engine, api, users)
    b = _new_user(engine, api, users)
    _link(api, a, DISCORD_A)
    api.discord.users["dup"] = DISCORD_A
    response = _complete(api, b, "dup", _start(api, b))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert api.as_user(b).get("/api/v1/discord/connection").json()["connected"] is False
    with engine.connect() as conn:
        assert _states(conn, b) == 0  # 失敗でも state は使い捨て
    # A が解除すれば B は連携できる
    assert api.as_user(a).delete("/api/v1/discord/connection").status_code == 204
    api.discord.users["dup2"] = DISCORD_A
    assert _complete(api, b, "dup2", _start(api, b)).status_code == 200


def test_complete_conflicts_when_already_linked_in_between(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _new_user(engine, api, users)
    state = _start(api, sub)
    _link(api, sub, DISCORD_A)  # 別タブで先に連携が終わった
    calls = len(api.discord.calls)
    api.discord.users["c"] = DISCORD_B
    response = _complete(api, sub, "c", state)
    assert response.status_code == 409
    assert len(api.discord.calls) == calls  # コード交換はしない


# -- 解除 -----------------------------------------------------------------------------------


def _agent(conn: Connection, owner: uuid.UUID) -> uuid.UUID:
    agent_id = uuid.uuid4()
    conn.execute(
        text("insert into public.agents (id, owner_id, name) values (:id, :o, 'A')"),
        {"id": agent_id, "o": owner},
    )
    return agent_id


def _conversation(conn: Connection, a: uuid.UUID, b: uuid.UUID) -> uuid.UUID:
    cid = conn.execute(
        text("insert into public.conversations (status) values ('completed') returning id")
    ).scalar_one()
    for ordinal, agent in ((1, a), (2, b)):
        conn.execute(
            text(
                "insert into public.conversation_participants "
                "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                "values (:c, :a, :o, true, true)"
            ),
            {"c": cid, "a": agent, "o": ordinal},
        )
    return uuid.UUID(str(cid))


def test_disconnect_revokes_and_cancels_pending_deliveries(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    a = _new_user(engine, api, users)
    b = _new_user(engine, api, users)
    _link(api, a, DISCORD_A)
    api.as_user(b).get("/api/v1/discord/connection")  # プロフィールを作る
    with engine.begin() as conn:
        agent_a = _agent(conn, a)
        agent_b = _agent(conn, b)
        conn.execute(
            text(
                "insert into public.discord_guilds (id, name, admin_discord_user_id) "
                "values (:g, 'テスト', :admin)"
            ),
            {"g": GUILD, "admin": ADMIN},
        )
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, name, configured_by) "
                "values (:c, :g, 'ai交流', :admin)"
            ),
            {"c": CHANNEL, "g": GUILD, "admin": ADMIN},
        )
        for agent in (agent_a, agent_b):
            conn.execute(
                text(
                    "insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g)"
                ),
                {"a": agent, "g": GUILD},
            )
        pending_conv = _conversation(conn, agent_a, agent_b)
        sent_conv = _conversation(conn, agent_a, agent_b)
        conn.execute(
            text(
                "insert into public.discord_deliveries (conversation_id, channel_id, status) "
                "values (:p, :ch, 'pending'), (:s, :ch, 'sent')"
            ),
            {"p": pending_conv, "s": sent_conv, "ch": CHANNEL},
        )
        notes = []
        for status in ("pending", "sending", "sent"):
            nid = conn.execute(
                text(
                    "insert into public.notifications (profile_id, kind, dedupe_key) "
                    "values (:p, 'conversation.completed', :k) returning id"
                ),
                {"p": a, "k": f"t:{status}"},
            ).scalar_one()
            conn.execute(
                text(
                    "insert into public.discord_dm_deliveries "
                    "(notification_id, discord_user_id, status) values (:n, :d, :s)"
                ),
                {"n": nid, "d": DISCORD_A, "s": status},
            )
            notes.append(nid)

    guilds = api.as_user(a).get("/api/v1/discord/guilds").json()
    assert guilds == [
        {
            "guild_id": GUILD,
            "guild_name": "テスト",
            "channel_id": CHANNEL,
            "channel_name": "ai交流",
            "guild_enabled": True,
            "consented_at": guilds[0]["consented_at"],
            "revoked_at": None,
        }
    ]

    response = api.as_user(a).delete("/api/v1/discord/connection")
    assert response.status_code == 204
    assert api.as_user(a).get("/api/v1/discord/connection").json()["connected"] is False

    with engine.connect() as conn:
        dm = dict(
            conn.execute(
                text(
                    "select n.dedupe_key, d.status from public.discord_dm_deliveries d "
                    "join public.notifications n on n.id = d.notification_id "
                    "where n.profile_id = :p"
                ),
                {"p": a},
            ).all()
        )
        posts = dict(
            conn.execute(
                text(
                    "select conversation_id, status from public.discord_deliveries "
                    "where conversation_id in (:p, :s)"
                ),
                {"p": pending_conv, "s": sent_conv},
            ).all()
        )
        consents = dict(
            conn.execute(
                text(
                    "select agent_id, revoked_at is not null from public.agent_discord_consents "
                    "where guild_id = :g"
                ),
                {"g": GUILD},
            ).all()
        )
        audit = conn.execute(
            text(
                "select metadata from public.audit_events "
                "where actor_profile_id = :a and action = 'discord.unlink'"
            ),
            {"a": a},
        ).scalar_one()
    # 未送信だけ取り消す（送信中・送信済みはそのまま）
    assert dm == {"t:pending": "cancelled", "t:sending": "sending", "t:sent": "sent"}
    assert posts == {pending_conv: "cancelled", sent_conv: "sent"}
    # 本人の AI の同意だけ取り消す
    assert consents == {agent_a: True, agent_b: False}
    assert audit == {"dm_cancelled": 1, "consents_revoked": 1, "posts_cancelled": 1}
    assert DISCORD_A not in json.dumps(audit)

    revoked = api.as_user(a).get("/api/v1/discord/guilds").json()
    assert revoked[0]["revoked_at"] is not None


def test_disconnect_is_idempotent(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    sub = _new_user(engine, api, users)
    assert api.as_user(sub).delete("/api/v1/discord/connection").status_code == 204
    _link(api, sub, DISCORD_A)
    assert api.as_user(sub).delete("/api/v1/discord/connection").status_code == 204
    assert api.as_user(sub).delete("/api/v1/discord/connection").status_code == 204
    with engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "select count(*) from public.audit_events "
                    "where actor_profile_id = :id and action = 'discord.unlink'"
                ),
                {"id": sub},
            ).scalar_one()
            == 1
        )


def test_disconnect_without_link_invalidates_pending_state(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    """開始 → 解除（未連携でも 204）→ 元の state で完了、の順でも連携させない。"""
    sub = _new_user(engine, api, users)
    state = _start(api, sub)
    assert api.as_user(sub).delete("/api/v1/discord/connection").status_code == 204
    with engine.connect() as conn:
        assert _states(conn, sub) == 0
    api.discord.users["after-unlink"] = DISCORD_A
    response = _complete(api, sub, "after-unlink", state)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OAUTH_STATE_INVALID"
    assert api.as_user(sub).get("/api/v1/discord/connection").json()["connected"] is False


def test_relink_after_disconnect_keeps_history(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _new_user(engine, api, users)
    _link(api, sub, DISCORD_A)
    api.as_user(sub).delete("/api/v1/discord/connection")
    _link(api, sub, DISCORD_B)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select discord_user_id, revoked_at is null as active "
                "from public.discord_connections where profile_id = :id order by connected_at"
            ),
            {"id": sub},
        ).all()
    assert [(r.discord_user_id, r.active) for r in rows] == [
        (DISCORD_A, False),
        (DISCORD_B, True),
    ]


def test_guilds_only_show_own_agent(engine: Engine, api: Api, users: list[uuid.UUID]) -> None:
    a = _new_user(engine, api, users)
    b = _new_user(engine, api, users)
    for sub in (a, b):
        api.as_user(sub).get("/api/v1/discord/connection")
    with engine.begin() as conn:
        _agent(conn, a)
        agent_b = _agent(conn, b)
        conn.execute(
            text(
                "insert into public.discord_guilds (id, admin_discord_user_id, enabled) "
                "values (:g, :admin, false)"
            ),
            {"g": GUILD, "admin": ADMIN},
        )
        conn.execute(
            text("insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g)"),
            {"a": agent_b, "g": GUILD},
        )
    assert api.as_user(a).get("/api/v1/discord/guilds").json() == []
    only = api.as_user(b).get("/api/v1/discord/guilds").json()
    assert [(g["guild_id"], g["guild_enabled"], g["channel_id"]) for g in only] == [
        (GUILD, False, None)
    ]


# -- 設定・到達不能 -------------------------------------------------------------------------


def test_not_configured_is_503(engine: Engine, users: list[uuid.UUID]) -> None:
    for api in _make_api(engine, discord_client_secret=""):
        sub = _new_user(engine, api, users)
        response = api.as_user(sub).post("/api/v1/discord/link/start")
        assert response.status_code == 503
        with engine.connect() as conn:
            assert _states(conn, sub) == 0


def test_discord_down_is_503_and_keeps_state(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    sub = _new_user(engine, api, users)
    state = _start(api, sub)
    api.discord.down = True
    response = _complete(api, sub, "c", state)
    assert response.status_code == 503
    with engine.connect() as conn:
        # 障害時は巻き戻す（Discord がコードを消費していなければ、そのまま再試行できる）
        assert _states(conn, sub) == 1


def test_revoke_or_deletion_cancels_pending_deliveries(
    engine: Engine, api: Api, users: list[uuid.UUID]
) -> None:
    """招待の失効・削除要求（同じ共通処理）で、未送信の DM・投稿とギルド別同意を取り消す。"""
    a = _new_user(engine, api, users)
    b = _new_user(engine, api, users)
    for sub in (a, b):
        api.as_user(sub).get("/api/v1/discord/connection")
    with engine.begin() as conn:
        agent_a = _agent(conn, a)
        agent_b = _agent(conn, b)
        conn.execute(
            text(
                "insert into public.discord_guilds (id, admin_discord_user_id) values (:g, :admin)"
            ),
            {"g": GUILD, "admin": ADMIN},
        )
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, configured_by) "
                "values (:c, :g, :admin)"
            ),
            {"c": CHANNEL, "g": GUILD, "admin": ADMIN},
        )
        for agent in (agent_a, agent_b):
            conn.execute(
                text(
                    "insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g)"
                ),
                {"a": agent, "g": GUILD},
            )
        pending = _conversation(conn, agent_a, agent_b)
        sending = _conversation(conn, agent_a, agent_b)
        conn.execute(
            text(
                "insert into public.discord_deliveries (conversation_id, channel_id, status) "
                "values (:p, :ch, 'pending'), (:s, :ch, 'sending')"
            ),
            {"p": pending, "s": sending, "ch": CHANNEL},
        )
        notes = {}
        for owner, key in ((a, "a"), (b, "b")):
            nid = conn.execute(
                text(
                    "insert into public.notifications (profile_id, kind, dedupe_key) "
                    "values (:p, 'conversation.completed', :k) returning id"
                ),
                {"p": owner, "k": f"t:{key}"},
            ).scalar_one()
            conn.execute(
                text(
                    "insert into public.discord_dm_deliveries "
                    "(notification_id, discord_user_id) values (:n, :d)"
                ),
                {"n": nid, "d": DISCORD_A if key == "a" else DISCORD_B},
            )
            notes[key] = nid

    with engine.begin() as conn:
        inv.cancel_pending_deliveries(conn, a)

    with engine.connect() as conn:
        dm = dict(
            conn.execute(
                text(
                    "select n.dedupe_key, d.status from public.discord_dm_deliveries d "
                    "join public.notifications n on n.id = d.notification_id "
                    "where n.id in (:a, :b)"
                ),
                {"a": notes["a"], "b": notes["b"]},
            ).all()
        )
        posts = dict(
            conn.execute(
                text(
                    "select conversation_id, status from public.discord_deliveries "
                    "where conversation_id in (:p, :s)"
                ),
                {"p": pending, "s": sending},
            ).all()
        )
        consents = dict(
            conn.execute(
                text(
                    "select agent_id, revoked_at is not null from public.agent_discord_consents "
                    "where guild_id = :g"
                ),
                {"g": GUILD},
            ).all()
        )
    assert dm == {"t:a": "cancelled", "t:b": "pending"}  # 相手あての DM はそのまま
    assert posts == {pending: "cancelled", sending: "sending"}
    assert consents == {agent_a: True, agent_b: False}
